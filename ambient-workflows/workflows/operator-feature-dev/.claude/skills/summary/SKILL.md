---
name: summary
description: Scan all workflow artifacts and present a synthesized summary of findings, decisions, and status across all 3 PRs.
---

# Workflow Summary Skill

This skill can be invoked at any point in the workflow. It does not require
prior phases to have completed; it summarizes whatever exists so far.

---

You are producing a concise, high-signal summary of everything the operator
feature development workflow has done so far. Your audience is someone who
hasn't been watching the workflow run — they want to know what was generated,
what PRs were created, and what needs attention.

## Your Role

Scan the artifact directory, read what's there, and synthesize the important
findings into a single summary. Surface things that might otherwise get buried:
review concerns, convention deviations, EP ambiguities that were resolved,
assumptions made during code generation.

## Process

### Step 1: Discover Artifacts

Scan the artifact root directory to find everything the workflow has produced:

```bash
find artifacts/operator-feature-dev/ -type f -name '*.md' ! -name 'summary.md' 2>/dev/null | sort
```

If `artifacts/operator-feature-dev/` doesn't exist or is empty, report that no
artifacts have been generated yet and stop.

### Step 2: Read All Artifacts

Read every artifact file found in Step 1. Don't skip any — even small or
seemingly unimportant files may contain notable findings.

### Step 3: Extract Key Findings

As you read each artifact, pull out information in these categories:

**Enhancement Proposal**

- EP URL and title
- Key API requirements extracted
- Any ambiguities resolved during generation

**PR #1: API Type Definitions**

- API group, version, kind generated
- Types and fields added or modified
- FeatureGate registration (if applicable)
- Integration tests generated and coverage
- Review verdict and any concerns
- PR URL and status

**PR #2: Controller Implementation**

- Framework detected (controller-runtime vs library-go)
- Reconciliation workflow implemented
- Dependent resources managed
- RBAC permissions generated
- Review verdict and any concerns
- PR URL and status

**PR #3: E2E Tests**

- Test format (Ginkgo vs bash)
- Test coverage (which scenarios)
- Review verdict and any concerns
- PR URL and status

**Convention Deviations**

- Any places where the EP conflicted with OpenShift/Kubernetes API conventions
- What was generated instead and why

**Outstanding Items**

- Assumptions made without user confirmation
- Review concerns that are still outstanding
- Known limitations or edge cases not covered
- Follow-up work recommended

### Step 4: Present the Summary

Present the summary directly to the user using this structure:

```markdown
## Operator Feature Dev Summary

**Enhancement Proposal:** [EP URL and title]
**Status:** [where the workflow stopped — e.g., "All 3 PRs created", "PR #1 created, PR #2 in progress"]

### PRs Created
- PR #1 (API Types): [URL or "not created"]
- PR #2 (Controller): [URL or "not created"]
- PR #3 (E2E Tests): [URL or "not created"]

### Key Findings
- [The most important things the user should know — 3-5 bullet points max]

### Decisions Made
- [Any choices made during generation, especially convention deviations or EP ambiguity resolutions]

### Outstanding Concerns
- [Review caveats, untested edge cases, unconfirmed assumptions — or "None"]

### Artifacts
- [List of all artifact files with one-line descriptions]
```

Keep it tight. The value of this summary is density — if it's as long as the
artifacts themselves, it's not a summary.

### Step 5: Write the Summary Artifact

Save the summary to `artifacts/operator-feature-dev/summary.md`.

## Rules

- **Read, don't assume.** Base everything on what the artifacts actually say,
  not on what you think happened during the workflow.
- **Flag what's missing.** If a phase was skipped or an artifact is absent,
  say so. "No E2E tests were generated" is useful information.
- **Don't editorialize.** Report what the artifacts say. If the review flagged
  a concern, include it. Don't soften it.
- **Keep it short.** The whole point is that nobody reads the full artifacts.
  If your summary is more than ~50 lines of Markdown, cut it down.

## Output

- Summary presented directly to the user (inline)
- Summary saved to `artifacts/operator-feature-dev/summary.md`

## When This Phase Is Done

The summary is the deliverable. Present it and stop — there is no next phase
to recommend.
