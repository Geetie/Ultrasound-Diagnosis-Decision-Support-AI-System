# -*- coding: utf-8 -*-
"""检查 .env 里数据库/Redis 密码是否含 URL 特殊字符（不打印明文）。"""
import io
import re

s = io.open(".env", encoding="utf-8").read()

for key in ("REDIS_PASSWORD", "POSTGRES_PASSWORD"):
    m = re.search(rf"^{key}\s*=\s*(.+)$", s, re.M)
    if not m:
        print(key, "-> 未设置")
        continue
    pw = m.group(1).strip()
    special = [c for c in pw if c in ':/@?#[]%&']
    print(f"{key}: len={len(pw)}  首尾引号={pw[0] in (chr(34), chr(39))}  URL特殊字符={special}")

url = re.search(r"^REDIS_URL\s*=\s*(.+)$", s, re.M)
print("REDIS_URL host:", re.sub(r"//[^@]*@", "//***@", url.group(1).strip()) if url else "未设置")
