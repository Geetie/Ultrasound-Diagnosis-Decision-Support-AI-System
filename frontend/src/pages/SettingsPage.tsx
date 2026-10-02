import { useNavigate } from 'react-router-dom';
import AppShell from '@/components/shell/AppShell';
import type { ThemeName } from '@/components/shell/AppSidebar';

/**
 * 设置 —— 08_设置（一级导航第 2 项）
 *
 * 画布上本屏是**只读展示**：4 个设置行都是 FRAME + 两个 TEXT，没有任何 actions，
 * 所以这里不造Switch / Checkbox。真实交互（改主题、开关脚踏）需要产品给出可编辑
 * 版本的设计数据后再接。
 *
 * 逐条对应的设计取舍：
 *   · 副标题**显式声明范围** —— 质控看板 / 设备运维 / 知识库是独立工作面，不是设置项
 *   · 主题行是**设备级一次设置**，不按页面分默认；影像区固定不参与切换
 *   · 脚踏行**用次要色而不是 attention** —— 脚踏不得为必需项，基层可能没有此硬件，
 *     未检测到也不该报警（不是「停」档）
 *   · 侧栏高亮在「设置」（画布已修正过，原先错停在「工作列表」）
 */
export default function SettingsPage({ theme = 'dark' }: { theme?: ThemeName }) {
  const navigate = useNavigate();

  return (
    <AppShell
      active="settings"
      theme={theme}
      screen="08_设置"
      onNavigate={(key) => navigate(key === 'settings' ? '/settings' : '/worklist')}
    >
      <h1 className="page-title">设置</h1>
      <p className="page-subtitle">
        只放个人与系统偏好。质控看板、设备运维、知识库是独立工作面，不塞进设置。
      </p>

      <span className="page-group-title">外观</span>
      {/* 主题是设备级一次设置；影像区固定不参与切换 */}
      <div className="page-setting-row" data-node="2:270">
        <span className="page-setting-row__label">主题</span>
        <span className="page-setting-row__value page-setting-row__value--attention">
          {`跟随设备 · 当前${theme === 'light' ? '浅色' : '深色'}（影像区恒为深色底，不参与切换）`}
        </span>
      </div>

      <span className="page-group-title">语音与双手</span>
      <div className="page-setting-row" data-node="2:277">
        <span className="page-setting-row__label">语音确认</span>
        <span className="page-setting-row__value page-setting-row__value--attention">
          开 · 系统回读 + 一个确认动作（说确认词 / 点大按钮 / 脚踏，三选一等效）
        </span>
      </div>
      {/* 未检测到脚踏**不报警**：用次要色，不用 attention，更不是「停」档 */}
      <div className="page-setting-row" data-node="2:284">
        <span className="page-setting-row__label">脚踏开关</span>
        <span className="page-setting-row__value">
          未检测到设备 · 脚踏不是必需项，不配也能走完全流程
        </span>
      </div>

      <span className="page-group-title">检查预设</span>
      {/* 与 09 的「默认已预选」配对 —— 医生通常一键即可，不增加强制步骤 */}
      <div className="page-setting-row" data-node="2:291">
        <span className="page-setting-row__label">默认部位预设</span>
        <span className="page-setting-row__value page-setting-row__value--attention">
          甲状腺（上次使用） · 建会话时默认选中，一键即可
        </span>
      </div>

      <p className="page-note">
        快捷键与主题同属设备级设置：同一台机器上所有页面共用一次设置，不按页面分别切换，避免反复打断暗适应。
      </p>
    </AppShell>
  );
}
