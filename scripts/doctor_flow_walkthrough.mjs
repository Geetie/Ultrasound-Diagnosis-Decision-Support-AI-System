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
 *   node scripts/doctor_flow_walkthrough.mjs --dicom <样本路径[,样本路径2]>
 *
 * 样本制度（2026-10-09 用户明令）：**走查/演示/评测一律真实数据**。
 * 影像样本用 scripts/camus_frame_to_dicom.py 在服务器上从 CAMUS 心超序列现转
 * （多帧走查给 2 帧不同帧号），scp 回本机后传给 --dicom。
 * 合成样本 make_test_dicom.py 只允许用于 DICOM 协议/PHI 剥离冒烟，
 * 不得再出现在医生流程走查与视频里（A.38 教训：视频里的图是假纹理）。
 *
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
// --dicom 接受逗号分隔的多帧真实样本（多帧走查需要多张）；制度（2026-10-09 用户明令）：
// 走查/演示/评测一律真实数据（CAMUS），合成样本（make_test_dicom.py）仅限 DICOM 协议冒烟
const DICOMS = (dicomIdx >= 0 ? args[dicomIdx + 1] : process.env.UAI_SMOKE_DICOM)
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);
const BASE = process.env.UAI_BASE_URL || 'http://127.0.0.1:8000';
const USER = process.env.UAI_DOCTOR_USER;
const PASSWORD = process.env.UAI_DOCTOR_PASSWORD;
const OUT = 'C:/Windows/TEMP/uai_doctor_flow';

if (!USER || !PASSWORD) {
  console.error('需要 UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD 环境变量（密码不落盘）');
  process.exit(2);
}
if (DICOMS.length === 0 || DICOMS.some((p) => !fs.existsSync(p))) {
  console.error(
    '需要 --dicom <路径[,路径2,...]> —— 一律真实数据（制度 2026-10-09）：' +
      '服务器上用 scripts/camus_frame_to_dicom.py 从 CAMUS 现转，scp 回本机使用',
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
  // 每步带全程耗时戳 —— 用户看视频说"等了 33 秒"时，插桩数据是唯一裁判
  console.log(`[${ok ? 'PASS' : 'FAIL'}] +${elapsed()} ${name}${detail ? ' — ' + detail : ''}`);
};
const sleep = (page, ms) => page.waitForTimeout(ms);

const b = await chromium.launch({ executablePath: chrome, headless: true });
// 全程录像（Playwright screencast，headless 可用）：视频在页面关闭时落盘，
// 结束后从 OUT/video 挪到 --video 指定的最终路径（默认本机 Videos 目录）。
const videoDir = path.join(OUT, 'video');
fs.mkdirSync(videoDir, { recursive: true });
const page = await b.newPage({
  viewport: { width: 1512, height: 950 },
  recordVideo: { dir: videoDir, size: { width: 1512, height: 950 } },
});
const video = page.video();

// 视频打点器：screencast 只在**重绘**时出帧 —— 判读等待的几十秒静态画面会被整段
// 压缩掉（首录实测视频只有 17s）。body 透明度在 0.999↔1 间微幅切换强制合成器出帧，
// 肉眼不可见，视频时长因此 = 真实墙钟时间。
// 必须用 addInitScript：**每次文档加载都重新注入** —— 用 page.evaluate 注入的
// 版本在第一次 goto 全量导航时就随旧文档一起消失了（踩过）。
await page.addInitScript(() => {
  setInterval(() => {
    const b = document.body;
    if (b) b.style.opacity = b.style.opacity === '0.999' ? '1' : '0.999';
  }, 400);
});
const errs = [];
// 资源加载失败的 console 文本不含 URL —— 把来源 URL 一起带上，豁免才能写得精确
page.on('console', (m) => {
  if (m.type() !== 'error') return;
  const url = (m.location() && m.location().url) || '';
  errs.push(m.text() + ' @ ' + url);
});
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

  // 身份必须数据驱动（A.36 P0）：不允许再出现写死的「张**」
  let identityOk = false;
  let identityText = '';
  for (let i = 0; i < 20 && !identityOk; i += 1) {
    identityText = await page.locator('.ws-bar__identity').innerText().catch(() => '');
    identityOk = identityText.trim().length > 0 && !identityText.includes('张**');
    if (!identityOk) await sleep(page, 500);
  }
  record('3b. 常驻条身份数据驱动（非写死「张**」）', identityOk, identityText.trim().slice(0, 30));

  // ---- 3) 装载 DICOM → 自动判读
  let fileInput = null;
  for (let i = 0; i < 30 && fileInput === null; i += 1) {
    fileInput = await page.$("input[type=file][accept*='.dcm']");
    if (fileInput === null) await sleep(page, 500);
  }
  record('4. 装载入口出现', fileInput !== null);
  if (!fileInput) { await shot('04-no-input'); throw new Error('no file input'); }

  // 多帧（A.38 分期①）：同一会话装两帧 —— 指示器/翻帧才有真实数据源，
  // 视频里也才有翻帧动作可看。管线本就收 image_ids 数组。
  await fileInput.setInputFiles(DICOMS.length > 1 ? DICOMS : [DICOMS[0], DICOMS[0]]);

  // 浏览工具胶囊组（窗宽窗位/缩放/平移）—— 工具注册早已在，激活入口是本轮补的
  let toolsOk = false;
  for (let i = 0; i < 20 && !toolsOk; i += 1) {
    toolsOk = (await page.locator('[data-region=browse-tools]').count()) > 0;
    if (!toolsOk) await sleep(page, 500);
  }
  record('4b. 影像浏览工具胶囊组出现（窗宽窗位/缩放/平移）', toolsOk);

  // 帧指示器：两帧 → 「1/2」，键盘 → 翻到「2/2」（视频里的翻帧动作）
  let indicatorOk = false;
  for (let i = 0; i < 30 && !indicatorOk; i += 1) {
    indicatorOk = (await page.locator('[data-region=frame-indicator]').innerText().catch(() => '')).includes('1/2');
    if (!indicatorOk) await sleep(page, 500);
  }
  record('4c. 帧指示器出现（1/2）', indicatorOk);
  if (indicatorOk) {
    await page.keyboard.press('ArrowRight');
    let flipped = false;
    for (let i = 0; i < 20 && !flipped; i += 1) {
      flipped = (await page.locator('[data-region=frame-indicator]').innerText().catch(() => '')).includes('2/2');
      if (!flipped) await sleep(page, 300);
    }
    record('4d. 键盘 → 翻帧到 2/2', flipped);
    await shot('04e-frame2');
    }

    // 生成进度显示（A.43）：「分析中」副行出现「已生成 N 字」（后端 SSE→Redis→轮询）。
    // 注意 locator.innerText() 有 30s auto-wait（元素不存在时挂起）—— 判读未开始时
    // 副行根本不在 DOM，必须 count() 先判存在再读（A.43 走查 +620s 卡死的根因）。
    let progressShown = false;
    for (let i = 0; i < 160 && !progressShown; i += 1) {
      if ((await page.locator('.ws-conclusion__subtitle').count()) > 0) {
        const sub = await page
          .locator('.ws-conclusion__subtitle')
          .innerText({ timeout: 500 })
          .catch(() => '');
        if (/已生成 \d+ 字/.test(sub)) progressShown = true;
      }
      if (!progressShown) await sleep(page, 500);
    }
    record('4h. 「分析中」显示已生成字数（流式进度）', progressShown);

  // 测量画线 + 标注存档（A.40）：长度工具 + canvas 拖拽 → 读数出现 + PUT 200
  let annPutOk = false;
  let annGetCount = -1;
  page.on('response', (res) => {
    const u = res.url();
    if (/\/images\/[^/]+\/annotations/.test(u)) {
      if (res.request().method() === 'PUT' && res.status() === 200) annPutOk = true;
      if (res.request().method() === 'GET' && res.status() === 200) {
        res.text().then((t) => {
          try { annGetCount = JSON.parse(t).annotations.length; } catch {}
        }).catch(() => {});
      }
    }
  });
  const lenBtn = await page.$("button[title='长度']");
  if (lenBtn) {
    await lenBtn.click();
    const layer = await page.$('[data-region=dicom-layer]');
    if (layer) {
      const box = await layer.boundingBox();
      if (box) {
        const cx = box.x + box.width / 2;
        const cy = box.y + box.height / 2;
        await page.mouse.move(cx - 60, cy - 40);
        await page.mouse.down();
        // cornerstone 有 400ms 双击容忍窗口：up 在窗口内会被当 click，
        // 不触发拖拽画线（A.40 探针实测）—— down 后停 600ms 再拖
        await sleep(page, 600);
        await page.mouse.move(cx + 70, cy + 50, { steps: 12 });
        await page.mouse.up();
      }
    }
    let readout = false;
    for (let i = 0; i < 20 && !readout; i += 1) {
      readout = (await page.locator('[data-region=measure-readout]').count()) > 0;
      if (!readout) await sleep(page, 500);
    }
    record('4f. 测量画线出 mm 读数', readout);
    for (let i = 0; i < 16 && !annPutOk; i += 1) await sleep(page, 250);
    record('4g. 标注存档 PUT 200', annPutOk);
  } else {
    record('4f. 测量画线出 mm 读数', false, '没有「长度」按钮');
  }
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

  // 报告草稿不是自动生成的（A.29 设计流程）：医生在报告页点「生成草稿」。
  // 没有草稿时页面会提示 Report not found —— 那是正常态，不是缺陷。
  // AppLayout 与页面状态是异步挂载的，按钮要轮询等（A.28.9 钉子 3）。
  let draftBtn = null;
  let statusEl = null;
  for (let i = 0; i < 40 && !draftBtn && !statusEl; i += 1) {
    draftBtn = await page.$("button:has-text('生成草稿')");
    statusEl = (await page.$$('[data-testid=report-status]').then((els) => els.length)) > 0;
    if (!draftBtn && !statusEl) await sleep(page, 500);
  }
  if (statusEl) {
    record('7b. 草稿已存在（无需生成）');
  } else if (draftBtn) {
    await draftBtn.click();
    let drafted = false;
    for (let i = 0; i < 60 && !drafted; i += 1) {
      drafted = (await page.$$('[data-testid=report-status]').then((els) => els.length)) > 0;
      if (!drafted) await sleep(page, 1000);
    }
    record('7b. 生成报告草稿', drafted);
    if (!drafted) { await shot('07b-draft-fail'); throw new Error('draft generation failed'); }
  } else {
    const btns = await page.$$eval('button', (els) => els.map((e) => e.textContent.trim()).filter(Boolean));
    throw new Error(`报告页既无草稿也无「生成草稿」按钮；现有按钮：${JSON.stringify(btns.slice(0, 10))}`);
  }
  await page.waitForSelector('[data-testid=report-status]', { timeout: 30000 });

  // 打印/导出 PDF（A.38，生命周期「分发」环节）：按钮在，纸面样式由 print.css 保证
  const printBtn = await page.$("button:has-text('打印 / 导出 PDF')");
  record('7c. 报告页有「打印 / 导出 PDF」入口', printBtn !== null);

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
    record('10. 知情确认弹窗出现（存在非阻断缺失）', true);
    await confirm.click();
  } else {
    record('10. 无需知情确认（完整性已过）', true);
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
// 豁免两类已知良性行为（照打印不吞掉）：
  // 1. Google Fonts CSP 拦截（若仍配置了外联）
  // 2. 草稿未生成时 GET /api/v1/report 的 404 —— 界面正是围绕它引导「生成草稿」的
  const blocking = errs.filter((t) => {
    if (/fonts\.googleapis\.com/.test(t)) return false;
    if (/status of 404/.test(t) && /\/api\/v1\/report/.test(t)) return false;
    return true;
  });
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
  // 视频落盘时序（踩过坑）：b.close() 返回后 screencast 可能仍在写最后一段，
  // 立即 rename 会 EBUSY、立即 exit 会截断文件。正确姿势：
  // 等 video.path() → 轮询文件大小连续两次不变 → 复制（不 rename）→ 再退出。
  await b.close();
  try {
    const raw = await video.path();
    let lastSize = -1;
    for (let i = 0; i < 20; i += 1) {
      const size = fs.existsSync(raw) ? fs.statSync(raw).size : -1;
      if (size > 0 && size === lastSize) break;
      lastSize = size;
      await new Promise((r) => setTimeout(r, 1000));
    }
    const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    const videoArg = args.indexOf('--video');
    const target =
      videoArg >= 0 && args[videoArg + 1]
        ? args[videoArg + 1]
        : path.join(os.homedir(), 'Videos', `US-DX-医生全流程走查-${stamp}.webm`);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.copyFileSync(raw, target);
    const mb = (fs.statSync(target).size / 1024 / 1024).toFixed(2);
    console.log(`视频已保存：${target}（${mb} MB）`);
  } catch (e) {
    console.log(`视频保存失败：${String(e).slice(0, 120)}（原始文件在 ${videoDir}）`);
  }
  process.exit(failed ? 1 : 0);
}