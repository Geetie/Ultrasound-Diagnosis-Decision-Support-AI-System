#!/usr/bin/env node
/**
 * 医生全流程端到端走查（Playwright + 真 Chrome，交付前最后一摸排）。
 *
 * 与 scripts/frontend_browser_smoke.mjs 的分工：
 *   smoke = 渲染守卫（每次部署跑，判"页面活着"）；
 *   本脚本 = 医生真实使用流程（判"活着的页面能不能把活干完"）：
 *     登录 → 新建会话（患者校验/设备）→ 装载 DICOM → 等判读出结论 →
 *     报告 → （补录/知情确认）→ HSM 签发 → 已签发。
 *
 * 运行（密码只能从环境变量注入，不得写进脚本或命令行）：
 *   UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD / UAI_BASE_URL
 *   node scripts/doctor_flow_walkthrough.mjs --dicom <样本路径>
 *
 * 样本 DICOM 用 scripts/make_test_dicom.py 现造 —— 不能取自 dicom_storage/：
 * 落盘副本已被 PHI 剥离，回传会被必需 tag 校验拒绝（16 号附录 A.28.5）。
 *
 * 判据全部用医生看得见的界面文案/控件状态，不扒网络请求。
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
  // 本机 homedir 与 npm 全局前缀不是同一路径（A.28），问 npm 自己最可靠
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
const OUT = 'C:/Windows/TEMP/uai_doctor_flow';

if (!USER || !PASSWORD) {
  console.error('需要 UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD 环境变量（密码不落盘）');
  process.exit(2);
}
if (!DICOM || !fs.existsSync(DICOM)) {
  console.error('需要 --dicom <路径>（样本用 scripts/make_test_dicom.py 现造）');
  process.exit(2);
}
fs.mkdirSync(OUT, { recursive: true });

const { chromium } = loadPlaywright();
const chrome = path.join(
  os.homedir(), '.agent-browser', 'browsers', 'chrome-154.0.8037.92', 'chrome.exe',
);

const checks = [];
const record = (name, ok, detail = '') => {
  checks.push({ name, ok });
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' — ' + detail : ''}`);
};
const sleep = (page, ms) => page.waitForTimeout(ms);

const b = await chromium.launch({ executablePath: chrome, headless: true });
const page = await b.newPage({ viewport: { width: 1512, height: 950 } });
const errs = [];
page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
const shot = (name) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true }).catch(() => {});

try {
  // ---- 1) 登录
  await page.goto(`${BASE}/ui/login`, { waitUntil: 'networkidle' });
  await page.fill("input#username, input[placeholder*='用户名']", USER);
  await page.fill("input#password, input[placeholder*='密码']", PASSWORD);
  await page.click("button:has-text('登录')");
  let logged = false;
  for (let i = 0; i < 40 && !logged; i += 1) {
    logged = /\/ui\/?$/.test(page.url()); // /ui/login 也 includes('/ui') —— 必须等落到工作区根
    if (!logged) await sleep(page, 500);
  }
  record('1. 登录并落在工作区', logged, page.url());
  if (!logged) { await shot('01-login-fail'); throw new Error('login failed'); }

  // ---- 2) 新建会话：患者校验 → 设备 → 部位 → 开始检查
  await page.goto(`${BASE}/ui/session/new`, { waitUntil: 'networkidle' });
  await page.fill("input[aria-label='患者编号']", 'DRILL-PATIENT-001');
  await page.click("button:has-text('校验患者')");
  let verified = false;
  for (let i = 0; i < 30 && !verified; i += 1) {
    verified = (await page.locator('text=AI 辅助知情同意有效').count()) > 0;
    if (!verified) await sleep(page, 500);
  }
  record('2. 患者校验通过（存在性 + 知情同意）', verified);
  if (!verified) { await shot('02-verify-fail'); throw new Error('patient verify failed'); }

  await page.fill("input[aria-label='设备编号']", `flow-${Date.now() % 100000}`);
  await page.click("button:has-text('开始检查')");
  let jumped = false;
  for (let i = 0; i < 40 && !jumped; i += 1) {
    jumped = /session_id=/.test(page.url());
    if (!jumped) await sleep(page, 500);
  }
  const sessionId = new URL(page.url()).searchParams.get('session_id');
  record('3. 新会话创建并直达工作区', jumped, sessionId);
  if (!jumped) { await shot('03-create-fail'); throw new Error('no session jump'); }

  // ---- 3) 装载 DICOM → 自动判读
  let fileInput = null;
  for (let i = 0; i < 30 && fileInput === null; i += 1) {
    fileInput = await page.$("input[type=file][accept*='.dcm']");
    if (fileInput === null) await sleep(page, 500);
  }
  record('4. 装载入口出现', fileInput !== null);
  if (!fileInput) { await shot('04-no-input'); throw new Error('no file input'); }
  await fileInput.setInputFiles(DICOM);
  // 等判读出结论：管线 33~300s（A.29 修过前端轮询上限），这里给足 7 分钟
  let concluded = false;
  let lastText = '';
  for (let i = 0; i < 210; i += 1) {
    const t = await page.locator('.ws-conclusion').innerText().catch(() => '');
    lastText = t.replace(/\s+/g, ' ').slice(0, 80);
    if (/恶性风险|建议|已确认/.test(t)) { concluded = true; break; }
    if (/AI 辅助暂不可用/.test(t)) break;
    if ((await page.locator('[role=alert]').count()) > 0) {
      const msg = await page.locator('[role=alert]').first().innerText();
      console.log(`  [alert] ${msg.trim().slice(0, 120)}`);
      break;
    }
    await sleep(page, 2000);
  }
  record('5. 判读出结论（装载后自动发起，无需任何按钮）', concluded, lastText);
  await shot('05-conclusion');
  if (!concluded) { fs.writeFileSync(`${OUT}/05-no-conclusion.html`, await page.content()); throw new Error('no conclusion'); }

  // ---- 4) 报告：常驻条「报告」入口（判读发起后出现）
  const reportBtn = await page.$("button:has-text('报告')");
  record('6. 工作区出现「报告」入口', reportBtn !== null);
  if (!reportBtn) { await shot('06-no-report-entry'); throw new Error('no report entry'); }
  await reportBtn.click();
  let onReport = false;
  for (let i = 0; i < 30 && !onReport; i += 1) {
    onReport = /\/ui\/report/.test(page.url());
    if (!onReport) await sleep(page, 500);
  }
  record('7. 进入报告页', onReport, page.url());
  if (!onReport) { await shot('07-report-nav-fail'); throw new Error('report nav failed'); }

  await page.waitForSelector('[data-testid=report-status]', { timeout: 30000 });

  // ---- 5) 补录（若缺段落）→ 签发（可能知情确认）→ 已签发
  const supplementInputs = await page.$$('[data-testid^=supplement-input-]');
  if (supplementInputs.length > 0) {
    const missingFields = [];
    for (const input of supplementInputs) {
      const field = (await input.getAttribute('data-testid')).replace('supplement-input-', '');
      missingFields.push(field);
      await input.fill(`走查补录：甲状腺实质回声欠均匀，右叶可见低回声结节，边界清。 (${field})`);
    }
    await page.click('[data-testid=supplement-save]');
    let saved = false;
    for (let i = 0; i < 40 && !saved; i += 1) {
      const left = await page.$$('[data-testid^=supplement-input-]').then((els) => els.length);
      saved = left === 0;
      if (!saved) await sleep(page, 1000);
    }
    record('8. 补录缺失段落并重渲染', saved, `fields=${missingFields.join(',')}`);
    await shot('08-supplemented');
  } else {
    record('8. 报告无缺失段落（无需补录）');
  }

  const signBtn = page.locator("button:has-text('签发报告')").first();
  let signable = false;
  for (let i = 0; i < 20 && !signable; i += 1) {
    signable = await signBtn.isEnabled().catch(() => false);
    if (!signable) await sleep(page, 500);
  }
  record('9. 签发按钮可用（门禁与补录状态一致）', signable);
  if (!signable) { await shot('09-sign-disabled'); throw new Error('sign disabled'); }
  await signBtn.click();

  // 部分段落缺失 → 知情确认弹窗（A.32：缺测量/处置等非阻断段时出现）
  const confirm = await page.$('[data-testid=sign-confirm-ok]');
  if (confirm) {
    record('10. 知情确认弹窗出现（存在非阻断缺失）');
    await confirm.click();
  } else {
    record('10. 无需知情确认（完整性已过）');
  }

  let signed = false;
  for (let i = 0; i < 60 && !signed; i += 1) {
    const status = await page.locator('[data-testid=report-status]').innerText().catch(() => '');
    signed = /已签发/.test(status);
    if (!signed) await sleep(page, 1000);
  }
  record('11. 报告已签发（HSM 签名锁定）', signed);
  await shot('11-signed');
  if (!signed) { fs.writeFileSync(`${OUT}/11-no-signed.html`, await page.content()); throw new Error('not signed'); }

  // ---- 6) 全程 console 健康（豁免 Google Fonts CSP —— 若仍配置了外联）
  const blocking = errs.filter((t) => !/fonts\.googleapis\.com/.test(t));
  record('12. 全程 0 个非豁免 console error', blocking.length === 0,
    JSON.stringify(blocking.slice(0, 3)));
} catch (e) {
  record('走查中断', false, String(e).slice(0, 200));
  try { fs.writeFileSync(`${OUT}/error.html`, await page.content()); } catch {}
  await shot('error');
} finally {
  const failed = checks.filter((c) => !c.ok).length;
  console.log(`\nDOCTOR FLOW: PASS=${checks.length - failed} FAIL=${failed}`);
  console.log(`截图与现场：${OUT}`);
  await b.close();
  process.exit(failed ? 1 : 0);
}