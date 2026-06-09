"""
CI Monitor agent: runs /oape:ci-monitor for one or more PR URLs.

Uses the Claude Agent SDK to execute the ci-monitor skill autonomously
without user prompts or confirmations.
"""

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

PLUGIN_DIR = str(Path(__file__).resolve().parent.parent / "plugins" / "oape")

CONVERSATION_LOG = Path("/tmp/ci-monitor-conversation.log")

conv_logger = logging.getLogger("ci_monitor_conversation")
conv_logger.setLevel(logging.INFO)
_handler = logging.FileHandler(CONVERSATION_LOG)
_handler.setFormatter(logging.Formatter("%(message)s"))
conv_logger.addHandler(_handler)

with open(Path(__file__).resolve().parent.parent / "config" / "config.json") as cf:
    CONFIGS = json.loads(cf.read())


@dataclass
class CIMonitorResult:
    """Result returned after running CI monitoring."""

    output: str
    cost_usd: float
    error: str | None = None
    conversation: list[dict] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.error is None


def _build_ci_monitor_prompt(pr_urls: list[str]) -> str:
    pr_args = " ".join(pr_urls)
    return f"""You are an OpenShift CI monitoring assistant. Your ONLY task is to run the ci-monitor command and produce a report.

## Task

Run the following command and let it complete:

```
/oape:ci-monitor {pr_args}
```

## CRITICAL: Fully Autonomous Execution

- Execute the ci-monitor command immediately without asking for any user input.
- Do NOT ask "should I proceed?", "shall I continue?", or any confirmation questions.
- Do NOT ask the user to approve tool calls or confirm actions.
- Accept and approve ALL tool calls automatically.
- Let the ci-monitor skill handle everything: polling, evidence collection, analysis, and report generation.
- When the command completes, present the final report to the user.
- If the ci-monitor suggests a retest or fix, execute it without asking.
- This is a fully automated pipeline — run to completion in one go.
"""


async def run_ci_monitor(
    pr_urls: list[str],
    on_message: Callable[[dict], None] | None = None,
) -> CIMonitorResult:
    """Run CI monitoring for one or more PR URLs.

    Args:
        pr_urls: List of GitHub PR URLs to monitor.
        on_message: Optional callback for real-time streaming.

    Returns:
        A CIMonitorResult with the monitoring report or error.
    """
    prompt = _build_ci_monitor_prompt(pr_urls)

    working_dir = tempfile.mkdtemp(prefix="oape-ci-monitor-")

    options = ClaudeAgentOptions(
        system_prompt=(
            "You are an OpenShift CI monitoring assistant. "
            "Run the ci-monitor command and produce a complete report. "
            "IMPORTANT: This is a fully automated pipeline. Execute ALL steps "
            "without pausing, asking for confirmation, or waiting for user input. "
            "Never ask 'should I proceed?' or 'shall I continue?'. "
            "Accept all tool calls automatically. Complete the monitoring autonomously."
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
        f"\n{'=' * 60}\n[ci-monitor] pr_urls={pr_urls}  "
        f"cwd={working_dir}\n{'=' * 60}"
    )

    def _emit(entry: dict) -> None:
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
        return CIMonitorResult(
            output="\n".join(output_parts),
            cost_usd=cost_usd,
            conversation=conversation,
        )
    except Exception as exc:
        conv_logger.info(f"[error] {traceback.format_exc()}")
        return CIMonitorResult(
            output="",
            cost_usd=cost_usd,
            error=str(exc),
            conversation=conversation,
        )
