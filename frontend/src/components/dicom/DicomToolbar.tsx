import { Button, Slider, Space, Tooltip } from 'antd';
import {
  AimOutlined,
  ZoomInOutlined,
  DragOutlined,
  UndoOutlined,
  EyeOutlined,
  EyeInvisibleOutlined,
  LeftOutlined,
  RightOutlined,
} from '@ant-design/icons';
import { useDicomStore } from '@/stores/dicomStore';

interface DicomToolbarProps {
  onFrameChange?: (frameIndex: number) => void;
  currentFrame?: number;
}

export default function DicomToolbar({ onFrameChange, currentFrame = 0 }: DicomToolbarProps) {
  const { activeTool, viewport, showOverlay, totalFrames } = useDicomStore();
  const { setActiveTool, setViewport, toggleOverlay, setCurrentFrame } = useDicomStore();

  const tools = [
    { key: 'windowLevel', icon: <AimOutlined />, label: '窗位' },
    { key: 'zoom', icon: <ZoomInOutlined />, label: '缩放' },
    { key: 'pan', icon: <DragOutlined />, label: '平移' },
  ] as const;

  return (
    <div
      className="absolute bottom-0 left-0 right-0 flex items-center justify-between"
      style={{
        background: 'linear-gradient(transparent, rgba(10, 14, 26, 0.9))',
        padding: '12px 16px 8px',
        zIndex: 50,
      }}
    >
      <Space size={4}>
        {tools.map((tool) => (
          <Tooltip key={tool.key} title={tool.label}>
            <Button
              size="small"
              type={activeTool === tool.key ? 'primary' : 'text'}
              icon={tool.icon}
              onClick={() => setActiveTool(tool.key as any)}
              style={
                activeTool === tool.key
                  ? { background: '#00d4ff20', borderColor: '#00d4ff40' }
                  : { color: '#94a3b8' }
              }
            />
          </Tooltip>
        ))}
        <Tooltip title="重置视图">
          <Button
            size="small"
            type="text"
            icon={<UndoOutlined />}
            onClick={() => setViewport({ windowWidth: 400, windowCenter: 40, scale: 1, translationX: 0, translationY: 0 })}
            style={{ color: '#94a3b8' }}
          />
        </Tooltip>
        <Tooltip title={showOverlay ? '隐藏信息' : '显示信息'}>
          <Button
            size="small"
            type="text"
            icon={showOverlay ? <EyeOutlined /> : <EyeInvisibleOutlined />}
            onClick={toggleOverlay}
            style={{ color: '#94a3b8' }}
          />
        </Tooltip>
      </Space>

      <div className="flex items-center gap-3">
        <span style={{ color: '#64748b', fontSize: 11, fontFamily: "'JetBrains Mono', monospace" }}>
          WW: {viewport.windowWidth}
        </span>
        <Slider
          min={1}
          max={4096}
          value={viewport.windowWidth}
          onChange={(val) => setViewport({ windowWidth: val })}
          style={{ width: 80, margin: 0 }}
        />
        <span style={{ color: '#64748b', fontSize: 11, fontFamily: "'JetBrains Mono', monospace" }}>
          WL: {viewport.windowCenter}
        </span>
        <Slider
          min={-2000}
          max={4000}
          value={viewport.windowCenter}
          onChange={(val) => setViewport({ windowCenter: val })}
          style={{ width: 80, margin: 0 }}
        />
      </div>

      {totalFrames > 1 && (
        <Space size={4}>
          <Button
            size="small"
            type="text"
            icon={<LeftOutlined />}
            onClick={() => {
              const prev = Math.max(0, currentFrame - 1);
              setCurrentFrame(prev);
              onFrameChange?.(prev);
            }}
            style={{ color: '#94a3b8' }}
          />
          <span style={{ color: '#64748b', fontSize: 11, fontFamily: "'JetBrains Mono', monospace" }}>
            {currentFrame + 1}/{totalFrames}
          </span>
          <Button
            size="small"
            type="text"
            icon={<RightOutlined />}
            onClick={() => {
              const next = Math.min(totalFrames - 1, currentFrame + 1);
              setCurrentFrame(next);
              onFrameChange?.(next);
            }}
            style={{ color: '#94a3b8' }}
          />
        </Space>
      )}
    </div>
  );
}
