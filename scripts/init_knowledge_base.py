#!/usr/bin/env python3
"""
CLI entry point: build (or rebuild) the knowledge base index.

Usage:
    python scripts/init_knowledge_base.py \\
        --kb-version kb_v1.0.0 \\
        --source-dir data/kb_sources/v1.0.0/ \\
        --organs thyroid,liver \\
        --force-rebuild

Reads the manifest and per-organ JSON files under --source-dir, runs the
chunk + feature-extract + embed + index pipeline, and prints the resulting
IndexBuildResult as JSON on stdout.

Exit codes:
    0 = success
    1 = IndexBuildError
    2 = invalid arguments
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow `python scripts/init_knowledge_base.py` from project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="init_knowledge_base",
        description="Build / rebuild the knowledge base index.",
    )
    parser.add_argument(
        "--kb-version",
        required=True,
        help="Knowledge base version, e.g. kb_v1.0.0",
    )
    parser.add_argument(
        "--source-dir",
        required=True,
        help="Directory containing manifest.json and <organ>/*.json",
    )
    parser.add_argument(
        "--organs",
        default="",
        help="Comma-separated organ filter (default: all)",
    )
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="Delete existing chunks for this kb_version before rebuilding",
    )
    parser.add_argument(
        "--embedding-model",
        default="bge-m3-medical",
        help="Logical embedding model name (default: bge-m3-medical)",
    )
    parser.add_argument(
        "--strategy",
        default="sentence_boundary",
        choices=["fixed_window", "sentence_boundary", "semantic"],
        help="Chunking strategy (default: sentence_boundary)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Embed/index batch size (default: 50)",
    )
    parser.add_argument(
        "--allow-mock-embedding",
        action="store_true",
        help=(
            "[测试/离线专用] 显式启用 Mock 哈希向量。默认**关闭**：默认路径必须接真实 "
            "embedding 服务，不可用即失败（假向量建出的知识库无语义检索能力）。"
        ),
    )
    return parser.parse_args(argv)


def _build_request(args: argparse.Namespace):
    from app.knowledge.protocols import IndexBuildRequest
    organs = [o.strip() for o in args.organs.split(",") if o.strip()]
    return IndexBuildRequest(
        kb_version=args.kb_version,
        source_dir=args.source_dir,
        organs=organs,
        force_rebuild=args.force_rebuild,
        embedding_model=args.embedding_model,
        strategy=args.strategy,
        batch_size=args.batch_size,
    )


async def _run(args: argparse.Namespace) -> int:
    from app.knowledge.builder import KnowledgeIndexBuilderImpl
    from app.knowledge.protocols import IndexBuildError

    request = _build_request(args)
    if args.allow_mock_embedding:
        # 测试/离线专用：显式注入 Mock（确定性哈希向量，**无语义**）。
        # 默认路径绝不产出假向量 —— 见 app/knowledge/builder._resolve_real_embedding_service。
        from app.knowledge.builder import MockEmbeddingService

        print(
            "[WARN] --allow-mock-embedding 已启用：本次构建使用无语义的哈希向量，"
            "产物仅可用于链路验证，**不得用于检索/一致性评估**。",
            file=sys.stderr,
        )
        builder = KnowledgeIndexBuilderImpl(
            strategy=args.strategy, embedding_service=MockEmbeddingService()
        )
    else:
        builder = KnowledgeIndexBuilderImpl(strategy=args.strategy)
    try:
        result = await builder.build(request)
    except IndexBuildError as e:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_code": e.error_code.value,
                    "message": e.message,
                    "detail": e.detail,
                    "retryable": e.retryable,
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "result": result.model_dump(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Early validation for fail-fast UX.
    from app.knowledge.version import parse_kb_version
    if parse_kb_version(args.kb_version) is None:
        print(
            f"[ERROR] invalid --kb-version {args.kb_version!r}; "
            "expected format kb_v{major}.{minor}.{patch}",
            file=sys.stderr,
        )
        return 2
    if not Path(args.source_dir).exists():
        print(
            f"[ERROR] --source-dir does not exist: {args.source_dir}\n"
            "Create the directory and a manifest.json (with at least "
            "'kb_version' field) plus per-organ subfolders containing "
            "*.json source documents.",
            file=sys.stderr,
        )
        return 2

    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
