"""Benchmark runner with iterative feedback loop.

Each iteration: generate code -> compare with ground truth -> improve the OAPE
tool (commands/skills) -> re-generate with improved tool. The tool itself gets
better after each iteration.

All agents use claude-opus-4-6 in max effort mode.
"""

import json
import logging
import shutil
import subprocess
import tempfile
import traceback
from pathlib import Path

from compare import compare_iteration
from models import GenerationResult, GroundTruth, IsolatedEnv, IterationScore, ToolImprovement

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


def _build_improvement_prompt(
    iteration: int,
    score: IterationScore,
    gen_result: GenerationResult,
    truth: GroundTruth,
    plugin_dir: str,
) -> str:
    """Build the prompt for the improver agent."""
    tool_contents: dict[str, str] = {}
    for rel in TOOL_FILES:
        path = Path(plugin_dir) / rel
        if path.exists():
            tool_contents[rel] = path.read_text()

    truth_files_summary = []
    for f in truth.files_added[:20]:
        truth_files_summary.append(f"  ADDED: {f}")
    for f in truth.files_modified[:20]:
        truth_files_summary.append(f"  MODIFIED: {f}")

    gen_files_summary = []
    for f in gen_result.files_created[:20]:
        gen_files_summary.append(f"  CREATED: {f}")
    for f in gen_result.files_modified[:20]:
        gen_files_summary.append(f"  MODIFIED: {f}")

    truth_diff_preview = truth.combined_diff[:12000]
    gen_diff_preview = gen_result.diff[:12000]

    fc = score.file_classification
    classification_summary = []
    if fc.auto_generated:
        classification_summary.append(f"  Auto-generated artifacts (NOT errors): {fc.auto_generated}")
    if fc.formatting_only:
        classification_summary.append(f"  Formatting-only changes (NOT errors): {fc.formatting_only}")
    if fc.valuable_extra:
        classification_summary.append(f"  Valuable extras (tool outperformed human): {fc.valuable_extra}")
    if fc.genuinely_wrong:
        classification_summary.append(f"  GENUINELY WRONG (these are the real errors): {fc.genuinely_wrong}")
    classification_text = chr(10).join(classification_summary) if classification_summary else "  No extra files generated."

    tool_sections = []
    for rel, content in tool_contents.items():
        tool_sections.append(f"### FILE: {rel}\n```markdown\n{content}\n```")

    return f"""You are an expert at improving AI code generation tools for OpenShift operators.

You are given a comparison between what an OAPE tool generated vs what a human actually
shipped for a specific Enhancement Proposal. Your job is to improve the tool's instruction
files so it performs better on ALL future EPs -- not just this specific one.

## CRITICAL: Make GENERIC improvements only

- DO NOT add EP-specific guidance (e.g., "when generating federation support, do X").
- DO NOT reference specific struct names, field names, or feature names from this EP.
- DO add GENERAL PATTERNS that would have caught the issues seen here.
- DO improve the tool's ability to discover what files to generate from ANY EP.
- Think: "What general rule or pattern was missing that caused this gap?"

## Iteration {iteration} Results

### Scores
- Completeness: {score.completeness:.1f}% (what % of ground truth structs/fields/functions were generated)
- Convention Compliance: {score.convention_compliance:.1f}%
- Build Success: {"PASS" if score.build_success else "FAIL"}
- Files matched: {score.files_matched} | Files missed: {score.files_missed}
- Genuinely wrong files: {score.genuinely_wrong} | Valuable extras: {score.valuable_extras} | Auto-generated: {score.auto_generated}

### Understanding Extra Files

Not all "extra" files are errors:

{classification_text}

Files like `zz_generated.deepcopy.go`, CRD YAMLs, `bundle.Dockerfile` are auto-generated
by `make` commands -- generating them is CORRECT. Test files, sample configs, and dedicated
handler files may be the tool doing BETTER than the human.

Only "GENUINELY WRONG" files are actual errors.

### Ground Truth (what the human produced)
{chr(10).join(truth_files_summary)}

Ground truth diff (first 12000 chars):
```diff
{truth_diff_preview}
```

### Generated Output (what the tool produced)
{chr(10).join(gen_files_summary)}

Generated diff (first 12000 chars):
```diff
{gen_diff_preview}
```

## Current OAPE Tool Instructions

{chr(10).join(tool_sections)}

## Your Task

Identify GENERAL PATTERNS the tool is missing, then make targeted edits:

### Priority 1: General patterns for missing files
If the tool missed files (e.g., routes, validation, services, tests), add GENERIC
rules about when an EP implies these file types are needed. Example of a good generic
improvement: "When the EP describes exposing an endpoint, generate a routes.go file
following the existing naming pattern in the controller package."

### Priority 2: General patterns for struct/field generation
If structs or fields don't match, improve the GENERAL conventions about how to derive
struct shapes from EP descriptions.

### Priority 3: General patterns for markers and validation
If markers were missing, add GENERAL rules about which markers to always include.

### Priority 4: General controller patterns
If controller logic was incomplete, add GENERAL reconciliation patterns.

### What NOT to do:
- Do NOT add feature-specific rules (no "for federation..." or "for rotation...")
- Do NOT reference specific struct/field names from this EP
- Do NOT reduce file coverage -- generating extra tests/samples is GOOD
- Do NOT tell the tool to skip `make generate`/`make manifests`
- Do NOT rewrite entire files -- make surgical, targeted edits

After editing, explain what GENERAL pattern you added and why.
"""


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


async def improve_tool(
    iteration: int,
    gen_result: GenerationResult,
    truth: GroundTruth,
    score: IterationScore,
    plugin_dir: str = PLUGIN_DIR,
    backup_dir: Path | None = None,
    model: str = "claude-opus-4-6",
    effort: str = "max",
) -> ToolImprovement:
    """Analyze weaknesses and edit OAPE tool files to improve next iteration."""
    logger.info("=== Improving tool after iteration %d (model=%s, effort=%s) ===",
                iteration, model, effort)

    if backup_dir:
        _backup_tool_files(plugin_dir, backup_dir, f"before-improvement-{iteration}")

    prompt = _build_improvement_prompt(iteration, score, gen_result, truth, plugin_dir)
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
                "You are an expert at improving AI code generation tools. "
                "You will analyze comparison results and make targeted edits to "
                "OAPE tool instruction files (markdown) to improve code generation quality. "
                "Focus on specific, actionable improvements. Do NOT rewrite entire files. "
                "Make surgical edits that address observed weaknesses."
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
        logger.warning("claude_agent_sdk not available for improvement step")
        improvement_log.append("[DRY RUN] claude_agent_sdk not installed")
    except Exception:
        improvement_log.append(f"[ERROR] {traceback.format_exc()}")
        logger.error("Tool improvement failed: %s", traceback.format_exc())

    diffs = {}
    if backup_dir:
        diffs = _diff_tool_files(plugin_dir, backup_dir, f"before-improvement-{iteration}")
        _backup_tool_files(plugin_dir, backup_dir, f"after-improvement-{iteration}")

    if diffs:
        logger.info("Tool improved: %d files changed", len(diffs))
        for fname, diff in diffs.items():
            added = sum(1 for l in diff.split("\n") if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in diff.split("\n") if l.startswith("-") and not l.startswith("---"))
            logger.info("  %s: +%d/-%d lines", fname, added, removed)
    else:
        logger.info("No tool file changes detected after improvement step")

    return ToolImprovement(
        iteration=iteration,
        files_changed=diffs,
        analysis_summary="\n".join(improvement_log[-5:]) if improvement_log else "",
        improvement_cost_usd=cost_usd,
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
- Missed ground truth files (first 15): {(rd.get('implementation_prs', []))}

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


async def run_feedback_loop(
    ep_url: str,
    source_env: IsolatedEnv,
    truth: GroundTruth,
    num_iterations: int = 3,
    plugin_dir: str = PLUGIN_DIR,
    backup_dir: Path | None = None,
    model: str = "claude-opus-4-6",
    effort: str = "max",
) -> tuple[list[GenerationResult], list[ToolImprovement]]:
    """Run iterative feedback loop: generate -> compare -> improve -> repeat.

    Each iteration uses the improved tool from the previous iteration.
    Returns generation results and tool improvements.
    """
    results: list[GenerationResult] = []
    improvements: list[ToolImprovement] = []

    if backup_dir:
        _backup_tool_files(plugin_dir, backup_dir, "original")

    for i in range(1, num_iterations + 1):
        logger.info("=" * 50)
        logger.info("FEEDBACK LOOP: Iteration %d of %d", i, num_iterations)
        logger.info("=" * 50)

        result = await run_single_iteration(
            ep_url, source_env, i,
            plugin_dir=plugin_dir, model=model, effort=effort,
        )
        results.append(result)

        score, outperf = compare_iteration(result, truth)
        logger.info(
            "Iteration %d scores: completeness=%.1f%% precision=%.1f%% build=%s",
            i, score.completeness, score.precision, score.build_success,
        )

        if i < num_iterations:
            logger.info("Analyzing weaknesses and improving tool...")
            improvement = await improve_tool(
                iteration=i,
                gen_result=result,
                truth=truth,
                score=score,
                plugin_dir=plugin_dir,
                backup_dir=backup_dir,
                model=model,
                effort=effort,
            )
            improvements.append(improvement)
            logger.info(
                "Tool improvement complete (cost=$%.4f). %d files changed.",
                improvement.improvement_cost_usd, len(improvement.files_changed),
            )
        else:
            logger.info("Final iteration complete. No further improvements.")

    if backup_dir:
        original_dir = backup_dir / "original"
        if original_dir.exists():
            logger.info("Restoring original tool files...")
            for rel in TOOL_FILES:
                src = original_dir / rel
                dst = Path(plugin_dir) / rel
                if src.exists():
                    shutil.copy2(src, dst)
            logger.info("Original tool files restored.")

    return results, improvements
