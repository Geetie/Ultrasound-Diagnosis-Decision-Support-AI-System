/**
 * 质控回填面板（A.133 / G42 + G38 1/3）。
 *
 * ## 为什么放在报告页、且报告**草稿期不显示最终诊断**
 *
 * `final_diagnosis` 是季度回顾审计（病理/随访/专家共识）回填的金标准，它代表
 * "事后知道的答案"。在医生**正在写结论**时展示它会造成锚定偏倚（先看到答案再下判断），
 * 所以草稿态只显示患者沟通话术，最终诊断在**已签发/已推送**后才出现——
 * 此时报告已经定稿，回看金标准属于质控复盘，不构成对本次判断的提示。
 *
 * ## 数据来源
 *
 * 全部来自 `GET /diagnosis/result/{request_id}`（A.107 契约）：
 * - `final_diagnosis` / `final_diagnosis_source` / `final_diagnosis_at`（G42）
 * - `clinical_attributes.patient_explanation`（M10，G38 的第三个组件）
 *
 * 缺值整块不渲染（默认沉默），不写"暂无最终诊断"这类占位。
 */
import { Card, Tag, Typography } from 'antd';
import { tokens } from '@/styles/tokens';

const { Text, Paragraph } = Typography;

export const FINAL_DIAGNOSIS_SOURCE_LABELS: Record<string, string> = {
  pathology: '病理',
  followup: '随访',
  expert_consensus: '专家共识',
};

export interface QcBackfillPanelProps {
  /** 报告状态（draft / signed / pushed，后端字符串枚举） */
  reportStatus: string | null;
  /** 金标准最终诊断（自由文本） */
  finalDiagnosis?: string;
  /** 金标准来源（pathology / followup / expert_consensus） */
  finalDiagnosisSource?: string;
  /** 回填时间（后端 ISO 串，原样展示） */
  finalDiagnosisAt?: string;
  /** 患者沟通话术（M10；非正式报告内容） */
  patientExplanation?: string;
}

export default function QcBackfillPanel({
  reportStatus,
  finalDiagnosis,
  finalDiagnosisSource,
  finalDiagnosisAt,
  patientExplanation,
}: QcBackfillPanelProps) {
  // 草稿期不展示金标准（锚定偏倚），只展示话术；两者都没有则整块不渲染
  const showFinalDiagnosis = Boolean(finalDiagnosis) && reportStatus !== 'draft' && reportStatus !== null;
  const showExplanation = Boolean(patientExplanation);
  if (!showFinalDiagnosis && !showExplanation) return null;

  return (
    <Card
      size="small"
      style={{ marginTop: tokens.spacing.md }}
      data-region="qc-backfill"
      title="质控回填与沟通材料"
    >
      {showFinalDiagnosis && (
        <div data-region="final-diagnosis" style={{ marginBottom: showExplanation ? tokens.spacing.md : 0 }}>
          <Text strong>最终诊断（金标准）：</Text>
          <Text>{finalDiagnosis}</Text>
          {finalDiagnosisSource && (
            <Tag style={{ marginLeft: tokens.spacing.sm }} color="blue">
              {FINAL_DIAGNOSIS_SOURCE_LABELS[finalDiagnosisSource] ?? finalDiagnosisSource}
            </Tag>
          )}
          {finalDiagnosisAt && (
            <Text type="secondary" style={{ marginLeft: tokens.spacing.sm }}>
              {finalDiagnosisAt}
            </Text>
          )}
          <Paragraph type="secondary" style={{ marginTop: tokens.spacing.xs, marginBottom: 0, fontSize: 12 }}>
            金标准为事后回填（病理/随访/专家共识），仅在报告定稿后展示，不参与本次判读。
          </Paragraph>
        </div>
      )}

      {showExplanation && (
        <div data-region="patient-explanation">
          <Text strong>患者沟通话术：</Text>
          <Paragraph style={{ marginTop: tokens.spacing.xs, marginBottom: 0, whiteSpace: 'pre-wrap' }}>
            {patientExplanation}
          </Paragraph>
          <Paragraph type="secondary" style={{ marginBottom: 0, fontSize: 12 }}>
            此段由模型生成，**不属于正式报告内容**，不得打印进报告；使用前请医生按患者情况改写。
          </Paragraph>
        </div>
      )}
    </Card>
  );
}
