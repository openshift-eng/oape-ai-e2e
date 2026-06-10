# Operator Feature Dev Workflow

Multi-PR OpenShift operator feature development from Enhancement Proposals. Takes an EP URL and generates a complete implementation across 3 Pull Requests.

## What It Does

Given an Enhancement Proposal PR URL and an operator repository URL, this workflow generates:

1. **PR #1 — API Type Definitions**: Go type definitions with markers, validation, godoc, FeatureGate registration, and `.testsuite.yaml` integration tests
2. **PR #2 — Controller Implementation**: Complete controller/reconciler code with reconciliation logic, dependent resource management, RBAC markers, and status handling
3. **PR #3 — E2E Tests**: End-to-end test artifacts (test cases, execution steps, and Ginkgo/bash test code) based on git diff

## Directory Structure

```text
workflows/operator-feature-dev/
├── .ambient/
│   └── ambient.json                    # Workflow configuration
├── .claude/
│   ├── commands/                       # Atomic, argument-driven operations
│   │   ├── oape.init.md                 # Clone and validate operator repo
│   │   ├── oape.api-generate.md         # Generate API types from EP
│   │   ├── oape.api-generate-tests.md   # Generate integration tests
│   │   ├── oape.api-implement.md        # Generate controller/reconciler
│   │   ├── oape.e2e-generate.md         # Generate E2E test artifacts
│   │   ├── oape.review.md              # Code review with auto-fix
│   │   ├── oape.pr.md                  # Create pull request
│   │   └── oape.speedrun.md            # Autonomous full pipeline
│   └── skills/                         # Orchestration + cross-cutting
│       ├── controller/SKILL.md         # Phase transition orchestrator
│       ├── effective-go/SKILL.md       # Go best practices
│       └── summary/SKILL.md           # Final synthesis
├── CLAUDE.md                           # Hard limits and safety rules
└── README.md
```

## Quick Start

1. Start the workflow in ACP
2. Provide an Enhancement Proposal PR URL and operator repo URL
3. The controller guides you through each phase, or use `/oape.speedrun` for autonomous execution

### Example Session

```text
User: I want to implement EP https://github.com/openshift/enhancements/pull/1234
      in https://github.com/openshift/cert-manager-operator, base branch main

Atlas: [runs /oape.init, then guides through each phase]
```

### Speedrun (Autonomous)

```text
/oape.speedrun https://github.com/openshift/enhancements/pull/1234 https://github.com/openshift/cert-manager-operator main
```

## Commands

| Command | Arguments | Purpose |
| --- | --- | --- |
| `/oape.init` | `<repo-url> <base-branch>` | Clone repo, detect framework |
| `/oape.api-generate` | `<ep-url> [--design-doc <gist-url>]` | Generate API type definitions |
| `/oape.api-generate-tests` | `<path-to-types>` | Generate integration tests |
| `/oape.api-implement` | `<ep-url> [--design-doc <gist-url>]` | Generate controller/reconciler |
| `/oape.e2e-generate` | `<base-branch>` | Generate E2E test artifacts |
| `/oape.review` | `<base-branch>` | Code review with auto-fix |
| `/oape.pr` | `<base-branch>` | Create draft pull request |
| `/oape.speedrun` | `<ep-url> <repo-url> <base-branch>` | Run all phases autonomously |

## Workflow Phases

```text
PR #1: /oape.init → /oape.api-generate → /oape.api-generate-tests → /oape.review → /oape.pr
PR #2: /oape.api-implement → /oape.review → /oape.pr
PR #3: /oape.e2e-generate → /oape.review → /oape.pr
Final: summary skill
```

## Artifacts

All artifacts are written to `artifacts/operator-feature-dev/`:

```text
artifacts/operator-feature-dev/
├── init-summary.md
├── api/
│   ├── generation-summary.md
│   ├── test-generation-summary.md
│   ├── review-verdict.md
│   └── pr-description.md
├── impl/
│   ├── implementation-summary.md
│   ├── review-verdict.md
│   └── pr-description.md
├── e2e/
│   ├── test-cases.md
│   ├── execution-steps.md
│   ├── e2e-suggestions.md
│   ├── generation-summary.md
│   ├── review-verdict.md
│   └── pr-description.md
└── summary.md
```

## Prerequisites

- `git` — Git installed
- `go` — Go toolchain installed
- `gh` — GitHub CLI installed and authenticated
- Access to `openshift/enhancements` repository
- Target operator repository must be a Go-based OpenShift operator

## Supported Frameworks

- **controller-runtime** (kubebuilder/operator-sdk) — most common
- **library-go** (OpenShift core operators) — uses SyncFunc pattern

## Testing

Use the ACP "Custom Workflow" feature to test without merging:

| Field | Value |
| --- | --- |
| **URL** | Your fork's git URL |
| **Branch** | Your branch name |
| **Path** | `workflows/operator-feature-dev` |
