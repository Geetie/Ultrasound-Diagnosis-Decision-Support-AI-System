import type { CrossOrganBlock, OrganDiagnosisStatus, OrganStatus } from '@/types/api';
import { organDisplayName } from '@/types/session';

/**
 * 多器官联合诊断的**展示口径**（纯函数，独立成文件以满足 react-refresh 约束）。
 *
 * 这个文件的全部意义是**不许把"没跑"说成"没问题"**：
 *   · 器官状态 skipped / failed ≠ completed；
 *   · `cross_organ.status` 的三态必须分开表达（ok / insufficient_responses / unavailable）；
 *   · `cross_organ` 整个缺席 = 未执行，也不许渲染成"一致"。
 * 这是临床口径问题：医生看到"多部位一致"会降低警惕，而系统实际可能根本没比。
 */

/** 单器官状态 → 医生可见中文。未知状态原样回显（不冒充"完成"）。 */
export function organStatusLabel(status: OrganStatus | string): string {
  switch (status) {
    case 'completed':
      return '已完成';
    case 'failed':
      return '失败';
    case 'skipped':
      return '未执行';
    case 'needs_review':
      return '需复核';
    case 'in_progress':
      return '分析中';
    case 'pending':
      return '等待中';
    default:
      // 后端新增了枚举值而前端还没跟上：如实显示原值，绝不猜成"已完成"
      return String(status);
  }
}

/**
 * 后端失败/跳过原因（机器串）→ 可读中文。
 *
 * 取值来源 `app/services/multi_organ_orchestrator.py:57-60`：
 * per_organ_timeout / pipeline_error / total_budget_exhausted / session_not_found，
 * 以及 `request_unavailable`（拿不到该器官的 request 行）。
 * 未登记的原因**原样显示**——编一个"未知错误"会把真实原因掩盖掉（A.86 教训）。
 */
export function organReasonText(reason?: string | null): string | null {
  if (!reason) return null;
  const s = String(reason);
  if (s === 'per_organ_timeout') return '该部位分析超时（单部位时限内未出结论）';
  if (s === 'total_budget_exhausted') return '本次多部位总时限已用尽，该部位未开始分析';
  if (s === 'request_unavailable') return '未能为该部位建立判读请求，未开始分析';
  if (s === 'session_not_found') return '检查会话不存在或已失效';
  // `pipeline_error:<ExceptionName>` —— 保留异常类型便于排障，但先说人话
  if (s.startsWith('pipeline_error:')) {
    return `该部位分析过程出错（${s.slice('pipeline_error:'.length)}）`;
  }
  return s;
}

/** 该器官是否需要「单独重跑」入口：失败 / 未执行 才有意义。 */
export function needsRerun(status: OrganStatus | string): boolean {
  return status === 'failed' || status === 'skipped';
}

export interface OrganRowView {
  organ: string;
  /** 中文显示名（未知代码原样，`organDisplayName` 的既有口径）。 */
  label: string;
  status: OrganStatus | string;
  statusText: string;
  /** 可读失败/跳过原因；正常完成 = null。 */
  reasonText: string | null;
  /** 是否显示「单独重跑该部位」。 */
  canRerun: boolean;
  /** 该器官的 request_id（报告入口用）；缺失 = null。 */
  requestId: string | null;
}

export function toOrganRows(results: OrganDiagnosisStatus[]): OrganRowView[] {
  return results.map((r) => ({
    organ: r.organ,
    label: organDisplayName(r.organ) ?? r.organ,
    status: r.status,
    statusText: organStatusLabel(r.status),
    reasonText: organReasonText(r.reason),
    canRerun: needsRerun(r.status),
    requestId: r.request_id ?? null,
  }));
}

/** 多部位一致性提示的展示模型。`tone` 决定用哪档语义色。 */
export interface CrossOrganView {
  /**
   * · `ok`      —— 真的做了核对，可以报结果；
   * · `not_run` —— 没做（前提不成立 / 引擎不可用 / 根本没返回）；
   * · `empty`   —— 响应里没有这块（后端未执行），同样按"没做"表达。
   */
  tone: 'ok' | 'not_run' | 'empty';
  /** 一句话标题（措辞必须区分"已核对"与"未核对"）。 */
  title: string;
  /** 明细行（冲突/一致性/原因）。空数组 = 无附加明细。 */
  lines: string[];
}

/**
 * 跨器官融合块 → 展示模型。
 *
 * ⚠️ 三态口径（`app/services/cross_organ_fusion.py`）：
 *   `ok`                     → 冲突检测 + 跨器官一致性都有真实结果；
 *   `insufficient_responses` → 有效结论 < 2 份，**前提不成立**（不是"无冲突"）；
 *   `unavailable`            → 融合引擎不可用，**没跑**（不是"没问题"）。
 * 把后两种显示成"多部位一致"就是把"没跑"说成"没问题"，是本函数唯一要防的事。
 */
export function toCrossOrganView(
  block: CrossOrganBlock | null | undefined
): CrossOrganView {
  if (!block || typeof block.status !== 'string' || block.status === '') {
    return {
      tone: 'empty',
      title: '多部位一致性：本次未执行核对',
      lines: ['系统本次没有返回跨部位核对结果 —— 这不等于「各部位一致」。'],
    };
  }

  if (block.status !== 'ok') {
    const why =
      block.status === 'insufficient_responses'
        ? `有效结论不足 2 份（本次 ${block.organ_count ?? 0} 份），核对前提不成立`
        : block.status === 'unavailable'
          ? '跨部位核对引擎不可用，本次未执行'
          : `核对未完成（${block.status}）`;
    return {
      tone: 'not_run',
      title: '多部位一致性：本次未完成核对',
      lines: [
        why,
        '这不等于「各部位一致」，也不构成任何排除结论。',
      ],
    };
  }

  const lines: string[] = [];
  const conflict = block.conflict;
  if (conflict) {
    lines.push(
      conflict.has_conflict
        ? `检出跨部位冲突：${
            conflict.conflict_types.length > 0
              ? conflict.conflict_types.join('、')
              : '类型未标注'
          }`
        : '未检出跨部位冲突'
    );
    if (conflict.has_conflict && conflict.resolution_suggestion) {
      lines.push(`处理建议：${conflict.resolution_suggestion}`);
    }
  }
  const consistency = block.consistency;
  if (consistency) {
    lines.push(
      consistency.is_consistent
        ? '各部位结论相互一致'
        : `各部位结论一致性存在差异（严重度：${consistency.severity}）`
    );
  }
  if (lines.length === 0) lines.push('核对已完成，但未返回可读明细');

  return { tone: 'ok', title: '多部位一致性：已核对', lines };
}
