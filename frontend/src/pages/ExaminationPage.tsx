/**
 * ⚠️ **已无路由的遗留页面（T7 登记为「待处置」，未删除）**
 *
 * 检查主界面已由工作区（`/` → `WorkspaceRoute` → `WorkspacePage`）承载，
 * 本文件在 `routes.tsx` 里**没有任何 path 指向它**。
 *
 * 为什么保留而不删（AGENTS.md：不删既有死代码，要删先说明）：
 *   1. `pages/__tests__/DiagnosisFlowWiring.test.ts` 仍以本文件为断言对象之一
 *      （「提交成功后把 request_id 带进结果页」的源码级回归守卫），
 *      删文件就等于删掉那条回归腿；
 *   2. 本文件是**唯一**演示 `useSession` + DICOM 上传 + 单次判读提交三者直连的
 *      最小完整例子，排查"上传→提交"链路时可当对照实现；
 *   3. ⚠️ 注意：它仍 `navigate(\`/diagnosis?request_id=\`)`，而该路由已删 ——
 *      也就是**若将来恢复本页路由，这条跳转会立刻变成死链**。
 *      保留它反而把这个问题留在明面上（删文件会让隐患一起消失、无从提醒）。
 * 处置建议：与 `DiagnosisPage.tsx` 一并处置；恢复路由前必须先改掉这条跳转。
 */
import { useState, useCallback, useRef, useEffect } from 'react';
import { Button, Card, Space, Typography, Upload, message, Select } from 'antd';
import { UploadOutlined, ApiOutlined } from '@ant-design/icons';
import DicomViewer from '@/components/dicom/DicomViewer';
import NavigationOverlay from '@/components/navigation/NavigationOverlay';
import { useNavigationAnnotations } from '@/hooks/useNavigationAnnotations';
import { useSession } from '@/hooks/useSession';
import {
  requestDiagnosis,
  DEFAULT_RESPONSE_TIER,
} from '@/services/diagnosisService';
import { useNavigate } from 'react-router-dom';
import { generateIdempotencyKey } from '@/utils/idGenerator';
import { getApiErrorMessage } from '@/utils/apiError';
import { wadouri } from '@/components/dicom/cornerstone';

const { Title, Text } = Typography;

export default function ExaminationPage() {
  const [imageId, setImageId] = useState<string | undefined>();
  const [diagnosisLoading, setDiagnosisLoading] = useState(false);
  const [selectedOrgan, setSelectedOrgan] = useState<string>('thyroid');
  // 02-3 方案B：医生可选校验档位；默认仍是 standard（默认行为不静默改变）
  const [responseTier, setResponseTier] = useState<'standard' | 'full'>(DEFAULT_RESPONSE_TIER);
  const objectUrlRef = useRef<string | null>(null);
  const { sessionId, status, targetOrgans } = useSession();
  useNavigationAnnotations();

  const handleFileUpload = useCallback((file: File) => {
    // Cornerstone3D：File 直接注册进 wadouri fileManager，返回可加载的 imageId
    //（旧 wado:uri?url=... 是 cornerstone-wado-image-loader 4.x 专属 scheme）。
    const dicomImageId = wadouri.fileManager.add(file);
    setImageId(dicomImageId);
    return false;
  }, []);

  useEffect(() => {
    return () => {
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
    };
  }, []);

  const navigate = useNavigate();

  const handleRequestDiagnosis = async () => {
    if (!sessionId) {
      message.warning('请先创建检查会话');
      return;
    }
    setDiagnosisLoading(true);
    try {
      const idempotencyKey = generateIdempotencyKey(selectedOrgan);
      const submitted = await requestDiagnosis({
        session_id: sessionId,
        image_ids: imageId ? [imageId] : [],
        target_organ: selectedOrgan as 'thyroid' | 'liver' | 'cardiac',
        response_tier: responseTier,
        idempotency_key: idempotencyKey,
      });
      message.success('诊断请求已提交，正在跳转查看结果');
      // 关键：把 request_id 交给结果页。此前提交成功后**既不跳转也不保存 id**，
      // 医生停留在本页只看到"已提交"，而结果页无从知道该取哪一次 → 永远看不到 AI 结论。
      navigate(`/diagnosis?request_id=${encodeURIComponent(submitted.request_id)}`);
    } catch (err: unknown) {
      message.error(getApiErrorMessage(err, '诊断请求失败'));
    } finally {
      setDiagnosisLoading(false);
    }
  };

  const organOptions = targetOrgans.length > 0
    ? targetOrgans.map((o) => ({ value: o, label: o === 'thyroid' ? '甲状腺' : o === 'liver' ? '肝脏' : o === 'cardiac' ? '心脏' : o }))
    : [
        { value: 'thyroid', label: '甲状腺' },
        { value: 'liver', label: '肝脏' },
        { value: 'cardiac', label: '心脏' },
      ];

  return (
    <div className="h-full flex gap-4 animate-fade-in" style={{ background: '#0a0e1a' }}>
      <div className="flex-1 relative" style={{ minHeight: 0 }}>
        <DicomViewer imageId={imageId} />
        {imageId && <NavigationOverlay />}
      </div>
      <div className="flex flex-col gap-3" style={{ width: 280, flexShrink: 0 }}>
        <Card size="small" style={{ background: '#111827', border: '1px solid #1e293b' }}>
          <Title level={5} style={{ fontFamily: "'Orbitron', sans-serif", color: '#00d4ff', fontSize: 12, letterSpacing: '0.05em', marginBottom: 12 }}>
            IMAGING
          </Title>
          <Upload accept=".dcm,.dicom" showUploadList={false} beforeUpload={handleFileUpload}>
            <Button icon={<UploadOutlined />} size="small" style={{ width: '100%' }}>加载DICOM文件</Button>
          </Upload>
        </Card>
        <Card size="small" style={{ background: '#111827', border: '1px solid #1e293b' }}>
          <Title level={5} style={{ fontFamily: "'Orbitron', sans-serif", color: '#7c3aed', fontSize: 12, letterSpacing: '0.05em', marginBottom: 12 }}>
            SESSION
          </Title>
          <Space vertical size={4} style={{ width: '100%' }}>
            <div className="flex justify-between">
              <Text style={{ color: '#64748b', fontSize: 11 }}>状态</Text>
              <Text style={{ color: status === 'active' ? '#10b981' : '#94a3b8', fontSize: 11 }}>{status || '未创建'}</Text>
            </div>
            <div className="flex justify-between">
              <Text style={{ color: '#64748b', fontSize: 11 }}>会话ID</Text>
              <Text style={{ color: '#94a3b8', fontSize: 11, fontFamily: "'JetBrains Mono', monospace" }}>{sessionId ? sessionId.slice(0, 8) + '...' : '--'}</Text>
            </div>
          </Space>
        </Card>
        <Card size="small" style={{ background: '#111827', border: '1px solid #1e293b' }}>
          <Title level={5} style={{ fontFamily: "'Orbitron', sans-serif", color: '#f59e0b', fontSize: 12, letterSpacing: '0.05em', marginBottom: 12 }}>
            AI DIAGNOSIS
          </Title>
          <Select
            value={selectedOrgan}
            onChange={setSelectedOrgan}
            options={organOptions}
            size="small"
            style={{ width: '100%', marginBottom: 8 }}
          />
          <Select
            value={responseTier}
            onChange={setResponseTier}
            options={[
              { value: 'standard', label: '标准校验' },
              { value: 'full', label: '全量校验（慢约 1s）' },
            ]}
            size="small"
            style={{ width: '100%', marginBottom: 8 }}
          />
          <Button
            icon={<ApiOutlined />}
            size="small"
            disabled={!sessionId}
            loading={diagnosisLoading}
            onClick={handleRequestDiagnosis}
            style={{ width: '100%' }}
          >
            请求诊断
          </Button>
          <Text style={{ color: '#64748b', fontSize: 10, display: 'block', marginTop: 8 }}>需要先创建检查会话</Text>
        </Card>
      </div>
    </div>
  );
}
