"""Speaker and source-time context shared by extraction and replay."""

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from .types import RetainInput


def extraction_context(value: RetainInput) -> str:
    return json.dumps(
        {
            "agent_name": value.agent_name,
            "speakers": {"user": None, "assistant": value.agent_name, **value.speakers},
            "source_timestamp": value.source_timestamp.isoformat()
            if value.source_timestamp
            else None,
            "reference_timezone": value.reference_timezone,
            "policy_version": value.policy_version,
            **(
                {"file_summary": value.metadata["file_summary"]}
                if "file_summary" in value.metadata
                else {}
            ),
        },
        ensure_ascii=False,
    )


RELATIVE_DAYS = {
    "today": 0,
    "yesterday": -1,
    "last night": -1,
    "tomorrow": 1,
    "今天": 0,
    "昨天": -1,
    "昨晚": -1,
    "明天": 1,
}


def fact_datetime(value, context: RetainInput | None) -> datetime | None:
    if value in (None, "", "null"):
        return None
    zone = ZoneInfo(context.reference_timezone if context else "UTC")
    if isinstance(value, str) and value.casefold().strip() in RELATIVE_DAYS:
        if context is None or context.source_timestamp is None:
            return None
        anchor = context.source_timestamp.astimezone(zone).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return (
            anchor + timedelta(days=RELATIVE_DAYS[value.casefold().strip()])
        ).astimezone(UTC)
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)).astimezone(UTC)
