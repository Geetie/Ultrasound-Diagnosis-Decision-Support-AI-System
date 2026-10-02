#!/bin/bash
# golden 语料 v2：v1 (433) + 新指南 83 文档 = 516；queries 不变
set -u
python3 - <<'EOF'
import json, pathlib

src = pathlib.Path('/mnt/ee16/home/student03/uai/src/data/golden/kb_qa_golden_v1.json')
dst = pathlib.Path('/mnt/ee16/home/student03/uai/src/data/golden/kb_qa_golden_v2.json')
v12 = pathlib.Path('/mnt/ee16/home/student03/uai/src/data/kb_src_v12')
v13 = pathlib.Path('/mnt/ee16/home/student03/uai/src/data/kb_src_v13')

raw = json.loads(src.read_text(encoding='utf-8'))
old_ids = {c['source_doc_id'] for c in raw['corpus']}

v12_ids = set()
for organ in ('thyroid', 'liver', 'cardiac'):
    for p in (v12 / organ).glob('*.json'):
        v12_ids.add(p.stem)

new_docs = []
for organ in ('thyroid', 'liver', 'cardiac'):
    for p in sorted((v13 / organ).glob('*.json')):
        d = json.loads(p.read_text(encoding='utf-8'))
        if p.stem not in v12_ids and d['source_doc_id'] not in old_ids:
            new_docs.append({
                'chunk_id': f'{d["source_doc_id"]}#g2',
                'source_doc_id': d['source_doc_id'],
                'text': d['content'],
            })

raw2 = dict(raw)
raw2['corpus'] = raw['corpus'] + new_docs
dst.write_text(json.dumps(raw2, ensure_ascii=False), encoding='utf-8')
print('v1 corpus:', len(raw['corpus']), '-> v2 corpus:', len(raw2['corpus']),
      'added:', len(new_docs), 'queries:', len(raw2['queries']))
EOF
