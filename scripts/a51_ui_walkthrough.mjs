#!/usr/bin/env node
/**
 * A.51 前端能力浏览器走查（Playwright + 真 Chrome，本机跑、被测系统在服务器经隧道）。
 *
 * 与既有两脚本的分工：
 *   frontend_browser_smoke.mjs   = 渲染守卫（页面活着）
 *   doctor_flow_walkthrough.mjs  = 医生全流程（能干活）
 *   本脚本                        = **A.51 两项新前端能力**的真机验证
 *
 * 判据（全部用医生看得见的界面文案/控件状态）：
 *   N1 新建会话页「患者候选下拉」：输入 ≥2 字符 → 出候选 → 点选即完成校验（A.51③）
 *   N2 单文件多帧 DICOM 原生解析：帧指示器出现 1/7（A.51② 的数据源前提）
 *   N3 分割**帧级归属**：非所属帧只显示「分割结果在第 X/Y 帧 …」提示、不画掩膜；
 *      翻到该帧才出现 `[data-region=segmentation-overlay]`（A.51② 前端门控）
 *   N4 主流程回归：装载 → 判读出结论（保证新门控没把结论区弄坏）
 *
 * 运行（密码只从环境变量注入，不得写进脚本或命令行）：
 *   UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD / UAI_BASE_URL
 *   node scripts/a51_ui_walkthrough.mjs --dicom <单文件多帧样本路径>
 *
 * 样本制度（2026-10-09 用户明令）：走查一律真实数据。本脚本样本用
 * `scripts/camus_frame_to_dicom.py --multiframe` 在服务器从 CAMUS 现转，
 * scp 回本机后传入（合成样本仅限 DICOM 协议冒烟）。
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

const args = process.argv.slice(2);
const dicomIdx = args.indexOf('--dicom');
const DICOM = dicomIdx >= 0 ? args[dicomIdx + 1] : process.env.UAI_SMOKE_DICOM;
const BASE = process.env.UAI_BASE_URL || 'http://127.0.0.1:8000';
const USER = process.env.UAI_DOCTOR_USER;
const PASSWORD = process.env.UAI_DOCTOR_PASSWORD;
const OUT = 'C:/Windows/TEMP/uai_a51_ui';

if (!USER || !PASSWORD) {
  console.error('需要 UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD 环境变量（密码不落盘）');
  process.exit(2);
}
if (!DICOM || !fs.existsSync(DICOM)) {
  console.error(
    '需要 --dicom <单文件多帧样本>（真实数据制度：服务器 scripts/camus_frame_to_dicom.py ' +
      '--multiframe 现转后 scp 回本机）',
  );
  process.exit(2);
}
fs.mkdirSync(OUT, { recursive: true });

const { chromium } = loadPlaywright();
const chrome = path.join(
  os.homedir(), '.agent-browser', 'browsers', 'chrome-154.0.8037.92', 'chrome.exe',
);

const checks = [];
const T0 = Date.now();
const elapsed = () => `${((Date.now() - T0) / 1000).toFixed(1)}s`;
const record = (name, ok, detail = '') => {
  checks.push({ name, ok });
  console.log(`[${ok ? 'PASS' : 'FAIL'}] +${elapsed()} ${name}${detail ? ' — ' + detail : ''}`);
};
const sleep = (page, ms) => page.waitForTimeout(ms);

// ---- 0) 用 API 找一个「已 grant 同意」的探针患者（候选下拉点选后要能校验通过）
async function resolvePatient() {
  const r = await fetch(`${BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: USER, password: PASSWORD }),
  });
  if (!r.ok) throw new Error(`login http=${r.status}`);
  const tok = (await r.json()).access_token;
  const r2 = await fetch(`${BASE}/api/v1/patients?q=probe-p19-e2e&limit=10`, {
    headers: { Authorization: `Bearer ${tok}` },
  });
  if (!r2.ok) throw new Error(`patients http=${r2.status}`);
  const body = await r2.json();
  const granted = (body.items || []).filter((p) =>
    String(p.consent_status || '').toLowerCase().includes('grant'),
  );
  const pick = granted[0] || (body.items || [])[0];
  return pick ? pick.patient_id : null;
}

const patientId = await resolvePatient();
console.log(`[info] 走查患者 = ${patientId}`);
if (!patientId) {
  console.error('未找到探针患者（先跑 scripts/audit/p19_a51_e2e_audit.py）');
  process.exit(2);
}

const b = await chromium.launch({ executablePath: chrome, headless: true });
const videoDir = path.join(OUT, 'video');
fs.mkdirSync(videoDir, { recursive: true });
const page = await b.newPage({
  viewport: { width: 1512, height: 950 },
  recordVideo: { dir: videoDir, size: { width: 1512, height: 950 } },
});
const video = page.video();
await page.addInitScript(() => {
  setInterval(() => {
    const b = document.body;
    if (b) b.style.opacity = b.style.opacity === '0.999' ? '1' : '0.999';
  }, 400);
});
const errs = [];
page.on('console', (m) => {
  if (m.type() === 'error') errs.push(m.text() + ' @ ' + ((m.location() && m.location().url) || ''));
});
// 诊断用：装载多帧 DICOM 后页面若被关闭，必须知道是渲染进程崩了还是别的原因
page.on('crash', () => errs.push('PAGE_CRASH（渲染进程崩溃）'));
page.on('pageerror', (e) => errs.push('pageerror: ' + String(e && e.message).slice(0, 300)));
page.on('requestfailed', (r) => {
  const f = r.failure();
  errs.push(`requestfailed: ${r.method()} ${r.url()} ${(f && f.errorText) || ''}`);
});
b.on('disconnected', () => errs.push('BROWSER_DISCONNECTED'));
const shot = (name) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true }).catch(() => {});

try {
  // ---- 1) 登录
  await page.goto(`${BASE}/ui/login`, { waitUntil: 'networkidle' });
  await page.fill("input#username, input[placeholder*='用户名']", USER);
  await page.fill("input#password, input[placeholder*='密码']", PASSWORD);
  await page.click("button:has-text('登录')");
  let logged = false;
  for (let i = 0; i < 40 && !logged; i += 1) {
    logged = /\/ui\/?$/.test(page.url());
    if (!logged) await sleep(page, 500);
  }
  record('1. 登录并落在工作区', logged, page.url());
  if (!logged) { await shot('01-login-fail'); throw new Error('login failed'); }

  // ---- 2) 新建会话页：患者候选下拉（A.51③）
  await page.goto(`${BASE}/ui/session/new`, { waitUntil: 'networkidle' });
  const pInput = "input[aria-label='患者编号']";
  await page.fill(pInput, patientId.slice(0, Math.max(2, patientId.length - 2)));

  // 候选列表（实现为 list 内 button/li，点选即校验）
  let candCount = 0;
  let candText = '';
  for (let i = 0; i < 40 && candCount === 0; i += 1) {
    const loc = page.locator(`text=${patientId}`);
    candCount = await loc.count().catch(() => 0);
    if (candCount === 0) await sleep(page, 250);
  }
  if (candCount > 0) candText = await page.locator(`text=${patientId}`).first().innerText().catch(() => '');
  record('N1a. 输入前缀即出患者候选（≥2 字符触发检索）', candCount > 0, `candidates=${candCount} ${candText.slice(0, 40)}`);
  await shot('02-candidates');

  if (candCount > 0) {
    await page.locator(`text=${patientId}`).first().click();
  } else {
    await page.fill(pInput, patientId);
    await page.click("button:has-text('校验患者')");
  }
  let verified = false;
  for (let i = 0; i < 40 && !verified; i += 1) {
    verified = (await page.locator('text=AI 辅助知情同意有效').count()) > 0;
    if (!verified) await sleep(page, 500);
  }
  record('N1b. 点选候选即完成校验（存在性+知情同意）', verified);
  if (!verified) { await shot('02-verify-fail'); throw new Error('patient verify failed'); }

  await page.fill("input[aria-label='设备编号']", `a51ui-${Date.now() % 100000}`);
  await page.click("button:has-text('开始检查')");
  let jumped = false;
  for (let i = 0; i < 40 && !jumped; i += 1) {
    jumped = /session_id=/.test(page.url());
    if (!jumped) await sleep(page, 500);
  }
  record('2. 新会话创建并直达工作区', jumped, new URL(page.url()).searchParams.get('session_id'));
  if (!jumped) { await shot('03-create-fail'); throw new Error('no session jump'); }

  // ---- 3) 装载单文件多帧 DICOM
  let fileInput = null;
  for (let i = 0; i < 30 && fileInput === null; i += 1) {
    fileInput = await page.$("input[type=file][accept*='.dcm']");
    if (fileInput === null) await sleep(page, 500);
  }
  record('3. 装载入口出现', fileInput !== null);
  if (!fileInput) { await shot('03-no-input'); throw new Error('no file input'); }
  await fileInput.setInputFiles([DICOM]);

  const frameText = async () =>
    page.locator('[data-region=frame-indicator]').innerText({ timeout: 500 }).catch(() => '');
  let ind = '';
  for (let i = 0; i < 40; i += 1) {
    ind = await frameText();
    if (/1\/7|1\/6|1\/8/.test(ind)) break;
    await sleep(page, 500);
  }
  record('N2. 单文件多帧原生解析（帧指示器 1/N，N≥6）', /1\/(6|7|8)/.test(ind), `indicator=${ind.trim()}`);
  await shot('04-frame1');

  // ---- 4) 等分割结果（判读完成）→ 帧级门控（A.51②）
  const segSel = '[data-region=segmentation-overlay]';
  const offSel = '[data-region=segmentation-off-frame]';
  let segSeen = false;
  let offText = '';
  let segMode = '';
  for (let i = 0; i < 210; i += 1) {
    const overlay = await page.locator(segSel).count();
    const off = await page.locator(offSel).count();
    if (overlay > 0 || off > 0) {
      segSeen = true;
      segMode = overlay > 0 ? 'overlay' : 'off-frame';
      if (off > 0) {
        offText = await page.locator(offSel).first().innerText({ timeout: 800 }).catch(() => '');
      }
      break;
    }
    // 结论区若已出但不含分割信息，也停下（避免盲等 7 分钟）
    const t = await page.locator('.ws-conclusion').innerText().catch(() => '');
    if (/恶性风险|建议/.test(t) && i > 5) break;
    await sleep(page, 2000);
  }
  record('N3a. 分割旁路结果到达界面（覆盖层或跨帧提示）', segSeen, `mode=${segMode}`);
  await shot('05-seg-state');

  if (segSeen) {
    const m = /分割结果在第\s*(\d+)\/(\d+)\s*帧/.exec(offText.replace(/\s+/g, ' '));
    if (segMode === 'overlay') {
      record('N3b. 首帧即所属帧 → 直接渲染掩膜覆盖层', true, 'overlay 可见');
      // 翻到其它帧必须隐藏掩膜（门控的反向断言）
      await page.keyboard.press('ArrowRight');
      let hidden = false;
      for (let i = 0; i < 20 && !hidden; i += 1) {
        hidden = (await page.locator(segSel).count()) === 0
          && (await page.locator(offSel).count()) > 0;
        if (!hidden) await sleep(page, 300);
      }
      record('N3c. 翻到非所属帧 → 掩膜隐藏并给出跨帧提示', hidden);
      await shot('05b-offframe');
    } else if (m) {
      const target = Number(m[1]);
      const total = Number(m[2]);
      record('N3b. 非所属帧只提示不画掩膜（含帧号）', target >= 1 && total >= 6,
        `提示=${offText.replace(/\s+/g, ' ').slice(0, 60)}`);
      for (let k = 1; k < target; k += 1) {
        await page.keyboard.press('ArrowRight');
        await sleep(page, 350);
      }
      let overlayShown = false;
      for (let i = 0; i < 20 && !overlayShown; i += 1) {
        overlayShown = (await page.locator(segSel).count()) > 0;
        if (!overlayShown) await sleep(page, 300);
      }
      record(`N3c. ⭐ 翻到第 ${target} 帧 → 掩膜覆盖层出现（帧级归属正确）`, overlayShown,
        `indicator=${(await frameText()).trim()}`);
      await shot('05c-overlay-on-frame');
    }
  }

  // ---- 5) 主流程回归：结论区出结论
  let concluded = false;
  let lastText = '';
  for (let i = 0; i < 150; i += 1) {
    const t = await page.locator('.ws-conclusion').innerText().catch(() => '');
    lastText = t.replace(/\s+/g, ' ').slice(0, 70);
    if (/恶性风险|建议/.test(t)) { concluded = true; break; }
    if (/AI 辅助暂不可用/.test(t)) break;
    await sleep(page, 2000);
  }
  record('N4. 主流程回归：判读出结论', concluded, lastText);
  await shot('06-conclusion');

  record('7. 全程无 JS console error', errs.length === 0, errs.slice(0, 3).join(' | ').slice(0, 200));
} catch (e) {
  record('异常终止', false, String(e).slice(0, 200));
  console.log(`  [diag] 捕获到的错误事件（${errs.length}）：`);
  for (const m of errs.slice(0, 8)) console.log('    - ' + m.slice(0, 220));
  await shot('99-crash');
} finally {
  await page.close();
  await b.close();
  try {
    const v = await video.path();
    if (v && fs.existsSync(v)) {
      fs.copyFileSync(v, path.join(OUT, 'a51_ui_walkthrough.webm'));
    }
  } catch {}
}

const fails = checks.filter((c) => !c.ok);
console.log(`\n== A.51 前端走查: PASS=${checks.length - fails.length} FAIL=${fails.length}`);
console.log(`   证据目录: ${OUT}`);
process.exit(fails.length ? 1 : 0);
