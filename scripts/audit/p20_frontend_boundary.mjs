#!/usr/bin/env node
/**
 * p20 前端深度边界摸排（Playwright + 真 Chrome；本机跑，被测系统是服务器经隧道）。
 *
 * 定位：A.51/A.52 走查只验证了「新能力能用」；本探针专门打**边界与异常**，含
 * 前端→后端整条链路：认证守卫、输入边界、注入、装载坏文件、报告页异常 request_id、
 * 帧越界、XSS、视口自适应、请求面 5xx 计数。
 *
 * 运行（密码只从环境变量注入）：
 *   UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD / UAI_BASE_URL
 *   node scripts/audit/p20_frontend_boundary.mjs --dicom <单文件多帧样本> [--bad <坏文件>]
 *
 * 退出码：全 PASS=0，否则 1（可进 CI/门禁）。
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
const DICOM = argVal('--dicom');
const BASE = process.env.UAI_BASE_URL || 'http://127.0.0.1:8000';
const USER = process.env.UAI_DOCTOR_USER;
const PASSWORD = process.env.UAI_DOCTOR_PASSWORD;
const OUT = (argVal('--out') || 'C:/Windows/TEMP/uai_p20').replace(/\/$/, '');

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
// 不依赖 page.waitForTimeout（各 Playwright 版本可用性不一致，实测某版本下报
// "page.waitForTimeout is not a function"）——直接 setTimeout。
const sleep = (_page, ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** 请求面统计：所有 /api/v1 请求的状态码 */
const reqLog = [];
function watch(page) {
  page.on('response', (r) => {
    const u = r.url();
    if (u.includes('/api/v1') || u.includes('/fhir/')) {
      reqLog.push({ url: u.replace(BASE, ''), status: r.status(), method: r.request().method() });
    }
  });
}
const fivexx = () => reqLog.filter((r) => r.status >= 500);
const notFound = () => reqLog.filter((r) => r.status === 404);

/** API 直连（造边界数据用；不经过前端） */
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
  const token = await loginApi();

  // ============ G1 认证与前端守卫（无 token 的干净 context） ============
  {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    watch(page);

    await page.goto(`${BASE}/ui/`, { waitUntil: 'domcontentloaded' });
    await sleep(page, 1500);
    const bodyLen = (await page.locator('body').innerText().catch(() => '')).length;
    const hasLoginForm = await page.getByPlaceholder('输入密码').count();
    record('G1.1 未登录访问 /ui/ → 落登录页（不白屏）',
      hasLoginForm > 0 && bodyLen > 20, `loginForm=${hasLoginForm} textLen=${bodyLen}`);

    // 工作区是 basename 后的根路径（`/ui/`）。写 `/ui/workspace` 不是注册路由，
    // 会被 `path="*"` 回落成 `/ui` 且**丢掉 query**（A.55 实测）——那就测不到
    // 「带 session_id 直连」这一形态了。
    await page.goto(`${BASE}/ui/?session_id=sess_fake_2026`, { waitUntil: 'domcontentloaded' });
    await sleep(page, 1500);
    const guarded = await page.getByPlaceholder('输入密码').count();
    record('G1.2 未登录直连工作区 → 被守卫拦回登录', guarded > 0, `loginForm=${guarded}`);

    await page.getByPlaceholder('输入用户名').fill(USER);
    await page.getByPlaceholder('输入密码').fill('definitely-wrong-password-2026');
    await page.getByRole('button', { name: /登录/ }).first().click();
    await sleep(page, 2500);
    const errText = await page.locator('body').innerText();
    const readable = /密码|凭证|错误|失败|不正确|无效/.test(errText);
    const rawJson = errText.includes('"detail"') || errText.includes('{"');
    record('G1.3 错误密码 → 可读中文提示且不吐原始 JSON',
      readable && !rawJson, `readable=${readable} rawJson=${rawJson}`);

    const tokenKeys = await page.evaluate(() => {
      const out = [];
      for (let i = 0; i < localStorage.length; i += 1) {
        const k = localStorage.key(i);
        if (k && /token/i.test(k) && localStorage.getItem(k)) out.push(k);
      }
      return out;
    });
    record('G1.4 登录失败后未写入 token（不残留半登录态）',
      tokenKeys.length === 0, `tokenKeys=[${tokenKeys.join(',')}]`);
    await ctx.close();
  }

  // ============ 主 context（登录态） ============
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', (e) => errs.push(`pageerror: ${String(e.message).slice(0, 200)}`));
  page.on('console', (m) => { if (m.type() === 'error') errs.push(`console: ${m.text().slice(0, 200)}`); });
  watch(page);
  await uiLogin(page);

  // ---- 边界数据 ----
  // ① 会话/装载路径需要一个「同意有效」的患者：同意只有 QC/管理员能签，
  //    医生账号签不了 —— 所以从既有探针患者里挑一个已授权的（A.51③ 候选下拉同源）。
  const stamp = Date.now().toString().slice(-8);
  let probePatient = process.env.UAI_PROBE_PATIENT;
  if (!probePatient) {
    const list = await api('GET', '/api/v1/patients?q=probe-&limit=50', undefined, token);
    const items = (list.json && list.json.items) || [];
    const granted = items.find((it) => String(it.consent_status || '').toLowerCase() === 'granted');
    probePatient = granted && granted.patient_id;
  }
  record('G0.1 找到「同意有效」的患者用于会话/装载路径', Boolean(probePatient),
    `patient=${probePatient || '(未找到：需要 QC 先授权一个探针患者，或传 UAI_PROBE_PATIENT)'}`);
  if (!probePatient) throw new Error('缺少同意有效的探针患者，后续会话路径无法验证');

  // ② XSS 名字的患者（只走候选下拉渲染路径，不需要同意）
  const xssPatient = `probe-p20-xss-${stamp}`;
  const xssName = '<img src=x onerror="window.__xss=1">';
  const cr = await api('POST', '/fhir/Patient', { patient_id: xssPatient, name: xssName, gender: 'female' }, token);
  record('G0.2 建 XSS 名字患者（供渲染防护验证）', cr.status < 300 || cr.status === 409,
    `http=${cr.status}`);

  // ============ G2 新建会话页输入边界 ============
  await page.goto(`${BASE}/ui/session/new`, { waitUntil: 'domcontentloaded' });
  await sleep(page, 800);
  const patientInput = page.getByLabel('患者编号');

  await patientInput.fill('p');
  await sleep(page, 1200);
  const suggestAt1 = await page.locator('[data-region="patient-suggest"]').count();
  record('G2.1 患者搜索 1 字符 → 不触发检索（阈值 ≥2）', suggestAt1 === 0, `suggest=${suggestAt1}`);

  for (const [label, q, expectOk] of [
    ['G2.2 搜索 % 通配 → 无 5xx 且界面不崩', '%', true],
    ['G2.3 搜索注入串 \' OR 1=1-- → 无 5xx', "' OR 1=1--", true],
    ['G2.4 搜索超长 300 字符 → 无 5xx', 'x'.repeat(300), true],
  ]) {
    const before = fivexx().length;
    await patientInput.fill(q);
    await sleep(page, 1600);
    const bodyLen = (await page.locator('body').innerText().catch(() => '')).length;
    const ok = expectOk && fivexx().length === before && bodyLen > 20;
    record(label, ok, `5xx=${fivexx().length - before} textLen=${bodyLen}`);
  }

  await patientInput.fill(`no-such-patient-${stamp}`);
  await page.getByRole('button', { name: '校验患者' }).click();
  await sleep(page, 1500);
  const notFoundText = await page.locator('body').innerText();
  record('G2.5 不存在患者 → 明示「系统里没有这个患者」+ 建档引导',
    /没有这个患者|建档/.test(notFoundText), `hit=${/没有这个患者|建档/.test(notFoundText)}`);

  await patientInput.fill('');
  await sleep(page, 400);
  const startDisabled = await page.getByRole('button', { name: '开始检查' }).isDisabled().catch(() => null);
  record('G2.6 患者编号为空 → 「开始检查」禁用（不会白发请求）', startDisabled === true, `disabled=${startDisabled}`);

  // 正常患者：校验通过 → 可开始
  await patientInput.fill(probePatient);
  await sleep(page, 900);
  await page.getByRole('button', { name: '校验患者' }).click();
  await sleep(page, 1500);
  const okText = await page.locator('body').innerText();
  record('G2.7 有效患者（同意有效）→ 校验通过并显示「已确认 · 同意有效」',
    /已确认|同意有效/.test(okText), `hit=${/已确认|同意有效/.test(okText)}`);
  // 注：此时「开始检查」仍禁用是**设计**（还缺设备编号，见 G2.8）——不在此判据里要求可用

  // 设备号必填（A.52 实测：空设备号时「开始检查」禁用，title 明示要先填设备编号）
  const startWhenNoDevice = await page.getByRole('button', { name: '开始检查' }).isDisabled().catch(() => false);
  const startTitle = await page.getByRole('button', { name: '开始检查' }).getAttribute('title').catch(() => '');
  record('G2.8 设备号为空 → 仍禁用并有可读原因（不靠医生猜）',
    startWhenNoDevice === true && /设备编号/.test(startTitle || ''), `title=${startTitle}`);

  // 填设备号 → 才可开始
  await page.getByLabel('设备编号').fill(`US-P20-${stamp}`);
  await sleep(page, 400);
  const startEnabled2 = !(await page.getByRole('button', { name: '开始检查' }).isDisabled().catch(() => true));
  await page.getByRole('button', { name: '开始检查' }).click();
  await page.waitForURL(/session_id=/, { timeout: 20000 }).catch(() => {});
  await sleep(page, 1500);
  record('G2.9 校验通过 + 设备编号已填 → 建会话成功并带 session_id 进工作区',
    startEnabled2 && /session_id=/.test(page.url()), `url=${page.url().replace(BASE, '').slice(0, 70)}`);

  // ============ G3 装载边界（坏文件不给静默黑屏） ============
  const badDir = path.join(OUT, 'bad');
  fs.mkdirSync(badDir, { recursive: true });
  const txtAsDcm = path.join(badDir, 'not-a-dicom.dcm');
  fs.writeFileSync(txtAsDcm, 'this is not a dicom file, just text\n');
  const emptyDcm = path.join(badDir, 'empty.dcm');
  fs.writeFileSync(emptyDcm, '');
  const corruptDcm = path.join(badDir, 'corrupt.dcm');
  {
    const buf = Buffer.alloc(2048);
    buf.write('DICM', 128, 'ascii');
    for (let i = 132; i < buf.length; i += 1) buf[i] = (i * 37) % 251;
    fs.writeFileSync(corruptDcm, buf);
  }

  for (const [label, file] of [
    ['G3.1 非 DICOM 文本（.dcm）→ 可读错误提示', txtAsDcm],
    ['G3.2 0 字节 .dcm → 可读错误提示', emptyDcm],
    ['G3.3 损坏 DICOM（伪 DICM 魔数）→ 可读错误提示', corruptDcm],
  ]) {
    const before = fivexx().length;
    const input = page.locator('[data-region="image-upload"] input[type="file"]').first();
    await input.setInputFiles(file).catch(() => undefined);
    await sleep(page, 3500);
    const upText = await page.locator('[data-region="image-upload"]').innerText().catch(() => '');
    // 判据（A.52 实测口径）：①有**中文**可读提示（不许英文原文直给医生）
    // ②不得出现后端原始英文文案 ③无 5xx
    const hasChinese = /[\u4e00-\u9fff]/.test(upText);
    const rawEnglish = /File too small|Missing required DICOM|Invalid DICOM|Failed to parse/i.test(upText);
    const ok = hasChinese && !rawEnglish && fivexx().length === before;
    record(label, ok, `cn=${hasChinese} rawEn=${rawEnglish} text=${upText.replace(/\s+/g, ' ').slice(-56)}`);
    await sleep(page, 300);
  }

  // ============ G5 帧边界 + 真实多帧装载 ============
  let multiLoaded = false;
  if (DICOM && fs.existsSync(DICOM)) {
    const before = fivexx().length;
    const input = page.locator('[data-region="image-upload"] input[type="file"]').first();
    await input.setInputFiles(DICOM).catch(() => undefined);
    await sleep(page, 6000);
    const indicator = await page.locator('[data-region="frame-indicator"]').innerText().catch(() => '');
    multiLoaded = /\d+\/\d+/.test(indicator) && fivexx().length === before;
    record('G5.1 正常多帧样本装载 → 帧指示器出现且无 5xx', multiLoaded, `indicator=${indicator.replace(/\s+/g, '')}`);
    if (multiLoaded) {
      const total = Number((indicator.match(/\d+\/(\d+)/) || [])[1] || 0);
      const frameNo = async () => Number(((await page.locator('[data-region="frame-indicator"]').innerText().catch(() => '1/1')).match(/(\d+)\//) || [])[1] || 0);

      await page.getByLabel('上一帧').click();
      await sleep(page, 700);
      record('G5.2 首帧点「上一帧」→ 不越界（仍 ≥1）', (await frameNo()) >= 1, `frame=${await frameNo()}`);

      for (let i = 0; i < total + 2; i += 1) { await page.getByLabel('下一帧').click(); await sleep(page, 250); }
      const last = await frameNo();
      record(`G5.3 末帧后继续点「下一帧」${total + 2} 次 → 不越界（≤N）`, last <= total && last >= 1, `frame=${last}/${total}`);

      // 判读是异步的：分割结果（覆盖层/跨帧提示/未启用）要等 10~30s 才到界面。
      // 不等就扫 = 空跑假通过（实测第一次扫描 hitFrames=[]）。
      let segArrived = false;
      for (let i = 0; i < 45 && !segArrived; i += 1) {
        const n = await page.locator(
          '[data-region="segmentation-overlay"], [data-region="segmentation-off-frame"], [data-region="segmentation-unavailable"]',
        ).count();
        segArrived = n > 0;
        if (!segArrived) await sleep(page, 2000);
      }
      const segUnavailable = await page.locator('[data-region="segmentation-unavailable"]').count();
      record('G5.4a 分割结果到达界面（等待至多 90s，不空跑）', segArrived, `arrived=${segArrived}`);

      // 逐帧扫，确认分割覆盖层只出现在片方标注的那一帧
      const hitFrames = [];
      for (let i = 0; i < total; i += 1) {
        await page.evaluate(() => undefined);
        while ((await frameNo()) > i + 1) { await page.getByLabel('上一帧').click(); await sleep(200); }
        while ((await frameNo()) < i + 1) { await page.getByLabel('下一帧').click(); await sleep(200); }
        const has = await page.locator('[data-region="segmentation-overlay"]').count();
        if (has > 0) hitFrames.push(i + 1);
      }
      if (segUnavailable > 0) {
        // 旁路未启用/无权重 → 显示降级原因，覆盖层本就不该出现
        record('G5.4 分割覆盖层在整栈中只出现于「所属帧」（唯一性）', hitFrames.length === 0,
          `segmentation-unavailable（旁路未启用）→ 不画掩膜 ✓`);
      } else {
        // 有可用分割 → 必须**恰好一帧**出现覆盖层（多帧错标/永不显示都算失败）
        record('G5.4 分割覆盖层在整栈中只出现于「所属帧」（唯一性）', hitFrames.length === 1,
          `hitFrames=[${hitFrames.join(',')}]/${total}`);
      }
    }
  } else {
    record('G5.1 多帧样本（需 --dicom）', false, '未提供 --dicom 或文件不存在');
  }

  // ============ G6 渲染健壮性 ============
  {
    // XSS：XSS 名字的患者走候选下拉渲染路径
    const p2 = await ctx.newPage();
    watch(p2);
    await p2.goto(`${BASE}/ui/session/new`, { waitUntil: 'domcontentloaded' });
    await sleep(p2, 600);
    await p2.getByLabel('患者编号').fill(xssPatient.slice(0, 14));
    await sleep(p2, 2000);
    const injected = await p2.evaluate(() => Boolean(window.__xss));
    const rawTagVisible = await p2.locator('img[src="x"]').count();
    record('G6.1 患者名含脚本 → 不执行且不注入元素（XSS 防护）',
      injected === false && rawTagVisible === 0, `executed=${injected} imgTags=${rawTagVisible}`);
    await p2.close();
  }

  {
    // 小视口（手机横屏/竖屏）：关键控件仍可见
    const p3 = await ctx.newPage();
    await p3.setViewportSize({ width: 375, height: 667 });
    await p3.goto(page.url(), { waitUntil: 'domcontentloaded' });
    await sleep(p3, 2000);
    const upBox = await p3.locator('[data-region="image-upload"]').first().boundingBox().catch(() => null);
    const region = await p3.locator('[data-region="image"]').first().boundingBox().catch(() => null);
    record('G6.2 375×667 小视口 → 影像区与上传入口仍可见', Boolean(upBox && region),
      `upload=${upBox ? `${Math.round(upBox.width)}x${Math.round(upBox.height)}` : 'none'} image=${region ? `${Math.round(region.width)}x${Math.round(region.height)}` : 'none'}`);
    await p3.close();
  }

  {
    const p4 = await ctx.newPage();
    await p4.setViewportSize({ width: 1920, height: 1080 });
    await p4.goto(page.url(), { waitUntil: 'domcontentloaded' });
    await sleep(p4, 2000);
    const region = await p4.locator('[data-region="image"]').first().boundingBox().catch(() => null);
    record('G6.3 1920×1080 大视口 → 影像区高度仍受控（≤700）',
      Boolean(region) && region.height <= 700, `h=${region ? Math.round(region.height) : 'none'}`);
    await p4.close();
  }

  // ============ G4 报告页异常入参 ============
  {
    const p5 = await ctx.newPage();
    watch(p5);
    await p5.goto(`${BASE}/ui/report`, { waitUntil: 'domcontentloaded' });
    await sleep(p5, 2000);
    const t = await p5.locator('body').innerText().catch(() => '');
    // placeholder / aria-label 不在 innerText 里 —— 必须按控件是否存在判
    const inputCount = await p5.getByLabel('诊断请求 ID').count().catch(() => 0);
    const loadingStuck = /加载中|Loading/.test(t) && t.length < 40;
    record('G4.1 报告页无 request_id → 出输入框引导（不是无限 loading）',
      inputCount > 0 && t.length > 20 && !loadingStuck,
      `input=${inputCount} textLen=${t.length} stuck=${loadingStuck}`);

    const before = fivexx().length;
    await p5.getByLabel('诊断请求 ID').fill('req_does_not_exist_2026');
    await p5.keyboard.press('Enter');
    await sleep(p5, 2500);
    const t2 = await p5.locator('body').innerText().catch(() => '');
    record('G4.2 无效 request_id → 可读提示（无 5xx、不白屏）',
      t2.length > 20 && fivexx().length === before, `5xx=${fivexx().length - before} textLen=${t2.length}`);
    await p5.close();
  }

  // ============ G7 请求面汇总 ============
  {
    const s5 = fivexx();
    record('G7.1 全程无 5xx（前端→后端边界无服务端崩溃）', s5.length === 0,
      s5.length ? s5.slice(0, 3).map((r) => `${r.status} ${r.url}`).join(' | ') : `requests=${reqLog.length}`);
    const unexpected404 = notFound().filter((r) => !/fhir\/Patient\/(no-such|probe-|.)/.test(r.url)
      && !/does_not_exist/.test(r.url));
    record('G7.2 无意外 404（探测用的「不存在」请求除外）', unexpected404.length === 0,
      unexpected404.slice(0, 3).map((r) => `${r.status} ${r.url}`).join(' | ') || `404s=${notFound().length}（均属预期探测）`);
    // 排除：①探针**故意**打的坏请求（400 坏文件 / 404 不存在患者）触发的资源加载告警
    //       ②favicon / DevTools 噪声。剩下才是真正的未捕获 JS 错误（如白屏类）。
    const jsErrs = errs.filter((e) =>
      !/favicon|DevTools|Download the React/.test(e)
      && !/Failed to load resource: the server responded with a status of (400|404|403|422)/.test(e)
      // 本探针**故意**喂坏 DICOM（G3.1~G3.3）：Cornerstone 解析必然在内部抛这两类
      // 加载错误 —— 属预期后果；医生可见面已由装载区中文提示兜住（G3 已断言）。
      && !/(no pixel data|past end of input|Invalid frame length|loadImageFromNaturalizedMetadata|Invalid DICOM file|meta length tag|Invalid vr type)/.test(e));
    record('G7.3 除「故意坏文件」的加载错误外无未捕获 JS 错误', jsErrs.length === 0,
      jsErrs.slice(0, 2).join(' || ') || `errors=0（原始 ${errs.length} 条均已归类：4xx 资源告警 + 坏文件加载错误）`);
  }

  await ctx.close();
} catch (e) {
  record('探针自身异常终止', false, String(e).slice(0, 200));
} finally {
  await browser.close().catch(() => undefined);
}

const pass = checks.filter((c) => c.ok).length;
const fail = checks.length - pass;
console.log(`\n== p20 前端边界摸排: PASS=${pass} FAIL=${fail}`);
fs.writeFileSync(
  path.join(OUT, 'p20_report.json'),
  JSON.stringify({ pass, fail, checks, requests: reqLog }, null, 2),
  'utf-8',
);
console.log(`   报告: ${OUT}/p20_report.json`);
process.exit(fail === 0 ? 0 : 1);
