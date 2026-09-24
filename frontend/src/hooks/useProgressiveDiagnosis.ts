import { useCallback, useEffect, useMemo, useState } from 'react';
import type { DiagnosisResult } from '@/types/api';
import type { DiagnosisStageEvent, StageTiming } from '@/services/diagnosisService';
import { extractStageTimings } from '@/services/diagnosisService';

/**
 * 诊断结果的**分段揭示**（T-GB-FE-01）。
 *
 * 设计意图：结论是医生的第一需求，先落地；证据链与质控信息随后补齐。
 * 后端接口是"整包返回"，因此这里的渐进是**展示层**的渐进（骨架屏 → 分段
 * 上浮），而不是假装后端在推流；阶段条里的预计阶段由 poller 标注
 * `estimated`，完成后的真实阶段耗时来自 `findings.timings_ms`。
 */
export type ResultBlockKey = 'conclusion' | 'hallucination' | 'quality' | 'evidence';

/** 揭示顺序：结论 → 安全警示 → 质控 → 证据链 */
export const BLOCK_REVEAL_ORDER: ResultBlockKey[] = [
  'conclusion',
  'hallucination',
  'quality',
  'evidence',
];

export const BLOCK_REVEAL_DELAY_MS: Record<ResultBlockKey, number> = {
  conclusion: 0,
  hallucination: 120,
  quality: 280,
  evidence: 440,
};

export interface ProgressiveDiagnosisState {
  revealed: Set<ResultBlockKey>;
  isRevealed: (key: ResultBlockKey) => boolean;
  allRevealed: boolean;
  /** 完成后从结果中提取的真实阶段耗时 */
  stageTimings: StageTiming[];
  /** 预计阶段（polling 期间） */
  stage: DiagnosisStageEvent | null;
  setStage: (stage: DiagnosisStageEvent | null) => void;
}

export function useProgressiveDiagnosis(result: DiagnosisResult | null): ProgressiveDiagnosisState {
  const [revealed, setRevealed] = useState<Set<ResultBlockKey>>(new Set());
  const [revealedFor, setRevealedFor] = useState<DiagnosisResult | null>(null);
  const [stage, setStage] = useState<DiagnosisStageEvent | null>(null);

  // React 官方推荐的"props 变化时同步状态"模式：在渲染期比对并重置，
  // 而不是在 effect 里同步 setState（后者会触发级联渲染）。
  // 首个区块（结论，延迟 0）直接可见，避免先闪一帧骨架屏。
  if (revealedFor !== result) {
    setRevealedFor(result);
    setRevealed(new Set(result ? [BLOCK_REVEAL_ORDER[0]] : []));
  }

  useEffect(() => {
    if (!result) return;
    const timers = BLOCK_REVEAL_ORDER.filter(
      (key) => BLOCK_REVEAL_DELAY_MS[key] > 0
    ).map((key) =>
      setTimeout(() => {
        setRevealed((prev) => {
          const next = new Set(prev);
          next.add(key);
          return next;
        });
      }, BLOCK_REVEAL_DELAY_MS[key])
    );
    return () => timers.forEach((timer) => clearTimeout(timer));
  }, [result]);

  const isRevealed = useCallback((key: ResultBlockKey) => revealed.has(key), [revealed]);

  const stageTimings = useMemo(() => extractStageTimings(result), [result]);

  return {
    revealed,
    isRevealed,
    allRevealed: revealed.size === BLOCK_REVEAL_ORDER.length,
    stageTimings,
    stage,
    setStage,
  };
}
