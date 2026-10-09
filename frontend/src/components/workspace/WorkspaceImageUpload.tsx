import { useRef } from 'react';
import type { DicomUploadState } from '@/hooks/useDicomUpload';

/**
 * 影像装载入口 —— 医生把 DICOM 送进系统的**唯一界面入口**。
 *
 * 【为什么落在结论区的 02_扫查态】
 *   设计稿 02 扫查态的结论区原文是「等待冻结取图 / 冻结当前帧即自动发起判读」，
 *   那套交互的前提是设备端有冻结键。本部署是**浏览器端**，没有冻结键，
 *   医生唯一能做的"取图"动作就是选一个 DICOM 文件 —— 所以把入口放在这个状态里，
 *   语义正好对上：「等的就是这一帧」。也因为落在结论区（两轴居中、只有两行文案），
 *   影像区 600 的骨架一个像素没动。
 *
 * 【文案偏离设计稿一处，已登记】
 *   主文案由「等待冻结取图」改为「等待装载影像」，副文案改为「装载后自动发起判读」。
 *   保留原词会让医生在界面上找一个不存在的冻结键。
 *
 * 【默认沉默】
 *   ① 没有会话（sessionId 为空）→ 不给选择器，只说明从哪儿取会话 + 给一个跳转。
 *      凭空造一个 session_id 提交，会在库里留下一条指向不存在患者的检查记录。
 *   ② 上传中 / 已上传 → 状态如实显示，不重复给选择器之外的假进展。
 *
 * input 用 clip 隐藏而非 display:none —— 后者会让键盘与读屏完全够不到它，
 * 而"看不见的 input"恰恰是走查时最容易漏掉的东西（2026-10-05 实测全站 file input=0）。
 */

export default function WorkspaceImageUpload({
  state,
  ready,
  blockedReason,
  onPick,
  onGoWorklist,
}: {
  state: DicomUploadState;
  /** false = 此刻不给选择器（未选定会话，或会话没声明部位）。 */
  ready: boolean;
  /** ready=false 时说明具体原因，比一句"请先选择会话"更可用。 */
  blockedReason?: string | null;
  onPick: (files: File[]) => void | Promise<void>;
  onGoWorklist?: () => void;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const busy = state.phase === 'uploading';

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    // 先清空 value：否则医生重选同一个文件不会再触发 change，表现为"点了没反应"
    e.target.value = '';
    // 多帧（A.38 分期①）：一次可选多帧，管线本就收 image_ids 数组 ——
    // 此前 input 没有 multiple，"多帧"在界面上根本选不出来（走查实测）。
    if (files.length > 0) void onPick(files);
  };

  if (!ready) {
    return (
      <div className="ws-upload" data-region="image-upload">
        <p className="ws-upload__hint">
          {blockedReason ?? '请先从工作列表选择检查会话，再装载影像'}
        </p>
        {onGoWorklist && (
          <button type="button" className="ws-upload__action" onClick={onGoWorklist}>
            去工作列表选择会话
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="ws-upload" data-region="image-upload">
      <label className="ws-upload__pick">
        <input
          ref={inputRef}
          type="file"
          accept=".dcm,.dicom"
          multiple
          className="ws-upload__input"
          onChange={handleChange}
          disabled={busy}
          aria-label="选择 DICOM 影像文件"
        />
        <span className="ws-upload__pick-text">
          {busy ? '影像上传中…' : state.phase === 'uploaded' ? '重新装载影像' : '选择 DICOM 影像'}
        </span>
      </label>

      <p className="ws-upload__hint">
        {busy
          ? '上传完成后自动发起判读'
          : '装载后自动发起判读，不需要再点按钮'}
      </p>

      {/* 错误只在出错时出现（契约 §1：不满足条件不渲染），且必须带 role=alert */}
      {state.error && (
        <p className="ws-upload__error" role="alert">
          {state.error}
        </p>
      )}
    </div>
  );
}
