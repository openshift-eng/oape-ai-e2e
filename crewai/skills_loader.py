"""
Load all skills from plugins/oape/skills/*/SKILL.md for use in CrewAI agents and tasks.

Skills are reusable guidelines (e.g. Effective Go, API conventions) that agents
should apply when generating or reviewing content. This loader discovers every
SKILL.md under the oape skills directory and returns a single context string.
"""

import os
from pathlib import Path
from typing import Optional


def _find_skills_dir() -> Path:
    """Resolve plugins/oape/skills relative to this file (crewai/skills_loader.py)."""
    # crewai/skills_loader.py -> oape-ai-e2e/crewai/ -> oape-ai-e2e/
    crewai_dir = Path(__file__).resolve().parent
    repo_root = crewai_dir.parent
    return repo_root / "plugins" / "oape" / "skills"


def load_skills_context() -> str:
    """
    Load all SKILL.md files under plugins/oape/skills/<name>/SKILL.md.
    Returns a single markdown string with each skill's content under a heading.
    """
    skills_dir = _find_skills_dir()
    if not skills_dir.is_dir():
        return ""

    parts = []
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            continue
        try:
            content = skill_file.read_text(encoding="utf-8")
            name = skill_dir.name
            parts.append(f"## Skill: {name}\n\n{content}")
        except Exception:
            continue

    if not parts:
        return ""
    return "\n\n---\n\n".join(parts)


def get_skills_context_for_prompt(max_chars: Optional[int] = None) -> str:
    """
    Same as load_skills_context(), but with a short instruction prefix.
    Truncated to stay under token limits. Set OAPE_SKILLS_MAX_CHARS to override (default 6000).
    Used when injecting skills into every task (legacy); prefer get_skills_context_for_agents().
    """
    if max_chars is None:
        try:
            max_chars = max(1000, int(os.getenv("OAPE_SKILLS_MAX_CHARS", "6000").strip()))
        except ValueError:
            max_chars = 6000
    raw = load_skills_context()
    if not raw:
        return ""
    if len(raw) > max_chars:
        raw = raw[:max_chars] + "\n\n[... skills truncated for context window ...]"
    return (
        "Apply the following skills and conventions where relevant. "
        "These are shared learnings from the project's skills directory.\n\n"
        + raw
    )


def get_skills_context_for_agents(max_chars: Optional[int] = None) -> str:
    """
    Skills context to inject once per agent (in backstory), not in every task.
    Larger limit than for tasks (default 12000) since it is sent only 4 times (one per agent).
    Set OAPE_SKILLS_FOR_AGENTS_MAX_CHARS to override.
    """
    if max_chars is None:
        try:
            max_chars = max(2000, int(os.getenv("OAPE_SKILLS_FOR_AGENTS_MAX_CHARS", "12000").strip()))
        except ValueError:
            max_chars = 12000
    raw = load_skills_context()
    if not raw:
        return ""
    if len(raw) > max_chars:
        raw = raw[:max_chars] + "\n\n[... skills truncated ...]"
    return (
        "\n\nWhen generating or reviewing code, apply these project conventions:\n\n"
        + raw
    )
