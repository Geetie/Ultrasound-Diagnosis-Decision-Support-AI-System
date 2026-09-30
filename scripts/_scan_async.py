import re
from pathlib import Path
# 找 dispatcher 的 agent 调用方式
p = Path('app/dispatcher')
for f in p.rglob('*.py'):
    t = f.read_text(encoding='utf-8')
    for i, l in enumerate(t.splitlines(), 1):
        if re.search(r'gather|create_task|TaskGroup|await .*invoke|await .*dispatch', l):
            print(f'{f.name}:{i}: {l.strip()[:90]}')
