"""Actual B2 attribution/time extraction runs; scores require evidence review."""

# This standalone runner bootstraps the repository path before project imports.
# ruff: noqa: E402

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from importlib.metadata import distributions, version
from functools import partial
from unittest.mock import patch
import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from upstream_extraction import load_upstream
from config.settings import settings
from src.engine.components.chunker import chunk_text
from src.engine.hindsight_components.config import HindsightOptions
from src.engine.hindsight_components.providers import ProjectHindsightProviders
from src.engine.hindsight_components.retain import RetainEngine
from src.engine.hindsight_components.types import RetainInput


def safe_error(error):
    message = str(error)
    for secret in (settings.llm.api_key, settings.llm.base_url):
        if secret:
            message = message.replace(secret, "[redacted]")
    return re.sub(r"https?://\S+", "[endpoint]", message)[:2000]


class ObservedProviders(ProjectHindsightProviders):
    """Observe the production HTTP path without changing payload or parsing."""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def _complete(self, *args, **kwargs):
        async def observe(response):
            await response.aread()
            value = response.json()
            self.calls.append(
                {
                    "http_status": response.status_code,
                    "model": value.get("model"),
                    "output": [
                        choice.get("message", {}).get("content")
                        for choice in value.get("choices", [])
                    ],
                    "usage": value.get("usage"),
                    "error": safe_error(value.get("error", "")),
                }
            )

        original = httpx.AsyncClient
        try:
            with patch(
                "src.engine.hindsight_components.providers.httpx.AsyncClient",
                partial(original, event_hooks={"response": [observe]}),
            ):
                return await super()._complete(*args, **kwargs)
        except Exception as error:
            self.calls.append(
                {"error_type": type(error).__name__, "error": safe_error(error)}
            )
            raise


def git_state(root):
    def run(*args):
        return subprocess.check_output(
            [
                "git",
                "-c",
                f"safe.directory={root.resolve().as_posix()}",
                "-C",
                str(root),
                *args,
            ],
            stderr=subprocess.DEVNULL,
        )

    return {
        "sha": run("rev-parse", "HEAD").decode().strip(),
        "tracked_patch_sha256": sha256(run("diff", "HEAD", "--binary")).hexdigest(),
        "untracked_source_sha256": {
            name: sha256((root / name).read_bytes()).hexdigest()
            for name in run("ls-files", "--others", "--exclude-standard")
            .decode()
            .splitlines()
            if Path(name).suffix in {".py", ".ts", ".tsx", ".json", ".md"}
        },
    }


def append_row(path, value):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


async def run(args):
    args.output.mkdir(parents=True, exist_ok=False)
    corpus_path = Path(__file__).with_name("cases.json")
    cases = [
        item
        for item in json.loads(corpus_path.read_text(encoding="utf-8"))["cases"]
        if item["batch"] == "B2" and set(item["categories"]) & {"attribution", "time"}
    ]
    if args.case:
        cases = [item for item in cases if item["id"] == args.case]
    if not cases:
        raise ValueError("no matching extraction cases")
    manifest = {
        "status": "running",
        "scope": "extraction_only",
        "semantic_score": "not_reviewed",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "tkb": git_state(ROOT),
        "upstream": git_state(args.upstream),
        "corpus_sha256": sha256(corpus_path.read_bytes()).hexdigest(),
        "model": settings.llm.require_model(),
        "temperature": 0,
        "dependencies": {
            item.metadata["Name"]: version(item.metadata["Name"])
            for item in distributions()
        },
        "repetitions": args.repetitions,
        "cases": [c["id"] for c in cases],
        "adapter_sha256": {
            p.name: sha256(p.read_bytes()).hexdigest()
            for p in (
                Path(__file__),
                Path(__file__).with_name("upstream_extraction.py"),
            )
        },
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    try:
        extraction, config_module, wrapper = load_upstream(args.upstream)
        config = config_module._get_raw_config()
        config.llm_temperature_retain = 0
        manifest["upstream_extraction_config"] = {
            key: getattr(config, key)
            for key in (
                "retain_extraction_mode",
                "retain_chunk_size",
                "retain_structured_chunk_size",
                "retain_extract_causal_links",
                "retain_max_completion_tokens",
                "llm_temperature_retain",
                "llm_max_retries",
                "retain_llm_max_retries",
            )
        }
        manifest["tkb_extraction_config"] = asdict(HindsightOptions())
        llm = wrapper.LLMConfig(
            provider="openai",
            api_key=settings.llm.api_key,
            base_url=settings.llm.base_url,
            model=settings.llm.model,
            timeout=90,
            max_retries=1,
        )
        provider = ObservedProviders()
        retain = RetainEngine(None, provider, HindsightOptions())
        for repetition in range(1, args.repetitions + 1):
            for case in cases:
                match = re.search(r"^source_time:(.+)$", case["input"], re.M)
                timestamp = datetime.fromisoformat(match[1]) if match else None
                content = re.sub(
                    r"^(source_time|ingest_time):.*\n?", "", case["input"], flags=re.M
                )
                source = RetainInput(
                    document_id=case["id"],
                    title="Conversation",
                    content=content,
                    file_type="conversation",
                    source_type="conversation",
                    agent_name="assistant",
                    speakers={"user": "user", "assistant": "assistant"},
                    source_timestamp=timestamp,
                    reference_timezone="Asia/Shanghai",
                )
                for engine in ("upstream", "tkb"):
                    started = time.monotonic()
                    result = {
                        "case_id": case["id"],
                        "repetition": repetition,
                        "engine": engine,
                        "source": asdict(source),
                        "semantic_score": "not_reviewed",
                    }
                    try:
                        async with asyncio.timeout(120):
                            if engine == "upstream":
                                (
                                    facts,
                                    chunks,
                                    usage,
                                ) = await extraction.extract_facts_from_text(
                                    text=content,
                                    event_date=timestamp,
                                    llm_config=llm,
                                    agent_name="assistant",
                                    config=config,
                                    context="Conversation between user and assistant; original reference timezone Asia/Shanghai.",
                                )
                                result.update(
                                    facts=[
                                        fact.model_dump(mode="json")
                                        if hasattr(fact, "model_dump")
                                        else fact
                                        for fact in facts
                                    ],
                                    usage=usage.model_dump()
                                    if hasattr(usage, "model_dump")
                                    else asdict(usage),
                                )
                            else:
                                provider.calls = []
                                facts, outcomes, _ = await retain._extract_facts(
                                    source, chunk_text(content)
                                )
                                result.update(
                                    facts=[
                                        asdict(fact)
                                        for group in facts
                                        for fact in group
                                    ],
                                    outcomes=outcomes,
                                    llm_calls=provider.calls,
                                )
                        result["status"] = (
                            "degraded"
                            if "degraded" in result.get("outcomes", [])
                            else "completed"
                        )
                    except Exception as error:
                        result.update(
                            status="failed",
                            error_type=type(error).__name__,
                            error=safe_error(error),
                        )
                    result["duration_seconds"] = time.monotonic() - started
                    append_row(args.output / "results.jsonl", result)
                    print(case["id"], repetition, engine, result["status"], flush=True)
        manifest["status"] = "executed_pending_review"
    except Exception as error:
        manifest.update(
            status="setup_failed",
            error_type=type(error).__name__,
            missing_module=getattr(error, "name", None),
        )
        raise
    finally:
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case")
    parser.add_argument("--repetitions", type=int, default=1)
    asyncio.run(run(parser.parse_args()))
