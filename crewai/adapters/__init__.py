"""
Workflow adapters: switch between CrewAI and Claude SDK backends.

Use the same scope (ProjectScope) and get a WorkflowResult from either:
- crewai: full 9-task document workflow (design -> review -> ... -> customer doc)
- claude-sdk: Claude Agent SDK path (e.g. api-implement via server or slash commands)

Set OAPE_BACKEND=crewai | claude-sdk or pass --backend to main.py.
"""

from .base import WorkflowAdapter, WorkflowResult
from .factory import get_adapter

__all__ = ["WorkflowAdapter", "WorkflowResult", "get_adapter"]
