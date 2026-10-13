import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Input, Modal, Space, Tag, Typography, message } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloudUploadOutlined,
  FileTextOutlined,
  HistoryOutlined,
  PrinterOutlined,
  SafetyCertificateOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { getApiErrorMessage } from '@/utils/apiError';
import {
  createReportDraft,
  getReportByRequest,
  pushReport,
  signReport,
  updateReportSections,
  verifyReportSignature,
} from '@/services/reportService';
import type { ReportResponse } from '@/types/report';
import { getSession } from '@/services/sessionService';
import { tokens } from '@/styles/tokens';
import { stripMarkdownEmphasis } from '@/utils/text';

/**
 * 缺失段落的临床权重（A.32，与后端 SIGN_BLOCKING_SECTIONS 同源）：
 *   findings / diagnosis 是报告的证据与结论 —— 缺了签发会被服务端 E1030 拒绝，
 *   必须补录；其余段落走知情确认（医生确认"本次检查确实无可测量项/无额外处置"）。
 * 硬门禁与服务端各维护一份是刻意的：前端负责"提前告诉你"，服务端负责"真的拦"。
 */
const BLOCKING_SECTIONS: ReadonlySet<string> = new Set(['findings', 'diagnosis']);

const { Title, Text, Paragraph } = Typography;

const ORGAN_LABELS: Record<string, string> = {
  thyroid: '甲状腺',
  liver: '肝脏',
  cardiac: '心脏',
};

const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  draft: { label: '草稿', color: tokens.color.accent.warning },
  signed: { label: '已签发', color: tokens.color.accent.success },
  pushed: { label: '已推送', color: tokens.color.accent.info },
};

/** 必需字段 → 中文名，供完整性面板提示"还差哪一段"。 */
const FIELD_LABELS: Record<string, string> = {
  organ: '检查器官',
  findings: '超声所见',
  measurement_summary: '测量摘要',
  conclusion: '诊断意见',
  recommendation: '建议',
  clinical_actions: '处置建议',
  thyroid_grading: '甲状腺分级',
  liver_grading: '肝脏分级',
  cardiac_grading: '心脏分级',
};

function labelOf(field: string): string {
  return FIELD_LABELS[field] ?? field;
}

export default function ReportPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const requestId = searchParams.get('request_id') ?? '';

  // 输入框取值："默认跟着 URL 上的 request_id，医生手动改过之后以手动值为准"。
  // 用派生值而不是"在 effect 里同步 setState"——后者会触发级联渲染（hooks 规则也禁止），
  // 而这里本质上是同一个数据的两种来源，派生比同步更准确。
  const [typedRequestId, setTypedRequestId] = useState<string | null>(null);
  const inputRequestId = typedRequestId ?? requestId;
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verifyNote, setVerifyNote] = useState<string | null>(null);

  const status = useMemo(() => {
    if (!report) return null;
    return (
      STATUS_LABELS[report.status] ?? {
        label: report.status,
        color: tokens.color.text.secondary,
      }
    );
  }, [report]);

  // 带 request_id 进入时自动取最新报告；状态更新都发生在网络回调里
  useEffect(() => {
    if (!requestId) return;
    let cancelled = false;
    getReportByRequest(requestId)
      .then((loaded) => {
        if (cancelled) return;
        setReport(loaded);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // 尚未生成草稿是正常状态（404），不当作错误打断医生
        setReport(null);
        setError(getApiErrorMessage(err, '尚未生成报告草稿'));
      });
    return () => {
      cancelled = true;
    };
  }, [requestId]);

  const handleGenerate = useCallback(async () => {
    const target = inputRequestId.trim();
    if (!target) {
      message.warning('请先填写诊断请求 ID');
      return;
    }
    setLoading(true);
    try {
      const created = await createReportDraft({ request_id: target });
      setReport(created);
      setError(null);
      setVerifyNote(null);
      message.success('报告草稿已生成');
    } catch (err: unknown) {
      message.error(getApiErrorMessage(err, '生成报告草稿失败'));
    } finally {
      setLoading(false);
    }
  }, [inputRequestId]);

  // 完整性信息在 handleSign 里要用（它决定要不要弹确认），必须先声明 ——
  // 原本在组件尾部，被 useCallback 的依赖数组提前引用会踩 TDZ。
  const completeness = report?.completeness ?? null;
  const [signConfirmOpen, setSignConfirmOpen] = useState(false);
  const [draftSections, setDraftSections] = useState<Record<string, string>>({});
  const [savingSections, setSavingSections] = useState(false);
  // useMemo 稳定身份：handleSign 的 useCallback 依赖它，每次渲染新建数组会让
  // 回调身份跟着抖（react-hooks/exhaustive-deps warning，超出 --max-warnings 预算）
  const missingSections = useMemo(
    () => (completeness && !completeness.passed ? completeness.missing : []),
    [completeness],
  );
  // 阻断段（超声所见 / 诊断意见）：缺了服务端会以 E1030 拒签，前端提前说明
  const blockingSections = missingSections.filter((f) => BLOCKING_SECTIONS.has(f));

  /**
   * 补录：把医生写的段落合并进草稿并重渲染。
   *
   * 为什么必须有：报告页此前**没有任何编辑能力**（A.30 查实），缺段落的医生
   * 只能"签一份缺内容的报告"或"不用系统"。补录与门禁是一对 ——
   * 只上门禁会把医生堵死；只上补录，缺段落的报告照样能签出去。
   */
  const handleSaveSections = useCallback(async () => {
    if (!report) return;
    const filled = Object.fromEntries(
      Object.entries(draftSections).filter(([, v]) => v.trim() !== '')
    );
    if (Object.keys(filled).length === 0) {
      message.warning('请先填写要补录的段落');
      return;
    }
    setSavingSections(true);
    try {
      const updated = await updateReportSections(report.report_id, filled);
      setReport(updated);
      setDraftSections({});
      setVerifyNote(null);
      message.success('补录已保存，报告正文已按模板重新渲染');
    } catch (err: unknown) {
      message.error(getApiErrorMessage(err, '补录保存失败'));
    } finally {
      setSavingSections(false);
    }
  }, [report, draftSections]);

  /** 真正执行签发（无任何前置确认）。完整性已过时由 handleSign 直接走这里。 */
  const doSign = useCallback(async () => {
    if (!report) return;
    setLoading(true);
    try {
      const signed = await signReport(report.report_id);
      setReport(signed);
      setVerifyNote(null);
      setSignConfirmOpen(false);
      message.success('报告已签发');
    } catch (err: unknown) {
      message.error(getApiErrorMessage(err, '签发失败'));
    } finally {
      setLoading(false);
    }
  }, [report]);

  /**
   * 签发（A.32 定稿口径）：
   *   · 缺**阻断段**（超声所见 / 诊断意见）→ 按钮禁用（见下方渲染），到不了这里；
   *   · 缺其余段落（测量摘要 / 处置建议…）→ 知情确认，确认后可签；
   *   · 全部齐备 → 直接签。
   *
   * 为什么阻断段要硬禁用（推翻 A.30 的"知情确认"方案）：超声所见是报告的
   * **证据段**，诊断意见是**结论段** —— 缺了它们，签出去的是"无证据的断言"，
   * 这正是融合层 / Self-RAG 防的东西，签发环节不该成为漏点。而补录入口就在
   * 同页（本页新增），禁用不是死路，是一步之遥。其余段落（测量/处置）在
   * 部分检查中确实可以为空，知情确认即可。
   *
   * 为什么用受控 <Modal> 而不是 Modal.confirm 静态方法：
   *   ①静态方法渲染在 ConfigProvider **之外**，拿不到暗室主题与 token；
   *   ②它挂在 React 树外，RTL 的 cleanup 拆不到，测试里会跨用例残留。
   */
  const handleSign = useCallback(() => {
    if (!report) return;
    if (blockingSections.length > 0) {
      // 理论到不了（按钮已禁用）；防御一次，把原因说清楚
      message.error(
        `缺少必需段落：${blockingSections.map(labelOf).join('、')}，请先在下方补录`
      );
      return;
    }
    if (missingSections.length === 0) {
      void doSign();
      return;
    }
    setSignConfirmOpen(true);
  }, [report, missingSections, blockingSections, doSign]);

  const handlePush = useCallback(async () => {
    if (!report) return;
    setLoading(true);
    try {
      const pushed = await pushReport(report.report_id);
      setReport(pushed);
      message.success('已推送到 PACS/HIS');
    } catch (err: unknown) {
      message.error(getApiErrorMessage(err, '推送失败'));
    } finally {
      setLoading(false);
    }
  }, [report]);

  const handleVerify = useCallback(async () => {
    if (!report) return;
    setLoading(true);
    try {
      const result = await verifyReportSignature(report.report_id);
      setVerifyNote(
        result.signature_verified
          ? '签名与正文一致，报告未被改动'
          : '签名校验未通过：正文可能已被改动，请重新签发前先核对内容'
      );
      setReport((current) =>
        current ? { ...current, signature_verified: result.signature_verified } : current
      );
    } catch (err: unknown) {
      message.error(getApiErrorMessage(err, '签名校验失败'));
    } finally {
      setLoading(false);
    }
  }, [report]);

  const signed = Boolean(report?.signature_present);

  /**
   * 历史对比入口的患者标识（A.54）。
   *
   * 三级来源，缺一不可少：
   *   1. 报告自带 `patient_id`（新版报告都有）；
   *   2. 没有就退回报告所属会话 —— 用 `session_id` 查会话拿 `patient_id`
   *      （老报告可能没写 patient_id，但会话一定知道是谁）；
   *   3. 都没有 → 入口禁用并说明原因（不猜、不用 request_id 顶替）。
   * 为什么放在顶部且不依赖"是否已生成报告"：历史对比看的是**既往检查**，
   * 与当前这次有没有报告无关 —— 报告页恰恰是医生最早想看历史的地方。
   */
  const [historyPatientId, setHistoryPatientId] = useState('');
  const [historyPidResolving, setHistoryPidResolving] = useState(false);
  useEffect(() => {
    if (report?.patient_id) {
      setHistoryPatientId(report.patient_id);
      return;
    }
    if (!report?.session_id) {
      setHistoryPatientId('');
      return;
    }
    let alive = true;
    setHistoryPidResolving(true);
    getSession(report.session_id)
      .then((s) => {
        if (alive) setHistoryPatientId(s.patient_id ?? '');
      })
      .catch(() => {
        if (alive) setHistoryPatientId('');
      })
      .finally(() => {
        if (alive) setHistoryPidResolving(false);
      });
    return () => {
      alive = false;
    };
  }, [report?.patient_id, report?.session_id]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: tokens.spacing.md }}>
      <Space direction="vertical" size={4}>
        <Title level={4} style={{ margin: 0, color: tokens.color.text.primary }}>
          报告查看与签发
        </Title>
        <Text style={{ color: tokens.color.text.muted, fontSize: 12 }}>
          按器官模板生成草稿 → 签发（HSM 数字签名）→ 推送 PACS/HIS。每一步都会写入审计日志。
        </Text>
      </Space>

      <Card style={{ background: tokens.color.bg.secondary }} size="small">
        <Space wrap>
          <Input
            value={inputRequestId}
            onChange={(event) => setTypedRequestId(event.target.value)}
            placeholder="诊断请求 ID（request_id）"
            aria-label="诊断请求 ID"
            style={{ width: 320 }}
          />
          <Button
            type="primary"
            icon={<FileTextOutlined />}
            loading={loading}
            onClick={handleGenerate}
            style={{ minHeight: tokens.touch.hitTarget }}
          >
            生成草稿
          </Button>
          <Button
            icon={<ReloadOutlined />}
            loading={loading}
            disabled={!report}
            onClick={handleVerify}
            style={{ minHeight: tokens.touch.hitTarget }}
          >
            校验签名
          </Button>
          {/* 第二梯队③ 历史对比阅片（A.54）：入口不依赖"当前是否已生成报告"——
              医生在签发前就想看既往检查的结论与测值趋势。患者标识按
              报告 → 会话 两级解析（见 historyPatientId 注释）。 */}
          <Button
            icon={<HistoryOutlined />}
            loading={historyPidResolving}
            disabled={!historyPatientId}
            title={
              historyPatientId
                ? '查看该患者的历史检查并做前后对比'
                : '尚未取得患者标识（报告与所属会话都没有），无法查历史'
            }
            onClick={() => navigate(`/history?patient_id=${encodeURIComponent(historyPatientId)}`)}
            style={{ minHeight: tokens.touch.hitTarget }}
            data-region="report-to-history"
          >
            历史对比
          </Button>
        </Space>
      </Card>

      {error && !report && (
        <Alert
          type="info"
          showIcon
          message={error}
          description="请确认该诊断已完成并写入结果后，再生成报告草稿。"
        />
      )}

      {report && (
        <Card
          size="small"
          title={
            <Space size={8}>
              <Text strong style={{ color: tokens.color.text.primary }}>
                {ORGAN_LABELS[report.organ ?? ''] ?? report.organ ?? '未知器官'}
              </Text>
              {status && (
                <Tag color={status.color} data-testid="report-status">
                  {status.label}
                </Tag>
              )}
              {report.template_version && (
                <Text style={{ color: tokens.color.text.muted, fontSize: 12 }}>
                  模板 {report.template_version}
                </Text>
              )}
            </Space>
          }
          style={{ background: tokens.color.bg.secondary }}
        >
          <Space direction="vertical" size={tokens.spacing.sm} style={{ width: '100%' }}>
            <Space wrap size={16}>
              <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>
                报告 ID：<Text code>{report.report_id}</Text>
              </Text>
              {report.signed_by && (
                <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>
                  签发人：{report.signed_by}（{report.signing_key_name ?? '未知密钥'}）
                </Text>
              )}
              {report.pacs_status && (
                <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>
                  推送：{report.pacs_adapter ?? '-'} / {report.pacs_status}
                  {report.pacs_attempts ? `（尝试 ${report.pacs_attempts} 次）` : ''}
                </Text>
              )}
            </Space>

            {verifyNote && (
              <Alert
                type={verifyNote.includes('未通过') ? 'error' : 'success'}
                showIcon
                message={verifyNote}
                data-testid="verify-note"
              />
            )}

            {completeness && (
              <div data-testid="completeness-panel">
                <Space
                  size={6}
                  style={{ marginBottom: tokens.spacing.xs, display: 'flex', alignItems: 'center' }}
                >
                  {completeness.passed ? (
                    <CheckCircleOutlined style={{ color: tokens.color.accent.success }} />
                  ) : (
                    <CloseCircleOutlined style={{ color: tokens.color.accent.danger }} />
                  )}
                  <Text style={{ color: tokens.color.text.primary, fontSize: 13 }}>
                    完整性校验：{completeness.passed ? '必需段落齐备' : '存在缺失段落'}
                  </Text>
                </Space>
                {!completeness.passed && (
                  <>
                    <Paragraph style={{ margin: 0, color: tokens.color.text.secondary, fontSize: 12 }}>
                      缺失：
                      {completeness.missing.map((field) => labelOf(field)).join('、')}
                    </Paragraph>
                    <Text style={{ color: tokens.color.text.muted, fontSize: 12 }}>
                      请回到诊断结果页补录后再生成报告；缺失内容不应带病签发。
                    </Text>
                  </>
                )}
              </div>
            )}

            <div>
              <Text style={{ color: tokens.color.text.muted, fontSize: 12 }}>报告正文预览</Text>
              <pre
                data-testid="report-preview"
                style={{
                  margin: `${tokens.spacing.xs}px 0 0`,
                  padding: tokens.spacing.sm,
                  maxHeight: 360,
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  fontFamily: tokens.font.mono,
                  fontSize: 12,
                  lineHeight: 1.6,
                  color: tokens.color.text.primary,
                  background: tokens.color.bg.primary,
                  border: `1px solid ${tokens.color.border.default}`,
                  borderRadius: tokens.radius.sm,
                }}
              >
                {/* A.58：报告正文是 Markdown 模板渲染结果，`**` 在 <pre> 里会裸露。
                    这里只去强调/行内代码标记，**保留标题与结构**（报告是文档）。 */}
                {report.rendered
                  ? stripMarkdownEmphasis(report.rendered)
                  : '（无正文：该报告尚未按模板渲染）'}
              </pre>
            </div>

            {/* A.32 补录面板：仅草稿且有缺失段落时渲染（默认沉默） */}
            {!signed && completeness && !completeness.passed && (
              <div
                data-testid="supplement-panel"
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: tokens.spacing.sm,
                  padding: tokens.spacing.sm,
                  border: `1px solid ${tokens.color.border.default}`,
                  borderRadius: tokens.radius.sm,
                  background: tokens.color.bg.primary,
                }}
              >
                <Text strong style={{ color: tokens.color.text.primary, fontSize: 13 }}>
                  补录缺失段落
                </Text>
                {missingSections.map((field) => {
                  const blocking = BLOCKING_SECTIONS.has(field);
                  return (
                    <div
                      key={field}
                      style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
                    >
                      <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>
                        {labelOf(field)}
                        {blocking && (
                          <Text style={{ color: tokens.color.accent.danger, fontSize: 12 }}>
                            {' '}（必需，补录后才能签发）
                          </Text>
                        )}
                      </Text>
                      <Input.TextArea
                        value={draftSections[field] ?? ''}
                        onChange={(e) =>
                          setDraftSections((prev) => ({ ...prev, [field]: e.target.value }))
                        }
                        placeholder={`请输入${labelOf(field)}`}
                        autoSize={{ minRows: 2, maxRows: 6 }}
                        aria-label={`补录${labelOf(field)}`}
                        data-testid={`supplement-input-${field}`}
                      />
                    </div>
                  );
                })}
                <div>
                  <Button
                    type="primary"
                    loading={savingSections}
                    onClick={handleSaveSections}
                    data-testid="supplement-save"
                    style={{ minHeight: tokens.touch.hitTarget }}
                  >
                    保存补录并重新渲染
                  </Button>
                </div>
              </div>
            )}

            <Space wrap>
              <Button
                type="primary"
                icon={<SafetyCertificateOutlined />}
                loading={loading}
                disabled={signed || blockingSections.length > 0}
                onClick={handleSign}
                style={{ minHeight: tokens.touch.hitTarget }}
              >
                {signed ? '已签发' : '签发报告'}
              </Button>
              <Button
                icon={<CloudUploadOutlined />}
                loading={loading}
                disabled={!signed}
                onClick={handlePush}
                style={{ minHeight: tokens.touch.hitTarget }}
              >
                推送 PACS/HIS
              </Button>
              {/* 打印/导出 PDF（A.38，阅片生命周期「分发」环节的最小闭环）：
                  医生要把报告交给患者或病历系统，此前只能截图。浏览器打印
                  对话框自选「另存为 PDF」即覆盖导出 —— 完整 PACS/HIS 结构化
                  推送仍走上方按钮（已签名的正文才可推送）。 */}
              <Button
                icon={<PrinterOutlined />}
                disabled={!report}
                onClick={() => window.print()}
                style={{ minHeight: tokens.touch.hitTarget }}
              >
                打印 / 导出 PDF
              </Button>

              {!signed && (
                <Text style={{ color: tokens.color.text.muted, fontSize: 12 }}>
                  未签发不可推送：推送的是已签名的报告正文，避免无签名内容进入病历。
                </Text>
              )}
            </Space>
          </Space>
        </Card>
      )}

      {/* 签发知情确认 —— 只在完整性未过时被 handleSign 打开。
          受控而非 Modal.confirm：见 handleSign 的说明。 */}
      <Modal
        title="报告存在缺失段落，确认仍要签发？"
        open={signConfirmOpen}
        onCancel={() => setSignConfirmOpen(false)}
        onOk={() => void doSign()}
        okText="我已知晓缺失，仍要签发"
        okButtonProps={{ danger: true, 'data-testid': 'sign-confirm-ok' }}
        cancelText="返回补录"
        cancelButtonProps={{ 'data-testid': 'sign-confirm-cancel' }}
        width={520}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <p style={{ margin: 0 }}>
            缺失：
            <strong>{missingSections.map(labelOf).join('、')}</strong>
          </p>
          {missingSections.includes('findings') && (
            <p style={{ margin: 0, color: tokens.color.accent.danger }}>
              「超声所见」是超声报告的主体段落，缺失时本报告不构成完整的检查记录。
            </p>
          )}
          <p style={{ margin: 0, color: tokens.color.text.muted, fontSize: 12 }}>
            签发会用 HSM 数字签名锁定当前正文，并把「医生对内容负责」写入审计链；
            此后任何改动都会被签名校验拦下。补录功能尚未上线，请确认你已在别处
            完成记录、且确需现在签发。
          </p>
        </div>
      </Modal>
    </div>
  );
}
