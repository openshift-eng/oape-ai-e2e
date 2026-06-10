"""Phase-specific prompt builders for the workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from state import WorkflowState

SYSTEM_PROMPT = (
    "You are an OpenShift operator code generation assistant. "
    "Follow the workflow instructions precisely and execute each step. "
    "Use the OAPE plugins to generate code, tests, and reviews. "
    "Create git branches, commits, and pull requests as instructed. "
    "IMPORTANT: This is a fully automated pipeline. Execute ALL steps "
    "without pausing, asking for confirmation, or waiting for user input. "
    "Never ask 'should I proceed?' or 'shall I continue?'. "
    "Complete the workflow autonomously in one run."
)


def build_phase1_prompt(state: WorkflowState) -> str:
    return f"""You are an OpenShift operator feature developer assistant. Your task is to generate API type definitions and their integration tests, then create a Pull Request.

## Input Information

- **Enhancement Proposal URL**: {state.ep_url}
- **Repository URL**: {state.repo_url}
- **Base Branch**: {state.base_branch}

## Workflow: PR #1 — API Type Definitions

Branch: `feature/api-types-<ep-number>`

1. Run `/oape:init {state.repo_url} {state.base_branch}` to clone the repository and checkout the base branch
2. Create and checkout a new branch from `{state.base_branch}`
3. Run `/oape:api-generate {state.ep_url}` to generate API type definitions
4. Run `/oape:api-generate-tests <path-to-generated-types>` to generate integration tests
5. Run `make generate && make manifests` to regenerate code
6. Run `/oape:review OCPBUGS-0 {state.base_branch}` to review and auto-fix issues
7. Commit all changes with a descriptive message
8. Push the branch and create a PR against `{state.base_branch}`

## Execution Instructions

1. Execute each step in sequence
2. After each step, verify it completed successfully before proceeding
3. If any step fails, stop and report the error clearly
4. For the review step, the `/oape:review` command will automatically apply fixes
5. When creating PRs, use `gh pr create` with descriptive titles and bodies
6. Report the PR URL after the PR is created

## CRITICAL: Fully Autonomous Execution

You MUST execute ALL steps in a single uninterrupted run. Do NOT ask the user for confirmation, approval, or permission. Do NOT pause to ask "should I proceed?" or "shall I continue?". Complete the entire workflow autonomously.

## Important Notes

- Extract the EP number from the URL (e.g., 1234 from .../pull/1234) for branch naming
- Use conventional commit messages (e.g., "feat: add API types for <feature>")
- The review command uses OCPBUGS-0 as a placeholder ticket ID since we're generating new code
- If the repository is already cloned, the init command will use the existing directory
- Ensure the PR has a clear description of what was generated

## CRITICAL: Output at End

After creating the PR, you MUST output a summary block in EXACTLY this format so we can parse it programmatically:

```
PHASE1_RESULT:
REPO_PATH=<absolute-path-to-cloned-repo>
BRANCH_NAME=<branch-name-you-created>
PR_URL=<full-github-pr-url>
PR_NUMBER=<pr-number>
PR_TITLE=<pr-title>
```

Begin now. Execute all steps without stopping.
"""


def build_phase2a_prompt(state: WorkflowState) -> str:
    context_section = ""
    if state.phase1_summary:
        context_section = f"""
### Phase 1 Summary (for context)
{state.phase1_summary[:3000]}
"""

    return f"""You are an OpenShift operator feature developer assistant. Your task is to generate the controller/reconciler implementation and create a Pull Request.

## Context from Previous Phase

- **Enhancement Proposal URL**: {state.ep_url}
- **Repository URL**: {state.repo_url}
- **Base Branch**: {state.base_branch}
- **Repository Local Path**: {state.repo_local_path}
- **API Types Branch**: {state.api_branch_name}
- **API Types PR**: {state.api_pr.pr_url if state.api_pr else 'N/A'}
{context_section}

## Workflow: PR #2 — Controller Implementation

Branch: `feature/controller-impl-<ep-number>`

1. Change directory to the cloned repository at `{state.repo_local_path}`
2. Create and checkout a new branch from `{state.base_branch}` (or from `{state.api_branch_name}` if the controller needs the API types)
3. Run `/oape:api-implement {state.ep_url}` to generate controller/reconciler code
4. Run `make generate && make build` to verify the build
5. Run `/oape:review OCPBUGS-0 {state.base_branch}` to review and auto-fix issues
6. Commit all changes with a descriptive message
7. Push the branch and create a PR against `{state.base_branch}`

## Execution Instructions

1. Execute each step in sequence
2. After each step, verify it completed successfully before proceeding
3. If any step fails, stop and report the error clearly
4. For the review step, the `/oape:review` command will automatically apply fixes
5. When creating PRs, use `gh pr create` with descriptive titles and bodies
6. Report the PR URL after the PR is created

## CRITICAL: Fully Autonomous Execution

You MUST execute ALL steps in a single uninterrupted run. Do NOT ask for confirmation or permission. Complete the entire workflow autonomously.

## Important Notes

- Extract the EP number from the URL (e.g., 1234 from .../pull/1234) for branch naming
- Use conventional commit messages (e.g., "feat: implement controller for <feature>")
- The review command uses OCPBUGS-0 as a placeholder ticket ID since we're generating new code

## CRITICAL: Output at End

After creating the PR, you MUST output a summary block in EXACTLY this format:

```
PHASE2A_RESULT:
BRANCH_NAME=<branch-name-you-created>
PR_URL=<full-github-pr-url>
PR_NUMBER=<pr-number>
PR_TITLE=<pr-title>
```

Begin now.
"""


def build_phase2b_prompt(state: WorkflowState) -> str:
    context_section = ""
    if state.phase1_summary:
        context_section = f"""
### Phase 1 Summary (for context)
{state.phase1_summary[:3000]}
"""

    return f"""You are an OpenShift operator feature developer assistant. Your task is to generate end-to-end tests and create a Pull Request.

## Context from Previous Phase

- **Enhancement Proposal URL**: {state.ep_url}
- **Repository URL**: {state.repo_url}
- **Base Branch**: {state.base_branch}
- **Repository Local Path**: {state.repo_local_path}
- **API Types Branch**: {state.api_branch_name}
- **API Types PR**: {state.api_pr.pr_url if state.api_pr else 'N/A'}
{context_section}

## Workflow: PR #3 — E2E Tests

Branch: `feature/e2e-tests-<ep-number>`

1. Change directory to the cloned repository at `{state.repo_local_path}`
2. Create and checkout a new branch from `{state.base_branch}` (or from `{state.api_branch_name}` if tests need the API types)
3. Run `/oape:e2e-generate {state.base_branch}` to generate e2e test artifacts
4. Run `/oape:review OCPBUGS-0 {state.base_branch}` to review and auto-fix issues
5. Commit all changes with a descriptive message
6. Push the branch and create a PR against `{state.base_branch}`

## Execution Instructions

1. Execute each step in sequence
2. After each step, verify it completed successfully before proceeding
3. If any step fails, stop and report the error clearly
4. For the review step, the `/oape:review` command will automatically apply fixes
5. When creating PRs, use `gh pr create` with descriptive titles and bodies
6. Report the PR URL after the PR is created

## CRITICAL: Fully Autonomous Execution

You MUST execute ALL steps in a single uninterrupted run. Do NOT ask for confirmation or permission. Complete the entire workflow autonomously.

## Important Notes

- Extract the EP number from the URL (e.g., 1234 from .../pull/1234) for branch naming
- Use conventional commit messages (e.g., "feat: add e2e tests for <feature>")
- The review command uses OCPBUGS-0 as a placeholder ticket ID since we're generating new code

## CRITICAL: Output at End

After creating the PR, you MUST output a summary block in EXACTLY this format:

```
PHASE2B_RESULT:
BRANCH_NAME=<branch-name-you-created>
PR_URL=<full-github-pr-url>
PR_NUMBER=<pr-number>
PR_TITLE=<pr-title>
```

Begin now.
"""
