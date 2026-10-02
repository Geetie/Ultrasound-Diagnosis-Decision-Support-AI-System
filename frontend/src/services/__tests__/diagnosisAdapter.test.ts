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
