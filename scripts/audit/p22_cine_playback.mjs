/**
 * p22 — 多帧分期② cine 播放真机走查（A.52 第二梯队）。
 *
 * 覆盖（全部取医生可见的界面状态，不 mock）：
 *   C1 多帧出现 cine 控件；点「播放」帧号真的在推进
 *   C2 点「暂停」帧号稳定（不会继续跑）
 *   C3 播放中测量工具被强制停用（按钮 aria-pressed 回到 false）
 *   C4 空格键可切换播放/暂停（心超阅片单手动作）
 *   C5 速度档可切换（8fps → 15fps → 30fps → 8fps 循环）
 *   C6 「跳到该帧」：等分割结果到达 → 点直达按钮 → 帧号变成该帧且覆盖层出现
 *
 * 前置（本机跑，被测系统是服务器，经 SSH 隧道）：
 *   ssh -N -L 8000:127.0.0.1:18000 student03@10.20.28.21
 *   UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD / UAI_BASE_URL 由环境变量注入
 * 用法：
 *   node scripts/audit/p22_cine_playback.mjs --dicom <单文件多帧 CAMUS 样本>
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
  // Node 20+ 在 Windows 上直接 spawn .cmd 会 EINVAL（本机实测 Node 24）——
  // 先试裸 npm.cmd，再退到 shell:true 的 npm（与 p20 同款处理）。
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

const args = process.argv.slice(2);
const dicomArg = args.indexOf('--dicom');
const DICOM =
  dicomArg >= 0 ? args[dicomArg + 1] : 'C:/Windows/Temp/uai_a51ui/mf7b.dcm';
const USER = process.env.UAI_DOCTOR_USER;
const PASS = process.env.UAI_DOCTOR_PASSWORD;
const BASE = (process.env.UAI_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const CHROME =
  process.env.UAI_CHROME ||
  path.join(
    process.env.USERPROFILE || process.env.HOME || '',
    '.agent-browser/browsers/chrome-154.0.8037.92/chrome.exe',
  );
const OUT = 'C:/Windows/TEMP/uai_p22';
fs.mkdirSync(OUT, { recursive: true });

const stamp = String(Date.now()).slice(-8);
const PATIENT = `probe-p22-${stamp}`;
let pass = 0;
let fail = 0;
const rows = [];

function record(label, ok, detail = '') {
  if (ok) pass += 1;
  else fail += 1;
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${label}${detail ? ` — ${detail}` : ''}`);
  rows.push({ label, ok, detail });
}
const sleep = (_p, ms) => new Promise((r) => setTimeout(r, ms));

/** 读帧指示器 "n/N" → n（读不到返回 null）。 */
async function frameNo(page) {
  const t = await page
    .locator('[data-region="frame-indicator"]')
    .innerText()
    .catch(() => '');
  const m = t.match(/(\d+)\s*\/\s*(\d+)/);
  return m ? { n: Number(m[1]), total: Number(m[2]) } : null;
}

if (!USER || !PASS) {
  console.error('缺少 UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD（不要写进文件或命令行）');
  process.exit(2);
}
if (!fs.existsSync(DICOM)) {
  console.error(`样本不存在：${DICOM}`);
  process.exit(2);
}

const browser = await chromium.launch({ executablePath: CHROME, headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on('pageerror', (e) => errs.push(`pageerror: ${String(e && e.message).slice(0, 200)}`));

try {
  // ---- API 侧准备：拿一个「同意有效」的患者（建会话前提；不存在就现建+授权）----
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
    try { json = JSON.parse(text); } catch { /* 非 JSON 响应 */ }
    return { status: r.status, json, text };
  }
  const login = await api('POST', '/api/v1/auth/login', { username: USER, password: PASS });
  if (login.status !== 200) throw new Error(`API 登录失败 http=${login.status}`);
  const dtoken = login.json.access_token;

  const found = await api('GET', '/api/v1/patients?q=probe-p19&limit=20', null, dtoken);
  let patient = (found.json?.items || []).find((it) => it.consent_status === 'granted')?.patient_id;
  if (!patient) {
    const adminUser = process.env.UAI_ADMIN_USER;
    const adminPass = process.env.UAI_ADMIN_PASSWORD;
    if (!adminUser || !adminPass) {
      throw new Error('没有「同意有效」的患者，且未提供 UAI_ADMIN_USER/UAI_ADMIN_PASSWORD 以现授权');
    }
    await api('POST', '/fhir/Patient', { patient_id: PATIENT, name: 'P22', gender: 'unknown' }, dtoken);
    const alogin = await api('POST', '/api/v1/auth/login', { username: adminUser, password: adminPass });
    await api(
      'POST',
      '/api/v1/consent',
      { patient_id: PATIENT, action: 'grant', operator_id: adminUser },
      alogin.json?.access_token,
    );
    patient = PATIENT;
  }
  console.log(`[info] 走查患者 = ${patient}`);

  // ---- UI 登录 + 建会话 ----
  await page.goto(`${BASE}/ui/login`, { waitUntil: 'domcontentloaded' });
  await page.getByPlaceholder('输入用户名').fill(USER);
  await page.getByPlaceholder('输入密码').fill(PASS);
  await page.getByRole('button', { name: /登录/ }).first().click();
  await page.waitForURL(/\/ui\//, { timeout: 30000 });
  await sleep(page, 1500);

  await page.goto(`${BASE}/ui/session/new`, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('患者编号').fill(patient);
  await sleep(page, 1200);
  await page.getByRole('button', { name: /校验患者/ }).click();
  await sleep(page, 1500);
  await page.getByLabel('设备编号').fill(`US-P22-${stamp}`);
  await page.getByRole('button', { name: '开始检查' }).click();
  await page.waitForURL(/session_id=/, { timeout: 30000 });
  await sleep(page, 1500);

  await page
    .locator('input[type="file"]')
    .first()
    .setInputFiles([DICOM]);

  // 等帧指示器（多帧样本）
  let frames = null;
  for (let i = 0; i < 40 && !frames; i += 1) {
    frames = await frameNo(page);
    if (!frames) await sleep(page, 1000);
  }
  record('C0 多帧样本装载成功（帧指示器出现）', !!frames, JSON.stringify(frames));
  if (!frames) throw new Error('帧指示器未出现，后续 cine 判据无法进行');

  const isPlaying = async () =>
    (await page.locator('[data-region="cine-toggle"]').getAttribute('aria-pressed')) === 'true';
  const clickPlay = () => page.locator('[data-region="cine-toggle"]').click();

  // ---- C1 播放真的在推进 ----
  await clickPlay();
  const seen = new Set();
  for (let i = 0; i < 12; i += 1) {
    const f = await frameNo(page);
    if (f) seen.add(f.n);
    await sleep(page, 250);
  }
  record('C1 点「播放」后帧号在推进（≥2 个不同帧）', seen.size >= 2, `seen=${[...seen].join(',')}`);
  record('C1b 播放态 aria-pressed=true', await isPlaying(), '');

  // ---- C2 暂停后帧号稳定 ----
  await clickPlay();
  await sleep(page, 600);
  const a1 = await frameNo(page);
  await sleep(page, 1200);
  const a2 = await frameNo(page);
  record('C2 点「暂停」后帧号稳定（不会继续跑）', !!a1 && !!a2 && a1.n === a2.n,
    `${a1 && a1.n} -> ${a2 && a2.n}`);

  // ---- C3 播放中测量工具被停用 ----
  await page.getByRole('button', { name: '长度' }).click();
  await sleep(page, 400);
  const lenBefore = await page.getByRole('button', { name: '长度' }).getAttribute('aria-pressed');
  await clickPlay();
  await sleep(page, 700);
  const lenDuring = await page.getByRole('button', { name: '长度' }).getAttribute('aria-pressed');
  const wlDuring = await page.getByRole('button', { name: '窗宽窗位' }).getAttribute('aria-pressed');
  await clickPlay();
  record('C3 播放中测量工具被强制停用（避免测量归属错帧）',
    lenBefore === 'true' && lenDuring === 'false' && wlDuring === 'true',
    `长度 before=${lenBefore} during=${lenDuring} / 窗宽窗位 during=${wlDuring}`);

  // ---- C4 空格切换 ----
  await page.locator('[data-region="dicom-layer"]').click({ position: { x: 40, y: 40 } }).catch(() => {});
  await page.keyboard.press(' ');
  await sleep(page, 600);
  const playingAfterSpace = await isPlaying();
  await page.keyboard.press(' ');
  await sleep(page, 500);
  const pausedAfterSpace = !(await isPlaying());
  record('C4 空格键切换播放/暂停', playingAfterSpace && pausedAfterSpace,
    `after1=${playingAfterSpace} after2_paused=${pausedAfterSpace}`);

  // ---- C5 速度档循环 ----
  const fpsText = async () => (await page.locator('[data-region="cine-fps"]').innerText()).trim();
  const f0 = await fpsText();
  await page.locator('[data-region="cine-fps"]').click();
  await sleep(page, 300);
  const f1 = await fpsText();
  await page.locator('[data-region="cine-fps"]').click();
  await sleep(page, 300);
  const f2 = await fpsText();
  record('C5 速度档可切换（8→15→30fps）', f0 === '8fps' && f1 === '15fps' && f2 === '30fps',
    `${f0} → ${f1} → ${f2}`);

  // ---- C6 跳到分割所属帧 ----
  let jumpBtn = null;
  for (let i = 0; i < 60 && !jumpBtn; i += 1) {
    const n = await page.locator('[data-region="segmentation-jump"]').count();
    jumpBtn = n > 0 ? true : null;
    if (!jumpBtn) await sleep(page, 1500);
  }
  if (jumpBtn) {
    const before = await frameNo(page);
    await page.locator('[data-region="segmentation-jump"]').click();
    await sleep(page, 1200);
    const after = await frameNo(page);
    const overlay = await page.locator('[data-region="segmentation-overlay"]').count();
    const offFrame = await page.locator('[data-region="segmentation-off-frame"]').count();
    record('C6 「跳到该帧」一步到位且覆盖层出现（提示消失）',
      !!before && !!after && after.n !== before.n && overlay > 0 && offFrame === 0,
      `${before && before.n} -> ${after && after.n} overlay=${overlay} offFrame=${offFrame}`);
  } else {
    record('C6 「跳到该帧」按钮出现', false, '等 90s 未出现分割结果（旁路未启用？）');
  }

  await page.screenshot({ path: `${OUT}/p22-final.png` });
  record('C7 全程无未捕获 JS 错误', errs.length === 0, errs.slice(0, 2).join(' || ') || 'errors=0');
} catch (e) {
  record('探针异常终止', false, String(e).slice(0, 200));
  await page.screenshot({ path: `${OUT}/p22-crash.png` }).catch(() => {});
} finally {
  fs.writeFileSync(
    `${OUT}/p22_report.json`,
    JSON.stringify({ pass, fail, rows, patient: PATIENT, dicom: DICOM }, null, 2),
    'utf-8',
  );
  await browser.close();
}

console.log(`\n== p22 cine 播放走查: PASS=${pass} FAIL=${fail}`);
console.log(`   报告与截图: ${OUT}`);
process.exit(fail === 0 ? 0 : 1);
