"""Shared types for block/unblock mechanisms."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class BlockResult:
    """Outcome of a block/unblock operation for a single mechanism."""

    mechanism: str
    action: str  # "block" | "unblock"
    success: bool
    reversible: bool = True
    detail: str = ""
    # Prior state captured before the change, so the operation can be reversed.
    previous_state: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mechanism": self.mechanism,
            "action": self.action,
            "success": self.success,
            "reversible": self.reversible,
            "detail": self.detail,
            "previous_state": self.previous_state,
        }
