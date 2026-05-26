from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MatchResult:
    transaction_id: str
    status: str
    confidence: str
    message: str
    bill_id: str | None = None
