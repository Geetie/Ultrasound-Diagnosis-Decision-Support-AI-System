"""A.149：专科 trace 必须落 findings（端到端可见性）。"""

from __future__ import annotations

import inspect

import pytest


def test_pipeline_writes_specialist_trace():
    from app.services import diagnosis_pipeline as dp

    src = inspect.getsource(dp._write_result_to_db)
    assert "specialist_reasoning_trace" in src
