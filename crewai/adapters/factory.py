"""
Factory: return the workflow adapter for the selected backend (crewai | claude-sdk).
"""

import os
from typing import Optional

from .base import WorkflowAdapter
from .crewai_adapter import CrewAIAdapter
from .claude_sdk_adapter import ClaudeSDKAdapter


def get_adapter(backend: Optional[str] = None) -> WorkflowAdapter:
    """
    Return the adapter for the given backend.

    backend: "crewai" | "claude-sdk". Defaults to OAPE_BACKEND env, then "crewai".
    """
    name = (backend or os.getenv("OAPE_BACKEND", "crewai")).strip().lower()
    if name in ("claude-sdk", "claude_sdk", "claudesdk"):
        return ClaudeSDKAdapter()
    return CrewAIAdapter()
