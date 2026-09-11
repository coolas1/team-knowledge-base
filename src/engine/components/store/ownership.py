"""Shared ownership columns; host authorization is enforced by repositories."""

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from src.engine.scope import DEFAULT_BANK_ID


class BankOwned:
    bank_id: Mapped[str] = mapped_column(
        Text, nullable=False, default=DEFAULT_BANK_ID, server_default=DEFAULT_BANK_ID
    )
