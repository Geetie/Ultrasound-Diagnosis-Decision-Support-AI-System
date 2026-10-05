#!/usr/bin/env python3
"""跑前端的静态渲染守卫，**不依赖 pytest**。

为什么要有这个运行器
--------------------
`tests/unit/quality/test_frontend_render_guard.py` 已经把 A.28 钉住的 4 个 P0 静态形态
守住，但它只能通过 pytest 跑；而**真机部署门禁**（`deploy_acceptance_gate.sh`）
在服务器上执行，服务器 venv 里**没有 pytest**（实测：`No module named pytest`）。
为跑一个守卫而给生产 venv 装测试依赖是本末倒置。

守卫文件本身只 import `re` 与 `pathlib`、只用原生 `assert`，所以本运行器用
`importlib` 按文件路径加载它、逐个调用 `Test*` 类的 `test_*` 方法即可。
**判据只有一份** —— 守卫的判定逻辑仍然只写在测试文件里，本文件不做任何重复判定。

退出码：0 = 全过；1 = 有失败；2 = 环境错（守卫文件缺失 / 无法导入）。
"""
from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

# Windows 控制台默认 GBK，打印「渲染守卫」等中文会抛 UnicodeEncodeError
# ——崩在最后一行，恰好把判定结果吃掉（与 check_frontend_api_contract.py 同款事故）。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GUARD = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "unit"
    / "quality"
    / "test_frontend_render_guard.py"
)


def _load(path: Path):
    spec = importlib.util.spec_from_file_location("_render_guard", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {path} 创建 import spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cases(module) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for name in sorted(dir(module)):
        obj = getattr(module, name)
        if name.startswith("Test") and isinstance(obj, type):
            # 存**绑定方法**，不是实例 —— 实例不可调用（第一版就踩了，
            # 报"object is not callable"，且因为是用例逐个 try，
            # 会被当成 1 个失败混在真失败里，看不出是运行器自己的错）
            instance = obj()
            for meth in sorted(dir(obj)):
                if meth.startswith("test"):
                    out.append((f"{name}.{meth}", getattr(instance, meth)))
        elif name.startswith("test_") and callable(obj):
            out.append((name, obj))
    return out


def main() -> int:
    if not GUARD.is_file():
        print(f"[FAIL] 守卫文件不存在：{GUARD}")
        return 2
    try:
        module = _load(GUARD)
    except Exception:
        print(f"[FAIL] 守卫文件无法导入：{GUARD}")
        print(traceback.format_exc())
        print("提示：守卫文件当前只应依赖 re/pathlib；若新增了第三方 import，")
        print("      服务器 venv 装不起，会在这里被明确挡下而不是静默跳过。")
        return 2

    cases = _cases(module)
    if not cases:
        print("[FAIL] 守卫文件里没找到任何用例 —— 文件可能被清空了")
        return 2

    passed = 0
    failed: list[tuple[str, str]] = []
    for label, case in cases:
        try:
            case()
            passed += 1
            print(f"  [PASS] {label}")
        except Exception:
            failed.append((label, traceback.format_exc()))
            print(f"  [FAIL] {label}")

    print("")
    print(f"渲染守卫（静态层）：PASS={passed} FAIL={len(failed)}")
    for label, tb in failed:
        print(f"--- {label} ---")
        print(tb)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
