"""
Project-agnostic scope and context for the CrewAI workflow.

Scope can be set via:
- CLI / env: project name, repo URL, scope description
- Context file: a .txt file (optional PROJECT_NAME=, REPO_URL= headers, then body)
- GitHub EP URL: fetch enhancement proposal content from openshift/enhancements PR
"""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


def _scope_limit(env_name: str, default: int) -> int:
    try:
        v = os.getenv(env_name, "").strip()
        return max(1000, int(v)) if v else default
    except ValueError:
        return default


@dataclass
class ProjectScope:
    """Scope for a single CrewAI run: what project and what feature/area."""

    project_name: str
    repo_url: str
    scope_description: str
    extra_context: Optional[str] = None
    repo_path: Optional[str] = None
    """Optional path to a local clone; when set, repo_layout is used to constrain paths."""
    repo_layout: Optional[str] = None
    """Directory tree of repo_path so agents suggest only existing paths."""

    def to_markdown(
        self,
        max_scope_chars: Optional[int] = None,
        max_repo_layout_chars: Optional[int] = None,
        max_extra_context_chars: int = 4000,
        include_repo_layout: bool = True,
    ) -> str:
        """Format scope for injection into task descriptions. Truncated to stay under token limits.
        Set include_repo_layout=False when repo layout is injected in agent backstory instead."""
        max_scope_chars = max_scope_chars or _scope_limit("OAPE_SCOPE_MAX_CHARS", 12000)
        max_repo_layout_chars = max_repo_layout_chars or _scope_limit("OAPE_REPO_LAYOUT_MAX_CHARS", 6000)
        max_extra = _scope_limit("OAPE_EXTRA_CONTEXT_MAX_CHARS", 4000)
        scope = (self.scope_description or "")[:max_scope_chars]
        if len(self.scope_description or "") > max_scope_chars:
            scope += "\n\n[... scope truncated for context window ...]"
        lines = [
            f"**Project:** {self.project_name}",
            f"**Repository:** {self.repo_url}",
            "",
            "**Scope for this run:**",
            scope,
        ]
        extra = (self.extra_context or "")[:max_extra]
        if extra:
            lines.extend(["", "**Additional context:**", extra])
        if include_repo_layout and self.repo_layout:
            layout = self.repo_layout[:max_repo_layout_chars]
            if len(self.repo_layout) > max_repo_layout_chars:
                layout += "\n... [layout truncated]"
            lines.extend([
                "",
                "**Current repository layout (you MUST suggest only files/packages under this structure):**",
                "```",
                layout,
                "```",
            ])
        return "\n".join(lines)


def get_repo_layout_for_backstory(repo_layout: Optional[str], max_chars: Optional[int] = None) -> str:
    """Format repository layout for injection into agent backstory (once per agent). Returns empty string if none."""
    if not repo_layout or not repo_layout.strip():
        return ""
    limit = max_chars or _scope_limit("OAPE_REPO_LAYOUT_MAX_CHARS", 6000)
    layout = repo_layout.strip()[:limit]
    if len(repo_layout.strip()) > limit:
        layout += "\n... [repository layout truncated]"
    return "\n\n**Repository layout (use ONLY these paths or new files under existing packages):**\n```\n" + layout + "\n```"


def get_repo_layout(repo_path: str, max_entries: int = 400, max_depth: int = 6) -> Optional[str]:
    """
    Return a directory/file tree for the given path so agents can suggest only existing paths.

    Skips .git, vendor, node_modules, __pycache__, .venv. Returns None if path is not a directory.
    """
    root = Path(repo_path).resolve()
    if not root.is_dir():
        return None
    skip_dirs = {".git", "vendor", "node_modules", "__pycache__", ".venv", "venv", "_output"}
    lines = []
    count = 0

    def walk(dir_path: Path, prefix: str, depth: int) -> None:
        nonlocal count
        if depth > max_depth or count >= max_entries:
            return
        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        for i, entry in enumerate(entries):
            if count >= max_entries:
                return
            if entry.name.startswith(".") and entry.name != ".":
                continue
            if entry.is_dir() and entry.name in skip_dirs:
                continue
            is_last = i == len(entries) - 1
            branch = "└── " if is_last else "├── "
            lines.append(prefix + branch + entry.name)
            count += 1
            if entry.is_dir():
                ext = "    " if is_last else "│   "
                walk(entry, prefix + ext, depth + 1)

    walk(root, "", 0)
    return "\n".join(lines) if lines else None


def default_scope() -> ProjectScope:
    """Example scope when none is provided (e.g. for testing)."""
    return ProjectScope(
        project_name="OpenShift Operator",
        repo_url="https://github.com/openshift/example-operator",
        scope_description=(
            "Add a new API and controller for a custom resource. "
            "Follow controller-runtime and OpenShift API conventions."
        ),
    )


# --- Context from text file ---

def load_context_from_file(file_path: str) -> Tuple[Optional[str], Optional[str], str]:
    """
    Load scope from a text file.

    Optional format: lines "PROJECT_NAME=...", "REPO_URL=..." at the start,
    then a blank line or "---", then the rest is the scope description.
    If no key=value lines, the entire file content is the scope description.

    Returns:
        (project_name or None, repo_url or None, scope_description)
    """
    path = Path(file_path)
    if not path.is_file():
        return None, None, ""

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None, None, ""

    project_name = None
    repo_url = None
    lines = text.split("\n")
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.upper().startswith("PROJECT_NAME="):
            project_name = stripped.split("=", 1)[1].strip()
            body_start = i + 1
        elif stripped.upper().startswith("REPO_URL="):
            repo_url = stripped.split("=", 1)[1].strip()
            body_start = i + 1
        elif stripped == "---" or stripped == "":
            body_start = i + 1
            break
        else:
            if project_name is not None or repo_url is not None:
                body_start = i
            break
    scope_desc = "\n".join(lines[body_start:]).strip()
    # If no key=value lines, entire file is the scope description
    if project_name is None and repo_url is None and body_start == 0:
        return None, None, text
    # If key=value lines present but no body, leave scope_desc empty (do not use full file)
    if not scope_desc and (project_name is not None or repo_url is not None):
        return project_name, repo_url, ""
    return project_name, repo_url, scope_desc or ""


# --- Context from GitHub EP (Enhancement Proposal) ---

EP_URL_PATTERN = re.compile(
    r"^https://github\.com/openshift/enhancements/pull/(\d+)/?$",
    re.IGNORECASE,
)


def load_context_from_ep_url(ep_url: str) -> Optional[str]:
    """
    Fetch Enhancement Proposal content from a GitHub PR URL.

    Expects: https://github.com/openshift/enhancements/pull/<number>
    Uses `gh pr view <number> --repo openshift/enhancements` if available,
    otherwise returns None (caller can use the URL as fallback).

    Returns:
        PR body text (title + body) or None if fetch fails.
    """
    ep_url = ep_url.rstrip("/")
    match = EP_URL_PATTERN.match(ep_url)
    if not match:
        return None
    pr_number = match.group(1)
    try:
        out = subprocess.run(
            ["gh", "pr", "view", pr_number, "--repo", "openshift/enhancements", "--json", "title,body"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if out.returncode != 0 or not out.stdout:
            return None
        import json
        data = json.loads(out.stdout)
        title = data.get("title") or ""
        body = data.get("body") or ""
        return f"# {title}\n\n{body}".strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return None
