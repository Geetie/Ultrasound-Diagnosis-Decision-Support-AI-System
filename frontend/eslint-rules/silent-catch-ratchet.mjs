#!/usr/bin/env node
/**
 * 静默 catch 棘轮（A.58「零静默」约定层 · 前端）。
 *
 * 用独立的 `eslint.silent-catch.config.js` 只跑 `local/no-silent-catch`，
 * 按文件统计静默 catch 数并与基线 `eslint-rules/no-silent-catch.baseline.json` 比对：
 *
 * - 任一文件计数 **高于** 基线 → 失败（未登记文件 = 基线 0，即"新代码不许静默"）；
 * - 任一文件计数 **低于** 基线 → 失败并提示下调（锁住成果，避免余量藏债务）。
 *
 * 用法：
 *   node eslint-rules/silent-catch-ratchet.mjs            # 门禁（0 通过 / 1 不达标 / 2 输入问题）
 *   node eslint-rules/silent-catch-ratchet.mjs --update   # 重算基线（维护时用，写回 JSON）
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

import { ESLint } from 'eslint';

const ROOT = path.resolve(import.meta.dirname, '..');
const BASELINE_FILE = path.join(ROOT, 'eslint-rules', 'no-silent-catch.baseline.json');
const RULE_ID = 'local/no-silent-catch';
const update = process.argv.includes('--update');

function rel(filePath) {
  return path.relative(ROOT, filePath).split(path.sep).join('/');
}

async function collectCounts() {
  const eslint = new ESLint({
    cwd: ROOT,
    overrideConfigFile: 'eslint.silent-catch.config.js',
  });
  const results = await eslint.lintFiles(['src']);
  // 解析失败必须**显式失败**：否则那些文件一个静默 catch 都统计不到，计数静默偏低
  // —— 守卫自己变成"静默的守卫"正是 A.35 的形态，绝不能重演。
  const unparsed = results.filter((r) => r.messages.some((m) => m.fatal));
  if (unparsed.length > 0) {
    const head = unparsed.slice(0, 5).map((r) => `  - ${rel(r.filePath)}`).join('\n');
    throw new Error(`有 ${unparsed.length} 个文件解析失败（计数会静默偏低，先修解析）：\n${head}`);
  }
  const counts = {};
  for (const r of results) {
    const n = r.messages.filter((m) => m.ruleId === RULE_ID).length;
    if (n > 0) counts[rel(r.filePath)] = n;
  }
  return { counts, lintedFiles: results.length };
}

function readBaseline() {
  if (!fs.existsSync(BASELINE_FILE)) return null;
  const data = JSON.parse(fs.readFileSync(BASELINE_FILE, 'utf8'));
  if (!data || typeof data.counts !== 'object' || data.counts === null) return null;
  return data;
}

function writeBaseline(counts) {
  const payload = {
    _note:
      '静默 catch 存量基线：文件 → 静默 catch 数。只许下调；未登记文件默认 0（新代码不许静默）。' +
      '重算：node eslint-rules/silent-catch-ratchet.mjs --update',
    _generated_at: '2026-10-13 A.58',
    counts,
  };
  fs.writeFileSync(BASELINE_FILE, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
}

let collected;
try {
  collected = await collectCounts();
} catch (err) {
  console.error(`[silent-catch] ${err.message}`);
  process.exit(2);
}
const { counts, lintedFiles } = collected;

if (update) {
  writeBaseline(counts);
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  console.log(
    `[silent-catch] 基线已写入 ${rel(BASELINE_FILE)}：${Object.keys(counts).length} 个文件 / ${total} 处` +
      `（共扫描 ${lintedFiles} 个文件）`,
  );
  process.exit(0);
}

const baseline = readBaseline();
if (baseline === null) {
  console.error(`[silent-catch] 缺基线文件或格式不对：${rel(BASELINE_FILE)}（用 --update 生成）`);
  process.exit(2);
}
const allowed = baseline.counts;

const grew = [];
const stale = [];
for (const [file, n] of Object.entries(counts)) {
  const base = allowed[file] ?? 0;
  if (n > base) grew.push(`${file}: ${base} -> ${n}`);
}
for (const [file, base] of Object.entries(allowed)) {
  const n = counts[file] ?? 0;
  if (n < base) stale.push(`${file}: 基线 ${base} -> 实测 ${n}（请下调）`);
}

const total = Object.values(counts).reduce((a, b) => a + b, 0);
if (grew.length > 0) {
  console.error('[silent-catch] 静默 catch 新增（catch 必须记录 / 抛出 / 可见降级，或 `// silent-ok: 理由`）：');
  for (const g of grew) console.error(`  - ${g}`);
  process.exit(1);
}
if (stale.length > 0) {
  console.error('[silent-catch] 存量已收敛，基线过期（棘轮失真会把新债务藏进余量）：');
  for (const s of stale) console.error(`  - ${s}`);
  process.exit(1);
}

console.log(
  `[silent-catch] PASS：扫描 ${lintedFiles} 个文件，静默 catch ${Object.keys(counts).length} 个文件 / ${total} 处（全部 ≤ 基线）`,
);
process.exit(0);
