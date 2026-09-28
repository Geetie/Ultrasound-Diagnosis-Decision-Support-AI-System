"""校验下载到的权重**可加载**（不产出任何指标，只做结构与可读性检查）。

用法：`~/uai/venv/bin/python ~/server_verify_models.py`

检查项：
1. GGUF：文件头魔数（`GGUF`）+ 版本 + 元数据键数（用官方解析器，不靠猜）；
2. HF 目录：`config.json` 可被 transformers 解析、tokenizer 可实例化、
   `model.safetensors` 存在且大小合理（**不加载权重**，避免在共享机上吃显存/内存）；
3. 记录每个模型的存在性、体积、关键配置（维度/层数），全部为**事实描述**。
"""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import sys

MODELS = pathlib.Path(os.path.expanduser("~/uai/models"))


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def dir_size(path: pathlib.Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def check_gguf(path: pathlib.Path) -> None:
    print(f"[GGUF] {path.name}  size={human(path.stat().st_size)}")
    magic = path.open("rb").read(4)
    if magic != b"GGUF":
        print(f"  ✗ 魔数不是 GGUF（读到 {magic!r}）—— 文件可能是 HTML 错误页或未下完")
        return
    try:
        from gguf import GGUFReader
    except ImportError:
        print("  ~ 魔数 OK；未装 gguf 解析器，跳过元数据读取")
        return
    reader = GGUFReader(str(path))
    fields = reader.fields
    interesting = [
        "general.architecture",
        "general.name",
        "qwen2.context_length",
        "qwen2.embedding_length",
        "qwen2.block_count",
    ]
    for key in interesting:
        field = fields.get(key)
        if field is None or not len(field.parts):
            continue
        try:
            value = field.parts[field.data[0]].tolist()
        except Exception:
            value = None
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        # GGUF 的字符串类型以字节数组给出，直接打印是数字序列（可读性为零）
        if isinstance(value, list) and value and all(isinstance(b, int) for b in value):
            with contextlib.suppress(UnicodeDecodeError):
                value = bytes(value).decode("utf-8")
        print(f"  {key} = {value}")
    print(f"  ✓ 元数据键数 = {len(fields)}")


def check_hf_dir(path: pathlib.Path) -> None:
    print(f"[HF] {path.name}  size={human(dir_size(path))}")
    config_file = path / "config.json"
    if not config_file.exists():
        print("  ✗ 缺 config.json（未下完或不是 transformers 目录）")
        return
    config = json.loads(config_file.read_text(encoding="utf-8"))
    print(
        "  config: "
        + ", ".join(
            f"{k}={config.get(k)}"
            for k in ("model_type", "hidden_size", "num_hidden_layers", "architectures")
            if k in config
        )
    )
    weights = sorted(path.glob("*.safetensors")) + sorted(path.glob("pytorch_model*.bin"))
    if weights:
        print(f"  权重文件: {[w.name for w in weights]} 共 {human(sum(w.stat().st_size for w in weights))}")
    else:
        print("  ✗ 没有找到权重文件")
    try:
        from transformers import AutoConfig, AutoTokenizer

        AutoConfig.from_pretrained(str(path), local_files_only=True)
        AutoTokenizer.from_pretrained(str(path), local_files_only=True)
        print("  ✓ transformers 能解析 config 与 tokenizer")
    except Exception as exc:
        print(f"  ✗ transformers 解析失败: {type(exc).__name__}: {exc}")


def main() -> int:
    if not MODELS.exists():
        print(f"✗ {MODELS} 不存在")
        return 1

    print(f"=== 校验 {MODELS} ===")
    found_any = False

    for gguf in sorted(MODELS.rglob("*.gguf")):
        found_any = True
        check_gguf(gguf)

    for candidate in ("bge-m3", "bge-reranker-base", "Qwen2.5-1.5B-Instruct"):
        path = MODELS / candidate
        if path.is_dir() and any(path.iterdir()):
            found_any = True
            check_hf_dir(path)

    if not found_any:
        print("✗ 没有找到任何权重 —— 下载可能尚未开始或全部失败")
        return 1

    print("=== 说明：本脚本只做结构/可读性校验，不产出任何性能或精度数值 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
