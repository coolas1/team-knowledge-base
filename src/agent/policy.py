"""Hook policy as DATA: which operations require approval, evaluated to a
serializable result (never a blocking callback). Face-agnostic — the same
NeedsApproval payload is interpreted identically by an in-process loop or an
external MCP caller. (See docs/architecture.md §2 "policy-as-data".)"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class PendingAction:
    op: str
    params: dict


@dataclass
class NeedsApproval:
    question: str
    pending_action: PendingAction
    status: str = "needs_approval"


@dataclass
class Proceed:
    status: str = "proceed"


HookDecision = Proceed | NeedsApproval


@dataclass
class HookPolicy:
    """Maps operation names that require approval to their question text."""

    gated_ops: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, hooks_dir: Path) -> "HookPolicy":
        gated: dict[str, str] = {}
        if hooks_dir.is_dir():
            for f in sorted(hooks_dir.glob("*.yaml")):
                data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                if data.get("op") and data.get("requires_approval"):
                    gated[data["op"]] = data.get("question", f"Approve {data['op']}?")
        return cls(gated_ops=gated)

    def check(self, op: str, params: dict, approved: bool = False) -> HookDecision:
        if op in self.gated_ops and not approved:
            return NeedsApproval(
                question=self.gated_ops[op],
                pending_action=PendingAction(op=op, params=params),
            )
        return Proceed()
