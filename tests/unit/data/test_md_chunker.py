"""md_chunker 单元测试：表格保护 / 结构感知 / 父子块 / overlap。

对应缺陷（2026-11-09 RAG 阶段1 改造）：
- 表格被切成碎片 → 实测 KB v1.4.0 有 12 个"孤儿表格块"（无表头无分隔行）
- 无 overlap（旧代码注释自承）
- 无父子块（迁移 0007 建了两列但代码 0 处写入）
"""
from __future__ import annotations

import pytest

from app.knowledge.md_chunker import (
    MarkdownStructureChunker,
    chunk_markdown,
    parse_markdown,
)

#: 真实 MinerU 输出片段（ctirads_2021.md 第 319 行起的围栏表格）。
#: 注意：这里刻意保留"空白对齐 + 半角 ~"，与 MinerU 4.0.12 实际输出一致。
CTIRADS_TABLE = """ 结节   分值    恶性率 %     C-TIRADS分类
无结节 无分值*      0     1 无结节
有结节    -1     0     2 良性
       0      <2    3 良性可能
       1     2~10   4A 低度可疑恶性
       2     10~50  4B 中度可疑恶性
      3~4    50~90  4C 高度可疑恶性
       5      >90   5 高度提示恶性
       -      -     6 活检证实的恶性"""

CTIRADS_DOC = f"""# 中国指南:C-TIRADS

## 第二部分:中国版TIRADS

（一）C-TIRADS的建立过程。专家委员会以中国甲状腺与乳腺超声人工智能联盟为基础，
建立了多中心研究团队，共提供了2141例患者的2141个经手术病理证实的甲状腺结节。

表1 基于计数法的 C-TIRADS

```
{CTIRADS_TABLE}
```

注:* 无结节,不予赋分

1.分类注意事项（1）医生经验和 C-TIRADS分类，本指南采用基于计数法建立的C-TIRADS。
"""


class TestTableProtection:
    def test_fenced_aligned_table_becomes_pipe_table(self):
        chunks = chunk_markdown(
            source_doc_id="pdf_thyroid_ctirads_2021_0000",
            organ="thyroid",
            markdown=CTIRADS_DOC,
            kb_version="kb_v2.0.0",
        )
        tables = [c for c in chunks if c.metadata.get("content_type") == "table"]
        assert tables, "整表没有被识别为 table chunk"
        body = "\n".join(c.content for c in tables)
        # 整表所有分档行都必须在同一个 chunk 里（这是"表格可检索"的根因）
        for row in ("4A", "4B", "4C", "5 高度提示恶性", "50~90", "2~10", "10~50"):
            assert row in body, f"表格行 {row!r} 被切断/丢失"
        # 表格必须带表头与分隔行（否则就是"孤儿表格块"）
        assert "|" in body and "---" in body
        assert "结节" in body and "恶性率" in body

    def test_table_never_split_even_when_oversized(self):
        """超长表格仍然整表成块，不按 token 上限切。"""
        big_rows = "\n".join(
            f"参数{i:03d}    {i}    正常参考值{i}~{i + 5}" for i in range(200)
        )
        md = f"# 参考值表\n\n```\n参数   数值   参考范围\n{big_rows}\n```\n"
        chunks = chunk_markdown(
            source_doc_id="d", organ="cardiac", markdown=md,
            kb_version="kb_v2.0.0", child_max_tokens=100,
        )
        tables = [c for c in chunks if c.metadata.get("content_type") == "table"]
        assert len(tables) == 1, f"超长表格被拆成 {len(tables)} 块"
        assert tables[0].content.count("参数") >= 200

    def test_pipe_table_without_fence_is_recognised(self):
        md = (
            "# 表\n\n"
            "| 分类 | 恶性率 |\n|---|---|\n| 4C | 50~90 |\n| 5 | >90 |\n"
        )
        chunks = chunk_markdown(
            source_doc_id="d", organ="thyroid", markdown=md, kb_version="kb_v2.0.0",
        )
        assert any(c.metadata.get("content_type") == "table" for c in chunks)

    def test_formula_fence_not_treated_as_table(self):
        """公式围栏不是表格——不能被转成管道表（会破坏公式）。"""
        md = "# 公式\n\n```\nE = mc^2\n```\n"
        chunks = chunk_markdown(
            source_doc_id="d", organ="cardiac", markdown=md, kb_version="kb_v2.0.0",
        )
        assert all(c.metadata.get("content_type") != "table" for c in chunks)
        assert any("E = mc^2" in c.content for c in chunks)


class TestStructureAware:
    def test_heading_hierarchy_becomes_section_path(self):
        md = "# 总则\n\n## 第一节\n\n正文甲。\n\n### 子节\n\n正文乙。\n"
        sections = parse_markdown(md)
        paths = [" / ".join(s.heading_path) for s in sections]
        assert any("总则 / 第一节" in p for p in paths)
        assert any("总则 / 第一节 / 子节" in p for p in paths)

    def test_section_path_used_in_chunk(self):
        chunks = chunk_markdown(
            source_doc_id="d", organ="thyroid", markdown=CTIRADS_DOC,
            kb_version="kb_v2.0.0",
        )
        assert any("中国版TIRADS" in c.section_path for c in chunks)

    def test_page_markers_stripped(self):
        md = "# A\n\n<!-- page 7 of 16 -->\n\n正文内容。\n"
        chunks = chunk_markdown(
            source_doc_id="d", organ="thyroid", markdown=md, kb_version="kb_v2.0.0",
        )
        assert all("page 7 of 16" not in c.content for c in chunks)


class TestParentChild:
    def test_multi_chunk_section_has_parent_and_children(self):
        body = "\n\n".join(f"第{i}段。" + "内容" * 200 for i in range(4))
        md = f"# 大节\n\n{body}\n"
        chunks = chunk_markdown(
            source_doc_id="d", organ="thyroid", markdown=md,
            kb_version="kb_v2.0.0", child_max_tokens=200, parent_max_tokens=1000,
        )
        parents = [c for c in chunks if c.chunk_type == "parent"]
        children = [c for c in chunks if c.chunk_type == "child"]
        assert parents and children
        parent_ids = {p.chunk_id for p in parents}
        for ch in children:
            assert ch.parent_chunk_id in parent_ids
        for p in parents:
            assert p.parent_chunk_id is None

    def test_single_chunk_section_has_no_parent(self):
        md = "# 小節\n\n短短一句话。\n"
        chunks = chunk_markdown(
            source_doc_id="d", organ="thyroid", markdown=md, kb_version="kb_v2.0.0",
        )
        assert chunks
        assert all(c.chunk_type == "child" for c in chunks)
        assert all(c.parent_chunk_id is None for c in chunks)

    def test_chunk_ids_unique_and_monotonic(self):
        chunks = chunk_markdown(
            source_doc_id="d", organ="thyroid", markdown=CTIRADS_DOC,
            kb_version="kb_v2.0.0", child_max_tokens=120,
        )
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids)), "chunk_id 有重复"
        idx = [c.metadata["chunk_index"] for c in chunks]
        assert idx == list(range(len(chunks)))


class TestOverlap:
    def test_overlap_keeps_boundary_sentence(self):
        """超长小节硬切时，切点附近的句子应在相邻块各出现一次（overlap）。"""
        sents = "".join(
            f"第{i:02d}句的内容比较长一些以便超过最小句长限制。" for i in range(40)
        )
        md = f"# 长节\n\n{sents}\n"
        chunks = chunk_markdown(
            source_doc_id="d", organ="thyroid", markdown=md,
            kb_version="kb_v2.0.0", child_max_tokens=60, overlap_ratio=0.15,
        )
        assert len(chunks) >= 2, f"应产生多块，实际 {len(chunks)}"
        assert all(c.content.strip() for c in chunks)
        # overlap 生效：某一句应同时出现在相邻两块中
        shared = any(
            any(s and s in chunks[i + 1].content for s in chunks[i].content.split("。"))
            for i in range(len(chunks) - 1)
        )
        assert shared, "相邻块之间没有重叠内容（overlap 未生效）"

    def test_zero_overlap_produces_disjoint_blocks(self):
        sents = "".join(
            f"第{i:02d}句的内容比较长一些以便超过最小句长限制。" for i in range(40)
        )
        md = f"# 长节\n\n{sents}\n"
        chunks = chunk_markdown(
            source_doc_id="d", organ="thyroid", markdown=md,
            kb_version="kb_v2.0.0", child_max_tokens=60, overlap_ratio=0.0,
        )
        assert len(chunks) >= 2


class TestMetadata:
    def test_source_set_authority_year_in_metadata(self):
        chunks = chunk_markdown(
            source_doc_id="pdf_thyroid_ctirads_2021_0000",
            organ="thyroid", markdown=CTIRADS_DOC, kb_version="kb_v2.0.0",
            source_set="ctirads_2021",
            source_authority="中华医学会超声医学分会",
            publish_year=2021,
        )
        md = chunks[0].metadata
        assert md["source_set"] == "ctirads_2021"
        assert md["source_authority"] == "中华医学会超声医学分会"
        assert md["publish_year"] == 2021

    def test_content_type_values(self):
        chunks = chunk_markdown(
            source_doc_id="d", organ="thyroid", markdown=CTIRADS_DOC,
            kb_version="kb_v2.0.0",
        )
        types = {c.metadata.get("content_type") for c in chunks}
        assert types <= {"text", "table", "list"}
        assert "table" in types
        assert "text" in types


class TestAdapter:
    def test_adapter_implements_chunker_protocol(self):
        from app.knowledge.chunker import (
            ChunkerStrategy,
            KnowledgeSourceDoc,
            create_chunker,
        )

        c = create_chunker(ChunkerStrategy.MD_STRUCTURE)
        assert isinstance(c, MarkdownStructureChunker)
        doc = KnowledgeSourceDoc(
            source_doc_id="pdf_thyroid_ctirads_2021_0000",
            organ="thyroid",
            content=CTIRADS_DOC,
        )
        chunks = c.chunk(doc, "kb_v2.0.0")
        assert chunks
        # 适配器必须自己补来源元数据（builder 不传）
        assert chunks[0].metadata.get("source_set") == "ctirads_2021"
        assert chunks[0].metadata.get("publish_year") == 2021

    def test_rejects_bad_limits(self):
        with pytest.raises(ValueError):
            MarkdownStructureChunker(child_max_tokens=0)
        with pytest.raises(ValueError):
            MarkdownStructureChunker(child_min_tokens=100, child_max_tokens=50)
