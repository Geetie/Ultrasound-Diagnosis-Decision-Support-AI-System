import { useState, useCallback, useRef, useEffect } from 'react';
import { Button, Card, Space, Typography, Upload, message, Select } from 'antd';
import { UploadOutlined, ApiOutlined } from '@ant-design/icons';
import DicomViewer from '@/components/dicom/DicomViewer';
import NavigationOverlay from '@/components/navigation/NavigationOverlay';
import { useNavigationAnnotations } from '@/hooks/useNavigationAnnotations';
import { useSession } from '@/hooks/useSession';
import { requestDiagnosis } from '@/services/diagnosisService';
import { useNavigate } from 'react-router-dom';
import { generateIdempotencyKey } from '@/utils/idGenerator';
import { getApiErrorMessage } from '@/utils/apiError';

const { Title, Text } = Typography;

export default function ExaminationPage() {
  const [imageId, setImageId] = useState<string | undefined>();
  const [diagnosisLoading, setDiagnosisLoading] = useState(false);
  const [selectedOrgan, setSelectedOrgan] = useState<string>('thyroid');
  const objectUrlRef = useRef<string | null>(null);
  const { sessionId, status, targetOrgans } = useSession();
  useNavigationAnnotations();

  const handleFileUpload = useCallback((file: File) => {
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
    }
    const url = URL.createObjectURL(file);
    objectUrlRef.current = url;
    const dicomImageId = `wado:uri?url=${encodeURIComponent(url)}&type=DICOM`;
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
        response_tier: 'standard',
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
