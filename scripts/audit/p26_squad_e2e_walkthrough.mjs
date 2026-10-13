#!/usr/bin/env node
/**
 * p26 —— 第一/第二梯队端到端摸排走查（医生视角：浏览器 → 后端全链路）
 *
 * 目的（2026-10-13 用户指令）：「对第一第二梯队的项进行端到端摸排走查，从前端
 * 走到后端。浏览器里要模拟医生的执行流程看到效果。」
 *
 * 一条医生流程串起两队的**界面可观测**项（判据只用医生看得见的文案/控件状态，
 * 不扒网络请求 —— 与 doctor_flow_walkthrough 同纪律）：
 *
 *   登录 → 建会话（患者候选下拉 / 设备）→ 装载真实 CAMUS 多帧 DICOM →
 *   工作区（cine 播放 · 帧级归属与「跳到该帧」· 测量读数 · 判读结论）→
 *   报告页 → 同一患者第二次检查（同部位）→ 历史对比页（并排影像 + 面积变化）
 *
 * 覆盖映射：
 *   [T1-3] 语音控件（前端面；引擎实效见 p9 / health engine_ready）
 *   [T1-5] 多帧分期③ 帧级归属与择优帧（A.51②）
 *   [T1-6] 设备/患者列表端点（A.51③）—— API 面 + 建会话页下拉接线
 *   [T1-7] 判读正常完成路径（A.51④ 降载的对照腿）
 *   [T2-1] cine 播放（A.52.3b）
 *   [T2-3] 历史对比阅片（A.54）
 *
 * **不在浏览器可验的项**（脚本末尾会打印侧证归属，避免"看起来全覆盖"）：
 *   [T1-1] 告警联系人 PUT 语义 —— 前端无该 UI → 侧证 p7_alerts.py
 *   [T1-2] fe-smoke 真浏览器守卫 —— 是工具本身，不是医生功能
 *   [T1-4] 检索口径闭合 —— 界面不暴露证据链 → 侧证 p15_retrieval_parity.py
 *   [T1-7] validate 降载路径 —— 界面只看到结果 → 侧证 p14c（降级 degraded=1）
 *   [T2-2] 生产容量 —— 非 UI → 侧证 A.53 阶梯压测（scripts/run_bench.sh）
 *
 * 样本制度（2026-10-09 用户明令）：走查一律真实数据。样本在服务器现转：
 *   ~/uai/venv/bin/python scripts/camus_frame_to_dicom.py --out /tmp/uai_w1.dcm \
 *       --count 7 --multiframe --keyword 4CH_half
 *   （第二次检查换 --keyword 2CH_half —— 同器官 cardiac，才谈得上前后对比）
 *   scp 回本机后传给 --dicom1 / --dicom2。
 *
 * 运行（密码只走环境变量，不落盘不进命令行）：
 *   UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD / UAI_BASE_URL \
 *   node scripts/audit/p26_squad_e2e_walkthrough.mjs --dicom1 w1.dcm --dicom2 w2.dcm
 */
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
function loadPlaywright() {
  try { return require('playwright'); } catch { /* 继续找全局 */ }
  // 本机 homedir 与 npm 全局前缀不同路径（A.28），问 npm 自己最可靠
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
const DICOM1 = argOf('--dicom1') || process.env.UAI_P26_DICOM1;
const DICOM2 = argOf('--dicom2') || DICOM1;
const BASE = (process.env.UAI_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const USER = process.env.UAI_DOCTOR_USER;
const PASSWORD = process.env.UAI_DOCTOR_PASSWORD;
const OUT = process.env.UAI_P26_OUT || 'C:/Windows/TEMP/uai_p26';
const ORGAN_LABEL = process.env.UAI_P26_ORGAN || '心脏'; // 两次检查必须同部位
const PATIENT_PREFERRED = process.env.UAI_P26_PATIENT || 'DRILL-PATIENT-001';

if (!USER || !PASSWORD) {
  console.error('需要 UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD（密码不落盘）');
  process.exit(2);
}
if (!DICOM1 || !fs.existsSync(DICOM1) || !fs.existsSync(DICOM2)) {
  console.error('需要 --dicom1 / --dicom2 指向真实 CAMUS 多帧样本（见文件头生成命令）');
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
const record = (id, name, ok, detail = '') => {
  checks.push({ id, name, ok, detail });
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${id} ${name}${detail ? ' — ' + detail : ''}  (+${elapsed()})`);
};

const browser = await chromium.launch({ executablePath: chrome, headless: true });
const page = await browser.newPage({ viewport: { width: 1512, height: 950 } });
const sleep = (ms) => page.waitForTimeout(ms);
const shot = (name) =>
  page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true }).catch(() => {});

const errs = [];
const badRes = [];
// 网络流水（诊断「点入口后视图不切换」用）：记录全部请求 + 4xx/5xx
const netLog = [];
page.on('request', (r) => netLog.push(`→ ${r.method()} ${r.url().replace(BASE, '')}`));
page.on('pageerror', (e) => errs.push(String(e && e.message).slice(0, 160)));
page.on('response', (r) => {
  if (r.status() >= 400) {
    badRes.push(`${r.status()} ${r.url().replace(BASE, '')}`);
    netLog.push(`  !! ${r.status()} ${r.url().replace(BASE, '')}`);
  }
});
page.on('console', (m) => {
  if (m.type() !== 'error') return;
  const loc = (m.location() && m.location().url) || '';
  errs.push(`${String(m.text()).slice(0, 120)}${loc ? ' @ ' + loc.replace(BASE, '') : ''}`);
});
let annPutOk = false;
page.on('response', (r) => {
  if (r.request().method() === 'PUT' && /\/images\/[^/]+\/annotations/.test(r.url()) && r.status() === 200) {
    annPutOk = true;
  }
});

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

const frameNo = async () => {
  const t = await page.locator('[data-region="frame-indicator"]').innerText().catch(() => '');
  const m = t.match(/(\d+)\s*\/\s*(\d+)/);
  return m ? { n: Number(m[1]), total: Number(m[2]) } : null;
};

/** 建会话（验证通过 → 选部位 → 设备 → 开始检查 → 带 session_id 直达工作区）。 */
async function createSession(patientId, deviceId) {
  await page.goto(`${BASE}/ui/session/new`, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('患者编号').fill(patientId);
  await page.getByRole('button', { name: /校验患者/ }).click();
  let verified = false;
  for (let i = 0; i < 30 && !verified; i += 1) {
    verified = (await page.locator('text=AI 辅助知情同意有效').count()) > 0;
    if (!verified) await sleep(600);
  }
  if (!verified) return { ok: false, why: '患者校验未通过（存在性/知情同意）' };
  // 部位显式点选：两次检查必须同部位，"前后对比"才成立。
  // 预设列表 ORGAN_PRESETS 已补入 'cardiac'（A.56：心脏图只能声明甲状腺会产出错误器官
  // 语义的结论）。脚本仍**如实记录实测选中的部位**，不假设按钮一定存在。
  const organBtn = page.getByRole('button', { name: ORGAN_LABEL });
  const hasOrgan = (await organBtn.count()) > 0;
  if (hasOrgan) await organBtn.first().click();
  await page.getByLabel('设备编号').fill(deviceId);
  await page.getByRole('button', { name: '开始检查' }).click();
  let ok = false;
  for (let i = 0; i < 40 && !ok; i += 1) {
    ok = /session_id=/.test(page.url());
    if (!ok) await sleep(500);
  }
  if (!ok) return { ok: false, why: '未跳到工作区（无 session_id）' };
  return {
    ok: true,
    sessionId: new URL(page.url()).searchParams.get('session_id'),
    organSelected: hasOrgan ? ORGAN_LABEL : '该部位不在预设列表（落默认 thyroid）',
  };
}

/** 装载样本并等帧指示器（多帧样本才有）。 */
async function loadDicomSample(file) {
  const input = page.locator('input[type="file"]').first();
  await input.setInputFiles([file]);
  let f = null;
  for (let i = 0; i < 60 && !f; i += 1) {
    f = await frameNo();
    if (!f) await sleep(1000);
  }
  return f;
}

/** 等判读结论（装载后自动发起）。返回 {ok, text}。 */
async function waitConclusion(maxSec = 150) {
  let text = '';
  for (let i = 0; i < maxSec / 2; i += 1) {
    text = (await page.locator('.ws-conclusion').innerText().catch(() => ''))
      .replace(/\s+/g, ' ')
      .trim();
    if (/恶性风险|建议|已确认|良性|不确定/.test(text)) return { ok: true, text };
    if (/AI 辅助暂不可用/.test(text)) return { ok: false, text };
    await sleep(2000);
  }
  return { ok: false, text };
}

const coverage = [
  ['T1-1 告警联系人 PUT 语义', '前端无该 UI（管理面 API）', 'scripts/audit/p7_alerts.py'],
  ['T1-2 fe-smoke 真浏览器守卫', '工具本身，非医生功能', 'make fe-smoke / scripts/frontend_browser_smoke.mjs'],
  ['T1-4 检索口径闭合', '界面不暴露证据链与知识库版本明细', 'scripts/audit/p15_retrieval_parity.py'],
  ['T1-7 validate 降载路径', '界面只看到结果（降载是内部行为）', 'scripts/audit/p14b_validate_bench.py（degraded=1）'],
  ['T2-2 生产容量验收', '非 UI（并发闸门 + 阶梯压测）', 'scripts/run_bench.sh → reports/prod_capacity_a53.json'],
];

try {
  // ================= A) 登录 =================
  await page.goto(`${BASE}/ui/login`, { waitUntil: 'domcontentloaded' });
  await page.getByPlaceholder('输入用户名').fill(USER);
  await page.getByPlaceholder('输入密码').fill(PASSWORD);
  await page.getByRole('button', { name: /登录/ }).first().click();
  let logged = false;
  for (let i = 0; i < 40 && !logged; i += 1) {
    logged = /\/ui\/?$/.test(page.url());
    if (!logged) await sleep(500);
  }
  record('T0', '登录并落在工作区', logged, page.url());
  await shot('a1-login');
  if (!logged) throw new Error('登录失败');

  // 语音入口（T1-3 前端面）：VoiceControl 挂在 **AppLayout** 上，而医生主流程四页
  // （工作列表/建会话/工作区/报告）刻意不套 AppLayout（routes.tsx 注释「设计包覆盖的
  // 4 条路由全部刻意不套」）—— 所以主流程页面上没有语音入口，只在遗留页
  // （质控/知识库/同意/反馈）可达。这里到反馈页验证入口真实存在。
  await page.goto(`${BASE}/ui/feedback`, { waitUntil: 'domcontentloaded' });
  await sleep(1500);
  const voiceBtn = await page.locator('[data-testid="voice-control-button"]').count();
  const dictateBtn = await page.getByRole('button', { name: /开始口述|语音填写/ }).count();
  record(
    'T1-3',
    '语音入口可达（反馈页：VoiceControl / 听写按钮）',
    voiceBtn > 0 || dictateBtn > 0,
    `voice-control=${voiceBtn} 听写=${dictateBtn}（注：主流程四页不套 AppLayout → 主流程无语音入口）`,
  );
  await shot('a2-voice-control');

  // ================= B) 患者/设备端点（T1-6）=================
  const st = await api('POST', '/api/v1/auth/login', { username: USER, password: PASSWORD });
  const stoken = st.json?.access_token;
  record('T1-6-probe', '脚本侧：医生 token 取得（用于独立取数）', !!stoken, `login=${st.status}`);

  const plist = await api('GET', '/api/v1/patients?q=DR&limit=10', null, stoken);
  const items = plist.json?.items || [];
  record(
    'T1-6a',
    'GET /api/v1/patients 返回患者候选（列表端点后端面）',
    plist.status === 200 && items.length > 0,
    `status=${plist.status} n=${items.length} first=${items[0]?.patient_id || '-'}`,
  );

  // 前端候选下拉：输入前缀 → 出现 patient-suggest
  await page.goto(`${BASE}/ui/session/new`, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('患者编号').fill(PATIENT_PREFERRED.slice(0, 5));
  let suggest = 0;
  for (let i = 0; i < 20 && suggest === 0; i += 1) {
    suggest = await page.locator('[data-region="patient-suggest"]').count();
    if (suggest === 0) await sleep(500);
  }
  const suggestText = await page
    .locator('[data-region="patient-suggest"]')
    .innerText()
    .catch(() => '');
  record(
    'T1-6b',
    '建会话页患者候选下拉出现（端点 → 前端接线）',
    suggest > 0,
    `region=${suggest} text=${suggestText.replace(/\s+/g, ' ').slice(0, 60)}`,
  );
  await shot('b1-patient-suggest');

  const dev = await api('GET', '/api/v1/admin/device?limit=5', null, stoken);
  const devNoAuth = await api('GET', '/api/v1/admin/device?limit=5', null, null);
  record(
    'T1-6c',
    'GET /api/v1/admin/device 端点存在且鉴权生效（非 404；权限码如实记录）',
    dev.status !== 404,
    `医生token=${dev.status} 无token=${devNoAuth.status}（403=需管理员角色，属权限边界而非端点缺失）`,
  );
  await page.goBack().catch(() => {});

  // ================= C) 第一次检查 =================
  let sess1 = null;
  for (const candidate of [PATIENT_PREFERRED, ...items.map((x) => x.patient_id).slice(0, 3)]) {
    const r = await createSession(candidate, `E2E-A-${Date.now() % 100000}`);
    if (r.ok) {
      sess1 = r.sessionId;
      record('T1-6d', `建会话成功并直达工作区（患者 ${candidate}）`, true, `session=${sess1} 部位=${r.organSelected}`);
      // A.56：影像与声明部位必须能对齐 —— 预设里缺该部位时医生只能错报器官
      record(
        'T1-6e',
        `建会话页部位预设含「${ORGAN_LABEL}」（声明部位可对齐真实影像）`,
        r.organSelected === ORGAN_LABEL,
        `实测选中部位=${r.organSelected}`,
      );
      break;
    }
    record('T1-6d', `建会话尝试失败（患者 ${candidate}）`, false, r.why);
  }
  await shot('c1-workspace-created');
  if (!sess1) throw new Error('无法建立第一个会话');

  const f1 = await loadDicomSample(DICOM1);
  record(
    'T2-1-pre',
    '第一次检查装载真实多帧样本 → 帧指示器出现',
    !!f1,
    JSON.stringify(f1),
  );
  await shot('c2-loaded');

  // ---- cine 播放（T2-1）----
  const isPlaying = async () =>
    (await page.locator('[data-region="cine-toggle"]').getAttribute('aria-pressed')) === 'true';
  const clickPlay = () => page.locator('[data-region="cine-toggle"]').click();

  await clickPlay();
  const seen = new Set();
  for (let i = 0; i < 14; i += 1) {
    const f = await frameNo();
    if (f) seen.add(f.n);
    await sleep(250);
  }
  record('T2-1a', '点「播放」后帧号推进（≥2 个不同帧）', seen.size >= 2, `seen=${[...seen].join(',')}`);
  record('T2-1b', '播放态 aria-pressed=true（可被无障碍读到）', await isPlaying());

  await clickPlay();
  await sleep(600);
  const p1 = await frameNo();
  await sleep(1200);
  const p2 = await frameNo();
  record('T2-1c', '「暂停」后帧号稳定', !!p1 && !!p2 && p1.n === p2.n, `${p1?.n} → ${p2?.n}`);
  await shot('c3-cine-paused');

  await page.getByRole('button', { name: '长度' }).click();
  await sleep(400);
  const lenBefore = await page.getByRole('button', { name: '长度' }).getAttribute('aria-pressed');
  await clickPlay();
  await sleep(700);
  const lenDuring = await page.getByRole('button', { name: '长度' }).getAttribute('aria-pressed');
  const wlDuring = await page.getByRole('button', { name: '窗宽窗位' }).getAttribute('aria-pressed');
  await clickPlay();
  record(
    'T2-1d',
    '播放中测量工具被强制停用（防测量归属错帧）',
    lenBefore === 'true' && lenDuring === 'false' && wlDuring === 'true',
    `长度 ${lenBefore}→${lenDuring} / 窗宽窗位 during=${wlDuring}`,
  );

  const fpsText = async () => (await page.locator('[data-region="cine-fps"]').innerText()).trim();
  const fps0 = await fpsText();
  await page.locator('[data-region="cine-fps"]').click();
  await sleep(300);
  const fps1 = await fpsText();
  await page.locator('[data-region="cine-fps"]').click();
  await sleep(300);
  const fps2 = await fpsText();
  record(
    'T2-1e',
    '速度档可切换（8→15→30fps）',
    fps0 === '8fps' && fps1 === '15fps' && fps2 === '30fps',
    `${fps0} → ${fps1} → ${fps2}`,
  );

  // ---- 测量（阅片生命周期最小闭环，随流程一并走）----
  const lenBtn = await page.$("button[title='长度']");
  if (lenBtn) {
    await lenBtn.click();
    const layer = await page.$('[data-region=dicom-layer]');
    const box = layer ? await layer.boundingBox() : null;
    if (box) {
      const cx = box.x + box.width / 2;
      const cy = box.y + box.height / 2;
      await page.mouse.move(cx - 60, cy - 40);
      await page.mouse.down();
      await sleep(600); // cornerstone 400ms 双击容忍窗口
      await page.mouse.move(cx + 70, cy + 50, { steps: 12 });
      await page.mouse.up();
    }
    let readout = false;
    for (let i = 0; i < 20 && !readout; i += 1) {
      readout = (await page.locator('[data-region=measure-readout]').count()) > 0;
      if (!readout) await sleep(500);
    }
    record('T2-1f', '测量画线出 mm 读数', readout);
    for (let i = 0; i < 16 && !annPutOk; i += 1) await sleep(250);
    record('T2-1g', '标注存档 PUT 200（持久化链路）', annPutOk);
    await shot('c4-measure');
  }

  // ---- 帧级归属 / 择优帧（T1-5）----
  // ---- 判读结论（T1-7 正常路径）----
  // ⚠️ 顺序：**先等结论落地，再验分割入口**。分割旁路（「跳到该帧」）是**结果的一部分**，
  // 判读本身要跑完管线（重启后冷启动实测 ~90s+），先等按钮会把「判读慢」误记成
  // 「分割旁路没出」（A.57 实测踩到：报告里明明有「取自第 6/7 帧」，T1-5a 却报 FAIL）。
  // 结论已落地之后按钮仍不出现，才是真缺陷。
  const c1 = await waitConclusion();
  record('T1-7a', '判读正常完成并出结论（降载的对照腿）', c1.ok, c1.text.slice(0, 90));
  await shot('c6-conclusion');
  if (!c1.ok) throw new Error('第一次判读未出结论');

  let jump = false;
  for (let i = 0; i < 12 && !jump; i += 1) {
    jump = (await page.locator('[data-region="segmentation-jump"]').count()) > 0;
    if (!jump) await sleep(1000);
  }
  if (jump) {
    const before = await frameNo();
    await page.locator('[data-region="segmentation-jump"]').click();
    await sleep(1200);
    const after = await frameNo();
    const overlay = await page.locator('[data-region="segmentation-overlay"]').count();
    const offFrame = await page.locator('[data-region="segmentation-off-frame"]').count();
    record(
      'T1-5a',
      '「跳到该帧」一步到位且覆盖层出现（帧级归属正确）',
      !!before && !!after && after.n !== before.n && overlay > 0 && offFrame === 0,
      `${before?.n} → ${after?.n} overlay=${overlay} offFrame=${offFrame}`,
    );
    await shot('c5-segmentation-frame');
  } else {
    record(
      'T1-5a',
      '「跳到该帧」入口出现（需分割结果）',
      false,
      '结论已落地但 12s 内未出现分割旁路入口 —— 结果含分割却没入口属真缺陷（本轮结果见 T1-5b/T1-5c）',
    );
  }

  // ================= D) 报告页 =================
  const reportBtn = await page.$("button:has-text('报告')");
  record('T1-7b', '工作区出现「报告」入口', reportBtn !== null);
  if (reportBtn) {
    // 诊断元信息（本轮定位「URL 已切但视图不动」用；定位完可精简）
    const entryMeta = await reportBtn.evaluate((el) => ({
      tag: el.tagName,
      cls: String(el.className),
      text: (el.textContent || '').trim(),
    }));
    const netFrom = netLog.length;
    await reportBtn.click();
    // 等**报告页 DOM** 就绪 —— 只看 URL 会被懒加载 chunk 抢跑（实测 URL 已切 /ui/report
    // 但 DOM 仍是工作区，四条判据因此同刻假性失败）。
    let onReport = false;
    for (let i = 0; i < 60 && !onReport; i += 1) {
      const body = await page.locator('body').innerText().catch(() => '');
      onReport = /报告查看与签发|该请求尚无报告/.test(body);
      if (!onReport) await sleep(500);
    }
    record('T1-7c', '点工作区「报告」入口后**视图**切换（DOM 就绪，非只看 URL）', onReport, page.url());
    if (!onReport) {
      await shot('d0-report-nav-stuck');
      const domState = await page.evaluate(() => ({
        href: location.href,
        wsRoot: !!document.querySelector('.ws-root'),
        antLayout: !!document.querySelector('.ant-layout'),
        reportHeading: /报告查看与签发/.test(document.body.innerText || ''),
        rootHtmlLen: (document.getElementById('root')?.innerHTML || '').length,
        head: (document.body.innerText || '').replace(/\s+/g, ' ').slice(0, 110),
      }));
      console.log(`DBG entry=${JSON.stringify(entryMeta)}`);
      console.log(`DBG dom=${JSON.stringify(domState)}`);
      console.log(`DBG net=${netLog.slice(netFrom, netFrom + 20).join(' | ') || '(click 后无任何请求)'}`);
      // 反证：同一 URL 直接导航能否渲染 —— 区分「路由/页面坏了」与「入口切换失效」
      const rid = new URL(page.url()).searchParams.get('request_id') || '';
      await page.goto(`${BASE}/ui/report?request_id=${encodeURIComponent(rid)}`, {
        waitUntil: 'domcontentloaded',
      });
      let direct = false;
      for (let i = 0; i < 40 && !direct; i += 1) {
        const b = await page.locator('body').innerText().catch(() => '');
        direct = /报告查看与签发|该请求尚无报告/.test(b);
        if (!direct) await sleep(500);
      }
      record('T1-7c2', '反证：同一 URL 直接导航可渲染报告页', direct, `rid=${rid}`);
      onReport = direct;
    }
    if (onReport) {
      // 报告草稿不自动生成（A.29 流程）：医生在报告页点「生成草稿」。
      const draftBtn = page.getByRole('button', { name: '生成草稿' });
      let statusEl = (await page.locator('[data-testid=report-status]').count()) > 0;
      if (!statusEl && (await draftBtn.count()) > 0) {
        await draftBtn.first().click();
        let drafted = false;
        for (let i = 0; i < 60 && !drafted; i += 1) {
          drafted = (await page.locator('[data-testid=report-status]').count()) > 0;
          if (!drafted) await sleep(1000);
        }
        record('T1-7d0', '报告草稿生成（医生点「生成草稿」）', drafted);
      } else {
        record('T1-7d0', '报告草稿已存在', statusEl);
      }
      const printBtn = await page.getByRole('button', { name: /打印 \/ 导出 PDF/ }).count();
      record('T1-7d', '报告页有「打印 / 导出 PDF」入口', printBtn > 0, `buttons=${printBtn}`);
      const body = (await page.locator('body').innerText().catch(() => '')).replace(/\s+/g, ' ');
      const frameRef = /第\s*\d+\s*\/\s*\d+\s*帧|\d+\s*\/\s*\d+\s*帧/.test(body);
      let frameDetail = frameRef
        ? (body.match(/第\s*\d+\s*\/\s*\d+\s*帧[^，。]{0,12}/) || [''])[0]
        : '未出现帧号';
      if (!frameRef) {
        // 失败时把原因钉死，避免下一位重复调查：**内容里没有** vs **模板没渲染**是两回事
        // （A.56 实测：结构化 content.nodule_summary 已含「取自第 6/7 帧」，但该腿
        //  organ=cardiac 的报告模板只渲染"心功能测量"，分割段（字段名仍是
        //  nodule_summary）未进正文 → 属报告模板侧缺口，不是判读链路丢数据）。
        const ridNow = new URL(page.url()).searchParams.get('request_id') || '';
        const rep = await api('GET', `/api/v1/report/by-request/${encodeURIComponent(ridNow)}`, null, stoken);
        const inContent = '帧' in JSON.stringify(rep.json ?? {});
        frameDetail = inContent
          ? `报告正文未见帧号，但后端 content 已含（organ=${rep.json?.organ}）→ 报告模板未渲染该段`
          : '报告内容（结构化 + 渲染）里都没有帧号 → 判读→报告数据链路缺口';
      }
      record('T1-5b', '报告正文含「第 X/Y 帧」帧级溯源（多帧③）', frameRef, frameDetail);
      const histEntry = await page.locator('[data-region="report-to-history"]').count();
      record('T2-3-pre', '报告页有「历史对比」入口', histEntry > 0, `entries=${histEntry}`);
      await shot('d1-report');
    }
  }

  // ================= E) 第二次检查（同患者同部位）=================
  const r2 = await createSession(PATIENT_PREFERRED, `E2E-B-${Date.now() % 100000}`);
  record('T2-3-a', '同一患者第二次建会话（同部位）', r2.ok, r2.ok ? r2.sessionId : r2.why);
  if (r2.ok) {
    const f2 = await loadDicomSample(DICOM2);
    record('T2-3-b', '第二次检查装载真实多帧样本（另一序列）', !!f2, JSON.stringify(f2));
    const c2 = await waitConclusion();
    record('T2-3-c', '第二次判读出结论（前后对比才有数据）', c2.ok, c2.text.slice(0, 70));
    await shot('e1-second-conclusion');
  }

  // ================= F) 历史对比页（T2-3）=================
  await page.goto(`${BASE}/ui/history?patient_id=${encodeURIComponent(PATIENT_PREFERRED)}`, {
    waitUntil: 'domcontentloaded',
  });
  let timeline = 0;
  for (let i = 0; i < 30 && !timeline; i += 1) {
    timeline = await page.locator('[data-region="history-timeline"]').count();
    if (!timeline) await sleep(500);
  }
  const leftCol = await page.locator('[data-region="history-col-left"]').count();
  const rightCol = await page.locator('[data-region="history-col-right"]').count();
  record('T2-3-d', '历史对比页时间线 + 并排两列（左早右晚）', timeline > 0 && leftCol > 0 && rightCol > 0,
    `timeline=${timeline} L=${leftCol} R=${rightCol}`);

  const mismatch = await page.locator('[data-region="history-organ-mismatch"]').count();
  const deltaRegion = await page.locator('[data-region="history-delta-area"]').count();
  const suppressed = await page.locator('[data-region="history-delta-suppressed"]').count();
  const deltaText = (await page.locator('[data-region="history-delta-area"]').innerText().catch(() => '')).replace(/\s+/g, ' ');
  if (mismatch > 0) {
    const supText = (await page.locator('[data-region="history-delta-suppressed"]').innerText().catch(() => '')).replace(/\s+/g, ' ');
    record('T2-3-e', '跨器官时不给测值变化（只说明原因）', deltaRegion === 0 && suppressed > 0, supText.slice(0, 60));
  } else {
    record('T2-3-e', '同器官给出面积变化（或明确不可比原因）', deltaRegion > 0 && /(%|不可比|未做分割)/.test(deltaText), deltaText.slice(0, 70));
  }

  const imgRegions = await page.locator('[data-region="history-image"]').count();
  let canvases = 0;
  for (let i = 0; i < 30 && canvases === 0; i += 1) {
    canvases = await page.locator('[data-region="history-image"] canvas').count();
    if (canvases === 0) await sleep(1000);
  }
  record('T2-3-f', '两列影像区都在且至少一幅渲染出 canvas', imgRegions === 2 && canvases >= 1,
    `regions=${imgRegions} canvas=${canvases}`);
  const frameNote = (await page.locator('body').innerText().catch(() => '')).match(/取第\s*\d+\s*\/\s*\d+\s*帧/);
  record('T1-5c', '历史对比页显示「取第 X/Y 帧」（帧级溯源）', !!frameNote, frameNote ? frameNote[0] : '未出现');
  await shot('f1-history-compare');

  // ================= G) 全程无未捕获错误 =================
  // 豁免：GET /report/by-request/{rid} 404 是**预期态**（该请求还没生成报告草稿，
  // 页面正是以这个 404 渲染「该请求尚无报告」引导医生点「生成草稿」），不是缺陷。
  const EXEMPT = /favicon|ResizeObserver|Download the React DevTools|report\/by-request\//i;
  const blocking = errs.filter((e) => !EXEMPT.test(e));
  record('T0-err', '全程 0 个非豁免 console/page error', blocking.length === 0,
    blocking.length ? JSON.stringify(blocking.slice(0, 3)) : 'errors=0');
  const badInteresting = badRes.filter((b) => !/favicon|report\/by-request\//i.test(b));
  record(
    'T0-http',
    '全程无 4xx/5xx 资源（前端产物完整性）',
    badInteresting.length === 0,
    badInteresting.length ? JSON.stringify(badInteresting.slice(0, 4)) : 'all 2xx/3xx',
  );
} catch (e) {
  record('走查中断', '端到端流程中断', false, String(e).slice(0, 200));
  await shot('zz-crash');
  try { fs.writeFileSync(`${OUT}/zz-crash.html`, await page.content()); } catch { /* 忽略 */ }
} finally {
  const pass = checks.filter((c) => c.ok).length;
  const fail = checks.length - pass;
  fs.writeFileSync(
    `${OUT}/p26_report.json`,
    JSON.stringify({ pass, fail, checks, dims: { dicom1: DICOM1, dicom2: DICOM2, patient: PATIENT_PREFERRED, organ: ORGAN_LABEL }, not_covered: coverage }, null, 2),
    'utf-8',
  );
  await browser.close();
  console.log(`\n== p26 梯队端到端走查: PASS=${pass} FAIL=${fail}`);
  console.log('   截图与报告: ' + OUT);
  console.log('   不在浏览器可验的项（侧证归属）:');
  for (const [id, why, ev] of coverage) console.log(`     · ${id} —— ${why} → ${ev}`);
  process.exit(fail === 0 ? 0 : 1);
}
