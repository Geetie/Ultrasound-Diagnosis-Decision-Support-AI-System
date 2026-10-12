/**
 * p25 — 影像叠加对比度真机取证（**每个版本都要出截图给用户验证**）。
 *
 * 为什么是这一套做法（对应"修法必须鲁棒科学准确，不许穷举色板"）：
 *   · 色值**不硬编码**：采集 `getComputedStyle` 的真实计算色（浏览器已按主题把 token 解析完），
 *     底板取该主题声明里的 `--color-image-bg`（不透明白底则用实际背景）；
 *   · 几何**不硬编码**：矩形来自 `getBoundingClientRect()`；
 *   · 判定交给 `scripts/contrast.py`（纯数学 + 退出码），本脚本只负责**取真值 + 出截图**；
 *   · 两主题各截一张（深色/浅色），并把截图路径打印出来交给用户目视验证。
 *
 * 用法（本机，被测系统=服务器，经 SSH 隧道）：
 *   node scripts/audit/p25_overlay_contrast.mjs [--theme both|dark|light]
 * 环境变量：UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD / UAI_BASE_URL
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
function loadPlaywright() {
  try {
    return require('playwright');
  } catch { /* 落到全局安装 */ }
  for (const [cmd, opts] of [
    ['npm.cmd', { encoding: 'utf-8' }],
    ['npm', { encoding: 'utf-8', shell: true }],
  ]) {
    try {
      const root = execFileSync(cmd, ['root', '-g'], opts).trim();
      const candidate = path.join(root, 'playwright');
      if (fs.existsSync(candidate)) return require(candidate);
    } catch { /* 试下一种 */ }
  }
  throw new Error('未找到 playwright');
}

const { chromium } = loadPlaywright();
const USER = process.env.UAI_DOCTOR_USER;
const PASS = process.env.UAI_DOCTOR_PASSWORD;
const BASE = (process.env.UAI_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const CHROME =
  process.env.UAI_CHROME ||
  path.join(process.env.USERPROFILE || process.env.HOME || '', '.agent-browser/browsers/chrome-154.0.8037.92/chrome.exe');
const ROOT = process.env.UAI_REPO || 'e:/Ultrasound Diagnosis Decision Support AI System';
const OUT = process.env.UAI_P25_OUT || 'C:/Windows/TEMP/uai_p25';
const THEMES = (process.env.UAI_THEMES || 'dark,light').split(',');

fs.mkdirSync(OUT, { recursive: true });

const sleep = (_p, ms) => new Promise((r) => setTimeout(r, ms));

async function api(method, p, body, token) {
  const r = await fetch(`${BASE}${p}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: r.status, json: await r.json().catch(() => null) };
}

if (!USER || !PASS) {
  console.error('缺少 UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD');
  process.exit(2);
}

// 页内采集：影像区内"真正承载文字"的元素 + 计算色 + 矩形 + 有效底板
const COLLECT = `(themeName) => {
  const region = document.querySelector('[data-region="image"]');
  if (!region) return { plate: '', elements: [] };
  const rootStyle = getComputedStyle(document.documentElement);
  const plate = (getComputedStyle(region).backgroundColor || '').trim();
  const tokenPlate = (rootStyle.getPropertyValue('--color-image-bg') || '').trim();
  const out = [];
  for (const el of region.querySelectorAll('*')) {
    const hasOwnText = [...el.childNodes].some(
      (n) => n.nodeType === 3 && n.textContent.trim().length > 0,
    );
    if (!hasOwnText) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) === 0) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    // 有效底板：向上找第一个"看起来不透明"的背景；找不到就把空值交回（Python 侧用该主题底板兜底）
    let bg = '';
    for (let n = el; n; n = n.parentElement) {
      const c = getComputedStyle(n).backgroundColor;
      if (c && c !== 'transparent' && !/rgba\\([^)]*,\\s*0(\\.0+)?\\)$/.test(c)) { bg = c; break; }
      if (n === region) break;
    }
    const label =
      el.getAttribute('data-node') ||
      el.className.toString().split(' ').filter(Boolean).slice(0, 2).join('.') ||
      el.tagName.toLowerCase();
    out.push({
      name: String(label).slice(0, 40),
      text: el.textContent.trim().slice(0, 24),
      color: cs.color,
      background: bg,
      rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      font_size_px: parseFloat(cs.fontSize),
      bold: Number(cs.fontWeight) >= 600,
      theme: themeName,
    });
  }
  return { plate: plate || tokenPlate, elements: out };
}`;

const browser = await chromium.launch({ executablePath: CHROME, headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
const errs = [];
page.on('pageerror', (e) => errs.push(String(e && e.message).slice(0, 200)));
page.on('crash', () => errs.push('PAGE_CRASH'));

let exitCode = 0;
try {
  const login = await api('POST', '/api/v1/auth/login', { username: USER, password: PASS });
  const dtoken = login.json?.access_token;
  if (!dtoken) throw new Error('医生登录失败（拿不到 token）');

  // 找一位"有已完成判读 + 有影像"的患者会话，用它打开工作区（真实数据）
  let sessionId = null;
  const patients = (await api('GET', '/api/v1/patients?q=probe-&limit=50', null, dtoken)).json?.items || [];
  for (const it of patients) {
    const h = await api('GET', `/api/v1/patients/${it.patient_id}/sessions?limit=10`, null, dtoken);
    const hit = (h.json?.items || []).find((x) => x.first_image_id && x.request_id);
    if (hit) { sessionId = hit.session_id; break; }
  }
  if (!sessionId) throw new Error('找不到"有影像且有判读"的会话，无法采集叠加层（先跑 p23/p24 造数据）');
  console.log(`[info] 走查会话 = ${sessionId}`);

  // UI 登录
  await page.goto(`${BASE}/ui/login`, { waitUntil: 'domcontentloaded' });
  await page.getByPlaceholder('输入用户名').fill(USER);
  await page.getByPlaceholder('输入密码').fill(PASS);
  await page.getByRole('button', { name: /登录/ }).first().click();
  await page.waitForURL(/\/ui\//, { timeout: 30000 });
  await sleep(page, 1200);

  await page.goto(`${BASE}/ui/workspace?session_id=${encodeURIComponent(sessionId)}`, { waitUntil: 'domcontentloaded' });
  // 等影像区出现（叠加标注挂在影像区里）
  for (let i = 0; i < 40; i += 1) {
    const ok = await page.locator('[data-region="image"]').count();
    if (ok) break;
    await sleep(page, 500);
  }
  await sleep(page, 4000); // 等 DICOM 与判读结果渲染完（叠加层依赖结果）

  const dpr = await page.evaluate(() => window.devicePixelRatio);
  const sidecar = { dpr, url: page.url(), generated_at: new Date().toISOString(), elements: [], screenshots: [] };

  for (const theme of THEMES) {
    await page.evaluate((t) => {
      document.documentElement.setAttribute('data-theme', t);
      for (const el of document.querySelectorAll('.page-root, .ws-root, [data-screen], [data-region="workspace"]')) {
        el.setAttribute('data-theme', t);
      }
    }, theme);
    await sleep(page, 900); // 让样式重算 + cornerstone 重绘

    const { plate, elements } = await page.evaluate(
      new Function('themeName', `return (${COLLECT})(themeName);`),
      theme,
    );
    const shot = `overlay-${theme}.png`;
    const shotPath = path.join(OUT, shot);
    const region = page.locator('[data-region="image"]');
    if (await region.count()) {
      await region.screenshot({ path: shotPath });
    } else {
      await page.screenshot({ path: shotPath });
    }
    sidecar.screenshots.push({ theme, path: shotPath, plate });
    for (const el of elements) sidecar.elements.push({ ...el, plate, screenshot: shot });
    console.log(`[info] 主题 ${theme}: 采集 ${elements.length} 个叠加文字元素，底板 ${plate || '(取 token)'}，截图 ${shotPath}`);
  }

  const sidecarPath = path.join(OUT, 'overlay_elements.json');
  fs.writeFileSync(sidecarPath, JSON.stringify(sidecar, null, 2), 'utf-8');
  console.log(`[info] DOM 旁证: ${sidecarPath}（${sidecar.elements.length} 条，dpr=${dpr}）`);

  // 判定交给纯数学工具（单一事实源 + 退出码）
  const py = process.env.UAI_PYTHON || 'python';
  const shotArgs = sidecar.screenshots.flatMap((s) => ['--shot', s.path]);
  const args = [path.join(ROOT, 'scripts/contrast.py'), '--sidecar', sidecarPath, '--json-out', path.join(OUT, 'contrast_result.json')];
  if (shotArgs.length) args.push('--shot', sidecar.screenshots[0].path);
  console.log(`[info] 运行审计: ${py} ${args.join(' ')}`);
  const audit = await new Promise((resolve) => {
    const child = execFileSync(py, args, { encoding: 'utf-8' });
    resolve({ code: 0, out: child });
  }).catch((e) => ({ code: e.status ?? 1, out: String(e.stdout || e.message) }));
  console.log(audit.out);
  if (audit.code !== 0) {
    console.log(`[FAIL] 对比度审计退出码 ${audit.code}（1=有项不达标，2=输入不合格）`);
    exitCode = audit.code;
  } else {
    console.log('[PASS] 对比度审计通过（阈值：正文 4.5 / 大字 3.0，字号与几何来自真实 DOM）');
  }

  const jsErrs = errs.filter((e) => !/favicon|DevTools/.test(e));
  console.log(jsErrs.length ? `[WARN] JS 错误 ${jsErrs.length}: ${jsErrs.slice(0, 2)}` : '[PASS] 无未捕获 JS 错误');
  console.log('\n截图（请目视验证）：');
  for (const s of sidecar.screenshots) console.log(`  - ${s.theme}: ${s.path}`);
} catch (e) {
  console.error(`[FAIL] 探针异常终止: ${String(e).slice(0, 300)}`);
  exitCode = 2;
} finally {
  await browser.close();
}

process.exit(exitCode);
