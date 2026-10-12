import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Empty, Input, Segmented, Space, Spin, Tag, Typography } from 'antd';
import { HistoryOutlined, SwapOutlined } from '@ant-design/icons';
import { getApiErrorMessage } from '@/utils/apiError';
import { fetchPatientHistory } from '@/services/patientHistoryService';
import { fetchDicomForDisplay } from '@/services/imagingService';
import WorkspaceDicomLayer from '@/components/workspace/WorkspaceDicomLayer';
import type { PatientHistoryItem, PatientHistoryResponse } from '@/types/patientHistory';
import { tokens } from '@/styles/tokens';

const { Title, Text, Paragraph } = Typography;

const ORGAN_LABELS: Record<string, string> = {
  thyroid: '甲状腺',
  liver: '肝脏',
  cardiac: '心脏',
};

const REPORT_STATUS_LABELS: Record<string, { label: string; color: string }> = {
  draft: { label: '草稿', color: tokens.color.accent.warning },
  signed: { label: '已签发', color: tokens.color.accent.success },
  pushed: { label: '已推送', color: tokens.color.accent.info },
  failed: { label: '推送失败', color: tokens.color.accent.danger },
};

function organLabel(organ?: string | null): string {
  if (!organ) return '未标注部位';
  return ORGAN_LABELS[organ] ?? organ;
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 分割面积变化（右侧相对左侧）：只在两侧都有面积时给数字，否则如实说清为什么没有。 */
export function areaDeltaLabel(
  left: PatientHistoryItem,
  right: PatientHistoryItem,
): { text: string; color: string } {
  const a = left.segmentation?.area_mm2;
  const b = right.segmentation?.area_mm2;
  if (typeof a !== 'number' || typeof b !== 'number') {
    if (!left.segmentation?.available && left.segmentation?.reason) {
      return { text: `上次未做分割（${left.segmentation.reason}）`, color: tokens.color.text.secondary };
    }
    if (!right.segmentation?.available && right.segmentation?.reason) {
      return { text: `本次未做分割（${right.segmentation.reason}）`, color: tokens.color.text.secondary };
    }
    return { text: '面积不可比（有一侧无分割结果）', color: tokens.color.text.secondary };
  }
  if (a === 0) return { text: '上次面积为 0，无法算变化率', color: tokens.color.text.secondary };
  const delta = ((b - a) / a) * 100;
  const sign = delta > 0 ? '+' : '';
  const color =
    Math.abs(delta) < 5
      ? tokens.color.text.secondary
      : delta > 0
        ? tokens.color.accent.danger
        : tokens.color.accent.success;
  return { text: `${sign}${delta.toFixed(1)}%（${a.toFixed(1)} → ${b.toFixed(1)} mm²）`, color };
}

export default function PatientHistoryPage() {
  const [searchParams] = useSearchParams();
  const initialPatientId = searchParams.get('patient_id') ?? '';
  const [patientId, setPatientId] = useState(initialPatientId);
  const [query, setQuery] = useState(initialPatientId);

  const [phase, setPhase] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [data, setData] = useState<PatientHistoryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [organ, setOrgan] = useState<string | undefined>(undefined);

  /** 左侧 = 较早一次，右侧 = 较近一次（阅读顺序与"前后对比"一致）。 */
  const [leftSid, setLeftSid] = useState<string | null>(null);
  const [rightSid, setRightSid] = useState<string | null>(null);

  const load = useCallback(async (pid: string, organFilter?: string) => {
    if (!pid) return;
    setPhase('loading');
    setError(null);
    try {
      const resp = await fetchPatientHistory(pid, { limit: 20, organ: organFilter });
      setData(resp);
      setPhase('ready');
    } catch (e) {
      setError(getApiErrorMessage(e, '历史检查读取失败'));
      setPhase('error');
    }
  }, []);

  useEffect(() => {
    if (patientId) void load(patientId, organ);
  }, [patientId, organ, load]);

  /**
   * 默认选中「最近两次同器官检查」。
   *
   * 为什么必须是**同器官**：甲状腺结节面积与心脏结构面积之间没有可比性，
   * 拿不同器官并排会让医生误读为"病灶变化"。只有 comparable_organs 里的器官
   * 才允许自动配对；否则退化为"最近两次"，并在界面上明确提示器官不同。
   */
  useEffect(() => {
    if (!data || data.items.length === 0) {
      setLeftSid(null);
      setRightSid(null);
      return;
    }
    const targetOrgan = organ ?? data.comparable_organs[0];
    const same = data.items.filter((it) => it.organ === targetOrgan);
    if (targetOrgan && same.length >= 2) {
      setRightSid(same[0].session_id);
      setLeftSid(same[1].session_id);
      return;
    }
    setRightSid(data.items[0].session_id);
    setLeftSid(data.items[1]?.session_id ?? null);
  }, [data, organ]);

  const left = useMemo(
    () => data?.items.find((it) => it.session_id === leftSid) ?? null,
    [data, leftSid],
  );
  const right = useMemo(
    () => data?.items.find((it) => it.session_id === rightSid) ?? null,
    [data, rightSid],
  );

  // 影像并排：两侧各取自己那次检查的代表帧（服务端副本 → 已验证的显示路径）。
  // 三态显式区分（A.54 真机走查抓到的坑）：**不能**把"取字节失败"与"正在加载"
  // 混成一句「加载中/不可用」——前者医生该重试/报障，后者只需等待。
  const [localImages, setLocalImages] = useState<Record<string, string | null>>({});
  const [imageErrors, setImageErrors] = useState<Record<string, boolean>>({});
  useEffect(() => {
    let alive = true;
    const loadOne = async (imageId: string) => {
      if (localImages[imageId] !== undefined) return;
      try {
        const localId = await fetchDicomForDisplay(imageId);
        if (alive) setLocalImages((prev) => ({ ...prev, [imageId]: localId }));
      } catch {
        if (alive) {
          setLocalImages((prev) => ({ ...prev, [imageId]: null }));
          setImageErrors((prev) => ({ ...prev, [imageId]: true }));
        }
      }
    };
    for (const id of [left?.first_image_id, right?.first_image_id]) {
      if (id) void loadOne(id);
    }
    return () => {
      alive = false;
    };
  }, [left?.first_image_id, right?.first_image_id, localImages]);

  const delta = left && right ? areaDeltaLabel(left, right) : null;
  const organMismatch = Boolean(left && right && left.organ && right.organ && left.organ !== right.organ);

  const renderColumn = (item: PatientHistoryItem | null, side: 'left' | 'right') => {
    if (!item) {
      return (
        <Card size="small" style={{ flex: 1 }} data-region={`history-col-${side}-empty`}>
          <Empty description="请选择要对比的检查" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        </Card>
      );
    }
    const seg = item.segmentation;
    const localId = item.first_image_id ? localImages[item.first_image_id] : null;
    return (
      <Card
        size="small"
        style={{ flex: 1 }}
        data-region={`history-col-${side}`}
        title={
          <Space size={8}>
            <Text strong>{fmtTime(item.created_at)}</Text>
            <Tag>{organLabel(item.organ)}</Tag>
          </Space>
        }
      >
        <Space direction="vertical" size={6} style={{ width: '100%' }}>
          {/* 容器必须有**尺寸 + 定位**：DICOM 层自身是 `position:absolute; inset:0`，
              放进无高度的普通 div 会塌成 0×0（canvas 不渲染）——A.54 真机走查实测。 */}
          <div data-region="history-image" style={{ position: 'relative', height: 220, width: '100%' }}>
            {localId ? (
              <WorkspaceDicomLayer imageIds={[localId]} />
            ) : (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {!item.first_image_id
                  ? '该次检查无影像'
                  : imageErrors[item.first_image_id]
                    ? '影像加载失败（服务端副本不可用，可点「刷新」重试）'
                    : '影像加载中…'}
              </Text>
            )}
          </div>
          <div>
            <Text type="secondary" style={{ fontSize: 12 }}>
              结论
            </Text>
            <Paragraph style={{ marginBottom: 4, whiteSpace: 'pre-wrap' }}>
              {item.conclusion ?? '（该次检查尚无结论）'}
            </Paragraph>
          </div>
          <Space size={12} wrap>
            <Text style={{ fontSize: 12 }}>
              置信度：{typeof item.confidence === 'number' ? `${(item.confidence * 100).toFixed(0)}%` : '—'}
            </Text>
            <Text style={{ fontSize: 12 }}>
              结节面积：
              {typeof seg?.area_mm2 === 'number'
                ? `${seg.area_mm2.toFixed(1)} mm²`
                : seg && !seg.available
                  ? `未做分割（${seg.reason ?? '未提供原因'}）`
                  : '—'}
            </Text>
            {seg?.frame_index != null && seg?.total_frames != null && (
              <Text style={{ fontSize: 12 }} type="secondary">
                取第 {seg.frame_index + 1}/{seg.total_frames} 帧
              </Text>
            )}
          </Space>
          <Space size={8} wrap>
            {item.report_id ? (
              <Tag color={REPORT_STATUS_LABELS[item.report_status ?? '']?.color}>
                报告{REPORT_STATUS_LABELS[item.report_status ?? '']?.label ?? item.report_status}
              </Tag>
            ) : (
              <Tag>尚无报告</Tag>
            )}
            <Tag>影像 {item.image_count ?? 0} 张</Tag>
            {item.knowledge_base_version && <Tag>{item.knowledge_base_version}</Tag>}
          </Space>
        </Space>
      </Card>
    );
  };

  if (!initialPatientId && !patientId) {
    return (
      <section data-region="history-missing-patient" style={{ padding: 16 }}>
        <Title level={4}>
          <HistoryOutlined /> 历史检查对比
        </Title>
        <Paragraph type="secondary">请输入患者标识（与报告页一致：标识来自建会话时录入的患者编号）。</Paragraph>
        <Space.Compact style={{ width: 480 }}>
          <Input
            aria-label="患者标识"
            placeholder="患者标识"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onPressEnter={() => setPatientId(query.trim())}
          />
          <Button type="primary" onClick={() => setPatientId(query.trim())}>
            查看历史
          </Button>
        </Space.Compact>
      </section>
    );
  }

  return (
    <section data-region="history-root" style={{ padding: 16 }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} align="center">
        <Title level={4} style={{ margin: 0 }}>
          <HistoryOutlined /> 历史检查对比
          <Text type="secondary" style={{ fontSize: 13, marginLeft: 8 }}>
            患者 {patientId}
          </Text>
        </Title>
        <Space>
          {data && data.organs.length > 1 && (
            <Segmented
              aria-label="按部位筛选"
              value={organ ?? 'all'}
              onChange={(v) => setOrgan(v === 'all' ? undefined : String(v))}
              options={[
                { label: '全部', value: 'all' },
                ...data.organs.map((o) => ({ label: organLabel(o), value: o })),
              ]}
            />
          )}
          <Button size="small" onClick={() => void load(patientId, organ)}>
            刷新
          </Button>
        </Space>
      </Space>

      {phase === 'loading' && (
        <div style={{ padding: 24, textAlign: 'center' }}>
          <Spin />
        </div>
      )}
      {phase === 'error' && (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 12 }}
          message="历史检查读取失败"
          description={error}
          data-region="history-error"
        />
      )}

      {phase === 'ready' && data && (
        <>
          {!data.patient_exists ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 12 }}
              data-region="history-not-exist"
              message="系统里没有这个患者"
              description="请确认患者标识是否正确；若确为新患者，请在建会话页录入并建档（同意签署后方可检查）。"
            />
          ) : data.total === 0 ? (
            <Alert
              type="info"
              showIcon
              style={{ marginTop: 12 }}
              data-region="history-empty"
              message="该患者暂无检查记录"
              description="完成一次检查后，这里会显示可前后对比的历史。"
            />
          ) : data.total === 1 ? (
            <Card style={{ marginTop: 12 }} data-region="history-single" title="只有一次检查，暂无可对比对象">
              {renderColumn(data.items[0], 'right')}
            </Card>
          ) : (
            <>
              <Card
                size="small"
                style={{ marginTop: 12 }}
                data-region="history-timeline"
                title={`共 ${data.total} 次检查`}
                extra={
                  <Space size={4}>
                    {data.items.map((it) => (
                      <Button
                        key={it.session_id}
                        size="small"
                        type={it.session_id === leftSid || it.session_id === rightSid ? 'primary' : 'default'}
                        onClick={() => {
                          // 点时间线：交替填左右（先左后右），让"选两次"不依赖下拉
                          if (leftSid === it.session_id || rightSid === it.session_id) return;
                          if (!leftSid) setLeftSid(it.session_id);
                          else setRightSid(it.session_id);
                        }}
                        data-region="history-timeline-item"
                      >
                        {fmtTime(it.created_at).slice(5, 16)} · {organLabel(it.organ)}
                      </Button>
                    ))}
                  </Space>
                }
              >
                <Text type="secondary" style={{ fontSize: 12 }}>
                  可对比部位：{data.comparable_organs.length > 0
                    ? data.comparable_organs.map(organLabel).join('、')
                    : '无（同一部位只有一次检查）'}
                </Text>
              </Card>

              {organMismatch && (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginTop: 12 }}
                  data-region="history-organ-mismatch"
                  message={`两次检查部位不同（${organLabel(left?.organ)} vs ${organLabel(right?.organ)}），不做直接对比`}
                  description="不同部位的测值没有可比性。请在上方筛选同一部位，或从时间线选择同部位的另一位检查。"
                />
              )}

              <div style={{ display: 'flex', gap: 12, marginTop: 12, alignItems: 'stretch' }}>
                {renderColumn(left, 'left')}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <SwapOutlined style={{ fontSize: 20, color: tokens.color.text.secondary }} />
                </div>
                {renderColumn(right, 'right')}
              </div>

              {/* 面积变化只在**同器官**时显示（A.54 真机走查抓到：跨器官也给了
                  "-23.2%" —— 甲状腺结节面积与心脏结构面积之间没有可比性，
                  给数字等于制造伪"病情变化"，宁可不说）。 */}
              {delta && !organMismatch && (
                <Card size="small" style={{ marginTop: 12 }} data-region="history-delta-area">
                  <Space size={8}>
                    <Text strong>结节面积变化：</Text>
                    <Text style={{ color: delta.color }}>{delta.text}</Text>
                  </Space>
                </Card>
              )}
              {delta && organMismatch && (
                <Card size="small" style={{ marginTop: 12 }} data-region="history-delta-suppressed">
                  <Text type="secondary">
                    两次检查部位不同，本次**不给出测值变化**（跨部位对比没有临床意义）。
                  </Text>
                </Card>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}
