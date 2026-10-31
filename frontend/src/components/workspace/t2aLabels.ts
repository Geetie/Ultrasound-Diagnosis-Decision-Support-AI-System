/**
 * T2a 影像分类的纯映射逻辑（02-1）。
 *
 * 刻意与 `WorkspaceImageRegion.tsx` 分离：影像区组件引入 cornerstone 链，
 * 在高负载机器上 jsdom 环境准备可达 500s+ 甚至 OOM（ERR_WORKER_OUT_OF_MEMORY，
 * A.100 实测）；本模块零依赖，测试可在任何环境秒级跑完。
 */

/** T2a 预测类别原文 → 中文（未知值原样透传，不猜）。 */
export function t2aPredictedLabel(predicted?: string): string {
  switch (predicted) {
    case 'Benign':
      return '良性';
    case 'Malignant':
      return '恶性';
    case 'Normal':
      return '正常';
    default:
      return predicted ?? '类别未提供';
  }
}

/** T2a reason 封闭集合 → 中文。缺权重/未启用与"没检出"是两回事 ——
 *  契约要求显示「未做影像分类：<原因>」（红线，同分割旁路口径）。 */
export const T2A_REASON_LABELS: Record<string, string> = {
  disabled: '分类功能未启用',
  no_image_ids: '本次请求没有影像',
  image_not_indexed: '影像未入库（未索引）',
  no_image_bytes: '影像字节不可取',
  frame_decode_failed: '代表帧解码失败',
  service_unavailable: '分类服务不可达',
  model_missing: '分类服务未加载模型',
  invalid_response: '分类服务响应异常',
  not_run: '本次未执行',
};

/** reason → 中文标签（未知 reason 原样兜底"原因未提供"，不编造）。 */
export function t2aReasonLabel(reason?: string): string {
  return T2A_REASON_LABELS[reason ?? ''] ?? '原因未提供';
}
