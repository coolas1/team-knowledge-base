"""Thin CLI adapter: wraps a KnowledgeBase instance built from config.

No business logic - every subcommand maps 1:1 to a KnowledgeBase method and
prints JSON. Usage: python -m src.engine.cli <subcommand> ...
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from config.schema import load_config
from src.engine.config import build_engine, engine_config_from_app
from src.engine.interface import KnowledgeBase


def _jsonable(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}  # type: ignore[arg-type]
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, Path):
        return str(obj)
    return obj


def _print(obj: Any) -> None:
    print(json.dumps(_jsonable(obj), ensure_ascii=False))


async def _run(kb: KnowledgeBase, args: argparse.Namespace) -> int:
    if args.command == "ingest":
        ref = await kb.ingest(
            __import__("src.engine.interface", fromlist=["IngestSource"]).IngestSource(
                name=args.name, data=args.data.encode("utf-8")
            )
        )
        _print(ref)
    elif args.command == "recall":
        from src.engine.interface import RecallRequest

        res = await kb.recall(RecallRequest(query=args.query, top_k=args.top_k))
        _print(res)
    elif args.command == "graph":
        _print(await kb.get_graph(args.entity))
    elif args.command == "get":
        _print(await kb.get_document(args.doc_id))
    elif args.command == "list":
        _print(await kb.list_documents(args.page, args.page_size))
    elif args.command == "remove":
        await kb.remove(args.doc_id)
        _print({"removed": args.doc_id})
    else:
        print(f"unknown command: {args.command}", file=sys.stderr)
        return 2
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kb")
    p.add_argument("--engine-impl", default=None)
    p.add_argument("--config-dir", default=None)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("ingest")
    s.add_argument("--name", required=True)
    s.add_argument("--data", required=True)
    s = sub.add_parser("recall")
    s.add_argument("--query", required=True)
    s.add_argument("--top-k", type=int, default=20)
    s = sub.add_parser("graph")
    s.add_argument("--entity", default=None)
    s = sub.add_parser("get")
    s.add_argument("--doc-id", required=True)
    s = sub.add_parser("list")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--page-size", type=int, default=20)
    s = sub.add_parser("remove")
    s.add_argument("--doc-id", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    ecfg = engine_config_from_app(load_config())
    if args.engine_impl:
        ecfg.impl = args.engine_impl
    if args.config_dir:
        ecfg.config_dir = Path(args.config_dir)
    kb = build_engine(ecfg)
    return asyncio.run(_run(kb, args))


if __name__ == "__main__":
    raise SystemExit(main())
