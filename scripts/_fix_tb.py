p = "scripts/server_faith_eval_async.py"
lines = open(p, encoding="utf-8").read().splitlines()
out = []
for l in lines:
    if 'item["error"] = traceback.format_exc()' in l:
        l = '        item["error"] = traceback.format_exc()[-350:]'
    if "import traceback" in l and "item[" not in l:
        continue
    out.append(l)
final = []
for l in out:
    final.append(l)
    if l.strip() == "except Exception as exc:":
        final.append("        import traceback")
open(p, "w", encoding="utf-8", newline="\n").write("\n".join(final) + "\n")
import ast

ast.parse(open(p, encoding="utf-8").read())
print("ok")
