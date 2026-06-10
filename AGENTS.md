# OAPE AI E2E Feature Development

This document provides context for AI agents when working with the OAPE AI E2E Feature Development tools.

## Purpose

This project provides AI-driven tools for end-to-end feature development in OpenShift operators. The workflow takes an Enhancement Proposal (EP), design document (gist), and/or Jira ticket and generates:
1. API type definitions (Go structs)
2. Integration tests for the API types
3. Controller/reconciler implementation code

## Commands

| Command                                                          | Purpose                                                        |
| ---------------------------------------------------------------- | -------------------------------------------------------------- |
| `/oape:init <git-url> <base-branch>`                             | Clone a Git repository and checkout the base branch            |
| `/oape:api-generate <EP-URL> [--design-doc <GIST-URL>] [--jira <TICKET>]` | Generate Go API types from EP, design doc, and/or Jira ticket |
| `/oape:api-generate-tests <path>`                                | Generate integration test suites for API types                 |
| `/oape:api-implement <EP-URL> [--design-doc <GIST-URL>] [--jira <TICKET>]` | Generate controller code from EP, design doc, and/or Jira ticket + API types |
| `/oape:analyze-rfe <rfe-key>`                                    | Analyze RFE and output EPIC, user stories, and outcomes        |
| `/oape:e2e-generate <base-branch>`                               | Generate e2e test artifacts from git diff against base branch  |
| `/oape:predict-regressions <base-branch>`                        | Predict API regressions and breaking changes from git diff     |
| `/oape:review <ticket_id> [base_ref]`                            | Production-grade code review against Jira requirements         |
| `/oape:implement-review-fixes <report>`                          | Automatically apply fixes from a review report                 |

### Input Sources for api-generate and api-implement

These commands support flexible input sources:

| Input Mode           | Command Example                                                                    |
| -------------------- | ---------------------------------------------------------------------------------- |
| EP only              | `/oape:api-generate https://github.com/openshift/enhancements/pull/1234`           |
| Design doc only      | `/oape:api-generate --design-doc https://gist.github.com/user/abc123`              |
| Jira ticket only     | `/oape:api-generate --jira OCPBUGS-12345`                                          |
| Jira ticket (URL)    | `/oape:api-generate --jira https://issues.redhat.com/browse/OCPBUGS-12345`         |
| EP + Design doc      | `/oape:api-generate https://github.com/openshift/enhancements/pull/1234 --design-doc https://gist.github.com/user/abc123` |
| EP + Jira ticket     | `/oape:api-generate https://github.com/openshift/enhancements/pull/1234 --jira OCPBUGS-12345` |
| All three sources    | `/oape:api-generate https://github.com/openshift/enhancements/pull/1234 --design-doc https://gist.github.com/user/abc123 --jira OCPBUGS-12345` |

When multiple sources are provided, precedence is: design document > Jira ticket > EP. The design document provides exact implementation details, the Jira ticket provides specific requirements and acceptance criteria, and the EP provides high-level context.

## Typical Workflow

```bash
# 1. Clone an operator repository (if not already cloned)
/oape:init https://github.com/openshift/cert-manager-operator main

# 2. Generate API types (using EP only)
/oape:api-generate https://github.com/openshift/enhancements/pull/XXXX

# 2b. Or generate API types with a detailed design document
/oape:api-generate https://github.com/openshift/enhancements/pull/XXXX --design-doc https://gist.github.com/user/my-design-doc

# 2c. Or generate API types from a Jira ticket
/oape:api-generate --jira OCPBUGS-12345

# 2d. Or combine Jira ticket with EP for richer context
/oape:api-generate https://github.com/openshift/enhancements/pull/XXXX --jira OCPBUGS-12345

# 3. Generate integration tests for the API types
/oape:api-generate-tests api/v1alpha1/

# 4. Predict potential regressions
/oape:predict-regressions main

# 5. Generate controller implementation
/oape:api-implement https://github.com/openshift/enhancements/pull/XXXX

# 5b. Or generate with detailed design document
/oape:api-implement https://github.com/openshift/enhancements/pull/XXXX --design-doc https://gist.github.com/user/my-design-doc

# 5c. Or generate from a Jira ticket
/oape:api-implement --jira OCPBUGS-12345

# 6. Build and verify
make generate && make manifests && make build && make test

# 7. Generate e2e tests for your changes
/oape:e2e-generate main
```

## Design Document Format

When using a design document (gist), it should contain structured implementation details:

```markdown
# Design Document: Feature Name

## API Specification
- Group: config.openshift.io
- Version: v1
- Kind: FeatureName
- Scope: Cluster (or Namespaced)

## Spec Fields
- `fieldName` (type): Description
  - Validation: required, enum values, min/max, pattern
  - Default: default value if any

## Status Fields
- `conditions`: Standard OpenShift conditions
- `observedGeneration`: int64

## Reconciliation Workflow (for api-implement)
1. Validate spec
2. Create/update dependent resources
3. Update status

## Dependent Resources (for api-implement)
- ConfigMap: purpose
- Deployment: purpose
```

---

## Supported Operator Repositories

The allowed repositories and their base branches are defined in [`team-repos.csv`](config/team-repos.csv). DO NOT raise PRs on any repos beyond that list. Always read `team-repos.csv` to determine the correct repo URL and base branch before cloning or creating branches.

---

## Operator Framework Detection

The commands automatically detect which framework the repository uses:

| Framework              | Detection                                   | Code Pattern                          |
| ---------------------- | ------------------------------------------- | ------------------------------------- |
| **controller-runtime** | `sigs.k8s.io/controller-runtime` in go.mod  | `Reconcile(ctx, req) (Result, error)` |
| **library-go**         | `github.com/openshift/library-go` in go.mod | `sync(ctx, syncCtx) error`            |

---

## Project Structure



---

## Prerequisites

Before running commands, ensure:

- **gh** (GitHub CLI) - installed and authenticated (`gh auth login`)
- **go** - Go toolchain installed
- **git** - Git installed
- **make** - Make installed
- **JIRA_PERSONAL_TOKEN** - Personal access token for Jira REST API (required when using `--jira` flag with api-generate or api-implement)

---

## Key Conventions

When generating code, these conventions are followed:

1. **OpenShift API Conventions** - [dev-guide/api-conventions.md](https://github.com/openshift/enhancements/blob/master/dev-guide/api-conventions.md)
2. **Kubernetes API Conventions** - [sig-architecture/api-conventions.md](https://github.com/kubernetes/community/blob/master/contributors/devel/sig-architecture/api-conventions.md)
3. **Effective Go** - [go.dev/doc/effective_go](https://go.dev/doc/effective_go)
4. **Kubebuilder Patterns** - [book.kubebuilder.io](https://book.kubebuilder.io/)

---

## Important Notes

- Always run `/oape:api-generate` before `/oape:api-generate-tests` or `/oape:api-implement`
- The commands READ existing code patterns and replicate them
- Generated code follows the repository's existing style
- API types are NOT modified by `api-generate-tests` or `api-implement` (they only read them)
