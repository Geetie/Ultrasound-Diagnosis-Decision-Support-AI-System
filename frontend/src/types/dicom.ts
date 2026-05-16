export interface DicomMetadata {
  patientId: string;
  patientName: string;
  studyInstanceUid: string;
  seriesInstanceUid: string;
  sopInstanceUid: string;
  modality: string;
  acquisitionDate: string;
  rows: number;
  columns: number;
  bitsAllocated: number;
  bodyPart?: string;
  institutionName?: string;
  manufacturer?: string;
}

export interface DicomViewportState {
  windowWidth: number;
  windowCenter: number;
  scale: number;
  translationX: number;
  translationY: number;
  invert: boolean;
}

export interface DicomFrameData {
  frameIndex: number;
  instanceUid: string;
  isKeyFrame: boolean;
  priority: number;
  data: ArrayBuffer;
  crc32: number;
}

export interface FrameIndexMapping {
  edgeFrameIndex: number;
  sessionFrameIndex: number;
  instanceUid: string;
  frameId: string;
}

export type DicomTool = 'windowLevel' | 'zoom' | 'pan' | 'length' | 'angle' | 'none';

export interface DicomImageState {
  isLoading: boolean;
  isLoaded: boolean;
  error: string | null;
  currentFrameIndex: number;
  totalFrames: number;
  viewport: DicomViewportState;
  metadata: DicomMetadata | null;
  showOverlay: boolean;
}
