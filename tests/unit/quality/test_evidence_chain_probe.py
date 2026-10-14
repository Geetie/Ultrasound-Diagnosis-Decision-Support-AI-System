"""`scripts/audit/p30_evidence_chain.py` 的判定器守卫（A.62 批 1 / 批 2）。

为什么要单独钉住：这个探针的结论会直接进台账（「我们记的证据是不是等于模型当时看到的」），
判定器一旦"永远 PASS"，就会把**证据链断掉**说成"没问题"。所以：
1. 关系必须各自判对（含"顺序不同""子集非前缀""记录含未知块"这些容易漏的形态）；
2. **批 2 新增**：落库里的"prompt 之外"块要能区分「CRAG 补充合并造成（可解释）」与
   「来源不明（要继续查）」—— 否则会笼统报 MISMATCH，把真实成因（CRAG 新块没进 prompt）
   和未知问题混为一谈；
3. 探针自带的反证腿必须全绿（自证不是拍脑袋）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PROBE = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p30_evidence_chain.py"


def _load_probe():
    spec = importlib.util.spec_from_file_location("_p30_under_test", _PROBE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_BASE = ["c1", "c2", "c3", "c4", "c5"]


@pytest.mark.parametrize(
    ("recorded", "retrieved", "expect"),
    [
        # 现实主力形态：prompt 用 5 条、落库只留前 3 条（设计使然）
        (["c1", "c2", "c3"], _BASE, "PREFIX_TRUNCATED"),
        (_BASE, _BASE, "PREFIX_TRUNCATED"),
        ([], _BASE, "NOT_RECORDED"),
        (["c5", "c4", "c3", "c2", "c1"], _BASE, "ORDER_MISMATCH"),
        (["c2", "c4"], _BASE, "SUBSET_NONPREFIX"),
        (["c1", "cX"], _BASE, "MISMATCH"),
        (["c1"], [], "NO_RETRIEVAL"),
    ],
)
def test_六种关系各自判对(recorded, retrieved, expect):
    probe = _load_probe()
    assert probe.classify(list(recorded), list(retrieved)) == expect


@pytest.mark.parametrize(
    ("recorded", "crag_ids", "expect"),
    [
        # 落库多了 cZ，而 cZ 出现在 CRAG 合并里 → 可解释（模型从未看到 CRAG 补充块）
        (["c1", "c2", "cZ"], ["cZ"], "CRAG_SUPPLEMENT"),
        # 同样多了一块，但它**不在** CRAG 合并里 → 来源不明，必须继续查
        (["c1", "c2", "cY"], ["cZ"], "MISMATCH"),
        # 没有 CRAG 留痕时不许"猜成 CRAG"（宁严不宽）
        (["c1", "c2", "cZ"], [], "MISMATCH"),
        # CRAG 只有部分解释得通（另一块来源不明）→ 仍判 MISMATCH
        (["c1", "c2", "cZ", "cY"], ["cZ"], "MISMATCH"),
        # 落库 ⊆ prompt 的正常形态不受 CRAG 干扰
        (["c1", "c2"], ["cZ"], "PREFIX_TRUNCATED"),
    ],
)
def test_CRAG补充块要能区分可解释与来源不明(recorded, crag_ids, expect):
    """批 2 核心：`CRAG_SUPPLEMENT` 只能由**留痕凭据**支撑，不许为了好看放宽。"""
    probe = _load_probe()
    assert probe.classify(list(recorded), _BASE, list(crag_ids)) == expect


def test_判定器不是永远PASS():
    """同一批输入里必须出现多种判定——否则说明判定逻辑退化成常量。"""
    probe = _load_probe()
    got = {
        probe.classify(["c1", "c2"], _BASE),
        probe.classify([], _BASE),
        probe.classify(["cX"], _BASE),
        probe.classify(["c1"], []),
    }
    assert len(got) == 4, f"判定结果退化成常量：{got}"


def test_日志解析_两行按request_id配对(tmp_path):
    """留痕行尾有兄弟字段（`prompt_chunks=5`），解析必须**截到最后一个 `]`** —— 踩过这个坑。"""
    probe = _load_probe()
    log = tmp_path / "api.log"
    log.write_text(
        "2026-10-13 23:22:54 [info     ] evidence_chain organ=thyroid "
        "prompt_chunk_ids=['a#1', 'b#0'] prompt_chunks=2 request_id=req_1\n"
        "2026-10-13 23:22:58 [info     ] evidence_chain organ=thyroid "
        "recorded_chunks=1 recorded_ids=['a#1'] request_id=req_1\n"
        "noise line without marker\n",
        encoding="utf-8",
    )
    entries = probe.parse_evidence_log(log)
    assert len(entries) == 1
    assert entries[0]["prompt_chunk_ids"] == ["a#1", "b#0"]
    assert entries[0]["recorded_ids"] == ["a#1"]
    assert "parse_errors" not in entries[0]


def test_日志解析_CRAG留痕按request_id并集(tmp_path):
    """`crag_evidence_merge` 三类 id 都要抠出来（它是 `CRAG_SUPPLEMENT` 的唯一凭据）。"""
    probe = _load_probe()
    log = tmp_path / "api.log"
    log.write_text(
        "2026-10-13 [info ] evidence_chain organ=cardiac prompt_chunk_ids=['p#1'] "
        "prompt_chunks=1 request_id=req_9\n"
        "2026-10-13 [info ] crag_evidence_merge request_id=req_9 mode=reinvoke "
        "original_chunks=1 new_chunks=2 final_chunks=2 "
        "original_ids=['o#1'] new_ids=['n#1', 'n#2'] "
        "final_ids=['o#1', 'n#1', 'n#2']\n",
        encoding="utf-8",
    )
    entries = probe.parse_evidence_log(log)
    assert len(entries) == 1
    e = entries[0]
    assert e["crag_calls"] == 1
    assert e["crag_modes"] == ["reinvoke"]
    assert e["crag_final_ids"] == ["o#1", "n#1", "n#2"]
    assert e["crag_new_ids"] == ["n#1", "n#2"]
    assert e["prompt_chunk_ids_union"] == ["p#1"]


def test_日志解析_解析失败必须可见(tmp_path):
    """坏行不许静默变空 list（那会让探针报"日志里没有留痕"的假象）。"""
    probe = _load_probe()
    log = tmp_path / "api.log"
    log.write_text(
        "evidence_chain organ=thyroid prompt_chunk_ids=[oops request_id=req_2\n",
        encoding="utf-8",
    )
    entries = probe.parse_evidence_log(log)
    assert entries and entries[0].get("parse_errors"), "解析失败要被记录下来"


def test_探针自带反证腿全绿():
    probe = _load_probe()
    counter = probe._counter_evidence()
    assert counter, "反证腿不能为空"
    bad = [name for name, ok, _ in counter if not ok]
    assert not bad, f"反证腿失败：{bad}"
