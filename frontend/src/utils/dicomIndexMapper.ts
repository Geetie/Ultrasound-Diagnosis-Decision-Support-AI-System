import type { FrameIndexMapping } from '@/types/dicom';

export class DicomIndexMapper {
  private mappings: Map<number, FrameIndexMapping> = new Map();
  private sessionFrameCount = 0;

  addFrame(edgeFrameIndex: number, instanceUid: string): FrameIndexMapping {
    const sessionFrameIndex = this.sessionFrameCount++;
    const frameId = `frame_${Date.now()}_${sessionFrameIndex}`;
    const mapping: FrameIndexMapping = {
      edgeFrameIndex,
      sessionFrameIndex,
      instanceUid,
      frameId,
    };
    this.mappings.set(edgeFrameIndex, mapping);
    return mapping;
  }

  getByEdgeIndex(edgeFrameIndex: number): FrameIndexMapping | undefined {
    return this.mappings.get(edgeFrameIndex);
  }

  getBySessionIndex(sessionFrameIndex: number): FrameIndexMapping | undefined {
    for (const mapping of this.mappings.values()) {
      if (mapping.sessionFrameIndex === sessionFrameIndex) {
        return mapping;
      }
    }
    return undefined;
  }

  getByFrameId(frameId: string): FrameIndexMapping | undefined {
    for (const mapping of this.mappings.values()) {
      if (mapping.frameId === frameId) {
        return mapping;
      }
    }
    return undefined;
  }

  getSessionFrameCount(): number {
    return this.sessionFrameCount;
  }

  clear(): void {
    this.mappings.clear();
    this.sessionFrameCount = 0;
  }
}
