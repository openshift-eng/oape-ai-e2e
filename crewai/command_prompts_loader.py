"""
Load prompting content from plugins/oape/commands/*.md for reuse in CrewAI tasks.

The /oape slash commands (api-generate, api-implement, api-generate-tests, review,
implement-review-fixes) contain the authoritative instructions for feature development.
This loader extracts the Description and optional Implementation criteria so CrewAI
tasks can reuse the same prompting instead of duplicating it.
"""

import re
from pathlib import Path
from typing import Dict, Optional


def _find_commands_dir() -> Path:
    """Resolve plugins/oape/commands relative to this file (crewai/command_prompts_loader.py)."""
    crewai_dir = Path(__file__).resolve().parent
    repo_root = crewai_dir.parent
    return repo_root / "plugins" / "oape" / "commands"


def _extract_section(content: str, section_header: str) -> Optional[str]:
    """Extract one section from markdown (from ## Section until next ## or end)."""
    pattern = rf"^##\s+{re.escape(section_header)}\s*$"
    match = re.search(pattern, content, re.IGNORECASE | re.MULTILINE)
    if not match:
        return None
    start = match.end()
    next_section = re.search(r"\n##\s+", content[start:])
    end = start + next_section.start() if next_section else len(content)
    return content[start:end].strip()


def _extract_review_criteria(content: str) -> Optional[str]:
    """Extract review criteria from review.md (Step 3 and modules, without bash blocks)."""
    impl = _extract_section(content, "Implementation")
    if not impl:
        return None
    # Find "### Step 3: Analyze" through "### Step 4" or end
    step3 = re.search(r"###\s+Step\s+3:.*?(?=###\s+Step\s+4|$)", impl, re.DOTALL | re.IGNORECASE)
    if not step3:
        return None
    text = step3.group(0).strip()
    # Remove bash code blocks so we keep only the criteria text
    text = re.sub(r"```\w*\n.*?```", "", text, flags=re.DOTALL)
    return text.strip() or None


def load_command_prompts() -> Dict[str, str]:
    """
    Load the Description section from each plugins/oape/commands/*.md.

    Returns:
        Dict mapping command stem (e.g. 'api-implement', 'review') to the
        Description section text. Used to inject into CrewAI task descriptions.
    """
    commands_dir = _find_commands_dir()
    if not commands_dir.is_dir():
        return {}

    result: Dict[str, str] = {}
    for md_file in sorted(commands_dir.glob("*.md")):
        stem = md_file.stem
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        desc = _extract_section(content, "Description")
        if desc:
            result[stem] = desc
    return result


def get_prompt_for_task(task_kind: str) -> str:
    """
    Get the combined prompt excerpt(s) relevant for a CrewAI task kind.

    task_kind: one of 'design' | 'design_review' | 'test_cases' | 'implementation_outline'
               | 'quality' | 'code_review' | 'address_review' | 'writeup' | 'customer_doc'

    Returns a string to inject into the task description (may be empty if no mapping).
    """
    prompts = load_command_prompts()
    if not prompts:
        return ""

    parts = []
    if task_kind == "design":
        # Design should align with what api-generate and api-implement expect (API + reconciliation intent)
        if "api-generate" in prompts:
            parts.append("**API/types (align with /oape:api-generate):**\n" + prompts["api-generate"])
        if "api-implement" in prompts:
            parts.append("**Implementation intent (align with /oape:api-implement):**\n" + prompts["api-implement"])
    elif task_kind == "design_review":
        if "review" in prompts:
            parts.append("**Review standards (align with /oape:review):**\n" + prompts["review"])
    elif task_kind == "test_cases":
        if "api-generate-tests" in prompts:
            parts.append("**Test expectations (align with /oape:api-generate-tests):**\n" + prompts["api-generate-tests"])
    elif task_kind == "implementation_outline":
        if "api-implement" in prompts:
            parts.append("**Implementation requirements (align with /oape:api-implement):**\n" + prompts["api-implement"])
    elif task_kind == "code_review":
        if "review" in prompts:
            parts.append("**Review criteria (align with /oape:review):**\n" + prompts["review"])
    elif task_kind == "address_review":
        if "implement-review-fixes" in prompts:
            parts.append("**Resolution standards (align with /oape:implement-review-fixes):**\n" + prompts["implement-review-fixes"])

    if not parts:
        return ""
    return "\n\n".join(parts)


def get_all_descriptions_for_context() -> str:
    """Single blob of all command descriptions for injection as shared context."""
    prompts = load_command_prompts()
    if not prompts:
        return ""
    parts = [f"### /oape:{name}\n{text}" for name, text in sorted(prompts.items())]
    return "Apply the following OAPE command requirements where relevant:\n\n" + "\n\n".join(parts)
