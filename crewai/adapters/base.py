"""
Common interface for workflow backends (CrewAI vs Claude SDK).

Both adapters accept the same ProjectScope and return a WorkflowResult,
so callers can switch backends without changing code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from context import ProjectScope


@dataclass
class WorkflowResult:
    """Result from running the workflow with either backend."""

    success: bool
    """True if the workflow completed without fatal error."""

    output_text: str
    """Primary output (e.g. customer doc for CrewAI, implementation output for Claude SDK)."""

    backend: str
    """Which backend ran: 'crewai' or 'claude-sdk'."""

    artifacts: Optional[Dict[str, Any]] = None
    """Backend-specific outputs (e.g. task outputs, cost, review report)."""

    error: Optional[str] = None
    """If success is False, optional error message."""

    def __post_init__(self) -> None:
        if self.artifacts is None:
            self.artifacts = {}


class WorkflowAdapter(ABC):
    """Adapter interface: run the feature workflow with the given scope."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Identifier for this backend (e.g. 'crewai', 'claude-sdk')."""
        pass

    @abstractmethod
    def run(self, scope: "ProjectScope") -> WorkflowResult:
        """
        Execute the workflow for the given scope.

        CrewAI: full 9-task pipeline (design -> design review -> ... -> customer doc).
        Claude SDK: implementation (and optionally review) from EP; may require
        scope.ep_url and operator repo path (env).
        """
        pass
