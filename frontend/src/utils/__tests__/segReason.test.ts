import { describe, expect, it } from 'vitest';
import { segReasonLabel } from '../segReason';

/**
 * A.86 守卫：分割未做的文案优先顺序。
 *
 * 真机教训：后端曾把"这是屏幕截图、本来不该勾画"报成 `image_bytes_unavailable`，
 * 前端又按 code 自己猜一次 → 医生被引去"找技师重传"（白跑）。
 * 所以：**后端给的说法优先**，本地映射只是兜底，且兜底也要可行动。
 */
describe('segReasonLabel', () => {
  it('后端给了可读说明就原样显示（不覆盖、不翻译）', () => {
    const text = '本次未做病灶勾画：该影像是屏幕截图（带烧录标注/标尺）';
    expect(segReasonLabel('secondary_capture_burned_in', text)).toBe(text);
    // 即使 code 是陌生的，只要后端给了说明就用说明
    expect(segReasonLabel('weird_case', text)).toBe(text);
  });

  it('没有后端说明时用本地兜底，且每条都能指导下一步', () => {
    expect(segReasonLabel('image_bytes_unavailable')).toMatch(/技师|重新上传/);
    expect(segReasonLabel('secondary_capture_burned_in')).toMatch(/屏幕截图.*DICOM/);
    expect(segReasonLabel('no_image_ids')).toMatch(/上传/);
    expect(segReasonLabel('not_enabled')).toMatch(/管理员/);
  });

  it('陌生 code 原文透传，不静默吞掉', () => {
    expect(segReasonLabel('brand_new_code')).toBe('brand_new_code');
  });

  it('两者都没有时给"原因未提供"而不是空白', () => {
    expect(segReasonLabel(undefined)).toBe('原因未提供');
    expect(segReasonLabel('', '')).toBe('原因未提供');
    expect(segReasonLabel(null, '   ')).toBe('原因未提供');
  });
});
