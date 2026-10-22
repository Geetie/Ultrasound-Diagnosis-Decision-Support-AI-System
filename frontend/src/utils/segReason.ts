/**
 * 分割"未做"的原因文案（A.86）。
 *
 * 为什么抽成纯函数：这段逻辑原先埋在 `WorkspaceImageRegion`（会 import cornerstone，
 * 重到单测跑不动），于是"文案优先顺序"实际上**没被测试保护**。
 * 抽出来后可以直接测，且组件与历史对比页共用同一份兜底表，不会各写一套。
 *
 * 优先顺序（**后端 > 本地**）：
 * 1. `reasonText`（后端给的医生可读说明，可行动）—— 直接显示；
 * 2. 本地映射表（老后端没有 reason_text 时的兜底，每条都要能指导下一步）；
 * 3. 原始 code（陌生原因透传，便于排查，不静默吞掉）；
 * 4. `原因未提供`。
 */
const SEG_REASON_LABEL: Record<string, string> = {
  not_enabled: '分割旁路未启用（管理员可在启动配置开启）',
  no_image_ids: '本次判读未关联影像，请确认上传是否成功',
  image_not_indexed: '影像未登记到本次检查，请确认上传是否成功',
  image_bytes_unavailable: '系统未取到影像文件，请联系技师重新上传',
  secondary_capture_burned_in:
    '该影像是屏幕截图，系统不对截图做勾画；如需要请上传设备原始 DICOM',
  no_frames: '影像不含有效帧，请重新上传含图像的 DICOM',
  no_candidate: '没有可用影像，请确认本次检查是否已上传影像',
};

export function segReasonLabel(reason?: string | null, reasonText?: string | null): string {
  const text = (reasonText ?? '').trim();
  if (text) return text;
  const code = (reason ?? '').trim();
  if (!code) return '原因未提供';
  return SEG_REASON_LABEL[code] ?? code;
}

export { SEG_REASON_LABEL };
