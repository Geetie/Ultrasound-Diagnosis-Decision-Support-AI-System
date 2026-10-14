#!/usr/bin/env node
/**
 * p33 医生视角**操作动作**验证（Playwright + 真 Chrome；本机跑，被测系统是服务器经隧道）。
 *
 * 为什么单独成探针
 * ----------------
 * p32 只证明「进度区块的数字 = 后端数字」；本探针走的是**医生真的会做的动作序列**：
 *   登录 → 新建会话（患者校验/知情同意 → 选部位 → 开始检查）→ 上传真实影像 →
 *   等出结论（并核对可读性）→ 进反馈页 → 提交一条**修正类**反馈 → 断言提交结果。
 *
 * 这条链上有两处单测盖不到的缝：
 *   ① 反馈表单的 `request_id` 需要**真实判读 id**（后端 `/feedback` 会校验存在性）；
 *   ② 只有走完「判读 → 反馈」才可能让评测集的「医生标注坏例」计数动起来
 *      （采集器按 `feedback_records.request_id` 关联到判读样本）。
 * 所以本探针顺带把「医生标注通道到底通不通」钉成可复跑的断言，而不是靠人说"应该没问题"。
 *
 * 运行（密码从环境变量注入）：
 *   UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD / UAI_BASE_URL
 *   node scripts/audit/p33_doctor_actions.mjs --dicom <多帧样本> [--organ 甲状腺] [--out DIR]
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
const argOf = (n) => { const i = argv.indexOf(n); return i >= 0 ? argv[i + 1] : undefined; };
const BASE = (process.env.UAI_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const USER = process.env.UAI_DOCTOR_USER;
const PASSWORD = process.env.UAI_DOCTOR_PASSWORD;
const DICOM = argOf('--dicom') || process.env.UAI_P33_DICOM;
const ORGAN = argOf('--organ') || process.env.UAI_P33_ORGAN || '甲状腺';
const PATIENT = argOf('--patient') || process.env.UAI_P33_PATIENT || 'DRILL-PATIENT-001';
const OUT = (argOf('--out') || 'C:/Windows/TEMP/uai_p33').replace(/\/$/, '');

if (!USER || !PASSWORD) { console.error('需要 UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD'); process.exit(2); }
if (!DICOM || !fs.existsSync(DICOM)) { console.error(`需要 --dicom（不存在：${DICOM || '未给'}）`); process.exit(2); }
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
const sleep = (_p, ms) => new Promise((r) => setTimeout(r, ms));
const norm = (s) => String(s || '').replace(/\s+/g, ' ').trim();

const netLog = [];
const feedbackLog = [];
let lastDiagnosisRequestId = null;

const browser = await chromium.launch({ executablePath: chrome, headless: true });

async function api(method, p, body, token) {
  const r = await fetch(`${BASE}${p}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch {}
  return { status: r.status, json, text };
}

try {
  const token = await (async () => {
    const r = await api('POST', '/api/v1/auth/login', { username: USER, password: PASSWORD });
    if (r.status !== 200) throw new Error(`API 登录失败 http=${r.status}`);
    return r.json.access_token;
  })();

  const ctx = await browser.newContext({ viewport: { width: 1512, height: 950 } });
  const page = await ctx.newPage();
  page.on('response', async (r) => {
    const u = r.url().replace(BASE, '');
    if (u.includes('/api/v1')) netLog.push({ url: u, status: r.status(), method: r.request().method() });
    // 抓真实判读 request_id（医生看到的结论就是这一次判读）
    if (/\/api\/v1\/diagnosis/.test(u) && r.status() < 400) {
      try {
        const j = await r.json();
        const rid = j?.request_id || j?.data?.request_id;
        if (rid) lastDiagnosisRequestId = rid;
      } catch {}
    }
    if (/\/api\/v1\/feedback/.test(u)) {
      let body = '';
      try { body = (await r.text()).slice(0, 300); } catch {}
      feedbackLog.push({ status: r.status(), body });
    }
  });

  // ============ D1 登录（医生） ============
  await page.goto(`${BASE}/ui/login`, { waitUntil: 'domcontentloaded' });
  await page.getByPlaceholder('输入用户名').fill(USER);
  await page.getByPlaceholder('输入密码').fill(PASSWORD);
  await page.getByRole('button', { name: /登录/ }).first().click();
  await page.waitForURL(/\/ui\/(workspace)?/, { timeout: 20000 }).catch(() => {});
  await sleep(page, 1500);
  const loggedIn = (await page.getByPlaceholder('输入密码').count()) === 0;
  record('D1 医生登录成功（离开登录页）', loggedIn, `url=${page.url().replace(BASE, '')}`);

  // ============ D2 新建会话：患者校验 → 选部位 → 开始检查 ============
  await page.goto(`${BASE}/ui/session/new`, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('患者编号').fill(PATIENT);
  await page.getByRole('button', { name: /校验患者/ }).click();
  let consent = false;
  for (let i = 0; i < 30 && !consent; i += 1) {
    consent = (await page.locator('text=AI 辅助知情同意有效').count()) > 0;
    if (!consent) await sleep(page, 600);
  }
  record('D2.1 患者校验通过（知情同意有效）', consent, `patient=${PATIENT}`);

  const organBtn = page.getByRole('button', { name: ORGAN });
  const hasOrgan = (await organBtn.count()) > 0;
  if (hasOrgan) await organBtn.first().click();
  await page.getByLabel('设备编号').fill(`P33-${Date.now() % 100000}`);
  await page.getByRole('button', { name: '开始检查' }).click();
  let sessionId = null;
  for (let i = 0; i < 40 && !sessionId; i += 1) {
    const m = page.url().match(/session_id=([^&]+)/);
    if (m) sessionId = decodeURIComponent(m[1]);
    else await sleep(page, 500);
  }
  record('D2.2 开始检查 → 进入工作区（拿到 session_id）', Boolean(sessionId), `session=${sessionId || '-'} organ=${hasOrgan ? ORGAN : '默认'}`);

  // ============ D3 上传真实影像 ============
  await page.locator('input[type="file"]').first().setInputFiles([DICOM]);
  let canvasSeen = false;
  let frameText = '';
  for (let i = 0; i < 45 && !canvasSeen; i += 1) {
    canvasSeen = (await page.locator('canvas').count()) > 0;
    frameText = norm(await page.locator('[data-region="frame-indicator"]').innerText().catch(() => ''));
    if (!canvasSeen) await sleep(page, 1000);
  }
  record('D3 影像上传后渲染出画面（canvas 出现）', canvasSeen, `frame=${frameText || '-'} file=${path.basename(DICOM)}`);

  // ============ D4 等判读结论（并核对可读性） ============
  let conclusion = '';
  for (let i = 0; i < 75; i += 1) {
    conclusion = norm(await page.locator('.ws-conclusion').innerText().catch(() => ''));
    if (/恶性风险|建议|分级|良性|不确定|已转人工/.test(conclusion)) break;
    await sleep(page, 2000);
  }
  const hasConclusion = conclusion.length > 10;
  record('D4.1 医生看到判读结论（非占位文案）', hasConclusion, `len=${conclusion.length} 摘要=${conclusion.slice(0, 60)}`);
  const techLeak = /\[(thyroid|cardiac|breast|abdomen)\]|"[a-z_]+"\s*:/.test(conclusion);
  record('D4.2 结论正文可读（无器官代码/英文键值技术串）', !techLeak, techLeak ? `泄漏样例=${conclusion.slice(0, 80)}` : '干净');
  record(
    'D4.3 抓到本次判读的真实 request_id（反馈要挂在它上面）',
    Boolean(lastDiagnosisRequestId),
    `request_id=${lastDiagnosisRequestId || '未抓到'}`,
  );

  // ============ D5 进反馈页（**走医生真实路径：点侧边栏「反馈提交」**） ============
  // 为什么不用 `page.goto('/ui/feedback')`：整页加载会清空前端内存态（会话 id 在 store 里），
  // 实测那样进去 `session_id` 为空 → 提交必 404「判读请求不存在」（A.62 批 3 踩到）。
  // 医生日常是从工作区点侧边栏进去的（SPA 内导航，store 保留），所以探针也按这条路径走。
  let navHow = 'none';
  // ① 首选：侧边栏菜单项（antd Menu；用 data-menu-id 定位，避免侧栏折叠时标签文本不可见）
  const byMenuId = page.locator('.ant-menu-item[data-menu-id$="/feedback"]').first();
  const byRole = page.getByRole('menuitem', { name: /反馈提交/ }).first();
  const byText = page.getByText('反馈提交').first();
  for (const [how, loc] of [['menuitem-role', byRole], ['menu-id', byMenuId], ['text', byText]]) {
    if ((await loc.count()) > 0) {
      await loc.click({ timeout: 5000 }).catch(() => {});
      await sleep(page, 1200);
      if (/\/feedback/.test(page.url())) { navHow = how; break; }
    }
  }
  // ② 兜底：深链带会话（`?session_id=`）——这条路径靠 A.62 批 3 新增的"URL 会话兜底"才成立，
  //    所以它也是**被测对象**，不是绕过。
  if (navHow === 'none') {
    await page.goto(`${BASE}/ui/feedback?session_id=${encodeURIComponent(sessionId || '')}`, {
      waitUntil: 'domcontentloaded',
    });
    navHow = 'deeplink';
  }
  await page.waitForSelector('[data-region="eval-set-progress"]', { timeout: 20000 }).catch(() => {});
  await sleep(page, 1200);
  record(
    'D5.0 进入反馈页（医生路径优先：侧边栏；退化时深链带会话）',
    /\/feedback/.test(page.url()),
    `how=${navHow} url=${page.url().replace(BASE, '')}`,
  );

  // ⚠️ 优先用 antd 表单的 `#name`（Form.Item name 会写成 input 的 id）：
  // `getByLabel('字段')` 会撞上「语音填写目标字段」那个下拉（aria-label 里含"字段"）——
  // 实测把 readonly 的 select input 当成目标，等 30s 超时（本轮踩到）。
  const fill = async (label, name, value) => {
    const byId = page.locator(`#${name}`).first();
    if ((await page.locator(`#${name}`).count()) > 0) { await byId.fill(value); return true; }
    const byLabel = page.getByLabel(label).first();
    if ((await page.getByLabel(label).count()) > 0) { await byLabel.fill(value); return true; }
    return false;
  };
  const f1 = await fill('字段', 'field', '分级');
  const f2 = await fill('原始值', 'original_value', 'TR3');
  const f3 = await fill('修正值', 'corrected_value', 'TR4');
  const f4 = await fill('原因', 'reason', 'p33 医生视角操作验证：造影提示微钙化，判为 TR4');
  record('D5.1 反馈表单可填写（字段/原始值/修正值/原因）', f1 && f2 && f3 && f4, `field=${f1} orig=${f2} corr=${f3} reason=${f4}`);

  const correctionChecked = await page.getByRole('radio', { name: /修正/ }).first().isChecked().catch(() => null);
  record('D5.2 反馈类型默认「修正」（采集器要的就是这一类）', correctionChecked !== false, `correctionChecked=${correctionChecked}`);

  // ============ D6 提交并断言结果 ============
  await page.getByRole('button', { name: /提交反馈/ }).first().click();
  let successMsg = false;
  for (let i = 0; i < 15 && !successMsg; i += 1) {
    successMsg = (await page.locator('text=反馈提交成功').count()) > 0;
    if (!successMsg) await sleep(page, 500);
  }
  const fb = feedbackLog[feedbackLog.length - 1] || {};
  record(
    'D6.1 POST /api/v1/feedback 被调用且返回 2xx',
    fb.status >= 200 && fb.status < 300,
    `http=${fb.status ?? '未发出'} body=${(fb.body || '').slice(0, 120)}`,
  );
  record('D6.2 医生看到「反馈提交成功」提示', successMsg, successMsg ? '已提示' : '未出现成功提示（提交未生效）');

  // ============ D7 挂载正确性：反馈必须落在**本次判读**上 ============
  // 这一条是「医生标注通道」的命门：评测集按 `request_id` 把医生修正关联为「正确口径」，
  // 若反馈挂到了别的判读（或没挂上），进度条永远涨不上去，而且**不会报错**。
  let attachedId = null;
  try { attachedId = JSON.parse(fb.body || '{}').request_id || null; } catch {}
  record(
    'D7 反馈挂在**本次判读**上（响应 request_id == 抓到的判读 id）',
    Boolean(attachedId) && attachedId === lastDiagnosisRequestId,
    `响应=${attachedId || '-'} 本次判读=${lastDiagnosisRequestId || '-'}`,
  );

  await page.screenshot({ path: path.join(OUT, 'doctor_feedback.png'), fullPage: true }).catch(() => {});
  await ctx.close();
} catch (err) {
  console.error(`[ERROR] ${err?.message || err}`);
  checks.push({ name: 'runner', ok: false, detail: String(err?.message || err) });
} finally {
  await browser.close();
}

const failed = checks.filter((c) => !c.ok);
const report = {
  probe: 'p33_doctor_actions',
  base: BASE,
  patient: PATIENT,
  organ: ORGAN,
  dicom: DICOM,
  diagnosis_request_id: lastDiagnosisRequestId,
  feedback_calls: feedbackLog,
  checks,
  passed: checks.length - failed.length,
  failed: failed.length,
  wall_s: Number(((Date.now() - T0) / 1000).toFixed(1)),
  evidence: OUT,
};
fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 2), 'utf-8');
console.log(`== p33 医生视角操作: PASS=${report.passed} FAIL=${report.failed}（${report.wall_s}s）  证据：${OUT}`);
process.exit(failed.length === 0 ? 0 : (checks.length === 0 ? 2 : 1));
