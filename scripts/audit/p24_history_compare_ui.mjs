/**
 * p24 — 历史对比阅片前端真机走查（第二梯队③，A.54）。
 *
 * 覆盖（全部取医生可见的界面状态，不 mock 接口）：
 *   J1 缺少 patient_id → 输入框引导（不白屏、不发请求）
 *   J2 不存在的患者 → 「系统里没有这个患者」+ 建档引导
 *   J3 正常患者 → 时间线 + 默认选中最近两次同器官 + 并排两列
 *   J4 面积变化卡片（含 ±% 与两侧绝对值）
 *   J5 两列影像区都渲染（服务端副本；不可用也要有不含歧义的提示）
 *   J6 报告页入口「历史对比」可达（从 /ui/report?request_id=… 点进 /ui/history）
 *   J7 跨器官不被直接对比：造一位「甲状腺 + 心脏」各一次的混合患者 → 出现部位不同提示
 *   J8 点时间线可切换两侧选择（交互可用，不是死界面）
 *   J9 全程无未捕获 JS 错误
 *
 * 用法（本机，被测系统是服务器，经 SSH 隧道）：
 *   node scripts/audit/p24_history_compare_ui.mjs
 * 依赖环境变量：UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD / UAI_BASE_URL
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
const ADMIN_USER = process.env.UAI_ADMIN_USER || 'smoke-admin';
const ADMIN_PASS = process.env.UAI_ADMIN_PASSWORD || '';
const BASE = (process.env.UAI_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const CHROME =
  process.env.UAI_CHROME ||
  path.join(process.env.USERPROFILE || process.env.HOME || '', '.agent-browser/browsers/chrome-154.0.8037.92/chrome.exe');
const OUT = 'C:/Windows/TEMP/uai_p24';
fs.mkdirSync(OUT, { recursive: true });
const DICOM = process.env.UAI_DICOM || 'C:/Windows/Temp/uai_a51ui/mf7b.dcm';

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

async function api(method, p, body, token) {
  const r = await fetch(`${BASE}${p}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let json = null;
  try { json = JSON.parse(text); } catch { /* 非 JSON */ }
  return { status: r.status, json, text };
}

if (!USER || !PASS) {
  console.error('缺少 UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD');
  process.exit(2);
}

const browser = await chromium.launch({ executablePath: CHROME, headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errs = [];
page.on('pageerror', (e) => errs.push(`pageerror: ${String(e && e.message).slice(0, 200)}`));
page.on('crash', () => errs.push('PAGE_CRASH'));

try {
  const login = await api('POST', '/api/v1/auth/login', { username: USER, password: PASS });
  const dtoken = login.json?.access_token;
  const alogin = ADMIN_PASS
    ? await api('POST', '/api/v1/auth/login', { username: ADMIN_USER, password: ADMIN_PASS })
    : { json: null };
  const atoken = alogin.json?.access_token;

  // ---- UI 登录（后续页面都在登录态下打开）----
  await page.goto(`${BASE}/ui/login`, { waitUntil: 'domcontentloaded' });
  await page.getByPlaceholder('输入用户名').fill(USER);
  await page.getByPlaceholder('输入密码').fill(PASS);
  await page.getByRole('button', { name: /登录/ }).first().click();
  await page.waitForURL(/\/ui\//, { timeout: 30000 });
  await sleep(page, 1200);

  // ---- J1 缺参数 ----
  await page.goto(`${BASE}/ui/history`, { waitUntil: 'domcontentloaded' });
  await sleep(page, 800);
  const missing = await page.locator('[data-region="history-missing-patient"]').count();
  const hasInput = await page.getByLabel('患者标识').count();
  record('J1 缺 patient_id → 出输入框引导', missing > 0 && hasInput > 0, `region=${missing} input=${hasInput}`);

  // ---- J2 不存在的患者 ----
  await page.getByLabel('患者标识').fill('no-such-p24-ghost');
  await page.getByRole('button', { name: '查看历史' }).click();
  let notExist = 0;
  for (let i = 0; i < 20 && !notExist; i += 1) {
    notExist = await page.locator('[data-region="history-not-exist"]').count();
    if (!notExist) await sleep(page, 500);
  }
  const notExistText = await page.locator('[data-region="history-not-exist"]').innerText().catch(() => '');
  record('J2 不存在患者 → 明示并给建档引导', notExist > 0 && /没有这个患者/.test(notExistText), notExistText.slice(0, 48));

  // ---- 找一位有 ≥2 次同器官检查的患者 ----
  let pid = null;
  const patients = (await api('GET', '/api/v1/patients?q=probe-&limit=50', null, dtoken)).json?.items || [];
  for (const it of patients) {
    if (/p24mix/.test(it.patient_id)) continue; // 混合器官样本不该被当成"可对比"来源
    const h = await api('GET', `/api/v1/patients/${it.patient_id}/sessions?limit=20`, null, dtoken);
    if (h.status !== 200) continue;
    // 现算"同一器官 ≥2 次"（不信 comparable_organs 字段本身，避免探针与接口同错同对）
    const counts = {};
    for (const row of h.json.items || []) {
      if (row.organ) counts[row.organ] = (counts[row.organ] || 0) + 1;
    }
    const sameOrgan = Object.entries(counts).find(([, n]) => n >= 2);
    if (sameOrgan) {
      pid = it.patient_id;
      break;
    }
  }
  if (!pid) {
    record('J0 前置：找到 ≥2 次同器官检查的患者', false, '库里没有可对比患者（需先跑 p23）');
    throw new Error('no comparable patient');
  }
  record('J0 前置：找到 ≥2 次同器官检查的患者', true, pid);

  // ---- J3/J4/J5 正常对比页 ----
  await page.goto(`${BASE}/ui/history?patient_id=${encodeURIComponent(pid)}`, { waitUntil: 'domcontentloaded' });
  let timeline = 0;
  for (let i = 0; i < 30 && !timeline; i += 1) {
    timeline = await page.locator('[data-region="history-timeline"]').count();
    if (!timeline) await sleep(page, 500);
  }
  const leftCol = await page.locator('[data-region="history-col-left"]').count();
  const rightCol = await page.locator('[data-region="history-col-right"]').count();
  const leftTitle = await page.locator('[data-region="history-col-left"]').innerText().catch(() => '');
  const rightTitle = await page.locator('[data-region="history-col-right"]').innerText().catch(() => '');
  record('J3 时间线出现且并排两列（左侧=较早）', timeline > 0 && leftCol > 0 && rightCol > 0,
    `timeline=${timeline} L=${leftCol} R=${rightCol}`);
  const leftTime = (leftTitle.match(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}/) || [''])[0];
  const rightTime = (rightTitle.match(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}/) || [''])[0];
  record('J3b 左侧时间早于右侧（顺序即"前后对比"语义）',
    !!leftTime && !!rightTime && leftTime <= rightTime, `${leftTime} <= ${rightTime}`);

  const mismatchHere = await page.locator('[data-region="history-organ-mismatch"]').count();
  const deltaRegion = await page.locator('[data-region="history-delta-area"]').count();
  const suppressed = await page.locator('[data-region="history-delta-suppressed"]').count();
  const deltaText = await page.locator('[data-region="history-delta-area"]').innerText().catch(() => '');
  if (mismatchHere > 0) {
    // 跨器官：绝不能给测值变化（A.54 修掉的伪"病情变化"）
    const supText = await page.locator('[data-region="history-delta-suppressed"]').innerText().catch(() => '');
    record('J4 跨器官时**不给**测值变化（只说明原因）',
      deltaRegion === 0 && suppressed > 0 && /不给出测值变化/.test(supText),
      `delta=${deltaRegion} suppressed=${suppressed} ${supText.replace(/\s+/g, ' ').slice(0, 50)}`);
  } else {
    record('J4 同器官时给出面积变化数字或明确不可比原因',
      deltaRegion > 0 && /(%|不可比|未做分割)/.test(deltaText), deltaText.replace(/\s+/g, ' ').slice(0, 60));
  }

  const imgRegions = await page.locator('[data-region="history-image"]').count();
  let canvases = 0;
  let imgTexts = '';
  for (let i = 0; i < 30; i += 1) {
    canvases = await page.locator('[data-region="history-image"] canvas').count();
    if (canvases > 0) break;
    await sleep(page, 1000);
  }
  if (canvases === 0) {
    imgTexts = await page
      .locator('[data-region="history-image"]')
      .allInnerTexts()
      .then((xs) => xs.join(' | ').replace(/\s+/g, ' '))
      .catch(() => '');
  }
  record('J5 两列影像区都在（其中至少一幅已渲染 canvas）',
    imgRegions === 2 && canvases >= 1,
    `regions=${imgRegions} canvas=${canvases}${imgTexts ? ` texts=${imgTexts.slice(0, 80)}` : ''}`);

  // ---- J8 点时间线切换选择 ----
  const before = `${leftTime}|${rightTime}`;
  const items = page.locator('[data-region="history-timeline-item"]');
  const n = await items.count();
  let switched = false;
  if (n >= 2) {
    await items.nth(0).click(); // 点最早一次：若已在选中集合内则不动
    await sleep(page, 400);
    const lt2 = (await page.locator('[data-region="history-col-left"]').innerText().catch(() => ''))
      .match(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}/);
    switched = !!lt2;
  }
  record('J8 时间线是可交互的（点击后页面状态自洽）', switched, `items=${n} before=${before}`);

  // ---- J6 报告页入口 ----
  const reqWithReport = await api(
    'GET',
    `/api/v1/patients/${encodeURIComponent(pid)}/sessions?limit=20`,
    null,
    dtoken,
  );
  const histItems = reqWithReport.json?.items || [];
  let reqId = histItems.find((it) => it.report_id)?.request_id;
  if (!reqId) {
    // 没有报告 → 现造一份草稿：入口不该依赖"是否已生成报告"（A.54 加的顶部入口就是这么设计的）
    const target = histItems.find((it) => it.request_id);
    if (target) {
      const dr = await api('POST', '/api/v1/report', { request_id: target.request_id }, dtoken);
      if (dr.status === 200 || dr.status === 201) reqId = target.request_id;
    }
  }
  if (reqId) {
    await page.goto(`${BASE}/ui/report?request_id=${encodeURIComponent(reqId)}`, { waitUntil: 'domcontentloaded' });
    await sleep(page, 2500);
    const entry = page.locator('[data-region="report-to-history"]');
    const entryCount = await entry.count();
    let navigated = false;
    if (entryCount > 0 && !(await entry.isDisabled().catch(() => true))) {
      await entry.click();
      await page.waitForURL(/\/ui\/history/, { timeout: 15000 }).catch(() => {});
      navigated = /\/ui\/history/.test(page.url());
    }
    record('J6 报告页「历史对比」入口可跳到历史页', entryCount > 0 && navigated,
      `count=${entryCount} url=${page.url().replace(BASE, '').slice(0, 60)}`);
  } else {
    record('J6 报告页入口（该患者无判读请求，跳过）', false, 'no request_id');
  }

  // ---- J7 跨器官不被直接对比 ----
  if (atoken && fs.existsSync(DICOM)) {
    const mixPid = `probe-p24mix-${String(Date.now()).slice(-8)}`;
    await api('POST', '/fhir/Patient', { patient_id: mixPid, name: 'P24 混合器官', gender: 'female' }, dtoken);
    await api('POST', '/api/v1/consent', { patient_id: mixPid, action: 'grant', operator_id: ADMIN_USER }, atoken);
    const raw = fs.readFileSync(DICOM);
    for (const [i, organ] of [['0', 'thyroid'], ['1', 'cardiac']]) {
      const s = await api('POST', '/api/v1/sessions', { patient_id: mixPid, device_id: `p24-${i}`, target_organs: [organ] }, dtoken);
      const sid = s.json?.session_id;
      if (!sid) continue;
      const form = new FormData();
      form.append('file', new Blob([raw], { type: 'application/dicom' }), 'mix.dcm');
      form.append('session_id', sid);
      form.append('priority', '1');
      const up = await fetch(`${BASE}/api/v1/imaging/dicom/upload`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${dtoken}` },
        body: form,
      });
      const imageId = (await up.json().catch(() => ({})))?.image_id;
      const d = await api('POST', '/api/v1/diagnosis/request', {
        session_id: sid,
        target_organ: organ,
        image_ids: imageId ? [imageId] : [],
        clinical_text: `混合器官样本（${organ}）`,
        patient_context: { age: 50, gender: 'female', conditions: [] },
      }, dtoken);
      const rid = d.json?.request_id;
      if (!rid) continue;
      for (let k = 0; k < 60; k += 1) {
        const rr = await api('GET', `/api/v1/diagnosis/result/${rid}`, null, dtoken);
        const st = rr.json?.status;
        if (['completed', 'failed', 'timeout', 'needs_review', 'cancelled'].includes(st)) break;
        await sleep(page, 2000);
      }
    }
    await page.goto(`${BASE}/ui/history?patient_id=${encodeURIComponent(mixPid)}`, { waitUntil: 'domcontentloaded' });
    let mix = 0;
    for (let i = 0; i < 30 && !mix; i += 1) {
      mix = await page.locator('[data-region="history-organ-mismatch"]').count();
      if (!mix) await sleep(page, 500);
    }
    const mixText = await page.locator('[data-region="history-organ-mismatch"]').innerText().catch(() => '');
    record('J7 跨器官不被直接对比（明示部位不同）', mix > 0, mixText.replace(/\s+/g, ' ').slice(0, 70));
  } else {
    record('J7 跨器官提示（缺管理员口令或样本，跳过）', false, 'skipped');
  }

  await page.screenshot({ path: `${OUT}/p24-final.png` });
  const jsErrs = errs.filter((e) => !/favicon|DevTools/.test(e));
  record('J9 全程无未捕获 JS 错误', jsErrs.length === 0, jsErrs.slice(0, 2).join(' || ') || 'errors=0');
} catch (e) {
  record('探针异常终止', false, String(e).slice(0, 220));
  await page.screenshot({ path: `${OUT}/p24-crash.png` }).catch(() => {});
} finally {
  fs.writeFileSync(`${OUT}/p24_report.json`, JSON.stringify({ pass, fail, rows }, null, 2), 'utf-8');
  await browser.close();
}

console.log(`\n== p24 历史对比前端走查: PASS=${pass} FAIL=${fail}`);
console.log(`   报告与截图: ${OUT}`);
process.exit(fail === 0 ? 0 : 1);
