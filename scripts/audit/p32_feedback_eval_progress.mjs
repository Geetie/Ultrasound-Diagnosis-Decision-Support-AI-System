#!/usr/bin/env node
/**
 * p32 反馈页「评测集进度」区块的**前后端联调**验证（Playwright + 真 Chrome；本机跑，被测系统是服务器经隧道）。
 *
 * 为什么单独成探针
 * ----------------
 * 单测（vitest / pytest）只能证明「组件在 mock 数据下渲染对」与「端点在假报告下返回对」；
 * **不能**证明真实部署里「浏览器上看到的数字」与「后端真实数据」一致 —— 那正是 A.55 白屏与
 * "界面看着有、其实没接上"这类事故的盲区。本探针把两边的数字**逐一对照**（关键：
 * **不硬编码任何数字**，全部以当场 API 返回为准），并且顺带覆盖未登录守卫与 5xx 面。
 *
 * 运行（密码只从环境变量注入）：
 *   UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD / UAI_BASE_URL（默认 http://127.0.0.1:8000，经隧道指向服务器 18000）
 *   node scripts/audit/p32_feedback_eval_progress.mjs [--out C:/Windows/TEMP/uai_p32]
 *
 * 退出码：全 PASS=0；有 FAIL=1；登录/前端不可达=2。
 */
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';

const require = createRequire(import.meta.url);
function loadPlaywright() {
  try { return require('playwright'); } catch {}
  for (const [cmd, opts] of [
    ['npm.cmd', { encoding: 'utf-8' }],
    ['npm', { encoding: 'utf-8', shell: true }],
  ]) {
    try {
      const root = execFileSync(cmd, ['root', '-g'], opts).trim();
      const candidate = path.join(root, 'playwright');
      if (fs.existsSync(candidate)) return require(candidate);
    } catch {}
  }
  throw new Error('未找到 playwright');
}

const argv = process.argv.slice(2);
const argVal = (name) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : undefined; };
const BASE = process.env.UAI_BASE_URL || 'http://127.0.0.1:8000';
const USER = process.env.UAI_DOCTOR_USER;
const PASSWORD = process.env.UAI_DOCTOR_PASSWORD;
const OUT = (argVal('--out') || 'C:/Windows/TEMP/uai_p32').replace(/\/$/, '');

if (!USER || !PASSWORD) { console.error('需要 UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD'); process.exit(2); }
fs.mkdirSync(OUT, { recursive: true });

const { chromium } = loadPlaywright();
const chrome = path.join(os.homedir(), '.agent-browser', 'browsers', 'chrome-154.0.8037.92', 'chrome.exe');

const checks = [];
const T0 = Date.now();
const elapsed = () => `${((Date.now() - T0) / 1000).toFixed(1)}s`;
const record = (name, ok, detail = '') => {
  checks.push({ name, ok, detail });
  console.log(`[${ok ? 'PASS' : 'FAIL'}] +${elapsed()} ${name}${detail ? ' — ' + detail : ''}`);
};
const sleep = (_page, ms) => new Promise((resolve) => setTimeout(resolve, ms));
const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();

const reqLog = [];
function watch(page) {
  page.on('response', (r) => {
    const u = r.url();
    if (u.includes('/api/v1') || u.includes('/fhir/')) {
      reqLog.push({ url: u.replace(BASE, ''), status: r.status(), method: r.request().method() });
    }
  });
}

async function api(method, p, body, token) {
  const r = await fetch(`${BASE}${p}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch {}
  return { status: r.status, json, text };
}
async function loginApi() {
  const r = await api('POST', '/api/v1/auth/login', { username: USER, password: PASSWORD });
  if (r.status !== 200) throw new Error(`API 登录失败 http=${r.status}`);
  return r.json.access_token;
}
async function uiLogin(page) {
  await page.goto(`${BASE}/ui/login`, { waitUntil: 'domcontentloaded' });
  await page.getByPlaceholder('输入用户名').fill(USER);
  await page.getByPlaceholder('输入密码').fill(PASSWORD);
  await page.getByRole('button', { name: /登录/ }).first().click();
  await page.waitForURL(/\/ui\/(workspace)?/, { timeout: 20000 }).catch(() => {});
  await sleep(page, 1200);
}

const browser = await chromium.launch({ executablePath: chrome, headless: true });

try {
  // ============ G1 未登录直连反馈页 → 被守卫拦回登录（干净 context） ============
  {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    watch(page);
    await page.goto(`${BASE}/ui/feedback`, { waitUntil: 'domcontentloaded' });
    await sleep(page, 1500);
    const loginForm = await page.getByPlaceholder('输入密码').count();
    const region = await page.locator('[data-region="eval-set-progress"]').count();
    record('G1.1 未登录直连 /ui/feedback → 出现登录表单', loginForm > 0, `loginForm=${loginForm}`);
    record('G1.2 未登录时**不渲染**进度区块（没数据也不假装有）', region === 0, `region=${region}`);
    await ctx.close();
  }

  const token = await loginApi();

  // ============ G2 后端真值（页面必须与它一致；**不硬编码**） ============
  const stats = await api('GET', '/api/v1/eval-set/stats', null, token);
  record('G2.1 GET /api/v1/eval-set/stats 带 token → 200', stats.status === 200, `http=${stats.status}`);
  if (stats.status !== 200) throw new Error(`进度端点不可用 http=${stats.status}`);
  const d = stats.json || {};
  const expectAvailable = d.available === true;
  record(
    'G2.2 报告已生成（available=true）',
    expectAvailable,
    `available=${d.available} reason=${d.reason || '-'}`,
  );

  // ============ G3 登录后进入反馈页，区块真的渲染出真值 ============
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  watch(page);
  await uiLogin(page);
  await page.goto(`${BASE}/ui/feedback`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('[data-region="eval-set-progress"]', { timeout: 20000 }).catch(() => {});
  await sleep(page, 2000);

  const regionCount = await page.locator('[data-region="eval-set-progress"]').count();
  record('G3.1 反馈页渲染出「评测集进度」区块', regionCount > 0, `region=${regionCount}`);

  const regionText = norm(await page.locator('[data-region="eval-set-progress"]').first().innerText().catch(() => ''));
  record('G3.2 区块标题为「评测集进度（双引擎）」', regionText.includes('评测集进度（双引擎）'), `text=${regionText.slice(0, 60)}`);

  if (expectAvailable && regionCount > 0) {
    const tTotal = (d.targets || {}).total ?? 0;
    const tDoc = (d.targets || {}).doctor_labeled ?? 0;

    // ① 样本进度行 —— 数字必须等于后端
    record(
      'G4.1 样本行数字 = 后端（去重后/目标）',
      regionText.includes(`样本（去重后）：${d.deduped} / ${tTotal}`),
      `期望「样本（去重后）：${d.deduped} / ${tTotal}」`,
    );
    // ② 医生标注行
    record(
      'G4.2 医生标注坏例行数字 = 后端',
      regionText.includes(`医生标注坏例（含正确口径）：${d.doctor_labeled} / ${tDoc}`),
      `期望「医生标注坏例（含正确口径）：${d.doctor_labeled} / ${tDoc}」`,
    );
    // ③ 判读总数行（含无影像跳过）
    record(
      'G4.3 判读总数/无法去重/无影像跳过 = 后端',
      regionText.includes(`判读总数 ${d.total} · 无法去重 ${d.undedupable} · 无影像跳过 ${d.skipped_no_image}`),
      `期望「判读总数 ${d.total} · 无法去重 ${d.undedupable} · 无影像跳过 ${d.skipped_no_image}」`,
    );
    // ④ A.62 批 3 新增：患者维度 + 去重口径（"700 例"的真实刻度）
    record(
      'G4.4 不同患者数 = 后端（A.62 批 3 新上屏）',
      regionText.includes(`不同患者 ${d.patient_count} 人`),
      `期望「不同患者 ${d.patient_count} 人」`,
    );
    const reasonStr = Object.entries(d.dedup_reason || {}).map(([k, v]) => `${k}:${v}`).join(' ');
    if (reasonStr) {
      record(
        'G4.5 去重口径与后端一致（patient_image/image_only/no_basis）',
        regionText.includes(reasonStr),
        `期望「${reasonStr}」`,
      );
    }
    // ⑤ 降级告警：缺患者 ID 时界面必须说清（不静默）
    if ((d.patient_id_missing || 0) > 0) {
      record(
        'G4.6 缺患者 ID 时界面显式告警（去重已降级）',
        regionText.includes(`${d.patient_id_missing} 条缺患者 ID`),
        `期望包含「${d.patient_id_missing} 条缺患者 ID」`,
      );
    } else {
      record('G4.6 无缺患者 ID → 界面不应出现降级告警', !regionText.includes('缺患者 ID'), 'patient_id_missing=0');
    }
  }

  // ============ G5 请求面健康：无 5xx；进度请求确实打到后端且 200 ============
  const statsReq = reqLog.filter((r) => r.url.includes('/eval-set/stats'));
  const five = reqLog.filter((r) => r.status >= 500);
  record('G5.1 反馈页请求面无 5xx', five.length === 0, `5xx=[${five.map((r) => `${r.url}:${r.status}`).join(',')}]`);
  record(
    'G5.2 页面确实调用了 /api/v1/eval-set/stats 且 200',
    statsReq.length > 0 && statsReq.every((r) => r.status === 200),
    `reqs=[${statsReq.map((r) => r.status).join(',')}]`,
  );

  // ============ G6 证据截图 ============
  const shot = path.join(OUT, 'feedback_eval_progress.png');
  await page.locator('[data-region="eval-set-progress"]').first().screenshot({ path: shot }).catch(async () => {
    await page.screenshot({ path: shot, fullPage: true });
  });
  const bytes = fs.existsSync(shot) ? fs.statSync(shot).size : 0;
  record('G6.1 区块截图已留存（证据）', bytes > 3000, `${shot} (${bytes}B)`);

  await ctx.close();
} catch (err) {
  console.error(`[ERROR] ${err?.message || err}`);
  checks.push({ name: 'runner', ok: false, detail: String(err?.message || err) });
} finally {
  await browser.close();
}

const failed = checks.filter((c) => !c.ok);
const report = {
  probe: 'p32_feedback_eval_progress',
  base: BASE,
  base_url_ok: checks.length > 0,
  checks,
  passed: checks.length - failed.length,
  failed: failed.length,
  wall_s: Number(((Date.now() - T0) / 1000).toFixed(1)),
  evidence: OUT,
};
fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 2), 'utf-8');
console.log(`== p32: PASS=${report.passed} FAIL=${report.failed}（${report.wall_s}s）  证据：${OUT}`);
process.exit(failed.length === 0 ? 0 : (checks.length === 0 ? 2 : 1));
