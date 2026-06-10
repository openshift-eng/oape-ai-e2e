"""Reusable message streaming for Claude Agent SDK query() calls."""

import json
from collections.abc import Callable

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
)

from config import conv_logger


async def stream_query(
    query_iter,
    phase_label: str,
    on_message: Callable[[dict], None] | None = None,
) -> tuple[str, float, list[dict]]:
    """Consume a query() async generator, streaming messages and collecting output.

    Returns (output_text, cost_usd, conversation_entries).
    """
    output_parts: list[str] = []
    conversation: list[dict] = []
    cost_usd = 0.0

    def _emit(entry: dict) -> None:
        entry["phase"] = phase_label
        conversation.append(entry)
        if on_message is not None:
            on_message(entry)

    async for message in query_iter:
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    output_parts.append(block.text)
                    _emit({
                        "type": "assistant",
                        "block_type": "text",
                        "content": block.text,
                    })
                    conv_logger.info(f"[{phase_label}:assistant] {block.text}")
                elif isinstance(block, ThinkingBlock):
                    _emit({
                        "type": "assistant",
                        "block_type": "thinking",
                        "content": block.thinking,
                    })
                    conv_logger.info(f"[{phase_label}:thinking] (thinking)")
                elif isinstance(block, ToolUseBlock):
                    _emit({
                        "type": "assistant",
                        "block_type": "tool_use",
                        "tool_name": block.name,
                        "tool_input": block.input,
                    })
                    conv_logger.info(f"[{phase_label}:tool_use] {block.name}")
                elif isinstance(block, ToolResultBlock):
                    content = block.content
                    if not isinstance(content, str):
                        content = json.dumps(content, default=str)
                    _emit({
                        "type": "assistant",
                        "block_type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": content,
                        "is_error": block.is_error or False,
                    })
                    conv_logger.info(
                        f"[{phase_label}:tool_result] {block.tool_use_id}"
                    )
                else:
                    detail = json.dumps(
                        getattr(block, "__dict__", str(block)), default=str
                    )
                    _emit({
                        "type": "assistant",
                        "block_type": type(block).__name__,
                        "content": detail,
                    })
                    conv_logger.info(
                        f"[{phase_label}:{type(block).__name__}] {detail}"
                    )
        elif isinstance(message, ResultMessage):
            cost_usd = message.total_cost_usd
            if message.result:
                output_parts.append(message.result)
            _emit({
                "type": "result",
                "content": message.result,
                "cost_usd": cost_usd,
            })
            conv_logger.info(f"[{phase_label}:result] cost=${cost_usd:.4f}")
        else:
            detail = json.dumps(
                getattr(message, "__dict__", str(message)), default=str
            )
            _emit({
                "type": type(message).__name__,
                "content": detail,
            })
            conv_logger.info(f"[{phase_label}:{type(message).__name__}] {detail}")

    return "\n".join(output_parts), cost_usd, conversation
