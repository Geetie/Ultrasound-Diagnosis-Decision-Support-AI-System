"""A.88 T4-⑦ 报告（**不是删除**）：实体匹配集合 ``entity_matching`` 无写入方。

## 现状取证（本文件就是证据，可复跑）

``ensure_entity_matching_collection`` 与 ``settings.rag_entity_matching_collection``
在**整个仓库**的引用只有四类：

| 位置 | 性质 |
|---|---|
| ``app/embedding/index_factory.py:213-244`` | 定义（建集合） |
| ``app/embedding/index_factory.py:209`` | ``__all__`` 导出 |
| ``app/config/settings.py`` | 配置项本身 |
| ``tests/unit/emb/test_index_factory.py`` | 测试 |

**没有任何生产调用方**：没有任何代码往里写向量（``upsert``）、也没有任何代码
按它检索。它与主知识库检索 collection（``settings.rag_collection``）完全独立。

## 架构含义（为什么这不是"小事"）

* 设计文档 ``01-系统架构设计.md`` §"Gate 0A 强制执行项"要求实体近似匹配迁到
  **Qdrant 的独立 collection**；而本函数建在 **PGVector** 上且注释写"PGVector 专用"。
  也就是说：**代码实现与已记录的架构裁决方向不一致**（不只是"没接线"）；
* 它同时也是 §3.3 那条「实体匹配集合 / RAG 三指标 无写入方 / 无生产者」的
  **实体匹配部分**（RAG 三指标已由 T4-⑤ 接上真实生产者）。

## 为什么不删（按 AGENTS.md「不删既有死代码，要删先说明」）

删掉它会动 ``app/embedding/index_factory.py`` 的公开导出与 3 个既有测试，
且**真正该做的是决定方向**：要么按 Gate 0A 迁到 Qdrant 后接写入方，
要么正式废止并删壳。这是架构裁决，不是本轮"接线"能做的决定。

本文件因此**只做三件事**：
1. 把"零写入方"这一事实钉成可复跑断言（人一改就会红，逼着更新报告）；
2. 记录删/接的两条路径与各自前置条件（给下一个人）；
3. 明确"本项**未完成**"的边界 —— 不伪装成"已修"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.track_emb]

_REPO = Path(__file__).resolve().parents[3]
_APP = _REPO / "app"


def _sources() -> dict[Path, str]:
    return {
        path: path.read_text(encoding="utf-8", errors="ignore")
        for path in _APP.rglob("*.py")
    }


class TestEntityMatchingIsDeadShell:
    def test_no_production_writer_or_reader(self):
        """全 ``app/`` 里除定义处外**没有任何**调用/写入方（事实断言）。

        一旦有人接上写入方（或按它检索），本用例会红 —— 那时请把本文件的
        报告改为"已接线"，并在 16 号附录 A 登记。
        """
        offenders: list[str] = []
        for path, text in _sources().items():
            rel = path.relative_to(_REPO).as_posix()
            # 定义处与配置文件本身不算"调用方"
            if rel in {
                "app/embedding/index_factory.py",
                "app/config/settings.py",
            }:
                continue
            for needle in (
                "ensure_entity_matching_collection",
                "rag_entity_matching_collection",
            ):
                if needle in text:
                    offenders.append(f"{rel}: {needle}")

        assert offenders == [], (
            "实体匹配集合出现了新的调用方（不再是死壳）→ 请更新本文件与台账："
            + "；".join(offenders)
        )

    def test_definition_still_exists(self):
        """壳还在（我们没删）—— 删除必须单独决策，见模块文档。"""
        source = (_APP / "embedding" / "index_factory.py").read_text(encoding="utf-8")
        assert "async def ensure_entity_matching_collection" in source
        assert '"ensure_entity_matching_collection"' in source  # __all__ 导出

    def test_definition_targets_pgvector_not_qdrant(self):
        """实现与已记录的 Gate 0A 裁决**方向不一致**（Qdrant 独立 collection）。

        这条断言是"报告"的一部分：它把"代码与架构文档冲突"这个事实钉住，
        直到有人做出裁决（迁 Qdrant 或废止）。
        """
        source = (_APP / "embedding" / "index_factory.py").read_text(encoding="utf-8")
        start = source.index("async def ensure_entity_matching_collection")
        body = source[start : start + 1500]
        assert "PGVector" in body, "注释不再声明 PGVector 专用 → 请复核本报告"
        assert "settings.rag_entity_matching_collection" in body


class TestHandoffNotes:
    """把"下一步两条路径 + 前置条件"写成可执行断言（防止报告只活在聊天里）。"""

    def test_two_paths_are_documented(self):
        doc = (Path(__file__).read_text(encoding="utf-8"))
        assert "迁到 Qdrant 后接写入方" in doc
        assert "正式废止并删壳" in doc

    def test_report_states_not_done(self):
        """本项**未完成**：不许在别的文档里被写成"已修"。"""
        doc = Path(__file__).read_text(encoding="utf-8")
        assert "本项**未完成**" in doc
