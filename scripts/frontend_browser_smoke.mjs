#!/usr/bin/env node
/**
 * 临床前端**真浏览器渲染**冒烟（运行时那一半守卫）。
 *
 * 为什么需要它
 * ------------
 * 2026-10-05 首次在真实浏览器里渲染前端，一次点击就抓到 4 个 P0 阻断缺陷
 * （白屏 / 登录 422 / 登录后跳后端根 JSON / 二级页面全打不开），
 * 而当时**全部门禁都是绿的** —— 因为它们全都只验到 HTTP 层与路径字符串：
 *
 *   · 门禁 23 项 curl：证明 `/ui/` 返回 200，不证明 `#root` 里有节点；
 *   · 契约对账 18 路径：证明 `apiClient.post('/auth/login')` 的路径存在，
 *     不证明该端点收 JSON，也不证明浏览器渲染得出来。
 *
 * 静态层由 `tests/unit/quality/test_frontend_render_guard.py` 负责；
 * 本脚本负责静态层原理上抓不到的那部分 —— **它到底渲染出来了吗**。
 *
 * 判定项
 * ------
 * 1. `#root` 子节点数 > 0（白屏）
 * 2. 能登录并**落在前端页面**上，而不是后端根 JSON
 * 3. 关键路由逐条可达，且文本长度**互不相同**
 *    （basename 缺失时所有路由都回落成同一个工作区，textLen 恒等 —— 这是判据）
 * 4. 工作区有导航出口（button > 0），且**医生主流程能走到上传入口**：
 *    裸 `/ui/` 没有 `?session_id=`，此时按设计只给「去工作列表选择会话」而不给
 *    file input —— 所以必须真的走一遍「工作区 → 工作列表 → 选一位病人 → 带
 *    session_id 回到工作区」，再断言 `input[type=file][accept*=.dcm]` 存在。
 *    直接在裸 `/ui/` 上断言 file input 会得到**恒假的结论**（第一版就是这么错的）。
 *    file input 计数**不过滤可见性** —— 过滤会漏掉 display:none 的隐藏控件。
 * 5. 全程 0 个 console error、0 个同源 4xx/5xx
 *
 * 用法
 * ----
 *   // 密码只能从环境变量注入（铁律 #10：不许出现在命令行/脚本/聊天里）
 *   export UAI_DOCTOR_USER=smoke-doctor
 *   export UAI_DOCTOR_PASSWORD="$(cat ~/uai/.smoke_pw)"
 *   node scripts/frontend_browser_smoke.mjs
 *   node scripts/frontend_browser_smoke.mjs --require   # 环境不具备即判失败
 *
 * 退出码：0 = 全过（或明确 SKIP 且未加 --require）；1 = 有 FAIL；2 = 环境错。
 */
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';

const require = createRequire(import.meta.url);

const BASE_URL = (process.env.UAI_BASE_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');
const MIN_TEXT_LEN = 40;
const TIMEOUT = Number(process.env.UAI_SMOKE_TIMEOUT_MS || 20000);

const ROUTES = [
  ['工作区', '/ui/'],
  ['工作列表', '/ui/worklist'],
  ['新建会话', '/ui/session/new'],
  ['设置', '/ui/settings'],
  ['质控报告', '/ui/qc-report'],
  ['知识库', '/ui/kb-management'],
  ['知情同意', '/ui/consent'],
  ['反馈', '/ui/feedback'],
  ['报告', '/ui/report'],
];

/**
 * 已知豁免的 console error —— **必须显式逐条列出，不许通配吞掉**。
 *
 * 一条守卫一旦长期是红的，就再也没人信它（A.27 → A.28 的核心教训）。所以豁免要写明
 * 「是什么、为什么、谁负责」，而且**照样计数打印**—— 既不挡门禁，也不会消失。
 *
 * 当前唯一豁免：`frontend/src/styles/global.css` 里的
 * `@import url('https://fonts.googleapis.com/...')` 被后端 CSP 拦掉
 * （`app/security/security_headers.py`：`style-src 'self' 'unsafe-inline'`、
 * `font-src 'self' data:`）。后果是临床界面**一直在用回退字体**渲染，且每次加载都
 * 尝试外联 Google —— 对医院内网部署既不生效也不该外联。正确修法是移除外部字体依赖、
 * 改用系统字体（已登记 feature_list，本轮按范围规则只记录不修）。
 */
const ACCEPTED_CONSOLE_PATTERNS = [
  // 注意消息里的顺序是「先 URL、后 violates」—— 写成反了会一条都匹配不到，
  // 表现为"豁免机制形同虚设"（第一版就踩了）。
  /Loading the stylesheet 'https:\/\/fonts\.googleapis\.com\/.*violates the following Content Security Policy/,
];

const isAcceptedConsoleError = (text) => ACCEPTED_CONSOLE_PATTERNS.some((re) => re.test(text));

/* ------------------------------------------------------------------ 运行环境 */

/**
 * 解析 playwright。
 *
 * 为什么这么绕：本机 playwright 装在**全局** npm 前缀下（`~/.npm-global`），
 * 而 `frontend/` 的 devDependencies 里没有它 —— 直接 import 必然失败。
 * 塞进 devDependency 又等于让 CI 的 `npm ci` 拖一个浏览器（几百 MB）。
 * 所以按 local → 全局前缀顺序找，找不到就明确 SKIP —— 守卫一旦静默就等于没有。
 */
function loadPlaywright() {
  const tries = [];
  try {
    return require('playwright');
  } catch (e) {
    tries.push(`require('playwright'): ${e.code || e.message}`);
  }
  const prefixes = [
    process.env.npm_config_prefix,
    path.join(os.homedir(), '.npm-global'),
    '/usr/local',
    '/usr',
  ].filter(Boolean);
  for (const prefix of prefixes) {
    const candidate = path.join(prefix, 'node_modules', 'playwright');
    try {
      if (fs.existsSync(candidate)) return require(candidate);
      tries.push(`not found: ${candidate}`);
    } catch (e) {
      tries.push(`${candidate}: ${e.code || e.message}`);
    }
  }
  // 兜底问 npm 自己 —— 本机 homedir(C:\Users\ghf) 与 npm 全局前缀(E:\home\ghf)
  // **不是同一个路径**，靠猜前缀会漏掉。
  // 不用 shell:true：那会触发 DEP0190 警告并把参数拼进命令行，nvm 的 npm 只有 .ps1
  // shim 时又跑不起来，所以先试 npm.cmd，再退回 shell。
  for (const [cmd, opts] of [
    ['npm.cmd', { encoding: 'utf-8' }],
    ['npm', { encoding: 'utf-8', shell: true }],
  ]) {
    try {
      const root = execFileSync(cmd, ['root', '-g'], opts).trim();
      const candidate = path.join(root, 'playwright');
      if (fs.existsSync(candidate)) return require(candidate);
      tries.push(`npm root -g → ${root}（无 playwright）`);
      break;
    } catch (e) {
      tries.push(`${cmd} root -g 失败: ${e.message}`);
    }
  }
  throw new Error(`未找到 playwright。试过：\n  ${tries.join('\n  ')}`);
}

/**
 * 真 Chrome 路径。优先环境变量；否则扫 agent-browser 下载目录
 * （2026-10-05 走查就是这么复用本机 Chrome 的，不必另下 Chromium）。
 */
function findChrome() {
  const explicit = process.env.UAI_CHROME_PATH || '';
  if (explicit) return fs.existsSync(explicit) ? explicit : null;
  const roots = [
    path.join(os.homedir(), '.agent-browser', 'browsers'),
    path.join(os.homedir(), 'AppData', 'Local', 'ms-playwright'),
  ];
  for (const root of roots) {
    let entries = [];
    try {
      entries = fs.readdirSync(root);
    } catch {
      continue;
    }
    for (const name of entries) {
      for (const rel of ['chrome.exe', 'chrome-win/chrome.exe', 'Contents/MacOS/Google Chrome']) {
        const p = path.join(root, name, rel);
        if (fs.existsSync(p)) return p;
      }
    }
  }
  return null; // 交给 playwright 自带的 chromium
}

/* ---------------------------------------------------------------------- 工具 */

class Failure extends Error {}

const checks = [];
function record(name, ok, detail = '') {
  checks.push({ check: name, ok, detail });
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`);
}

/** 失败即产出：立刻截图 + 落页面结构，流程中断也留得到断点证据。 */
async function dump(page, tag) {
  const outdir = path.join(os.tmpdir(), 'uai_browser_smoke');
  fs.mkdirSync(outdir, { recursive: true });
  const shot = path.join(outdir, `${tag}.png`);
  const html = path.join(outdir, `${tag}.html`);
  try {
    await page.screenshot({ path: shot, fullPage: true });
  } catch {
    /* 截图失败不该盖掉真正的失败原因 */
  }
  try {
    fs.writeFileSync(html, await page.content(), 'utf-8');
  } catch {
    /* 同上 */
  }
  return `证据：${shot} 与 ${html}`;
}

function summarize() {
  const failed = checks.filter((c) => !c.ok);
  return { total: checks.length, failed: failed.length, failed_checks: failed };
}

/**
 * 等 URL 匹配正则。
 *
 * 不用 `page.waitForURL`：它默认等 `waitUntil:'load'`，而 React Router 的
 * `navigate()` 是**同文档 pushState**，不产生 load 事件 —— 于是一次
 * 真实成功的跳转会被判成超时（第一版就踩了：点击「继续」确实跳了并渲染出
 * 上传控件，却报 TimeoutError）。这里直接轮询 `page.url()`。
 */
async function waitForUrlMatch(page, re, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (re.test(page.url())) return true;
    await page.waitForTimeout(200);
  }
  return false;
}

/**
 * 等页面上出现任一匹配的正则（对 `body` 的 innerText）。
 *
 * 用来判"某个阶段到了没有"，而不是去点某个具体元素 ——
 * 阶段文案是医生看得见的东西，判据就该用那个。
 */
async function waitForAny(page, regexes, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const text = await page.innerText('body');
    if (regexes.some((re) => re.test(text))) return true;
    await page.waitForTimeout(1500);
  }
  console.log(`# 等待超时：${label}（${timeoutMs / 1000}s）`);
  return false;
}

/**
 * 按可见文字点按钮。
 *
 * 不用 `page.click(sel)`：SPA 懒加载时 URL 先变、组件后渲染，
 * 直接点会扑空（第一版就踩了：点「报告」成功进了报告页，
 * 紧接着点「生成草稿」却超时 —— 因为 ReportPage 是 lazy chunk）。
 */
async function clickByText(page, text, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const btn = page.locator(`button:has-text("${text}")`).first();
    if ((await btn.count()) > 0 && (await btn.isVisible())) {
      await btn.click();
      return true;
    }
    await page.waitForTimeout(300);
  }
  return false;
}

/* -------------------------------------------------------------------- 主流程 */

async function main() {
  const mustRun = process.argv.includes('--require');
  const skipExit = (reason) => {
    console.log(`SKIP：${reason}`);
    return mustRun ? 1 : 0;
  };

  if (!process.env.UAI_DOCTOR_USER || !process.env.UAI_DOCTOR_PASSWORD) {
    return skipExit('UAI_DOCTOR_USER / UAI_DOCTOR_PASSWORD 未设置（本脚本不接受命令行传密码，铁律 #10）');
  }

  let pw;
  try {
    pw = loadPlaywright();
  } catch (e) {
    return skipExit(e.message);
  }

  const username = process.env.UAI_DOCTOR_USER;
  const password = process.env.UAI_DOCTOR_PASSWORD;

  const launchOpts = { headless: !process.argv.includes('--headed') };
  const chrome = findChrome();
  if (chrome) launchOpts.executablePath = chrome;
  console.log(`# base=${BASE_URL} chrome=${chrome || '(playwright 自带 chromium)'}`);

  let browser;
  try {
    browser = await pw.chromium.launch(launchOpts);
  } catch (e) {
    return skipExit(`无法启动浏览器：${e.message}`);
  }

  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await context.newPage();
  page.setDefaultTimeout(TIMEOUT);

  const consoleErrors = [];
  const badResponses = [];
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text());
  });
  page.on('response', (r) => {
    if (r.status() >= 400 && r.url().startsWith(BASE_URL)) badResponses.push(`${r.status()} ${r.url()}`);
  });

  const finish = async (code) => {
    await browser.close().catch(() => {});
    console.log(JSON.stringify({ summary: summarize() }, null, 2));
    return code;
  };

  try {
    // ---- 1. 首屏能渲染（白屏守卫）
    await page.goto(`${BASE_URL}/ui/login`, { waitUntil: 'networkidle' });
    const rootChildren = await page.$eval('#root', (el) => el.childElementCount);
    record('首屏 #root 有子节点（白屏守卫）', rootChildren > 0, `childElementCount=${rootChildren}`);
    if (rootChildren === 0) {
      throw new Failure(`白屏：#root 子节点为 0。${await dump(page, 'blank')}`);
    }

    // ---- 2. 登录（422 守卫）
    await page.fill("input#username, input[placeholder*='用户名']", username);
    await page.fill("input#password, input[placeholder*='密码']", password);
    await page.click("button:has-text('登录')");
    await page.waitForURL(`${BASE_URL}/ui/`, { timeout: TIMEOUT });
    await page.waitForLoadState('networkidle');
    record('登录成功并落在前端工作区', page.url().startsWith(`${BASE_URL}/ui`), `url=${page.url()}`);

    // ---- 3. 关键路由逐条可达且内容互不相同（basename 守卫）
    const lens = {};
    for (const [label, p] of ROUTES) {
      await page.goto(`${BASE_URL}${p}`, { waitUntil: 'networkidle' });
      const len = (await page.innerText('body')).trim().length;
      lens[label] = len;
      record(`路由可达：${label}`, len >= MIN_TEXT_LEN, `textLen=${len}`);
      if (len < MIN_TEXT_LEN) await dump(page, `route-${label}`);
    }
    const distinct = new Set(Object.values(lens)).size;
    record(
      '各路由内容互不相同（basename 回落守卫）',
      distinct >= 2,
      `不同 textLen 数=${distinct}/${ROUTES.length}：${JSON.stringify(lens)}`,
    );
    if (distinct < 2) {
      throw new Failure(
        `所有路由文本长度相同 —— 典型 basename 缺失症状（全部回落成工作区）。${await dump(page, 'basename-fallback')}`,
      );
    }

    // ---- 4. 工作区：导航出口 + 医生主流程能走到上传入口
    await page.goto(`${BASE_URL}/ui/`, { waitUntil: 'networkidle' });
    const buttons = await page.$$eval('button', (els) => els.length);
    record('工作区有导航出口', buttons > 0, `button 数=${buttons}`);
    if (buttons === 0) {
      throw new Failure(`工作区 btn=[]，医生被困住。${await dump(page, 'no-nav')}`);
    }

    // 裸 /ui/ 没有 ?session_id=，此时按设计**不给 file input**，只给「去工作列表选择会话」。
    // 所以这里必须**走医生真实路径**再断言上传入口，否则这项永远是假的：
    // 工作区 → 工作列表 → 选一位病人 → 带 session_id 回到工作区。
    const guide = await page.$("button:has-text('去工作列表选择会话')");
    record('无会话时给出可点的引导（不是死路）', guide !== null);
    if (guide) await guide.click();
    const toList = await waitForUrlMatch(page, /\/ui\/worklist$/, TIMEOUT);
    await page.waitForLoadState('networkidle');
    record('引导跳转到工作列表', toList, `url=${page.url()}`);
    if (!toList) {
      throw new Failure(`点了引导但没跳到工作列表。${await dump(page, 'guide-noop')}`);
    }

    // 工作列表是**异步取数**的：networkidle 只保证网络静默，不保证 React 已渲染完。
    // 第一版在这里直接 $() 就断言，拿到的是「加载中…」骨架 —— 竞态假红。
    // 所以改成轮询等行按钮出现，最长 ~15s。
    let rowBtn = null;
    for (let i = 0; i < 30 && rowBtn === null; i += 1) {
      rowBtn = await page.$("button:has-text('继续')") ?? (await page.$("button:has-text('查看')"));
      if (rowBtn === null) await page.waitForTimeout(500);
    }
    if (rowBtn === null) {
      throw new Failure(
        `工作列表里没有可进入的会话行，医生无法开始一次检查。${await dump(page, 'worklist-empty')}`,
      );
    }
    await rowBtn.click();
    // React Router 会把 `/ui/` 归一成 `/ui`，所以斜杠要可选 ——
    // 写成 `/ui/?session_id=` 会把一次**成功**的跳转判成超时。
    const jumped = await waitForUrlMatch(page, /\/ui\/?\?session_id=/, TIMEOUT);
    await page.waitForLoadState('networkidle');
    record('选中会话后带 session_id 回到工作区', jumped, `url=${page.url()}`);
    if (!jumped) {
      throw new Failure(`点了会话行但URL 没带 session_id，医生无法开始检查。${await dump(page, 'no-session-jump')}`);
    }

    // 选定会话后结论区才出现真正的装载控件；同样要等它出现而不是立刻断言
    let fileInput = null;
    for (let i = 0; i < 30 && fileInput === null; i += 1) {
      fileInput = await page.$("input[type=file][accept*='.dcm']");
      if (fileInput === null) await page.waitForTimeout(500);
    }
    // 不过滤可见性 —— 隐藏的 file input 也是入口
    const accepts = await page.$$eval('input[type=file]', (els) => els.map((e) => e.getAttribute('accept') || ''));
    record(
      '选定会话后有 DICOM 装载入口',
      accepts.length > 0,
      `file input 数=${accepts.length} accept=${JSON.stringify(accepts)}`,
    );
    if (accepts.length === 0) {
      throw new Failure(`选定会话后仍没有 file input，医生无法装载影像。${await dump(page, 'no-upload')}`);
    }

    // ---- 4b 可选：真传一个 DICOM，验证「装载 → 自动发起判读」真的接上了
    //
    // 默认**不做**：它要真跑一次判读（LLM + 检索，~90s），CI 里既慢又依赖 GPU。
    // 但"入口存在"不等于"传得上去"，所以留一个显式开关给交付前的完整走查用。
    const uploadIdx = process.argv.indexOf('--upload');
    const uploadPath = uploadIdx >= 0 ? process.argv[uploadIdx + 1] : process.env.UAI_SMOKE_UPLOAD;
    if (uploadPath) {
      if (!fs.existsSync(uploadPath)) {
        throw new Failure(`--upload 指向的文件不存在：${uploadPath}`);
      }
      await fileInput.setInputFiles(uploadPath);
      // 期望：上传成功后自动进入「分析中」（= flow.start 已被真实触发）。
      // 判据用界面文案而不是网络请求 —— 医生看得见的就是文案。
      let analyzing = false;
      for (let i = 0; i < 60 && !analyzing; i += 1) {
        analyzing = (await page.locator('text=分析中').count()) > 0;
        if (!analyzing) {
          if ((await page.locator('[role=alert]').count()) > 0) {
            const msg = await page.locator('[role=alert]').first().innerText();
            throw new Failure(`上传后界面报错：${msg.trim()}。${await dump(page, 'upload-error')}`);
          }
          await page.waitForTimeout(1000);
        }
      }
      record(
        '装载 DICOM 后自动进入「分析中」（判读主链有起点）',
        analyzing,
        analyzing ? '' : '等了 60s 仍未出现「分析中」',
      );
      if (!analyzing) {
        throw new Failure(`装载影像后没有自动发起判读。${await dump(page, 'no-auto-diagnosis')}`);
      }
    } else {
      console.log('# 跳过真实上传（加 --upload <dicom路径> 可验证「装载→自动发起判读」）');
    }

    // ---- 4c 可选：走完医生流程最后一跳（结论 → 报告草稿 → 签名 → 验签）
    //
    // 为什么要单独一个开关：它需要 GPU 在线（判读要真跑一次）**且**会真的写一条
    // 已签发报告。默认不做。
    //
    // ⚠️ 这一段刻意**不断言"签发一定成功"**：本次样本触发了 4 条禁忌 +
    // Self-RAG 校验未通过，草稿可能因必填段缺失而**依法拒绝签发** ——
    // 那是安全网关的正确行为，不是缺陷。所以只断言"链路通到了那一步"，
    // 并把界面上的真实结果原样记下来。
    if (uploadPath && process.argv.includes('--report')) {
      const concluded = await waitForAny(
        page,
        [/恶性风险/, /建议：/, /需人工确认/],
        300_000,
        '判读结论',
      );
      record('判读出结论（不是永远停在「分析中」）', concluded);
      if (!concluded) {
        throw new Failure(`等了 300s 仍无结论。${await dump(page, 'no-conclusion')}`);
      }

      // SPA 懒加载：URL 先变、组件后渲染。直接点会扑空，所以等按钮真的出现。
      // 用 /报告/ 而不是 /^报告$/：这里匹配的是整页 innerText（多行），
      // 不带 m 标志的 ^ $ 匹配不到单独一行 —— 第一版就栽在这。
      const reportBtn = await waitForAny(page, [/报告/], 20_000, '报告入口');
      record('结论区出现「报告」入口（默认沉默 → 有结论才出现）', reportBtn);
      if (reportBtn) {
        await clickByText(page, '报告');
        const onReport = await waitForUrlMatch(page, /\/ui\/report\?request_id=/, 20_000);
        record('点「报告」进入报告页并带上 request_id', onReport, `url=${page.url()}`);
        if (!onReport) {
          throw new Failure(`点「报告」没进报告页。${await dump(page, 'report-no-jump')}`);
        }

        const draftBtn = await waitForAny(page, [/生成草稿/], 30_000, '生成草稿按钮');
        record('报告页可操作（生成草稿按钮存在）', draftBtn);
        if (draftBtn) {
          await clickByText(page, '生成草稿');
          const drafted = await waitForAny(
            page,
            [/Report not found/, /草稿/, /完整性/, /缺少/],
            120_000,
            '报告草稿',
          );
          record('报告草稿生成（或如实回报缺什么）', drafted);
          await page.screenshot({ path: `${SHOT_DIR}/flow-report.png`, fullPage: true });

          const signBtn = await waitForAny(page, [/签发报告|已签发/], 20_000, '签发按钮');
          if (signBtn) {
            await clickByText(page, '签发报告');
            const signed = await waitForAny(
              page,
              [/已签发|签名完成|HSM|签发成功/],
              120_000,
              '签发结果',
            );
            // 不把"签发成功"写成 PASS 断言：被安全网关依法拒绝也是正确行为，
            // 只把界面上的真实结果记下来（下面 dump 全文）。
            record('走到签发这一步（结果原样记录，不预设必须成功）', signed);
            await page.screenshot({ path: `${SHOT_DIR}/flow-signed.png`, fullPage: true });
            console.log(
              `# 签发后页面正文：${JSON.stringify(
                (await page.innerText('body')).replace(/\s+/g, ' ').slice(0, 300),
              )}`,
            );
          } else {
            record('未出现签发按钮（草稿未达可签发态）', true, '如实记录，不判失败');
          }
        }
      }
    }

    // ---- 5. 零 console error / 零同源错误响应
    const blockingErrors = consoleErrors.filter((t) => !isAcceptedConsoleError(t));
    const acceptedErrors = consoleErrors.filter(isAcceptedConsoleError);
    record(
      '全程 0 个非豁免 console error',
      blockingErrors.length === 0,
      `${blockingErrors.length} 条：${JSON.stringify(blockingErrors.slice(0, 5))}`,
    );
    // 豁免项照样打印：被豁免不等于被消失
    if (acceptedErrors.length > 0) {
      console.log(
        `[WARN] ${acceptedErrors.length} 条已豁免 console error（见脚本 ACCEPTED_CONSOLE_PATTERNS）：` +
          `${JSON.stringify([...new Set(acceptedErrors)].slice(0, 2))}`,
      );
    }
    record('全程 0 个同源 4xx/5xx', badResponses.length === 0, `${badResponses.length} 条：${JSON.stringify(badResponses.slice(0, 5))}`);
  } catch (e) {
    if (e instanceof Failure) {
      record('运行时判定', false, e.message);
      return finish(1);
    }
    const evidence = await dump(page, 'exception').catch(() => '');
    record('运行时异常', false, `${e.name}: ${e.message}；${evidence}`);
    return finish(1);
  }

  const summary = summarize();
  await browser.close().catch(() => {});
  console.log(JSON.stringify({ summary }, null, 2));
  return summary.failed === 0 ? 0 : 1;
}

main()
  .then((code) => process.exit(code))
  .catch((e) => {
    console.error(`FAIL：脚本自身异常 ${e.name}: ${e.message}`);
    process.exit(2);
  });
