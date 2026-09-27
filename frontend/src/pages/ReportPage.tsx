import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Input, Space, Tag, Typography, message } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloudUploadOutlined,
  FileTextOutlined,
  SafetyCertificateOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import { getApiErrorMessage } from '@/utils/apiError';
import {
  createReportDraft,
  getReportByRequest,
  pushReport,
  signReport,
  verifyReportSignature,
} from '@/services/reportService';
import type { ReportResponse } from '@/types/report';
import { tokens } from '@/styles/tokens';

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

  const handleSign = useCallback(async () => {
    if (!report) return;
    setLoading(true);
    try {
      const signed = await signReport(report.report_id);
      setReport(signed);
      setVerifyNote(null);
      message.success('报告已签发');
    } catch (err: unknown) {
      message.error(getApiErrorMessage(err, '签发失败'));
    } finally {
      setLoading(false);
    }
  }, [report]);

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

  const completeness = report?.completeness ?? null;
  const signed = Boolean(report?.signature_present);

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
                {report.rendered ?? '（无正文：该报告尚未按模板渲染）'}
              </pre>
            </div>

            <Space wrap>
              <Button
                type="primary"
                icon={<SafetyCertificateOutlined />}
                loading={loading}
                disabled={signed}
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
              {!signed && (
                <Text style={{ color: tokens.color.text.muted, fontSize: 12 }}>
                  未签发不可推送：推送的是已签名的报告正文，避免无签名内容进入病历。
                </Text>
              )}
            </Space>
          </Space>
        </Card>
      )}
    </div>
  );
}
