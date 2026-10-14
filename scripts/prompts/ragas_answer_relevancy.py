"""RAGAS 官方 answer_relevancy prompt（照抄原文，不自造）。

来源：ragas 官方仓库 vibrantlabsai/ragas（同 explodinggradients/ragas）
路径：src/ragas/metrics/collections/answer_relevancy/util.py
      （评分公式见同目录 metric.py 的 AnswerRelevancy.ascore）
抓取：2026-10-15，与 `ragas_faithfulness.py` **同一 tree sha**
      （298b68274234c060deacab3cf5fb52aa3a20e885）
许可：Apache-2.0

来源自证（怎么确认抄的是同一份东西，而不是"大概是这个意思"）：
* 用同一个 sha 抓 `faithfulness/util.py`，与本地参考副本
  `~/uai/data/ragas_ref/faithfulness_util.py` **sha256 完全一致**
  （255ebb3305934a3c5769ecfd395d5825050a62b3f03f09b5e18647c3f651776d）；
* answer_relevancy 的 util/metric/`__init__` 原始文件已存档在
  `~/uai/data/ragas_ref/answer_relevancy_{util,metric,__init__}.py`。

本文件仅摘录 prompt 的 instruction 与 examples 原文（去掉 pydantic/ragas 框架
依赖），渲染方式与官方 BasePrompt 一致：instruction + 示例对 + 真实输入。

官方口径（**必须照抄，不许自造评分规则**，见 metric.py）：
    strictness 次生成问题（默认 3）
    每问同时给 noncommittal 标记（1 = 答案敷衍/回避）
    score = cos(生成问题, 原始问题) 的均值 × int(not 全部 noncommittal)
即：**所有生成问题都被判为 noncommittal 时，分数直接为 0**。
"""
from __future__ import annotations

#: 官方 `AnswerRelevancy(strictness=3)` 的默认值（生成几个问题）。
ANSWER_RELEVANCE_STRICTNESS = 3

#: 本仓库允许的最大 strictness（判官调用次数上限，防跑批时长失控）。
ANSWER_RELEVANCE_MAX_STRICTNESS = 5

# --- AnswerRelevancePrompt（答案 → 能由它回答的问题 + 是否敷衍）---
ANSWER_RELEVANCE_INSTRUCTION = (
    "Generate a question for the given answer and identify if the answer is noncommittal.\n"
    "Give noncommittal as 1 if the answer is noncommittal (evasive, vague, or ambiguous) "
    "and 0 if the answer is substantive.\n"
    'Examples of noncommittal answers: "I don\'t know", "I\'m not sure", "It depends".'
)

#: 官方 examples 原文（三对）。每项 = (answer, question, noncommittal)。
ANSWER_RELEVANCE_EXAMPLES: tuple[tuple[str, str, int], ...] = (
    (
        "Albert Einstein was born in Germany.",
        "Where was Albert Einstein born?",
        0,
    ),
    (
        "The capital of France is Paris, a city known for its architecture and culture.",
        "What is the capital of France?",
        0,
    ),
    (
        "I don't know about the groundbreaking feature of the smartphone invented in "
        "2023 as I am unaware of information beyond 2022.",
        "What was the groundbreaking feature of the smartphone invented in 2023?",
        1,
    ),
)
