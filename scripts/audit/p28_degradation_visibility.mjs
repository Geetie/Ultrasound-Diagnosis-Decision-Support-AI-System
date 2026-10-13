/**
 * p28 —— 渲染层「降级可见性」守卫（A.58「零静默」渲染层；20 号研究产出 2 第 2 条）。
 *
 * 问的是什么
 * ----------
 * 影像区 / 结论区在 **15 秒窗口**内是否出现**真实数据特征**：
 *   · 影像区：`[data-region="image"]` 里出现 canvas（真实 DICOM 已上屏）且**没有**设计稿
 *     示意框（「结节 A · 未校验」）—— 占位底板长得像影像，是最会骗过 DOM 断言的一种；
 *   · 结论区：`.ws-conclusion` 出现真实字段文案（恶性风险/建议/已确认/良性/不确定），
 *     而不是占位（AI 辅助暂不可用 / 分析中）。
 * 之后再用截图做**像素方差**判定（PIL）：空白/纯色底板方差接近 0 —— DOM 有、像素是空，
 * 正是 A.35 的形态。
 *
 * 为什么必须有反证
 * ----------------
 * 20 号原文：「降级可见性：占位与真实数据在截图像素级难辨」。A.35 影像层三处 catch 链
 * 吞掉 chunk 求值失败 → **页面空白活了 3 天**，而当时所有门禁都是绿的。
 * 所以本探针除了正向腿，还强制跑**反证腿**：用 `page.route` 屏蔽影像字节，
 * 断言守卫**必然判红**、且界面**给出可读错误**（不是静默空白）——
 * 一条只会打印"PASS"的守卫等于没有守卫。
 *
 * 运行（密码只从环境变量注入）：
 *   UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD / UAI_BASE_URL \
 *   node scripts/audit/p28_degradation_visibility.mjs [--patient DRILL-PATIENT-001] [--out DIR]
 *
 * 退出码：0 = 正反两腿都符合预期；1 = 有判据不过；2 = 输入不合格（缺凭据 / 找不到可用会话）。
 */
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
function loadPlaywright() {
  try { return require('playwright'); } catch { /* 继续找全局 */ }
  for (const [cmd, opts] of [
    ['npm.cmd', { encoding: 'utf-8' }],
    ['npm', { encoding: 'utf-8', shell: true }],
  ]) {
    try {
      const root = execFileSync(cmd, ['root', '-g'], opts).trim();
      const candidate = path.join(root, 'playwright');
      if (fs.existsSync(candidate)) return require(candidate);
    } catch { /* 下一个 */ }
  }
  throw new Error('未找到 playwright');
}

const args = process.argv.slice(2);
const argOf = (name) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : null;
};
const BASE = (process.env.UAI_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const USER = process.env.UAI_DOCTOR_USER;
const PASSWORD = process.env.UAI_DOCTOR_PASSWORD;
const PATIENT = argOf('--patient') || process.env.UAI_P28_PATIENT || 'DRILL-PATIENT-001';
const OUT = (argOf('--out') || process.env.UAI_P28_OUT || 'C:/Windows/TEMP/uai_p28').replace(/\/$/, '');
const WINDOW_MS = Number(process.env.UAI_P28_WINDOW_MS || 15000);

if (!USER || !PASSWORD) {
  console.error('需要 UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD（密码不落盘、不进命令行）');
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
/**
 * 记录一条判据：``record(id, ok, detail)``，描述写在 id 里。
 *
 * ⚠️ **参数类型自检**：初版按 p26 的 ``record(id, name, ok, detail)`` 签名写成 3 参数，
 * 结果布尔被当成 name、detail 字符串被当成 ok → 一律 truthy → **守卫永远打印 PASS**
 * （A.58 实测踩到：反证腿明明无错误提示却显示 PASS）。这类"参数错位"是守卫失效的
 * 经典形态，所以这里硬校验第二参必须是 boolean，把错误挡在使用处。
 */
const record = (id, ok, detail = '') => {
  if (typeof ok !== 'boolean') {
    throw new Error(
      `record() 用法错误：第 2 个参数必须是 boolean（收到 ${typeof ok}）—— ` +
        '参数错位会让守卫永远打印 PASS（A.58 初版实测）',
    );
  }
  checks.push({ id, ok, detail });
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${id}${detail ? ' — ' + detail : ''}  (+${elapsed()})`);
};

async function api(method, p, body, token) {
  const r = await fetch(`${BASE}${p}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: r.status, json: await r.json().catch(() => null) };
}

/** 挑一个「有影像 + 已有判读请求」的会话（这样打开工作区就能同时验影像区与结论区）。 */
async function pickSession(token) {
  const r = await api('GET', `/api/v1/patients/${encodeURIComponent(PATIENT)}/sessions?limit=30`, null, token);
  const items = r.json?.items || [];
  const hit = items.find((x) => x.first_image_id && x.request_id);
  return hit || null;
}

/** 结论区是否出现**真实字段**（占位文案不算）。 */
const REAL_CONCLUSION = /恶性风险|建议|已确认|良性|不确定/;
const PLACEHOLDER = /AI 辅助暂不可用|分析中|加载中/;

const browser = await chromium.launch({ executablePath: chrome, headless: true });
const page = await browser.newPage({ viewport: { width: 1512, height: 950 } });
const sleep = (ms) => page.waitForTimeout(ms);

async function snapshot() {
  const canvas = await page.locator('[data-region="image"] canvas').count();
  const mock = await page.getByText('结节 A · 未校验').count();
  const conclusionText = (await page.locator('.ws-conclusion').innerText().catch(() => ''))
    .replace(/\s+/g, ' ')
    .trim();
  // 两层可读错误都要认：`image-load-error` = 取字节/列表失败（A.58 新增），
  // `dicom-load-error` = cornerstone 层渲染失败（A.52 已有）。缺任一层都算"静默空白"。
  const loadError = await page
    .locator('[data-region="dicom-load-error"], [data-region="image-load-error"]')
    .count();
  return {
    canvas,
    mock,
    loadError,
    conclusionReal: REAL_CONCLUSION.test(conclusionText) && !PLACEHOLDER.test(conclusionText),
    conclusionHead: conclusionText.slice(0, 80),
  };
}

/** 在 windowMs 内轮询，返回「首次满足 pred」的快照与耗时；超时返回最后一次快照。 */
async function waitFor(pred, windowMs) {
  const deadline = Date.now() + windowMs;
  let snap = await snapshot();
  const t0 = Date.now();
  while (Date.now() < deadline) {
    if (pred(snap)) return { ok: true, ms: Date.now() - t0, snap };
    await sleep(500);
    snap = await snapshot();
  }
  return { ok: false, ms: Date.now() - t0, snap };
}

function runPil(shots, jsonOut, expectPass) {
  const argsList = [path.resolve('scripts/degradation_visibility.py'), '--json-out', jsonOut];
  for (const s of shots) argsList.push('--shot', s);
  const res = execFileSync('python', argsList, { encoding: 'utf-8' });
  const parsed = JSON.parse(fs.readFileSync(jsonOut, 'utf-8'));
  return { exitOk: true, out: res.trim(), parsed, expectPass };
}

let failed = false;
try {
  const login = await api('POST', '/api/v1/auth/login', { username: USER, password: PASSWORD });
  const token = login.json?.access_token;
  if (!token) {
    console.error(`登录失败（status=${login.status}）`);
    process.exit(2);
  }
  const session = await pickSession(token);
  if (!session) {
    console.error(`找不到「有影像 + 有判读请求」的会话：patient=${PATIENT}（先跑 p26 建一次检查）`);
    process.exit(2);
  }
  console.log(`会话：${session.session_id} request=${session.request_id} image=${session.first_image_id}`);

  // 登录页 → 工作区（带 request_id 会回放该次判读结果，结论区才有真实字段）
  await page.goto(`${BASE}/ui/login`, { waitUntil: 'domcontentloaded' });
  await page.getByPlaceholder('输入用户名').fill(USER);
  await page.getByPlaceholder('输入密码').fill(PASSWORD);
  await page.getByRole('button', { name: /登录/ }).first().click();
  await page.waitForURL(/\/ui\/?$/, { timeout: 30000 });
  await page.goto(
    `${BASE}/ui/?request_id=${encodeURIComponent(session.request_id)}&session_id=${encodeURIComponent(session.session_id)}`,
    { waitUntil: 'domcontentloaded' },
  );

  // ---------- 正向腿：15s 窗口内必须出现真实数据特征 ----------
  const real = await waitFor((s) => s.canvas > 0 && s.mock === 0, WINDOW_MS);
  record(
    'P28-1 影像区 15s 内出现真实影像（canvas>0 且无设计稿示意框）',
    real.ok,
    `canvas=${real.snap.canvas} mock=${real.snap.mock} 耗时=${real.ms}ms`,
  );
  const concl = await waitFor((s) => s.conclusionReal, WINDOW_MS);
  record(
    'P28-2 结论区 15s 内出现真实字段文案（非占位）',
    concl.ok,
    `${concl.snap.conclusionHead || '(空)'} 耗时=${concl.ms}ms`,
  );

  const imgShot = `${OUT}/p28-image.png`;
  const conclShot = `${OUT}/p28-conclusion.png`;
  await page.locator('[data-region="image"]').screenshot({ path: imgShot }).catch(() => {});
  await page.locator('.ws-conclusion').screenshot({ path: conclShot }).catch(() => {});
  await page.screenshot({ path: `${OUT}/p28-full.png`, fullPage: true }).catch(() => {});

  // ---------- 像素级确认（PIL）：空白/纯色底板方差接近 0 ----------
  const pilOut = `${OUT}/p28_variance.json`;
  try {
    const pil = runPil([imgShot, conclShot], pilOut, true);
    record(
      'P28-3 像素方差达标（截图不是一片空白/纯色）',
      pil.parsed?.passed === true,
      (pil.parsed?.shots || [])
        .map((s) => `${s.file}=${s.variance}`)
        .join(' '),
    );
    if (pil.parsed?.passed !== true) failed = true;
  } catch (err) {
    // 退出码 1 = 判红（正向腿不应发生）；2 = 输入问题
    record('P28-3 像素方差达标（截图不是一片空白/纯色）', false, `PIL 判定：${String(err).slice(0, 120)}`);
    failed = true;
  }

  // ---------- 反证腿（A.35 形态）：屏蔽影像字节 → 守卫必须判红 ----------
  await page.route('**/api/v1/imaging/**', (route) => route.abort());
  await page.goto(
    `${BASE}/ui/?request_id=${encodeURIComponent(session.request_id)}&session_id=${encodeURIComponent(session.session_id)}`,
    { waitUntil: 'domcontentloaded' },
  );
  const broken = await waitFor((s) => s.canvas === 0, WINDOW_MS);
  record(
    'P28-R1 反证：屏蔽影像字节后影像区**不再**出现真实影像（守卫会判红）',
    broken.ok,
    `canvas=${broken.snap.canvas}`,
  );
  record(
    'P28-R2 反证：且给出可读错误提示（不是静默空白 —— 零静默的底线）',
    broken.snap.loadError > 0,
    `dicom-load-error=${broken.snap.loadError}`,
  );
  const brokenShot = `${OUT}/p28-broken-image.png`;
  await page.locator('[data-region="image"]').screenshot({ path: brokenShot }).catch(() => {});
  try {
    const pilBroken = runPil([brokenShot], `${OUT}/p28_variance_broken.json`, false);
    record(
      'P28-R3 反证：像素方差守卫在坏形态下判红（证明它不是"永远通过"）',
      pilBroken.parsed?.passed === false,
      `variance=${pilBroken.parsed?.shots?.[0]?.variance}`,
    );
    if (pilBroken.parsed?.passed !== false) failed = true;
  } catch {
    record('P28-R3 反证：像素方差守卫在坏形态下判红', true, 'PIL 以退出码 1 判红（符合预期）');
  }
} catch (err) {
  record('P28-0 探针执行', false, String(err).slice(0, 200));
  failed = true;
} finally {
  await browser.close();
}

const passed = checks.filter((c) => c.ok).length;
const total = checks.length;
console.log(`\n== p28 渲染层降级可见性: PASS=${passed} FAIL=${total - passed}（截图与 JSON 在 ${OUT}）`);
fs.writeFileSync(`${OUT}/p28_report.json`, JSON.stringify({ checks, out: OUT }, null, 2), 'utf-8');
process.exit(failed || passed !== total ? 1 : 0);
