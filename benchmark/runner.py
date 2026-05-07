"""Benchmark runner for OAPE tools.

Provides:
- run_single_iteration: Run OAPE tools once in an isolated environment
- improve_tool_from_all_results: Analyze cross-EP patterns and make concise improvements
"""

import json
import logging
import shutil
import subprocess
import tempfile
import traceback
from pathlib import Path

from models import GenerationResult, IsolatedEnv, ToolImprovement

logger = logging.getLogger(__name__)

PLUGIN_DIR = str(Path(__file__).resolve().parent.parent / "plugins" / "oape")

TOOL_FILES = [
    "commands/api-generate.md",
    "commands/api-implement.md",
]


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kwargs)


def _clone_iteration_env(source_env: IsolatedEnv, iteration: int) -> Path:
    """Create a fresh copy of the isolated environment for one iteration."""
    iter_dir = Path(tempfile.mkdtemp(prefix=f"oape-bench-iter{iteration}-"))
    logger.info("Copying baseline to %s for iteration %d", iter_dir, iteration)
    shutil.copytree(source_env.path, iter_dir, dirs_exist_ok=True)
    _run(["git", "checkout", "--quiet", source_env.baseline_sha], cwd=iter_dir)
    _run(["git", "clean", "-fdx", "--quiet"], cwd=iter_dir)
    return iter_dir


def _build_generation_prompt(ep_url: str, repo_dir: str) -> str:
    return f"""You are running OAPE tools to implement an Enhancement Proposal.

Repository: {repo_dir} (already cloned and ready)
EP URL: {ep_url}

Steps:
1. Run /oape:api-generate {ep_url}
2. After api-generate completes, run /oape:api-implement {ep_url}

CRITICAL RULES:
- Do NOT run `git push` under any circumstances
- Do NOT run `gh pr create` under any circumstances
- Do NOT create any branches for pushing
- Work ONLY in the current directory
- After running both commands, stop. Do not push or create PRs.
- Execute both commands fully and autonomously without asking for confirmation.
"""


def _try_build(work_dir: Path) -> bool:
    """Attempt `make build` in the working directory."""
    try:
        result = subprocess.run(
            ["make", "build"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _backup_tool_files(plugin_dir: str, backup_dir: Path, label: str) -> None:
    """Save a copy of all OAPE tool files to a backup directory."""
    dest = backup_dir / label
    dest.mkdir(parents=True, exist_ok=True)
    for rel in TOOL_FILES:
        src = Path(plugin_dir) / rel
        if src.exists():
            dst = dest / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    logger.info("Tool files backed up to %s (%s)", dest, label)


def _diff_tool_files(plugin_dir: str, backup_dir: Path, before_label: str) -> dict[str, str]:
    """Compute unified diffs between backed-up version and current tool files."""
    diffs: dict[str, str] = {}
    for rel in TOOL_FILES:
        before = backup_dir / before_label / rel
        current = Path(plugin_dir) / rel
        if not before.exists() or not current.exists():
            continue
        try:
            result = subprocess.run(
                ["diff", "-u", str(before), str(current)],
                capture_output=True, text=True,
            )
            if result.stdout.strip():
                diffs[rel] = result.stdout
        except FileNotFoundError:
            pass
    return diffs


async def run_single_iteration(
    ep_url: str,
    source_env: IsolatedEnv,
    iteration: int,
    plugin_dir: str = PLUGIN_DIR,
    model: str = "claude-opus-4-6",
    effort: str = "max",
) -> GenerationResult:
    """Run one iteration of OAPE tools in an isolated environment."""
    iter_dir = _clone_iteration_env(source_env, iteration)

    logger.info("=== Iteration %d: running OAPE tools (model=%s, effort=%s) ===",
                iteration, model, effort)

    prompt = _build_generation_prompt(ep_url, str(iter_dir))
    agent_log_parts: list[str] = []
    cost_usd = 0.0

    tool_version = "original" if iteration == 1 else f"improved-v{iteration - 1}"

    try:
        from claude_agent_sdk import (
            query,
            ClaudeAgentOptions,
            AssistantMessage,
            ResultMessage,
            TextBlock,
            ThinkingBlock,
            ToolUseBlock,
            ToolResultBlock,
        )

        options = ClaudeAgentOptions(
            model=model,
            effort=effort,
            system_prompt=(
                "You are an OpenShift operator code generation assistant. "
                "Follow the workflow instructions precisely. "
                "CRITICAL: Do NOT push branches. Do NOT create PRs. "
                "Do NOT run git push or gh pr create. "
                "Work purely locally. Execute all steps without pausing."
            ),
            cwd=str(iter_dir),
            permission_mode="bypassPermissions",
            allowed_tools=[
                "Read", "Write", "Edit", "MultiEdit",
                "Bash", "Glob", "Grep",
                "TodoRead", "TodoWrite",
            ],
            plugins=[{"type": "local", "path": plugin_dir}],
        )

        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        agent_log_parts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        agent_log_parts.append(f"[tool:{block.name}]")
            elif isinstance(message, ResultMessage):
                cost_usd = message.total_cost_usd
                if message.result:
                    agent_log_parts.append(message.result)

    except ImportError:
        logger.warning(
            "claude_agent_sdk not available; running in dry-run mode. "
            "Install claude-agent-sdk to run actual benchmarks."
        )
        agent_log_parts.append("[DRY RUN] claude_agent_sdk not installed")
    except Exception:
        agent_log_parts.append(f"[ERROR] {traceback.format_exc()}")
        logger.error("Iteration %d failed: %s", iteration, traceback.format_exc())

    try:
        diff_result = _run(["git", "diff", "HEAD"], cwd=iter_dir)
        diff = diff_result.stdout

        status_result = _run(["git", "status", "--porcelain"], cwd=iter_dir)
        untracked = [
            line[3:] for line in status_result.stdout.strip().split("\n")
            if line.startswith("?? ")
        ]
        if untracked:
            _run(["git", "add"] + untracked, cwd=iter_dir)
            diff_result2 = _run(["git", "diff", "--cached"], cwd=iter_dir)
            diff = diff + "\n" + diff_result2.stdout
            _run(["git", "reset", "HEAD"] + untracked, cwd=iter_dir)

        diff_stat = _run(
            ["git", "diff", "--name-status", "HEAD"],
            cwd=iter_dir,
        )
        all_status = _run(["git", "status", "--porcelain"], cwd=iter_dir)

        files_created: list[str] = []
        files_modified: list[str] = []

        for line in diff_stat.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t", 1)
            if len(parts) < 2:
                continue
            status, fpath = parts[0].strip(), parts[1].strip()
            if status == "A":
                files_created.append(fpath)
            elif status == "M":
                files_modified.append(fpath)

        for line in all_status.stdout.strip().split("\n"):
            if line.startswith("?? "):
                fpath = line[3:]
                if fpath not in files_created:
                    files_created.append(fpath)

    except subprocess.CalledProcessError:
        diff = ""
        files_created = []
        files_modified = []

    build_ok = _try_build(iter_dir)

    logger.info(
        "Iteration %d complete: %d created, %d modified, build=%s, cost=$%.4f",
        iteration, len(files_created), len(files_modified), build_ok, cost_usd,
    )

    return GenerationResult(
        iteration=iteration,
        diff=diff,
        files_created=files_created,
        files_modified=files_modified,
        build_success=build_ok,
        gen_dir=iter_dir,
        agent_log="\n".join(agent_log_parts),
        cost_usd=cost_usd,
        tool_version=tool_version,
    )


async def improve_tool_from_all_results(
    results_data: list[dict],
    truth_data: dict[str, dict],
    iter_diffs: dict[str, str],
    plugin_dir: str = PLUGIN_DIR,
    backup_dir: Path | None = None,
    model: str = "claude-opus-4-6",
    effort: str = "max",
) -> ToolImprovement:
    """Analyze results from ALL EPs and make ONE set of concise improvements.

    This is the Phase 2 function -- it looks at patterns across all EPs
    and makes generic improvements, not EP-specific ones.
    """
    logger.info("=== Analyzing %d EP results for cross-EP patterns ===", len(results_data))

    if backup_dir:
        _backup_tool_files(plugin_dir, backup_dir, "original")

    tool_contents: dict[str, str] = {}
    for rel in TOOL_FILES:
        path = Path(plugin_dir) / rel
        if path.exists():
            tool_contents[rel] = path.read_text()

    ep_summaries = []
    for rd in results_data:
        ep_num = rd["ep_url"].rstrip("/").split("/")[-1]
        scores = rd.get("iteration_scores", [{}])
        s = scores[0] if scores else {}
        fc = s.get("file_classification", {})

        truth_diff = truth_data.get(ep_num, {}).get("diff_preview", "")
        gen_diff = iter_diffs.get(ep_num, "")

        ep_summaries.append(f"""
### EP #{ep_num}: {rd.get('description', '')}
- Repo: {rd.get('repo_url', '')}
- Completeness: {s.get('completeness', 0):.1f}%
- Convention: {s.get('convention_compliance', 0):.1f}%
- Build: {"PASS" if s.get('build_success') else "FAIL"}
- Matched: {s.get('files_matched', 0)} | Missed: {s.get('files_missed', 0)} | Wrong: {s.get('genuinely_wrong', 0)} | Extras: {s.get('valuable_extras', 0)}
- Genuinely wrong files: {fc.get('genuinely_wrong', [])}
- Genuinely wrong files: {fc.get('genuinely_wrong', [])}

Ground truth diff (first 5000 chars):
```diff
{truth_diff}
```

Generated diff (first 5000 chars):
```diff
{gen_diff}
```
""")

    tool_sections = []
    for rel, content in tool_contents.items():
        tool_sections.append(f"### FILE: {rel}\n```markdown\n{content}\n```")

    prompt = f"""You are improving OAPE tool instructions based on benchmark results from {len(results_data)} different Enhancement Proposals across different OpenShift operators.

## CRITICAL RULES

1. Make GENERIC improvements only. Do NOT reference specific EP numbers, feature names, struct names, or repo names.
2. Keep edits CONCISE. Add short rules or checklist items, not paragraphs of explanation.
3. Each edit must address a pattern seen across MULTIPLE EPs, not just one.
4. Do NOT bloat the files. If a 3-line rule can replace a 20-line explanation, use the 3-line rule.
5. Preserve the existing file structure and phases.

## Results from {len(results_data)} EPs

{''.join(ep_summaries)}

## Current Tool Instructions

{chr(10).join(tool_sections)}

## What to do

Look at the patterns across ALL EPs:
- What types of files are consistently missed? Add a concise rule.
- What types of files are consistently marked "genuinely wrong"? Add a concise "do not" rule.
- Are there common convention gaps? Add a short checklist item.
- Are there controller patterns the tool misses? Add a brief pattern.

Make your edits to the tool files. Keep them short and to the point.
After editing, list what you changed in 2-3 bullet points.
"""

    improvement_log: list[str] = []
    cost_usd = 0.0

    try:
        from claude_agent_sdk import (
            query,
            ClaudeAgentOptions,
            AssistantMessage,
            ResultMessage,
            TextBlock,
            ToolUseBlock,
        )

        options = ClaudeAgentOptions(
            model=model,
            effort=effort,
            system_prompt=(
                "You improve AI code generation tools by making concise, targeted edits. "
                "You never add bloat. Every edit is a short rule or checklist item. "
                "You never reference specific EPs, features, or repos."
            ),
            cwd=plugin_dir,
            permission_mode="bypassPermissions",
            allowed_tools=[
                "Read", "Write", "Edit", "MultiEdit",
                "Bash", "Glob", "Grep",
            ],
        )

        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        improvement_log.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        improvement_log.append(f"[tool:{block.name}]")
            elif isinstance(message, ResultMessage):
                cost_usd = message.total_cost_usd
                if message.result:
                    improvement_log.append(message.result)

    except ImportError:
        logger.warning("claude_agent_sdk not available")
        improvement_log.append("[DRY RUN]")
    except Exception:
        improvement_log.append(f"[ERROR] {traceback.format_exc()}")
        logger.error("Tool improvement failed: %s", traceback.format_exc())

    diffs = {}
    if backup_dir:
        diffs = _diff_tool_files(plugin_dir, backup_dir, "original")
        _backup_tool_files(plugin_dir, backup_dir, "improved")

    return ToolImprovement(
        iteration=0,
        files_changed=diffs,
        analysis_summary="\n".join(improvement_log[-5:]) if improvement_log else "",
        improvement_cost_usd=cost_usd,
    )


