"""BUSIAdapter 契约守卫：禁止"路径不存在 → 静默合成样本"，并兼容真实公开数据集 schema。

## 为什么有这条测试

`busi_adapter` 原来的行为是：路径不存在时 `logger.warning` + **返回 6 条合成占位样本**
（`image_path` 指向不存在的文件）。后果不是"少测一点"，而是
**评估脚本在空目录上照样"跑出" I2T@k 等指标** —— 与 `MockEmbeddingService`
让检索指标失真属同一类缺陷（本会话已因该类缺陷改过建库链路）。

真实公开数据集 schema 也与旧契约不符：US-365K（HuggingFace `JJY-0823/US-365K`，
其 `test.jsonl` 实测 71919 行）用的是
`{"image": "<file>.jpg", "caption": "..."}` —— 既没有 `image_path`/`text`，
也**没有 label**。因此这里同时钉住两件事：别名兼容、以及**缺 label 时不得伪造**。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.embedding.busi_adapter import BUSIAdapter


class TestNoSilentSyntheticFallback:
    def test_missing_path_raises_by_default(self, tmp_path: Path):
        adapter = BUSIAdapter(str(tmp_path / "nope"))

        with pytest.raises(FileNotFoundError, match="allow_synthetic"):
            adapter.load(max_samples=4)

    def test_error_message_explains_the_risk(self, tmp_path: Path):
        adapter = BUSIAdapter(str(tmp_path / "nope"))

        with pytest.raises(FileNotFoundError) as exc:
            adapter.load()

        assert "合成样本" in str(exc.value)
        assert "评估" in str(exc.value)

    def test_explicit_opt_in_still_supported_for_smoke(self, tmp_path: Path):
        adapter = BUSIAdapter(str(tmp_path / "nope"), allow_synthetic=True)

        samples = adapter.load(max_samples=6)

        assert len(samples) == 6
        assert all(s.image_path.startswith("<synthetic>/") for s in samples)

    def test_synthetic_marker_is_detectable(self, tmp_path: Path):
        """合成样本必须**可被识别**（路径带 `<synthetic>` 前缀），否则下游无法拒绝。"""
        samples = BUSIAdapter(str(tmp_path / "nope"), allow_synthetic=True).load()

        assert all("<synthetic>" in s.image_path for s in samples)


class TestRealPublicDatasetSchema:
    """兼容 US-365K 的真实字段名（`image` / `caption`），且不伪造缺失的标签。"""

    def _write(self, tmp_path: Path, rows: list[dict]) -> Path:
        p = tmp_path / "test.jsonl"
        p.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )
        return p

    def test_accepts_us365k_field_names(self, tmp_path: Path):
        path = self._write(
            tmp_path,
            [
                {"image": "236482_040210_abc.jpg", "caption": "胆囊及胆管超声可见占位"},
                {"image": "007669_000446_def.jpg", "caption": "腹膜后及大血管所见"},
            ],
        )

        samples = BUSIAdapter(str(path)).load()

        assert [s.image_path for s in samples] == [
            "236482_040210_abc.jpg",
            "007669_000446_def.jpg",
        ]
        assert samples[0].text == "胆囊及胆管超声可见占位"

    def test_missing_label_is_not_fabricated(self, tmp_path: Path):
        """无标注语料的 label 必须是 None —— 伪造会污染任何按标签统计的指标。"""
        path = self._write(tmp_path, [{"image": "a.jpg", "caption": "甲状腺低回声结节"}])

        samples = BUSIAdapter(str(path)).load()

        assert samples[0].label is None

    def test_legacy_schema_still_supported(self, tmp_path: Path):
        path = self._write(
            tmp_path,
            [{"image_path": "/x/b1.png", "text": "良性结节", "label": "benign"}],
        )

        samples = BUSIAdapter(str(path)).load()

        assert samples[0].image_path == "/x/b1.png"
        assert samples[0].label == "benign"

    def test_max_samples_is_honoured(self, tmp_path: Path):
        path = self._write(
            tmp_path, [{"image": f"{i}.jpg", "caption": f"c{i}"} for i in range(5)]
        )

        assert len(BUSIAdapter(str(path)).load(max_samples=2)) == 2
