import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import AppShell from '@/components/shell/AppShell';
import type { ThemeName } from '@/components/shell/AppSidebar';
import { ORGAN_LABEL, ORGAN_PRESETS, type OrganType } from '@/types/session';
import { createSession } from '@/services/sessionService';
import { getApiErrorMessage } from '@/utils/apiError';

/**
 * 新建检查会话 —— 09_新建检查会话（进入检查前必经）
 *
 * 侧栏高亮**停在「工作列表」**，这是有意为之：本屏是工作列表流程的子页，
 * 导航反映的是「所在板块」而不是「当前子页」（画布 `_nav_highlight_decision`）。
 * 对照 08_设置 —— 设置是一级项，所以高亮在设置。别把这个「不一致」当缺陷修掉。
 *
 * 设计取舍：
 *   · **不要求医生事前声明全部部位** —— 超声是操作者驱动、边扫边定；
 *     这里是「默认预选 + 自动补全」，所以甲状腺默认选中，医生通常一键即可
 *   · 自动补全是**补全而不是替代**，保持显式声明为主，避免纯推断带来的不安全
 *   · 患者行的同意状态在此**显式带出一次** —— 建会话阶段是同意校验的必经点
 *     （这是契约 §1「同意状态仅非常驻」在流程入口的一次性例外，画布明确要求）
 *
 * 部位显示名与 ID 走 types/session 的 ORGAN_LABEL / ORGAN_PRESETS，
 * 与 14_多部位标签排共用同一份，不会再走散。
 *
 * ⚠️ 未接线部分：患者 / 设备的选择器。画布只给了两个已填好的值，没有选择器 UI，
 * 也没有 patient_id / device_id。所以本组件把这两个值作为 props 接收，
 * 由上层（患者登记 / 设备注册流程）传入；未传时只做展示，「开始检查」不会调后端。
 */
export default function SessionCreatePage({
  theme = 'dark',
  patientLabel = '李** · 女 · 41 岁 · 门诊号 A2301 · 已签署 AI 辅助知情同意',
  deviceLabel = '诊室 3 · 便携超声 02 号机（已注册 · 在线）',
  patientId,
  deviceId,
  onStart,
}: {
  theme?: ThemeName;
  patientLabel?: string;
  deviceLabel?: string;
  /** 真实患者 ID：有值时「开始检查」才调后端（画布未给选择器，由上层传入）。 */
  patientId?: string;
  deviceId?: string;
  /** 点击「开始检查」时回调选中的部位（画布 2:294 → 创建会话 → 02_扫查态）。 */
  onStart?: (organ: OrganType) => void;
}) {
  const navigate = useNavigate();
  // 默认预选上次使用的部位 —— 医生通常一键即可
  const [organ, setOrgan] = useState<OrganType>('thyroid');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /**
   * 「开始检查」：真的建会话。
   *
   * 画布 09 只给了两个已填好的值、没有选择器 UI，所以 patient_id / device_id
   * 由 props 传入。**两者齐备才调后端** —— 缺任一个都不该凭空编一个 id 提交，
   * 那会在库里留下一条指向不存在患者的会话。
   */
  const handleStart = async () => {
    onStart?.(organ);
    if (!patientId || !deviceId) return;
    setSubmitting(true);
    setError(null);
    try {
      await createSession({ patient_id: patientId, device_id: deviceId, target_organs: [organ] });
      navigate('/');
    } catch (err: unknown) {
      setError(getApiErrorMessage(err, '创建会话失败'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AppShell
      // 本屏属工作列表流程子页 → 高亮工作列表，不高亮自己
      active="worklist"
      theme={theme}
      screen="09_新建检查会话"
      onNavigate={(key) => navigate(key === 'settings' ? '/settings' : '/worklist')}
    >
      <h1 className="page-title">新建检查会话</h1>

      <div className="page-form-card">
        <span className="page-field-label">患者</span>
        <span className="page-field-value">{patientLabel}</span>

        <span className="page-field-label">设备</span>
        <span className="page-field-value">{deviceLabel}</span>

        <span className="page-field-label">
          检查部位 · 默认已预选上次使用的部位，通常一键即可，不必手工挑
        </span>
        <div className="page-organ-row" data-node="2:285">
          {ORGAN_PRESETS.map((o) => (
            <button
              key={o}
              type="button"
              className={o === organ ? 'page-organ page-organ--selected' : 'page-organ'}
              aria-pressed={o === organ}
              onClick={() => setOrgan(o)}
            >
              {ORGAN_LABEL[o]}
            </button>
          ))}
        </div>
      </div>

      <p className="page-subtitle" style={{ lineHeight: '22px' }}>
        系统会把实际扫到的部位自动补入，作为补全而不是替代 —— 保持显式声明为主，避免纯推断带来的不安全。
      </p>

      {/* 错误提示：仅在提交失败时出现（契约 §0 P1：不满足条件不渲染） */}
      {error && (
        <p className="page-error-note" role="alert">
          {error}
        </p>
      )}

      <div className="page-actions">
        <button
          type="button"
          className="page-primary-action"
          disabled={!patientId || !deviceId || submitting}
          onClick={handleStart}
          title={
            patientId && deviceId
              ? undefined
              : '未接入患者/设备选择器（画布未提供该UI），无法创建会话'
          }
        >
          {submitting ? '创建中…' : '开始检查'}
        </button>
        <button
          type="button"
          className="page-secondary-action"
          onClick={() => navigate('/worklist')}
        >
          从工作列表继续已有会话
        </button>
      </div>
    </AppShell>
  );
}
