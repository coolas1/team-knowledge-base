"""Options for small structured calls on models with default reasoning enabled."""

import hashlib
import json
import logging
from urllib.parse import urlsplit


def memory_options(model: str, base_url: str, policy: str, *, bounded=False) -> dict:
    """Validate explicit policies; auto preserves the existing request contract."""
    if policy == "auto":
        return bounded_json_options(model) if bounded else {}
    if policy not in {"disabled", "enabled"}:
        raise ValueError("Invalid LLM_MEMORY_THINKING policy")
    host = urlsplit(base_url).hostname
    supported = (
        host == "ark.cn-beijing.volces.com"
        and model.lower().startswith(("doubao-seed-2.1-", "doubao-seed-2-1-"))
    ) or (
        host == "api.deepseek.com"
        and model.lower().startswith(("deepseek-v4", "deepseek-v3.2"))
    )
    if not supported:
        raise ValueError("LLM_MEMORY_THINKING is unsupported for this endpoint/model")
    return {"thinking": {"type": policy}}


def memory_identity(llm, *, bounded=False) -> str:
    options = memory_options(
        llm.require_model(), llm.base_url, llm.memory_thinking, bounded=bounded
    )
    value = [llm.base_url.rstrip("/"), llm.model, options]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def log_completion(logger: logging.Logger, payload: dict, response: dict) -> None:
    """Only metadata: never log prompts, completions or credentials."""
    logger.info(
        "llm_completion %s",
        json.dumps(
            {
                "requested_model": payload["model"],
                "actual_model": response.get("model"),
                "thinking": payload.get("thinking", {"type": "provider_default"}),
                "usage": response.get("usage"),
            }
        ),
    )


def bounded_json_options(model: str) -> dict:
    # DeepSeek V4/V3.2 support this switch; max_tokens otherwise includes
    # reasoning and can be exhausted before any JSON is emitted.
    # https://api-docs.deepseek.com/guides/thinking_mode/
    if model.lower().startswith(("deepseek-v4", "deepseek-v3.2")):
        return {"thinking": {"type": "disabled"}}
    return {}
