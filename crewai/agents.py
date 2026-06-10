"""
CrewAI agents for the OAPE workflow (project-agnostic).

Skills and repository layout are injected once per agent (in backstory) so they are not
repeated in every task description, reducing noise and token use.
"""

import os
from typing import List, Optional

from crewai import Agent

from personas import (
    SSE_PERSONA,
    PSE_PERSONA,
    SQE_PERSONA,
    TECHNICAL_WRITER_PERSONA,
)
from skills_loader import get_skills_context_for_agents


def _get_llm():
    """Use Vertex Claude (if configured) or CrewAI default (OpenAI). Fails with a clear error if neither is set."""
    use_vertex = os.getenv("OAPE_CREWAI_USE_VERTEX", "").strip().lower() in ("1", "true", "yes")
    has_vertex_vars = bool(
        os.getenv("ANTHROPIC_VERTEX_PROJECT_ID", "").strip()
        and os.getenv("CLOUD_ML_REGION", "").strip()
    )
    has_openai = bool(os.getenv("OPENAI_API_KEY", "").strip())

    # Prefer Vertex when explicitly requested or when Vertex vars are set and OpenAI is not
    if use_vertex or (has_vertex_vars and not has_openai):
        try:
            from llm_vertex import VertexClaudeLLM
            return VertexClaudeLLM(
                model=os.getenv("VERTEX_CLAUDE_MODEL", "claude-3-5-haiku@20241022"),
                project_id=os.getenv("ANTHROPIC_VERTEX_PROJECT_ID", ""),
                region=os.getenv("CLOUD_ML_REGION", "us-east5"),
                temperature=0.2,
                max_tokens=8192,
            )
        except Exception as e:
            if use_vertex or not has_openai:
                raise RuntimeError(
                    "Vertex LLM failed (OAPE_CREWAI_USE_VERTEX or no OPENAI_API_KEY). "
                    "Set ANTHROPIC_VERTEX_PROJECT_ID, CLOUD_ML_REGION, and run 'gcloud auth application-default login'. "
                    f"Detail: {e}"
                ) from e
    if has_openai:
        return None  # CrewAI will use default (OpenAI)
    raise ValueError(
        "No LLM configured. Set one of:\n"
        "  • OPENAI_API_KEY for OpenAI, or\n"
        "  • OAPE_CREWAI_USE_VERTEX=1 with ANTHROPIC_VERTEX_PROJECT_ID and CLOUD_ML_REGION (and 'gcloud auth application-default login') for Vertex Claude."
    )


def _agent(
    persona: dict,
    allow_delegation: bool = False,
    repo_layout: Optional[str] = None,
) -> Agent:
    llm = _get_llm()
    max_reasoning = 20
    try:
        env_val = os.getenv("OAPE_MAX_REASONING_ATTEMPTS", "").strip()
        if env_val:
            max_reasoning = max(1, int(env_val))
    except ValueError:
        pass
    backstory = persona["backstory"]
    skills_for_agents = get_skills_context_for_agents()
    if skills_for_agents:
        backstory = backstory.rstrip() + skills_for_agents
    if repo_layout:
        from context import get_repo_layout_for_backstory
        layout_block = get_repo_layout_for_backstory(repo_layout)
        if layout_block:
            backstory = backstory.rstrip() + layout_block
    kwargs = {
        "role": persona["role"],
        "goal": persona["goal"],
        "backstory": backstory,
        "allow_delegation": allow_delegation,
        "verbose": True,
        "reasoning": True,  # show agent plan/thinking before executing each task (for debugging)
        "max_reasoning_attempts": max_reasoning,
    }
    if llm is not None:
        kwargs["llm"] = llm
    return Agent(**kwargs)


def build_agents(repo_layout: Optional[str] = None) -> List[Agent]:
    """Build the four workflow agents. When repo_layout is provided, it is added to each agent's backstory (project tree once per agent, not in every task)."""
    return [
        _agent(SSE_PERSONA, allow_delegation=True, repo_layout=repo_layout),
        _agent(PSE_PERSONA, repo_layout=repo_layout),
        _agent(SQE_PERSONA, repo_layout=repo_layout),
        _agent(TECHNICAL_WRITER_PERSONA, repo_layout=repo_layout),
    ]


# Module-level agents (no repo layout in backstory; used when adapter does not pass scope layout)
sse, pse, sqe, technical_writer = build_agents(None)
