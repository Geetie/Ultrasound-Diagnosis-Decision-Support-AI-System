/**
 * 静默 catch 专用 ESLint 配置（A.58）。
 *
 * 为什么**独立**成一份 config 而不是并进 eslint.config.js：
 * 仓库的 `npm run lint` 是 `eslint . --max-warnings 1`（严格预算），而存量
 * catch 有 50+ 处只有 3 处 `console.error` —— 直接并进去会一次冒出几十条告警，
 * 把严格预算冲垮，然后大家只能把它关掉。所以静默 catch 走**独立 config + 基线棘轮**
 * （`eslint-rules/silent-catch-ratchet.mjs`）：只挡新增，不挡存量。
 *
 * 只启用本项目这一条本地规则（不 extends recommended）—— 目的是把"静默 catch 计数"
 * 与其它 lint 噪声解耦，计数稳定、棘轮才可信。
 */
import tseslint from 'typescript-eslint';

import noSilentCatch from './eslint-rules/no-silent-catch.js';

export default [
  { ignores: ['dist/**', 'node_modules/**', 'eslint-rules/**', 'coverage/**'] },
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      parser: tseslint.parser,
      ecmaVersion: 2022,
      sourceType: 'module',
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: {
      local: { rules: { 'no-silent-catch': noSilentCatch } },
    },
    rules: {
      'local/no-silent-catch': 'error',
    },
  },
];
