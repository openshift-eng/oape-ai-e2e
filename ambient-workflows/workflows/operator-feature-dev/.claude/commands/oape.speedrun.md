# /oape.speedrun - Run all remaining phases without stopping

## Purpose

Execute the full operator feature development pipeline autonomously. Detects
which phases are already complete and picks up from the next incomplete one.
Runs all 3 PR deliverables (API types, controller, E2E tests) in sequence
without user interaction between phases.

## Arguments

- `$ARGUMENTS`: `<ep-url> <repo-url> <base-branch>` (for a fresh start), or
  empty (to resume from where you left off)

Consider the user input before proceeding. It may contain an EP URL, repo URL,
context about where they are in the workflow, or instructions about which
phases to include or skip.

## How Speedrun Works

The speedrun loop:

1. Determine which phase to run next (see "Determine Next Phase" below)
2. If all phases are done (including summary), stop
3. Otherwise, run the command for that phase
4. When the command completes, continue to the next phase

This loop continues until all phases are complete or an escalation stops you.

## Determine Next Phase

Check which phases are already done by looking for artifacts and conversation
context, then pick the first phase that is NOT done.

### Phase Order and Completion Signals

| Phase | Command | "Done" signal |
| --- | --- | --- |
| init | `/oape.init` | `artifacts/operator-feature-dev/init-summary.md` exists |
| api-generate | `/oape.api-generate` | `artifacts/operator-feature-dev/api/generation-summary.md` exists |
| api-generate-tests | `/oape.api-generate-tests` | `artifacts/operator-feature-dev/api/test-generation-summary.md` exists |
| api-review | `/oape.review` | `artifacts/operator-feature-dev/api/review-verdict.md` exists |
| api-pr | `/oape.pr` | PR #1 URL has been shared in conversation |
| api-implement | `/oape.api-implement` | `artifacts/operator-feature-dev/impl/implementation-summary.md` exists |
| impl-review | `/oape.review` | `artifacts/operator-feature-dev/impl/review-verdict.md` exists |
| impl-pr | `/oape.pr` | PR #2 URL has been shared in conversation |
| e2e-generate | `/oape.e2e-generate` | `artifacts/operator-feature-dev/e2e/generation-summary.md` exists |
| e2e-review | `/oape.review` | `artifacts/operator-feature-dev/e2e/review-verdict.md` exists |
| e2e-pr | `/oape.pr` | PR #3 URL has been shared in conversation |
| summary | summary skill | `artifacts/operator-feature-dev/summary.md` exists |

### Rules

- Check artifacts in order. The first phase whose signal is NOT satisfied is next.
- If no artifacts exist, start at **init**.
- If the user specifies a starting point in `$ARGUMENTS`, respect that.
- If conversation context clearly establishes a phase was completed (even
  without an artifact), skip it.

## Execute a Phase

1. **Announce** the phase to the user (e.g., "Starting /oape.api-generate — speedrun mode.")
2. **Run** the command for the current phase
3. When the command completes, continue to the next phase

## Branch Management Between PRs

After each PR is created, prepare for the next PR deliverable:

- **After PR #1 (api-pr)**: Create a new branch from the api-types branch for
  controller work: `feature/ep-{number}-controller`
- **After PR #2 (impl-pr)**: Create a new branch from the controller branch for
  E2E work: `feature/ep-{number}-e2e-tests`

## Speedrun Rules

- **Do not stop and wait between phases.** After each phase completes,
  continue to the next one.
- **Do not use the controller skill.** This command replaces the controller for
  this run.
- **DO still follow CLAUDE.md escalation rules.** If a phase hits an
  escalation condition (confidence below 80%, ambiguous EP, multiple valid API
  designs with unclear trade-offs, security or compliance concern, architectural
  decision needed), stop and ask the user. After the user responds, continue
  with the next phase.

## Phase-Specific Notes

### init

- If no EP URL, repo URL, or base branch exists in `$ARGUMENTS` or
  conversation, ask the user once, then proceed.
- Present the init summary inline but do not wait for confirmation.

### api-generate

- If the EP is ambiguous about API design, this is an escalation point — stop
  and ask the user for clarification.

### api-generate-tests

- Run `make generate && make manifests` (or `make update`) before this phase
  to ensure CRD manifests are up to date.

### api-review

- **Verdict: "API types and tests are solid"** — continue to api-pr.
- **Verdict: "tests incomplete"** — attempt to add missing tests, then
  continue to api-pr.
- **Verdict: "API design inadequate"** — perform **one** revision cycle: go
  back to api-generate → api-generate-tests → api-review. If the second
  review still says "inadequate," stop and report the issues to the user.

### api-pr / impl-pr / e2e-pr

- Follow the PR command's full process including its fallback ladder.
- If PR creation fails after exhausting fallbacks, report and stop.

### api-implement

- If the EP doesn't describe controller behavior clearly, this is an
  escalation point — stop and ask.

### impl-review

- Same verdict handling as api-review: "solid" → continue, "issues found" →
  one revision cycle, then stop if still failing.

### e2e-generate

- Uses diff from base branch to generate targeted tests.

### e2e-review

- Same verdict handling as other reviews.

### summary

- Always run this as the final phase.
- The summary skill scans all artifacts and presents a synthesized overview.
- This is the last thing the user sees.

## Completion Report (Early Stop Only)

If you stop early due to escalation (before summary runs), present:

```markdown
## Speedrun Complete

### PRs Created
- PR #1 (API Types): [URL or "not created"]
- PR #2 (Controller): [URL or "not created"]
- PR #3 (E2E Tests): [URL or "not created"]

### Phases Run
- [each phase that ran and its key outcome]

### Artifacts Created
- [all artifacts with paths]

### Result
- [PR URLs, or reason for stopping early]

### Notes
- [any escalations, skipped phases, or items needing follow-up]
```

## Usage Examples

**From the beginning (fresh start):**

```text
/oape.speedrun https://github.com/openshift/enhancements/pull/1234 https://github.com/openshift/cert-manager-operator main
```

**Mid-workflow (some phases already done):**

```text
/oape.speedrun
```

The command detects existing artifacts and picks up from the next incomplete phase.

**With an explicit starting point:**

```text
/oape.speedrun Start from /oape.api-implement — I already have the API types
```
