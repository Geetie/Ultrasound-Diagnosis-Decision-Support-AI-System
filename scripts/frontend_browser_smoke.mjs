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
 * 4. 工作区有导航出口（button > 0）与 DICOM 装载入口（`input[type=file]` > 0，
 *    **不过滤可见性** —— 过滤可见性会漏掉 display:none 的隐藏控件）
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

    // ---- 4. 工作区：导航出口 + DICOM 装载入口
    await page.goto(`${BASE_URL}/ui/`, { waitUntil: 'networkidle' });
    const buttons = await page.$$eval('button', (els) => els.length);
    record('工作区有导航出口', buttons > 0, `button 数=${buttons}`);
    if (buttons === 0) {
      throw new Failure(`工作区 btn=[]，医生被困住。${await dump(page, 'no-nav')}`);
    }

    // 不过滤可见性 —— 隐藏的 file input 也是入口
    const accepts = await page.$$eval('input[type=file]', (els) => els.map((e) => e.getAttribute('accept') || ''));
    record('工作区有 DICOM 装载入口', accepts.length > 0, `file input 数=${accepts.length} accept=${JSON.stringify(accepts)}`);
    if (accepts.length === 0) {
      throw new Failure(`工作区没有 file input，医生无法装载影像。${await dump(page, 'no-upload')}`);
    }

    // ---- 5. 零 console error / 零同源错误响应
    record('全程 0 个 console error', consoleErrors.length === 0, `${consoleErrors.length} 条：${JSON.stringify(consoleErrors.slice(0, 5))}`);
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
