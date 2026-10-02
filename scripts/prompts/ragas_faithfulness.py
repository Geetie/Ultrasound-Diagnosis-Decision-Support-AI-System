"""RAGAS 官方 faithfulness prompt（照抄原文，不自造）。

来源：ragas 官方仓库 vibrantlabsai/ragas（exiting explodinggradients/ragas）
路径：src/ragas/metrics/collections/faithfulness/util.py
抓取：2026-09-29，main 分支（tree sha 298b68274234c060deacab3cf5fb52aa3a20e885）
许可：Apache-2.0

本文件仅摘录 prompt 的 instruction 与 examples 原文（去掉 pydantic/ragas 框架
依赖），渲染方式与官方 BasePrompt 一致：instruction + 示例对 + 真实输入。
"""

# --- StatementGeneratorPrompt（答案 → 原子断言分解）---
STATEMENT_GENERATOR_INSTRUCTION = (
    "Given a question and an answer, analyze the complexity of each sentence "
    "in the answer. Break down each sentence into one or more fully "
    "understandable statements. Ensure that no pronouns are used in any "
    "statement."
)

STATEMENT_GENERATOR_EXAMPLE = {
    "question": (
        "Who was Albert Einstein and what is he best known for?"
    ),
    "answer": (
        "He was a German-born theoretical physicist, widely acknowledged to "
        "be one of the greatest and most influential physicists of all time. "
        "He was best known for developing the theory of relativity, he also "
        "made important contributions to the development of the theory of "
        "quantum mechanics."
    ),
    "statements": [
        "Albert Einstein was a German-born theoretical physicist.",
        (
            "Albert Einstein is recognized as one of the greatest and most "
            "influential physicists of all time."
        ),
        "Albert Einstein was best known for developing the theory of relativity.",
        (
            "Albert Einstein made important contributions to the development "
            "of the theory of quantum mechanics."
        ),
    ],
}

# --- NLIStatementPrompt（断言 × 上下文 → 逐条 0/1 判定）---
NLI_STATEMENT_INSTRUCTION = (
    "Your task is to judge the faithfulness of a series of statements based "
    "on a given context. For each statement you must return verdict as 1 if "
    "the statement can be directly inferred based on the context or 0 if the "
    "statement can not be directly inferred based on the context."
)

NLI_STATEMENT_EXAMPLE = {
    "context": (
        "John is a student at XYZ University. He is pursuing a degree in "
        "Computer Science. He is enrolled in several courses this semester, "
        "including Data Structures, Algorithms, and Database Management. John "
        "is a diligent student and spends a significant amount of time "
        "studying and completing assignments. He often stays late in the "
        "library to work on his projects."
    ),
    "statements": [
        "John is majoring in Biology.",
        "John is taking a course on Artificial Intelligence.",
        "John is a dedicated student.",
        "John has a part-time job.",
    ],
    "verdicts": [
        {
            "statement": "John is majoring in Biology.",
            "reason": (
                "John's major is explicitly stated as Computer Science, not "
                "Biology."
            ),
            "verdict": 0,
        },
        {
            "statement": "John is taking a course on Artificial Intelligence.",
            "reason": (
                "The context mentions courses in Data Structures, Algorithms, "
                "and Database Management, but does not mention Artificial "
                "Intelligence."
            ),
            "verdict": 0,
        },
        {
            "statement": "John is a dedicated student.",
            "reason": (
                "The context states that John is a diligent student who "
                "spends a significant amount of time studying and completing "
                "assignments."
            ),
            "verdict": 1,
        },
        {
            "statement": "John has a part-time job.",
            "reason": (
                "There is no information in the context about John having a "
                "part-time job."
            ),
            "verdict": 0,
        },
    ],
}

# --- 官方评分公式（metric.py _compute_score）---
# Faithfulness = 支持（verdict=1）的断言数 / 总断言数
# 无断言（分解不出任何 statement）时官方记 score=1.0
