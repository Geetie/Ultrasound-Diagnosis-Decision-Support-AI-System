import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import AppShell from '@/components/shell/AppShell';
import VoiceDictationButton from '@/components/voice/VoiceDictationButton';
import type { ThemeName } from '@/components/shell/AppSidebar';
import { ORGAN_LABEL, ORGAN_PRESETS, type OrganType } from '@/types/session';
import { createSession } from '@/services/sessionService';
import {
  createFhirPatient,
  getFhirPatient,
  listPatients,
  type PatientListItem,
} from '@/services/fhirService';
import { getApiErrorCode, getApiErrorMessage } from '@/utils/apiError';

/**
 * 新建检查会话 —— 09_新建检查会话（进入检查前必经）
 *
 * 侧栏高亮停在「工作列表」是有意为之：本屏是工作列表流程的子页，
 * 导航反映的是「所在板块」而不是「当前子页」（画布 _nav_highlight_decision）。
 * 别把这个「不一致」当缺陷修掉。
 *
 * 设计取舍：
 *   · 不要求医生事前声明全部部位 —— 超声是操作者驱动、边扫边定；
 *     这里是「默认预选 + 自动补全」，甲状腺默认选中，医生通常一键即可
 *   · 自动补全是补全而不是替代，保持显式声明为主，避免纯推断带来的不安全
 *
 * 【接线 2026-10-08】原先本页只显示画布给的两个写死值（李** / 诊室 3），
 * 没有选择器也没有 patient_id / device_id，「开始检查」永久禁用 —— 医生在
 * 界面上开不了新检查。接线后的真实数据链：
 *
 *   患者编号 → GET /fhir/Patient/{id} 校验（存在性 + 知情同意，一次调用两件事）
 *     ├─ 200            → 已确认（同意有效），可开始检查
 *     ├─ 403 E1008/E1010 → 未签署/已撤回同意 → 引导找质控/科室负责人
 *     │   （consent 端点医生 403，本来就不可能由医生补签 —— 如实引导）
 *     └─ 404 E1002      → 库中无此患者行 → 医生可就地建档（POST /fhir/Patient
 *                         只要求登录，医生有权限），建档后重新校验
 *   患者候选 → GET /api/v1/patients?q=（A.51 新增端点）给编号/姓名模糊候选，
 *              点候选即走同一条校验路径。**纯增强**：搜索失败静默兜底，
 *              手工录入 + 校验的老路径一字未改（走查路径不被推翻）。
 *   设备编号 → 手工录入 + 记住上次使用（A.51 已补 GET /api/v1/admin/device
 *              台账端点；本页仍以输入为主，device_id 后端不校验存在性）
 *   开始检查 → POST /sessions → 带 ?session_id= 跳工作区。
 *              原先 navigate('/') 把新会话 id 丢了 —— 工作区的上传与判读都以
 *              session_id 为外键，丢掉它医生又得回工作列表绕一圈。
 *
 * 部位显示名与 ID 走 types/session 的 ORGAN_LABEL / ORGAN_PRESETS，
 * 与 14_多部位标签排共用同一份，不会再走散。
 */

/** 上次成功建会话用的设备号。key 前缀与 access_token 等既有键一致。 */
const LAST_DEVICE_KEY = 'uai_last_device_id';

/**
 * 知情同意未签署 / 已撤回的指引。同意由质控/科室负责人签署（医生角色 403），如实引导。
 */
const CONSENT_GUIDANCE =
  '该患者尚未签署 AI 辅助知情同意 —— 知情同意由质控 / 科室负责人在「知情同意」页签署，' +
  '签署后回到本页重新「校验患者」即可。';
const WITHDRAWN_GUIDANCE = '该患者的知情同意已被撤回 —— 需重新签署后才能开始 AI 辅助检查。';

type PatientState =
  | { kind: 'unverified' }
  | { kind: 'verifying' }
  | { kind: 'verified'; patientId: string; displayName: string }
  | { kind: 'needs-consent'; patientId: string; message: string }
  | { kind: 'not-found'; patientId: string }
  | { kind: 'creating' }
  | { kind: 'created'; patientId: string }
  | { kind: 'error'; message: string };

/** 把建会话/校验阶段的 consent 类错误（可能是校验后、创建前的竞态）收敛成同一指引。 */
function consentMessageFor(code: string | null): string | null {
  if (code === 'E1008') return CONSENT_GUIDANCE;
  if (code === 'E1010') return WITHDRAWN_GUIDANCE;
  return null;
}

export default function SessionCreatePage({ theme = 'dark' }: { theme?: ThemeName }) {
  const navigate = useNavigate();
  // 默认预选上次使用的部位 —— 医生通常一键即可
  // **2026-10-28 修（P1-9）**：从"单选 1 个部位"改为"多选"。
  // 原实现 `target_organs: [organ]` 只能传 1 个 → 生产上永远造不出多部位会话，
  // 而多器官端点 / 跨器官互检 / 05·14 两态全都以 `target_organs.length >= 2` 为前提
  // （routes.tsx 的 isMultiOrgan 判定）→ 那三块能力事实上不可达。
  // 现在保留"默认预选甲状腺"，其余部位可点选/取消；首个选中项 = 单器官判读用的部位。
  const [organs, setOrgans] = useState<OrganType[]>(['thyroid']);
  const toggleOrgan = useCallback((o: OrganType) => {
    setOrgans((prev) => {
      if (prev.includes(o)) {
        // 不允许清空：至少留一个部位（判读必须带 target_organ）
        return prev.length <= 1 ? prev : prev.filter((x) => x !== o);
      }
      return [...prev, o];
    });
  }, []);
  const [patientInput, setPatientInput] = useState('');
  const [patientState, setPatientState] = useState<PatientState>({ kind: 'unverified' });
  // 上次成功建会话用过的设备号直接回填 —— 重复检查时设备往往就是这一台
  const [deviceId, setDeviceId] = useState(() => localStorage.getItem(LAST_DEVICE_KEY) ?? '');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // A.51 患者候选（搜索是增强：失败一律静默兜底，不影响手工录入链路）
  const [suggestions, setSuggestions] = useState<PatientListItem[]>([]);

  // 输入 ≥2 字符后防抖检索；点候选直接复用同一个校验入口。
  useEffect(() => {
    const q = patientInput.trim();
    if (q.length < 2) {
      setSuggestions([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      listPatients({ q, limit: 6 })
        .then((res) => {
          if (!cancelled) setSuggestions(res.items ?? []);
        })
        .catch(() => {
          // 检索失败不打扰医生（端点不可用/限流都在这里被吞）——手工录入仍可用
          if (!cancelled) setSuggestions([]);
        });
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [patientInput]);

  const handleVerify = async (explicitPid?: string) => {
    const pid = (explicitPid ?? patientInput).trim();
    if (!pid) return;
    setPatientState({ kind: 'verifying' });
    try {
      const patient = await getFhirPatient(pid);
      setPatientState({
        kind: 'verified',
        patientId: pid,
        displayName: patient.name?.[0]?.text || pid,
      });
    } catch (err: unknown) {
      const code = getApiErrorCode(err);
      const consentMsg = consentMessageFor(code);
      if (consentMsg) {
        setPatientState({ kind: 'needs-consent', patientId: pid, message: consentMsg });
      } else if (code === 'E1002') {
        // 这条 404 只在「同意有效但 patients 表无行」时出现（后端先查同意再查行）
        setPatientState({ kind: 'not-found', patientId: pid });
      } else {
        setPatientState({ kind: 'error', message: getApiErrorMessage(err, '患者校验失败') });
      }
    }
  };

  /** 就地建档（404 分支）。建档不等于同意 —— 成功后仍要求重新校验。 */
  const handleCreatePatient = async () => {
    const pid = patientState.kind === 'not-found' ? patientState.patientId : patientInput.trim();
    if (!pid) return;
    setPatientState({ kind: 'creating' });
    try {
      await createFhirPatient({ patient_id: pid });
      setPatientState({ kind: 'created', patientId: pid });
    } catch (err: unknown) {
      const code = getApiErrorCode(err);
      if (code === 'E1001') {
        // 已存在（可能是并发建档/竞态）→ 回到建档前状态并说明，不吞事实
        setPatientState({ kind: 'not-found', patientId: pid });
        setSubmitError('该患者已存在 —— 请直接点「校验患者」确认同意状态');
      } else {
        setPatientState({ kind: 'error', message: getApiErrorMessage(err, '建档失败') });
      }
    }
  };

  /** 点候选 = 填入编号并立即校验（少一次点击；校验路径与手工完全同一条）。 */
  const handlePickSuggestion = (pid: string) => {
    setPatientInput(pid);
    setSuggestions([]);
    void handleVerify(pid);
  };

  const handleStart = async () => {
    if (patientState.kind !== 'verified') return;
    const trimmedDevice = deviceId.trim();
    if (!trimmedDevice) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const session = await createSession({
        patient_id: patientState.patientId,
        device_id: trimmedDevice,
        target_organs: organs,
      });
      localStorage.setItem(LAST_DEVICE_KEY, trimmedDevice);
      navigate(`/?session_id=${encodeURIComponent(session.session_id)}`);
    } catch (err: unknown) {
      // 校验通过后才发生同意失效（撤回/管理员改判）—— 把状态如实退回去
      const consentMsg = consentMessageFor(getApiErrorCode(err));
      if (consentMsg) {
        setPatientState({
          kind: 'needs-consent',
          patientId: patientState.patientId,
          message: consentMsg,
        });
      } else {
        setSubmitError(getApiErrorMessage(err, '创建会话失败'));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const canSubmit = patientState.kind === 'verified' && deviceId.trim() !== '' && !submitting;
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
        <span className="page-field-label">患者编号 · 校验存在性与 AI 辅助知情同意</span>
        <div className="page-verify-row">
          <input
            className="page-field-input"
            value={patientInput}
            onChange={(e) => setPatientInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void handleVerify();
            }}
            placeholder="建档编号 / 门诊号"
            aria-label="患者编号"
            disabled={patientState.kind === 'verifying' || patientState.kind === 'creating'}
          />
          <button
            type="button"
            className="page-secondary-action"
            onClick={() => void handleVerify()}
            disabled={
              !patientInput.trim() ||
              patientState.kind === 'verifying' ||
              patientState.kind === 'creating'
            }
          >
            {patientState.kind === 'verifying' ? '校验中…' : '校验患者'}
          </button>
          {/* A.58 口径落地：主流程语音入口（新建会话页）。
              本页**还没有 session_id**，所以不用指令型 `VoiceControl`（其确认层要求会话），
              用「口述→可编辑文本」的 `VoiceDictationButton` 填进患者编号框 ——
              医生在设备旁双手不便时不用敲键盘。文本仍可编辑、仍要点「校验患者」。 */}
          <VoiceDictationButton
            label="口述患者编号"
            disabled={patientState.kind === 'verifying' || patientState.kind === 'creating'}
            onTranscript={(text) => setPatientInput(text.trim())}
          />
        </div>

        {/* A.51 患者候选：仅在尚未确认患者时给建议，点选即校验。
            搜索为空/失败都不渲染任何东西（手工录入路径不受影响）。 */}
        {suggestions.length > 0 &&
          patientState.kind !== 'verified' &&
          patientState.kind !== 'verifying' && (
            <ul
              className="page-suggest-list"
              data-region="patient-suggest"
              aria-label="患者候选"
            >
              {suggestions.map((s) => (
                <li key={s.patient_id}>
                  <button
                    type="button"
                    className="page-suggest-item"
                    onClick={() => handlePickSuggestion(s.patient_id)}
                  >
                    <span className="page-suggest-name">
                      {s.name || s.patient_id}
                    </span>
                    <span className="page-suggest-meta">
                      {s.patient_id}
                      {s.consent_status === 'granted' ? ' · 同意有效' : ''}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}

        {patientState.kind === 'verified' && (
          <span className="page-field-value">
            已确认：{patientState.displayName}（{patientState.patientId}） · AI 辅助知情同意有效
          </span>
        )}

        {patientState.kind === 'needs-consent' && (
          <span className="page-field-note page-field-note--stop" role="alert">
            {patientState.message}
          </span>
        )}

        {patientState.kind === 'not-found' && (
          <>
            <span className="page-field-note">
              系统里没有这个患者。可以直接建档（医生有权限），建档后点「校验患者」重新确认；
              若该患者此前未签署知情同意，建档后仍需质控 / 科室负责人先签署。
            </span>
            <button
              type="button"
              className="page-secondary-action"
              onClick={() => void handleCreatePatient()}
            >
              为 {patientState.patientId} 建档
            </button>
          </>
        )}

        {patientState.kind === 'created' && (
          <span className="page-field-note">
            建档完成。请点「校验患者」重新确认 —— 若该患者未签署知情同意，还需质控 /
            科室负责人先签署。
          </span>
        )}

        {patientState.kind === 'error' && (
          <span className="page-field-note page-field-note--stop" role="alert">
            {patientState.message}
          </span>
        )}

        <span className="page-field-label">设备编号 · 由管理员登记；会记住上一次使用的编号</span>
        <input
          className="page-field-input"
          value={deviceId}
          onChange={(e) => setDeviceId(e.target.value)}
          placeholder="设备编号"
          aria-label="设备编号"
        />

        <span className="page-field-label">
          检查部位 · 可多选（选 2 个以上走多部位联合判读；首个选中项为单器官判读部位）
        </span>
        <div className="page-organ-row" data-node="2:285">
          {ORGAN_PRESETS.map((o) => {
            const selected = organs.includes(o);
            return (
              <button
                key={o}
                type="button"
                className={selected ? 'page-organ page-organ--selected' : 'page-organ'}
                aria-pressed={selected}
                // 2026-10-28（P1-9）：多选切换；至少保留 1 个部位
                onClick={() => toggleOrgan(o)}
                title={
                  selected && organs.length <= 1
                    ? '至少保留一个检查部位'
                    : selected
                      ? '点击取消该部位'
                      : '点击加入该部位'
                }
              >
                {ORGAN_LABEL[o]}
              </button>
            );
          })}
        </div>
        {organs.length > 1 && (
          <p className="page-note" data-region="multi-organ-hint">
            已选 {organs.length} 个部位：{organs.map((o) => ORGAN_LABEL[o]).join('、')}
            {' '}· 判读会走多部位联合端点
          </p>
        )}
      </div>

      <p className="page-subtitle" style={{ lineHeight: '22px' }}>
        系统会把实际扫到的部位自动补入，作为补全而不是替代 —— 保持显式声明为主，避免纯推断带来的不安全。
      </p>

      {/* 提交失败提示：仅在失败时出现（契约 §0 P1：不满足条件不渲染） */}
      {submitError && (
        <p className="page-error-note" role="alert">
          {submitError}
        </p>
      )}

      <div className="page-actions">
        <button
          type="button"
          className="page-primary-action"
          disabled={!canSubmit}
          onClick={() => void handleStart()}
          title={canSubmit ? undefined : '先「校验患者」并填写设备编号'}
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