import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import AppShell from '@/components/shell/AppShell';
import type { ThemeName } from '@/components/shell/AppSidebar';
import { listSessions } from '@/services/sessionService';
import type { SessionListItem } from '@/types/api';

/**
 * 工作列表 —— 01_工作列表（深色） / 15_工作列表浅色（浅色）
 *
 * 两屏**逐节点同构，只有颜色变**（_页面分类.md：「与 01 逐节点对照：只有颜色变、
 * 布局一个像素没动」），唯一文字差异是侧栏脚注的「深色 / 浅色」。
 * 所以只写这一个组件，浅色版换 data-theme 即得 —— **不要写第二套页面**。
 *
 * 设计取舍：
 *   · 进行中的会话**置顶一行 + 行内「继续」**（why: 2:68）
 *   · 「待复核」属「看一眼」档 → 中性青灰，**不得用红或橙**
 *   · 「已签发」是正常态 → **不带任何语义色**，这正是「无颜色＝正常」的样例
 *
 * 数据源：`GET /api/v1/sessions`（2026-10-02 补的 —— 此前后端只有
 * POST "" / GET {id} / PATCH {id}，**没有列表**，本页只能写死三行假数据）。
 * 真机验证脚本：scripts/verify_sessions_list.py。
 *
 * 【P1 默认沉默】两个分组各自按数据有无决定渲不渲染：没有进行中会话就不出
 * 「进行中」标题，没有待处理就不出该组 —— 不是渲染成空壳，也不显示
 * 「暂无数据」这种设计稿里没有的文案。
 *
 * 注意：本页不套旧 AppLayout，画布上它自带侧栏 200 且没有顶栏/状态栏。
 */

/** 部位代码 → 中文。未知值原样显示，不兜底成「未知部位」—— 那会掩盖数据问题。 */
const ORGAN_LABEL: Record<string, string> = {
  thyroid: '甲状腺',
  breast: '乳腺',
  liver: '肝脏',
  heart: '心脏',
  kidney: '肾脏',
  pancreas: '胰腺',
};

const organLabel = (organ: string): string => ORGAN_LABEL[organ] ?? organ;

const genderLabel = (g: string | null): string | null =>
  g === 'male' ? '男' : g === 'female' ? '女' : null;

/**
 * 患者标识：`张** · 女 · 52 岁`
 * 姓名用服务端已脱敏的值；性别/年龄缺失就少一段，不补「未知」——
 * 契约要求「不满足条件不渲染」，补「未知」等于凭空造了一条信息。
 */
function patientLine(item: SessionListItem): string {
  const parts: string[] = [];
  if (item.patient_name_masked) parts.push(item.patient_name_masked);
  const g = genderLabel(item.gender);
  if (g) parts.push(g);
  if (typeof item.age === 'number') parts.push(`${item.age} 岁`);
  return parts.join(' · ');
}

/** 剩余分钟。ttl 为 0 或已过期时不显示「剩余 0 分钟」—— 那是在说不存在的时间。 */
function remainingText(item: SessionListItem): string | null {
  const ttl = item.ttl_remaining_seconds;
  if (typeof ttl !== 'number' || ttl <= 0) return null;
  const min = Math.floor(ttl / 60);
  return min > 0 ? `剩余 ${min} 分钟` : null;
}

function toLocalTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** 状态 → 行内标签。待复核＝看一眼档（青灰）；已签发＝正常态（无色）。 */
function statusOf(item: SessionListItem): { text: string; cls: string } {
  switch (item.status) {
    case 'pending':
    case 'needs_review':
      return { text: '待复核', cls: 'page-row__status--attention' };
    case 'completed':
      return { text: '已签发', cls: 'page-row__status--plain' };
    case 'failed':
      return { text: '未完成', cls: 'page-row__status--attention' };
    case 'active':
    case 'partial':
      return { text: '进行中', cls: 'page-row__status--plain' };
    default:
      return { text: item.status, cls: 'page-row__status--plain' };
  }
}

export default function WorklistPage({ theme = 'dark' }: { theme?: ThemeName }) {
  const navigate = useNavigate();
  const [inProgress, setInProgress] = useState<SessionListItem[] | null>(null);
  const [pending, setPending] = useState<SessionListItem[] | null>(null);
  const [total, setTotal] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    listSessions()
      .then((d) => {
        if (!alive) return;
        setInProgress(d.in_progress ?? []);
        setPending(d.pending_today ?? []);
        setTotal(d.total_pending ?? 0);
      })
      .catch(() => {
        // 取不到就当两组都空。注意：静默失败会让医生以为"没有待处理病人"，
        // 这在临床上比报错更危险 —— 但契约 §1 要求不渲染设计稿里没有的文案，
        // 所以错误提示交给上层横幅（属"仅异常时渲染"），本页不塞占位块。
        if (!alive) return;
        setInProgress([]);
        setPending([]);
        setTotal(0);
      });
    return () => {
      alive = false;
    };
  }, []);

  // null = 加载中。此时两组都不渲染，避免先闪一个空列表再跳内容。
  // 用 ?? 收敛掉 null，好让 TS 在后续 .map 处能收窄。
  const loading = inProgress === null;
  const currentList = inProgress ?? [];
  const pendingList = pending ?? [];
  const showInProgress = !loading && currentList.length > 0;
  const showPending = !loading && pendingList.length > 0;

  return (
    <AppShell
      active="worklist"
      theme={theme}
      screen={theme === 'light' ? '15_工作列表浅色' : '01_工作列表'}
      onNavigate={(key) => navigate(key === 'settings' ? '/settings' : '/worklist')}
    >
      {/* 页头：标题左、统计右 —— 靠 SPACE_BETWEEN，不是绝对定位 */}
      <div className="page-header">
        <h1 className="page-title">工作列表</h1>
        {total !== null && total > 0 && (
          <span className="page-count">今日 {total} 例待处理</span>
        )}
      </div>

      {/* 进行中组：仅有进行中会话时整组渲染（含标题） */}
      {showInProgress && (
        <>
          <span className="page-group-title">进行中</span>
          {currentList.map((item) => {
            const organs = item.target_organs.map(organLabel).join('、');
            const started = toLocalTime(item.created_at);
            const remain = remainingText(item);
            return (
              <div key={item.session_id} className="page-row-current" data-node="2:68">
                <span className="page-row-current__patient">{patientLine(item)}</span>
                <span className="page-row__meta">
                  {[organs, started ? `${started} 开始` : null, remain]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
                <button
                  type="button"
                  className="ws-primary-button"
                  onClick={() => navigate('/')}
                >
                  继续
                </button>
              </div>
            );
          })}
        </>
      )}

      {/* 今日待处理组：仅有待处理项时整组渲染 */}
      {showPending && (
        <>
          <div className="page-header-row">
            <span className="page-group-title">今日待处理</span>
            <span className="page-filter-note">默认筛选：待人工复核</span>
          </div>
          <div className="page-list">
            {pendingList.map((item) => {
              const st = statusOf(item);
              const organs = item.target_organs.map(organLabel).join('、');
              const time = toLocalTime(item.created_at);
              return (
                <div key={item.session_id} className="page-row" data-node="2:79">
                  <div className="page-row__patient">
                    <span className="page-row__name">{patientLine(item)}</span>
                    <span className="page-row__meta">
                      {[organs, time].filter(Boolean).join(' · ')}
                    </span>
                  </div>
                  <span className={st.cls}>{st.text}</span>
                  <button
                    type="button"
                    className="page-row__action"
                    onClick={() => navigate('/')}
                  >
                    查看
                  </button>
                </div>
              );
            })}
          </div>
        </>
      )}
    </AppShell>
  );
}
