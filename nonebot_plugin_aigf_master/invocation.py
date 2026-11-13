"""Delivery acknowledgements, deliberately separate from plugin execution results."""
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class DeliveryResult:
    status: Literal["not_sent", "accepted", "dispatched", "uncertain"]
    message: str
    legacy_code: Literal["ok", "timeout", "error"] = "error"

    @property
    def may_have_run(self) -> bool:
        """Only definite pre-delivery failures are safe to omit from deduplication."""
        return self.status != "not_sent"
