import type { T2aView } from '@/services/diagnosisAdapter';
import { t2aPredictedLabel, t2aReasonLabel } from './t2aLabels';

/**
 * 02-1 T2a 影像分类芯片（辅助参考）。
 *
 * 刻意独立成组件：影像区组件引入 cornerstone 链，高负载机器上 jsdom
 * 环境准备可达 500s+ 甚至 worker OOM（A.100.4 实测），渲染测试因此
 * 无法稳定运行；本组件零 cornerstone 依赖，测试秒级可跑。
 *
 * 红线：辅助参考字段——不参与结论选择 / confidence / status；
 * available=false 必须显示「未做影像分类：<原因>」，不得显示成"未检出恶性"。
 */
export default function T2aChip({ t2a }: { t2a: T2aView }) {
  return (
    <span
      className="ws-image__text ws-image__t2a"
      style={{ right: 24, bottom: 12, maxWidth: 560, textAlign: 'right' }}
      data-region="t2a-aux"
      role="note"
    >
      {t2a.available ? (
        <>
          AI 影像分类（辅助参考，不参与结论）：
          <strong>{t2aPredictedLabel(t2a.predicted)}</strong>
          {t2a.malignantProb != null &&
            ` · 恶性概率 ${Math.round(t2a.malignantProb * 100)}%`}
        </>
      ) : (
        <>未做影像分类：{t2aReasonLabel(t2a.reason)}</>
      )}
    </span>
  );
}
