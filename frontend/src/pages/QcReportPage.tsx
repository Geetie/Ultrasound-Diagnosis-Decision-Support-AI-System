import { useState } from 'react';
import { Card, Table, Tag, Button, Typography, Badge, Statistic, Timeline } from 'antd';
import {
  AlertOutlined,
  CheckCircleOutlined,
  RiseOutlined,
  TeamOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import { tokens } from '@/styles/tokens';

const { Title, Text } = Typography;

const mockRole: 'director' | 'operator' = 'director';

const kpiData = [
  { title: '诊断总量', value: 12846, suffix: '例', icon: <SafetyCertificateOutlined />, color: tokens.color.accent.primary },
  { title: 'AI准确率', value: 94.7, suffix: '%', icon: <CheckCircleOutlined />, color: tokens.color.accent.success },
  { title: '人工修正率', value: 5.3, suffix: '%', icon: <AlertOutlined />, color: tokens.color.accent.warning },
  { title: '质控覆盖率', value: 98.2, suffix: '%', icon: <TeamOutlined />, color: tokens.color.accent.secondary },
];

interface DepartmentRow {
  department: string;
  total: number;
  accuracy: number;
  correctionRate: number;
  status: string;
}

const departmentColumns = [
  { title: '科室', dataIndex: 'department', key: 'department' },
  { title: '诊断总量', dataIndex: 'total', key: 'total', sorter: (a: DepartmentRow, b: DepartmentRow) => a.total - b.total },
  { title: 'AI准确率', dataIndex: 'accuracy', key: 'accuracy', render: (v: number) => <span style={{ color: v >= 95 ? tokens.color.accent.success : tokens.color.accent.warning }}>{v}%</span> },
  { title: '修正率', dataIndex: 'correctionRate', key: 'correctionRate', render: (v: number) => <span style={{ color: v > 8 ? tokens.color.accent.danger : tokens.color.text.secondary }}>{v}%</span> },
  { title: '状态', dataIndex: 'status', key: 'status', render: (v: string) => {
    const map: Record<string, { color: string; label: string }> = {
      normal: { color: tokens.color.accent.success, label: '正常' },
      warning: { color: tokens.color.accent.warning, label: '关注' },
      alert: { color: tokens.color.accent.danger, label: '预警' },
    };
    const item = map[v] || map.normal;
    return <Tag color={item.color === tokens.color.accent.success ? 'success' : item.color === tokens.color.accent.warning ? 'warning' : 'error'}>{item.label}</Tag>;
  }},
];

const departmentData = [
  { key: '1', department: '甲状腺外科', total: 3241, accuracy: 96.2, correctionRate: 3.8, status: 'normal' },
  { key: '2', department: '肝胆外科', total: 2876, accuracy: 93.1, correctionRate: 6.9, status: 'warning' },
  { key: '3', department: '心血管内科', total: 2543, accuracy: 95.8, correctionRate: 4.2, status: 'normal' },
  { key: '4', department: '超声科', total: 2105, accuracy: 94.5, correctionRate: 5.5, status: 'normal' },
  { key: '5', department: '妇产科', total: 2081, accuracy: 91.2, correctionRate: 8.8, status: 'alert' },
];

const trendOption = {
  backgroundColor: 'transparent',
  tooltip: { trigger: 'axis' },
  legend: { data: ['AI准确率', '人工修正率'], textStyle: { color: tokens.color.text.secondary } },
  grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
  xAxis: {
    type: 'category',
    data: ['1月', '2月', '3月', '4月', '5月', '6月'],
    axisLine: { lineStyle: { color: tokens.color.border.default } },
    axisLabel: { color: tokens.color.text.muted },
  },
  yAxis: {
    type: 'value',
    min: 0,
    max: 100,
    axisLine: { lineStyle: { color: tokens.color.border.default } },
    axisLabel: { color: tokens.color.text.muted },
    splitLine: { lineStyle: { color: tokens.color.border.default, type: 'dashed' as const } },
  },
  series: [
    {
      name: 'AI准确率',
      type: 'line',
      data: [92.1, 93.0, 93.8, 94.2, 94.5, 94.7],
      smooth: true,
      lineStyle: { color: tokens.color.accent.primary, width: 2 },
      itemStyle: { color: tokens.color.accent.primary },
      areaStyle: { color: { type: 'linear' as const, x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: tokens.color.accent.primaryAlpha40 }, { offset: 1, color: 'transparent' }] } },
    },
    {
      name: '人工修正率',
      type: 'line',
      data: [7.9, 7.0, 6.2, 5.8, 5.5, 5.3],
      smooth: true,
      lineStyle: { color: tokens.color.accent.warning, width: 2 },
      itemStyle: { color: tokens.color.accent.warning },
      areaStyle: { color: { type: 'linear' as const, x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(245,158,11,0.25)' }, { offset: 1, color: 'transparent' }] } },
    },
  ],
};

const correctionAlertOption = {
  backgroundColor: 'transparent',
  tooltip: { trigger: 'axis' },
  grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
  xAxis: {
    type: 'category',
    data: ['甲状腺', '肝脏', '心脏', '肾脏', '乳腺', '血管'],
    axisLine: { lineStyle: { color: tokens.color.border.default } },
    axisLabel: { color: tokens.color.text.muted },
  },
  yAxis: {
    type: 'value',
    axisLine: { lineStyle: { color: tokens.color.border.default } },
    axisLabel: { color: tokens.color.text.muted },
    splitLine: { lineStyle: { color: tokens.color.border.default, type: 'dashed' as const } },
  },
  series: [
    {
      type: 'bar',
      data: [
        { value: 3.8, itemStyle: { color: tokens.color.accent.success } },
        { value: 6.9, itemStyle: { color: tokens.color.accent.warning } },
        { value: 4.2, itemStyle: { color: tokens.color.accent.success } },
        { value: 5.1, itemStyle: { color: tokens.color.accent.warning } },
        { value: 8.8, itemStyle: { color: tokens.color.accent.danger } },
        { value: 4.5, itemStyle: { color: tokens.color.accent.success } },
      ],
      barWidth: '40%',
    },
  ],
};

const alertTimeline = [
  { color: tokens.color.accent.danger, children: '妇产科 AI修正率突破8%阈值（当前8.8%），建议复核' },
  { color: tokens.color.accent.warning, children: '肝胆外科 AI修正率持续偏高（6.9%），需关注' },
  { color: tokens.color.accent.success, children: '甲状腺外科 连续3个月AI准确率>95%' },
  { color: tokens.color.accent.primary, children: '系统整体AI准确率环比上升0.2个百分点' },
];

export default function QcReportPage() {
  const [timeRange, setTimeRange] = useState<string>('6m');

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
            QC REPORT
          </Title>
          {mockRole === 'director' && (
            <Badge
              count="主任"
              style={{
                backgroundColor: tokens.color.accent.secondary,
                fontFamily: tokens.font.body,
                fontSize: 11,
              }}
            />
          )}
        </div>
        <div className="flex items-center gap-2">
          {['3m', '6m', '1y'].map((range) => (
            <Button
              key={range}
              size="small"
              type={timeRange === range ? 'primary' : 'default'}
              onClick={() => setTimeRange(range)}
            >
              {range === '3m' ? '近3月' : range === '6m' ? '近6月' : '近1年'}
            </Button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-4 gap-4" style={{ marginBottom: 24 }}>
        {kpiData.map((kpi) => (
          <Card
            key={kpi.title}
            style={{
              background: tokens.color.bg.secondary,
              border: `1px solid ${tokens.color.border.default}`,
            }}
          >
            <div className="flex items-center gap-3">
              <div
                style={{
                  width: 40,
                  height: 40,
                  borderRadius: tokens.radius.md,
                  background: `${kpi.color}15`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: kpi.color,
                  fontSize: 18,
                }}
              >
                {kpi.icon}
              </div>
              <Statistic
                title={<span style={{ color: tokens.color.text.muted, fontSize: 12 }}>{kpi.title}</span>}
                value={kpi.value}
                suffix={kpi.suffix}
                valueStyle={{ color: kpi.color, fontSize: 22, fontFamily: tokens.font.mono }}
              />
            </div>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-4" style={{ marginBottom: 24 }}>
        <Card
          title={
            <span style={{ color: tokens.color.text.primary, fontFamily: tokens.font.display, fontSize: 13, letterSpacing: '0.05em' }}>
              <RiseOutlined style={{ marginRight: 8, color: tokens.color.accent.primary }} />
              TREND ANALYSIS
            </span>
          }
          style={{ background: tokens.color.bg.secondary, border: `1px solid ${tokens.color.border.default}` }}
        >
          <ReactECharts option={trendOption} style={{ height: 280 }} />
        </Card>

        <Card
          title={
            <span style={{ color: tokens.color.text.primary, fontFamily: tokens.font.display, fontSize: 13, letterSpacing: '0.05em' }}>
              <AlertOutlined style={{ marginRight: 8, color: tokens.color.accent.warning }} />
              CORRECTION RATE BY ORGAN
            </span>
          }
          style={{ background: tokens.color.bg.secondary, border: `1px solid ${tokens.color.border.default}` }}
        >
          <ReactECharts option={correctionAlertOption} style={{ height: 280 }} />
        </Card>
      </div>

      <Card
        title={
          <span style={{ color: tokens.color.text.primary, fontFamily: tokens.font.display, fontSize: 13, letterSpacing: '0.05em' }}>
            <TeamOutlined style={{ marginRight: 8, color: tokens.color.accent.secondary }} />
            DEPARTMENT STATISTICS
          </span>
        }
        style={{ background: tokens.color.bg.secondary, border: `1px solid ${tokens.color.border.default}`, marginBottom: 24 }}
      >
        <Table
          columns={departmentColumns}
          dataSource={departmentData}
          pagination={false}
          size="small"
        />
      </Card>

      {mockRole === 'director' && (
        <Card
          title={
            <span style={{ color: tokens.color.text.primary, fontFamily: tokens.font.display, fontSize: 13, letterSpacing: '0.05em' }}>
              <AlertOutlined style={{ marginRight: 8, color: tokens.color.accent.danger }} />
              ALERT TIMELINE
            </span>
          }
          style={{ background: tokens.color.bg.secondary, border: `1px solid ${tokens.color.border.default}` }}
        >
          <Timeline
            items={alertTimeline.map((item) => ({
              color: item.color === tokens.color.accent.danger ? 'red' : item.color === tokens.color.accent.warning ? 'gold' : item.color === tokens.color.accent.success ? 'green' : 'blue',
              children: <Text style={{ color: tokens.color.text.secondary, fontSize: 13 }}>{item.children}</Text>,
            }))}
          />
        </Card>
      )}
    </div>
  );
}
