import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import WorkspaceImageUpload from '../WorkspaceImageUpload';
import type { DicomUploadState } from '@/hooks/useDicomUpload';

/**
 * 影像装载控件。
 *
 * 2026-10-05 真浏览器走查实测全站 `input[type=file]` = 0（含隐藏），
 * 所以这里最要紧的不是样式，是**那个 input 必须真的在 DOM 里、真的能被触发**。
 */

const idle: DicomUploadState = {
  phase: 'idle',
  fileName: null,
  error: null,
  localImageIds: [],
  serverImageIds: [],
};

function dicomFile() {
  return new File([new Uint8Array([0, 1, 2, 3])], 'thyroid.dcm', {
    type: 'application/dicom',
  });
}

describe('WorkspaceImageUpload · 可用态', () => {
  it('渲染一个 accept .dcm/.dicom 的 file input（默认沉默的反面：该出现时必须出现）', () => {
    render(<WorkspaceImageUpload state={idle} ready onPick={vi.fn()} />);

    const input = screen.getByLabelText('选择 DICOM 影像文件') as HTMLInputElement;
    expect(input).toBeInTheDocument();
    expect(input.type).toBe('file');
    expect(input.getAttribute('accept')).toBe('.dcm,.dicom');
  });

  it('选文件后把 File 交给 onPick', async () => {
    const onPick = vi.fn();
    render(<WorkspaceImageUpload state={idle} ready onPick={onPick} />);

    const input = screen.getByLabelText('选择 DICOM 影像文件');
    const f = dicomFile();
    fireEvent.change(input, { target: { files: [f] } });

    await waitFor(() => expect(onPick).toHaveBeenCalledWith([f]));
  });

  it('多帧（A.38）：一次选多张整批交给 onPick —— input 必须 multiple，否则界面上选不出多帧', async () => {
    const onPick = vi.fn();
    render(<WorkspaceImageUpload state={idle} ready onPick={onPick} />);

    const input = screen.getByLabelText('选择 DICOM 影像文件');
    expect(input.hasAttribute('multiple')).toBe(true);
    const f1 = dicomFile();
    const f2 = new File([new Uint8Array([4, 5, 6])], 'thyroid-2.dcm', {
      type: 'application/dicom',
    });
    fireEvent.change(input, { target: { files: [f1, f2] } });

    await waitFor(() => expect(onPick).toHaveBeenCalledWith([f1, f2]));
  });

  it('选完后清空 input.value —— 否则重选同一个文件不再触发 change', () => {
    render(<WorkspaceImageUpload state={idle} ready onPick={vi.fn()} />);
    const input = screen.getByLabelText('选择 DICOM 影像文件') as HTMLInputElement;

    fireEvent.change(input, { target: { files: [dicomFile()] } });

    expect(input.value).toBe('');
  });

  it('上传中禁用选择器并如实显示进度，不给"已完成"之类的假进展', () => {
    const uploading: DicomUploadState = {
      phase: 'uploading',
      fileName: 'thyroid.dcm',
      error: null,
      localImageIds: [],
      serverImageIds: [],
    };
    render(<WorkspaceImageUpload state={uploading} ready onPick={vi.fn()} />);

    const input = screen.getByLabelText('选择 DICOM 影像文件') as HTMLInputElement;
    expect(input.disabled).toBe(true);
    expect(screen.getByText('影像上传中…')).toBeInTheDocument();
    expect(screen.queryByText(/已完成/)).toBeNull();
  });

  it('出错时渲染带 role=alert 的原因', () => {
    const failed: DicomUploadState = {
      phase: 'error',
      fileName: 'thyroid.dcm',
      error: 'DICOM 缺少必需标签',
      localImageIds: [],
      serverImageIds: [],
    };
    render(<WorkspaceImageUpload state={failed} ready onPick={vi.fn()} />);

    expect(screen.getByRole('alert')).toHaveTextContent('DICOM 缺少必需标签');
  });

  it('已装载时提示可以重新装载', () => {
    const done: DicomUploadState = {
      phase: 'uploaded',
      fileName: 'thyroid.dcm',
      error: null,
      imageId: 'img_x',
      localImageIds: [],
      serverImageIds: [],
    };
    render(<WorkspaceImageUpload state={done} ready onPick={vi.fn()} />);
    expect(screen.getByText('重新装载影像')).toBeInTheDocument();
  });
});

describe('WorkspaceImageUpload · 未就绪（默认沉默）', () => {
  it('没有会话时不渲染 file input，只说明从哪取会话', () => {
    render(<WorkspaceImageUpload state={idle} ready={false} onPick={vi.fn()} />);

    expect(screen.queryByLabelText('选择 DICOM 影像文件')).toBeNull();
    expect(document.querySelector('input[type=file]')).toBeNull();
    expect(screen.getByText(/请先从工作列表选择检查会话/)).toBeInTheDocument();
  });

  it('给出跳回工作列表的出口（医生不能被困在工作区）', () => {
    const onGoWorklist = vi.fn();
    render(
      <WorkspaceImageUpload
        state={idle}
        ready={false}
        onPick={vi.fn()}
        onGoWorklist={onGoWorklist}
      />
    );

    fireEvent.click(screen.getByText('去工作列表选择会话'));
    expect(onGoWorklist).toHaveBeenCalled();
  });

  it('blockedReason 优先于通用文案（会话读取失败要说人话）', () => {
    render(
      <WorkspaceImageUpload
        state={idle}
        ready={false}
        blockedReason="该检查会话未声明部位，无法发起判读"
        onPick={vi.fn()}
      />
    );
    expect(
      screen.getByText('该检查会话未声明部位，无法发起判读')
    ).toBeInTheDocument();
  });
});
