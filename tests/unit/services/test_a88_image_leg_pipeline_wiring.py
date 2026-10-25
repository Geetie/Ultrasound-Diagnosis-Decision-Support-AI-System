"""A.88 守卫：影像检索腿必须**真的被管线接到**（否则它仍结构性不可达）。

## 为什么这份守卫必须存在（而不是只测 retriever 内部）

`retrieval-wiring` 交付了 `image_leg_scope_for_request()` + 一套 22 条的注入测试，
**但那个函数在 `app/` 里零生产调用方** —— 也就是说：函数本身全绿，
生产链路仍然"装了等于没装"。这正是本项目反复踩过的形状
（`process_full_pipeline` / `dispatch_all` / `build_dispatcher_with_thresholds`
都曾有"实现完整、无人调用"的记录）。

所以本守卫钉的是**接线**，不是函数：

1. `run_diagnosis_pipeline` 的源码里必须出现 `image_leg_scope_for_request`，
   且它**包住** dispatch 与 fusion（否则检索发生在作用域外，字节照样读不到）；
2. 作用域形状必须是 `async with`（退出必须由解释器保证 —— worker 协程复用，
   漏退出会把上一次判读的字节串给下一位病人，A.78 红线）；
3. 反证腿：把作用域从源码里去掉，第 1 条立刻红。

## 为什么不在这里做真机端到端

真机端到端（真的取到 DICOM 字节 → CLIP 嵌入 → 影像腿命中）依赖 8003 的
影像向量集合，而 `probe_image_leg()` 已证实 `collection_exists=false`。
所以本守卫只钉"接线形状"，真机验证单列为 🔬 项（见 16 号附录 A.88）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[3] / "app" / "services" / "diagnosis_pipeline.py"


def _src() -> str:
    return PIPELINE.read_text(encoding="utf-8")


class TestPipelineWiresImageLeg:
    def test_scope_helper_is_called_in_pipeline(self):
        """反证腿：从管线里删掉 `image_leg_scope_for_request` 即红。"""
        assert "image_leg_scope_for_request" in _src()

    def test_scope_is_async_with_not_bare_call(self):
        """必须以 `async with` 进入作用域（退出由解释器保证）。

        裸 `await attach_...(enter_scope=True)` 再手写 finally 也能跑，
        但 worker 协程复用下漏 finally 就是跨病人串号；这里钉住"不许退化成裸调用"。
        """
        assert "async with image_leg_scope_for_request(" in _src()

    def test_scope_wraps_both_dispatch_and_fusion(self):
        """作用域必须在**源码顺序上**同时罩住 dispatch 与 fusion 两个 span。

        判据用 AST 的嵌套关系（不是字符串包含）：`_safe_dispatch` 与 `_safe_fuse`
        两个调用节点都必须是 `image_leg_scope_for_request` 那个 `async with`
        语句的**后代节点**。这样"看起来包了但实际只包了一半"会被抓到。
        """
        tree = ast.parse(_src())

        scope_nodes: list[ast.AsyncWith] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncWith):
                continue
            for item in node.items:
                call = item.context_expr
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "image_leg_scope_for_request"
                ):
                    scope_nodes.append(node)

        assert scope_nodes, "管线里没有 `async with image_leg_scope_for_request(...)`"

        def _calls_in(node: ast.AST, name: str) -> bool:
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == name
                ):
                    return True
            return False

        wrapped = [
            n
            for n in scope_nodes
            if _calls_in(n, "_safe_dispatch") and _calls_in(n, "_safe_fuse")
        ]
        assert wrapped, (
            "影像腿作用域没有同时罩住 _safe_dispatch 与 _safe_fuse —— "
            "检索发生在专科智能体内部与融合层的指南比对里，只包一半等于没接"
        )


class TestSkipReasonIsObservable:
    def test_pipeline_logs_skip_reason(self):
        """取了但没取到字节时，必须留痕真实原因（不许静默跳过）。

        与 AGENTS.md 硬规则 8 同源：每个被跳过的分支必须留下自己的原因。
        """
        src = _src()
        assert "pipeline_image_leg_skipped" in src
        assert "skip_reason" in src


@pytest.mark.parametrize("dead_api", ["_fuse_with_span"])
def test_helpers_used_by_pipeline_actually_exist(dead_api: str):
    """源码卫生：不许调用不存在的辅助函数（我在本轮接线时真的写错过一次）。"""
    src = _src()
    if dead_api not in src:
        pytest.skip(f"{dead_api} 已不在管线里（历史误写已修）")
    tree = ast.parse(src)
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert dead_api in defined, f"管线调用了 {dead_api}，但文件里没有定义它"
