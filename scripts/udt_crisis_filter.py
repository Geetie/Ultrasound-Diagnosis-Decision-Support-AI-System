# -*- coding: utf-8 -*-
"""Task 2: filter cardiac crisis terms from UDT LLM extraction output."""
import json

SRC = '/mnt/ee16/home/student03/uai/src/data/udt/llm_terms.json'
DST = '/mnt/ee16/home/student03/uai/src/data/udt/crisis_terms_cardiac.json'

d = json.load(open(SRC, encoding='utf-8'))
kw = ('填塞', '夹层', '穿孔', '断裂', '破裂', '赘生物', '血栓', '大量',
      '重度', '梗死', '撕裂', '动脉瘤')
out = {t: c for t, c in d.get('cardiac', {}).items() if any(k in t for k in kw)}
json.dump(out, open(DST, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('cardiac total:', len(d.get('cardiac', {})))
print('crisis terms:', len(out))
for t in sorted(out):
    print(' -', t)
