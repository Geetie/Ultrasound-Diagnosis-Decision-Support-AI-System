import { describe, expect, it } from 'vitest';

import { stripMarkdownEmphasis } from '../text';

/**
 * 显示层文本归一化（A.58）。
 *
 * 背景：真机截图里结论区显示 `**LVEF:**无法计算**分级:**…` —— LLM 自由文本带
 * 裸露 Markdown 标记，而适配层契约要求**原样透传**（便于与后端对账），
 * 所以去标记只能发生在渲染层。本文件钉住"只去标记、不改语义"这条边界。
 */
describe('stripMarkdownEmphasis', () => {
  it('成对强调标记：去标记保留内容', () => {
    expect(stripMarkdownEmphasis('**LVEF:**无法计算')).toBe('LVEF:无法计算');
    expect(stripMarkdownEmphasis('结论：**考虑良性**，建议随访')).toBe('结论：考虑良性，建议随访');
    expect(stripMarkdownEmphasis('__强调__')).toBe('强调');
    expect(stripMarkdownEmphasis('用 `TR4` 分级')).toBe('用 TR4 分级');
  });

  it('落单的标记符也去掉（LLM 常把 ** 当分隔符，成对不齐是常态）', () => {
    expect(stripMarkdownEmphasis('**[cardiac] LVEF=unknown, severity=normal')).toBe(
      '[cardiac] LVEF=unknown, severity=normal',
    );
    expect(stripMarkdownEmphasis('LVEF:**无法计算')).toBe('LVEF:无法计算');
  });

  it('标识符里的下划线**不动**（改了就成伪造数据）', () => {
    expect(stripMarkdownEmphasis('reason=grade_unparseable')).toBe('reason=grade_unparseable');
    expect(stripMarkdownEmphasis('policy=best_mean_prob')).toBe('policy=best_mean_prob');
    expect(stripMarkdownEmphasis('a__b')).toBe('a__b');
  });

  it('标题标记默认保留，结论区按需去掉（headings: true）', () => {
    const md = '# 超声诊断报告\n\n**所见**：…';
    expect(stripMarkdownEmphasis(md)).toContain('# 超声诊断报告');
    expect(stripMarkdownEmphasis(md, { headings: true })).not.toContain('# 超声诊断报告');
  });

  it('换行与缩进保持原样（只去标记）', () => {
    expect(stripMarkdownEmphasis('第一行**粗**\n  第二行')).toBe('第一行粗\n  第二行');
  });

  it('空值安全（null/undefined → 空串，不抛）', () => {
    expect(stripMarkdownEmphasis(null)).toBe('');
    expect(stripMarkdownEmphasis(undefined)).toBe('');
  });
});
