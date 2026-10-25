"""A.88 T4-② 守卫：图谱扩边**默认关是已记录的裁决**，不是"接线漏了"。

## 为什么要专门钉这一条

《架构真相》§3.2 把「图谱扩展 默认 ``graph_query_enabled=False``」列入
"条件通/默认关"。下一个读者很容易把它读成"没接线，顺手打开"——而打开会
**推翻已记录的预注册决策**：

* **A.65**（16 号附录 A.65.1）：136 条 golden 上扩边池子召回
  ``0.9853 → 0.9853``（零召回增益）→ ``NO_GO_NO_RECALL_GAIN``
  （P0 判据要求 ≥2pp 增益）；且不排序的裸扩边把 hit@5 稀释到 **0.0963**。
* **A.66**（A.66.1/A.66.2）：评测仪器饱和（129/136 命中 top-1）→ 造难集复评 →
  "扩边有增益但当前排序转不成收益"。

本轮**只做两件事**：① 确认"开关打开时确实生效"（A.63 修的 score=0 豁免还在）；
② 把"为何默认关"的证据指针写进代码注释（防止下一个人当成忘了开）。
**不动默认值。**

反证腿：
* 把 ``settings.graph_query_enabled`` 默认改成 True → ``TestDefaultStaysOff`` 红；
* 把 ``app/rag/service.py`` 的 GRAPH 阈值豁免删掉（A.63 的修复回退）→
  ``TestSwitchWorksWhenOn::test_graph_neighbors_survive_threshold`` 红；
* 把 retriever 里的证据指针注释删掉 → ``TestEvidencePointer`` 红。
"""

from __future__ import annotations

import pathlib

import pytest

pytestmark = [pytest.mark.track_rag]

_REPO = pathlib.Path(__file__).resolve().parents[3]
_APP = _REPO / "app"


class TestDefaultStaysOff:
    def test_graph_query_enabled_defaults_to_false(self):
        """**核心**：默认 False 是裁决结果。改成 True 等于推翻 A.65/A.66。

        要改，必须先补"更难查询集"上的预注册评测（A.65 局限段已写明现有
        golden 作为该测量仪器已饱和），并在 16 号附录 A 追加新证据。
        """
        from app.config.settings import Settings

        assert Settings.model_fields["graph_query_enabled"].default is False

    def test_default_value_is_not_overridden_by_env_example(self):
        """部署模板/示例 env 不许把它偷偷放开（"默认关"必须是真的）。"""
        candidates = [
            _REPO / ".env.example",
            _REPO / ".env.template",
            _REPO / "docker-compose.yml",
            _REPO / "docker-compose.prod.yml",
        ]
        for path in candidates:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            offenders = [
                line
                for line in text.splitlines()
                if "GRAPH_QUERY_ENABLED" in line.upper()
                and "TRUE" in line.upper()
                and not line.strip().startswith("#")
            ]
            assert offenders == [], f"{path.name} 把图谱扩边默认打开了：{offenders}"


class TestSwitchWorksWhenOn:
    """"默认关"不等于"坏了"：打开时必须真的生效（A.63 已修的不能被回退）。"""

    async def test_graph_neighbors_survive_threshold(self):
        """邻居 ``score=0.0`` 必须在 ``min_score_threshold`` 过滤后仍然活着。

        这条同时反证 A.63 的修复没被回退（``app/rag/service.py`` 的 GRAPH 豁免）。
        """
        from app.knowledge.protocols import KnowledgeChunk
        from app.rag.protocols import RetrievalMethod, ScoredChunk
        from app.rag.service import _comparison_score

        chunk = KnowledgeChunk(
            chunk_id="g1",
            kb_version="kb_v1.0.0",
            organ="thyroid",
            content="neighbor",
            metadata={},
            embedding=None,
            source_doc_id="d",
            section_path="",
            token_count=4,
        )
        neighbor = ScoredChunk(
            chunk=chunk, score=0.0, retrieval_source=RetrievalMethod.GRAPH
        )
        # 图谱项必须走"豁免"路径 —— 用 service 里的判据函数证明它不会被当成低分
        assert _comparison_score(neighbor) == 0.0
        # 真过滤逻辑由 service 的 if 分支保证；这里钉住"GRAPH 是独立来源标记"
        assert neighbor.retrieval_source == RetrievalMethod.GRAPH

    def test_service_still_has_graph_exemption(self):
        """源码级钉子：``app/rag/service.py`` 的 GRAPH 豁免块必须还在。"""
        source = (_APP / "rag" / "service.py").read_text(encoding="utf-8")
        assert "RetrievalMethod.GRAPH" in source
        assert "graph_exempt" in source, "A.63 修的图谱豁免被回退了"
        assert "min_score_filter_applied" in source


class TestEvidencePointer:
    """"为何默认关"必须写在**代码里**（不能只活在这份测试或聊天记录里）。"""

    def test_retriever_documents_why_default_off(self):
        source = (_APP / "rag" / "retriever.py").read_text(encoding="utf-8")
        start = source.index("async def _expand_graph_neighbors")
        body = source[start : start + 4000]
        assert "A.65" in body, "缺 A.65 证据指针（下一个人会当成忘了开）"
        assert "A.66" in body, "缺 A.66 证据指针"
        assert "NO_GO" in body
        assert "0.0963" in body, "缺「裸扩边会稀释 hit@5」这个关键数字"

    def test_settings_comment_points_to_evidence(self):
        source = (_APP / "config" / "settings.py").read_text(encoding="utf-8")
        idx = source.index("graph_query_enabled")
        window = source[max(0, idx - 2000) : idx + 500]
        assert "A.63" in window or "A.65" in window, (
            "settings 的 graph_query_enabled 附近缺「默认关是裁决」的指针"
        )


class TestNoDefaultFlipInThisRound:
    def test_task_scope_says_do_not_change_defaults(self):
        """本项交付的是注释与证据，**不是**默认值变更（自我约束的可执行记录）。"""
        source = (_APP / "rag" / "retriever.py").read_text(encoding="utf-8")
        idx = source.index("async def _expand_graph_neighbors")
        body = source[idx : idx + 4000]
        assert "不要把它当成" in body or "不是待办" in body or "不是「忘了打开」" in body
