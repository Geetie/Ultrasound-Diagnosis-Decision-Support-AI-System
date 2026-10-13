/**
 * 显示层文本归一化（A.58）。
 *
 * 为什么需要
 * ----------
 * 真机截图实证：工作区结论区显示成
 *   `**[cardiac] LVEF=unknown, severity=normal — …**LVEF:**无法计算**分级:**无法判断…`
 * —— 医生可见正文里**裸露 Markdown 强调标记**。链路是：
 *   LLM 自由文本 → `diagnosisAdapter` 原样透传（契约明确要求"不二次改写"）
 *   → `WorkspaceConclusionBody` / 报告页 `<pre>` 原样渲染，三层都没洗。
 *
 * 为什么放在这里（渲染层）而不是 adapter
 * --------------------------------------
 * `diagnosisAdapter.test.ts` 有一条契约断言：
 *   `expect(view.headline).toBe(REAL_COMPLETED.diagnosis.fusion_result!.content)`
 * —— 适配层必须**原样透传**（数据层不做改写，便于与后端对账）。所以归一化只发生在
 * **渲染那一刻**：数据仍然是原文，显示才是给人看的。
 *
 * 边界（刻意保守）
 * ----------------
 * * 只做"去标记"，**不做**语义改写、不引入 markdown 渲染器、不改换行与缩进；
 * * `**粗体**` / `__粗体__` / `` `代码` `` 成对时去标记保留内容；**落单的标记符也去掉**
 *   （LLM 常把 `**` 当分隔符用，成对不齐是常态，留着只会干扰阅读）；
 * * `__` 只在**成对**时去（标识符里下划线很常见：`grade_unparseable`、
 *   `best_mean_prob` —— 一刀切会改动医学/技术标识符，属伪造数据）；
 * * 标题标记（行首 `#`）默认**保留**（报告正文是 Markdown 文档，`#` 是其结构），
 *   仅结论区按需去掉（`{ headings: true }`）—— 结论不是文档，`#` 只会干扰阅读。
 */

/** 成对强调/行内代码：保留中间内容 */
const PAIRED = [
  /\*\*([^*\n]+?)\*\*/g,
  /__([^_\n]+?)__/g,
  /`([^`\n]+?)`/g,
];

/** 行首标题标记（`# ` / `## ` …），仅结论区使用 */
const HEADING = /^[ \t]*#{1,6}[ \t]+/gm;

/**
 * 去 Markdown 标记，返回**给医生看**的纯文本。
 *
 * @param text 原始文本（LLM 自由文本 / 后端渲染结果）
 * @param opts.headings 是否连行首标题标记一起去掉（默认 false —— 报告正文保留结构）
 */
export function stripMarkdownEmphasis(
  text: string | null | undefined,
  opts: { headings?: boolean } = {},
): string {
  if (!text) return '';
  let out = text;
  for (const re of PAIRED) out = out.replace(re, '$1');
  // 落单的强调标记（成对不齐的残留）：只在**成对出现不了**时才会走到这里
  out = out.replace(/\*\*/g, '').replace(/__/g, (m, offset: number, whole: string) => {
    // `__` 两侧都是标识符字符时不动（`a__b` 是名字不是强调）
    const before = whole[offset - 1] ?? '';
    const after = whole[offset + 2] ?? '';
    const isWordChar = (ch: string) => /[\w]/.test(ch);
    return isWordChar(before) || isWordChar(after) ? m : '';
  });
  if (opts.headings) out = out.replace(HEADING, '');
  return out;
}
