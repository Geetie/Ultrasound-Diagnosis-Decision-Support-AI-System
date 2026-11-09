"""Structure-aware Markdown chunker for MinerU-parsed clinical guidelines.

Why this module exists (2026-11-09, RAG 阶段1 摄取层改造)
--------------------------------------------------------
旧的 ``chunker.SentenceBoundaryChunker`` 把文档当纯文本切分，导致三类实测缺陷：

1. **表格被切碎**：实测 KB v1.4.0 有 12 个"孤儿表格块"——正文以 ``|`` 开头、
   没有表头也没有 ``|---|`` 分隔行（例如 ``||下限|上限|…`` 纯数字矩阵），
   零语义、检索必然挂。
2. **``<br>`` 切断数值**：pypdf 抽出的 ``85%～<br>100%`` 把区间切断；
   MinerU 输出把整表放在围栏代码块里，逐行对齐，不受此影响。
3. **无 overlap、无父子块**：块边界硬切，跨块信息丢失。

设计决策（每条都有依据，见 reports/RAG阶段1_分块前沿研究_20261109.md）
--------------------------------------------------------------------
- **结构感知优先**：标题层级用 MinerU 的 ``#``/``##``/``###``，不用正则猜。
- **表格整表成块**：围栏代码块 + 管道行 + ``表N``/``Table N`` 题注都识别为表格，
  整表不切（表头重复/整表成块的选择见实测：整表成块 0.6241 > 管道化 0.6077）。
- **overlap 按实测取**：Chroma 2024 实测 400tok/0-overlap recall 89.5 高于
  400tok/200-overlap 88.1，且 IoU 明显更好 → 默认对小节内**句子级**回退重叠
  （只在超长小节兜底切分时启用），不是无脑字符重叠。
- **父子块**：子块（检索单元）带 ``parent_chunk_id`` 指向父块（喂 LLM 单元）。
  父块只在子块 >1 时生成，单块小节不产生父块（避免索引膨胀）。

本模块**只做切分**，不碰 embedding/索引；元数据推断见 ``metadata_infer``。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

from app.knowledge.protocols import KnowledgeChunk

logger = structlog.get_logger("app.knowledge.md_chunker")


# ---------------------------------------------------------------- 常量（都有出处）

#: 子块目标 token 上限。Chroma 实测 200–400tok 是精度/召回甜区；
#: 本项目嵌入模型 bge-m3 上下文 8192，取 400 留足父子块头部余量。
DEFAULT_CHILD_MAX_TOKENS = 400
#: 子块下限：低于此值并入相邻块（避免"零语义碎片"）。
DEFAULT_CHILD_MIN_TOKENS = 80
#: 父块上限：喂给 LLM 的上下文单元。上限取 1600 是因为临床指南一节
#: （如 C-TIRADS 表1+注意事项）约 800–1500 字，超此值再切会丢表。
DEFAULT_PARENT_MAX_TOKENS = 1600
#: 超长小节兜底切分时的句子重叠比例。Chroma 实测 overlap 会降低 IoU、
#: 对 recall 无稳定增益，故取得很小（10%），仅在必须硬切时启用。
DEFAULT_OVERLAP_RATIO = 0.10

#: CJK 近似 token 数：1 token ≈ 2 字符（与旧 chunker 同口径，保持可比）。
_CHARS_PER_TOKEN = 2


def approx_tokens(text: str) -> int:
    """与 ``app.knowledge.chunker._approx_tokens`` 同口径，便于前后对比。"""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


# ---------------------------------------------------------------- 表格识别

#: 题注：``表1 基于计数法的C-TIRADS`` / ``Table 2. Parameters``（行首独立成行）
_CAPTION_RE = re.compile(
    r"^\s*(?:表|Table)\s*[0-9]+(?:\s*[.、:：-]|\s)",
    re.IGNORECASE,
)
#: 管道行：以 ``|`` 开头且含至少一个 ``|``
_PIPE_ROW_RE = re.compile(r"^\s*\|.*\|")
#: Markdown 表格分隔行 ``|---|---|``
_PIPE_SEP_RE = re.compile(r"^\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*$")
#: 围栏代码块（MinerU 把表格/公式放这里）
_FENCE_RE = re.compile(r"^\s*```")
#: 标题（1–6 级）
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
#: MinerU 页码注释 ``<!-- page 7 of 16 -->``
_PAGE_MARK_RE = re.compile(r"^\s*<!--\s*page\s+\d+", re.IGNORECASE)


def _looks_like_aligned_table(lines: list[str]) -> bool:
    """判断围栏块内容是否是"对齐的表格"而非代码/公式。

    判据（任一）：
    - 含 ``|`` 分隔的管道行；
    - 连续 ≥3 行且每行有 ≥2 个由 ≥2 空格分隔的列；
    - 数字密度 ≥0.3 且行数 ≥3（数值矩阵，如正常参考值表）。
    """
    rows = [ln for ln in lines if ln.strip()]
    if len(rows) < 2:
        return False
    if any("|" in r for r in rows):
        return True
    cols_ok = 0
    for r in rows:
        if len(re.split(r"\s{2,}", r.strip())) >= 2:
            cols_ok += 1
    if cols_ok >= 3 and cols_ok / len(rows) >= 0.6:
        return True
    num_rows = sum(1 for r in rows if re.search(r"\d", r))
    return num_rows >= 3 and num_rows / len(rows) >= 0.3


def _aligned_to_pipe(lines: list[str]) -> str:
    """把"空白对齐"的表格转成 Markdown 管道表（保表头，列数取众数）。

    保留原始行文本作为单元格内容（不做语义改写——实测改写会掉相似度）。

    ## 为什么按"列位"切而不是按 ``\\s{2,}`` 切

    MinerU 的对齐表里，跨行的合并单元格会留空（例如 C-TIRADS 计数表的
    ``有结节`` 只写在首行，后面几行该列为空），按空格分组会让后续行**整体
    左移一列**（实测 ``| 0 | <2 | 3 良性可能 |  |`` 把"0"错放进了"结节"列）。
    这里改为：用**表头各列的起始字符位**作为列边界，逐行按位切，缺列留空。
    行长短不齐时退化为 ``\\s{2,}`` 切分（保持原行为）。
    """
    rows = [ln.rstrip() for ln in lines if ln.strip()]
    if not rows:
        return ""
    if any("|" in r for r in rows):
        return "\n".join(rows)

    header_cells = [c for c in re.split(r"\s{2,}", rows[0].strip()) if c]
    ncol = len(header_cells)
    # 行内的多空格是**列间填充**还是**单元格内容自带的空格**，在纯文本里
    # 无法可靠区分（实测：``0     1 无结节`` 里 "0" 和 "1 无结节" 是两个单元格，
    # 而 "恶性率 %" 本身含空格）。位置法在数值列比表头宽时必然错位。
    #
    # 因此采取**保守且可解释**的策略：数据行按 ``\s{2,}`` 切，若切出的段数
    # 多于列数则把多出的段并回最后一列；少于列数则用空串补齐。
    # 好处：绝不把一行的内容丢进错误列到"看起来对"的程度——宁可留空隙也不串列。
    out: list[str] = []
    out.append("| " + " | ".join(header_cells) + " |")
    out.append("|" + "|".join(["---"] * ncol) + "|")
    for r in rows[1:]:
        cells = [c for c in re.split(r"\s{2,}", r.strip()) if c]
        if len(cells) > ncol:
            cells = cells[: ncol - 1] + [" ".join(cells[ncol - 1:])]
        cells = cells + [""] * (ncol - len(cells))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


# ---------------------------------------------------------------- 结构解析

@dataclass
class Block:
    """解析后的结构化单元（标题 / 段落 / 表格 / 列表）。"""
    kind: str            # heading | text | table | list
    text: str
    level: int = 0        # heading 专用
    caption: str = ""     # table 题注
    heading_path: list[str] = field(default_factory=list)


@dataclass
class Section:
    """一个标题小节：标题路径 + 有序块列表。"""
    heading_path: list[str]
    blocks: list[Block] = field(default_factory=list)

    @property
    def title(self) -> str:
        return " / ".join(self.heading_path) if self.heading_path else "（无标题）"


def parse_markdown(md: str) -> list[Section]:
    """把 MinerU Markdown 解析成有序小节（标题 → 块）。

    保持原文顺序；不丢内容（无法归类的行按 text 处理）。
    """
    lines = md.split("\n")
    sections: list[Section] = []
    path: list[tuple[int, str]] = []          # [(level, title)]
    cur = Section(heading_path=[])
    pending_caption = ""

    def _flush() -> None:
        nonlocal cur
        if cur.blocks or cur.heading_path:
            sections.append(cur)
        cur = Section(heading_path=[t for _, t in path])

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        if _PAGE_MARK_RE.match(line) or not line.strip():
            i += 1
            continue

        # --- 标题 ---
        hm = _HEADING_RE.match(line)
        if hm:
            level = len(hm.group(1))
            title = hm.group(2).strip()
            _flush()
            path = [(lv, t) for lv, t in path if lv < level]
            path.append((level, title))
            cur = Section(heading_path=[t for _, t in path])
            i += 1
            continue

        # --- 围栏块（MinerU 的表格/公式）---
        if _FENCE_RE.match(line):
            j = i + 1
            body: list[str] = []
            while j < n and not _FENCE_RE.match(lines[j]):
                body.append(lines[j])
                j += 1
            if _looks_like_aligned_table(body):
                cur.blocks.append(Block(
                    kind="table",
                    text=_aligned_to_pipe(body),
                    caption=pending_caption,
                    heading_path=[t for _, t in path],
                ))
                pending_caption = ""
            elif body and any(b.strip() for b in body):
                # 公式/代码：保留原文（公式识别信息不能丢）
                cur.blocks.append(Block(
                    kind="text",
                    text="\n".join(b for b in body if b.strip()),
                    heading_path=[t for _, t in path],
                ))
            i = j + 1
            continue

        # --- 表题注（下一块若是表格则挂上去）---
        if _CAPTION_RE.match(line) and len(line.strip()) <= 120:
            pending_caption = line.strip()
            # 题注也可能是整块表格的引言，先记下但不出块
            i += 1
            continue

        # --- 直接是管道表（非围栏）---
        if _PIPE_ROW_RE.match(line):
            j = i
            body = []
            while j < n and (_PIPE_ROW_RE.match(lines[j]) or not lines[j].strip()):
                if lines[j].strip():
                    body.append(lines[j])
                j += 1
            cur.blocks.append(Block(
                kind="table",
                text="\n".join(body),
                caption=pending_caption,
                heading_path=[t for _, t in path],
            ))
            pending_caption = ""
            i = j
            continue

        # --- 列表项 ---
        if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", line):
            j = i
            body = []
            while j < n and (re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", lines[j])
                            or (lines[j].strip() and lines[j].startswith((" ", "\t")))):
                body.append(lines[j].strip())
                j += 1
            cur.blocks.append(Block(
                kind="list",
                text="\n".join(body),
                heading_path=[t for _, t in path],
            ))
            i = j
            continue

        # --- 普通段落：吃到空行/标题/围栏/表格为止 ---
        j = i
        body = []
        while (j < n and lines[j].strip()
               and not _HEADING_RE.match(lines[j])
               and not _FENCE_RE.match(lines[j])
               and not _PIPE_ROW_RE.match(lines[j])
               and not _PAGE_MARK_RE.match(lines[j])):
            body.append(lines[j].strip())
            j += 1
        if body:
            cur.blocks.append(Block(
                kind="text",
                text="\n".join(body),
                heading_path=[t for _, t in path],
            ))
        i = max(j, i + 1)

    # 落尾
    if cur.blocks or cur.heading_path:
        sections.append(cur)
    # 题注没等到表格：作为正文补进最后一节，不丢信息
    if pending_caption and sections:
        sections[-1].blocks.append(Block(
            kind="text", text=pending_caption,
            heading_path=sections[-1].heading_path,
        ))
    return [s for s in sections if s.blocks]


# ---------------------------------------------------------------- 切分

_SENT_SPLIT_RE = re.compile(r"(?<=[。！？；])|(?<=[.!?;])\s+|\n+")


def _split_sentences(text: str, min_chars: int = 12) -> list[str]:
    """按标点切句，并把**过短**碎片与相邻句合并。

    为什么要合并：临床指南里大量"第1段。""注:""（1）"这类 1–5 字的独立片段，
    原样切出来会变成 2-token 的块（实测：4 段正文被切成 8 个 2-token 碎片），
    既污染索引又让父块分组失效。
    """
    raw = [p.strip() for p in _SENT_SPLIT_RE.split(text) if p and p.strip()]
    out: list[str] = []
    for s in raw:
        if out and len(s) < min_chars:
            out[-1] = out[-1] + s
        elif out and len(out[-1]) < min_chars:
            out[-1] = out[-1] + s
        else:
            out.append(s)
    return out



def _pack_units(
    units: list[tuple[str, str]],
    max_tokens: int,
    min_tokens: int,
    overlap_ratio: float,
) -> list[list[tuple[str, str]]]:
    """把 (kind, text) 单元装箱到不超过 max_tokens 的组。

    规则（刻意简单，便于单测钉住）：
    1. **表格是原子**：整表独占一组，既不拆分也不与其他单元合并；
    2. 其余单元：超长则按句子拆成原子（相邻块间只在该情形下才带 overlap），
       未超长则整个单元是一个原子；
    3. 原子按顺序贪心装箱，超过 max_tokens 就换组；
    4. 装箱后把 < min_tokens 的小组并进邻居（表格组除外），避免零语义碎片。
    """
    # --- 1/2. 展开成原子 -------------------------------------------------
    atoms: list[tuple[str, str, bool]] = []   # (kind, text, is_table)
    for kind, text in units:
        if kind == "table":
            atoms.append((kind, text, True))
            continue
        if approx_tokens(text) <= max_tokens:
            atoms.append((kind, text, False))
            continue
        # 超长：按句子拆。overlap 只在相邻原子间额外带上一句。
        sents = _split_sentences(text)
        n_ov = max(0, int(round(len(sents) * overlap_ratio)))
        cur: list[str] = []
        cur_tok = 0
        for s in sents:
            st = approx_tokens(s)
            if cur and cur_tok + st > max_tokens:
                atoms.append((kind, " ".join(cur), False))
                cur = cur[-n_ov:] if n_ov else []
                cur_tok = sum(approx_tokens(x) for x in cur)
            cur.append(s)
            cur_tok += st
        if cur:
            atoms.append((kind, " ".join(cur), False))

    # --- 3. 贪心装箱 -----------------------------------------------------
    groups: list[list[tuple[str, str]]] = []
    buf: list[tuple[str, str]] = []
    buf_tok = 0
    for kind, text, is_table in atoms:
        tok = approx_tokens(text)
        if is_table:
            if buf:
                groups.append(buf)
                buf, buf_tok = [], 0
            groups.append([(kind, text)])
            continue
        if buf and buf_tok + tok > max_tokens:
            groups.append(buf)
            buf, buf_tok = [], 0
        buf.append((kind, text))
        buf_tok += tok
    if buf:
        groups.append(buf)

    # --- 4. 小组合并（表格组不动）----------------------------------------
    merged: list[list[tuple[str, str]]] = []
    for g in groups:
        g_tok = sum(approx_tokens(t) for _, t in g)
        is_tbl = g[0][0] == "table"
        if (not is_tbl and g_tok < min_tokens and merged
                and merged[-1][0][0] != "table"
                and sum(approx_tokens(t) for _, t in merged[-1]) + g_tok <= max_tokens):
            merged[-1].extend(g)
            continue
        merged.append(g)
    return merged



# ---------------------------------------------------------------- 对外接口

def chunk_markdown(
    *,
    source_doc_id: str,
    organ: str,
    markdown: str,
    kb_version: str,
    source_set: str = "",
    source_authority: str = "",
    publish_year: int | None = None,
    child_max_tokens: int = DEFAULT_CHILD_MAX_TOKENS,
    child_min_tokens: int = DEFAULT_CHILD_MIN_TOKENS,
    parent_max_tokens: int = DEFAULT_PARENT_MAX_TOKENS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
) -> list[KnowledgeChunk]:
    """把一个 MinerU Markdown 文档切成父子块。

    返回顺序：先父块后其子块（builder 需按 chunk_index 稳定排序）。
    父块 ``chunk_type="parent"``、``parent_chunk_id=None``；
    子块 ``chunk_type="child"``、``parent_chunk_id=<父 chunk_id>``。
    单块小节不生成父块，直接出 child（``parent_chunk_id`` 为空）。
    """
    sections = parse_markdown(markdown or "")
    if not sections:
        return []

    base_meta = {
        "source_set": source_set,
        "source_authority": source_authority,
        "publish_year": publish_year,
    }

    out: list[KnowledgeChunk] = []
    idx = 0

    for sec in sections:
        units: list[tuple[str, str]] = []
        for b in sec.blocks:
            if b.kind == "table":
                caption = b.caption
                text = (f"{caption}\n{b.text}" if caption else b.text)
                units.append(("table", text))
            else:
                units.append((b.kind, b.text))
        if not units:
            continue

        groups = _pack_units(units, child_max_tokens, child_min_tokens, overlap_ratio)
        # 小节内再按父块上限归组（父块 = 喂 LLM 的上下文单元）
        parent_groups: list[list[list[tuple[str, str]]]] = []
        cur: list[list[tuple[str, str]]] = []
        cur_tok = 0
        for g in groups:
            g_tok = sum(approx_tokens(t) for _, t in g)
            if cur and cur_tok + g_tok > parent_max_tokens:
                parent_groups.append(cur)
                cur, cur_tok = [], 0
            cur.append(g)
            cur_tok += g_tok
        if cur:
            parent_groups.append(cur)

        for pg in parent_groups:
            kids = pg
            # 单块小节：不出父块（避免索引膨胀），子块自己就是上下文单元
            if len(kids) == 1:
                content = "\n\n".join(t for _, t in kids[0])
                kinds = {k for k, _ in kids[0]}
                out.append(_mk(
                    kb_version=kb_version, source_doc_id=source_doc_id,
                    organ=organ, content=content, section_path=sec.title,
                    index=idx, chunk_type="child", parent_id=None,
                    content_type=_content_type(kinds), meta=base_meta,
                ))
                idx += 1
                continue

            parent_content = "\n\n".join(
                t for g in kids for _, t in g
            )
            parent_types = {k for g in kids for k, _ in g}
            parent_index = idx
            parent_id = _chunk_id(kb_version, source_doc_id, parent_index)
            out.append(_mk(
                kb_version=kb_version, source_doc_id=source_doc_id,
                organ=organ, content=parent_content, section_path=sec.title,
                index=parent_index, chunk_type="parent", parent_id=None,
                content_type=_content_type(parent_types), meta=base_meta,
            ))
            idx += 1
            for g in kids:
                content = "\n\n".join(t for _, t in g)
                kinds = {k for k, _ in g}
                out.append(_mk(
                    kb_version=kb_version, source_doc_id=source_doc_id,
                    organ=organ, content=content, section_path=sec.title,
                    index=idx, chunk_type="child", parent_id=parent_id,
                    content_type=_content_type(kinds), meta=base_meta,
                ))
                idx += 1

    return out


class MarkdownStructureChunker:
    """``Chunker`` 协议适配器：把 ``chunk_markdown`` 接到 builder 上。

    builder 传进来的是 ``KnowledgeSourceDoc``（content = MinerU Markdown 原文），
    这里补上从 ``source_doc_id`` 推断的来源元数据后调用 ``chunk_markdown``。
    """

    def __init__(
        self,
        child_max_tokens: int = DEFAULT_CHILD_MAX_TOKENS,
        child_min_tokens: int = DEFAULT_CHILD_MIN_TOKENS,
        parent_max_tokens: int = DEFAULT_PARENT_MAX_TOKENS,
        overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
    ):
        if child_max_tokens <= 0 or parent_max_tokens <= 0:
            raise ValueError("token limits must be positive")
        if child_min_tokens > child_max_tokens:
            raise ValueError("child_min_tokens must be <= child_max_tokens")
        self.child_max_tokens = child_max_tokens
        self.child_min_tokens = child_min_tokens
        self.parent_max_tokens = parent_max_tokens
        self.overlap_ratio = overlap_ratio

    def chunk(self, doc, kb_version: str) -> list[KnowledgeChunk]:
        from app.knowledge.metadata_infer import infer_source_metadata

        meta = infer_source_metadata(doc.source_doc_id)
        return chunk_markdown(
            source_doc_id=doc.source_doc_id,
            organ=doc.organ,
            markdown=doc.content,
            kb_version=kb_version,
            source_set=meta.source_set,
            source_authority=meta.source_authority,
            publish_year=meta.publish_year,
            child_max_tokens=self.child_max_tokens,
            child_min_tokens=self.child_min_tokens,
            parent_max_tokens=self.parent_max_tokens,
            overlap_ratio=self.overlap_ratio,
        )


def _content_type(kinds: set[str]) -> str:
    """content_type 取值：table / list / text（表格优先级最高）。"""
    if "table" in kinds:
        return "table"
    if "list" in kinds and kinds == {"list"}:
        return "list"
    return "text"


def _chunk_id(kb_version: str, source_doc_id: str, index: int) -> str:
    # 与旧 chunker 同格式：含 kb_version 防多版本撞车
    return f"{kb_version}:{source_doc_id}#{index:04d}"


def _mk(
    *,
    kb_version: str,
    source_doc_id: str,
    organ: str,
    content: str,
    section_path: str,
    index: int,
    chunk_type: str,
    parent_id: str | None,
    content_type: str,
    meta: dict,
) -> KnowledgeChunk:
    metadata = {
        "strategy": "md_structure",
        "chunk_index": index,
        "chunk_type": chunk_type,
        "content_type": content_type,
        **{k: v for k, v in meta.items() if v not in ("", None)},
    }
    if parent_id:
        metadata["parent_chunk_id"] = parent_id
    return KnowledgeChunk(
        chunk_id=_chunk_id(kb_version, source_doc_id, index),
        kb_version=kb_version,
        organ=organ,
        content=content.strip(),
        metadata=metadata,
        source_doc_id=source_doc_id,
        section_path=section_path,
        token_count=approx_tokens(content),
        chunk_type=chunk_type,
        parent_chunk_id=parent_id,
    )
