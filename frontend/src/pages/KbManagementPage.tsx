import { useState } from 'react';
import { Card, Table, Tag, Button, Typography, Badge, Timeline, Modal, Statistic } from 'antd';
import {
  DatabaseOutlined,
  HistoryOutlined,
  BellOutlined,
  EyeOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons';
import { tokens } from '@/styles/tokens';

const { Title, Text } = Typography;

const mockRole: 'admin' | 'operator' = 'admin';

const versionColumns = [
  { title: '版本号', dataIndex: 'version', key: 'version', render: (v: string) => <Text style={{ fontFamily: tokens.font.mono, color: tokens.color.accent.primary }}>{v}</Text> },
  { title: '发布日期', dataIndex: 'releaseDate', key: 'releaseDate' },
  { title: '状态', dataIndex: 'status', key: 'status', render: (v: string) => {
    const map: Record<string, { color: string; label: string }> = {
      active: { color: tokens.color.accent.success, label: '当前版本' },
      archived: { color: tokens.color.text.muted, label: '已归档' },
      pending: { color: tokens.color.accent.warning, label: '待审核' },
    };
    const item = map[v] || map.archived;
    return <Tag color={v === 'active' ? 'success' : v === 'pending' ? 'warning' : 'default'}>{item.label}</Tag>;
  }},
  { title: '规则数量', dataIndex: 'ruleCount', key: 'ruleCount' },
  { title: '审核人', dataIndex: 'reviewer', key: 'reviewer' },
  {
    title: '操作',
    key: 'action',
    render: (_: unknown, record: KbVersionRecord) => (
      <Button
        type="link"
        size="small"
        icon={<EyeOutlined />}
        onClick={() => showChangeDetail(record)}
      >
        变更记录
      </Button>
    ),
  },
];

interface KbVersionRecord {
  key: string;
  version: string;
  releaseDate: string;
  status: string;
  ruleCount: number;
  reviewer: string;
}

const versionData: KbVersionRecord[] = [
  { key: '1', version: 'v3.2.1', releaseDate: '2026-05-10', status: 'active', ruleCount: 12847, reviewer: '张主任' },
  { key: '2', version: 'v3.2.0', releaseDate: '2026-04-22', status: 'archived', ruleCount: 12653, reviewer: '张主任' },
  { key: '3', version: 'v3.1.9', releaseDate: '2026-04-05', status: 'archived', ruleCount: 12401, reviewer: '李主任' },
  { key: '4', version: 'v3.1.8', releaseDate: '2026-03-18', status: 'archived', ruleCount: 12189, reviewer: '李主任' },
  { key: '5', version: 'v3.3.0-beta', releaseDate: '--', status: 'pending', ruleCount: 13102, reviewer: '待审核' },
];

const changeRecords: Record<string, { date: string; author: string; changes: string[] }[]> = {
  'v3.2.1': [
    { date: '2026-05-10', author: '王医生', changes: ['更新甲状腺TI-RADS分级规则', '修正肝脏局灶性病变描述术语'] },
    { date: '2026-05-08', author: '赵医生', changes: ['新增心脏瓣膜反流评估标准'] },
  ],
  'v3.2.0': [
    { date: '2026-04-22', author: '张主任', changes: ['重构甲状腺诊断知识图谱', '新增乳腺BI-RADS分级规则'] },
  ],
  'v3.1.9': [
    { date: '2026-04-05', author: '李主任', changes: ['修正血管狭窄程度判定阈值', '更新肝脏弹性评分标准'] },
  ],
  'v3.1.8': [
    { date: '2026-03-18', author: '李主任', changes: ['新增肾脏囊肿Bosniak分级', '优化心脏射血分数计算逻辑'] },
  ],
  'v3.3.0-beta': [
    { date: '2026-05-12', author: '王医生', changes: ['新增AI辅助淋巴结评估模块', '更新多器官联合诊断规则'] },
    { date: '2026-05-11', author: '赵医生', changes: ['优化超声伪影识别算法参数'] },
  ],
};

const reviewNotifications = [
  { id: '1', version: 'v3.3.0-beta', type: 'urgent' as const, message: 'v3.3.0-beta 版本待审核，包含13,102条规则变更', time: '2小时前' },
  { id: '2', version: 'v3.2.1', type: 'info' as const, message: 'v3.2.1 版本已通过审核并上线', time: '5天前' },
  { id: '3', version: 'v3.2.0', type: 'success' as const, message: 'v3.2.0 版本归档完成', time: '3周前' },
];

const showChangeDetail = (record: KbVersionRecord) => {
  const records = changeRecords[record.version] || [];
  Modal.info({
    title: (
      <span style={{ fontFamily: tokens.font.display, color: tokens.color.accent.primary, letterSpacing: '0.05em' }}>
        {record.version} 变更记录
      </span>
    ),
    width: 560,
    styles: {
      body: { background: tokens.color.bg.elevated },
      header: { background: tokens.color.bg.elevated },
    },
    content: (
      <Timeline
        style={{ marginTop: 16 }}
        items={records.map((r) => ({
          color: 'blue',
          children: (
            <div>
              <div style={{ marginBottom: 4 }}>
                <Text style={{ color: tokens.color.text.secondary, fontSize: 12 }}>{r.date}</Text>
                <Text style={{ color: tokens.color.accent.primary, fontSize: 12, marginLeft: 12 }}>{r.author}</Text>
              </div>
              {r.changes.map((c, i) => (
                <div key={i} style={{ paddingLeft: 8, borderLeft: `2px solid ${tokens.color.border.accent}`, marginBottom: 4 }}>
                  <Text style={{ color: tokens.color.text.primary, fontSize: 13 }}>{c}</Text>
                </div>
              ))}
            </div>
          ),
        }))}
      />
    ),
  });
};

export default function KbManagementPage() {
  const [activeTab, setActiveTab] = useState<'versions' | 'notifications'>('versions');

  return (
    <div className="h-full overflow-auto animate-fade-in" style={{ padding: '0 32px 32px' }}>
      <div className="flex items-center justify-between" style={{ marginBottom: 24 }}>
        <div className="flex items-center gap-3">
          <Title
            level={4}
            style={{
              fontFamily: tokens.font.display,
              color: tokens.color.accent.primary,
              letterSpacing: '0.05em',
              margin: 0,
            }}
          >
            KB MANAGEMENT
          </Title>
          {mockRole === 'admin' && (
            <Badge
              count="管理员"
              style={{
                backgroundColor: tokens.color.accent.secondary,
                fontFamily: tokens.font.body,
                fontSize: 11,
              }}
            />
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="small"
            type={activeTab === 'versions' ? 'primary' : 'default'}
            icon={<DatabaseOutlined />}
            onClick={() => setActiveTab('versions')}
          >
            版本列表
          </Button>
          <Button
            size="small"
            type={activeTab === 'notifications' ? 'primary' : 'default'}
            icon={<BellOutlined />}
            onClick={() => setActiveTab('notifications')}
          >
            审核通知
            {reviewNotifications.filter((n) => n.type === 'urgent').length > 0 && (
              <Badge
                dot
                style={{ marginLeft: 4 }}
              />
            )}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4" style={{ marginBottom: 24 }}>
        <Card style={{ background: tokens.color.bg.secondary, border: `1px solid ${tokens.color.border.default}` }}>
          <Statistic
            title={<span style={{ color: tokens.color.text.muted, fontSize: 12 }}>当前版本</span>}
            value="v3.2.1"
            valueStyle={{ color: tokens.color.accent.primary, fontSize: 22, fontFamily: tokens.font.mono }}
            prefix={<DatabaseOutlined />}
          />
        </Card>
        <Card style={{ background: tokens.color.bg.secondary, border: `1px solid ${tokens.color.border.default}` }}>
          <Statistic
            title={<span style={{ color: tokens.color.text.muted, fontSize: 12 }}>规则总数</span>}
            value={12847}
            valueStyle={{ color: tokens.color.accent.success, fontSize: 22, fontFamily: tokens.font.mono }}
            prefix={<CheckCircleOutlined />}
          />
        </Card>
        <Card style={{ background: tokens.color.bg.secondary, border: `1px solid ${tokens.color.border.default}` }}>
          <Statistic
            title={<span style={{ color: tokens.color.text.muted, fontSize: 12 }}>待审核</span>}
            value={1}
            valueStyle={{ color: tokens.color.accent.warning, fontSize: 22, fontFamily: tokens.font.mono }}
            prefix={<ClockCircleOutlined />}
          />
        </Card>
      </div>

      {activeTab === 'versions' && (
        <Card
          title={
            <span style={{ color: tokens.color.text.primary, fontFamily: tokens.font.display, fontSize: 13, letterSpacing: '0.05em' }}>
              <HistoryOutlined style={{ marginRight: 8, color: tokens.color.accent.secondary }} />
              VERSION HISTORY
            </span>
          }
          style={{ background: tokens.color.bg.secondary, border: `1px solid ${tokens.color.border.default}` }}
        >
          <Table
            columns={versionColumns}
            dataSource={versionData}
            pagination={false}
            size="small"
          />
        </Card>
      )}

      {activeTab === 'notifications' && (
        <Card
          title={
            <span style={{ color: tokens.color.text.primary, fontFamily: tokens.font.display, fontSize: 13, letterSpacing: '0.05em' }}>
              <BellOutlined style={{ marginRight: 8, color: tokens.color.accent.warning }} />
              REVIEW NOTIFICATIONS
            </span>
          }
          style={{ background: tokens.color.bg.secondary, border: `1px solid ${tokens.color.border.default}` }}
        >
          <Timeline
            items={reviewNotifications.map((n) => {
              const iconMap = {
                urgent: <ExclamationCircleOutlined style={{ color: tokens.color.accent.danger, fontSize: 16 }} />,
                info: <ClockCircleOutlined style={{ color: tokens.color.accent.info, fontSize: 16 }} />,
                success: <CheckCircleOutlined style={{ color: tokens.color.accent.success, fontSize: 16 }} />,
              };
              const colorMap = {
                urgent: 'red' as const,
                info: 'blue' as const,
                success: 'green' as const,
              };
              return {
                color: colorMap[n.type],
                dot: iconMap[n.type],
                children: (
                  <div className="flex items-start justify-between">
                    <div>
                      <Text style={{ color: tokens.color.text.primary, fontSize: 13 }}>{n.message}</Text>
                      <div style={{ marginTop: 4 }}>
                        <Text style={{ color: tokens.color.text.muted, fontSize: 11 }}>{n.time}</Text>
                      </div>
                    </div>
                    {n.type === 'urgent' && mockRole === 'admin' && (
                      <Button size="small" type="primary">
                        去审核
                      </Button>
                    )}
                  </div>
                ),
              };
            })}
          />
        </Card>
      )}
    </div>
  );
}
