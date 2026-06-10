"""
CrewAI tasks for the OAPE workflow (project-agnostic).

Skills and repository layout are in agent backstory when provided; task descriptions get scope
(optionally without repeating the project tree) and command prompts only.
"""

from typing import List, Optional

from crewai import Agent, Task

from agents import sse, pse, sqe, technical_writer
from command_prompts_loader import get_prompt_for_task
from context import ProjectScope


def build_tasks(
    scope: ProjectScope,
    agents: Optional[List[Agent]] = None,
    scope_include_repo_layout: Optional[bool] = None,
):
    """
    Build the 11-task workflow with the given project scope.
    When agents is provided (e.g. built with repo_layout in backstory), scope excludes repo layout
    to avoid duplicating the project tree in every task.
    """
    sse_agent = (agents[0] if agents and len(agents) >= 4 else sse)
    pse_agent = (agents[1] if agents and len(agents) >= 4 else pse)
    sqe_agent = (agents[2] if agents and len(agents) >= 4 else sqe)
    tw_agent = (agents[3] if agents and len(agents) >= 4 else technical_writer)
    # When agents have repo_layout in backstory, do not repeat it in scope
    include_layout = scope_include_repo_layout if scope_include_repo_layout is not None else (agents is None or not scope.repo_layout)
    scope_md = scope.to_markdown(include_repo_layout=include_layout)
    # One-line reminder so agents use their backstory conventions (no large skills block per task)
    conventions_note = " Apply the project's conventions (Effective Go, API conventions) from your role context."

    def _desc(prefix: str, rest: str, task_kind: str = "") -> str:
        base = f"{prefix}{conventions_note}\n\n{rest}"
        cmd_prompt = get_prompt_for_task(task_kind) if task_kind else ""
        if cmd_prompt:
            base = base + "\n\n" + cmd_prompt
        return base

    # --- Task 1: SSE — Design ---
    t1 = Task(
        description=_desc(
            f"Create the **design document** for the feature in scope.\n\n{scope_md}",
            "Your design MUST include: (1) Overview and goals, (2) API/CRD changes with example YAML, "
            "(3) Operator reconciliation, (4) Security and operational considerations, (5) Open questions. "
            "Output: Markdown, no placeholders.",
            "design",
        ),
        expected_output="Design document in Markdown with the five sections above.",
        agent=sse_agent,
    )

    # --- Task 2: PSE — Design review ---
    t2 = Task(
        description=_desc(
            "Review the SSE's design document (in your context). Principal Engineer **design review**.",
            "Evaluate correctness, completeness, security. For each finding use MUST / SHOULD / NICE-TO-HAVE. "
            "In Summary, end with exactly one verdict on its own line: **Approved**, **Approved with changes**, or **Changes required**.",
            "design_review",
        ),
        expected_output="Design review with MUST/SHOULD/NICE-TO-HAVE and Summary (verdict as last line).",
        agent=pse_agent,
        context=[t1],
    )

    # --- Task 3: SQE — Test cases ---
    t3 = Task(
        description=_desc(
            "Using the design and design review (in your context), create the **test plan and test cases**.",
            "For each test case: ID, Title, Preconditions, Steps, Expected result. Traceable to design. Output: Markdown.",
            "test_cases",
        ),
        expected_output="Test plan and test cases in Markdown.",
        agent=sqe_agent,
        context=[t1, t2],
    )

    # --- Task 4: SSE — Implementation outline ---
    t4_desc = "Using design, review, and test cases (in your context), produce the **implementation outline and unit test plan**."
    t4_rest = "Include: (1) Files/packages to add or modify, (2) Reconciliation logic outline, (3) Unit test plan. No full code; outline only. SQE will write unit tests first, then SSE will write implementation logic; the final code must compile. Output: Markdown."
    if scope.repo_layout:
        t4_rest += " Use the repository layout from your role context; suggest ONLY paths that exist in that layout (do not invent directories)."
    t4 = Task(
        description=_desc(
            t4_desc,
            t4_rest,
            "implementation_outline",
        ),
        expected_output="Implementation outline and unit test plan in Markdown.",
        agent=sse_agent,
        context=[t1, t2, t3],
    )

    # --- Task 5: SQE — Write unit test code first ---
    t5_desc = "Using the design, test plan, and implementation outline (in your context), **write the unit test code first**. Produce the actual test files (e.g. *_test.go) that will exercise the implementation. Do not write production code yet; SSE will implement the logic next so that these tests pass and the code compiles."
    t5_rest = "Output: Markdown with one section per file. Each section must have a heading with the repo-relative file path (e.g. ## pkg/controller/foo/foo_test.go) followed by a code block containing the full file content. Ensure test code is valid and would compile once stubs or implementation exist."
    if scope.repo_layout:
        t5_rest += " Use ONLY paths from the repository layout in your role context (or new files under existing packages)."
    t5 = Task(
        description=_desc(t5_desc, t5_rest, "unit_tests"),
        expected_output="Unit test code as Markdown (sections with file path and code block per file).",
        agent=sqe_agent,
        context=[t1, t2, t3, t4],
    )

    # --- Task 6: SSE — Write implementation logic (code must compile) ---
    t6_desc = "Using the design, implementation outline, and the **unit tests written by SQE** (in your context), **write the implementation logic** so that the unit tests pass and **the code compiles**. Produce the actual production code (reconciler, CRD, etc.); do not leave stubs. The final codebase must build successfully (e.g. go build ./...)."
    t6_rest = "Output: Markdown with one section per file. Each section must have a heading with the repo-relative file path (e.g. ## pkg/controller/foo/reconciler.go) followed by a code block containing the full file content. Align with the implementation outline and ensure all SQE tests can pass."
    if scope.repo_layout:
        t6_rest += " Use ONLY paths from the repository layout in your role context (or new files under existing packages)."
    t6 = Task(
        description=_desc(t6_desc, t6_rest, "implementation"),
        expected_output="Implementation code as Markdown (sections with file path and code block per file). Code must compile.",
        agent=sse_agent,
        context=[t1, t2, t3, t4, t5],
    )

    # --- Task 7: SQE — Quality ---
    t7 = Task(
        description=_desc(
            "Using design, test cases, implementation outline, unit test code, and implementation code (in your context), produce **quality feedback and recommendations**.",
            "Test execution summary (Pass/Fail/Blocked per case), quality issues (High/Medium/Low), recommended improvements. Note whether the code would compile. Output: Markdown.",
        ),
        expected_output="Quality feedback and recommendations in Markdown.",
        agent=sqe_agent,
        context=[t1, t2, t3, t4, t5, t6],
    )

    # --- Task 8: PSE — Code review ---
    t8 = Task(
        description=_desc(
            "Review the implementation outline, unit test code, and implementation code (in your context). Principal Engineer **code/implementation review**. Ensure the code compiles and tests are appropriate.",
            "Evaluate correctness, security, alignment with design. MUST / SHOULD / NICE-TO-HAVE. "
            "In Summary, end with exactly one verdict: **Approved**, **Approved with changes**, or **Changes required**.",
            "code_review",
        ),
        expected_output="Code review with MUST/SHOULD/NICE-TO-HAVE and Summary (verdict as last line).",
        agent=pse_agent,
        context=[t4, t5, t6, t7],
    )

    # --- Task 9: SSE — Address review ---
    t9_desc = _desc(
        "Using the PSE code review (in your context), produce the **revision summary**: for each MUST and SHOULD, state your resolution (fix, document, or defer). Output: Markdown, section « Resolution of review items ».",
        "",
        "address_review",
    )
    t9 = Task(
        description=t9_desc,
        expected_output="Resolution for each MUST/SHOULD from code review.",
        agent=sse_agent,
        context=[t4, t5, t6, t7, t8],
    )

    # --- Task 10: SSE — Write-up for Technical Writer ---
    t10 = Task(
        description="Using the full context (design, reviews, test plan, implementation outline, unit tests, implementation code, quality feedback, revision summary), produce a **structured write-up for the Technical Writer**: summary, prerequisites, configuration steps, verification, troubleshooting, references. Do not write final prose; provide structured content. Output: Markdown.",
        expected_output="Structured write-up for docs in Markdown.",
        agent=sse_agent,
        context=[t1, t2, t3, t4, t5, t6, t7, t8, t9],
    )

    # --- Task 11: Technical Writer — Customer doc ---
    t11 = Task(
        description="Using the SSE write-up (in your context), create the **customer-facing documentation**: overview, prerequisites, step-by-step configuration, verification, troubleshooting. Markdown suitable for publication.",
        expected_output="Customer-facing Markdown documentation.",
        agent=tw_agent,
        context=[t10],
    )

    return [t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11]
