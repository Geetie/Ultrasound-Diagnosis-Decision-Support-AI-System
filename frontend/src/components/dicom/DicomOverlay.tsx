import { useDicomStore } from '@/stores/dicomStore';

export default function DicomOverlay() {
  const { metadata } = useDicomStore();

  if (!metadata) return null;

  return (
    <div
      className="absolute inset-0 pointer-events-none"
      style={{ zIndex: 10 }}
    >
      <div
        className="absolute top-2 left-2"
        style={{
          background: 'rgba(10, 14, 26, 0.7)',
          padding: '4px 8px',
          borderRadius: 4,
          fontSize: 11,
          fontFamily: "'JetBrains Mono', monospace",
          lineHeight: 1.6,
        }}
      >
        <div style={{ color: '#94a3b8' }}>
          <span style={{ color: '#64748b' }}>Modality:</span> {metadata.modality}
        </div>
        <div style={{ color: '#94a3b8' }}>
          <span style={{ color: '#64748b' }}>Date:</span> {metadata.acquisitionDate}
        </div>
        <div style={{ color: '#94a3b8' }}>
          <span style={{ color: '#64748b' }}>Size:</span> {metadata.columns}×{metadata.rows}
        </div>
        {metadata.institutionName && (
          <div style={{ color: '#94a3b8' }}>
            <span style={{ color: '#64748b' }}>Inst:</span> {metadata.institutionName}
          </div>
        )}
      </div>

      <div
        className="absolute top-2 right-2"
        style={{
          background: 'rgba(10, 14, 26, 0.7)',
          padding: '4px 8px',
          borderRadius: 4,
          fontSize: 11,
          fontFamily: "'JetBrains Mono', monospace",
          lineHeight: 1.6,
        }}
      >
        <div
          style={{
            background: '#ef444430',
            color: '#ef4444',
            padding: '2px 6px',
            borderRadius: 3,
            fontSize: 10,
            marginBottom: 2,
          }}
        >
          ID已遮蔽
        </div>
        <div style={{ color: '#64748b', fontSize: 10 }}>
          ****** (患者ID已遮蔽)
        </div>
        <div style={{ color: '#64748b', fontSize: 10 }}>
          ****** (患者姓名已遮蔽)
        </div>
      </div>
    </div>
  );
}
