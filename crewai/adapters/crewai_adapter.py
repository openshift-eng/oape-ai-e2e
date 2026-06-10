"""
CrewAI backend: runs the full 11-task workflow (design -> review -> ... -> customer doc).

Trace and thinking: agents use reasoning=True; Crew uses tracing=True.
- CREWAI_DEBUG_LLM=1: print full LLM request/response in hooks.
- CREWAI_DEBUG_REASONING=1: print full agent reasoning plan (what the AI is thinking) before each task.
- OAPE_CONTEXT_MAX_CHARS_PER_TASK: truncate each task output when used as context (default 10000) to avoid "prompt is too long" (200k token limit).
"""

import os

from .base import WorkflowAdapter, WorkflowResult


def _install_context_truncation():
    """Truncate task outputs when building context so the prompt stays under the model's token limit (e.g. 200k)."""
    try:
        max_chars = 5000  # per task; 11 * 5k = 55k chars keeps context well under 200k tokens
        env_val = os.getenv("OAPE_CONTEXT_MAX_CHARS_PER_TASK", "").strip()
        if env_val:
            max_chars = max(1000, int(env_val))
    except ValueError:
        max_chars = 5000
    from crewai.utilities import formatter as formatter_module

    _dividers = formatter_module.DIVIDERS

    def _truncated_aggregate(task_outputs):
        parts = []
        for output in task_outputs:
            raw = (output.raw or "")
            if len(raw) > max_chars:
                raw = raw[:max_chars] + "\n\n[... output truncated for context window; full output is in artifacts ...]"
            parts.append(raw)
        return _dividers.join(parts)

    formatter_module.aggregate_raw_outputs_from_task_outputs = _truncated_aggregate


# Apply context truncation when adapter is loaded so "from crewai import Crew" sees the patched formatter
_install_context_truncation()

# Captured trace ID/URL/access_code (before and after CrewAI finalize_batch)
_last_trace_id: str | None = None
_last_trace_url: str | None = None
_last_trace_access_code: str | None = None


def _print_trace_now():
    """Print trace in same format as CrewAI panel / ZTWIM POC: one clear block per workflow."""
    global _last_trace_id, _last_trace_url, _last_trace_access_code
    if not _last_trace_id and not _last_trace_url:
        return
    # Match CrewAI panel format (Trace Batch Finalization) so it matches the previous POC
    message_parts = [
        f"✅ Trace batch finalized with session ID: {_last_trace_id or '(id)'}",
        "",
        f"🔗 View here: {_last_trace_url or ''}",
    ]
    if _last_trace_access_code:
        message_parts.append(f"🔑 Access Code: {_last_trace_access_code}")
    print("\n" + "\n".join(message_parts), flush=True)


def _parse_trace_url(url: str) -> tuple[str | None, str | None]:
    """Extract trace_id and access_code from CrewAI trace URL if possible."""
    import re
    if not url:
        return None, None
    # .../trace_batches/<id> or .../ephemeral_trace_batches/<id>?access_code=...
    m = re.search(r"/trace_batches/([^/?]+)|/ephemeral_trace_batches/([^/?]+)", url)
    tid = (m.group(1) or m.group(2)) if m else None
    m_ac = re.search(r"[?&]access_code=([^&]+)", url)
    ac = m_ac.group(1) if m_ac else None
    return tid, ac


def _crewai_trace_capture_hook():
    """Register for CrewKickoffCompletedEvent to capture trace ID and URL (before finalize clears them)."""
    global _last_trace_id, _last_trace_url, _last_trace_access_code
    _last_trace_id = None
    _last_trace_url = None
    _last_trace_access_code = None

    from crewai.cli.constants import DEFAULT_CREWAI_ENTERPRISE_URL
    from crewai.events.event_bus import crewai_event_bus
    from crewai.events.listeners.tracing.trace_listener import TraceCollectionListener
    from crewai.events.types.crew_events import CrewKickoffCompletedEvent

    @crewai_event_bus.on(CrewKickoffCompletedEvent)
    def _capture_trace_before_finalize(_source, _event):
        global _last_trace_id, _last_trace_url, _last_trace_access_code
        try:
            listener = TraceCollectionListener()
            mgr = getattr(listener, "batch_manager", None)
            if not mgr:
                return
            tid = getattr(mgr, "trace_batch_id", None)
            if not tid and getattr(mgr, "current_batch", None):
                tid = getattr(mgr.current_batch, "batch_id", None)
            if tid:
                _last_trace_id = tid
                base = getattr(mgr, "plus_api", None)
                base_url = getattr(base, "base_url", None) if base else None
                base_url = base_url or DEFAULT_CREWAI_ENTERPRISE_URL
                ephemeral_url = getattr(mgr, "ephemeral_trace_url", None)
                if ephemeral_url:
                    _last_trace_url = ephemeral_url
                    _tid2, ac = _parse_trace_url(ephemeral_url)
                    if ac:
                        _last_trace_access_code = ac
                else:
                    _last_trace_url = f"{base_url}/crewai_plus/trace_batches/{tid}"
        except Exception:
            pass


def _crewai_trace_capture_after_finalize(crew):
    """Register handlers that run AFTER the trace listener: print trace at start (so user can watch live) and capture final URL/access_code at end."""
    global _last_trace_id, _last_trace_url, _last_trace_access_code

    from crewai.cli.constants import DEFAULT_CREWAI_ENTERPRISE_URL
    from crewai.events.event_bus import crewai_event_bus
    from crewai.events.listeners.tracing.trace_listener import TraceCollectionListener
    from crewai.events.types.crew_events import CrewKickoffCompletedEvent, CrewKickoffStartedEvent

    @crewai_event_bus.on(CrewKickoffStartedEvent)
    def _print_trace_as_soon_as_ready(_source, _event):
        """As soon as the run starts and the batch is initialized, print trace so user can open the dashboard and watch live."""
        global _last_trace_id, _last_trace_url
        try:
            listener = TraceCollectionListener()
            mgr = getattr(listener, "batch_manager", None)
            if not mgr:
                return
            if getattr(mgr, "wait_for_batch_initialization", None):
                mgr.wait_for_batch_initialization(timeout=3.0)
            tid = getattr(mgr, "trace_batch_id", None)
            if not tid and getattr(mgr, "current_batch", None):
                tid = getattr(mgr.current_batch, "batch_id", None)
            if tid:
                _last_trace_id = tid
                base = getattr(mgr, "plus_api", None)
                base_url = getattr(base, "base_url", None) if base else None
                base_url = base_url or DEFAULT_CREWAI_ENTERPRISE_URL
                _last_trace_url = f"{base_url}/crewai_plus/trace_batches/{tid}"
                _print_trace_now()
        except Exception:
            pass

    @crewai_event_bus.on(CrewKickoffCompletedEvent)
    def _capture_trace_after_finalize(_source, _event):
        global _last_trace_id, _last_trace_url, _last_trace_access_code
        try:
            listener = TraceCollectionListener()
            mgr = getattr(listener, "batch_manager", None)
            if not mgr:
                return
            ephemeral_url = getattr(mgr, "ephemeral_trace_url", None)
            if ephemeral_url:
                _last_trace_url = ephemeral_url
                tid, ac = _parse_trace_url(ephemeral_url)
                if tid:
                    _last_trace_id = tid
                if ac:
                    _last_trace_access_code = ac
                _print_trace_now()
        except Exception:
            pass


def _crewai_reasoning_hooks():
    """Register event handlers to print full agent reasoning when CREWAI_DEBUG_REASONING=1."""
    if os.getenv("CREWAI_DEBUG_REASONING", "").strip().lower() not in ("1", "true", "yes"):
        return
    from crewai.events.event_bus import crewai_event_bus
    from crewai.events.types.reasoning_events import (
        AgentReasoningCompletedEvent,
        AgentReasoningStartedEvent,
    )

    @crewai_event_bus.on(AgentReasoningStartedEvent)
    def _on_reasoning_started(_source, event):
        print("\n" + "=" * 60)
        print(f"[REASONING] Agent: {event.agent_role} | Task ID: {event.task_id} | Attempt: {event.attempt}")
        print("Thinking...")
        print("=" * 60)

    @crewai_event_bus.on(AgentReasoningCompletedEvent)
    def _on_reasoning_completed(_source, event):
        print("\n" + "=" * 60)
        print(f"[REASONING] Agent: {event.agent_role} | Task ID: {event.task_id}")
        print(f"Ready to execute: {event.ready}")
        print("-" * 60)
        print("Full plan (what the AI is thinking):")
        print(event.plan or "(empty)")
        print("=" * 60 + "\n")


def _crewai_llm_hooks():
    """Register CrewAI before/after LLM hooks. Log each task once, then short lines for later calls."""
    from crewai.hooks import after_llm_call, before_llm_call

    _logged_task_keys = set()

    @before_llm_call
    def _log_request(context):
        task_id = getattr(context.task, "id", None) or id(context.task)
        key = (task_id, context.agent.role)
        if key not in _logged_task_keys:
            _logged_task_keys.add(key)
            task_desc = (context.task.description or "")[:80].replace("\n", " ")
            print(f"\n[LLM] → Agent: {context.agent.role} | Task: {task_desc}...")
        else:
            print(f"\n[LLM] → Agent: {context.agent.role} | (same task) iter {context.iterations} msgs {len(context.messages)}")
        return None

    @after_llm_call
    def _log_response(context):
        if context.response is None:
            return None
        n = len(context.response)
        if os.getenv("CREWAI_DEBUG_LLM", "").strip().lower() in ("1", "true", "yes"):
            print(f"\n[LLM] ← Response ({n} chars):\n{context.response}\n")
        else:
            preview = (context.response[:300] + "...") if n > 300 else context.response
            print(f"\n[LLM] ← Response ({n} chars): {preview}")
        return None


class CrewAIAdapter(WorkflowAdapter):
    """Execute the OAPE workflow using CrewAI (4 agents, 9 sequential tasks)."""

    @property
    def backend_name(self) -> str:
        return "crewai"

    def run(self, scope) -> WorkflowResult:
        _crewai_llm_hooks()
        _crewai_reasoning_hooks()
        _crewai_trace_capture_hook()  # register before Crew so we capture trace ID before finalize clears it
        from crewai import Crew, Process
        from agents import build_agents
        from context import ProjectScope
        from tasks import build_tasks

        if not isinstance(scope, ProjectScope):
            return WorkflowResult(
                success=False,
                output_text="",
                backend=self.backend_name,
                error="scope must be a ProjectScope instance",
            )
        try:
            # When repo_layout is set, put project tree in agent backstory (once per agent) and exclude from task scope
            agents_list = build_agents(repo_layout=scope.repo_layout)
            tasks = build_tasks(
                scope,
                agents=agents_list,
                scope_include_repo_layout=False if scope.repo_layout else True,
            )
            crew = Crew(
                agents=agents_list,
                tasks=tasks,
                process=Process.sequential,
                verbose=True,
                tracing=True,
            )
            _crewai_trace_capture_after_finalize(crew)  # run after trace listener to capture URL + access_code
            result = crew.kickoff()
            # Wait for event handlers to finish so trace ID/URL are captured (handlers run in thread pool)
            try:
                from crewai.events.event_bus import crewai_event_bus
                crewai_event_bus.flush(timeout=30.0)
            except Exception:
                pass
            output_text = str(result) if result is not None else ""
            artifacts = {}
            # Token usage and cost (CrewOutput has token_usage after kickoff)
            usage = getattr(result, "token_usage", None) if result is not None else None
            if usage is None:
                usage = getattr(crew, "token_usage", None)
            if usage is not None:
                artifacts["token_usage"] = {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(usage, "completion_tokens", 0),
                    "total_tokens": getattr(usage, "total_tokens", 0),
                    "successful_requests": getattr(usage, "successful_requests", 0),
                }
                try:
                    from cost_estimator import estimate_cost_from_usage_metrics
                    cost_info = estimate_cost_from_usage_metrics(usage)
                    if cost_info:
                        artifacts["estimated_cost_usd"] = cost_info["cost_usd"]
                        artifacts["cost_estimate"] = cost_info
                except Exception:
                    pass
            for i, task in enumerate(tasks, start=1):
                out = getattr(task, "output", None)
                if out is not None and hasattr(out, "raw"):
                    raw = out.raw or ""
                    # Keep full output for code tasks (5=unit tests, 6=implementation); cap others for size
                    max_len = 80_000 if i in (5, 6) else 2000
                    artifacts[f"task_{i}"] = raw[:max_len] if len(raw) > max_len else raw
            # Use trace ID/URL/access_code from event handlers; fallback: read from listener once more after kickoff
            if _last_trace_id:
                artifacts["trace_id"] = _last_trace_id
            if _last_trace_url:
                artifacts["trace_url"] = _last_trace_url
            if _last_trace_access_code:
                artifacts["trace_access_code"] = _last_trace_access_code
            # Fallback: read from TraceBatchManager (ephemeral_trace_url is left set after finalize_batch)
            if not artifacts.get("trace_id") or not artifacts.get("trace_url"):
                try:
                    from crewai.cli.constants import DEFAULT_CREWAI_ENTERPRISE_URL
                    from crewai.events.listeners.tracing.trace_listener import TraceCollectionListener
                    listener = TraceCollectionListener()
                    mgr = getattr(listener, "batch_manager", None)
                    if mgr:
                        url = getattr(mgr, "ephemeral_trace_url", None)
                        if url:
                            if not artifacts.get("trace_url"):
                                artifacts["trace_url"] = url
                            tid, ac = _parse_trace_url(url)
                            if tid and not artifacts.get("trace_id"):
                                artifacts["trace_id"] = tid
                            if ac and not artifacts.get("trace_access_code"):
                                artifacts["trace_access_code"] = ac
                        if not artifacts.get("trace_id") or not artifacts.get("trace_url"):
                            tid = getattr(mgr, "trace_batch_id", None)
                            if tid:
                                if not artifacts.get("trace_id"):
                                    artifacts["trace_id"] = tid
                                if not artifacts.get("trace_url"):
                                    base = getattr(mgr, "plus_api", None)
                                    base_url = getattr(base, "base_url", None) if base else None
                                    base_url = base_url or DEFAULT_CREWAI_ENTERPRISE_URL
                                    artifacts["trace_url"] = f"{base_url}/crewai_plus/trace_batches/{tid}"
                except Exception:
                    pass
            return WorkflowResult(
                success=True,
                output_text=output_text,
                backend=self.backend_name,
                artifacts=artifacts,
            )
        except Exception as e:
            return WorkflowResult(
                success=False,
                output_text="",
                backend=self.backend_name,
                error=str(e),
            )
