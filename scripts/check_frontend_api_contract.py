"""前端 ↔ 后端 API 契约对账（交付前必查）。

为什么需要
----------
前端 React 应用（151 文件、Cornerstone3D 影像、报告页）**从未被真机验证过
能否跑通一次诊断**——只验了 `/ui/` 返回 200，而 SPA 返回 200 与"能登录、
能提交诊断、能拿到报告"是两件事。历史上有过整类缺陷：前端调用的路径后端
根本不存在（"实现了但从未接线"），页面落进 catch 渲染空列表，测试因 mock
照样全绿。

做法
----
1. 用 AST 扫 ``frontend/src``：抽出所有 ``fetch/axios/api.get/api.post`` 调用的
   路径字面量（拼接式的动态路径单独列出，那些无法静态判定）；
2. 读后端 ``/openapi.json`` 的 paths；
3. 做差集，输出**前端有、后端无**的路径（= 断链）与**后端有、前端无**的
   （= 可能是给脚本/edge 用的，不算缺陷，仅供参考）。

运行：``python scripts/check_frontend_api_contract.py``（在服务器上跑，此时
API_BASE 的 127.0.0.1:18000 可达），或用 ``--openapi <文件>`` 离线对账。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import urllib.request
from collections import defaultdict
from pathlib import Path

FRONTEND_SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
DEFAULT_API_BASE = "http://127.0.0.1:18000"

# 前端请求封装里可能出现的调用形态
_CALL_NAMES = {"fetch", "get", "post", "put", "patch", "delete", "request"}
_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _literal(node: ast.AST) -> str | None:
    """取字符串字面量；拼接式（模板/加号）返回 None（动态路径无法静态判定）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def collect_frontend_paths() -> tuple[set[str], list[tuple[str, int]]]:
    """返回 (静态可判定的 API 路径集合, 动态拼接的调用点列表)。

    **为什么用正则而不是 ast.parse**（实测踩坑两轮才定位）：
    1. 前端是 TypeScript/TSX，``ast.parse`` 解析不了 TS 类型注解与 JSX →
       每个文件都 SyntaxError。第一版用 ``except SyntaxError: continue`` 静默跳过，
       结果扫出 0 个路径还输出"✅ 全部匹配"的**假通过结论**——比不检查更糟。
    2. 调用形态是 ``apiClient.get('/xxx')``（axios，baseURL=/api/v1），
       路径字面量不带 /api/v1 前缀。
    所以：正则抓 ``<fn>('<path>')``，命中失败必须显式报出，不许静默。
    """
    found: set[str] = set()
    dynamic: list[tuple[str, int]] = []
    call_re = re.compile(
        r"""\b(?:apiClient|api|client|http|axios|request|apiService)\s*\.\s*"""
        r"""(get|post|put|patch|delete|request)\s*(?:<[^>]*>)?\s*\(\s*"""
        r"""(['"`])([^'"`]+)\2""",
        re.IGNORECASE,
    )
    # 模板字符串拼路径（`/sessions/${id}/reports`）→ 记为动态
    dynamic_re = re.compile(
        r"""\b(?:apiClient|api|client|http|axios|request|apiService)\s*\.\s*"""
        r"""(get|post|put|patch|delete|request)\s*(?:<[^>]*>)?\s*\(\s*`([^`]*)`"""
    )

    for fp in FRONTEND_SRC.rglob("*"):
        if fp.suffix not in (".ts", ".tsx") or not fp.is_file():
            continue
        if any(s in fp.parts for s in ("node_modules", "dist")):
            continue
        rel = fp.relative_to(FRONTEND_SRC.parent).as_posix()
        text = fp.read_text(encoding="utf-8", errors="replace")
        for m in call_re.finditer(text):
            lit = m.group(3)
            if lit.startswith("http"):
                continue
            path = lit.split("?")[0]
            if not path.startswith("/"):
                dynamic.append((rel, text[:m.start()].count("\n") + 1))
                continue
            if not path.startswith("/api/"):
                path = "/api/v1" + path
            found.add(path.rstrip("/") or "/")
        for m in dynamic_re.finditer(text):
            dynamic.append((rel, text[:m.start()].count("\n") + 1))

    if not found and not dynamic:
        print("⚠️ 未扫到任何 API 调用——前端调用形态可能又变了，"
              "本脚本的正则需要同步更新（别把这种空结果当成通过）")
    return found, dynamic


def normalize(path: str) -> str:
    """路径参数统一成 {}，便于对齐。

    两边写法不同：后端 ``/x/{id}``，前端模板串 ``/x/${id}``——都要归一，
    否则会把同一路径误报成"断链"（首次实跑 8 个全是这种假阳性）。
    """
    p = re.sub(r"\$\{[^}]+\}", "{}", path)   # 前端模板串
    p = re.sub(r"\{[^}]+\}", "{}", p)          # 后端路径参数
    return p.rstrip("/") or "/"


def fetch_backend_paths(api_base: str, openapi_file: str | None) -> set[str]:
    if openapi_file:
        spec = json.loads(Path(openapi_file).read_text(encoding="utf-8"))
    else:
        with urllib.request.urlopen(f"{api_base}/openapi.json", timeout=60) as resp:
            spec = json.loads(resp.read().decode("utf-8"))
    return {p.rstrip("/") or "/" for p in spec.get("paths", {})}


def main() -> int:
    ap = argparse.ArgumentParser(description="前后端 API 契约对账")
    ap.add_argument("--api-base", default=DEFAULT_API_BASE,
                    help="后端地址（默认本机 18000，需在服务器上跑）")
    ap.add_argument("--openapi", default=None,
                    help="离线模式：直接读本地 openapi.json 文件")
    args = ap.parse_args()

    if not FRONTEND_SRC.is_dir():
        print(f"MISSING_FRONTEND_SRC: {FRONTEND_SRC}")
        return 2

    fe_paths, dynamic = collect_frontend_paths()
    be_paths = fetch_backend_paths(args.api_base, args.openapi)
    be_norm = {normalize(p) for p in be_paths}

    fe_norm_map: dict[str, set[str]] = defaultdict(set)
    for p in fe_paths:
        fe_norm_map[normalize(p)].add(p)

    missing = {k: v for k, v in fe_norm_map.items() if k not in be_norm}

    print(f"前端静态可判定调用点: {len(fe_paths)} 个路径（去重后 {len(fe_norm_map)}）")
    print(f"动态拼接调用点: {len(dynamic)} 处（无法静态判定，需人工确认）")
    print(f"后端路由: {len(be_paths)} 个")
    print("")
    if missing:
        print(f"❌ 前端调用但后端不存在（断链）: {len(missing)} 个")
        for norm, variants in sorted(missing.items()):
            print(f"   {norm}   ← 前端写法: {sorted(variants)}")
    else:
        print("✅ 前端静态路径全部有后端对应路由")
    if dynamic:
        print("")
        print("动态路径调用点（前 15，需人工确认是否有对应路由）:")
        for rel, line in dynamic[:15]:
            print(f"   {rel}:{line}")

    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
