"""守卫：发给安全网关的 `output_data` 只能有一个构造点，且两个调用点都用它。

## 为什么需要它（2026-09-29 真机第 1 例真实诊断暴露）

项目里有两处调用 `safety_gateway_client.check_output`：

* 写路径 `app/services/diagnosis_pipeline.py`（诊断产出时）；
* 读路径 `app/api/v1/diagnosis.py::get_diagnosis_result`（结果被查询时再校验一次输出安全）。

两边各自拼 payload → 必然漂移。真机症状：

    写路径发 {"organ","findings","recommendation"}（**无患者条件**）
    读路径发 worker 落库的整包 findings（organ/fusion_result/sg_result/timings_ms）

而安全网关的禁忌判定在"拿不到条件数据"时按设计判**命中**，于是同一份诊断
在写处 pass、在读处"检测到 4 条禁忌" → `requires_manual_review` 恒为真。
本守卫钉住"唯一构造点"这个不变量。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.services.sg_output_data import build_sg_output_data, extract_findings_text

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content


class _Fusion:
    def __init__(self, content: str) -> None:
        self.final_response = _Response(content)


class TestBuilderAddsConditionsOnlyWhenKnown:
    def test_空列表要带上表示已确认无条件(self):
        data = build_sg_output_data(
            organ="thyroid", findings="低回声结节", patient_context={"conditions": []}
        )

        assert data["diagnosis_data"] == {"patient_conditions": []}

    def test_有具体条件时如实转发(self):
        data = build_sg_output_data(
            organ="thyroid",
            findings="低回声结节",
            patient_context={"conditions": ["pregnancy"]},
        )

        assert data["diagnosis_data"]["patient_conditions"] == ["pregnancy"]

    def test_未提供条件时不带该键(self):
        """方向性关键：None 不能当成"无条件"，否则静默关掉安全网关的保守判定。"""
        for context in (None, {"conditions": None}, {}, "not-a-dict"):
            data = build_sg_output_data(
                organ="thyroid", findings="低回声结节", patient_context=context
            )
            assert "diagnosis_data" not in data, context

    def test_基础字段与形状(self):
        data = build_sg_output_data(organ="thyroid", findings="x" * 600)

        assert data["organ"] == "thyroid"
        assert data["findings"] == "x" * 600
        # recommendation 默认由 findings 截断而来，保证两个调用点形状一致
        assert data["recommendation"] == "x" * 500

    def test_无发现文本时不塞空字符串(self):
        data = build_sg_output_data(organ="thyroid", findings="")

        assert "findings" not in data
        assert "recommendation" not in data


class TestExtractFindingsHandlesBothShapes:
    def test_对象形态(self):
        assert extract_findings_text(_Fusion("低回声结节")) == "低回声结节"

    def test_dict形态_读路径拿到的就是它(self):
        stored = {"fusion_result": {"final_response": {"content": "低回声结节"}}}

        assert extract_findings_text(stored["fusion_result"]) == "低回声结节"

    def test_生产落库的扁平形状必须认得(self):
        """A.88 反证腿：真实落库形状是**扁平 content**，不是 final_response。

        真机核对（服务器 PG 最近 20 条带 fusion_result 的行）：**20/20 扁平**、
        `0/20` 含 final_response。所以这条用例是"读路径到底读不读得到正文"的
        唯一判据 —— 去掉扁平分支，这条立刻红（而在修之前它是绿的，
        因为样本形状是手写的）。
        """
        from app.services.diagnosis_pipeline import _fusion_result_to_dict

        # 用**真实写入侧**的产物做样本：样本形状与生产同源，不再手写
        stored = _fusion_result_to_dict(
            _Fusion("TI-RADS 4a，建议细针穿刺")
        )

        assert "final_response" not in stored, "写入侧不应有 final_response（形状自证）"
        assert stored["content"]
        assert extract_findings_text(stored) == "TI-RADS 4a，建议细针穿刺"

    def test_扁平形状优先且不掩盖嵌套形状(self):
        both = {"content": "扁平结论", "final_response": {"content": "嵌套结论"}}

        assert extract_findings_text(both) == "扁平结论"

    def test_缺失或异常形态返回空串(self):
        for value in (None, {}, {"final_response": None}, "", 0, {"final_response": {}}):
            assert extract_findings_text(value) == ""


def _functions_calling(src: str, name: str) -> set[str]:
    """列出源码里调用了 `name(...)` 的函数名（AST 判定，注释/字符串不算）。"""
    found: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if called == name:
                found.add(node.name)
    return found


class TestBothCallSitesUseTheSingleBuilder:
    @pytest.mark.parametrize(
        "relative_path",
        ["app/services/diagnosis_pipeline.py", "app/api/v1/diagnosis.py"],
    )
    def test_凡调check_output的函数都必须用构造函数(self, relative_path):
        src = (REPO_ROOT / relative_path).read_text(encoding="utf-8")

        callers = _functions_calling(src, "check_output")
        builders = _functions_calling(src, "build_sg_output_data")

        assert callers, f"{relative_path} 里没找到 check_output 调用（守卫需更新？）"
        assert callers <= builders, (
            f"{relative_path} 里这些函数发 SG 载荷却没走唯一构造点："
            f"{sorted(callers - builders)} —— 两边形状不同会让同一份诊断被判出两种结论"
        )

    def test_读路径不再把整包findings当载荷(self):
        src = (REPO_ROOT / "app/api/v1/diagnosis.py").read_text(encoding="utf-8")

        assert "output_data=result.findings" not in src, (
            "读路径不能把 worker 落库的整包 findings 当 output_data（形状与写路径不同）"
        )
