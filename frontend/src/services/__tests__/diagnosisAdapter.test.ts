/**
 * 适配层测试 —— 夹具是**真机实测响应**，不是手编对象。
 *
 * 来源：GET /api/v1/diagnosis/result/req_20261002_da4fa542
 *       （student03@10.20.28.21，2026-10-02 实测）
 *
 * 为什么必须用真夹具：`DiagnosisConclusion` 长期读着一批真机根本不存在的字段
 * （standard_conclusion / full_conclusion / deep_analysis / target_organ），
 * 而当时用手编夹具 + 同样的错字段名，测试一路绿灯。真机响应一摆出来就露馅。
 */
import { describe, expect, it } from 'vitest';

import {
  LOW_CONFIDENCE_THRESHOLD,
  needsDoctorReview,
  toConclusionView,
} from '../diagnosisAdapter';
import type { DiagnosisResult } from '@/types/api';

/** 真机实测：completed、SG 未通过，但 requires_manual_review 为 false。 */
const REAL_COMPLETED = {
  request_id: 'req_20261002_da4fa542',
  status: 'completed',
  consistency_flag: 'consistent',
  desensitization_status: 'full',
  consent_status_at_output: 'granted',
  fusion_status: 'normal',
  diagnosis: {
    organ: 'thyroid',
    rollout: {
      stage: 'off',
      track: 'full',
      bucket: null,
      reason: 'rollout_disabled',
      allowed: true,
      percent: 0,
      session_index: 0,
    },
    routing: {
      reasons: ['single_feature_grading', 'doctor_requested_full'],
      complexity: 'single_feature',
      is_upgraded: false,
      target_tier: 'full',
      effective_tier: 'full',
      requested_tier: 'full',
      detected_organs: ['thyroid'],
      organ_threshold: 0.85,
      requires_confirmation: false,
    },
    sg_result: {
      passed: false,
      allowed: false,
      message: '安全校验服务不可用，请转为人工诊断',
      error_code: 'E4005',
      degraded_mode: true,
      consent_status_at_output: 'unknown_blocked',
    },
    timings_ms: {
      db_read_ms: 5.52,
      'fusion.fuse_ms': 656.17,
      'pipeline.total_ms': 2488.23,
      'sg.check_output_ms': 14.62,
      'dispatcher.invoke_ms': 1808.66,
    },
    completed_at: '2026-10-02T03:08:53.348383+00:00',
    fusion_result: {
      content:
        '[thyroid] TR2 — 分级: TR2 总分: 2 置信度: 0.85 理由: 回声: 低回声 (2) + ' +
        '其他特征未提及 = 2 分 → TR2 证据: chunk_121, chunk_124 ' +
        '患者PATIENT_001: 发现的低回声结节属于良性可能性较大，建议定期复查超声，' +
        '一般每6-12个月一次。如有任何疑问，请PATIENT_007医生。',
      needs_review: false,
      review_reason: null,
      crag_recovered: false,
      crag_triggered: false,
      self_rag_passed: true,
      final_confidence: 0.6284,
      total_latency_ms: 656.0269969049841,
    },
    review_reason: null,
  },
  evidence: {
    knowledge_ids: [
      'kb_v1.3.0:pdf_thyroid_ctirads_2021_0025#0000',
      'kb_v1.3.0:pdf_thyroid_ctirads_2021_0010#0001',
      'kb_v1.3.0:pdf_thyroid_ctirads_2021_0023#0000',
    ],
  },
  requires_manual_review: false,
  ai_diagnosis_failed: false,
  hallucination_severity: null,
} as unknown as DiagnosisResult;

describe('toConclusionView（真机响应 → 结论视图）', () => {
  const view = toConclusionView(REAL_COMPLETED)!;

  it('结论文本取自 fusion_result.content（真机唯一来源）', () => {
    expect(view).not.toBeNull();
    expect(view.headline).toBe(REAL_COMPLETED.diagnosis.fusion_result!.content);
    // 明确不是那几个不存在的字段
    expect(view.headline).not.toMatch(/待生成|暂无/);
  });

  it('器官取自 diagnosis.organ，不取 target_organ（真机无此键）', () => {
    expect(view.organ).toBe('thyroid');
  });

  it('分级原文抽出，不解释不改写', () => {
    expect(view.tier).toBe('TR2');
  });

  it('证据 id 合并内联引用与 knowledge_ids 并去重', () => {
    expect(view.evidenceIds).toContain('chunk_121');
    expect(view.evidenceIds).toContain('chunk_124');
    expect(view.evidenceIds).toContain('kb_v1.3.0:pdf_thyroid_ctirads_2021_0025#0000');
    expect(new Set(view.evidenceIds).size).toBe(view.evidenceIds.length);
  });

  it('终置信度透传，不二次加工', () => {
    expect(view.finalConfidence).toBe(0.6284);
  });

  it('SG 未通过时带出转人工文案与错误码', () => {
    expect(view.safetyBlocked).toEqual({
      message: '安全校验服务不可用，请转为人工诊断',
      errorCode: 'E4005',
    });
  });

  /**
   * P1 默认沉默：后端没有任何恶性概率字段，所以这一行**必须不存在**。
   * 设计稿 04 上的「低 · 约 2%–5%」是画布示意文案，不是后端产物。
   * 硬编码它等于编造临床数据 —— 这是本层最重要的一条断言。
   */
  it('后端未给恶性概率时，风险行整体省略（不编造）', () => {
    expect(view.malignancyRisk).toBeUndefined();
    expect(JSON.stringify(view)).not.toMatch(/\d+%\s*[-–~]\s*\d+%/);
  });

  /**
   * A.107 / G1：后端 `malignancy_risk` 一旦能到（G1 修复前它恒空，所以这里曾写死
   * undefined 并声称"后端没有这个字段"），适配层必须原样收敛——但**仍不编造区间**。
   */
  it('后端给了恶性概率：分级转中文 + 点估计百分比，不造区间', () => {
    const view = toConclusionView({
      ...REAL_COMPLETED,
      // 契约字段在**响应顶层**（后端 clinical_attribute_fields 展开），不在 diagnosis 里
      malignancy_risk: { probability: 0.31, level: 'high', basis: [] },
    } as never)!;
    expect(view.malignancyRisk).toEqual({
      level: '高',
      probability: 0.31,
      levelRaw: 'high',
      sourceReference: undefined,
    });
    expect(JSON.stringify(view)).not.toMatch(/\d+%\s*[-–~]\s*\d+%/);
  });

  it('只有分级没有概率：仍给分级，但不显示百分比', () => {
    const view = toConclusionView({
      ...REAL_COMPLETED,
      malignancy_risk: { level: 'medium' },
    } as never)!;
    expect(view.malignancyRisk?.level).toBe('中');
    expect(view.malignancyRisk?.probability).toBeUndefined();
  });

  it('脏数据（既无概率也无分级）→ 整行省略', () => {
    const view = toConclusionView({
      ...REAL_COMPLETED,
      malignancy_risk: { basis: [] },
    } as never)!;
    expect(view.malignancyRisk).toBeUndefined();
  });

  it('终置信度低于阈值时触发「看一眼」，且只给一条（P2 一次决策）', () => {
    // 实测 0.6284 < 0.7 → 触发 low_confidence
    expect(view.attention?.reason).toBe('low_confidence');
    expect(LOW_CONFIDENCE_THRESHOLD).toBe(0.7);
    expect(Array.isArray(view.attention)).toBe(false);
  });

  it('self_rag_passed 为 true 时不因它触发「看一眼」', () => {
    // 实测真值是 true（设计文档称恒 False）—— 照文档写就会误报
    expect(REAL_COMPLETED.diagnosis.fusion_result!.self_rag_passed).toBe(true);
    expect(view.attention?.reason).not.toBe('self_rag_failed');
  });

  it('self_rag_passed 为 false 时优先报 Self-RAG 失败', () => {
    const bad = structuredClone(REAL_COMPLETED) as DiagnosisResult;
    bad.diagnosis.fusion_result!.self_rag_passed = false;
    expect(toConclusionView(bad)?.attention?.reason).toBe('self_rag_failed');
  });

  it('无 fusion_result.content 时返回 null（整区不渲染，不是空壳）', () => {
    const empty = structuredClone(REAL_COMPLETED) as DiagnosisResult;
    delete empty.diagnosis.fusion_result;
    expect(toConclusionView(empty)).toBeNull();
    expect(toConclusionView(null)).toBeNull();
  });
});

describe('needsDoctorReview（裁决 requires_manual_review 与 sg_result.allowed 的矛盾）', () => {
  it('真机这条：顶层 false 但 SG allowed=false → 仍判定需复核', () => {
    expect(REAL_COMPLETED.requires_manual_review).toBe(false);
    expect(REAL_COMPLETED.diagnosis.sg_result!.allowed).toBe(false);
    // 只看顶层会漏判 —— 这正是实测暴露的语义矛盾
    expect(needsDoctorReview(REAL_COMPLETED)).toBe(true);
  });

  it('顶层为 true 时也判定需复核', () => {
    const r = structuredClone(REAL_COMPLETED) as DiagnosisResult;
    r.diagnosis.sg_result!.allowed = true;
    r.requires_manual_review = true;
    expect(needsDoctorReview(r)).toBe(true);
  });

  it('严重幻觉时判定需复核', () => {
    const r = structuredClone(REAL_COMPLETED) as DiagnosisResult;
    r.diagnosis.sg_result!.allowed = true;
    r.requires_manual_review = false;
    r.hallucination_severity = 'severe';
    expect(needsDoctorReview(r)).toBe(true);
  });

  it('两者都正常时不判定需复核（确认动作区不该出现）', () => {
    const r = structuredClone(REAL_COMPLETED) as DiagnosisResult;
    r.diagnosis.sg_result!.allowed = true;
    r.requires_manual_review = false;
    r.hallucination_severity = null;
    expect(needsDoctorReview(r)).toBe(false);
  });

  it('无结果时不判定', () => {
    expect(needsDoctorReview(null)).toBe(false);
  });
});

describe('M4/M11/M12 字段透传（03-4 / 03-6 / 06-5）', () => {
  it('带 differential_diagnoses 时原样透传：长度与 rank 保持，前端不重排', () => {
    const r = structuredClone(REAL_COMPLETED) as DiagnosisResult;
    r.differential_diagnoses = [
      { rank: 2, disease_name: '结节性甲状腺肿', confidence: 0.3, supporting_evidence: [], excluding_evidence: [] },
      { rank: 1, disease_name: '乳头状癌', confidence: null, supporting_evidence: ['微钙化'], excluding_evidence: [] },
    ];
    const v = toConclusionView(r)!;
    expect(v.differentialDiagnoses).toHaveLength(2);
    expect(v.differentialDiagnoses?.map((d) => d.rank)).toEqual([2, 1]); // 后端顺序原样
    expect(v.differentialDiagnoses?.[1]?.disease_name).toBe('乳头状癌');
  });

  it('cross_modality_hints 与 research_metrics 原样透传', () => {
    const r = structuredClone(REAL_COMPLETED) as DiagnosisResult;
    r.cross_modality_hints = [{ modality: 'CT', finding: '…', source_reference: 'ACR TI-RADS 2017' }];
    r.research_metrics = { ai_features: { feature_vector_hash: 'h1' } };
    const v = toConclusionView(r)!;
    expect(v.crossModalityHints?.[0]?.modality).toBe('CT');
    expect(v.researchMetrics?.ai_features?.feature_vector_hash).toBe('h1');
  });

  it('后端没给（缺键 / None / 空数组）时字段为空 —— P1 默认沉默，不编造', () => {
    const r = structuredClone(REAL_COMPLETED) as DiagnosisResult;
    delete r.differential_diagnoses;
    delete r.cross_modality_hints;
    r.research_metrics = null;
    const v = toConclusionView(r)!;
    expect(v.differentialDiagnoses ?? []).toHaveLength(0);
    expect(v.crossModalityHints ?? []).toHaveLength(0);
    expect(v.researchMetrics).toBeUndefined();
  });
});

describe('G34 禁忌命中明细（A.121）', () => {
  it('后端给了命中明细 → 原样收敛为视图字段', () => {
    const view = toConclusionView({
      ...REAL_COMPLETED,
      contraindication_matches: [
        {
          rule_id: 'thyroid_pregnancy_fna',
          description: '妊娠期避免 FNA',
          severity: 'high',
          organ: 'thyroid',
        },
      ],
    } as never)!;
    expect(view.contraindicationMatches).toEqual([
      {
        ruleId: 'thyroid_pregnancy_fna',
        description: '妊娠期避免 FNA',
        severity: 'high',
        organ: 'thyroid',
      },
    ]);
  });

  it('没有该字段 → 空数组（整块不渲染，不编造）', () => {
    const view = toConclusionView(REAL_COMPLETED as never)!;
    expect(view.contraindicationMatches).toEqual([]);
  });

  it('脏数据（非数组 / 空行）→ 丢弃而不是渲染错误框', () => {
    const v1 = toConclusionView({ ...REAL_COMPLETED, contraindication_matches: 'x' } as never)!;
    expect(v1.contraindicationMatches).toEqual([]);
    const v2 = toConclusionView({
      ...REAL_COMPLETED,
      contraindication_matches: [{}, 'junk', { rule_id: '' }],
    } as never)!;
    expect(v2.contraindicationMatches).toEqual([]);
  });
});

describe('G24 转人工原因（A.122）', () => {
  it('已知原因码 → 中文人话', () => {
    const view = toConclusionView({ ...REAL_COMPLETED, review_reason: 'consistency_failed' } as never)!;
    expect(view.reviewReason).toBe('一致性校验未通过');
    expect(view.reviewReasonCode).toBe('consistency_failed');
  });

  it('熔断原因 → 带状态的中文', () => {
    const view = toConclusionView({
      ...REAL_COMPLETED,
      review_reason: 'ai_safety_circuit_breaker_pending_confirmation',
    } as never)!;
    expect(view.reviewReason).toContain('AI 安全熔断');
    expect(view.reviewReason).toContain('pending_confirmation');
  });

  it('未知原因码 → 原样露出（不猜语义、不静默）', () => {
    const view = toConclusionView({ ...REAL_COMPLETED, review_reason: 'weird_new_code' } as never)!;
    expect(view.reviewReason).toBe('需人工复核（原因码：weird_new_code）');
  });

  it('无原因 → undefined（整块不渲染）', () => {
    const view = toConclusionView(REAL_COMPLETED as never)!;
    expect(view.reviewReason).toBeUndefined();
  });
});

describe('G33 处置建议降级（A.123）', () => {
  it('后端标记 true → actionsDegraded=true', () => {
    const view = toConclusionView({ ...REAL_COMPLETED, clinical_actions_degraded: true } as never)!;
    expect(view.actionsDegraded).toBe(true);
  });

  it('缺省/false → undefined（不提示"已降级"）', () => {
    expect(toConclusionView(REAL_COMPLETED as never)!.actionsDegraded).toBeUndefined();
    expect(
      toConclusionView({ ...REAL_COMPLETED, clinical_actions_degraded: false } as never)!
        .actionsDegraded,
    ).toBeUndefined();
  });
});

describe('G26 部分一致性（A.124）', () => {
  it('partial_consistent → 「只完成部分校验」文案（不是"双引擎不一致"）', () => {
    const view = toConclusionView({ ...REAL_COMPLETED, consistency_flag: 'partial_consistent' } as never)!;
    expect(view.attention?.reason).toBe('consistency_partial');
    expect(view.attention?.text).toContain('仅完成部分一致性校验');
    expect(view.attention?.text).not.toContain('双引擎');
  });
});

describe('G38 结构化处置建议/测量迁移（A.125）', () => {
  it('clinical_actions → 视图字段（紧急度翻中文、带出处）', () => {
    const view = toConclusionView({
      ...REAL_COMPLETED,
      clinical_actions: [
        {
          action_type: 'fna',
          target_department: '超声科',
          urgency: 'urgent',
          description: '建议超声引导下细针穿刺',
          applicable_level: '三甲',
          source_reference: 'ACR TI-RADS 2017',
        },
      ],
    } as never)!;
    expect(view.clinicalActions).toEqual([
      {
        actionType: 'fna',
        department: '超声科',
        urgency: '紧急',
        description: '建议超声引导下细针穿刺',
        applicableLevel: '三甲',
        reference: 'ACR TI-RADS 2017',
      },
    ]);
  });

  it('缺字段/脏行 → 空数组（整块不渲染）', () => {
    expect(toConclusionView(REAL_COMPLETED as never)!.clinicalActions).toEqual([]);
    const v = toConclusionView({
      ...REAL_COMPLETED,
      clinical_actions: [{}, 'junk', { action_type: '' }],
    } as never)!;
    expect(v.clinicalActions).toEqual([]);
  });

  it('measurement → 视图字段；全空对象 → undefined', () => {
    const v = toConclusionView({
      ...REAL_COMPLETED,
      measurement: { long_mm: 12.5, short_mm: 8, pixel_spacing_source: 'default' },
    } as never)!;
    expect(v.measurementView).toEqual({
      longMm: 12.5,
      shortMm: 8,
      anteroposteriorMm: undefined,
      aspectRatio: undefined,
      volumeMm3: undefined,
      pixelSpacingSource: 'default',
      measurementType: undefined,
    });
    expect(toConclusionView({ ...REAL_COMPLETED, measurement: {} } as never)!.measurementView).toBeUndefined();
  });
});

describe('G23 证据来源溯源（A.126）', () => {
  it('后端给了 sources → chunk_id→文档/章节（只保留非空字段）', () => {
    const view = toConclusionView({
      ...REAL_COMPLETED,
      evidence: {
        knowledge_ids: ['c1', 'c2'],
        sources: {
          c1: { source_doc_id: 'acr_ti_rads_2017', section_path: '第4章/4.2' },
          c2: { source_doc_id: 'acr_ti_rads_2017' },
        },
      },
    } as never)!;
    expect(view.evidenceSources).toEqual({
      c1: { docId: 'acr_ti_rads_2017', sectionPath: '第4章/4.2' },
      c2: { docId: 'acr_ti_rads_2017', sectionPath: undefined },
    });
  });

  it('没有 sources / 脏形状 → undefined（界面回落裸 ID）', () => {
    expect(toConclusionView(REAL_COMPLETED as never)!.evidenceSources).toBeUndefined();
    const v = toConclusionView({
      ...REAL_COMPLETED,
      evidence: { knowledge_ids: ['c1'], sources: { c1: {} , c2: 'junk' } },
    } as never)!;
    expect(v.evidenceSources).toBeUndefined();
  });
});
