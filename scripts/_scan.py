import re
from pathlib import Path
hits = []
for p in list(Path('scripts').glob('*.py')) + list(Path('app').rglob('*.py')):
    s = p.read_text(encoding='utf-8', errors='ignore')
    for i, l in enumerate(s.splitlines(), 1):
        if re.search(r'urlopen|requests\.(post|get)\(|chat/completions|/v1/embeddings|embed_texts\(', l):
            tag = 'SYNC' if 'urlopen' in l or 'requests.' in l else 'CALL'
            hits.append(f'{p}:{i} [{tag}] {l.strip()[:95]}')
print(chr(10).join(hits[:45]))
print('total:', len(hits))
