"""Options for small structured calls on models with default reasoning enabled."""


def bounded_json_options(model: str) -> dict:
    # DeepSeek V4/V3.2 support this switch; max_tokens otherwise includes
    # reasoning and can be exhausted before any JSON is emitted.
    # https://api-docs.deepseek.com/guides/thinking_mode/
    if model.lower().startswith(("deepseek-v4", "deepseek-v3.2")):
        return {"thinking": {"type": "disabled"}}
    return {}
