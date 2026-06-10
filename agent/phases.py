"""Phased workflow orchestration.

Phase 1: API Types PR  (init -> api-generate -> tests -> review -> PR)
Phase 2a: Controller PR (api-implement -> review -> PR)
Phase 2b: E2E Tests PR  (e2e-generate -> review -> PR)
"""

import re
import tempfile
import traceback
from collections.abc import Callable

from claude_agent_sdk import query, ClaudeAgentOptions

from config import PLUGIN_DIR, conv_logger, load_config
from prompts import (
    SYSTEM_PROMPT,
    build_phase1_prompt,
    build_phase2a_prompt,
    build_phase2b_prompt,
)
from state import PRResult, WorkflowResult, WorkflowState
from streaming import stream_query


def _make_options(working_dir: str, config: dict) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        cwd=working_dir,
        permission_mode="bypassPermissions",
        allowed_tools=config["claude_allowed_tools"],
        plugins=[{"type": "local", "path": PLUGIN_DIR}],
    )


def _parse_pr_result(output: str, prefix: str) -> PRResult | None:
    pattern = rf"{prefix}:\s*\n((?:\w+=.*\n?)+)"
    match = re.search(pattern, output)
    if not match:
        return None

    fields = {}
    for line in match.group(1).strip().split("\n"):
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()

    pr_url = fields.get("PR_URL", "")
    if not pr_url:
        return None

    try:
        return PRResult(
            pr_number=int(fields.get("PR_NUMBER", "0")),
            pr_url=pr_url,
            branch_name=fields.get("BRANCH_NAME", ""),
            title=fields.get("PR_TITLE", ""),
        )
    except (ValueError, KeyError):
        return None


def _extract_pr_url_fallback(output: str) -> str | None:
    m = re.search(r"https://github\.com/[^\s)]+/pull/\d+", output)
    return m.group(0) if m else None


async def run_phase1(
    state: WorkflowState,
    config: dict,
    on_message: Callable[[dict], None] | None = None,
) -> float:
    conv_logger.info(
        f"\n{'=' * 60}\n[phase1] Starting API Types PR\n{'=' * 60}"
    )

    prompt = build_phase1_prompt(state)
    options = _make_options(state.working_dir, config)

    output, cost, _ = await stream_query(
        query(prompt=prompt, options=options),
        phase_label="phase1",
        on_message=on_message,
    )

    pr_result = _parse_pr_result(output, "PHASE1_RESULT")
    if pr_result:
        state.api_pr = pr_result
        state.api_branch_name = pr_result.branch_name
    else:
        fallback_url = _extract_pr_url_fallback(output)
        if fallback_url:
            state.api_pr = PRResult(
                pr_number=0,
                pr_url=fallback_url,
                branch_name="",
                title="API Types PR",
            )

    repo_path_match = re.search(r"REPO_PATH=(.+)", output)
    if repo_path_match:
        state.repo_local_path = repo_path_match.group(1).strip()

    state.phase1_summary = output[-2000:] if len(output) > 2000 else output

    conv_logger.info(f"[phase1] Complete. cost=${cost:.4f}")
    return cost


async def run_phase2a(
    state: WorkflowState,
    config: dict,
    on_message: Callable[[dict], None] | None = None,
) -> float:
    conv_logger.info(
        f"\n{'=' * 60}\n[phase2a] Starting Controller PR\n{'=' * 60}"
    )

    prompt = build_phase2a_prompt(state)
    options = _make_options(state.working_dir, config)

    output, cost, _ = await stream_query(
        query(prompt=prompt, options=options),
        phase_label="phase2a",
        on_message=on_message,
    )

    pr_result = _parse_pr_result(output, "PHASE2A_RESULT")
    if pr_result:
        state.controller_pr = pr_result
    else:
        fallback_url = _extract_pr_url_fallback(output)
        if fallback_url:
            state.controller_pr = PRResult(
                pr_number=0,
                pr_url=fallback_url,
                branch_name="",
                title="Controller Implementation PR",
            )

    conv_logger.info(f"[phase2a] Complete. cost=${cost:.4f}")
    return cost


async def run_phase2b(
    state: WorkflowState,
    config: dict,
    on_message: Callable[[dict], None] | None = None,
) -> float:
    conv_logger.info(
        f"\n{'=' * 60}\n[phase2b] Starting E2E Tests PR\n{'=' * 60}"
    )

    prompt = build_phase2b_prompt(state)
    options = _make_options(state.working_dir, config)

    output, cost, _ = await stream_query(
        query(prompt=prompt, options=options),
        phase_label="phase2b",
        on_message=on_message,
    )

    pr_result = _parse_pr_result(output, "PHASE2B_RESULT")
    if pr_result:
        state.e2e_pr = pr_result
    else:
        fallback_url = _extract_pr_url_fallback(output)
        if fallback_url:
            state.e2e_pr = PRResult(
                pr_number=0,
                pr_url=fallback_url,
                branch_name="",
                title="E2E Tests PR",
            )

    conv_logger.info(f"[phase2b] Complete. cost=${cost:.4f}")
    return cost


async def run_workflow(
    ep_url: str,
    repo_url: str,
    base_branch: str,
    on_message: Callable[[dict], None] | None = None,
) -> WorkflowResult:
    """Run the full operator feature development workflow.

    Phase 1 (sequential): API Types PR
    Phase 2a (sequential): Controller Implementation PR
    Phase 2b (sequential): E2E Tests PR
    """
    working_dir = tempfile.mkdtemp(prefix="oape-")
    config = load_config()
    total_cost = 0.0
    conversation: list[dict] = []

    def _on_msg(entry: dict) -> None:
        conversation.append(entry)
        if on_message is not None:
            on_message(entry)

    state = WorkflowState(
        ep_url=ep_url,
        repo_url=repo_url,
        base_branch=base_branch,
        working_dir=working_dir,
    )

    conv_logger.info(
        f"\n{'=' * 60}\n[workflow] ep_url={ep_url}  repo={repo_url}  "
        f"cwd={working_dir}\n{'=' * 60}"
    )

    try:
        # Phase 1: API Types PR
        cost1 = await run_phase1(state, config, on_message=_on_msg)
        total_cost += cost1

        if state.api_pr is None:
            return WorkflowResult(
                output="Phase 1 failed: no API types PR created",
                cost_usd=total_cost,
                error="Phase 1 did not produce a PR",
                conversation=conversation,
            )

        # Phase 2a: Controller Implementation PR
        try:
            cost2a = await run_phase2a(state, config, on_message=_on_msg)
            total_cost += cost2a
        except Exception as exc:
            conv_logger.info(f"[phase2a:error] {traceback.format_exc()}")
            _on_msg({
                "type": "phase_error",
                "phase": "phase2a",
                "error": str(exc),
            })

        # Phase 2b: E2E Tests PR
        try:
            cost2b = await run_phase2b(state, config, on_message=_on_msg)
            total_cost += cost2b
        except Exception as exc:
            conv_logger.info(f"[phase2b:error] {traceback.format_exc()}")
            _on_msg({
                "type": "phase_error",
                "phase": "phase2b",
                "error": str(exc),
            })

        conv_logger.info(f"[done] total_cost=${total_cost:.4f}")
        return WorkflowResult(
            output=f"Created {len(state.all_prs)} PRs",
            cost_usd=total_cost,
            conversation=conversation,
            prs=state.all_prs,
        )

    except Exception as exc:
        conv_logger.info(f"[error] {traceback.format_exc()}")
        return WorkflowResult(
            output="",
            cost_usd=total_cost,
            error=str(exc),
            conversation=conversation,
            prs=state.all_prs,
        )
