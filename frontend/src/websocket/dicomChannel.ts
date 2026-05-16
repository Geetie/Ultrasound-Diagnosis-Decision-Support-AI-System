import { DicomIndexMapper } from '@/utils/dicomIndexMapper';
import { useDicomStore } from '@/stores/dicomStore';

const indexMapper = new DicomIndexMapper();
const frameCache = new Map<number, ArrayBuffer>();
const MAX_CACHE_SIZE = 60;

function crc32(data: Uint8Array): number {
  let crc = 0xffffffff;
  for (let i = 0; i < data.length; i++) {
    crc ^= data[i];
    for (let j = 0; j < 8; j++) {
      crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1;
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

export interface ParsedDicomFrame {
  frameIndex: number;
  flags: number;
  data: ArrayBuffer;
  isValid: boolean;
}

export function parseDicomFrame(raw: ArrayBuffer): ParsedDicomFrame | null {
  try {
    const view = new DataView(raw);
    let offset = 0;

    if (raw.byteLength < 13) return null;

    const frameIndex = view.getUint32(offset, true);
    offset += 4;

    const flags = view.getUint8(offset);
    offset += 1;

    const dicomDataLength = view.getUint32(offset, true);
    offset += 4;

    if (raw.byteLength < offset + dicomDataLength + 4) return null;

    const dicomData = raw.slice(offset, offset + dicomDataLength);
    offset += dicomDataLength;

    const expectedCrc32 = view.getUint32(offset, true);

    const dataBytes = new Uint8Array(dicomData);
    const computedCrc32 = crc32(dataBytes);
    const isValid = computedCrc32 === expectedCrc32;

    return { frameIndex, flags, data: dicomData, isValid };
  } catch {
    return null;
  }
}

export function handleDicomFrame(rawFrame: ArrayBuffer): void {
  const parsed = parseDicomFrame(rawFrame);
  if (!parsed || !parsed.isValid) return;

  const { frameIndex, flags, data } = parsed;

  const isKeyFrame = (flags & 0x01) !== 0;
  const mapping = indexMapper.addFrame(frameIndex, `uid_${frameIndex}`);

  if (frameCache.size >= MAX_CACHE_SIZE) {
    const oldestKey = frameCache.keys().next().value;
    if (oldestKey !== undefined) {
      frameCache.delete(oldestKey);
    }
  }
  frameCache.set(frameIndex, data);

  const store = useDicomStore.getState();
  store.addFrameMapping(mapping);

  if (isKeyFrame || frameIndex === 0) {
    store.setTotalFrames(Math.max(store.totalFrames, mapping.sessionFrameIndex + 1));
  }
}

export function getCachedFrame(frameIndex: number): ArrayBuffer | undefined {
  return frameCache.get(frameIndex);
}

export function clearFrameCache(): void {
  frameCache.clear();
  indexMapper.clear();
}
