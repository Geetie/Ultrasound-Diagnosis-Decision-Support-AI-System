#!/usr/bin/env python3
"""合成数据质量门（p121）：不靠模型自觉，靠程序判据。

## 为什么必须有这把闸

实测：即使提示词明确写「禁止编造文献名称与年份」，II-Medical-8B 仍然输出：
- `依据《C-TIRADS 2018》` ← 真实是 2021
- `根据《LI-RADS 2017》` ← 用在了甲状腺问题上
- `根据《Hürthle 细胞肿瘤》` ← 编的

**8B 模型约束不住。必须程序校验。**

## 五道闸

| 闸 | 判据 | 抓什么问题 |
|---|---|---|
| 1 引文真实性 | 答案里的 `《...》` 必须能在 KB 里找到同名字符串 | 编造文献名 |
| 2 体系一致性 | 甲状腺问题不许出现 LI-RADS；肝脏不许出现 C-TIRADS | 跨体系混用 |
| 3 问句形态 | query 必须含问号或疑问词，且 ≤80 字 | 把原文复述当 query |
| 4 答案信息量 | reference_answer ≥40 字，且不能整段抄 query | 空洞答案 |
| 5 gold 一致性 | gold 的 chunk_id 必须存在，且非空 | 标错位置 |

## 用法

```bash
PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p121_synth_quality_gate.py \
    --dataset /tmp/synth.jsonl --kb-version kb_v2.1.0
```
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_CITE_RE = re.compile(r"《([^》]{2,40})》")
_ASK_RE = re.compile(r"[？?]|要不要|是否需要|怎么|如何|为什么|什么|多少|是否|能否|该不该|下一步")

#: 器官 → 允许出现的分级体系；出现其它体系 = 跨体系混用
_ORGAN_SYSTEMS = {
    "thyroid": ("C-TIRADS", "CTIRADS", "TI-RADS", "TIRADS", "TR1", "TR2", "TR3", "TR4", "TR5"),
    "liver": ("LI-RADS", "LIRADS", "LR-", "CEUS", "OPTN"),
    "cardiac": ("LVEF", "EF", "E/e", "GLS", "ASE", "BSE"),
}
#: 明确不该出现的（跨器官体系）
_FORBIDDEN = {
    "thyroid": ("LI-RADS", "LIRADS", "LR-1", "LR-2", "LR-3", "LR-4", "LR-5", "LR-M"),
    "liver": ("C-TIRADS", "CTIRADS", "TI-RADS"),
    "cardiac": ("C-TIRADS", "LI-RADS", "TI-RADS"),
}

MIN_ANSWER = 40
MAX_QUERY = 80


async def load_kb_texts(kb_version: str) -> str:
    """把 KB 全文拼起来，用于查「引文里的书名是否真实存在」。"""
    from app.config.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                text("SELECT chunk_id, content FROM knowledge_chunks WHERE kb_version = :v"),
                {"v": kb_version},
            )
        ).fetchall()
    return "\n".join((c or "") for _cid, c in rows)


def check_one(row: dict, kb_text: str, existing_ids: set[str]) -> list[str]:
    """返回问题列表（空 = 通过）。"""
    probs: list[str] = []
    organ = row.get("organ") or ""
    q = (row.get("query") or "").strip()
    a = (row.get("reference_answer") or "").strip()

    # ---- 闸 3：问句形态
    if not q:
        probs.append("query 为空")
    else:
        if not _ASK_RE.search(q):
            probs.append("query 不含疑问词（像陈述而非提问）")
        if len(q) > MAX_QUERY:
            probs.append(f"query 过长 {len(q)}>{MAX_QUERY}（像复述原文）")

    # ---- 闸 4：答案信息量
    if len(a) < MIN_ANSWER:
        probs.append(f"答案过短 {len(a)}<{MIN_ANSWER}")
    if q and a and q in a:
        probs.append("答案整段抄了 query")

    # ---- 闸 1：引文真实性（《X》必须在 KB 里出现过）
    for m in _CITE_RE.finditer(a + " " + q):
        name = m.group(1).strip()
        # 允许的形式：KB 里含该名字，或含其去掉空格/连字符的变体
        variants = {name, name.replace(" ", ""), name.replace("-", ""), name.upper(), name.lower()}
        if not any(v and v in kb_text for v in variants):
            probs.append(f"编造文献名《{name}》（KB 里找不到）")
            break   # 一条就够，不必刷屏

    # ---- 闸 2：体系一致性
    text = q + " " + a
    for bad in _FORBIDDEN.get(organ, ()):
        if bad.lower() in text.lower():
            probs.append(f"{organ} 问题出现「{bad}」（跨体系混用）")
            break

    # ---- 闸 6（2026-11-09 新增）：query 里的体系码必须与器官匹配
    #
    # 实测漏网案例：
    #   [liver] 「报告写 TR4，我作为外科医生该不该安排手术？」← TR4 是甲状腺的码
    #   [liver] 「肝脏 13mm 4A 类结节紧邻被膜，要不要穿刺？」← 4A 是甲状腺的码
    #
    # 原闸 2 只查 `_FORBIDDEN` 里的**体系名**（C-TIRADS / LI-RADS），
    # 没查**裸码**（TR4 / 4A / LR-5）。而模型恰恰爱写裸码。
    _BARE_CODE_OWNER = {
        "thyroid": ("TR1", "TR2", "TR3", "TR4", "TR5", "4A", "4B", "4C"),
        "liver": ("LR-1", "LR-2", "LR-3", "LR-4", "LR-5", "LR-M", "LR-TIV"),
        "cardiac": (),
    }
    q_upper = (q or "").upper()
    for other_organ, codes in _BARE_CODE_OWNER.items():
        if other_organ == organ:
            continue
        for code in codes:
            # 只匹配「独立出现的码」，避免 4A 命中 LABEL 之类
            if re.search(rf"(?<![A-Z0-9]){re.escape(code)}(?![0-9A-Za-z])", q_upper):
                probs.append(
                    f"{organ} 问题的 query 里出现「{code}」（属于 {other_organ} 的码）"
                )
                break
        else:
            continue
        break

    # ---- 闸 7（2026-11-09 新增）：答案里不许有「原文没有的具体数值」
    #
    # 实测漏网案例：「C-TIRADS 3 类…一般2~3个月复检一次彩超」
    # —— 原文说的是「随访间隔 6~24 个月」，2~3 个月是编的。
    #
    # 判据：答案里出现的**带单位数值**，若在原文（KB 全文）里找不到，
    # 且不是常见的通用值（如 1mm 这类），就报警。
    # 这是启发式：可能误伤"合理换算"（如 1cm 写成 10mm），所以只作 WARN 级。
    _num_re = re.compile(r"\d+(?:\.\d+)?\s*(?:mm|cm|%|个月|周|岁)")
    invented: list[str] = []
    for m in _num_re.finditer(a):
        tok = m.group(0).replace(" ", "")
        # 允许等效写法：1cm ≈ 10mm
        variants = {tok, tok.replace("cm", "mm"), tok.replace("mm", "cm")}
        if not any(v in kb_text for v in variants):
            invented.append(tok)
    if invented:
        probs.append(f"答案含原文没有的数值: {invented[:3]}")

    # ---- 闸 8（2026-11-09 新增）：query 必须含具体锚点
    #
    # 实测漏网：「边界情况：指南在这条上有没有例外或有争议的地方？」
    # 「什么情况下这条不适用」「报告这个分级意味着什么、严不严重？」
    # —— **没说是哪个分级、哪条规则**。医生不会这样问，因为它不含任何可检索的信息。
    #
    # 这类 query 的根因：模型把「问法骨架」直接当成了问题，没填入原文内容。
    _ANCHOR_RE = (
        re.compile(r"(?:C-?TIRADS|TI-?RADS|LI-?RADS|LR-?[1-5M]|TR\s?[1-5]|[1-6]\s*[A-C]?\s*类)", re.I),
        re.compile(r"\d+(?:\.\d+)?\s*(?:mm|cm|%|个月|周|岁)", re.I),
        re.compile(r"甲状腺|肝|心脏|左室|右室|二尖瓣|三尖瓣|主动脉瓣|被膜|结节|门静脉|室间隔|瓣环"),
    )
    if q and not any(p.search(q) for p in _ANCHOR_RE):
        probs.append("query 无具体锚点（没说是哪个分级/数值/部位，医生不会这样问）")

    # ---- 闸 9（2026-11-09 新增）：书名号清理留下的残句
    #
    # 实测漏网：「如果医生按原文提到：的分级标准，质控该如何处理？」
    # —— 我的 _strip_fake_citations 把「按《LI-RADS 2017》」清成了「按原文提到：」，
    # 后面直接接「的分级标准」，句子碎了。
    #
    # 判据：`原文提到：` 后面紧跟「的/是/中」这类需要名词的助词，或直接接标点。
    if re.search(r"原文提到[：:]\s*(?:的|是|中|里|[，。；、])", q or ""):
        probs.append("书名号清理后语句破碎（「原文提到：的分级标准」）")
    if re.search(r"原文提到[：:]\s*(?:的|是|中|里|[，。；、])", a or ""):
        probs.append("答案书名号清理后语句破碎")

    # ---- 闸 10（2026-11-09 L3 审核后新增）：query 与 answer 的参数必须对齐
    #
    # L3 人工审核 6 条里 5 条不合格，最典型的两类：
    #   l3-001: query「报告这个分级意味着什么」没提 LR-3，答案却自己说「分级为 LR-3」
    #           → 答案在回答一个 query 里没有的问题
    #   l3-006: query「大小 1.2cm，直径小于 1.0cm」自相矛盾，答案只答了 <1.0cm
    #
    # 判据：query 里出现的数值/分级码，必须在答案里出现**同值的**（反之亦然不要强求，
    # 因为答案可能需要引用 query 未提的其他数据）。
    _NUM_TOK = re.compile(r"\d+(?:\.\d+)?\s*(?:mm|cm|%|个月|周|岁)", re.I)
    _GRADE_TOK = re.compile(r"(?:C-?TIRADS|TI-?RADS|LI-?RADS|CEUS)?\s*(?:LR-?[1-5M]|TR\s?[1-5]|[1-6]\s*[A-C]?\s*类)", re.I)

    def _norm_tok(t: str) -> str:
        return t.replace(" ", "").lower()

    q_nums = {_norm_tok(m.group(0)) for m in _NUM_TOK.finditer(q or "")}
    a_nums = {_norm_tok(m.group(0)) for m in _NUM_TOK.finditer(a or "")}
    # 允许等效换算：1.2cm == 12mm
    def _to_mm(tok: str) -> float | None:
        m = re.match(r"(\d+(?:\.\d+)?)(mm|cm)", tok)
        if not m:
            return None
        v = float(m.group(1))
        return v * 10 if m.group(2) == "cm" else v

    a_mm = {_to_mm(t) for t in a_nums} - {None}
    missing = []
    for t in q_nums:
        if t in a_nums:
            continue
        v = _to_mm(t)
        if v is not None and any(x is not None and abs(x - v) < 0.01 for x in a_mm):
            continue
        missing.append(t)
    if missing:
        probs.append(f"query 提到的数值 {missing[:3]} 未出现在答案里（答非所问）")

    # ---- 闸 11（2026-11-09 L3 审核后新增）：跨体系码不许互换
    #
    # L3 实测 l3-005：答案写「4A 类…根据 TR4 级」，把 C-TIRADS 的 4A 说成 ACR 的 TR4。
    # **这两者差一档**（4A 低度可疑 2-10% / TR4 中度可疑），是项目中已记录的口径陷阱。
    #
    # 判据：同一答案里若同时出现 C-TIRADS 系（4A/4B/4C）与 ACR 系（TR1-TR5），
    # 且它们是**在同一句里对同一次判读**，就是混用。
    _HAS_CT = re.search(r"[1-6]\s*[A-C]\s*类|C-?TIRADS", a or "", re.I)
    _HAS_TR = re.search(r"\bTR\s?[1-5]\b", a or "", re.I)
    if _HAS_CT and _HAS_TR:
        # 只有当两套码出现在相邻 40 字以内才算"同一次判读混用"
        text_a = a or ""
        for mCt in re.finditer(r"[1-6]\s*[A-C]\s*类|C-?TIRADS", text_a, re.I):
            for mTr in re.finditer(r"\bTR\s?[1-5]\b", text_a, re.I):
                if abs(mCt.start() - mTr.start()) <= 40:
                    probs.append("同一句里混用 C-TIRADS 的 4A/4B/4C 与 ACR 的 TRx（差一档）")
                    break
            else:
                continue
            break

    # ---- 闸 12（2026-11-09 L3 审核后新增）：答案不许夹外文原文 + 清理残留
    #
    # L3 实测 l3-002：答案里夹着 "Pediatric FTC is a rare malignancy."
    # L3 实测 l3-006：答案出现「原文提到：和原文提到：」（我的清理重复）
    _EN_SPAN = re.search(r"[A-Za-z][A-Za-z\s,.'\-]{25,}", a or "")
    if _EN_SPAN:
        probs.append(f"答案里夹了整段外文原文: {_EN_SPAN.group(0)[:40]!r}")
    # ## 2026-11-09 实测修正：`count("原文提到") >= 2` 是错判据
    #
    # 原写法把「答案里出现两次『原文提到：』」判成清理残留 ——
    # 但**正常答案引用两处不同事实时本来就会写两次**：
    #   「原文提到 EF≤35% 是分层点…原文提到该类患者需随访…」
    # 实测：这条把一条正确答案判成不合格（TestQueryShape::test_good_question_passes 变红）。
    #
    # 真正的「清理残留」只有两种形态：
    #   1. 紧邻重复：「原文提到：原文提到：」
    #   2. 用连接词连着的重复：「原文提到：和原文提到：」「原文提到：或原文提到：」
    # 这两种都在 _strip_fake_citations 里折叠过，若还剩下就是真的没清干净。
    _DUP_RE = re.compile(r"原文提到[：:]\s*(?:(?:和|与|或|、|及|以及|跟|同)\s*)?原文提到[：:]")
    if _DUP_RE.search(a or ""):
        probs.append("答案里「原文提到：」重复（清理残留）")

    # ---- 闸 13（2026-11-09 L3 后新增）：检查「清理后变残句」
    #
    # ## 为什么这道闸必须存在
    #
    # 我的 `_strip_fake_citations` 把《X》替换成「原文提到：」或直接删掉。
    # 删完之后可能出现无主语的残句：
    #   「统一使用《A》或《B》中的标准」→「统一使用中的标准进行分级」  ← 缺宾语
    #   「《A》和《B》的分类标准」      →「的分类标准」                ← 缺主语
    #   「如果医生按《A》的分级标准」    →「如果医生按的分级标准」      ← 缺宾语
    #
    # **这类残句不该靠再修正则去救**（越救越复杂），
    # 而是**直接判不合格**，让模型重试一次。
    # 因为「让 LLM 重写一个不含书名的自然句子」比「用正则修复语法」可靠得多。
    _BROKEN = (
        (re.compile(r"使用\s*(?:中|里)的"), "「使用中的」缺宾语"),
        (re.compile(r"(?:和|与|或|、|及)\s*(?:的|中|里)"), "连接词后直接接助词"),
        (re.compile(r"按\s*的"), "「按的」缺宾语"),
        (re.compile(r"^(?:的|是和)"), "句首是助词"),
        (re.compile(r"(?:，|。|；)\s*(?:的|是和)"), "标点后接助词"),
        (re.compile(r"为\s*的"), "「为的」缺宾语"),
        (re.compile(r"用\s*的"), "「用的」缺宾语"),
    )
    for pat, desc in _BROKEN:
        for fld, txt in (("query", q), ("答案", a)):
            if txt and pat.search(txt):
                probs.append(f"{fld}里清理后出现残句（{desc}）")
                break
        else:
            continue
        break

    # ---- 闸 14（2026-11-09 L3 后新增）：C-TIRADS 分级与积分必须对得上
    #
    # ## 为什么这道闸最重要
    #
    # L3 审核抓到 L2 编造的医学错误（8B 模型不知道规则）：
    #   「1.5cm 边界清晰 → TR2 级，积分为 3 分」  ← TR2 是 1 分（无恶性征象）
    #   「13 分结节」                              ← C-TIRADS 满分 5 分
    #
    # **这类错误靠提示词修不了**（模型知识不够），但**靠规则能查**：
    # C-TIRADS 的分数与分级是一一对应的，写死了。
    #
    # | 分数 | 分级 |
    # |---|---|
    # | 无结节 | 1 类 |
    # | -1 | 2 类（良性） |
    # | 0 | 3 类（良性可能） |
    # | 1 | 4A |
    # | 2 | 4B |
    # | 3~4 | 4C |
    # | 5 | 5 类 |
    #
    # 所以：答案里若同时出现「N 分」与「X 类」，必须满足上表。
    _SCORE_TO_GRADE = {(-1): "2", 0: "3", 1: "4a", 2: "4b", 3: "4c", 4: "4c", 5: "5"}

    # ## 2026-11-09 实测修正：原实现有漏洞
    #
    # 原写法在拿到 `TR2` 这类码时，因为 `got.startswith("tr")` 就跳过比对 ——
    # 结果「1.5cm 边界清晰 → TR2 级，积分为 3 分」这个**真实错误没被抓到**
    # （实测：只有「13 分」被抓，「TR2+3分」漏了）。
    #
    # 修法：分两步判
    #   步骤 A：ACR 的 TRx 与 C-TIRADS 的分数**根本不属于同一体系**，
    #           同一句里同时出现「N 分」和「TRx」就是体系混用（闸 11 已管，这里再加一层）
    #   步骤 B：C-TIRADS 的「N 分」与「X 类」必须符合对照表
    score_m = re.search(r"(\d+)\s*分", a or "")
    if score_m:
        score = int(score_m.group(1))
        if score > 5:
            probs.append(f"C-TIRADS 积分不可能为 {score}（满分 5 分）")

        # 步骤 A：分数 + TRx 同现 = 体系混用
        tr_m = re.search(r"\bTR\s?([1-5])\b", a or "", re.I)
        if tr_m:
            probs.append(
                f"答案里「{score} 分」与 ACR 的「TR{tr_m.group(1)}」同时出现——"
                "TRx 是 ACR 体系，不接受 C-TIRADS 的分数"
            )

        # 步骤 B：分数 + C-TIRADS 类码 必须匹配对照表
        cm = re.search(r"([1-6])\s*([A-Ca-c]?)\s*类", a or "")
        if cm:
            got = (cm.group(1) + (cm.group(2) or "")).lower()
            want = _SCORE_TO_GRADE.get(score)
            if want and got != want:
                probs.append(
                    f"C-TIRADS 积分与分级不符：{score} 分应为 {want} 类，答案写 {got} 类"
                )

    # ---- 闸 15（2026-11-09 L3 后新增）：LI-RADS 类别与语义必须对得上
    #
    # ## 为什么必须有（闸 14 只覆盖 C-TIRADS）
    #
    # L3 人工审核抓到这条：
    #   「原文提到：…**LR-3：明确恶性（高度确定恶性）**」  ← 错误
    #   LR-3 实际是「可能良性或可能恶性（不确定）」，**LR-5 才是明确恶性**。
    #
    # C-TIRADS 可以靠「分数↔分级」对照表校验（闸14），
    # 但 LI-RADS 没有分数，只能靠**类别↔定义**对照。
    #
    # | 类别 | 正确定义 | 出现这些词就是错的 |
    # |---|---|---|
    # | LR-1 | 明确良性（100% 良性） | 恶性 |
    # | LR-2 | 可能良性 | 明确恶性 |
    # | LR-3 | 不确定（可能良性或可能恶性） | **明确恶性 / 100% 恶性 / 肯定恶性** |
    # | LR-4 | 可能恶性 | 明确良性 |
    # | LR-5 | **明确恶性**（HCC） | 良性 |
    # | LR-M | 可能或肯定恶性，但**非 HCC 特异** | 良性 |
    # | LR-TIV | 静脉内瘤栓 | 良性 |
    _LR_DEF = {
        "1": ("良性", ("恶性", "癌")),
        "2": ("良性", ("明确恶性", "肯定恶性", "100%")),
        "3": ("不确定", ("明确恶性", "肯定恶性", "100%", "高度确定恶性")),
        "4": ("可能恶性", ("明确良性", "100% 良性", "肯定良性")),
        "5": ("明确恶性", ("明确良性", "100% 良性", "肯定良性", "可能良性")),
        "m": ("非HCC特异", ("良性", "100% 良性")),
        "tiv": ("静脉瘤栓", ("良性",)),
    }
    for m in re.finditer(r"LR-?\s?(1|2|3|4|5|M|TIV)\b[^。；\n]{0,40}", a or "", re.I):
        code = m.group(1).lower()
        span = m.group(0)
        if code in _LR_DEF:
            _want, bad_words = _LR_DEF[code]
            for bw in bad_words:
                if bw in span:
                    probs.append(
                        f"LI-RADS 类别定义错误：LR-{code.upper()} 是「{_want}」，"
                        f"答案却说「{bw}」"
                    )
                    break

    # ---- 闸 16（2026-11-09 抽取式后新增）：摘录必须回答 query
    #
    # ## 为什么需要（实测漏网）
    #
    # 加了抽取式约束后，医学错误消失了，但出现新问题：**答非所问**。
    #
    #   实测案例 1：
    #     Q: 两套指南对甲状腺癌局部侵犯的描述有何不同？
    #     A: 「PTC 易早期发生区域淋巴转移…」← 讲的是淋巴转移，不是局部侵犯
    #
    #   实测案例 2：
    #     Q: 这个 5 分结节按两套指南分别归哪一类？该听谁的？
    #     A: 「诊疗规范指出 4a 类具有 1 种恶性征象；C-TIRADS 指出 4a 类具有 1 种恶性征象」
    #        ← 两个来源给了**同一个定义**，等于没回答"分别归哪类"
    #
    # **摘录真实性（闸 B）与相关性是两件事**：抄得再准，抄错了段落也是废的。
    #
    # ## 判据
    #
    # query 的「内容要素」（数值/分级码/关键术语）必须出现在答案里。
    # 只查要素**是否被回答**，不查答案是否还讲了别的（摘录常带上下文，这是合理的）。
    _Q_ELEM = (
        re.compile(r"\d+(?:\.\d+)?\s*(?:mm|cm|%|个月|周|岁|分)", re.I),
        re.compile(r"(?:C-?TIRADS|TI-?RADS|LI-?RADS|CEUS|LR-?[1-5M]|TR\s?[1-5]|[1-6]\s*[A-Ca-c]?\s*类)", re.I),
    )
    q_elems: list[str] = []
    for pat in _Q_ELEM:
        q_elems.extend(m.group(0) for m in pat.finditer(q or ""))
    # 关键的临床动作词（问了"要不要穿刺"就要看到穿刺相关的内容）
    _ACTION_WORDS = ("穿刺", "活检", "FNA", "手术", "消融", "随访", "复查", "处理", "治疗")
    q_actions = [w for w in _ACTION_WORDS if w in (q or "")]
    a_norm = re.sub(r"\s+", "", a or "").lower()

    if q_elems:
        miss = [
            e for e in dict.fromkeys(q_elems)
            if re.sub(r"\s+", "", e).lower() not in a_norm
        ]
        if miss:
            probs.append(f"答案没覆盖 query 的要素 {miss[:3]}（答非所问）")
    if q_actions:
        if not any(w in (a or "") for w in q_actions):
            probs.append(f"query 问「{'/'.join(q_actions)}」，答案没提任何处置（答非所问）")

    # 修 1（2026-11-09 实测补）：query 无数字/无动作词时，用**中文主题词**判相关性。
    #
    # 实测漏网：「两套指南对甲状腺癌**局部侵犯**的描述有何不同？」
    #   query 里既无数字也无动作词 → 前面两条判据都不触发。
    #   而答案讲的是「PTC 易早期发生区域**淋巴转移**」—— 主题完全跑偏。
    #
    # 判据：从 query 抽 ≥2 字的临床主题词（去停用词与疑问词），
    # 要求答案命中 ≥1 个主题词。
    _STOP_ZH = set(
        "的是了在和与或及等这那你我他她它之其中为以所被把从到对按该不没有可就还要会能"
        "什么怎么如何哪些多少是否请问有无区别差异不同情况时候"
    )
    # 从 query 抽 2-3 字滑窗作为主题词候选
    q_terms: list[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", q or ""):
        for n in (2, 3):
            for i in range(len(run) - n + 1):
                w = run[i : i + n]
                if not (set(w) & _STOP_ZH):
                    q_terms.append(w)
    q_terms = [t for t in dict.fromkeys(q_terms) if len(t) >= 2]
    if q_terms and not q_elems and not q_actions:
        hit_terms = [t for t in q_terms if t in (a or "")]
        if not hit_terms:
            probs.append(
                f"答案与 query 主题不符（query 主题词 {q_terms[:3]} 一个都没出现在答案里）"
            )

    # ---- 闸 17（2026-11-09 新增）：答案不许整句重复
    #
    # 实测漏网：
    #   「专家委员会经过认真讨论，认为有必要建立符合中国国情的C-TIRADS。
    #     基于以上现状，专家委员会经过认真讨论，认为有必要建立符合中国国情的C-TIRADS。」
    # 修 2（2026-11-09 实测补）：两句只差**前缀**（「原文提到：X」与「基于以上现状，X」）也算重复。
    #
    # 实测漏网：
    #   「原文提到：专家委员会…建立 C-TIRADS。
    #     基于以上现状，专家委员会…建立 C-TIRADS。」  ← 只差前缀，正文一模一样
    #
    # 判据：剥掉「原文提到：」「基于…，」这类前缀后再比对。
    _sents_a = [x.strip() for x in re.split(r"[。；;]", a or "") if len(x.strip()) >= 12]

    def _strip_prefix(x: str) -> str:
        x = re.sub(r"^(?:原文提到|指南指出|根据原文)[：:]\s*", "", x)
        x = re.sub(r"^(?:基于|根据|由此|因此)[^，,。]{0,15}[，,]", "", x)
        return x.strip()

    _cores = [_strip_prefix(x) for x in _sents_a]
    _cores = [c for c in _cores if len(c) >= 10]
    if len(_cores) != len(set(_cores)):
        probs.append("答案里有整句重复（去掉前缀后正文相同）")

    # ---- 闸 5：gold 一致性
    gold = row.get("gold") or []
    if not gold:
        probs.append("gold 为空")
    else:
        for g in gold:
            cid = g.get("chunk_id") or ""
            if cid and cid not in existing_ids:
                probs.append(f"gold chunk 不存在: {cid[:50]}")
                break

    return probs


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="合成数据质量门（p121）")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--kb-version", default="kb_v2.1.0")
    ap.add_argument("--limit", type=int, default=20, help="展示前 N 个问题项")
    args = ap.parse_args(argv)

    rows = [
        json.loads(l)
        for l in Path(args.dataset).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    # **只检已过闸的条目**（2026-11-09 实测修正）
    #
    # 原来只滤 `error`，但 p122 的输出里有 `rejected: true` 的条目 ——
    # 它们本来就没有 query/answer（结构不同：只有 scenario_id + reason），
    # 拿它们进闸会报「query 为空 / 答案过短 / gold 为空」，
    # 把真实的闸通过率稀释了（实测 44 条里 17 条是被拒条目，
    # 导致"34.1% 不合规"这个数字毫无意义）。
    #
    # 要检「已过闸的条目质量」，就该只看 `gate_passed: true` 的。
    _before = len(rows)
    rows = [r for r in rows if "error" not in r and not r.get("rejected")]
    _skipped = _before - len(rows)
    if _skipped:
        print(f"（跳过 {_skipped} 条被拒/出错条目，只检已过闸的）")

    kb_text = await load_kb_texts(args.kb_version)
    print(f"数据集 {len(rows)} 条；KB 全文 {len(kb_text)} 字")

    # gold chunk id 集合（用于闸 5）
    from app.config.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as s:
        ids = {
            r[0]
            for r in (
                await s.execute(
                    text("SELECT chunk_id FROM knowledge_chunks WHERE kb_version = :v"),
                    {"v": args.kb_version},
                )
            ).fetchall()
        }

    gates = {"闸1 引文真实": 0, "闸2 体系一致": 0, "闸3 问句形态": 0, "闸4 答案信息量": 0, "闸5 gold一致": 0}
    bad_rows: list[tuple[str, list[str]]] = []
    for r in rows:
        probs = check_one(r, kb_text, ids)
        if probs:
            bad_rows.append((r.get("scenario_id") or r.get("query", "")[:20], probs))
            for p in probs:
                if "编造文献名" in p:
                    gates["闸1 引文真实"] += 1
                elif "跨体系" in p:
                    gates["闸2 体系一致"] += 1
                elif "query" in p:
                    gates["闸3 问句形态"] += 1
                elif "答案" in p:
                    gates["闸4 答案信息量"] += 1
                elif "gold" in p:
                    gates["闸5 gold一致"] += 1

    print(f"\n=== 五道闸 ===")
    for k, v in gates.items():
        print(f"  {k}: {v} 条不合规")
    n_bad = len(bad_rows)
    print(f"\n总不合规条目: {n_bad}/{len(rows)} = {n_bad/max(len(rows),1)*100:.1f}%")

    for sid, probs in bad_rows[: args.limit]:
        print(f"\n  {sid}:")
        for p in probs:
            print(f"    - {p}")
    if n_bad > args.limit:
        print(f"\n  … 还有 {n_bad - args.limit} 条")

    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
