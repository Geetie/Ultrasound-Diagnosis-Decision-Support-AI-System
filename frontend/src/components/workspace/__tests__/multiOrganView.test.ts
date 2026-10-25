import { describe, expect, it } from 'vitest';

import {
  needsRerun,
  organReasonText,
  organStatusLabel,
  toCrossOrganView,
  toOrganRows,
} from '../multiOrganView';
import type { CrossOrganBlock, OrganDiagnosisStatus } from '@/types/api';

/**
 * 多器官展示口径（T7）。
 *
 * 这个文件守的是一句话：**不许把"没跑"说成"没问题"**。
 * `cross_organ.status` 三态（ok / insufficient_responses / unavailable）与
 * 「整个块缺失」四种输入里，只有 `ok` 才能说"已核对"；其余三种都必须说清没做。
 */

function result(patch: Partial<OrganDiagnosisStatus>): OrganDiagnosisStatus {
  return {
    organ: 'thyroid',
    status: 'completed',
    confidence: 0.9,
    elapsed_ms: 1200,
    ...patch,
  };
}

describe('器官状态文案', () => {
  it('六个后端状态都有中文，且 skipped ≠ completed', () => {
    expect(organStatusLabel('completed')).toBe('已完成');
    expect(organStatusLabel('failed')).toBe('失败');
    expect(organStatusLabel('skipped')).toBe('未执行');
    expect(organStatusLabel('needs_review')).toBe('需复核');
    expect(organStatusLabel('in_progress')).toBe('分析中');
    expect(organStatusLabel('pending')).toBe('等待中');
    // 关键反证：未执行/失败绝不能显示成"已完成"
    expect(organStatusLabel('skipped')).not.toBe(organStatusLabel('completed'));
    expect(organStatusLabel('failed')).not.toBe(organStatusLabel('completed'));
  });

  it('未知状态原样回显，不猜成已完成', () => {
    expect(organStatusLabel('some_new_status')).toBe('some_new_status');
  });
});

describe('失败原因人话化', () => {
  it('后端四个机器原因各有可读中文', () => {
    expect(organReasonText('per_organ_timeout')).toMatch(/超时/);
    expect(organReasonText('total_budget_exhausted')).toMatch(/总时限/);
    expect(organReasonText('request_unavailable')).toMatch(/判读请求/);
    expect(organReasonText('session_not_found')).toMatch(/会话/);
  });

  it('pipeline_error 保留异常类型便于排障', () => {
    expect(organReasonText('pipeline_error:TimeoutError')).toMatch(/TimeoutError/);
    expect(organReasonText('pipeline_error:RuntimeError')).toMatch(/RuntimeError/);
  });

  it('未登记原因原样显示（不编"未知错误"掩盖真实原因）', () => {
    expect(organReasonText('weird_new_reason')).toBe('weird_new_reason');
  });

  it('正常完成没有原因行', () => {
    expect(organReasonText(null)).toBeNull();
    expect(organReasonText(undefined)).toBeNull();
    expect(organReasonText('')).toBeNull();
  });
});

describe('哪些器官要给「单独重跑」入口', () => {
  it('只有失败与未执行才给', () => {
    expect(needsRerun('failed')).toBe(true);
    expect(needsRerun('skipped')).toBe(true);
    expect(needsRerun('completed')).toBe(false);
    expect(needsRerun('needs_review')).toBe(false);
    expect(needsRerun('in_progress')).toBe(false);
  });
});

describe('toOrganRows', () => {
  it('逐部位给出中文名、状态、原因、重跑资格与 request_id', () => {
    const rows = toOrganRows([
      result({ organ: 'thyroid', status: 'completed', request_id: 'req_1' }),
      result({
        organ: 'liver',
        status: 'failed',
        reason: 'per_organ_timeout',
        request_id: 'req_2',
      }),
      result({ organ: 'cardiac', status: 'skipped', reason: 'total_budget_exhausted' }),
    ]);

    expect(rows.map((r) => r.label)).toEqual(['甲状腺', '肝脏', '心脏']);
    expect(rows[0].statusText).toBe('已完成');
    expect(rows[0].canRerun).toBe(false);
    expect(rows[0].requestId).toBe('req_1');
    expect(rows[1].statusText).toBe('失败');
    expect(rows[1].canRerun).toBe(true);
    expect(rows[1].reasonText).toMatch(/超时/);
    expect(rows[2].statusText).toBe('未执行');
    expect(rows[2].canRerun).toBe(true);
    expect(rows[2].requestId).toBeNull();
  });

  it('未知器官代码原样显示（不兜底成"未知部位"）', () => {
    const rows = toOrganRows([result({ organ: 'spleen' })]);
    expect(rows[0].label).toBe('spleen');
  });
});

describe('toCrossOrganView：三态必须区分（核心）', () => {
  it('ok → 才敢说"已核对"，并列出冲突/一致性真实结果', () => {
    const block: CrossOrganBlock = {
      status: 'ok',
      organ_count: 3,
      conflict: {
        has_conflict: true,
        conflict_types: ['diagnosis_conflict'],
        resolution_suggestion: 'manual_review',
      },
      consistency: {
        is_consistent: false,
        severity: 'high',
        compared_with_prior: false,
        compared_with_guidelines: true,
      },
    };
    const view = toCrossOrganView(block);
    expect(view.tone).toBe('ok');
    expect(view.title).toMatch(/已核对/);
    expect(view.lines.join('\n')).toMatch(/检出跨部位冲突/);
    expect(view.lines.join('\n')).toMatch(/diagnosis_conflict/);
    expect(view.lines.join('\n')).toMatch(/一致/);
  });

  it('insufficient_responses → 明说"未完成核对"，且不许出现"一致"结论', () => {
    const view = toCrossOrganView({
      status: 'insufficient_responses',
      organ_count: 1,
      reason: 'need >= 2 organ conclusions, got 1',
    });
    expect(view.tone).toBe('not_run');
    expect(view.title).toMatch(/未完成核对/);
    const text = [view.title, ...view.lines].join('\n');
    // 断言没有把"没跑"说成"没问题"
    expect(text).not.toMatch(/各部位结论相互一致/);
    expect(text).not.toMatch(/未检出跨部位冲突/);
    expect(text).toMatch(/前提不成立|不足/);
    expect(text).toMatch(/不等于/);
  });

  it('unavailable → 明说引擎不可用，不许暗示一切正常', () => {
    const view = toCrossOrganView({
      status: 'unavailable',
      reason: 'fusion_engine_unavailable',
    });
    expect(view.tone).toBe('not_run');
    expect(view.title).toMatch(/未完成核对/);
    const text = [view.title, ...view.lines].join('\n');
    expect(text).toMatch(/不可用/);
    expect(text).not.toMatch(/各部位结论相互一致/);
  });

  it('块缺失（后端未执行）→ 同样是"没做"，不是"没问题"', () => {
    for (const input of [null, undefined, {} as CrossOrganBlock]) {
      const view = toCrossOrganView(input as CrossOrganBlock | null | undefined);
      expect(view.tone).toBe('empty');
      expect(view.title).toMatch(/未执行/);
      expect(view.lines.join('\n')).toMatch(/不等于/);
    }
  });

  it('ok 但没有任何明细时也如实说"未返回可读明细"，不编造一致', () => {
    const view = toCrossOrganView({ status: 'ok', organ_count: 2 });
    expect(view.tone).toBe('ok');
    expect(view.lines.join('\n')).toMatch(/未返回可读明细/);
  });
});
