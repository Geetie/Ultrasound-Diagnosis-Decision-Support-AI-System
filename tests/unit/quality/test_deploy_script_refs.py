"""部署脚本的**悬空引用**守卫。

问题背景（同类已发生两次，都是交付前的真机阻塞）
---------------------------------------------------
1. ``scripts/vllm_server.py`` 在「收敛与部署脚本入库」里被当探针删除，
   但 ``server_start_vllm_engine.sh`` 仍在引用它 → 真机 8002 起不来
   （``can't open file '.../scripts/vllm_server.py'``）。
2. ``scripts/llama_cpp_server.py`` 同样被删，``server_start_judge.sh``
   引用它 → 判官 8006 起不来。

两次都是**部署脚本在、它调用的 python 文件不在**，静态检查（ruff/pytest）
完全测不出来：ruff 只看 Python 语法，pytest 不执行 shell 脚本。

本守卫做静态交叉引用：把 ``scripts/*.sh`` 里出现的
``scripts/xxx.py`` / ``$SRC/scripts/xxx.py`` 引用抽出来，逐个断言文件存在。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"

# scripts/foo.py 或 $SRC/scripts/foo.py 或 ~/uai/src/scripts/foo.py
_REF_RE = re.compile(r"""scripts/([A-Za-z0-9_./-]+\.py)""")

# 这些脚本是"探针/临时"性质，不该被部署脚本引用（若真需要应先转正）
_ALLOW_MISSING_SUFFIX = ("_",)  # 下划线开头的一律是探针，不在守卫范围


def _referenced_scripts() -> set[str]:
    refs: set[str] = set()
    for sh in SCRIPTS_DIR.glob("*.sh"):
        try:
            text = sh.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.warning("_referenced_scripts: 未预期异常，按原逻辑降级", exc_info=True)
            continue
        for m in _REF_RE.finditer(text):
            refs.add(m.group(1))
    return refs


class TestNoDanglingScriptReferences:
    def test_shell_scripts_exist(self):
        assert SCRIPTS_DIR.is_dir(), f"找不到 scripts 目录：{SCRIPTS_DIR}"

    def test_referenced_python_files_exist(self):
        """正式脚本的引用必须存在；下划线开头的探针引用只报告不断言。

        区分理由：清理 `/tmp` 与 `scripts/_*.py` 探针是**正常维护动作**，
        而服务入口（无下划线）被删是**事故**——判官脚本引用的
        llama_cpp_server.py 消失就直接让 8006 起不来。
        """
        missing_formal: list[str] = []
        missing_probe: list[str] = []
        for ref in sorted(_referenced_scripts()):
            name = ref.rsplit("/", 1)[-1]
            candidate = (SCRIPTS_DIR / ref).resolve()
            if candidate.is_file():
                continue
            if name.startswith("_"):
                missing_probe.append(ref)
            else:
                missing_formal.append(ref)
        if missing_probe:
            print(f"ℹ️ 探针类悬空引用（清理探针导致，非事故，需人工确认无用后可删引用）：{missing_probe}")
        assert not missing_formal, (
            "正式部署脚本引用了不存在的 Python 文件（真机表现：服务起不来，"
            f"且 ruff/pytest 都测不出）：{missing_formal}"
        )

    def test_key_entrypoints_present(self):
        """关键部署入口必须存在（这些是服务启动的根）。"""
        must_exist = [
            "vllm_server.py",          # 8002 引擎包装
            "llama_cpp_server.py",     # 判官 8006
        ]
        absent = [m for m in must_exist if not (SCRIPTS_DIR / m).is_file()]
        assert not absent, f"关键服务入口缺失：{absent}"

    def test_start_scripts_have_health_check(self):
        """启动脚本应带探活（起没起来要能自证，不靠人肉看）。"""
        missing_hc = []
        for name in ("server_start_vllm_engine.sh", "server_start_judge.sh"):
            p = SCRIPTS_DIR / name
            if not p.is_file():
                missing_hc.append(f"{name}(文件不存在)")
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            if "health" not in text:
                missing_hc.append(f"{name}(无探活)")
        assert not missing_hc, f"启动脚本缺探活：{missing_hc}"
