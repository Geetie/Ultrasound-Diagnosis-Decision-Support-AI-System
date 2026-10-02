import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Card, Typography, Button, Spin, Empty, Progress, Tag } from 'antd';
import { ReloadOutlined, ClockCircleOutlined, LoadingOutlined } from '@ant-design/icons';
import { tokens } from '@/styles/tokens';
import { useSessionStore } from '@/stores/sessionStore';
import { pollDiagnosisResult, STAGE_LABELS } from '@/services/diagnosisService';
import type { DiagnosisStageEvent } from '@/services/diagnosisService';
import type { DiagnosisResult } from '@/types/api';
import { useProgressiveDiagnosis } from '@/hooks/useProgressiveDiagnosis';
import { useTouchAdaptation } from '@/hooks/useTouchAdaptation';
import DiagnosisConclusion from '@/components/diagnosis/DiagnosisConclusion';
import EvidenceTrace from '@/components/diagnosis/EvidenceTrace';
import { normalizeEvidence } from '@/components/diagnosis/evidence';
import HallucinationWarning from '@/components/diagnosis/HallucinationWarning';
import QualityControlBadge from '@/components/diagnosis/QualityControlBadge';
import MeasurementTable from '@/components/diagnosis/MeasurementTable';
import DifferentialDiagnosisList from '@/components/diagnosis/DifferentialDiagnosisList';
import ClinicalActions from '@/components/diagnosis/ClinicalActions';
import { getConsentDisplayText, isPureReportMode } from '@/utils/consentGuard';
import PatientExplanation from '@/components/diagnosis/PatientExplanation';
import ResearchMetrics from '@/components/diagnosis/ResearchMetrics';
import ProgressiveBlock from '@/components/diagnosis/ProgressiveBlock';

const { Text, Title } = Typography;

type LoadingPhase = 'idle' | 'requesting' | 'polling' | 'completed' | 'timeout' | 'error';

export default function DiagnosisPage() {
  const sessionId = useSessionStore((s) => s.sessionId);
  const touch = useTouchAdaptation();
  const [result, setResult] = useState<DiagnosisResult | null>(null);
  const [phase, setPhase] = useState<LoadingPhase>('idle');
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  // 结果页的数据来源是 URL 上的 request_id（由检查主界面或语音控件在提交成功后带上）。
  // **这里原先写的是 `useState<string | null>(null)` 且没有 setter**：于是 requestId 恒为
  // null，`handleRetry` 永远提前返回、轮询入口是死代码，页面拿不到任何诊断结果。
  const requestId = searchParams.get('request_id');
  const [reviewAcknowledged, setReviewAcknowledged] = useState(false);
  const [responseTier] = useState<'realtime' | 'standard' | 'full'>('standard');
  const [stage, setStage] = useState<DiagnosisStageEvent | null>(null);
  const cancelRef = useRef<{ cancel: () => void } | null>(null);

  // T-GB-FE-01：结论先落地，安全警示/质控/证据链随后分块补齐
  const progressive = useProgressiveDiagnosis(result);

  useEffect(() => {
    return () => {
      cancelRef.current?.cancel();
    };
  }, []);

  const handleResult = useCallback((diagnosisResult: DiagnosisResult) => {
    setResult(diagnosisResult);
    setPhase('completed');
    setReviewAcknowledged(false);
    cancelRef.current = null;
  }, []);

  const handleTimeout = useCallback(() => {
    setPhase('timeout');
    cancelRef.current = null;
  }, []);

  const startPolling = useCallback(
    (id: string) => {
      cancelRef.current?.cancel();
      const controller = new AbortController();
      const poller = pollDiagnosisResult(id, handleResult, handleTimeout, {
        signal: controller.signal,
        firstPollDelay: 800,
        pollInterval: 1500,
        maxWaitMs: 60000,
        tier: responseTier,
        onStage: setStage,
      });
      cancelRef.current = poller;
      setPhase('polling');
    },
    [handleResult, handleTimeout, responseTier],
  );

  // 带上 request_id 进入本页即开始轮询（提交方负责把它放进 URL）。
  // 放进 then 回调触发：既与"轮询是异步过程"的语义一致，也避免在 effect 体内同步
  // setState 引发的级联渲染告警。
  useEffect(() => {
    if (!requestId) return;
    Promise.resolve().then(() => startPolling(requestId));
  }, [requestId, startPolling]);

  const handleRetry = useCallback(() => {
    if (!requestId) return;
    setPhase('requesting');
    startPolling(requestId);
  }, [requestId, startPolling]);

  const isSevere = result?.hallucination_severity === 'severe';
  const needsReview = result?.requires_manual_review ?? false;

  const renderLoadingState = () => {
    const messages: Record<string, string> = {
      requesting: '正在提交诊断请求...',
      polling: 'AI 正在分析影像数据...',
    };
    const msg = messages[phase] ?? '处理中...';
    const stageKeys = [
      'submitted',
      'retrieval',
      'reasoning',
      'fusion',
      'safety',
      'finalizing',
    ] as const;
    const currentIndex = stage ? Math.max(0, stageKeys.indexOf(stage.key)) : 0;

    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          padding: tokens.spacing.xl * (touch.isTouch ? 1 : 2),
          gap: tokens.spacing.md,
        }}
      >
        <Spin size="large" />
        <Text style={{ color: tokens.color.text.secondary, fontSize: 14 }}>{msg}</Text>

        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: tokens.spacing.sm,
            minWidth: touch.isTouch ? 320 : 260,
            background: tokens.color.bg.secondary,
            border: `1px solid ${tokens.color.border.default}`,
            borderRadius: tokens.radius.md,
            padding: tokens.spacing.md,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text style={{ color: tokens.color.text.secondary, fontSize: 12, fontFamily: tokens.font.mono }}>
              推理阶段
            </Text>
            {stage?.estimated && (
              <Tag
                style={{
                  margin: 0,
                  fontSize: 10,
                  lineHeight: '16px',
                  background: tokens.color.accent.primaryAlpha20,
                  borderColor: `${tokens.color.accent.primary}40`,
                  color: tokens.color.accent.primary,
                }}
              >
                预计
              </Tag>
            )}
          </div>

          <Progress
            percent={Math.round(((currentIndex + 1) / stageKeys.length) * 100)}
            showInfo={false}
            size="small"
            strokeColor={tokens.color.accent.primary}
            trailColor={tokens.color.border.default}
          />

          {stageKeys.map((key, index) => {
            const done = index < currentIndex;
            const active = index === currentIndex;
            return (
              <div
                key={key}
                style={{ display: 'flex', alignItems: 'center', gap: tokens.spacing.sm, opacity: done ? 0.55 : 1 }}
              >
                <span
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    flexShrink: 0,
                    background: done
                      ? tokens.color.accent.success
                      : active
                        ? tokens.color.accent.primary
                        : tokens.color.border.default,
                  }}
                />
                <Text
                  style={{
                    fontSize: tokens.touch.fontScale > 1 ? 13 : 12,
                    color: active ? tokens.color.text.primary : tokens.color.text.muted,
                  }}
                >
                  {STAGE_LABELS[key]}
                </Text>
                {active && <LoadingOutlined style={{ fontSize: 10, color: tokens.color.accent.primary }} />}
              </div>
            );
          })}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: tokens.spacing.xs }}>
          <ClockCircleOutlined style={{ color: tokens.color.text.muted, fontSize: 12 }} />
          <Text style={{ color: tokens.color.text.muted, fontSize: 11, fontFamily: tokens.font.mono }}>
            预计耗时 3-15 秒
          </Text>
        </div>
      </div>
    );
  };

  const renderTimeoutState = () => (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: tokens.spacing.xl * 2,
        gap: tokens.spacing.md,
      }}
    >
      <Empty
        description={
          <Text style={{ color: tokens.color.text.muted }}>诊断请求超时，请重试</Text>
        }
      />
      <Button
        icon={<ReloadOutlined />}
        onClick={handleRetry}
        style={{
          background: tokens.color.accent.primaryAlpha20,
          borderColor: `${tokens.color.accent.primary}40`,
          color: tokens.color.accent.primary,
        }}
      >
        重新获取结果
      </Button>
    </div>
  );

  const renderIdleState = () => (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        padding: tokens.spacing.xl * 2,
        gap: tokens.spacing.md,
      }}
    >
      <Title
        level={4}
        style={{
          fontFamily: tokens.font.display,
          color: tokens.color.accent.secondary,
          letterSpacing: '0.05em',
          margin: 0,
        }}
      >
        DIAGNOSIS
      </Title>
      <Text style={{ color: tokens.color.text.muted, fontSize: 13 }}>
        {sessionId ? '请从检查页面发起诊断请求' : '请先创建检查会话'}
      </Text>
    </div>
  );

  const renderResult = () => {
    if (!result) return null;

    const evidence = normalizeEvidence(result.evidence as Record<string, unknown> | undefined);
    const stageTimings = progressive.stageTimings;

    const consentText = getConsentDisplayText(result.consent_status_at_output);
    const pureReportMode = isPureReportMode(result.consent_status_at_output);

    // T-G1-INT-06：同意状态展示拦截。
    // 同意被撤回（或从未授权）时，本结果可能是在同意失效前算出来的，或来自
    // 纯报告模式——继续把 AI 结论、恶性概率、鉴别诊断当诊断意见展示，医生会
    // 在不知情的情况下使用一份不该使用的输出。故此处整段拦截。
    if (pureReportMode) {
      return (
        <div
          data-testid="consent-blocked-notice"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: tokens.spacing.sm,
            background: tokens.color.bg.secondary,
            border: `1px solid ${tokens.color.accent.warning}60`,
            borderLeft: `3px solid ${tokens.color.accent.warning}`,
            borderRadius: tokens.radius.md,
            padding: tokens.spacing.lg,
          }}
        >
          <Text
            style={{
              fontFamily: tokens.font.display,
              color: tokens.color.accent.warning,
              fontSize: 12,
              letterSpacing: '0.05em',
            }}
          >
            AI OUTPUT BLOCKED
          </Text>
          <Text style={{ color: tokens.color.text.primary, fontSize: 14 }}>{consentText}</Text>
          <Text style={{ color: tokens.color.text.muted, fontSize: 12 }}>
            AI 辅助诊断结论已按同意状态拦截，不在此处展示。请以超声原始报告与执业医师判断为准。
          </Text>
        </div>
      );
    }

    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: tokens.spacing.lg }}>
        {/* 结论先行：医生最需要的信息第一时间落地 */}
        <ProgressiveBlock ready={progressive.isRevealed('conclusion')} skeletonRows={3} label="结论">
          <Card
            size="small"
            style={{
              background: tokens.color.bg.secondary,
              border: `1px solid ${tokens.color.border.default}`,
              borderRadius: tokens.radius.md,
            }}
            styles={{ body: { padding: tokens.spacing.lg } }}
          >
            <DiagnosisConclusion result={result} responseTier={responseTier} />
          </Card>
        </ProgressiveBlock>

        {/* 安全警示随后补齐 */}
        <ProgressiveBlock ready={progressive.isRevealed('hallucination')} skeletonRows={2} label="安全警示">
          <HallucinationWarning
            visible={isSevere}
            reviewReason={result.diagnosis?.review_reason ?? result.review_reason ?? null}
            onAcknowledgeReview={() => setReviewAcknowledged(true)}
          />
        </ProgressiveBlock>

        <ProgressiveBlock ready={progressive.isRevealed('quality')} skeletonRows={2} label="质量控制">
          <Card
            size="small"
            style={{
              background: tokens.color.bg.secondary,
              border: `1px solid ${tokens.color.border.default}`,
              borderRadius: tokens.radius.md,
            }}
            styles={{ body: { padding: tokens.spacing.md } }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: tokens.spacing.sm }}>
              <Text
                style={{
                  fontFamily: tokens.font.display,
                  color: tokens.color.accent.secondary,
                  fontSize: 12,
                  letterSpacing: '0.05em',
                }}
              >
                QUALITY CONTROL
              </Text>
              {needsReview && !reviewAcknowledged && (
                <Text style={{ color: tokens.color.accent.danger, fontSize: 11, fontFamily: tokens.font.mono }}>
                  ⚠ 需人工审核
                </Text>
              )}
            </div>
            <QualityControlBadge
              hallucination_severity={result.hallucination_severity}
              fusion_status={result.fusion_status}
              desensitization_status={result.desensitization_status}
            />
          </Card>
        </ProgressiveBlock>

        {/* M7 处置建议：无建议时整块不渲染（不展示"无需处置"这种误导性结论） */}
        {result.clinical_actions && result.clinical_actions.length > 0 && (
          <ProgressiveBlock ready={progressive.isRevealed('quality')} skeletonRows={2} label="处置建议">
            <Card
              size="small"
              style={{
                background: tokens.color.bg.secondary,
                border: `1px solid ${tokens.color.border.default}`,
                borderRadius: tokens.radius.md,
              }}
              styles={{ body: { padding: tokens.spacing.md } }}
            >
              <Text
                style={{
                  display: 'block',
                  marginBottom: tokens.spacing.sm,
                  fontFamily: tokens.font.display,
                  color: tokens.color.accent.secondary,
                  fontSize: 12,
                  letterSpacing: '0.05em',
                }}
              >
                CLINICAL ACTIONS
              </Text>
              <ClinicalActions actions={result.clinical_actions} />
            </Card>
          </ProgressiveBlock>
        )}

        {/* M4 鉴别诊断清单：无清单时整块不渲染 */}
        {result.differential_diagnoses && result.differential_diagnoses.length > 0 && (
          <ProgressiveBlock ready={progressive.isRevealed('quality')} skeletonRows={3} label="鉴别诊断">
            <Card
              size="small"
              style={{
                background: tokens.color.bg.secondary,
                border: `1px solid ${tokens.color.border.default}`,
                borderRadius: tokens.radius.md,
              }}
              styles={{ body: { padding: tokens.spacing.md } }}
            >
              <Text
                style={{
                  display: 'block',
                  marginBottom: tokens.spacing.sm,
                  fontFamily: tokens.font.display,
                  color: tokens.color.accent.secondary,
                  fontSize: 12,
                  letterSpacing: '0.05em',
                }}
              >
                DIFFERENTIAL DIAGNOSIS
              </Text>
              <DifferentialDiagnosisList items={result.differential_diagnoses} />
            </Card>
          </ProgressiveBlock>
        )}

        {/* M3 自动测量：无测量数据时整块不渲染（不展示空表格） */}
        {result.measurement && (
          <ProgressiveBlock ready={progressive.isRevealed('quality')} skeletonRows={2} label="自动测量">
            <Card
              size="small"
              style={{
                background: tokens.color.bg.secondary,
                border: `1px solid ${tokens.color.border.default}`,
                borderRadius: tokens.radius.md,
              }}
              styles={{ body: { padding: tokens.spacing.md } }}
            >
              <MeasurementTable measurement={result.measurement} />
            </Card>
          </ProgressiveBlock>
        )}

        <ProgressiveBlock ready={progressive.isRevealed('evidence')} skeletonRows={4} label="证据链">
          <EvidenceTrace
            evidence={evidence}
            crossModalityHints={result.cross_modality_hints ?? []}
          />
        </ProgressiveBlock>

        {/* M11 科研指标：默认折叠（科研用途，展开会稀释临床结论） */}
        {result.research_metrics && (
          <ResearchMetrics metrics={result.research_metrics} />
        )}

        {/* M10 患者解释话术：默认折叠在底部，标注为非正式报告内容 */}
        {result.patient_explanation && (
          <PatientExplanation explanation={result.patient_explanation} />
        )}

        {/* M8：报告查看与签发入口。放在结果页——医生看完结论，最自然的下一步就是出报告 */}
        {result.status === 'completed' && (
          <Card
            size="small"
            style={{
              background: tokens.color.bg.secondary,
              border: `1px solid ${tokens.color.border.default}`,
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: tokens.spacing.md,
                flexWrap: 'wrap',
              }}
            >
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <Text style={{ color: tokens.color.text.primary, fontSize: 13 }}>
                  报告查看与签发
                </Text>
                <Text style={{ color: tokens.color.text.muted, fontSize: 12 }}>
                  按器官模板生成草稿 → 签发（HSM 数字签名）→ 推送 PACS/HIS
                </Text>
              </div>
              <Button
                type="primary"
                onClick={() =>
                  navigate(`/report?request_id=${encodeURIComponent(result.request_id)}`)
                }
                style={{ minHeight: tokens.touch.hitTarget }}
              >
                打开报告
              </Button>
            </div>
          </Card>
        )}

        {/* 真实阶段耗时：来自后端 findings.timings_ms，仅在有数据时展示 */}
        {stageTimings.length > 0 && (
          <div
            data-testid="stage-timings"
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: tokens.spacing.sm,
              padding: `${tokens.spacing.sm}px 0`,
            }}
          >
            {stageTimings.map((item) => (
              <span
                key={item.key}
                style={{
                  fontFamily: tokens.font.mono,
                  fontSize: 10,
                  color: tokens.color.text.muted,
                  border: `1px solid ${tokens.color.border.default}`,
                  borderRadius: tokens.radius.sm,
                  padding: '2px 8px',
                  background: tokens.color.bg.secondary,
                }}
              >
                {item.label} {item.durationMs}ms
              </span>
            ))}
          </div>
        )}

        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: `${tokens.spacing.sm}px 0`,
          }}
        >
          <Text style={{ color: tokens.color.text.muted, fontSize: 10, fontFamily: tokens.font.mono }}>
            模型 {result.model_version} · 知识库 {result.knowledge_base_version} · 耗时 {result.processing_time_ms}ms
          </Text>
          <Text style={{ color: tokens.color.text.muted, fontSize: 10, fontFamily: tokens.font.mono }}>
            {result.request_id.slice(0, 12)}...
          </Text>
        </div>
      </div>
    );
  };

  const renderContent = () => {
    switch (phase) {
      case 'idle':
        return renderIdleState();
      case 'requesting':
      case 'polling':
        return renderLoadingState();
      case 'timeout':
        return renderTimeoutState();
      case 'completed':
        return renderResult();
      default:
        return renderIdleState();
    }
  };

  return (
    <div
      className="h-full animate-fade-in"
      style={{
        background: tokens.color.bg.primary,
        overflowY: 'auto',
        padding: tokens.spacing.lg,
      }}
    >
      <div style={{ maxWidth: 800, margin: '0 auto' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: tokens.spacing.lg,
          }}
        >
          <Title
            level={4}
            style={{
              fontFamily: tokens.font.display,
              color: tokens.color.accent.primary,
              letterSpacing: '0.08em',
              margin: 0,
            }}
          >
            DIAGNOSIS
          </Title>
          <div style={{ display: 'flex', alignItems: 'center', gap: tokens.spacing.sm }}>
            {phase === 'completed' && (
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={handleRetry}
                style={{
                  background: tokens.color.accent.primaryAlpha20,
                  borderColor: `${tokens.color.accent.primary}40`,
                  color: tokens.color.accent.primary,
                  fontFamily: tokens.font.mono,
                  fontSize: 11,
                }}
              >
                刷新
              </Button>
            )}
          </div>
        </div>

        {renderContent()}
      </div>
    </div>
  );
}
