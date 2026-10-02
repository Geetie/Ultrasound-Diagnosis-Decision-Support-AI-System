"""守卫：`POST /api/v1/diagnosis/request` 必须**先提交请求行、再入队**。

## 为什么需要它（2026-09-28 真机链路冒烟暴露的阻断级缺陷）

`DiagnosisRepository.create_request()` 只 `flush()`、不提交；端点原先
"flush 完就直接 `enqueue_diagnosis(request_id)`"。而 worker BLPOP 到 id 后是用
**另一个会话**去读库的 —— 未提交的行对它不可见，于是：

    真机实测：pipeline_request_not_found
    → diagnosis_results 里的行永久停在 status='processing'
    → GET /api/v1/diagnosis/result/{id} 一直 404（没有重试会救它）

这是个**竞态**：提交足够快时看不出来，worker 空转（BLPOP 立刻返回）时必然踩中 ——
所以本地/单测都不报，只有真机连续跑才现形。

## 为什么用源码级断言而不是行为测试

要行为验证这个顺序，得把端点的依赖（限流、三个仓库、审计、鉴权）全替身，还要
精确复现"入队后立刻读"的竞态；那套脚手架本身比被守卫的三行代码更容易腐坏。
这里钉的是**顺序不变量**本身：在 `submit_diagnosis_request` 的函数体里，
`commit()` 必须出现在 `enqueue_diagnosis(` 之前。
"""
from __future__ import annotations

import ast
import pathlib

MODULE = pathlib.Path(__file__).resolve().parents[3] / "app" / "api" / "v1" / "diagnosis.py"


def _function_source(name: str) -> str:
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"未找到函数 {name}（端点改名后请同步本守卫）")


def _position(source: str, needle: str) -> int:
    index = source.find(needle)
    assert index != -1, f"源码里找不到 {needle!r}"
    return index


class TestRequestIdIsSharedAcrossTheChain:
    """同一个 request_id 必须贯穿"落库 / 入队 / 响应"，不能各生成各的。"""

    def test_端点把自己的request_id传给仓库(self):
        body = _function_source("submit_diagnosis_request")

        assert "request_id=request_id" in body, (
            "create_request 必须收到端点生成的 request_id；让它自行生成会与队列/响应里的"
            " id 分叉 → worker `pipeline_request_not_found` → 请求永久 processing"
        )

    def test_入队用的是同一个request_id(self):
        body = _function_source("submit_diagnosis_request")

        commit_at = _position(body, "await diagnosis_repo.db.commit()")
        enqueue_at = _position(body, "enqueue_diagnosis(request_id)")
        assert commit_at < enqueue_at

    def test_仓库支持外部指定id(self):
        """行为级：传了 request_id 就必须用传进来的那个。"""
        import asyncio

        from app.models.schemas import ResponseTier
        from app.repositories.diagnosis_repository import DiagnosisRepository

        class _FakeSession:
            def __init__(self) -> None:
                self.added: list = []

            def add(self, obj) -> None:
                self.added.append(obj)

            async def flush(self) -> None:
                return None

        fake = _FakeSession()
        repo = DiagnosisRepository(fake)  # type: ignore[arg-type]

        returned = asyncio.run(
            repo.create_request(
                session_id="s-1",
                organ="thyroid",
                response_tier=ResponseTier.FULL,
                request_id="req_external_0001",
            )
        )

        assert returned == "req_external_0001"
        assert fake.added and fake.added[0].request_id == "req_external_0001"


class TestCommitBeforeEnqueue:
    def test_提交早于入队(self):
        body = _function_source("submit_diagnosis_request")

        commit_at = _position(body, "await diagnosis_repo.db.commit()")
        enqueue_at = _position(body, "enqueue_diagnosis(")

        assert commit_at < enqueue_at, (
            "入队必须发生在提交之后：worker 用另一个会话读库，未提交的行它看不见 → "
            "pipeline_request_not_found → 请求永久停在 processing"
        )

    def test_提交发生在创建请求之后(self):
        body = _function_source("submit_diagnosis_request")

        create_at = _position(body, "create_request(")
        commit_at = _position(body, "await diagnosis_repo.db.commit()")

        assert create_at < commit_at, "提交必须发生在 create_request 之后（否则无行可提交）"

    def test_守卫没找错函数(self):
        body = _function_source("submit_diagnosis_request")

        assert "rate_limit" in body
        assert "consent_repo" in body
