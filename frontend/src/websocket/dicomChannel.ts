import { DicomIndexMapper } from '@/utils/dicomIndexMapper';
import { useDicomStore } from '@/stores/dicomStore';
import { reportWarn } from '@/utils/errorReport';

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
  } catch (err) {
    // A.60：解析异常原先静默返回 null（与"帧头长度不足"分支一起被丢弃）——
    // 影像少帧而界面照常，属"占位被当成真实"。留痕，丢弃行为不变。
    reportWarn('dicom-channel', '二进制帧解析异常（该帧已丢弃）', err);
    return null;
  }
}

/**
 * 被丢弃的帧计数（A.60 观测用）。
 *
 * 丢帧本身是**设计内**行为（长度不足 / CRC 不匹配的帧必须丢），但**必须可观测**：
 * 每帧都打日志会淹没控制台，所以按「首帧 + 每 50 帧」上报一次，便于判断
 * "是偶发丢帧还是整条通道都在丢"。
 */
let droppedFrames = 0;

export function handleDicomFrame(rawFrame: ArrayBuffer): void {
  const parsed = parseDicomFrame(rawFrame);
  if (!parsed || !parsed.isValid) {
    droppedFrames += 1;
    if (droppedFrames === 1 || droppedFrames % 50 === 0) {
      reportWarn(
        'dicom-channel',
        `已丢弃 ${droppedFrames} 个无效影像帧（帧头长度不足或 CRC 不匹配，影像可能缺帧）`,
        parsed ? { frameIndex: parsed.frameIndex } : undefined,
      );
    }
    return;
  }

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
