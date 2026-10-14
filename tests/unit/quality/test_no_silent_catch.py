"""零静默守卫（约定层 · 后端）：全仓 ``except`` 静默失败审计（ast 判定 + 存量棘轮）。

为什么从"正则 + 7 文件白名单"升级（A.58，依据 20 号「零静默」研究设计）
----------------------------------------------------------------------
旧实现有两个结构性弱点：

1. **判定用正则**：``except`` 出现在字符串/注释里会误判；``body`` 只往后看 10 行、
   遇多行语句会截断。这与 A.55 在对比度工具上踩过的"正则/穷举静默失效"是同一类问题
   （守卫自己失明，却一直打印"通过"）。
2. **只看 7 个主路径文件**：新代码（新模块、新脚本）不在覆盖面内，而 20 号对
   ``zero-silence-guard`` 的 "ongoing" 定义正是"只要还在写代码就可能引入新静默路径"。

三条不变量
----------
1. **零容忍区**（``ZERO_TOLERANCE``，原白名单 7 个主路径文件）静默 catch 必须为 **0**；
2. 其余 git 跟踪的 ``*.py`` 不得超过基线（``silent_catch_baseline.json``，**只许下调**）
   —— 未登记的文件默认 0，即"新代码不许静默"；
3. 实测低于基线 → 失败并提示下调（锁住成果，与 ``test_ruff_ratchet`` 同口径）。

什么算"静默"（ast 判据）
------------------------
遍历 ``ExceptHandler`` 处理体的每条语句，**找不到任何非静默证据**即判为静默。
非静默证据（任一即算）：

* 调用 ``logger.*`` / ``logging.*`` / ``log.*``（结构化/标准日志）；
* 调用 ``print(...)``，或 ``warnings.warn(...)``；
* 任何 ``raise``；
* 把 ``error=`` 作为关键字实参传出去（结构化日志的兜底形态）；
* 把失败写进名字含 error/warn/issue/problem/fail 的变量或属性
  （``errors.append(...)``、``self.last_error = ...``）。

纳入 ``with contextlib.suppress(...)``：它与裸 ``except: pass`` 语义等价，
只查 ``except`` 会让它成为绕过守卫的后门。

显式豁免：``except ...:  # silent-ok: 理由``
------------------------------------------
可选依赖探测等**正当**的静默分支用行内指令豁免，理由必填（≥4 字符）便于审计与 grep：

    try:
        import ujson as json          # noqa: F401
    except ImportError:  # silent-ok: 可选加速依赖，缺失不影响语义
        pass

统计范围与基线
--------------
范围＝``git ls-files '*.py'``（CI 是干净检出；本地未跟踪探针不参与，避免计数漂移
—— 与 ``test_ruff_ratchet`` ⑦ 的口径重定一致）。

**当前基线：0 条 / 0 个文件（2026-10-14 A.62「全仓归零」）**。演进：
A.58 首次以 ast 全仓口径登记 **450/199** → A.60 收敛 Tier A（影像/推理/实时/安全/基础设施
14 个文件归零 + 2 处 `suppress` 后门）**348/178** → A.62 批 2 三段收敛 **263/132**
→ A.62 全仓归零 **0/0**（263 处：app/ 43、tests/ 97、scripts/ 与 lab 等 123）。

A.62 的加固方式：由**本文件的 ast 判据**（不另立清单）驱动一个一次性加固器，
对被判为静默的处理体在**开头插入** ``logger.warning("<函数>: 未预期异常，按原逻辑降级",
exc_info=True)``（必要时补 `import logging` 与模块级 logger），**不改任何降级返回值与
控制流**；`# silent-ok: 理由` 的豁免一仍其旧。附带成果：19 处 `except: pass` 因此不再是
SIM105（`ruff` 基线已同步下调 37 → 18）。

重算：

    python tests/unit/quality/test_no_silent_catch.py     # 打印当前各文件静默 catch 数

把输出的 ``counts`` 写回同名 JSON 即可（**只许下调**；每次重定在 16 号附录写清来源）。
"""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE_FILE = Path(__file__).with_name("silent_catch_baseline.json")

# 零容忍区：医生主链路上的生产代码，必须 0 静默 catch（原白名单口径不变、判定变严）
ZERO_TOLERANCE = [
    "app/services/diagnosis_pipeline.py",
    "app/services/diagnosis_worker.py",
    "app/services/safety_gateway_client.py",
    "app/fusion/layered_validator.py",
    "app/fusion/self_rag.py",
    "app/rag/retriever.py",
    "app/rag/service.py",
]

# 日志/告警类调用：`<这些名字>.<方法>(...)` 视为已记录
_LOG_OBJECTS = {"logger", "log", "logging", "_logger", "_log", "structlog"}
# 告警类方法名（warnings.warn / logger.exception 等），以及 <obj>.warn(...)
_ALERT_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}
# 裸函数名（直接调用）
_ALERT_FUNCS = {"print", "warn", "warning", "add_note"}
# 变量/属性名里出现这些词 = 在记录失败
_FAILURE_WORDS = ("error", "warn", "issue", "problem", "failed", "failure")
# 行内豁免指令（必须带理由）
_SILENT_OK = "# silent-ok:"


def baseline() -> dict[str, int]:
    """读取存量基线（文件 → 静默 catch 数）。缺文件/格式错即失败，不静默跳过。"""
    assert BASELINE_FILE.is_file(), (
        f"缺基线文件 {BASELINE_FILE.name}（见文件头「统计范围与基线」节的重算方式）"
    )
    data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    counts = data.get("counts")
    assert isinstance(counts, dict), f"{BASELINE_FILE.name} 格式不对：应有 counts 字典"
    return {str(k): int(v) for k, v in counts.items()}


def _tracked_python_files() -> list[str]:
    """git 跟踪的全部 .py（相对仓库根的 POSIX 路径，已排序）。"""
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(p for p in out.split() if p)


def _name_mentions_failure(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in _FAILURE_WORDS)


def _call_is_evidence(node: ast.Call) -> bool:
    if any(kw.arg == "error" for kw in node.keywords):
        return True
    func = node.func
    if isinstance(func, ast.Attribute):
        if func.attr in _ALERT_METHODS or _name_mentions_failure(func.attr):
            return True
        # 接收者也可能是"失败容器"：errors.append(...) / self._errors.record(...)
        receiver = func.value
        if isinstance(receiver, ast.Name):
            if _name_mentions_failure(receiver.id):
                return True
            return receiver.id in _LOG_OBJECTS
        if isinstance(receiver, ast.Attribute):
            return _name_mentions_failure(receiver.attr)
        return False
    if isinstance(func, ast.Name):
        return func.id in _ALERT_FUNCS or _name_mentions_failure(func.id)
    return False


def _statement_is_evidence(stmt: ast.stmt) -> bool:
    """单条语句是否构成"非静默证据"（递归看子节点，覆盖嵌套表达式）。"""
    for node in ast.walk(stmt):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call) and _call_is_evidence(node):
            return True
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                items = list(target.elts) if isinstance(target, ast.Tuple) else [target]
                for one in items:
                    if isinstance(one, ast.Name) and _name_mentions_failure(one.id):
                        return True
                    if isinstance(one, ast.Attribute) and _name_mentions_failure(one.attr):
                        return True
    return False


def _handler_is_silent(handler: ast.ExceptHandler) -> bool:
    """处理体里没有任何证据 → 静默。"""
    return not any(_statement_is_evidence(s) for s in handler.body)


def _is_suppress_call(call: ast.Call) -> bool:
    """``suppress(...)`` / ``contextlib.suppress(...)``。"""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "suppress"
    return isinstance(func, ast.Attribute) and func.attr == "suppress"


def _silent_span(node: ast.stmt) -> list[ast.stmt] | None:
    """该语句是否构成"静默吞异常"的载体；是则返回其处理体语句表。

    覆盖两种等价形态（**必须都覆盖**：只查 ``except`` 会让
    ``with contextlib.suppress(Exception): pass`` 成为绕过守卫的后门）：
    * ``except ...:`` 处理体；
    * ``with suppress(...):`` 块体。
    """
    if isinstance(node, ast.ExceptHandler):
        return None if not _handler_is_silent(node) else node.body
    if isinstance(node, ast.With):
        suppressed = any(
            isinstance(item.context_expr, ast.Call) and _is_suppress_call(item.context_expr)
            for item in node.items
        )
        if suppressed and not any(_statement_is_evidence(s) for s in node.body):
            return node.body
    return None


def _is_waived(lines: list[str], lineno: int) -> bool:
    """``except`` 行带 ``# silent-ok: 理由`` → 豁免（理由必填，便于审计）。"""
    if not (1 <= lineno <= len(lines)):
        return False
    line = lines[lineno - 1]
    if _SILENT_OK not in line:
        return False
    return len(line.split(_SILENT_OK, 1)[1].strip()) >= 4


def silent_handlers(rel_path: str) -> list[tuple[int, str]]:
    """返回该文件里静默吞异常的 ``(行号, 处理体首行摘要)``（含 ``suppress`` 块）。"""
    path = REPO_ROOT / rel_path
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:  # pragma: no cover - 语法错会在别处更早暴露
        raise AssertionError(f"{rel_path} 解析失败：{exc}") from exc
    lines = src.splitlines()
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ExceptHandler, ast.With)):
            continue
        body = _silent_span(node)
        if body is None or _is_waived(lines, node.lineno):
            continue
        first = body[0] if body else None
        summary = ast.unparse(first).strip()[:60] if first is not None else "<empty>"
        found.append((node.lineno, summary))
    return found


def counts() -> dict[str, int]:
    """按文件统计（只保留 >0 的），供棘轮与维护脚本使用。"""
    out: dict[str, int] = {}
    for rel in _tracked_python_files():
        n = len(silent_handlers(rel))
        if n:
            out[rel] = n
    return out


def _spans_in(src: str) -> list[int]:
    """对源码片段跑一遍判据，返回被判定为"静默"的行号（自测用）。"""
    tree = ast.parse(src)
    lines = src.splitlines()
    out: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ExceptHandler, ast.With)):
            continue
        if _silent_span(node) is not None and not _is_waived(lines, node.lineno):
            out.append(node.lineno)
    return out


class TestGuardItself:
    """守卫的反证：**判据必须真的能响**（否则就是"永远打印通过"的假守卫）。"""

    def test_silent_forms_are_flagged(self):
        # ① 裸 except ② except + 仅有注释/占位 ③ suppress 后门（只查 except 会漏掉它）
        assert _spans_in("try:\n    f()\nexcept Exception:\n    pass\n") == [3]
        assert _spans_in("try:\n    f()\nexcept Exception:\n    # 忽略\n    x = 0\n") == [3]
        assert _spans_in("with contextlib.suppress(Exception):\n    pass\n") == [1]

    def test_evidence_forms_are_not_flagged(self):
        assert _spans_in("try:\n    f()\nexcept Exception as e:\n    logger.warning('x', error=e)\n") == []
        assert _spans_in("try:\n    f()\nexcept Exception:\n    raise\n") == []
        assert _spans_in("try:\n    f()\nexcept Exception:\n    errors.append('x')\n") == []
        assert _spans_in("try:\n    f()\nexcept Exception:\n    print('x')\n") == []
        assert _spans_in("with contextlib.suppress(Exception):\n    logger.warning('x')\n") == []
        assert _spans_in("try:\n    f()\nexcept Exception:\n    raise RuntimeError('x')\n") == []

    def test_explicit_waiver_needs_a_reason(self):
        waived = "try:\n    f()\nexcept TimeoutError:  # silent-ok: 空轮询超时是正常空转\n    pass\n"
        assert _spans_in(waived) == []
        # 理由太短（<4 字符）不算豁免 —— 防止 `# silent-ok:` 变成万能开关
        shorty = "try:\n    f()\nexcept TimeoutError:  # silent-ok: ok\n    pass\n"
        assert _spans_in(shorty) == [3]


def test_zero_tolerance_zone_is_clean():
    """主链路生产代码不允许任何静默 catch（原白名单升为 0 容忍）。"""
    violations: list[str] = []
    for rel in ZERO_TOLERANCE:
        assert (REPO_ROOT / rel).is_file(), f"零容忍区文件不存在（路径被挪走了？）：{rel}"
        for lineno, summary in silent_handlers(rel):
            violations.append(f"{rel}:L{lineno} -> {summary}")
    assert violations == [], (
        "主链路静默失败（except 后既无日志也无 raise/降级记录）："
        + chr(10)
        + chr(10).join(violations)
    )


def test_no_file_grew_beyond_baseline():
    """全仓棘轮：任一文件的静默 catch 数不得超过基线（未登记 = 0）。"""
    base = baseline()
    grew: list[str] = []
    for rel, n in sorted(counts().items()):
        allowed = base.get(rel, 0)
        if n > allowed:
            detail = "；".join(f"L{ln} {s}" for ln, s in silent_handlers(rel)[:3])
            grew.append(f"{rel}: {allowed} -> {n}（{detail}）")
    assert grew == [], (
        "静默失败路径新增（except 后必须有日志/raise/降级记录；正当的静默分支用 "
        "`# silent-ok: 理由` 显式豁免）：" + chr(10) + chr(10).join(grew)
    )


def test_baseline_is_not_stale():
    """实测低于基线 → 要求下调，避免棘轮失真（锁住成果）。"""
    stale: list[str] = []
    actual = counts()
    for rel, allowed in sorted(baseline().items()):
        n = actual.get(rel, 0)
        if n < allowed:
            stale.append(f"{rel}: 基线 {allowed} → 实测 {n}（请把它下调到 {n}）")
    assert stale == [], (
        "存量已收敛，基线过期（棘轮失真会让新增债务藏进余量）：" + chr(10) + chr(10).join(stale)
    )


if __name__ == "__main__":  # pragma: no cover - 维护用：打印当前基线
    payload = {
        "_note": (
            "零静默守卫（约定层）存量基线：文件 → 静默 except 数。只许下调；"
            "未登记文件默认 0（新代码不许静默）。重算："
            "python tests/unit/quality/test_no_silent_catch.py"
        ),
        "_generated_at": "2026-10-14 A.62（全仓归零）",
        "counts": counts(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
