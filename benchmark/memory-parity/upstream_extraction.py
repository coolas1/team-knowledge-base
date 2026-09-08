"""Load the selected upstream checkout with its normal package initialization.

This extraction-stage adapter does not substitute for retain/storage parity.
"""

import importlib
from pathlib import Path
import sys


def load_upstream(checkout: Path):
    root = checkout.resolve() / "hindsight-api-slim" / "hindsight_api"
    if not (root / "engine/retain/fact_extraction.py").is_file():
        raise ValueError("upstream extraction source is missing")
    if "hindsight_api" in sys.modules:
        raise RuntimeError("load upstream in a fresh process")
    sys.path.insert(0, str(root.parent))
    package = importlib.import_module("hindsight_api")
    if Path(package.__file__).resolve().parent != root:
        raise RuntimeError("upstream loaded from an unexpected checkout")
    extraction = importlib.import_module("hindsight_api.engine.retain.fact_extraction")
    config = importlib.import_module("hindsight_api.config")
    wrapper = importlib.import_module("hindsight_api.engine.llm_wrapper")
    return extraction, config, wrapper


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, required=True)
    arguments = parser.parse_args()
    extraction, config, wrapper = load_upstream(arguments.upstream)
    print(
        json.dumps(
            {
                "status": "loaded",
                "entrypoint": extraction.extract_facts_from_text.__name__,
                "provider": wrapper.LLMConfig.__name__,
            }
        )
    )
