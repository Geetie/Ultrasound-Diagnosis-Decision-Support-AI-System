import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import AppShell from '@/components/shell/AppShell';
import type { ThemeName } from '@/components/shell/AppSidebar';
import { listDevices, type DeviceListItem } from '@/services/adminService';
import { getApiErrorMessage } from '@/utils/apiError';

/** 从 axios 形状的异常里取 HTTP 状态（不猜角色字段 —— 以状态码为准）。 */
function httpStatusOf(err: unknown): number | undefined {
  return (err as { response?: { status?: number } })?.response?.status;
}

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
 *
 * 【A.58 有意偏离设计稿：新增「设备与权限」区组】
 * 画布上没有这一区。加它的原因是「设备台账属管理员工作面」这条口径此前**只写在审计
 * 探针里**（p16：医生 token 403 / 无 token 401），界面上完全没有落点 ——
 * 医生既看不到"为什么没有设备列表"，也无法自助判断该找谁。这里按只读区组落地：
 *   · 静态行说明权限口径（设备台账需管理员角色，检查与判读不受影响）；
 *   · 动态行如实呈现取数结果：**403 给可读说明**（权限边界，不是故障）、其它错误给
 *     可读原因、成功则列出台账（只读，最多 5 条 —— 设置页不是运维面板）。
 * 不猜角色、不自动重试（避免对管理员面接口造成无效轰炸）。回退方式：删掉这一区组
 * 与对应单测即可，其余功能不受影响。
 */
export default function SettingsPage({ theme = 'dark' }: { theme?: ThemeName }) {
  const navigate = useNavigate();
  const [devices, setDevices] = useState<DeviceListItem[] | null>(null);
  const [permissionNote, setPermissionNote] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    listDevices({ limit: 5 })
      .then((resp) => {
        if (alive) setDevices(resp.items ?? []);
      })
      .catch((err: unknown) => {
        if (!alive) return;
        // 403 = 权限边界（需 SYSTEM_ADMIN），不是故障 —— 如实说明，不静默、不猜角色
        setPermissionNote(
          httpStatusOf(err) === 403
            ? '需管理员角色 · 设备台账（型号/位置/状态）仅系统管理员可见，检查与判读不受影响'
            : `设备台账读取失败：${getApiErrorMessage(err, '请稍后重试')}`,
        );
      });
    return () => {
      alive = false;
    };
  }, []);

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

      {/* A.58（有意偏离设计稿）：设备与权限 —— 见文件头说明 */}
      <span className="page-group-title">设备与权限</span>
      <div className="page-setting-row" data-region="settings-device-permission">
        <span className="page-setting-row__label">设备台账权限</span>
        <span className="page-setting-row__value">
          {permissionNote ?? '需管理员角色 · 检查与判读不受影响'}
        </span>
      </div>
      {(devices ?? []).map((d) => (
        <div className="page-setting-row" key={d.device_id} data-region="settings-device-row">
          <span className="page-setting-row__label">{d.device_name || d.device_id}</span>
          <span className="page-setting-row__value">
            {`${d.device_type} · ${d.location ?? '未登记位置'} · ${d.status}`}
          </span>
        </div>
      ))}

      <p className="page-note">
        快捷键与主题同属设备级设置：同一台机器上所有页面共用一次设置，不按页面分别切换，避免反复打断暗适应。
      </p>
    </AppShell>
  );
}
