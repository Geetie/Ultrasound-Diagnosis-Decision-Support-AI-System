"""守卫：依赖清单必须覆盖 `app/` 与 `safety_gateway/` 的第三方导入。

## 为什么需要它（2026-09-28 真机部署暴露）

真机上按 `requirements.txt` 装完依赖后，应用**启动即崩**：
首个报错 `ModuleNotFoundError: pydicom`（来自 `app/api/v1/imaging.py`）。
逐个补装后发现共 **10 个包**被 `app/` 直接 import 却从不在任何清单里 —— 它们只在
长期开发的那台机器上"顺手装过"，所以从未暴露。

这类缺陷**无法靠运行测试发现**：单测环境里包早就装好了，测试永远绿。唯一根治手段
就是静态检查"代码里 import 的第三方模块集合 ⊆ 清单声明的包集合"。

## 规则

1. **硬导入必须被声明**：不在 `try/except ImportError` 里的第三方 `import` 必须能在
   `requirements*.txt` 里找到（模块名 → 分发包名有映射表）。
2. **软导入可以缺省，但必须"真的是软的"**：登记在 `_OPTIONAL_UNDECLARED` 里的模块，
   必须所有导入点都在 `try/except ImportError` 内 —— 否则它就是个会炸启动的硬依赖，
   只是被藏起来了。
3. 扫描必须非空（防止扫描逻辑写坏后"永远通过"）。
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
from typing import ClassVar

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCAN_PACKAGES = ("app", "safety_gateway")
REQUIREMENT_FILES = ("requirements.txt", "requirements-emb.txt")

# 本地包 / 工具目录：不算第三方。
_LOCAL_TOP_LEVEL = {
    "app",
    "safety_gateway",
    "alembic",
    "tests",
    "scripts",
    "edge",
}

# 导入名（小写）→ PyPI 分发包名（小写、`_`→`-` 后比较）。
_MODULE_TO_PACKAGE = {
    "pil": "pillow",
    "yaml": "pyyaml",
    "qdrant_client": "qdrant-client",
    "rank_bm25": "rank-bm25",
    "pydantic_settings": "pydantic-settings",
    "prometheus_client": "prometheus-client",
    "pynvml": "pynvml",
    "opentelemetry": "opentelemetry-api",
    # jwt → PyJWT（2026-09-30 替换 python-jose，见 15-技术升级规划 附录D）；
    # bcrypt 为独立直依赖（app/security/auth.py 直接 import bcrypt）。
    "jwt": "pyjwt",
    "bcrypt": "bcrypt",
}

# 允许"未声明"的模块 —— 每一条都必须给出理由，且被测试校验其确实是软导入。
_OPTIONAL_UNDECLARED = {
    "pkuseg": (
        "PyPI 无 py3.12 wheel，源码构建在本机失败；"
        "app/knowledge/segmentation_validation.py 里是 try/except ImportError 软导入"
        "（退回 jieba → 再退回字符级），故不入清单"
    ),
    "json_repair": (
        "P0-2 约束解码的可选修复库；app/inference/grading_schema.py 里是"
        " try/except（ImportError + JSONDecodeError 双兜底）软导入——约束输出"
        "为单字段枚举 JSON，损坏概率极低，缺失时 extract_guided_grading 返回"
        " None 回退正则抽取器，故不入清单"
    ),
}


def _normalize(name: str) -> str:
    return name.strip().lower().replace("_", "-")


class _ImportCollector(ast.NodeVisitor):
    """收集第三方导入，并记录它们是否处在 try/except ImportError 内。"""

    _SOFT_EXCEPTIONS: ClassVar[set[str]] = {
        "ImportError",
        "ModuleNotFoundError",
        "Exception",
        "BaseException",
    }

    def __init__(self) -> None:
        self.all: set[str] = set()
        self.guarded: set[str] = set()
        self._inside_soft_try = 0

    def _add(self, top: str) -> None:
        if not top or top.startswith("_") or top in _LOCAL_TOP_LEVEL:
            return
        if top in sys.stdlib_module_names:
            return
        self.all.add(top)
        if self._inside_soft_try:
            self.guarded.add(top)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._add(alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            return
        if node.module:
            self._add(node.module.split(".")[0])

    def visit_Try(self, node: ast.Try) -> None:
        soft = False
        for handler in node.handlers:
            names: list[str] = []
            if handler.type is None:
                names = ["Exception"]
            elif isinstance(handler.type, ast.Name):
                names = [handler.type.id]
            elif isinstance(handler.type, ast.Tuple):
                names = [e.id for e in handler.type.elts if isinstance(e, ast.Name)]
            if set(names) & self._SOFT_EXCEPTIONS:
                soft = True
                break

        if soft:
            self._inside_soft_try += 1
            for stmt in node.body:
                self.visit(stmt)
            self._inside_soft_try -= 1
        else:
            for stmt in node.body:
                self.visit(stmt)

        for handler in node.handlers:
            for stmt in handler.body:
                self.visit(stmt)
        for stmt in node.orelse + node.finalbody:
            self.visit(stmt)


def _collect_imports() -> _ImportCollector:
    collector = _ImportCollector()
    for package in SCAN_PACKAGES:
        for path in (REPO_ROOT / package).rglob("*.py"):
            collector.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return collector


def startup_third_party_imports() -> set[str]:
    """AST 扫描：返回 `app/` 与 `safety_gateway/` 里的第三方顶层模块名。"""
    return _collect_imports().all


def soft_third_party_imports() -> set[str]:
    """只返回"处于 try/except ImportError 内"的第三方顶层模块名。"""
    return _collect_imports().guarded


def declared_requirements() -> set[str]:
    """解析 requirements*.txt，返回归一化后的分发包名集合。"""
    declared: set[str] = set()
    for filename in REQUIREMENT_FILES:
        path = REPO_ROOT / filename
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            name = re.split(r"[<>=!~\[;@ ]", line, maxsplit=1)[0]
            if name:
                declared.add(_normalize(name))
    return declared


def _package_of(module: str) -> str:
    return _normalize(_MODULE_TO_PACKAGE.get(module.lower(), module))


class TestRequirementsCoverStartupImports:
    def test_scan_is_not_vacuous(self):
        """扫描必须真的看到东西 —— 否则守卫会"永远通过"。"""
        found = startup_third_party_imports()

        assert len(found) > 20, f"扫描结果异常少（{len(found)}），扫描逻辑可能已失效"
        for must_have in ("fastapi", "sqlalchemy", "structlog", "redis", "pydantic"):
            assert must_have in found

    def test_every_hard_import_is_declared(self):
        missing = sorted(
            module
            for module in startup_third_party_imports()
            if _package_of(module) not in declared_requirements()
            and module not in _OPTIONAL_UNDECLARED
        )

        assert missing == [], (
            "以下第三方导入既不在 requirements*.txt 里，也没登记为软依赖 —— "
            f"全新环境会启动即崩：{missing}"
        )

    def test_optional_undeclared_are_really_soft(self):
        """登记为"可缺省"的模块，必须所有导入点都包在 try/except ImportError 内。"""
        soft = soft_third_party_imports()
        hard_violations = sorted(set(_OPTIONAL_UNDECLARED) - soft)

        assert hard_violations == [], (
            "以下模块被登记为可缺省，但它存在**未受保护**的导入点 —— "
            f"那会直接炸启动，必须装包或改成软导入：{hard_violations}"
        )

    def test_declared_set_is_parsable(self):
        declared = declared_requirements()

        assert len(declared) > 20
        assert "fastapi" in declared
        assert "pydantic-settings" in declared, "带连字符的包名必须能正确归一"
