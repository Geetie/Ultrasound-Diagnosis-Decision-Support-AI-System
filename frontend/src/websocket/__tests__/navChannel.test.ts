import { beforeEach, describe, expect, it } from 'vitest';

import type { WsNavigationMessage } from '@/types/websocket';
import { useNavigationStore } from '@/stores/navigationStore';
import { handleNavigationMessage, handleNavigationRevocation } from '../navChannel';

function _message(overrides: Partial<WsNavigationMessage> = {}): WsNavigationMessage {
  return {
    type: 'navigation',
    frame_id: 'frame_7',
    annotations: [
      {
        label: 'nodule',
        confidence: 0.8,
        threshold: 0.5,
        bbox: [0.1, 0.2, 0.3, 0.4],
      },
    ],
    desensitization_status: 'full',
    timestamp: 1700000000,
    ...overrides,
  };
}

describe('navChannel（M1/M2/M6 导航消息映射）', () => {
  beforeEach(() => {
    useNavigationStore.setState({
      currentFrameAnnotations: [],
      currentFramePlaneAssessment: null,
      currentFrameImageQuality: null,
      lowConfidenceFrameIds: new Set<string>(),
    });
  });

  it('映射基础标注字段', () => {
    handleNavigationMessage(_message());

    const [item] = useNavigationStore.getState().currentFrameAnnotations;
    expect(item.annotation.label).toBe('nodule');
    expect(item.annotation.confidence).toBe(0.8);
    expect(item.annotation.bbox).toEqual([0.1, 0.2, 0.3, 0.4]);
    expect(item.status).toBe('pending');
    expect(item.frameId).toBe('frame_7');
  });

  it('后端未下发 id 时派生稳定 id（否则撤销与去重会静默失效）', () => {
    handleNavigationMessage(
      _message({
        annotations: [
          { label: 'a', confidence: 0.7, threshold: 0.5, bbox: [0, 0, 1, 1] },
          { label: 'b', confidence: 0.7, threshold: 0.5, bbox: [0, 0, 1, 1] },
        ],
      })
    );

    const ids = useNavigationStore
      .getState()
      .currentFrameAnnotations.map((item) => item.annotation.id);
    expect(ids).toEqual(['frame_7:0', 'frame_7:1']);
    expect(new Set(ids).size).toBe(2);
  });

  it('后端已下发 id 时保留后端值', () => {
    handleNavigationMessage(
      _message({
        annotations: [
          { id: 'server_id', label: 'a', confidence: 0.7, threshold: 0.5, bbox: [0, 0, 1, 1] },
        ],
      })
    );

    expect(useNavigationStore.getState().currentFrameAnnotations[0].annotation.id).toBe(
      'server_id'
    );
  });

  it('透传 M2 的 B+ 级高危标记', () => {
    handleNavigationMessage(
      _message({
        annotations: [
          {
            label: 'malignant_suspected',
            confidence: 0.95,
            threshold: 0.5,
            bbox: [0, 0, 1, 1],
            risk_flag: 'high',
            suggested_action: 'start_standard_evaluation',
          },
        ],
      })
    );

    const { annotation } = useNavigationStore.getState().currentFrameAnnotations[0];
    expect(annotation.risk_flag).toBe('high');
    expect(annotation.suggested_action).toBe('start_standard_evaluation');
  });

  it('缺省时高危字段为 null（而非 undefined，便于渲染判断）', () => {
    handleNavigationMessage(_message());

    const { annotation } = useNavigationStore.getState().currentFrameAnnotations[0];
    expect(annotation.risk_flag).toBeNull();
    expect(annotation.suggested_action).toBeNull();
  });

  it('写入 M1/M6 帧级属性', () => {
    handleNavigationMessage(
      _message({
        plane_assessment: {
          plane_type: 'longitudinal',
          is_standard: true,
          plane_score: 0.9,
          issues: [],
          required_structures_visible: ['left_lobe'],
        },
        image_quality: {
          overall_score: 0.42,
          issues: ['severe_blur'],
        },
      })
    );

    const state = useNavigationStore.getState();
    expect(state.currentFramePlaneAssessment?.is_standard).toBe(true);
    expect(state.currentFrameImageQuality?.overall_score).toBe(0.42);
  });

  it('后端未部署评估头时帧级属性为 null', () => {
    handleNavigationMessage(_message());

    const state = useNavigationStore.getState();
    expect(state.currentFramePlaneAssessment).toBeNull();
    expect(state.currentFrameImageQuality).toBeNull();
  });

  it('无标注帧记为低置信度帧', () => {
    handleNavigationMessage(_message({ annotations: [] }));

    expect(useNavigationStore.getState().lowConfidenceFrameIds.has('frame_7')).toBe(true);
  });

  it('清空标注时同时清空帧级属性（避免上一帧残影）', () => {
    handleNavigationMessage(
      _message({
        plane_assessment: {
          plane_type: 'transverse',
          is_standard: false,
          plane_score: 0.3,
          issues: ['low_contrast'],
          required_structures_visible: [],
        },
      })
    );
    expect(useNavigationStore.getState().currentFramePlaneAssessment).not.toBeNull();

    useNavigationStore.getState().clearAnnotations();

    const state = useNavigationStore.getState();
    expect(state.currentFramePlaneAssessment).toBeNull();
    expect(state.currentFrameImageQuality).toBeNull();
    expect(state.currentFrameAnnotations).toHaveLength(0);
  });

  it('撤销消息按派生的 id 移除标注', () => {
    handleNavigationMessage(_message());
    expect(useNavigationStore.getState().currentFrameAnnotations).toHaveLength(1);

    handleNavigationRevocation({
      type: 'navigation_revocation',
      frame_id: 'frame_7',
      annotation_ids: ['frame_7:0'],
      reason: 'safety',
      timestamp: 1700000001,
    });

    expect(useNavigationStore.getState().currentFrameAnnotations).toHaveLength(0);
  });
});
