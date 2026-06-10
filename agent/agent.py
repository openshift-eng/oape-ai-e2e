"""
Core agent execution logic for multi-PR operator feature development workflow.

Uses the Claude Agent SDK to orchestrate a sequence of OAPE skills that:
1. PR #1: init → api-generate → api-generate-tests → review-and-fix → raise PR
2. PR #2: api-implement → review-and-fix → raise PR
3. PR #3: e2e-generate → review-and-fix → raise PR
4. CI stage: monitor PR checks and analyze likely fixes for failures
"""

import csv
import json
import logging
import tempfile
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

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

# Resolve the plugin directory (repo root) relative to this file.
PLUGIN_DIR = str(Path(__file__).resolve().parent.parent / "plugins" / "oape")

CONVERSATION_LOG = Path("/tmp/conversation.log")

conv_logger = logging.getLogger("conversation")
conv_logger.setLevel(logging.INFO)
_handler = logging.FileHandler(CONVERSATION_LOG)
_handler.setFormatter(logging.Formatter("%(message)s"))
conv_logger.addHandler(_handler)

with open(Path(__file__).resolve().parent.parent / "config" / "config.json") as cf:
    CONFIGS = json.loads(cf.read())

@dataclass
class PRResult:
    """Result of a single PR creation."""

    pr_number: int
    pr_url: str
    branch_name: str
    title: str


@dataclass
class WorkflowResult:
    """Result returned after running the full workflow."""

    output: str
    cost_usd: float
    error: str | None = None
    conversation: list[dict] = field(default_factory=list)
    prs: list[PRResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.error is None


def _build_workflow_prompt(
    ep_url: str,
    repo_info: dict,
) -> str:
    """Build the system prompt for the full workflow."""

    return f"""You are an OpenShift operator feature developer assistant. Your task is to take an Enhancement Proposal (EP) and generate a complete implementation across three Pull Requests.

## Input Information

- **Enhancement Proposal URL**: {ep_url}
- **Repository URL**: {repo_info['url']}
- **Base Branch**: {repo_info["base_branch"]}

## Workflow Overview

You will create THREE separate Pull Requests, each building on the previous one:

### PR #1: API Type Definitions
Branch: `feature/api-types-<ep-number>`
1. Run `/oape:init {repo_info['url']} {repo_info['base_branch']}` to clone the repository and checkout the base branch
2. Create and checkout a new branch from `{repo_info['base_branch']}`
3. Run `/oape:api-generate {ep_url}` to generate API type definitions
4. Run `/oape:api-generate-tests <path-to-generated-types>` to generate integration tests
5. Run `make generate && make manifests` to regenerate code
6. Run `/oape:review OCPBUGS-0 {repo_info['base_branch']}` to review and auto-fix issues
7. Commit all changes with a descriptive message
8. Push the branch and create a PR against `{repo_info['base_branch']}`
9. Run `/oape:ci-monitor <pr1-url> --timeout-min 120 --max-fix-rounds 2`
   - ci-monitor uses adaptive polling (60s for fast jobs, 120s during cluster provisioning)
   - If CI fails with a fixable error (build/lint/test), apply the fix, push, and ci-monitor re-polls automatically
   - If CI fails with infra flake or repo-wide issue, report it and continue to PR #2
   - If max-fix-rounds (2) exhausted, report and continue

### PR #2: Controller Implementation
Branch: `feature/controller-impl-<ep-number>`
1. Create and checkout a new branch from `{repo_info['base_branch']}` (or from PR #1's branch if needed)
2. Run `/oape:api-implement {ep_url}` to generate controller/reconciler code
3. Run `make generate && make build` to verify the build
4. Run `/oape:review OCPBUGS-0 {repo_info['base_branch']}` to review and auto-fix issues
5. Commit all changes with a descriptive message
6. Push the branch and create a PR against `{repo_info['base_branch']}`
7. Run `/oape:ci-monitor <pr2-url> --timeout-min 120 --max-fix-rounds 2`
   - Same adaptive polling and fix loop as PR #1

### PR #3: E2E Tests
Branch: `feature/e2e-tests-<ep-number>`
1. Create and checkout a new branch from `{repo_info['base_branch']}` (or from PR #2's branch if needed)
2. Run `/oape:e2e-generate {repo_info['base_branch']}` to generate e2e test artifacts
3. Run `/oape:review OCPBUGS-0 {repo_info['base_branch']}` to review and auto-fix issues
4. Commit all changes with a descriptive message
5. Push the branch and create a PR against `{repo_info['base_branch']}`
6. Run `/oape:ci-monitor <pr3-url> --timeout-min 120 --max-fix-rounds 2`
   - Same adaptive polling and fix loop as PR #1

## Execution Instructions

1. Execute each PR workflow in sequence
2. After each step, verify it completed successfully before proceeding
3. If any step fails, stop and report the error clearly
4. For the review step, the `/oape:review` command will automatically apply fixes
5. When creating PRs, use `gh pr create` with descriptive titles and bodies
6. Report the PR URL after each PR is created
7. The `/oape:ci-monitor` call handles the full CI watch + fix loop autonomously:
   - It uses adaptive intervals (60s/120s/60s) to minimize API usage
   - It detects SHA changes from pushes and retests automatically
   - It applies fixes and re-polls up to 2 times before giving up
   - It reports infra flakes as non-fixable and continues

## CRITICAL: Fully Autonomous Execution

You MUST execute ALL THREE PRs in a single uninterrupted run. Do NOT ask the user for confirmation, approval, or permission between PRs. Do NOT pause to ask "should I proceed?" or "shall I continue?". After completing each PR, immediately move on to the next one without any interaction. This is a fully automated pipeline — complete PR #1, then PR #2, then PR #3, all in one go.

## Important Notes

- Extract the EP number from the URL (e.g., 1234 from .../pull/1234) for branch naming
- Use conventional commit messages (e.g., "feat: add API types for <feature>")
- The review command uses OCPBUGS-0 as a placeholder ticket ID since we're generating new code
- If the repository is already cloned, the init command will use the existing directory
- Ensure each PR has a clear description of what was generated

Begin now. Execute PR #1, then immediately PR #2, then immediately PR #3 — all without stopping or asking for user input.
"""


async def run_workflow(
    ep_url: str,
    repo_url: str,
    base_branch: str,
    on_message: Callable[[dict], None] | None = None,
) -> WorkflowResult:
    """Run the full operator feature development workflow.

    Args:
        ep_url: The enhancement proposal PR URL.
        repo_url: URL for git repository.
        base_branch: The base branch to create feature branches from.
        on_message: Optional callback invoked with each conversation message
            dict as it arrives, enabling real-time streaming.

    Returns:
        A WorkflowResult with the output, PRs created, or error.
    """
    prompt = _build_workflow_prompt(ep_url, {
        'url': repo_url,
        'base_branch': base_branch
    })

    working_dir = tempfile.mkdtemp(prefix="oape-")

    options = ClaudeAgentOptions(
        system_prompt=(
            "You are an OpenShift operator code generation assistant. "
            "Follow the workflow instructions precisely and execute each step. "
            "Use the OAPE plugins to generate code, tests, and reviews. "
            "Create git branches, commits, and pull requests as instructed. "
            "IMPORTANT: This is a fully automated pipeline. Execute ALL steps "
            "and ALL PRs without pausing, asking for confirmation, or waiting "
            "for user input. Never ask 'should I proceed?' or 'shall I continue?'. "
            "Complete the entire workflow autonomously in one run."
        ),
        cwd=working_dir,
        permission_mode="bypassPermissions",
        allowed_tools=CONFIGS["claude_allowed_tools"],
        plugins=[{"type": "local", "path": PLUGIN_DIR}],
    )

    output_parts: list[str] = []
    conversation: list[dict] = []
    cost_usd = 0.0

    conv_logger.info(
        f"\n{'=' * 60}\n[workflow] ep_url={ep_url}  repo={repo_url}  "
        f"cwd={working_dir}\n{'=' * 60}"
    )

    def _emit(entry: dict) -> None:
        """Append to conversation and invoke on_message callback if set."""
        conversation.append(entry)
        if on_message is not None:
            on_message(entry)

    try:
        async for message in query(
            prompt=prompt,
            options=options,
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        output_parts.append(block.text)
                        entry = {
                            "type": "assistant",
                            "block_type": "text",
                            "content": block.text,
                        }
                        _emit(entry)
                        conv_logger.info(f"[assistant] {block.text}")
                    elif isinstance(block, ThinkingBlock):
                        entry = {
                            "type": "assistant",
                            "block_type": "thinking",
                            "content": block.thinking,
                        }
                        _emit(entry)
                        conv_logger.info("[assistant:ThinkingBlock] (thinking)")
                    elif isinstance(block, ToolUseBlock):
                        entry = {
                            "type": "assistant",
                            "block_type": "tool_use",
                            "tool_name": block.name,
                            "tool_input": block.input,
                        }
                        _emit(entry)
                        conv_logger.info(f"[assistant:ToolUseBlock] {block.name}")
                    elif isinstance(block, ToolResultBlock):
                        content = block.content
                        if not isinstance(content, str):
                            content = json.dumps(content, default=str)
                        entry = {
                            "type": "assistant",
                            "block_type": "tool_result",
                            "tool_use_id": block.tool_use_id,
                            "content": content,
                            "is_error": block.is_error or False,
                        }
                        _emit(entry)
                        conv_logger.info(
                            f"[assistant:ToolResultBlock] {block.tool_use_id}"
                        )
                    else:
                        detail = json.dumps(
                            getattr(block, "__dict__", str(block)),
                            default=str,
                        )
                        entry = {
                            "type": "assistant",
                            "block_type": type(block).__name__,
                            "content": detail,
                        }
                        _emit(entry)
                        conv_logger.info(
                            f"[assistant:{type(block).__name__}] {detail}"
                        )
            elif isinstance(message, ResultMessage):
                cost_usd = message.total_cost_usd
                if message.result:
                    output_parts.append(message.result)
                entry = {
                    "type": "result",
                    "content": message.result,
                    "cost_usd": cost_usd,
                }
                _emit(entry)
                conv_logger.info(f"[result] {message.result}  cost=${cost_usd:.4f}")
            else:
                detail = json.dumps(
                    getattr(message, "__dict__", str(message)), default=str
                )
                entry = {
                    "type": type(message).__name__,
                    "content": detail,
                }
                _emit(entry)
                conv_logger.info(f"[{type(message).__name__}] {detail}")

        conv_logger.info(f"[done] cost=${cost_usd:.4f}  parts={len(output_parts)}\n")
        return WorkflowResult(
            output="\n".join(output_parts),
            cost_usd=cost_usd,
            conversation=conversation,
        )
    except Exception as exc:
        conv_logger.info(f"[error] {traceback.format_exc()}")
        return WorkflowResult(
            output="",
            cost_usd=cost_usd,
            error=str(exc),
            conversation=conversation,
        )
