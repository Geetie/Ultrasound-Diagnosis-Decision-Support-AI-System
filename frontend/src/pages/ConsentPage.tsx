import { useState, useEffect, useCallback } from 'react';
import { Card, Form, Input, Button, Typography, Alert, message } from 'antd';
import {
  SafetyCertificateOutlined,
  FileProtectOutlined,
  WarningOutlined,
  ApiOutlined,
  EditOutlined,
  EyeInvisibleOutlined,
} from '@ant-design/icons';
import { getConsentStatus, grantConsent, withdrawConsent } from '@/services/consentService';
import type { ConsentActionData } from '@/services/consentService';
import { getApiErrorMessage } from '@/utils/apiError';
import { useSessionStore } from '@/stores/sessionStore';
import { tokens } from '@/styles/tokens';
import type { ConsentStatus } from '@/types/api';

const { Title, Text } = Typography;

interface ConsentFormValues {
  patient_id: string;
  consent_version: string;
  operator_id: string;
}

const statusConfigMap: Record<
  NonNullable<ConsentStatus['consent_status']>,
  {
    icon: React.ReactNode;
    color: string;
    title: string;
    desc: string;
    alertType: 'success' | 'warning' | 'error' | 'info';
  }
> = {
  granted: {
    icon: <SafetyCertificateOutlined />,
    color: tokens.color.accent.success,
    title: 'AI辅助诊断已解锁',
    desc: '患者已签署知情同意书，AI辅助诊断功能完全可用',
    alertType: 'success',
  },
  denied: {
    icon: <FileProtectOutlined />,
    color: tokens.color.accent.warning,
    title: '纯报告模式',
    desc: '患者未签署知情同意，AI诊断入口已禁用，仅保留基础报告编辑功能',
    alertType: 'warning',
  },
  withdrawn: {
    icon: <WarningOutlined />,
    color: tokens.color.accent.danger,
    title: '知情同意已撤回',
    desc: '患者已撤回知情同意，AI诊断结论已立即隐藏，系统运行于纯报告模式',
    alertType: 'error',
  },
  pending: {
    icon: <FileProtectOutlined />,
    color: tokens.color.accent.info,
    title: '知情同意审批中',
    desc: '知情同意正在审批流程中，请等待审批结果',
    alertType: 'info',
  },
};

export default function ConsentPage() {
  const [loading, setLoading] = useState(false);
  const [consentDetail, setConsentDetail] = useState<ConsentStatus | null>(null);
  const [form] = Form.useForm<ConsentFormValues>();

  const consentStatus = useSessionStore((s) => s.consentStatus);
  const patientId = useSessionStore((s) => s.patientId);
  const setConsentStatus = useSessionStore((s) => s.setConsentStatus);

  const fetchStatus = useCallback(async () => {
    if (!patientId) return;
    try {
      const result = await getConsentStatus(patientId);
      setConsentDetail(result);
      setConsentStatus(result.consent_status);
    } catch {
      // silently fail
    }
  }, [patientId, setConsentStatus]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  useEffect(() => {
    if (consentStatus === 'withdrawn' && consentDetail?.consent_status !== 'withdrawn' && patientId) {
      getConsentStatus(patientId).then(setConsentDetail).catch(() => {});
    }
  }, [consentStatus, consentDetail?.consent_status, patientId]);

  const handleGrant = async (values: ConsentFormValues) => {
    setLoading(true);
    try {
      const data: ConsentActionData = {
        patient_id: values.patient_id,
        consent_version: values.consent_version,
        operator_id: values.operator_id,
      };
      const result = await grantConsent(data);
      setConsentDetail(result);
      setConsentStatus(result.consent_status);
      message.success('知情同意已签署，AI辅助诊断已解锁');
    } catch (err: unknown) {
      message.error(getApiErrorMessage(err, '签署知情同意失败'));
    } finally {
      setLoading(false);
    }
  };

  const handleWithdraw = async () => {
    if (!consentDetail) return;
    setLoading(true);
    try {
      const data: ConsentActionData = {
        patient_id: consentDetail.patient_id,
        consent_version: consentDetail.consent_version,
        operator_id: form.getFieldValue('operator_id') || 'current_operator',
      };
      const result = await withdrawConsent(data);
      setConsentDetail(result);
      setConsentStatus(result.consent_status);
      message.warning('知情同意已撤回，系统切换至纯报告模式');
    } catch (err: unknown) {
      message.error(getApiErrorMessage(err, '撤回知情同意失败'));
    } finally {
      setLoading(false);
    }
  };

  const config = consentStatus ? statusConfigMap[consentStatus] : null;
  const isGranted = consentStatus === 'granted';
  const isPureReport = consentStatus === 'denied' || consentStatus === 'withdrawn' || consentStatus === null;

  return (
    <div className="h-full overflow-auto animate-fade-in" style={{ padding: '0 48px' }}>
      <Title
        level={4}
        style={{
          fontFamily: tokens.font.display,
          color: tokens.color.accent.primary,
          letterSpacing: '0.05em',
          marginBottom: 24,
        }}
      >
        INFORMED CONSENT
      </Title>

      <div style={{ maxWidth: 640, display: 'flex', flexDirection: 'column', gap: 20 }}>
        {config && (
          <Card
            style={{
              background: tokens.color.bg.secondary,
              border: `1px solid ${config.color}40`,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 12 }}>
              <span style={{ fontSize: 32, color: config.color }}>{config.icon}</span>
              <Text style={{ color: tokens.color.text.primary, fontSize: 16, fontWeight: 600 }}>
                {config.title}
              </Text>
            </div>
            <Alert
              type={config.alertType}
              message={config.desc}
              showIcon={false}
              style={{
                background: `${config.color}10`,
                border: `1px solid ${config.color}30`,
              }}
            />
            {consentDetail && (
              <div style={{ marginTop: 12, display: 'flex', gap: 24, flexWrap: 'wrap' }}>
                {consentDetail.consented_at && (
                  <Text style={{ color: tokens.color.text.muted, fontSize: 12 }}>
                    签署时间: {new Date(consentDetail.consented_at).toLocaleString('zh-CN')}
                  </Text>
                )}
                {consentDetail.withdrawn_at && (
                  <Text style={{ color: tokens.color.text.muted, fontSize: 12 }}>
                    撤回时间: {new Date(consentDetail.withdrawn_at).toLocaleString('zh-CN')}
                  </Text>
                )}
                <Text style={{ color: tokens.color.text.muted, fontSize: 12 }}>
                  版本: {consentDetail.consent_version}
                </Text>
              </div>
            )}
          </Card>
        )}

        <Card
          style={{
            background: tokens.color.bg.secondary,
            border: `1px solid ${tokens.color.border.default}`,
          }}
        >
          <Title level={5} style={{ color: tokens.color.text.primary, marginBottom: 16 }}>
            功能可用性
          </Title>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <EditOutlined style={{ color: tokens.color.accent.success, fontSize: 16 }} />
              <Text style={{ color: tokens.color.text.primary }}>基础报告编辑</Text>
              <Text style={{ color: tokens.color.accent.success, fontSize: 12, marginLeft: 'auto' }}>
                可用
              </Text>
            </div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                opacity: isPureReport ? 0.4 : 1,
              }}
            >
              <ApiOutlined
                style={{
                  color: isGranted ? tokens.color.accent.primary : tokens.color.text.muted,
                  fontSize: 16,
                }}
              />
              <Text
                style={{
                  color: isGranted ? tokens.color.text.primary : tokens.color.text.muted,
                }}
              >
                AI辅助诊断
              </Text>
              <Text
                style={{
                  color: isGranted ? tokens.color.accent.primary : tokens.color.text.muted,
                  fontSize: 12,
                  marginLeft: 'auto',
                }}
              >
                {isGranted ? '可用' : '已禁用'}
              </Text>
            </div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                opacity: isPureReport ? 0.4 : 1,
              }}
            >
              <EyeInvisibleOutlined
                style={{
                  color: isGranted ? tokens.color.accent.primary : tokens.color.text.muted,
                  fontSize: 16,
                }}
              />
              <Text
                style={{
                  color: isGranted ? tokens.color.text.primary : tokens.color.text.muted,
                }}
              >
                诊断结论查看
              </Text>
              <Text
                style={{
                  color: isGranted ? tokens.color.accent.primary : tokens.color.text.muted,
                  fontSize: 12,
                  marginLeft: 'auto',
                }}
              >
                {isGranted ? '可用' : '已隐藏'}
              </Text>
            </div>
          </div>
        </Card>

        {isGranted ? (
          <Card
            style={{
              background: tokens.color.bg.secondary,
              border: `1px solid ${tokens.color.border.default}`,
            }}
          >
            <Title level={5} style={{ color: tokens.color.text.primary, marginBottom: 12 }}>
              撤回知情同意
            </Title>
            <Text
              style={{
                color: tokens.color.text.secondary,
                fontSize: 13,
                display: 'block',
                marginBottom: 16,
              }}
            >
              撤回后系统将立即切换至纯报告模式，所有AI诊断结论将被隐藏
            </Text>
            <Button danger onClick={handleWithdraw} loading={loading} style={{ maxWidth: 200 }}>
              撤回知情同意
            </Button>
          </Card>
        ) : consentStatus === 'pending' ? (
          <Card
            style={{
              background: tokens.color.bg.secondary,
              border: `1px solid ${tokens.color.border.default}`,
            }}
          >
            <div style={{ textAlign: 'center', padding: '16px 0' }}>
              <Text style={{ color: tokens.color.text.secondary }}>
                知情同意审批中，请等待审批结果
              </Text>
            </div>
          </Card>
        ) : (
          <Card
            style={{
              background: tokens.color.bg.secondary,
              border: `1px solid ${tokens.color.border.default}`,
            }}
          >
            <Title level={5} style={{ color: tokens.color.text.primary, marginBottom: 16 }}>
              签署知情同意
            </Title>
            {consentStatus === 'withdrawn' && (
              <Alert
                type="error"
                message="患者此前已撤回知情同意，重新签署将再次启用AI辅助诊断功能"
                showIcon
                style={{ marginBottom: 16 }}
              />
            )}
            <Form
              form={form}
              layout="vertical"
              onFinish={handleGrant}
              initialValues={{
                patient_id: patientId || '',
                consent_version: '1.0',
              }}
            >
              <Form.Item
                label="患者ID"
                name="patient_id"
                rules={[{ required: true, message: '请输入患者ID' }]}
              >
                <Input placeholder="输入患者ID" />
              </Form.Item>
              <Form.Item
                label="同意书版本"
                name="consent_version"
                rules={[{ required: true, message: '请输入同意书版本' }]}
              >
                <Input placeholder="例如: 1.0" />
              </Form.Item>
              <Form.Item
                label="操作员ID"
                name="operator_id"
                rules={[{ required: true, message: '请输入操作员ID' }]}
              >
                <Input placeholder="输入操作员ID" />
              </Form.Item>
              <Form.Item>
                <Button type="primary" htmlType="submit" loading={loading} block>
                  签署知情同意
                </Button>
              </Form.Item>
            </Form>
          </Card>
        )}
      </div>
    </div>
  );
}
