# PR Lifecycle Agent — Subtasks

**Parent Ticket:** [OAPE-710](https://redhat.atlassian.net/browse/OAPE-710)
**Type:** Story
**Status:** To Do
**Assignee:** Neha Kumari

> **Phase 1 Scope Change (2026-06-10, updated 2026-06-18 for Prow migration):** Phase 1 is a **Prow presubmit CI monitor** (`oape-ci-monitor`) configured centrally in `openshift/release` for each target repo. The presubmit runs alongside other CI jobs, polls until all checks reach a terminal state, then classifies failures, queries Sippy for flake history, and posts a structured report as a PR comment. Phase 1 is **report-only**: no auto-fix, no review comment handling, no Claude dependency. The report includes a machine-readable JSON output with suggested trigger actions for future phases. The job builds a `ci-monitor-agent` container image inline via ci-operator's `dockerfile_literal` and mounts Prow-managed secrets for GCP and GitHub App credentials. Configuration template: `docs/prow-ci-operator-config.yaml`.

## Motivation & Goals

### The Problem

OAPE automates the code-generation half of the feature development lifecycle — from Enhancement Proposal to API types, tests, and controller implementation. But once a PR is opened, the journey from **"PR created" to "PR merged" is entirely manual**. Developers are left to monitor CI checks, dig through Prow and GitHub Actions logs, fix trivial lint and formatting failures, parse noisy bot comments, track reviewer feedback, and push fixes — all by hand.

This manual loop is both time-consuming and mechanical. Each CI round-trip (fail → read logs → fix → push → wait for CI) takes 15–30 minutes. Trivial failures — formatting, import ordering, missing generated files — account for a large share of CI failures on OAPE-generated code, yet each one requires the same checkout-fix-verify-push cycle. Across multiple PRs and repos, this adds up to hours of wasted developer time per week.

The PR agent closes this gap by automating the post-PR lifecycle as a **Prow presubmit CI job**: CI monitoring, failure triage, trivial auto-fixing, review comment addressing, and status reporting — all running autonomously alongside other CI checks or on-demand via `/test oape-ci-monitor`.

### Why Prow Presubmit (Not K8s Jobs or GitHub Actions)

The existing OAPE execution model uses K8s Jobs via the go-server for code generation workloads. The PR agent uses Prow presubmit jobs instead because:

- **Native OpenShift CI integration**: Prow presubmits run alongside existing CI jobs in the same infrastructure — no separate runner fleet or workflow files per repo
- **Centralized configuration**: Job definitions live in `openshift/release` (`ci-operator/config/`), not scattered as `.github/workflows/*.yml` across target repos
- **Prow secret management**: Secrets (GCP ADC, GitHub App credentials) are mounted from the `test-credentials` namespace — no per-repo GitHub Actions secrets to configure
- **ci-operator image build**: The `ci-monitor-agent` container is built inline via `dockerfile_literal`, ensuring consistent dependencies across all target repos
- **ChatOps trigger**: Manually triggerable via `/test oape-ci-monitor` on any PR — no `workflow_dispatch` UI needed
- The go-server/K8s Job model is optimized for long-running code generation workloads that need specific tools and cluster access — the PR agent's presubmit-driven CI monitoring pattern integrates naturally with OpenShift CI

Human Review Required

> **AI-generated code must not be relied upon without human review.** All fixes pushed by these jobs must go through the standard GitHub PR review process. Repository OWNERS are responsible for reviewing and approving all changes.

### Before & After Workflow

```
BEFORE (manual):
  Developer opens PR
    → Polls `gh pr checks` repeatedly
    → Reads CI logs (Prow/GCS, GitHub Actions)
    → Identifies failure: "oh, it's just goimports"
    → Checks out branch, runs goimports, verifies build
    → Commits, pushes, waits 20 min for CI
    → Repeats for next failure
    → Reads 15 review comments, 10 are bots
    → Identifies 2 actionable items
    → Fixes, pushes again
  Total: 2–4 hours of mechanical work

AFTER (with Prow presubmit ci-monitor):
  Developer opens PR
    → CI runs (Prow presubmits fire, including oape-ci-monitor)
    → oape-ci-monitor polls until all other checks reach terminal state
    → Agent classifies failures deterministically (regex + Sippy)
    → Agent posts structured CI analysis report as PR comment
    → (Phase 2+) Agent auto-fixes trivial CI failures, addresses reviews
    → Developer sees failure categories, flake rates, and recommended actions
    → Developer focuses on actionable failures only
  Total: Developer spends ~10 min triaging CI results instead of reading raw logs
```

### Pain Points & Solutions


| Pain Point                                 | Impact                         | PR Agent Solution                                                                              |
| ------------------------------------------ | ------------------------------ | ---------------------------------------------------------------------------------------------- |
| Repeated manual CI polling                 | Context switching, wasted time | Prow presubmit runs alongside CI and reports once all checks complete — no manual polling needed |
| Fixing lint/format/generated-file failures | 15–30 min per round-trip       | Auto-fix engine applies `go fmt`, `goimports`, `make generate`                                 |
| Parsing noisy bot comments                 | Signal buried in noise         | Categorizes comments, filters bots, surfaces actionable items only                             |
| Waiting between CI re-runs                 | Hours of idle-but-blocked time | Agent pushes fixes immediately, compresses feedback loop                                       |
| Uncertainty about PR readiness             | "What still needs to happen?"  | Structured status report posted as PR comment                                                  |


### Capabilities at a Glance


| Capability              | Description                                                                                                                                                                                            |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| CI Monitoring           | Fetches all CI checks (GitHub Actions + Prow), categorizes status as passed/failed/pending                                                                                                             |
| Failure Analysis        | Classifies failures as trivial (auto-fixable) vs. non-trivial (requires human attention) via Claude Code                                                                                               |
| Auto-Fix Engine         | Runs the correct fix command, verifies compilation, commits and pushes                                                                                                                                 |
| Review Comment Handling | Analyzes unresolved review threads, addresses actionable feedback via Claude Code                                                                                                                      |
| Trigger Modes           | Prow presubmit (automatic on every PR push in configured repos, also triggerable via `/test oape-ci-monitor`)                                                                                          |
| Safety Guardrails       | File blocklists, commit limits, audit logging, dry-run mode                                                                                                                                            |
| Status Reporting        | Markdown report posted as PR comment; build logs available in Prow GCS artifacts                                                                                                                       |


### Prior Art: HyperShift AI-Assisted CI Jobs

This feature follows the pattern established by [HyperShift's AI-assisted CI jobs](https://hypershift.pages.dev/how-to/ci/ai-assisted-ci-jobs/), which use GitHub Actions workflows powered by Claude Code to automate Jira issue resolution, PR review comment handling, and dependabot triage. The PR Lifecycle Agent adapts this pattern for code-generation workflows, where failure patterns are more predictable (missing schemes, RBAC consistency, generated file sync).

Key design parallels with HyperShift:


| Aspect            | HyperShift                              | OAPE PR Agent                                                                                      |
| ----------------- | --------------------------------------- | -------------------------------------------------------------------------------------------------- |
| CI platform       | GitHub Actions                          | Prow presubmit (ci-operator)                                                                       |
| AI engine         | Claude Code CLI via Vertex AI           | Claude Code CLI via Vertex AI                                                                      |
| Periodic scanner  | `periodic-review-agent` (every 3h)      | N/A (presubmit-driven, no periodic sweep in Phase 1)                                               |
| On-demand trigger | `/test address-review-comments`         | `/test oape-ci-monitor` (Prow chatops)                                                             |
| PR scope          | `app/hypershift-jira-solve-ci` PRs only | All open PRs in allowed repos (`team-repos.csv`)                                                   |
| Max items per run | 10 PRs (review agent)                   | 4 PRs (configurable via `PR_AGENT_MAX_PRS`)                                                        |
| Max budget per PR | $5.00 per PR                            | $5.00 per PR (configurable via `MAX_BUDGET_PER_PR`, passed to `--max-budget-usd`)                  |
| Safety            | Draft PRs only, human review required   | File blocklists, commit limits, audit log                                                          |


### Expected Outcome

Once the PR Lifecycle Agent is complete, all open PRs in allowed repos (`team-repos.csv`) will be automatically monitored via a Prow presubmit job (`oape-ci-monitor`) configured centrally in `openshift/release`. The presubmit runs alongside other CI checks and reports once all are terminal. The agent will classify CI failures, post structured analysis reports, and (in later phases) fix trivial CI failures and address review comments — all without developer intervention. Developers can also trigger the agent on-demand via `/test oape-ci-monitor`. The measurable goal is to **eliminate manual trivial-fix round-trips** and reduce time from PR-opened to CI-green from hours to minutes for the common case.

---

## Completion Status

| # | Subtask | Phase | Status | Notes |
|---|---------|-------|--------|-------|
| 0 | Prow presubmit infrastructure | 1 | **Done** | `docs/prow-ci-operator-config.yaml`, `images/ci-monitor.Dockerfile`, release PR #80727 (rehearsal passed) |
| 1 | Entrypoint + PR discovery | 1 | **Done** | `scripts/pr-agent/entrypoint.sh` — periodic/on-demand modes, state persistence, per-PR timeout |
| 2 | CI check monitoring | 1 | **Done** | Dual implementation: lightweight in entrypoint.sh, comprehensive in `scripts/ci-monitor/monitor.sh` |
| 3 | Failure log analysis | 1 | **Partial** | Deterministic regex classification done. Claude fallback for `unknown` deferred to Phase 2 |
| 4 | Trivial auto-fix engine | 2 | **Partial** | `trivial-format`, `trivial-generated-files`, and `lint-failure` (treated as format) implemented in auto-fix.sh. Fine-grained `trivial-import`/`trivial-lint` classification deferred |
| 5 | Review comment handler | 2 | **In progress** | PR #63 (`oape-review-handler`) implements `review-handler.sh`, `address-review-comments` skill, `pr-agent-safety` skill, and Prow config. Open, not yet merged. |
| 6 | Pipeline wiring | 1 | **Done** | `process_pr()` orchestrates all phases; `dispatch.sh` routes actions (Phase 1 = log-only) |
| 7 | Safety guardrails | 1 | **Done** | `scripts/pr-agent/safety.sh` — blocklist, commit limits, audit log, retry helpers |
| 8 | Status reporting | 1 | **Done** | Report generation + idempotent PR comment posting in entrypoint.sh |
| 9 | Testing & validation | 1 | **Done** | `scripts/pr-agent/test-dry-run.sh` — shellcheck + dry-run integration + output verification |
| 10 | `/oape:pr-agent` command | 1 | **Done** | `plugins/oape/commands/pr-agent.md` + AGENTS.md command table |

**Phase 1 (report-only):** Complete — core infrastructure done (subtasks 0-2, 6-10). Subtasks 3, 4 partial.
**Phase 2 (auto-fix + review):** Subtask 4 (expand) and 3 (Claude fallback) remain. Subtask 5 in progress via PR #63.

---

## Subtask Overview

This document breaks the PR Lifecycle Agent into 10 implementable subtasks. Each subtask is self-contained with a clear definition, acceptance criteria, dependencies, and implementation hints. The architecture follows a **hybrid model**: deterministic bash for mechanical orchestration (PR discovery, CI polling, tool setup, safety guardrails) and Claude Code CLI for intelligent analysis (failure classification, review comment handling, complex code fixes).

### Dependency Graph

```
Subtask 0 (Prow Presubmit Infrastructure + ci-operator Config)
├── Subtask 1 (Entrypoint + PR Discovery + State Tracking)
│   ├── Subtask 2 (CI Monitoring)
│   │   └── Subtask 3 (Log Analysis + Deterministic Classification)
│   ├── Subtask 7 (Safety Guardrails) ← no dependencies (standalone utility)
│   │   └── Subtask 4 (Auto-Fix) ← depends on 3 + 7
│   ├── Subtask 5 (Review Comments)
│   ├── Subtask 6 (Wire Processing Pipeline) ← depends on 2–5, 7–8
│   │   Subtask 8 (Status Reporting) ← depends on 2–7
│   │   Subtask 9 (Testing) ← depends on all above
│   └── Subtask 10 (/oape:pr-agent Command) ← depends on 1–8
```

> **Note:** Subtask 7 (Safety Guardrails) is a standalone utility module with no dependencies
> on other subtasks. It provides blocklist, commit limit, audit log, and retry helper functions
> consumed by Subtasks 1, 2, 4, 5, and 6. Pipeline scripts (ci-monitor, auto-fix, review-handler,
> report) are standalone executables communicating via JSON files in `$RUNNER_TEMP`.
> `safety.sh` is a **sourced utility library** (`source scripts/pr-agent/safety.sh`) providing
> shared functions to all scripts that need them.

### Responsibility Split


| Layer                   | Responsibility                                                                                                                                                                                                                                                                                                                                    | Implementation                                                                            |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| **Prow ci-operator config** | Presubmit job definition, container image build (`ci-monitor-agent`), secret mounts, resource requests, timeout configuration                                                                                                                                                                                                                | `docs/prow-ci-operator-config.yaml` (template); actual config in `openshift/release`     |
| **Bash scripts**        | PR discovery, CI status polling, deterministic failure classification, safety guardrails, audit logging, reporting. Pipeline scripts are **standalone executables** communicating via JSON files in `$RUNNER_TEMP`. `safety.sh` is a **sourced utility library** providing shared functions (blocklist, audit log, retry helper, commit counter). | `scripts/pr-agent/*.sh`                                                                   |
| **Claude Code CLI**     | Fallback failure classification (for `unknown` categories), review comment analysis/response, complex code fixes. Skills included via `cat` in the prompt.                                                                                                                                                                                        | `plugins/oape/skills/*.md` content injected via `claude --print -p "$(cat SKILL.md) ..."` |


### Two-Layer Classification Taxonomy

The system uses two classification layers that serve different purposes:

| Layer | Script | Categories | Purpose |
| ----- | ------ | ---------- | ------- |
| **Job-level (triage)** | `scripts/ci-monitor/monitor.sh` | `install-failure`, `build-failure`, `lint-failure`, `test-failure`, `infra-flake`, `unknown` | Classifies CI job failures for reporting and triage. Maps to high-level actions: `retest`, `auto-fix-lint`, `investigate`. |
| **Fix-level (actionable)** | `scripts/pr-agent/entrypoint.sh` | `trivial-format`, `trivial-import`, `trivial-lint`, `trivial-generated-files`, `build-error`, `test-failure`, `infra-flake`, `unknown` | Classifies failures by the specific fix command needed: `go fmt`, `goimports`, `make generate`, etc. |

`dispatch.sh` bridges the two layers: it reads `monitor.sh`'s job-level classification (e.g., `lint-failure` → `auto-fix-lint` action) and invokes the appropriate `pr-agent/` script, which performs the finer-grained fix-level classification to determine the exact fix command.


---

## Subtask 0: Prow presubmit infrastructure and ci-operator configuration

### Description

Establish the Prow presubmit job infrastructure that all subsequent subtasks build upon. This includes the ci-operator config snippet that defines the `ci-monitor-agent` container image (built inline via `dockerfile_literal`) and the `oape-ci-monitor` presubmit test step. The config is added to each target repo's ci-operator config in `openshift/release`.

> **Phase 1 implementation:** A single `oape-ci-monitor` presubmit job runs as `always_run: true, optional: true` alongside other CI jobs. It polls until all other checks reach a terminal state, then runs `monitor.sh` and `dispatch.sh`. Manually triggerable via `/test oape-ci-monitor`.

### Acceptance Criteria

1. A reference ci-operator config exists at `docs/prow-ci-operator-config.yaml` with three snippets:
  - Inline `ci-monitor-agent` image build under `images.items[]`
  - Promotion exclusion under `promotion.to[].excluded_images`
  - Presubmit test definition under `tests[]`
2. The `ci-monitor-agent` image is built from `registry.access.redhat.com/ubi9/go-toolset` and installs: `git`, `make`, `jq`, `gh` CLI. It clones `oape-ai-e2e` at build time and copies scripts/plugins/config into the image. It installs `goimports` and `golangci-lint`.
3. The presubmit job is defined as `always_run: true, optional: true` with job name `oape-ci-monitor`.
4. Authentication is configured with a fallback strategy:
  - **Primary:** GitHub App installation token generated inline via JWT signing from the PEM key mounted at `/var/run/github-app/private-key.pem` (secret: `openshift-app-platform-shift-github-bot` in `test-credentials` namespace). Required for Phase 2+ auto-fix pushes that must trigger downstream CI.
  - **Fallback:** If the GitHub App is not installed on the target repo, falls back to `GITHUB_TOKEN` (Prow-provided). Sufficient for Phase 1 (read + comment only). Logs a warning with instructions to install the App for Phase 2+.
  - Claude API access via GCP Application Default Credentials mounted at `/var/run/gcloud-adc/application_default_credentials.json` (secret: `oap-lts-claude-gcp-vertex-sa` in `test-credentials` namespace).
5. Job timeout is set to `2h30m0s`. Resource requests: 1 CPU, 500Mi memory.
6. The test step invokes `/app/scripts/ci-monitor/monitor.sh` followed by `/app/scripts/ci-monitor/dispatch.sh`.
7. Manually triggerable via `/test oape-ci-monitor` on any PR in a configured target repo.
8. **Rehearsal detection:** When the job runs as a Prow rehearsal (i.e., `REPO_NAME=release` and `REPO_OWNER=openshift`), it detects the `openshift/release` context and switches to a real open PR on the target repo (e.g., `openshift/must-gather-operator`). The rehearsal runs the full pipeline — including posting the analysis comment on the target PR — to validate the end-to-end flow without requiring the release PR to be merged first. The first open PR on the target repo is selected via the GitHub API.

### Dependencies

None — this is the foundation subtask.

### Implementation Hints

- **ci-operator config template** (`docs/prow-ci-operator-config.yaml`):
  The config contains three snippets to add to the target repo's ci-operator config in `openshift/release` at `ci-operator/config/REPO_ORG/REPO_NAME/REPO_ORG-REPO_NAME-BRANCH.yaml`:

  1. **Inline image build** — builds the `ci-monitor-agent` container:
  ```yaml
  images:
    items:
    - dockerfile_literal: |-
        FROM registry.access.redhat.com/ubi9/go-toolset
        USER 0
        RUN dnf install -y git make jq && \
            dnf install -y 'dnf-command(config-manager)' && \
            dnf config-manager --add-repo https://cli.github.com/packages/rpm/gh-cli.repo && \
            dnf install -y gh && \
            dnf clean all
        WORKDIR /app
        RUN git clone --depth 1 -b main https://github.com/openshift-eng/oape-ai-e2e.git /tmp/oape && \
            cp -r /tmp/oape/scripts /app/scripts && \
            cp -r /tmp/oape/plugins /plugins && \
            mkdir -p /config && cp -r /tmp/oape/deploy/config/* /config/ && \
            rm -rf /tmp/oape
        RUN go install golang.org/x/tools/cmd/goimports@latest && \
            curl -sSfL https://raw.githubusercontent.com/golangci/golangci-lint/master/install.sh | sh -s -- -b /usr/local/bin
        RUN git config --global user.name "openshift-app-platform-shift-bot" && \
            git config --global user.email "267347085+openshift-app-platform-shift-bot@users.noreply.github.com"
        RUN chmod -R g=u /opt/app-root/src
        USER 1001
      to: ci-monitor-agent
  ```

  2. **Presubmit test step** — runs the CI monitor:
  ```yaml
  - always_run: true
    as: oape-ci-monitor
    optional: true
    steps:
      test:
      - as: monitor
        commands: |
          set -euo pipefail

          echo "[setup] Starting oape-ci-monitor for ${REPO_OWNER}/${REPO_NAME} PR#${PULL_NUMBER}"

          # --- Rehearsal detection ---
          # Prow rehearsal runs against openshift/release, not the target repo.
          # Switch to a real target-repo PR to validate the full pipeline.
          if [[ "${REPO_NAME}" == "release" && "${REPO_OWNER}" == "openshift" ]]; then
            echo "[setup] Detected openshift/release context — switching to test target"
            export REPO_OWNER="REPO_ORG"
            export REPO_NAME="REPO_NAME"
            TEST_PR=$(curl -s "https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/pulls?state=open&per_page=1" \
              | python3 -c "import sys,json; data=json.load(sys.stdin); print(data[0]['number'] if data else '')" 2>/dev/null || echo "")
            if [[ -z "$TEST_PR" ]]; then
              echo "[setup] No open PRs found on ${REPO_OWNER}/${REPO_NAME} — skipping"
              exit 0
            fi
            export PULL_NUMBER="$TEST_PR"
            export PR_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}/pull/${PULL_NUMBER}"
            echo "[setup] Testing against ${REPO_OWNER}/${REPO_NAME}#${PULL_NUMBER}"
          fi

          # --- GitHub auth: try App token, fall back to GITHUB_TOKEN ---
          # App token is preferred (required for Phase 2+ pushes that trigger CI).
          # For Phase 1 (report-only), GITHUB_TOKEN is sufficient for read + comment.
          USE_APP_TOKEN="false"
          if [[ -f /var/run/github-app/app-id && -f /var/run/github-app/private-key.pem ]]; then
            echo "[auth] Attempting GitHub App token..."
            APP_ID=$(cat /var/run/github-app/app-id)
            PEM_PATH="/var/run/github-app/private-key.pem"
            HEADER=$(printf '{"alg":"RS256","typ":"JWT"}' | openssl base64 -e -A | tr '+/' '-_' | tr -d '=')
            NOW=$(date +%s); EXP=$((NOW + 300))
            PAYLOAD=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' "$NOW" "$EXP" "$APP_ID" | openssl base64 -e -A | tr '+/' '-_' | tr -d '=')
            SIGNATURE=$(printf '%s' "${HEADER}.${PAYLOAD}" | openssl dgst -sha256 -sign "$PEM_PATH" -binary | openssl base64 -e -A | tr '+/' '-_' | tr -d '=')
            JWT="${HEADER}.${PAYLOAD}.${SIGNATURE}"

            INSTALL_RESPONSE=$(curl -s -w "\n%{http_code}" -H "Authorization: Bearer ${JWT}" -H "Accept: application/vnd.github+json" \
              "https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/installation")
            HTTP_CODE=$(echo "$INSTALL_RESPONSE" | tail -1)
            INSTALL_BODY=$(echo "$INSTALL_RESPONSE" | sed '$d')

            if [[ "$HTTP_CODE" -eq 200 ]]; then
              INST_ID=$(echo "$INSTALL_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
              TOKEN_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST -H "Authorization: Bearer ${JWT}" -H "Accept: application/vnd.github+json" \
                "https://api.github.com/app/installations/${INST_ID}/access_tokens")
              T_CODE=$(echo "$TOKEN_RESPONSE" | tail -1)
              T_BODY=$(echo "$TOKEN_RESPONSE" | sed '$d')
              if [[ "$T_CODE" -eq 201 ]]; then
                export GH_TOKEN=$(echo "$T_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
                USE_APP_TOKEN="true"
                echo "[auth] GitHub App token generated successfully"
              else
                echo "[auth] WARN: App token creation failed (HTTP ${T_CODE}), falling back to GITHUB_TOKEN"
              fi
            else
              echo "[auth] WARN: App not installed on ${REPO_OWNER}/${REPO_NAME} (HTTP ${HTTP_CODE}), falling back to GITHUB_TOKEN"
            fi
          else
            echo "[auth] GitHub App credentials not mounted, using GITHUB_TOKEN"
          fi

          if [[ "$USE_APP_TOKEN" != "true" ]]; then
            if [[ -z "${GH_TOKEN:-}" && -z "${GITHUB_TOKEN:-}" ]]; then
              echo "[auth] ERROR: No GitHub token available (App token failed and GITHUB_TOKEN not set)" >&2
              exit 1
            fi
            export GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN}}"
            echo "[auth] Using GITHUB_TOKEN (Phase 1 report-only — sufficient for read + comment)"
            echo "[auth] NOTE: Phase 2+ auto-fix pushes require the GitHub App to be installed on ${REPO_OWNER}/${REPO_NAME}"
          fi

          # --- GCP auth for Claude (Vertex AI) ---
          export GOOGLE_APPLICATION_CREDENTIALS="/var/run/gcloud-adc/application_default_credentials.json"
          export CLAUDE_CODE_USE_VERTEX="1"
          export CLOUD_ML_REGION="global"
          export ANTHROPIC_VERTEX_PROJECT_ID="itpc-gcp-hcm-pe-eng-claude"

          # --- Run CI monitor ---
          export PR_URL="https://github.com/${REPO_OWNER}/${REPO_NAME}/pull/${PULL_NUMBER}"
          export SKIP_POLL="false"
          export SELF_JOB_NAME="oape-ci-monitor"
          export BUILD_ID="${BUILD_ID:-}"
          export OAPE_RUN_URL="${BUILD_LOG_URL:-}"

          gh auth setup-git
          /app/scripts/ci-monitor/monitor.sh
          /app/scripts/ci-monitor/dispatch.sh
        credentials:
        - mount_path: /var/run/gcloud-adc
          name: oap-lts-claude-gcp-vertex-sa
          namespace: test-credentials
        - mount_path: /var/run/github-app
          name: openshift-app-platform-shift-github-bot
          namespace: test-credentials
        from: ci-monitor-agent
        resources:
          requests:
            cpu: "1"
            memory: 500Mi
        timeout: 2h30m0s
  ```

- **GitHub App token** is preferred over `GITHUB_TOKEN` because pushes made with `GITHUB_TOKEN` do not trigger downstream CI runs (GitHub's anti-recursion rule). The Prow job attempts to generate the App token inline via JWT signing from the mounted PEM key. If the App is not installed on the target repo, it falls back to `GITHUB_TOKEN` which is sufficient for Phase 1 (read + comment only). Phase 2+ auto-fix pushes require the App to be installed.
- **Rollout**: To add the CI monitor to a target repo, copy the three snippets from `docs/prow-ci-operator-config.yaml` into the target repo's ci-operator config in `openshift/release` and submit a PR. The rehearsal detection block allows validating the full pipeline (including comment posting on the target repo) via `/pj-rehearse` before merging the release PR.

### Files


| File                                | Action                                                               |
| ----------------------------------- | -------------------------------------------------------------------- |
| `docs/prow-ci-operator-config.yaml` | Create (reference ci-operator config for target repos)               |


---

## Subtask 1: Create entrypoint script with PR discovery and prechecks

### Description

Create the main bash entrypoint script that orchestrates the PR agent workflow. This script handles two execution modes: **periodic** (discovers all open PRs across allowed repos and processes each) and **on-demand** (processes a single specified PR). It includes argument parsing, prechecks, and the top-level processing loop that invokes downstream capabilities (CI monitoring, auto-fix, review handling).

> **Phase 1 implementation:** The entrypoint supports a `--monitor-only` flag that skips the auto-fix phase entirely, running only CI monitoring, deterministic classification, and status reporting. Phase 1 uses on-demand + `--monitor-only` mode exclusively. The periodic sweep and auto-fix code paths exist but are not exercised until Phase 2.

### Acceptance Criteria

1. Entrypoint script accepts `--mode` flag with values `periodic` or `on-demand`.
2. In `periodic` mode:
  - Queries GitHub for all open PRs across repos listed in `deploy/config/team-repos.csv`.
  - Processes up to `PR_AGENT_MAX_PRS` (default 4) PRs per run. Kept low to ensure the run completes within the Prow job timeout (`2h30m0s`).
  - Adds a 60-second delay between processing each PR (rate limiting).
3. In `on-demand` mode:
  - Accepts `--pr-url <URL>` argument.
  - Parses PR URL in both formats: full URL (`https://github.com/org/repo/pull/123`) and shorthand (`org/repo#123`). Extracts owner, repo name, and PR number.
  - Validates the PR exists and is in `open` state.
  - Validates the PR targets a repo listed in `deploy/config/team-repos.csv`. Rejects PRs from repos not in the allowlist.
4. In `periodic` mode, filters out PRs with the `pr-agent:skip` label. Developers can add this label to exclude specific PRs from automated processing.
5. Before processing each PR, checks for merge conflicts via `gh pr view --json mergeable -q .mergeable`. If `CONFLICTING`, skips CI analysis and auto-fix phases, proceeding directly to the status report with "merge conflict" as the primary finding.
6. Prechecks all pass before any work begins:
  - `gh auth status` confirms GitHub CLI authentication.
  - Claude Code CLI is available (`claude --version`).
  - Required environment variables are set (`GH_TOKEN`, `CLAUDE_CODE_USE_VERTEX`).
7. Fails immediately with a clear, prefixed error message (e.g., `PRECHECK FAILED: PR #123 is not open`) when any precheck fails.
8. Emits structured log lines to stdout for each PR processed: `[PR #N] owner/repo#123 — processing started`.
9. Stays within Prow job timeout: The Prow presubmit timeout is `2h30m0s`. The GitHub App installation token is generated inline at job start and is valid for 1 hour, which is sufficient for single-PR presubmit processing. The periodic run limits `PR_AGENT_MAX_PRS` to 4 (default) to ensure processing completes within this window.
10. Maintains lightweight state persistence to avoid re-processing: tracks which CI jobs have been analyzed and which review comments have been addressed. State is persisted **across job runs** by embedding a hidden state block in the PR report comment: `<!-- oape-pr-agent-state:BASE64_ENCODED_JSON -->`. The state schema includes `analyzed` (array of `name:url` job keys — URL changes after `/retest`, ensuring re-runs get fresh analysis), `addressed` (array of comment IDs), and `last_run` (ISO timestamp). On each run, the agent reads the existing report comment, parses the embedded state, and skips already-processed jobs and comments. Within a run, an in-memory copy in `$RUNNER_TEMP/pr-agent-state-<owner>-<repo>-<pr-number>.json` prevents duplicate work across multiple PRs.
11. Wraps `gh` API calls in a retry helper function with exponential backoff (3 retries at 5s/15s/45s intervals) for resilience against transient GitHub API failures.

### Dependencies

Subtask 0 (Prow presubmit infrastructure must exist).

### Implementation Hints

- **PR discovery for periodic mode:**
  ```bash
  # Query all open PRs across allowed repos
  {
    read -r  # Skip CSV header row
    while IFS=, read -r product role repo_url; do
      owner_repo=$(echo "$repo_url" | sed 's|https://github.com/||;s|\.git$||')
      prs=$(gh pr list --repo "$owner_repo" \
        --state open --json number,url,headRefName,title,labels --limit 20)
      # Filter out PRs with pr-agent:skip label
      prs=$(echo "$prs" | jq '[.[] | select(.labels | map(.name) | index("pr-agent:skip") | not)]')
      # Append to processing list
    done
  } < deploy/config/team-repos.csv
  ```
- **PR URL parsing:**
  ```bash
  parse_pr_url() {
    local url="$1"
    if [[ "$url" =~ https://github.com/([^/]+)/([^/]+)/pull/([0-9]+) ]]; then
      OWNER="${BASH_REMATCH[1]}"
      REPO="${BASH_REMATCH[2]}"
      PR_NUMBER="${BASH_REMATCH[3]}"
    elif [[ "$url" =~ ^([^/]+)/([^#]+)#([0-9]+)$ ]]; then
      OWNER="${BASH_REMATCH[1]}"
      REPO="${BASH_REMATCH[2]}"
      PR_NUMBER="${BASH_REMATCH[3]}"
    else
      echo "PRECHECK FAILED: Invalid PR URL format: $url" >&2
      return 1
    fi
  }
  ```
- **PR validation:**
  ```bash
  pr_state=$(gh pr view "$PR_URL" --json state -q .state)
  if [[ "$pr_state" != "OPEN" ]]; then
    echo "PRECHECK FAILED: PR $PR_URL is not open (state: $pr_state)" >&2
    return 1
  fi
  ```
- **Merge conflict detection (run before processing):**
  ```bash
  check_merge_conflicts() {
    local pr_url="$1"
    local mergeable
    mergeable=$(gh_retry gh pr view "$pr_url" --json mergeable -q .mergeable)
    if [[ "$mergeable" == "CONFLICTING" ]]; then
      echo "[PR] Merge conflict detected — skipping CI analysis and auto-fix"
      return 1
    fi
    return 0
  }
  ```
- **GitHub API retry helper:**
  ```bash
  gh_retry() {
    local retries=3 delay=5
    for ((i = 1; i <= retries; i++)); do
      if "$@"; then return 0; fi
      if [[ "$i" -lt "$retries" ]]; then
        echo "[retry] Attempt $i/$retries failed, waiting ${delay}s..." >&2
        sleep "$delay"
        delay=$((delay * 3))
      fi
    done
    echo "[retry] All $retries attempts failed for: $*" >&2
    return 1
  }
  ```
- **Processing loop structure:**
  ```bash
  process_pr() {
    local pr_url="$1"
    parse_pr_url "$pr_url"
    
    echo "[PR #${PR_NUMBER}] ${OWNER}/${REPO}#${PR_NUMBER} — processing started"

    # Phase 0: Merge conflict check
    if ! check_merge_conflicts "$pr_url"; then
      # Skip to status report with merge conflict finding
      scripts/pr-agent/report.sh --pr-url "$pr_url" --merge-conflict
      return 0
    fi
    
    # Phase 1: CI Check Monitoring (Subtask 2)
    # Phase 2: Failure Analysis + Auto-Fix (Subtasks 3, 4)
    # Phase 3: Review Comment Handling (Subtask 5)
    # Phase 4: Status Report (Subtask 8)
    
    echo "[PR #${PR_NUMBER}] ${OWNER}/${REPO}#${PR_NUMBER} — processing complete"
  }
  ```
- **Reference:** HyperShift's Jira Agent iterates over issues with a 60-second rate limit between each. The Review Agent iterates over PRs similarly.

### Files


| File                             | Action |
| -------------------------------- | ------ |
| `scripts/pr-agent/entrypoint.sh` | Create |


---

## Subtask 2: Implement CI check monitoring and status polling

### Description

Add the CI monitoring capability as a standalone executable script. The agent needs to fetch the current state of all CI checks on the PR, categorize the overall status, and extract details about any failures. This is the primary input that drives the analyze-fix cycle. Uses `gh pr checks` which aggregates both GitHub Actions checks (Checks API) and Prow/OpenShift CI status checks (Status API) in a single call, matching the yolo-agent pattern.

### Acceptance Criteria

1. Fetches all CI checks for the PR using `gh pr checks` which aggregates both the Checks API (GitHub Actions) and the Status API (Prow/OpenShift CI) in one call.
2. Categorizes overall PR CI status into one of: `all-passed`, `some-failed`, `all-pending`, `mixed-pending`, `no-checks`.
3. For each failed check, extracts: check name, workflow/job name, conclusion (failure/cancelled/timed_out), and the URL to the failed run.
4. Handles `pending` state correctly — reports it as "in progress" rather than treating it as a failure. When all non-pending checks pass, reports status as `mixed-pending`.
5. Outputs structured JSON to a temp file (`$RUNNER_TEMP/ci-status-<owner>-<repo>-<pr-number>.json`) for consumption by downstream scripts.
6. Writes a one-line summary to stdout: `[CI] 8/10 passed | 1 failed | 1 pending`.
7. Is a standalone executable script that accepts `--owner`, `--repo`, and `--pr-number` arguments and writes output to `$RUNNER_TEMP`.

### Dependencies

Subtask 1 (entrypoint must exist and provide PR context variables).

### Implementation Hints

- **CI status fetching via `gh pr checks`** (aggregates both Checks API and Status API):
  ```bash
  fetch_ci_status() {
    local owner="$1" repo="$2" pr_number="$3"
    local output_file="${RUNNER_TEMP}/ci-status-${owner}-${repo}-${pr_number}.json"

    # gh pr checks aggregates both GitHub Actions (Checks API) and Prow (Status API)
    gh_retry gh pr checks "$pr_number" --repo "${owner}/${repo}" \
      --json name,state,link,bucket \
      > "$output_file"

    # Compute and append summary
    local total passed failed pending
    total=$(jq 'length' "$output_file")
    passed=$(jq '[.[] | select(.bucket == "pass")] | length' "$output_file")
    failed=$(jq '[.[] | select(.bucket == "fail")] | length' "$output_file")
    pending=$(jq '[.[] | select(.bucket == "pending")] | length' "$output_file")

    echo "[CI] ${passed}/${total} passed | ${failed} failed | ${pending} pending"
  }
  ```
- **Status aggregation logic:**
  ```bash
  aggregate_ci_status() {
    local status_file="${RUNNER_TEMP}/ci-status-${1}-${2}-${3}.json"  # owner, repo, pr_number
    local total passed failed pending
    total=$(jq 'length' "$status_file")
    passed=$(jq '[.[] | select(.bucket == "pass")] | length' "$status_file")
    failed=$(jq '[.[] | select(.bucket == "fail")] | length' "$status_file")
    pending=$(jq '[.[] | select(.bucket == "pending")] | length' "$status_file")

    if [[ "$total" -eq 0 ]]; then echo "no-checks"
    elif [[ "$failed" -gt 0 ]]; then echo "some-failed"
    elif [[ "$pending" -eq "$total" ]]; then echo "all-pending"
    elif [[ "$pending" -gt 0 ]]; then echo "mixed-pending"
    else echo "all-passed"
    fi
  }
  ```
- **Reference:** The yolo-agent uses `gh pr checks` with the same `--json name,state,link,bucket` pattern. HyperShift's review agent skips PRs where all checks pass.

### Files


| File                             | Action                                                    |
| -------------------------------- | --------------------------------------------------------- |
| `scripts/pr-agent/ci-monitor.sh` | Create (standalone executable, called by `entrypoint.sh`) |


---

## Subtask 3: Implement CI failure log analysis and root cause classification

### Description

Create the failure log fetching and classification pipeline as a standalone executable script. Classification uses a **two-tier approach**: deterministic regex-based classification first (handles ~80-90% of cases with zero API cost), with Claude Code CLI as a fallback only for failures classified as `unknown`. A Claude Code skill at `plugins/oape/skills/ci-failure-analysis/SKILL.md` serves as the single source of truth for classification taxonomy — its content is included via `cat` in the Claude prompt.

### Acceptance Criteria

1. A Claude Code skill exists at `plugins/oape/skills/ci-failure-analysis/SKILL.md` following the project's skill pattern. Its content is included in the Claude prompt via `cat` (not loaded via the plugin system).
2. **Deterministic classification first:** A bash function `classify_failure_deterministic()` uses regex patterns to classify failures without any Claude API call. Covers: `trivial-lint`, `trivial-format`, `trivial-import`, `trivial-generated-files`, `build-error`, `test-failure`, `infra-flake`.
3. **Claude Code as fallback only:** Claude CLI is invoked only for failures classified as `unknown` by the deterministic step. The classification step is read-only — it analyzes logs but never modifies files or runs git write operations. The skill content is included via `cat`:
  ```bash
   CLASSIFICATION_SCHEMA='{"type":"array","items":{"type":"object","properties":{"category":{"type":"string","enum":["trivial-lint","trivial-format","trivial-import","trivial-generated-files","build-error","test-failure","infra-flake","unknown"]},"confidence":{"type":"string","enum":["high","medium","low"]},"affected_files":{"type":"array","items":{"type":"string"}},"root_cause":{"type":"string"},"suggested_fix":{"type":"string"}},"required":["category","confidence","root_cause"]}}'

   claude --print -p "$(cat plugins/oape/skills/ci-failure-analysis/SKILL.md)

   Analyze the following CI failure log: $(cat "$LOG_FILE")" \
     --allowedTools "Bash(curl*),Read" \
     --json-schema "$CLASSIFICATION_SCHEMA" --max-budget-usd "${MAX_BUDGET_PER_PR:-5.00}"
  ```
4. Classifies each failure into exactly one category: `trivial-lint`, `trivial-format`, `trivial-import`, `trivial-generated-files`, `build-error`, `test-failure`, `infra-flake`, or `unknown`.
5. For trivial failures, identifies the specific files and (where possible) line numbers causing the issue.
6. Distinguishes infrastructure flakes (timeouts, network errors, pod scheduling failures, registry pull errors) from genuine code issues.
7. Produces a structured JSON analysis output per failed check containing: category, confidence level (high/medium/low), affected files, root cause summary, and suggested fix action.
8. Is a standalone executable script that accepts `--owner`, `--repo`, and `--pr-number` and reads CI status from `$RUNNER_TEMP/ci-status-<owner>-<repo>-<pr-number>.json`.

### Dependencies

Subtask 2 (needs the list of failed checks and their URLs from the CI status JSON).

### Implementation Hints

- **Log fetching (deterministic bash, before classification):**
  ```bash
  fetch_failure_logs() {
    local pr_number="$1"
    local status_file="${RUNNER_TEMP}/ci-status-${owner}-${repo}-${pr_number}.json"

    # gh pr checks output uses .bucket and .link fields
    jq -r '.[] | select(.bucket == "fail") | .link' "$status_file" | while read -r url; do
      if [[ "$url" == *"github.com"*"/actions/"* ]]; then
        # GitHub Actions: extract run ID, fetch failed logs
        run_id=$(echo "$url" | grep -oP 'runs/\K[0-9]+')
        gh_retry gh run view "$run_id" --log-failed > "${RUNNER_TEMP}/log-${run_id}.txt" 2>/dev/null || true
      elif [[ "$url" == *"prow.ci.openshift.org"* ]]; then
        # Prow: target_url points to Prow UI (e.g., https://prow.ci.openshift.org/view/gs/BUCKET/PATH)
        # Extract the GCS path and fetch build-log.txt from gcsweb
        local gcs_path
        gcs_path=$(echo "$url" | sed -n 's|.*/view/g[cs]s\?/||p')
        if [[ -n "$gcs_path" ]]; then
          local gcsweb_base="${GCSWEB_BASE_URL:-https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com}"
          local gcsweb_url="${gcsweb_base}/gcs/${gcs_path}/build-log.txt"
          # Prow build-log.txt can be 100K+ lines; truncate to last 1000 lines (failure is at the tail)
          curl -sSL "$gcsweb_url" | tail -1000 > "${RUNNER_TEMP}/log-prow-$(date +%s).txt" 2>/dev/null || true
        fi
      fi
    done
  }
  ```
- **Deterministic classification (handles ~80-90% of cases, zero API cost):**
  ```bash
  classify_failure_deterministic() {
    local log_file="$1"
    local content
    content=$(cat "$log_file")

    if echo "$content" | grep -qiE 'golangci-lint|golint|staticcheck|revive'; then
      echo "trivial-lint"
    elif echo "$content" | grep -qiE 'gofmt|goimports|formatting differs|diff.*\.go'; then
      echo "trivial-format"
    elif echo "$content" | grep -qiE 'imported and not used|could not import|import ordering'; then
      echo "trivial-import"
    elif echo "$content" | grep -qiE 'generated code is out of date|make generate|make manifests|deepcopy-gen|zz_generated'; then
      echo "trivial-generated-files"
    elif echo "$content" | grep -qiE 'cannot compile|undefined:|syntax error|cannot use.*as.*in'; then
      echo "build-error"
    elif echo "$content" | grep -qiE '--- FAIL|FAIL\s|panic:.*test|assertion failed'; then
      echo "test-failure"
    elif echo "$content" | grep -qiE 'context deadline exceeded|connection refused|i/o timeout|ErrImagePull|pod sandbox|TLS handshake timeout'; then
      echo "infra-flake"
    else
      echo "unknown"
    fi
  }
  ```
- **Two-tier classification flow:**
  ```bash
  classify_failures() {
    local pr_number="$1"
    local log_dir="${RUNNER_TEMP}"
    local results="[]"
    local unknown_logs=""

    for log_file in "${log_dir}"/log-*.txt; do
      [[ -f "$log_file" ]] || continue
      local category
      category=$(classify_failure_deterministic "$log_file")

      if [[ "$category" != "unknown" ]]; then
        # Deterministic classification — no Claude API cost
        results=$(echo "$results" | jq --arg cat "$category" --arg file "$log_file" \
          '. + [{"category": $cat, "confidence": "high", "affected_files": [], "root_cause": $cat, "suggested_fix": ""}]')
      else
        unknown_logs="${unknown_logs} ${log_file}"
      fi
    done

    # Fallback: invoke Claude only for unknown failures (read-only analysis)
    if [[ -n "$unknown_logs" ]]; then
      local claude_result
      local classification_schema='{"type":"array","items":{"type":"object","properties":{"category":{"type":"string","enum":["trivial-lint","trivial-format","trivial-import","trivial-generated-files","build-error","test-failure","infra-flake","unknown"]},"confidence":{"type":"string","enum":["high","medium","low"]},"affected_files":{"type":"array","items":{"type":"string"}},"root_cause":{"type":"string"},"suggested_fix":{"type":"string"}},"required":["category","confidence","root_cause"]}}'
      if ! claude_result=$(claude --print \
        --max-budget-usd "${MAX_BUDGET_PER_PR:-5.00}" \
        -p "$(cat plugins/oape/skills/ci-failure-analysis/SKILL.md)
      Analyze the following CI failure logs and classify each failure.
      $(for f in $unknown_logs; do echo "--- $(basename "$f") ---"; tail -1000 "$f"; done)" \
        --allowedTools "Bash(curl*),Read" \
        --json-schema "$classification_schema" 2>"${RUNNER_TEMP}/claude-stderr.txt"); then
        audit_log "error" "claude-classification" "" "" \
          "Claude CLI failed: $(head -1 "${RUNNER_TEMP}/claude-stderr.txt")"
        claude_result='[]'
      fi
      results=$(echo "$results" | jq --argjson cr "$claude_result" '. + $cr')
    fi

    echo "$results" > "${RUNNER_TEMP}/failure-analysis-${owner}-${repo}-${pr_number}.json"
  }
  ```
- **Classification heuristics (documented in the skill for Claude's guidance):**
  - `trivial-lint`: Log contains `golangci-lint`, `golint`, `staticcheck`, or linter rule names.
  - `trivial-format`: Log contains `gofmt`, `goimports`, `diff` output showing whitespace/formatting-only changes.
  - `trivial-import`: Log contains `imported and not used`, `could not import`, or import ordering errors.
  - `trivial-generated-files`: Log contains `generated code is out of date`, `make generate`, `make manifests`, `deepcopy-gen`.
  - `build-error`: Log contains `cannot compile`, `undefined:`, `syntax error`, compilation errors.
  - `test-failure`: Log contains `FAIL`, `--- FAIL`, test function names, assertion failures.
  - `infra-flake`: Log contains `context deadline exceeded`, `connection refused`, `i/o timeout`, `ErrImagePull`, `pod sandbox`.
- **Skill structure:** Follow `plugins/oape/skills/analyze-rfe/SKILL.md` pattern — persona, prerequisites, step-by-step procedure.

### Files


| File                                               | Action                                                                                      |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `scripts/pr-agent/log-analyzer.sh`                 | Create (standalone executable: log fetching, deterministic classification, Claude fallback) |
| `plugins/oape/skills/ci-failure-analysis/SKILL.md` | Create                                                                                      |


---

## Subtask 4: Implement trivial auto-fix engine

### Description

Build the automated fix-and-push capability for trivial CI failures. When the failure analysis (Subtask 3) identifies a trivial issue, the agent checks out the PR branch, applies the appropriate fix command, verifies the fix compiles, and pushes. Auto-fix is always enabled in the CI job context (unlike the interactive mode which required `--auto-fix`). The `DRY_RUN` environment variable controls whether modifications are actually committed and pushed.

### Acceptance Criteria

1. In `DRY_RUN=true` mode, reports what *would* be fixed without modifying files or pushing.
2. Maps each trivial failure category to the correct fix command:
  - `trivial-format` → `go fmt ./...`
  - `trivial-import` → `goimports -w <affected-files>` (or `go fmt ./...` if goimports unavailable)
  - `trivial-lint` → targeted fix based on linter rule (e.g., `golangci-lint run --fix` where supported)
  - `trivial-generated-files` → `make generate && make manifests`
3. Verifies fix compiles successfully (`go build ./...` and `go vet ./...`) before committing.
4. Creates a commit with a descriptive message following repository conventions (e.g., `fix: run goimports to resolve CI lint failure`).
5. Pushes to the PR's head branch using the GitHub App token (not `GITHUB_TOKEN`) to ensure CI is re-triggered.
6. Reports the fix to the audit log (commit SHA, files changed, fix type).
7. Respects all safety guardrails from Subtask 7 (file blocklist, commit limits, diff size limits).
8. **(Phase 2)** For `infra-flake` failures: posts a targeted `/test <job-name>` comment to re-trigger only the flaky job (not blanket `/retest`). Gated by `RETEST_INFRA_FLAKES` config flag (default `false`). Limited to max 2 retests per job per run to prevent retry loops. Tracked in the state to avoid re-posting on subsequent runs.

### Dependencies

Subtask 3 (needs failure classification to determine fix type and affected files).
Subtask 7 (safety guardrails must be enforced before any file modification).

### Implementation Hints

- **Checkout and fix flow:**
  ```bash
  apply_trivial_fixes() {
    local owner="$1" repo="$2" pr_number="$3"
    local analysis_file="${RUNNER_TEMP}/failure-analysis-${owner}-${repo}-${pr_number}.json"
    local pr_commit_count=0
    # Global commit counter file shared across auto-fix and review handler
    local commit_counter_file="${RUNNER_TEMP}/pr-agent-commit-count.txt"
    local total_commits
    total_commits=$(cat "$commit_counter_file" 2>/dev/null || echo 0)

    # Clone with blobless filter for performance (OpenShift repos can be multi-GB)
    local workdir="${RUNNER_TEMP}/repo-${owner}-${repo}-${pr_number}"
    gh repo clone "${owner}/${repo}" "$workdir" -- --filter=blob:none --single-branch
    cd "$workdir"
    gh pr checkout "$pr_number"

    # Configure git identity for the bot (matches the GitHub App identity)
    git config user.name "openshift-app-platform-shift-bot"
    git config user.email "267347085+openshift-app-platform-shift-bot@users.noreply.github.com"
    git remote set-url origin "https://x-access-token:${GH_TOKEN}@github.com/${owner}/${repo}.git"

    while read -r fix; do
      local category=$(echo "$fix" | jq -r '.category')
      local files=$(echo "$fix" | jq -r '.affected_files[]')

      # Pre-fix blocklist check (fast guard on known affected files, category-aware for go.sum exception)
      if ! check_blocklist "$files" "$category"; then
        audit_log "blocked" "$category" "$files" "" "security-sensitive file"
        continue
      fi

      # Check global commit limits
      if [[ "$total_commits" -ge "${MAX_COMMITS_PER_RUN:-10}" ]]; then
        audit_log "skipped" "$category" "$files" "" "run commit limit reached"
        continue
      fi
      if [[ "$pr_commit_count" -ge "${MAX_COMMITS_PER_PR:-3}" ]]; then
        audit_log "skipped" "$category" "$files" "" "per-PR commit limit reached"
        continue
      fi

      # Apply the fix (framework-aware for generated files)
      # Determine PR base branch for scoping fixes to PR-changed files only
      local base_branch
      base_branch=$(gh pr view "$pr_number" --repo "${owner}/${repo}" --json baseRefName -q .baseRefName)
      git fetch origin "${base_branch}" --depth=1 2>/dev/null || true

      case "$category" in
        trivial-format)
          git diff --name-only HEAD "$(git merge-base HEAD "origin/${base_branch}")" -- '*.go' | xargs -r go fmt
          ;;
        trivial-import)  goimports -w $files ;;
        trivial-lint)    golangci-lint run --fix ./... 2>/dev/null || true ;;
        trivial-generated-files)
          if grep -q 'sigs.k8s.io/controller-runtime' go.mod; then
            make generate && make manifests
          elif grep -q 'github.com/openshift/library-go' go.mod; then
            make update
          else
            make generate 2>/dev/null || make update 2>/dev/null || true
          fi
          ;;
      esac

      # Verify fix compiles
      if ! go build ./... || ! go vet ./...; then
        git checkout -- .
        git clean -fd
        audit_log "reverted" "$category" "$files" "" "fix broke compilation"
        continue
      fi

      # Post-fix blocklist check (safety net — verify ACTUAL modified files)
      local modified_files
      modified_files=$(git diff --name-only; git ls-files --others --exclude-standard)
      if ! check_blocklist "$modified_files" "$category"; then
        git checkout -- .
        git clean -fd
        audit_log "reverted" "$category" "$modified_files" "" "post-fix blocklist violation"
        continue
      fi

      # Check diff size guard (count both insertions and deletions)
      local diff_lines
      diff_lines=$(git diff --numstat | awk '{s+=$1+$2} END {print s+0}')
      if [[ "$diff_lines" -gt 500 ]]; then
        git checkout -- .
        git clean -fd
        audit_log "reverted" "$category" "$files" "" "diff too large ($diff_lines lines)"
        continue
      fi

      if [[ "${DRY_RUN:-false}" == "true" ]]; then
        audit_log "dry-run" "$category" "$files" "" "would commit and push"
        git checkout -- .
        git clean -fd
        continue
      fi

      # Stage both modified tracked files AND new untracked files
      git diff --name-only -z | xargs -0 git add
      git ls-files --others --exclude-standard -z | xargs -0 git add
      git commit -m "fix: ${category} — auto-fix by oape-pr-agent"
      local sha=$(git rev-parse HEAD)

      # Pull before push to handle concurrent pushes to the same branch
      if ! git pull --rebase origin HEAD 2>/dev/null; then
        git rebase --abort 2>/dev/null || true
        audit_log "reverted" "$category" "$files" "$sha" "rebase conflict — concurrent push detected"
        git reset --hard HEAD~1
        continue
      fi
      git push origin HEAD
      pr_commit_count=$((pr_commit_count + 1))
      total_commits=$((total_commits + 1))
      echo "$total_commits" > "$commit_counter_file"

      audit_log "auto-fix" "$category" "$files" "$sha" "success"
    done < <(jq -c '.[] | select(.category | startswith("trivial-"))' "$analysis_file")
  }
  ```
- **GitHub App token for push:** The token generated via JWT signing from the Prow-mounted PEM key is set as `GH_TOKEN` and also used for git push via:
  ```bash
  git remote set-url origin "https://x-access-token:${GH_TOKEN}@github.com/${owner}/${repo}.git"
  ```
- **Reference:** `plugins/oape/commands/implement-review-fixes.md` for the fix-verify-commit pattern already used in OAPE. HyperShift's Jira Agent uses a similar clone → fix → push → PR flow.

### Files


| File                           | Action                                                    |
| ------------------------------ | --------------------------------------------------------- |
| `scripts/pr-agent/auto-fix.sh` | Create (standalone executable, called by `entrypoint.sh`) |


---

## Subtask 5: Implement review comment monitoring and response

### Description

Add the ability to fetch, analyze, and respond to review comments on PRs in allowed repos. Following HyperShift's Review Agent pattern, the agent identifies unresolved review threads that need attention, skips threads already addressed by the bot, and invokes Claude Code CLI to generate appropriate responses (code changes for actionable requests, explanations for questions). Bot-generated comments are filtered via `SKIP_USERS`.

### Acceptance Criteria

1. Fetches all review threads (inline and top-level) and review summaries from the PR.
2. Implements HyperShift-style comment analysis logic:
  - **Process**: No bot reply in thread (first response needed), or human replied after bot's last comment (follow-up needed).
  - **Skip**: Bot already replied with no human follow-up, thread is resolved, thread is outdated (code changed).
3. Filters out bot-generated comments using a configurable skip list (default: `openshift-ci`, `openshift-bot`, `dependabot`, `codecov`, `sonarcloud`, `coderabbitai[bot]`). Additional users configured via `SKIP_USERS` env var.
4. Skips known bot accounts (via `SKIP_USERS`). All other commenters are treated as legitimate reviewers — branch protection and repo permissions provide the authorization boundary.
5. Invokes Claude Code CLI to address each unresolved thread. Claude receives the full thread context and decides whether to make code changes or provide an explanation — no separate intent classification step. The safety skill content is included via `cat` in the prompt.
6. Pushes code changes (if any) and posts inline reply comments via `gh api`.
7. Respects the global commit counter shared with the auto-fix engine (via `$RUNNER_TEMP/pr-agent-commit-count.txt`). Increments the counter for each commit pushed.
8. Claude Code invocations use `--allowedTools` to exclude destructive git operations (no `git push --force`, `git push -f`, `git rebase`, `git reset --hard`).

### Dependencies

Subtask 1 (entrypoint must provide PR context).

### Implementation Hints

- **Thread analysis (deterministic bash):**
  ```bash
  analyze_review_threads() {
    local owner="$1" repo="$2" pr_number="$3"

    # Fetch review comments (inline)
    local comments
    comments=$(gh api "repos/${owner}/${repo}/pulls/${pr_number}/comments" \
      --paginate --jq '.')

    # Fetch review summaries
    local reviews
    reviews=$(gh api "repos/${owner}/${repo}/pulls/${pr_number}/reviews" \
      --paginate --jq '.')

    # Fetch top-level PR conversation comments (not inline on code)
    local issue_comments
    issue_comments=$(gh api "repos/${owner}/${repo}/issues/${pr_number}/comments" \
      --paginate --jq '.')

    # Group by thread (in_reply_to_id), determine if bot has replied
    # Filter: skip resolved, skip outdated, skip unauthorized authors
    # Output: list of threads needing attention
  }
  ```
- **Bot detection:**
  ```bash
  SKIP_USERS="${SKIP_USERS:-openshift-ci,openshift-bot,dependabot,codecov,sonarcloud,coderabbitai[bot]}"
  is_bot_or_skipped() {
    local login="$1" user_type="$2"
    [[ "$user_type" == "Bot" ]] && return 0
    echo "$SKIP_USERS" | tr ',' '\n' | grep -qx "$login" && return 0
    return 1
  }
  ```
- **Claude Code CLI invocation for review response:**
Claude receives the full thread context and decides whether to make code changes or provide an explanation. No separate `classify_thread_intent()` step — Claude handles this naturally based on the comment content.
  ```bash
  address_review_thread() {
    local owner="$1" repo="$2" pr_number="$3" thread_file="$4"
    local workdir="${RUNNER_TEMP}/repo-${owner}-${repo}-${pr_number}"

    cd "$workdir"

    # Check global commit limit before invoking Claude
    local commit_counter_file="${RUNNER_TEMP}/pr-agent-commit-count.txt"
    local total_commits
    total_commits=$(cat "$commit_counter_file" 2>/dev/null || echo 0)
    if [[ "$total_commits" -ge "${MAX_COMMITS_PER_RUN:-10}" ]]; then
      echo "[review] Skipping thread — commit limit reached"
      return 0
    fi

    # Include safety skill content and let Claude decide how to respond
    claude --print \
      --max-budget-usd "${MAX_BUDGET_PER_PR:-5.00}" \
      -p "$(cat plugins/oape/skills/pr-agent-safety/SKILL.md)

  Address the following review comment on PR #${pr_number} in ${owner}/${repo}.
  Review thread: $(cat "$thread_file")

  If the reviewer requests a code change (imperative language like 'change', 'fix', 'update',
  'remove', 'add'), make the change, verify it compiles (go build ./...), and commit.
  If the reviewer asks a question, reply with a concise explanation only — do NOT change code.
  One response per feedback — never respond via both inline reply AND general PR comment." \
      --allowedTools "Bash(git diff*),Bash(git add*),Bash(git commit*),Bash(git push origin HEAD),Bash(git log*),Bash(git status*),Bash(go*),Bash(make*),Bash(gh api*),Bash(gh pr comment*),Read,Write,Edit"

    # Update global commit counter if Claude pushed commits
    local new_commits
    new_commits=$(git rev-list --count HEAD ^"${HEAD_SHA_BEFORE}")
    if [[ "$new_commits" -gt 0 ]]; then
      total_commits=$((total_commits + new_commits))
      echo "$total_commits" > "$commit_counter_file"
    fi
  }
  ```
  > **Note:** The `--allowedTools` restriction explicitly excludes `git push --force`,
  > `git push -f`, `git rebase`, and `git reset --hard` — only safe git operations are
  > permitted. The existing `/oape:implement-review-fixes` command pattern is referenced
  > in the prompt for fix prioritization and verification patterns.
- **Reference:** HyperShift's Review Agent (`periodic-review-agent`) uses identical thread analysis logic. Their `/utils:address-reviews` command is the equivalent of this Claude Code invocation.

### Files


| File                                 | Action                                                    |
| ------------------------------------ | --------------------------------------------------------- |
| `scripts/pr-agent/review-handler.sh` | Create (standalone executable, called by `entrypoint.sh`) |


---

## Subtask 6: Wire together the PR processing pipeline

### Description

Create the `process_pr()` orchestration function in `entrypoint.sh` that wires together all capabilities from Subtasks 2–5 and 7–8 into the end-to-end processing pipeline. This subtask does NOT modify the workflow YAML files (those are fully specified in Subtask 0) — it only adds the function that sequences CI monitoring → failure analysis → auto-fix → review handling → status reporting for a single PR.

> **Phase 1 implementation:** `process_pr()` checks the `MONITOR_ONLY` environment variable (set by the `--monitor-only` CLI flag). When `MONITOR_ONLY=true`, the auto-fix phase is skipped entirely — the pipeline runs: merge conflict check → CI monitoring → failure classification → status report. This allows Phase 1 to validate CI monitoring and reporting without risk of pushing code changes.

### Acceptance Criteria

1. The `process_pr()` function invokes all phases in order: CI status check → failure log analysis → trivial auto-fix → review comment handling → status report.
2. Phases are conditional: failure analysis and auto-fix only run when CI status is `some-failed`; review comment handling only runs when there are unresolved threads.
3. Each phase logs its start/end with structured output: `[PR #N] Phase: <name> — started/completed`.
4. If a phase fails, it logs the error and continues to the next phase (best-effort processing).
5. The `run_periodic()` loop and `run_on_demand()` entry point call `process_pr()` for each PR.
6. Each PR is processed with a per-PR time limit (`PR_TIMEOUT_SECONDS`, default 720 = 12 minutes). On timeout, posts a partial status report and continues to the next PR, preventing one complex PR from starving subsequent PRs.

### Dependencies

Subtasks 1, 2, 3, 4, 5, 7, 8 (all capabilities must exist before they can be wired together).

### Implementation Hints

- **Periodic mode main loop** (in `entrypoint.sh`):
  ```bash
  run_periodic() {
    local max_prs="${PR_AGENT_MAX_PRS:-4}"
    local processed=0

    discover_oape_prs  # Populates $PR_LIST_FILE

    while IFS= read -r pr_url; do
      if [[ "$processed" -ge "$max_prs" ]]; then
        echo "[periodic] Reached max PRs ($max_prs), stopping"
        break
      fi

      echo "[periodic] Processing PR $((processed + 1))/${max_prs}: $pr_url"
      local pr_timeout="${PR_TIMEOUT_SECONDS:-720}"
      if timeout "$pr_timeout" bash -c "process_pr '$pr_url'"; then
        echo "[periodic] PR $pr_url — completed successfully"
      elif [[ $? -eq 124 ]]; then
        echo "[periodic] PR $pr_url — timed out after ${pr_timeout}s, posting partial report"
        parse_pr_url "$pr_url"
        scripts/pr-agent/report.sh --owner "$OWNER" --repo "$REPO" --pr-number "$PR_NUMBER" --partial
      else
        echo "[periodic] PR $pr_url — failed (continuing to next)"
      fi

      processed=$((processed + 1))

      # Rate limit between PRs
      if [[ "$processed" -lt "$max_prs" ]]; then
        echo "[periodic] Waiting 60s before next PR..."
        sleep 60
      fi
    done < "$PR_LIST_FILE"

    echo "[periodic] Processed $processed PRs"
  }
  ```
- **On-demand mode** (in `entrypoint.sh`):
  ```bash
  run_on_demand() {
    local pr_url="$1"
    echo "[on-demand] Processing single PR: $pr_url"
    process_pr "$pr_url"
    echo "[on-demand] Done"
  }
  ```
- **Process function** (invokes all phases via standalone scripts):
  ```bash
  process_pr() {
    local pr_url="$1"
    parse_pr_url "$pr_url"

    # Phase 0: Merge conflict check
    local mergeable
    mergeable=$(gh_retry gh pr view "$pr_url" --json mergeable -q .mergeable)
    if [[ "$mergeable" == "CONFLICTING" ]]; then
      echo "[PR #${PR_NUMBER}] Merge conflict detected — skipping to report"
      scripts/pr-agent/report.sh --owner "$OWNER" --repo "$REPO" \
        --pr-number "$PR_NUMBER" --merge-conflict
      return 0
    fi

    # Phase 1: CI Check Monitoring (standalone script — outputs aggregate status to stdout, saves details to JSON)
    local ci_status
    ci_status=$(scripts/pr-agent/ci-monitor.sh --owner "$OWNER" --repo "$REPO" --pr-number "$PR_NUMBER")
    echo "[CI] Status: $ci_status"

    # Phase 2: Failure Analysis + Auto-Fix (only if failures exist)
    if [[ "$ci_status" == "some-failed" ]]; then
      scripts/pr-agent/log-analyzer.sh --owner "$OWNER" --repo "$REPO" --pr-number "$PR_NUMBER"
      scripts/pr-agent/auto-fix.sh --owner "$OWNER" --repo "$REPO" --pr-number "$PR_NUMBER"
    fi

    # Phase 3: Review Comment Handling (standalone script)
    scripts/pr-agent/review-handler.sh --owner "$OWNER" --repo "$REPO" --pr-number "$PR_NUMBER"

    # Phase 4: Status Report (standalone script)
    scripts/pr-agent/report.sh --owner "$OWNER" --repo "$REPO" --pr-number "$PR_NUMBER"
  }
  ```
- **Reference:** HyperShift's `periodic-review-agent` runs every 3 hours and processes up to 10 PRs. The `address-review-comments` job is the on-demand equivalent. Both share setup steps and use the same processing logic.

### Files


| File                             | Action                                                                     |
| -------------------------------- | -------------------------------------------------------------------------- |
| `scripts/pr-agent/entrypoint.sh` | Modify (add `process_pr()`, `run_periodic()`, `run_on_demand()` functions) |


---

## Subtask 7: Implement safety guardrails and file-modification boundaries

### Description

Define and enforce safety boundaries for the autonomous agent. Since the agent can modify code and push to branches in a CI context, strong guardrails are essential to prevent accidental damage. This includes file blocklists, commit limits, diff size limits, force-push prevention, and a comprehensive audit log. A dedicated safety script encapsulates the guardrail functions, and a Claude Code skill documents the safety rules for the LLM's awareness.

### Acceptance Criteria

1. Maintains a blocklist of file patterns that are never auto-modified. Uses extension-aware patterns to protect actual secret storage files while allowing Go source files that operate on Kubernetes Secret/Token resources:
  - Secret storage files: `*.key`, `*.pem`, `*.crt`, `*.cert`, `*.p12`, `*.pfx`, `*.env`, `credentials.`*, `kubeconfig`
  - Container/CI files: `Dockerfile`, `Containerfile`, `.dockerignore`
  - Workflow/build files: `.github/workflows/*`, `.tekton/*`, `Makefile`
  - RBAC manifests: `**/rbac/*.yaml`, `**/clusterrole*.yaml`
  - Dependency files: `go.mod` (blocked by default, but **allowed for the `trivial-generated-files` category** since `make generate` legitimately modifies it via `go mod tidy`)
  - `go.sum` (blocked by default, but **allowed for the `trivial-generated-files` category** since `make generate` legitimately modifies it via `go mod tidy`)
2. Enforces commit limits: max 3 commits per PR processing, max 10 total commits across all PRs in a single run. Stops auto-fixing (but continues monitoring/reporting) when limits are reached.
3. Never executes `git push --force` or any destructive operation that modifies remote/shared history. Local rollback of unpushed agent commits (e.g., `git reset --hard HEAD~1` after a failed rebase, `git pull --rebase` to sync with concurrent pushes) is permitted as a recovery mechanism.
4. Logs every action to a structured audit log (JSON lines format) at `$RUNNER_TEMP/pr-agent-audit-<run-id>.jsonl` including: timestamp, PR URL, action type, affected files, commit SHA (if applicable), and outcome.
5. `DRY_RUN=true` mode executes the full analysis pipeline but skips all file modifications, commits, and pushes. Reports what *would* have been done.
6. Diff size guard: if an auto-fix produces more than 500 lines of changes, abort and report.
7. Audit log is available in Prow GCS artifacts at the end of every run.

### Dependencies

None — this is a standalone utility module. Its functions are consumed by Subtasks 4, 5, and 6 but it has no dependency on them.

### Implementation Hints

- **Blocklist check function (category-aware for go.sum exception):**
  ```bash
  # Protect actual secret storage files (not Go source files that operate on Secrets)
  BLOCKED_PATTERNS='\.(key|pem|crt|cert|p12|pfx)$|\.env$|credentials\.|(^|/)kubeconfig$'
  BLOCKED_PATTERNS+='|(^|/)Dockerfile$|(^|/)Containerfile$|\.dockerignore$'
  BLOCKED_PATTERNS+='|\.github/workflows|\.tekton/|(^|/)Makefile$'
  BLOCKED_PATTERNS+='|rbac/.*\.yaml|clusterrole.*\.yaml'
  BLOCKED_PATTERNS+='|go\.mod|go\.sum'
  # Same patterns but without go.mod and go.sum (allowed for trivial-generated-files)
  BLOCKED_PATTERNS_GENERATED='\.(key|pem|crt|cert|p12|pfx)$|\.env$|credentials\.|(^|/)kubeconfig$'
  BLOCKED_PATTERNS_GENERATED+='|(^|/)Dockerfile$|(^|/)Containerfile$|\.dockerignore$'
  BLOCKED_PATTERNS_GENERATED+='|\.github/workflows|\.tekton/|(^|/)Makefile$'
  BLOCKED_PATTERNS_GENERATED+='|rbac/.*\.yaml|clusterrole.*\.yaml'

  check_blocklist() {
    local files="$1"
    local category="${2:-}"
    local patterns="$BLOCKED_PATTERNS"
    # Allow go.mod and go.sum for trivial-generated-files (make generate legitimately modifies them via go mod tidy)
    if [[ "$category" == "trivial-generated-files" ]]; then
      patterns="$BLOCKED_PATTERNS_GENERATED"
    fi
    if echo "$files" | grep -iqE "$patterns"; then
      return 1  # blocked
    fi
    return 0  # safe
  }
  ```
- **Audit log function:**
  ```bash
  AUDIT_LOG="${RUNNER_TEMP}/pr-agent-audit-${GITHUB_RUN_ID:-local}.jsonl"

  audit_log() {
    local action="$1" category="$2" files="$3" commit="$4" outcome="$5"
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    printf '{"ts":"%s","pr":"%s","action":"%s","type":"%s","files":%s,"commit":"%s","outcome":"%s"}\n' \
      "$ts" "${CURRENT_PR_URL:-}" "$action" "$category" \
      "$(echo "$files" | jq -R 'split(" ")' 2>/dev/null || echo '[]')" \
      "$commit" "$outcome" \
      >> "$AUDIT_LOG"
  }
  ```
- **Commit counter (global across the run):**
  ```bash
  TOTAL_COMMITS=0
  MAX_COMMITS_PER_RUN="${MAX_COMMITS_PER_RUN:-10}"
  MAX_COMMITS_PER_PR=3

  check_commit_limit() {
    if [[ "$TOTAL_COMMITS" -ge "$MAX_COMMITS_PER_RUN" ]]; then
      echo "GUARDRAIL: Total commit limit reached ($TOTAL_COMMITS/$MAX_COMMITS_PER_RUN)"
      return 1
    fi
    return 0
  }
  ```
- **Diff size guard:**
  ```bash
  check_diff_size() {
    local max_lines="${MAX_DIFF_LINES:-500}"
    local changed_lines
    changed_lines=$(git diff --numstat | awk '{s+=$1+$2} END {print s+0}')
    if [[ "$changed_lines" -gt "$max_lines" ]]; then
      echo "GUARDRAIL: Diff too large ($changed_lines lines > $max_lines limit)"
      return 1
    fi
    return 0
  }
  ```
- **Skill for Claude's awareness:**
  ```markdown
  # Safety Guardrails for PR Agent

  When operating as the OAPE PR agent, you MUST follow these rules:
  1. NEVER modify files matching: [blocklist patterns]
  2. NEVER use git push --force, git rebase, or git reset --hard
  3. ALWAYS verify changes compile before committing
  4. STOP if diff exceeds 500 lines
  ```
- **Reference:** KNOWN-ISSUES.md documents "Unrestricted agent permissions" as a critical issue. HyperShift's agents enforce similar guardrails: "Cannot execute destructive operations — no ability to delete resources or force-push."

### Files


| File                                           | Action                                                                                                                      |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `scripts/pr-agent/safety.sh`                   | Create (sourced utility library, provides shared functions to entrypoint.sh, auto-fix.sh, review-handler.sh, and report.sh) |
| `plugins/oape/skills/pr-agent-safety/SKILL.md` | Create                                                                                                                      |


---

## Subtask 8: Implement status reporting and PR comment summary

### Description

Build the reporting layer that gives developers clear visibility into what the agent did. The agent produces a structured markdown report and posts it as a PR comment, so developers see results directly in the PR conversation. The report is also available in Prow GCS artifacts for archival. Following HyperShift's pattern, the report includes token/cost tracking data.

### Acceptance Criteria

1. Produces a markdown summary report containing all of the following sections:
  - **PR Status:** current state, branch, title, URL.
  - **Merge Conflict Status:** if the PR has merge conflicts, prominently flagged as the primary action item. When merge conflicts are detected, CI analysis and auto-fix sections are replaced with a message directing the developer to resolve conflicts first.
  - **CI Check Results:** table of all checks with pass/fail/pending status.
  - **Fixes Applied:** list of auto-fixes with commit SHA, fix type, and files changed.
  - **Review Comments Addressed:** summary of review threads handled.
  - **Infrastructure Flakes:** list of CI jobs classified as infrastructure flakes (timeouts, network errors, registry pull failures) with job names and links. Presented separately from code failures so developers can quickly identify retestable jobs.
  - **Remaining Issues:** items requiring manual intervention, separated into "auto-fixable but blocked by guardrails" vs. "requires human judgment."
  - **Run Summary:** total time elapsed, commit count. (Claude API costs are tracked at the GCP project billing level via Vertex AI, not per-invocation.)
2. Each auto-fix entry includes a clickable link to the commit on GitHub (`https://github.com/{owner}/{repo}/commit/{sha}`).
3. Report is posted as a PR comment via `gh pr comment`. If a previous agent comment exists, it is updated (not duplicated).
4. Report is saved to `$RUNNER_TEMP/pr-agent-report-<owner>-<repo>-<pr-number>.md` and available in Prow GCS artifacts.
5. When `DRY_RUN=true`, the report clearly states it was a dry run and no changes were made.
6. Before pushing any auto-fixes, posts an "in progress" comment (or updates the existing report comment with a "Processing..." header) so developers see context before surprise commits appear on the branch. The final report replaces this in-progress state.
7. On agent crash or failure, a `trap` handler posts a brief error note to the PR comment so developers know the agent attempted but failed.

### Dependencies

Subtasks 2–7 (aggregates data from all other capabilities).

### Implementation Hints

- **Report generation:**
  ```bash
  generate_status_report() {
    local owner="$1" repo="$2" pr_number="$3"
    local report_file="${RUNNER_TEMP}/pr-agent-report-${owner}-${repo}-${pr_number}.md"
    local audit_file="${RUNNER_TEMP}/pr-agent-audit-${GITHUB_RUN_ID:-local}.jsonl"
    local ci_file="${RUNNER_TEMP}/ci-status-${owner}-${repo}-${pr_number}.json"

    local pr_info
    pr_info=$(gh pr view "$pr_number" --repo "${owner}/${repo}" \
      --json title,url,headRefName,baseRefName -q '.')

    local title=$(echo "$pr_info" | jq -r '.title')
    local url=$(echo "$pr_info" | jq -r '.url')
    local head=$(echo "$pr_info" | jq -r '.headRefName')
    local base=$(echo "$pr_info" | jq -r '.baseRefName')

    local passed failed pending
    passed=$(jq '[.[] | select(.bucket == "pass")] | length' "$ci_file")
    failed=$(jq '[.[] | select(.bucket == "fail")] | length' "$ci_file")
    pending=$(jq '[.[] | select(.bucket == "pending")] | length' "$ci_file")

    local fixes_applied
    fixes_applied=$(grep '"action":"auto-fix"' "$audit_file" 2>/dev/null | wc -l || echo 0)

    cat > "$report_file" <<EOF
  ## PR Agent Report: ${repo}#${pr_number}

  **PR:** [${title}](${url})
  **Branch:** \`${head}\` → \`${base}\`
  **Run:** [${OAPE_RUN_URL:-Build #${BUILD_ID:-N/A}}](${OAPE_RUN_URL:-#})
  **Mode:** ${PR_AGENT_MODE:-periodic}
  ${DRY_RUN:+**DRY RUN — no changes were made**}

  ### CI Check Results
  | Status | Count |
  |--------|-------|
  | Passed | ${passed} |
  | Failed | ${failed} |
  | Pending | ${pending} |

  ### Fixes Applied
  $(grep '"action":"auto-fix"' "$audit_file" 2>/dev/null | jq -r '"- [\(.commit)](https://github.com/'${owner}'/'${repo}'/commit/\(.commit)) — `\(.type)`: \(.outcome)"' || echo "- (none)")

  ### Remaining Issues
  $(grep '"action":"blocked\|"action":"skipped"' "$audit_file" 2>/dev/null | jq -r '"- `\(.type)` on \(.files | join(", ")): \(.outcome)"' || echo "- (none)")

  ---
  *Generated by oape-pr-agent on $(date -u +"%Y-%m-%d %H:%M UTC")*
  EOF
  }
  ```
- **Post as PR comment (update if exists):**
  ```bash
  post_status_comment() {
    local owner="$1" repo="$2" pr_number="$3"
    local report_file="${RUNNER_TEMP}/pr-agent-report-${owner}-${repo}-${pr_number}.md"
    local marker="<!-- oape-pr-agent-report -->"

    # Check for existing agent comment and load persisted state
    local existing_comment_id existing_body
    existing_comment_id=$(gh api "repos/${owner}/${repo}/issues/${pr_number}/comments" \
      --jq ".[] | select(.body | contains(\"${marker}\")) | .id" | head -1)
    if [[ -n "$existing_comment_id" ]]; then
      existing_body=$(gh api "repos/${owner}/${repo}/issues/comments/${existing_comment_id}" --jq .body)
      # Extract and restore persisted state from previous run
      local persisted_state
      persisted_state=$(echo "$existing_body" | grep -oP '(?<=oape-pr-agent-state:)[A-Za-z0-9+/=]+' | head -1)
      if [[ -n "$persisted_state" ]]; then
        echo "$persisted_state" | base64 -d > "${RUNNER_TEMP}/pr-agent-state-${owner}-${repo}-${pr_number}.json"
      fi
    fi

    # Embed cross-run state in the comment for persistence across job runs
    local state_file="${RUNNER_TEMP}/pr-agent-state-${owner}-${repo}-${pr_number}.json"
    local state_block=""
    if [[ -f "$state_file" ]]; then
      local state_b64
      state_b64=$(base64 -w0 < "$state_file")
      state_block="<!-- oape-pr-agent-state:${state_b64} -->"
    fi

    local body="${marker}${state_block}
      $(cat "$report_file")"

    if [[ -n "$existing_comment_id" ]]; then
      gh api "repos/${owner}/${repo}/issues/comments/${existing_comment_id}" \
        -X PATCH -f body="$body"
    else
      gh pr comment "$pr_number" --repo "${owner}/${repo}" --body "$body"
    fi
  }
  ```
- **Reference:** HyperShift's Dependabot Triage Agent generates an HTML report with token usage and cost breakdown. The OAPE report follows the same principle but in markdown.

### Files


| File                         | Action                                                    |
| ---------------------------- | --------------------------------------------------------- |
| `scripts/pr-agent/report.sh` | Create (standalone executable, called by `entrypoint.sh`) |


---

## Subtask 9: PR agent testing and validation

### Description

Add automated testing for the PR agent itself. Since the agent autonomously pushes code to production repositories, it must be validated before deployment. This includes static analysis of bash scripts, a dry-run integration test against a known-state test PR, and a CI workflow that runs validation on every push to this repo.

### Acceptance Criteria

1. All bash scripts in `scripts/pr-agent/*.sh` pass **shellcheck** with zero errors and zero warnings.
2. A **dry-run integration test** script exists that:
  - Creates a test PR in a designated test repository (or uses a pre-existing test PR).
  - Runs the full agent pipeline in `DRY_RUN=true` mode.
  - Verifies: PR discovery finds the test PR, CI status is fetched, failure analysis produces valid JSON, report is generated (but not posted).
  - Exits with a non-zero status if any phase fails.
3. A **CI validation target** (Makefile or Prow presubmit) runs shellcheck on all `scripts/pr-agent/*.sh` and `scripts/ci-monitor/*.sh` files and executes the dry-run integration test.
4. Test scripts themselves follow shellcheck-clean conventions.

### Dependencies

Subtasks 0–8 (all agent components must exist before they can be tested).

### Implementation Hints

- **Shellcheck in CI:**
  ```yaml
  - name: Lint bash scripts
    run: |
      shellcheck scripts/pr-agent/*.sh
  ```
- **Dry-run integration test:**
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  # Run the full agent pipeline against a known test PR in dry-run mode
  export DRY_RUN=true
  export PR_AGENT_MAX_PRS=1

  # Use a pre-existing test PR (created once, kept open for testing)
  TEST_PR_URL="${TEST_PR_URL:-https://github.com/openshift-eng/oape-ai-e2e/pull/1}"

  scripts/pr-agent/entrypoint.sh --mode on-demand --pr-url "$TEST_PR_URL"

  # Verify outputs were generated (owner-repo-pr_number naming convention)
  [[ -f "${RUNNER_TEMP}/ci-status-openshift-eng-oape-ai-e2e-1.json" ]] || { echo "FAIL: CI status not generated"; exit 1; }
  [[ -f "${RUNNER_TEMP}/pr-agent-report-openshift-eng-oape-ai-e2e-1.md" ]] || { echo "FAIL: Report not generated"; exit 1; }

  echo "PASS: Dry-run integration test completed successfully"
  ```
### Files


| File                                  | Action |
| ------------------------------------- | ------ |
| `scripts/pr-agent/test-dry-run.sh`    | Create |


---

## Subtask 10: Create `/oape:pr-agent` command

### Description

Create a Claude Code command that serves as the **interactive/developer** entry point for the PR agent, following the pattern of all existing OAPE commands (`/oape:review`, `/oape:init`, etc.). This command is for developers running the agent locally in their terminal — the Prow presubmit invokes `monitor.sh` and `dispatch.sh` directly (deterministic, no Claude orchestration overhead). This separation ensures the CI path is fast and predictable, while the interactive path provides a richer developer experience.

### Acceptance Criteria

1. A command file exists at `plugins/oape/commands/pr-agent.md` following the project's command pattern (frontmatter with `description` and `argument-hint`, Synopsis, Description, Arguments, Implementation sections).
2. Accepts a PR URL as the primary argument.
3. Supports flags: `--dry-run` (no modifications), `--auto-fix` (default true).
4. Prompts the user before pushing fixes, displays status inline, and offers to monitor the PR on a schedule (via `CronCreate`).
5. Delegates to the same bash scripts (`ci-monitor.sh`, `auto-fix.sh`, etc.) used by the Prow presubmit, ensuring parity between interactive and CI execution.
6. The CLAUDE.md command table is updated to include `/oape:pr-agent`.
7. **Note:** The Prow presubmit invokes `monitor.sh` and `dispatch.sh` directly — it does NOT invoke this command. This command is for developer use only.

### Dependencies

Subtasks 1–8 (the command wraps all existing capabilities).

### Implementation Hints

- **Command frontmatter pattern** (follow `plugins/oape/commands/review.md`):
  ```markdown
  ---
  description: Monitor a PR, auto-fix trivial CI failures, address review comments, and report status
  argument-hint: <PR-URL> [--dry-run] [--auto-fix]
  ---
  ```
- **Interactive monitoring** uses `CronCreate` for periodic re-checks (like yolo-agent's interactive mode):
  ```
  After the initial pass, offer: "Would you like me to keep monitoring this PR?"
  If yes, schedule a one-shot CronCreate to re-run the analysis in 5 minutes.
  ```
- **Prow path is separate:** The Prow presubmit invokes `monitor.sh` and `dispatch.sh` directly — it does not use this command. This keeps the CI path deterministic and avoids Claude orchestration overhead.

### Files


| File                                | Action |
| ----------------------------------- | ------ |
| `plugins/oape/commands/pr-agent.md` | Create |


---

## Data Flow and Security

### Authentication


| System              | Method                                         | Details                                                                                                                                                                                        |
| ------------------- | ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GitHub (read/write) | GitHub App token with `GITHUB_TOKEN` fallback   | **Primary:** App token generated via JWT signing from PEM key at `/var/run/github-app/private-key.pem` (Prow secret: `openshift-app-platform-shift-github-bot`). Required for Phase 2+ pushes (avoids `GITHUB_TOKEN` anti-recursion). **Fallback:** If App is not installed on the target repo, uses `GITHUB_TOKEN` (sufficient for Phase 1 read + comment). |
| Claude API          | GCP Application Default Credentials (ADC) via Vertex AI | `CLAUDE_CODE_USE_VERTEX=1`, `ANTHROPIC_VERTEX_PROJECT_ID=itpc-gcp-hcm-pe-eng-claude`. ADC JSON mounted at `/var/run/gcloud-adc/application_default_credentials.json` (Prow secret: `oap-lts-claude-gcp-vertex-sa`). |
| GitHub (CI logs)    | Same GitHub App installation token             | Used via `gh` CLI for `gh api` calls and log fetching.                                                                                                                                          |
| Sippy API           | None (unauthenticated)                         | Public API at `sippy.dptools.openshift.org`. Used by `monitor.sh` to query flake history for test failures. No credentials required.                                                            |


### Data Retention

- No persistent storage beyond PR comments and Prow GCS artifacts.
- Audit logs and reports are written to the Prow job container's filesystem and available in GCS build artifacts for the job's retention period (standard OpenShift CI retention).
- No secrets are logged — the audit log contains only file paths, commit SHAs, and action outcomes.

### Data Flow: Prow Presubmit CI Monitor

```
  Developer pushes to PR branch
    → Prow triggers oape-ci-monitor presubmit (alongside other CI jobs)
    → Also triggerable manually via: /test oape-ci-monitor

┌─────────────────────────────────────────────────────────────────┐
│  Prow Pod (ci-monitor-agent container, ci-operator managed)     │
│                                                                 │
│  ┌───────────┐    ┌───────────────┐    ┌─────────────────────┐ │
│  │ Generate  │───▶│ monitor.sh    │───▶│ dispatch.sh          │ │
│  │ GitHub    │    │ (poll checks, │    │ (log trigger actions,│ │
│  │ App token │    │  collect GCS  │    │  Phase 2+: invoke    │ │
│  │ from PEM  │    │  artifacts,   │    │  auto-fix/Claude)    │ │
│  └───────────┘    │  classify,    │    └─────────────────────┘ │
│                    │  sippy query, │                             │
│                    │  post report) │                             │
│                    └───────────────┘                             │
└─────────────────────────────────────────────────────────────────┘
        │                    │                    │
        ▼                    ▼                    ▼
   ┌─────────┐      ┌──────────────┐     ┌──────────────┐
   │ GitHub  │      │ Claude API   │     │ Sippy API    │
   │ API     │      │ (Vertex AI)  │     │ (flake       │
   │ (PRs,   │      │ (Phase 2+    │     │  history)    │
   │  checks,│      │  only)       │     │              │
   │  comment)│     │              │     │              │
   └─────────┘      └──────────────┘     └──────────────┘
```

---

## Configuration


| Variable              | Default                                                                      | Description                                                                                                              |
| --------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `BOT_USER`            | `openshift-app-platform-shift-bot`                                           | Git identity used for bot commits (matches the GitHub App). Also used to detect bot's own replies in review threads (skip re-responding to self). |
| `PR_AGENT_MAX_PRS`    | `4`                                                                          | Maximum PRs to process per periodic run (kept low to stay within the Prow job timeout)                                   |
| `MAX_BUDGET_PER_PR`   | `5.00`                                                                       | Maximum dollar amount to spend on Claude API per PR (passed to `--max-budget-usd`)                                       |
| `MAX_COMMITS_PER_RUN` | `10`                                                                         | Maximum total commits across all PRs in a single run                                                                     |
| `MAX_COMMITS_PER_PR`  | `3`                                                                          | Maximum commits per individual PR processing                                                                             |
| `MAX_DIFF_LINES`      | `500`                                                                        | Maximum lines changed by a single auto-fix before aborting                                                               |
| `PR_TIMEOUT_SECONDS`  | `720`                                                                        | Maximum seconds to spend processing a single PR (12 min). On timeout, posts partial report and continues.                |
| `DRY_RUN`             | `false`                                                                      | When `true`, skips all file modifications, commits, and pushes                                                           |
| `SKIP_USERS`          | `openshift-ci,openshift-bot,dependabot,codecov,sonarcloud,coderabbitai[bot]` | Comma-separated list of users whose comments are skipped                                                                 |
| `RATE_LIMIT_SECONDS`  | `60`                                                                         | Delay between processing PRs in periodic mode                                                                            |
| `RETEST_INFRA_FLAKES` | `false`                                                                      | (Phase 2) When `true`, posts targeted `/test <job-name>` for infrastructure flakes. Max 2 retests per job per run.       |
| `GCSWEB_BASE_URL`     | `https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com`                        | Base URL for OpenShift CI gcsweb (Prow log fetching). Update if CI infrastructure migrates.                              |


### Required Prow Secrets


| Secret (in `test-credentials` namespace)           | Mount Path                | Purpose                                                                                      |
| -------------------------------------------------- | ------------------------- | -------------------------------------------------------------------------------------------- |
| `oap-lts-claude-gcp-vertex-sa`                     | `/var/run/gcloud-adc/`    | GCP ADC JSON for Vertex AI Claude access (`application_default_credentials.json`)            |
| `openshift-app-platform-shift-github-bot`          | `/var/run/github-app/`    | GitHub App ID (`app-id` key) and private key PEM (`private-key.pem` key) for generating installation tokens |


---

## Limitations

- **AI may produce incorrect or incomplete solutions** — all fixes pushed by the agent must be reviewed by repository OWNERS before merging.
- **Complex issues may not be fully addressed** — multi-faceted build errors, test failures, and architectural issues require human intervention.
- **Rate limited**: 4 PRs per periodic run (configurable via `PR_AGENT_MAX_PRS`), 100 agentic turns per PR.
- **Cannot access private resources** — no access to internal systems beyond GitHub and Jira.
- **Cannot execute destructive operations** — no ability to force-push, rebase, or delete branches. Enforced via `--allowedTools` restrictions on Claude CLI invocations.
- **Concurrent processing race** — multiple Prow presubmit runs for the same PR (e.g., after rapid pushes) could process simultaneously. The consequence is duplicate work (not data loss): both runs may analyze the same failures and attempt the same fixes, with the second push either succeeding (identical fix) or gracefully failing (conflict detected by `git pull --rebase`). State persistence uses last-writer-wins, which may cause already-addressed comments to be re-analyzed on the next run.
- **Prow job timeout** — presubmit timeout is `2h30m0s`, providing ample time for CI polling plus analysis. The GitHub App installation token generated at job start is valid for 1 hour, which is sufficient for single-PR presubmit processing.
- **No periodic sweep** — Phase 1 is purely presubmit-driven. There is no periodic scanner catching PRs that were missed. If the presubmit is not configured for a repo, no monitoring occurs for that repo's PRs.
- **Cost** — deterministic classification handles ~80-90% of cases without Claude API cost. Claude Code is invoked only for `unknown` failures and review comment handling.
- **Target repo ci-operator config** — the `oape-ci-monitor` presubmit must be added to each target repo's ci-operator config in `openshift/release`. Requires a PR to `openshift/release` approved by the repo's CI admins.

---

## Monitoring and Effectiveness

### Performance Monitoring

- **Prow job logs**: View at `https://prow.ci.openshift.org` → search for `oape-ci-monitor` job for the target repo.
- **GCS artifacts**: Build logs and artifacts stored in GCS buckets accessible via gcsweb (e.g., `gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com`).
- **PR comments**: The CI monitor report is posted directly to the PR, providing immediate visibility without navigating CI systems.
- Track job success/failure rates via Prow job history.

### Metrics and Indicators


| Metric                   | Description                                          |
| ------------------------ | ---------------------------------------------------- |
| PRs processed per run    | Number of PRs successfully analyzed per periodic run |
| Auto-fixes applied       | Count of trivial CI failures automatically resolved  |
| Review threads addressed | Count of review comments handled by the agent        |
| Fix success rate         | Percentage of auto-fixes that pass subsequent CI     |
| Time to CI-green         | Duration from PR creation to all checks passing      |


### Periodic Review Process

The OAPE team should conduct monthly reviews:

- Review auto-fix commits for quality and correctness.
- Track false positives (agent applied a fix that was wrong) and false negatives (agent missed a trivial fix).
- Adjust classification heuristics in the `ci-failure-analysis` skill based on results.
- Monitor Claude API costs and adjust `MAX_BUDGET_PER_PR` if needed.
- Review safety guardrail effectiveness — are blocked patterns correct? Are commit limits appropriate?

---

## Summary


| #   | Subtask                                                                                                         | Type                   | Effort Estimate |
| --- | --------------------------------------------------------------------------------------------------------------- | ---------------------- | --------------- |
| 0   | Prow presubmit infrastructure + ci-operator config                                                              | Prow ci-operator Config | Medium          |
| 1   | Create entrypoint script with PR discovery, prechecks, merge conflict detection, skip label, and state tracking | Script                 | Medium          |
| 2   | Implement CI check monitoring via `gh pr checks`                                                                | Script                 | Medium          |
| 3   | Implement CI failure log analysis with deterministic classification + Claude fallback                           | Script + Skill         | Large           |
| 4   | Implement trivial auto-fix engine with blobless clone, pre/post blocklist, global commit counter                | Script                 | Large           |
| 5   | Implement review comment monitoring and response with `--allowedTools` restrictions                             | Script + Claude Code   | Medium          |
| 6   | Wire together the PR processing pipeline (standalone scripts)                                                   | Script                 | Small           |
| 7   | Implement safety guardrails and file-modification boundaries (sourced utility library, no dependencies)         | Script + Skill         | Medium          |
| 8   | Implement status reporting with merge conflict section and PR comment summary                                   | Script                 | Medium          |
| 9   | PR agent testing and validation                                                                                 | Script                 | Small           |
| 10  | Create `/oape:pr-agent` command                                                                                 | Command                | Small           |


### Files Created


| File                                               | Purpose                                                                                                               |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `docs/prow-ci-operator-config.yaml`                | Reference ci-operator config for adding `oape-ci-monitor` presubmit to target repos in `openshift/release`            |
| `scripts/pr-agent/entrypoint.sh`                   | Main orchestration script (both modes, merge conflict check, skip label filter, state tracking)                       |
| `scripts/pr-agent/ci-monitor.sh`                   | CI check fetching via `gh pr checks` and status aggregation (standalone executable)                                   |
| `scripts/pr-agent/log-analyzer.sh`                 | Log fetching + deterministic classification + Claude fallback for unknowns (standalone executable)                    |
| `scripts/pr-agent/auto-fix.sh`                     | Trivial fix application with blobless clone, pre/post blocklist checks, global commit counter (standalone executable) |
| `scripts/pr-agent/review-handler.sh`               | Review comment analysis and Claude Code invocation with `--allowedTools` restrictions (standalone executable)         |
| `scripts/pr-agent/safety.sh`                       | Blocklists (category-aware go.sum exception), commit limits, audit logging, retry helper (sourced utility library)    |
| `scripts/pr-agent/report.sh`                       | Status report generation with merge conflict section and PR comment posting (standalone executable)                   |
| `scripts/pr-agent/test-dry-run.sh`                 | Dry-run integration test script                                                                                       |
| `plugins/oape/skills/ci-failure-analysis/SKILL.md` | Claude Code skill for failure classification (content included via `cat` in prompts)                                  |
| `plugins/oape/skills/pr-agent-safety/SKILL.md`     | Safety guardrails skill (content included via `cat` in prompts)                                                       |
| `plugins/oape/commands/pr-agent.md`                | `/oape:pr-agent` command for interactive developer use (Prow presubmit uses `monitor.sh`/`dispatch.sh` directly)      |


### User Guide

#### Viewing Agent Output

Track PRs processed by the agent:

- **Prow job logs**: Navigate to Prow CI dashboard (`prow.ci.openshift.org`), filter by job name `oape-ci-monitor`
- **Agent comments**: Look for comments containing `<!-- oape-ci-monitor -->` on PRs in configured repos
- **GCS artifacts**: Available via gcsweb for the `oape-ci-monitor` job run

#### Triggering On-Demand

The agent triggers automatically as a Prow presubmit on every PR push, or manually:

1. **Automatic (primary)**: The `oape-ci-monitor` presubmit runs automatically on every PR push in configured repos
2. **Via Prow chatops**: Comment `/test oape-ci-monitor` on the PR

#### Skipping a PR

To exclude a PR from automated processing, add the `pr-agent:skip` label. The presubmit will skip PRs with this label.

#### Reprocessing

The agent maintains lightweight state across runs via the PR report comment (tracking which CI jobs have been analyzed and which review comments have been addressed). On each run, already-processed items are skipped to avoid duplicate work. To force a full reprocessing of a PR, delete the agent's report comment (containing `<!-- oape-ci-monitor -->`) from the PR, then trigger another run via `/test oape-ci-monitor`.

---

## Implementation Phasing

The subtasks above describe the full target architecture. Implementation is phased to deliver value incrementally and validate the approach before investing in the full design.

### Phase 1: MVP — Prow Presubmit CI Monitor (Report-Only)

**Goal**: Prove the concept by adding a Prow presubmit job to target repos that monitors CI and reports failures. The presubmit runs alongside other CI jobs, polls until all other checks reach a terminal state, then classifies failures and posts a structured report. `dispatch.sh` then logs planned next-step actions (no-op in Phase 1, real invocations in Phase 2+). No auto-fix, no review comment handling, no Claude dependency.

**Architecture**: Each target repo's ci-operator config in `openshift/release` gains three additions from `docs/prow-ci-operator-config.yaml`: (1) an inline `ci-monitor-agent` image build, (2) a promotion exclusion, and (3) an `oape-ci-monitor` presubmit test. The presubmit builds the container, generates a GitHub App token from the mounted PEM key, and runs the analysis scripts.

```
PR push → Prow triggers oape-ci-monitor presubmit
  → Build ci-monitor-agent container (inline Dockerfile)
  → Generate GitHub App token from mounted PEM
  → monitor.sh: polls gh pr checks → collects GCS artifacts → classifies → Sippy → report → result JSON
  → dispatch.sh: reads result JSON → logs planned actions (Phase 1) / invokes auto-fix, Claude, /retest (Phase 2+)
```

| File                                        | Purpose                                                                                                                                         | Maps to Subtasks  |
| ------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| `scripts/ci-monitor/monitor.sh`             | CI monitor: polls checks, collects GCS artifacts, classifies failures, queries Sippy, generates report, posts comment, writes result JSON        | 2, 3 (partial)    |
| `scripts/ci-monitor/dispatch.sh`            | Failure dispatch: reads result JSON, logs planned actions (Phase 1), invokes further oape-ai-e2e tools on failure (Phase 2+)                     | 6 (partial)       |
| `docs/prow-ci-operator-config.yaml`         | Reference ci-operator config template for adding `oape-ci-monitor` presubmit to target repos in `openshift/release`                             | 0 (partial)       |

**Validation**: The pipeline has been validated end-to-end via Prow rehearsal on [openshift/release#80727](https://github.com/openshift/release/pull/80727). The rehearsal detects the `openshift/release` context, switches to a real open PR on `openshift/must-gather-operator`, and runs the full pipeline including posting the analysis comment — allowing validation without merging the release PR first.

**Scope**: Report-only CI monitoring via a Prow presubmit in each target repo. First target repo: must-gather-operator. The presubmit runs as `always_run: true, optional: true` and polls until all other checks are terminal. Once complete, it runs `monitor.sh` which fetches `gh pr checks`, collects `build-log.txt` from GCS for failed Prow jobs, classifies failures into categories (`install-failure`, `test-failure`, `build-failure`, `lint-failure`, `infra-flake`, `unknown`), queries Sippy for flake history, and posts a structured markdown report on the PR. A machine-readable JSON result (`ci-monitor-result.json`) includes suggested trigger actions (retest, auto-fix-lint, investigate). `dispatch.sh` reads this result and logs planned actions — in Phase 1 these are no-ops, in Phase 2+ they become real invocations of oape-ai-e2e tools.

**Phase 1 enhancements (from PR #60 analysis):**
- **Release repo discovery**: `monitor.sh` fetches the ci-operator config from `openshift/release` for the target repo/branch, providing authoritative job metadata (required/optional, cluster_profile, OCP release version). Falls back to name-based heuristics if unavailable.
- **Non-test context exclusions**: Filters out non-CI contexts (`tide`, `Mergeable`, `DCO`, `CodeRabbit`, `stale`, `sonarcloud`, `codecov`) that should never be counted as failures.
- **Expanded failure patterns**: Infra-flake detection includes `registry.ci.openshift.org` errors, `etcdserver` timeouts, lease failures, cloud quota errors, `dial tcp` timeouts. Install-failure detection includes `level=fatal.*installer`, `bootstrapComplete` waits.
- **Dynamic Sippy release version**: Resolves OCP version from ci-operator config (`releases.latest.release.version`) or Prow job name pattern, providing accurate flake data per release.
- **Prow Job Breakdown table**: Report includes a table of ALL checks (pass/fail) with state, category, required/optional status, flake%, and recommended action.

**Retained for future phases**: The PR agent scripts (`scripts/pr-agent/entrypoint.sh`, `safety.sh`) are retained as the foundation for `dispatch.sh` to invoke in Phase 2+. Since the `ci-monitor-agent` container includes all oape-ai-e2e scripts and plugins at build time, all tools are available at runtime.

### Phase 2: Auto-Fix + Claude Intelligence

**Goal**: Add CI-triggered auto-fix for trivial failures, Claude-powered analysis for unknown failures, and auto-retest for confirmed flakes. The CI monitor's `trigger_actions` output from Phase 1 drives dispatch. Incorporate context-aware analysis patterns from PR #60's ci-monitor skill.

| File                                               | Purpose                                                                                                 | Maps to Subtasks |
| -------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | ---------------- |
| `scripts/pr-agent/auto-fix.sh`                     | Extracted auto-fix engine: `go fmt`, `goimports`, `make generate`, scoped to PR-changed files           | 4                |
| `scripts/pr-agent/log-analyzer.sh`                 | Deterministic + Claude fallback classification for `unknown` failures                                   | 3                |
| `plugins/oape/skills/ci-failure-analysis/SKILL.md` | Claude skill for unknown failure classification (reference: PR #60's `plugins/oape/skills/ci-monitor/SKILL.md`) | 3                |
| `scripts/pr-agent/safety.sh`                       | Retained guardrails: blocklist, audit log, commit limits, diff size guard                               | 7                |

**Scope adds**: Auto-fix for `lint-failure` and `build-failure` categories, auto-retest (`/retest`) for `infra-flake`, Claude Code CLI fallback for `unknown` failures. `dispatch.sh` (already invoked by the Prow presubmit after `monitor.sh`) reads `ci-monitor-result.json` and takes action — no separate dispatch workflow needed.

**Learnings from PR #60 to incorporate in Phase 2:**

- **Auto-retest protocol**: When ALL failures on a PR are infra-flake (Mode E), `dispatch.sh` posts `/retest` automatically (max 2 per session). Only triggers when every failure is infrastructure-related. Disable with `--no-auto-retest`. Each auto-retest is logged in the report with timestamp, affected contexts, and outcome.

- **On-demand PR diff fetch**: When a build/test failure references a specific file, fetch the diff for that file only (`gh pr diff $PR -- $FILE`) to correlate the error with the actual code change. Only fetch for files in `PR_CHANGED_FILES` — if the error is in a file not changed by the PR, flag it as a dependency or generated-code issue.

- **Error signature hashing**: Normalize error messages (strip timestamps, line numbers, hex addresses `0x[a-f0-9]+`, UUIDs `[a-f0-9-]{36}`, temp paths `/tmp/[^ ]+`), then SHA-256 hash. Track `context_name -> error_hash` per fix round. If >= 75% of failed contexts share the same hash as the previous round, the fix was ineffective — stop the fix loop.

- **Root cause tracing protocol**: Step-by-step diagnostic decision tree for each failure:
  1. Does the error reference a specific file? Is it in `PR_CHANGED_FILES`? → PR likely introduced the issue.
  2. Is it about a missing tool, command, or image? → Check ci-operator config's `container.from` or step `from:` image.
  3. Is it about authentication/credentials? → Check ci-operator `credentials` entries (Vault-injected, declared in `openshift/release`).
  4. Is it transient/environmental (network, quota, lease)? → Recommend `/retest`.
  5. Is it about missing generated code (`zz_generated.deepcopy.go`, CRD YAML)? → Check if `_types.go` changed but generated files weren't updated.
  6. None of the above → Report with all available evidence, confidence: low.
  Each step cites concrete artifacts (log line, file path, config entry). Output format: numbered trace steps, fix location, fix owner, confidence level.

- **PR change context**: Fetch changed files list per PR (`gh pr view --json files`). Classify change types: API (`_types.go`), controller (`controller|reconcil*.go`), test (`_test.go`), CRD (`crd/*.yaml`), RBAC (`rbac*.yaml`). Used for error-to-file correlation and stage-aware summary.

- **Operator repo context**: Detect operator framework from `go.mod` (`sigs.k8s.io/controller-runtime` vs `github.com/openshift/library-go`). Detect Makefile presence, test directories. Used for targeted fix suggestions and local verification commands.

- **Step registry resolution**: For failed Prow jobs with multi-stage steps, resolve step refs from `openshift/release` step registry (`ci-operator/step-registry/`). Maps "e2e-aws failed" to "step `openshift-e2e-test` failed, running `openshift-tests run openshift/conformance/parallel`". Resolved on demand only for failed jobs (saves API calls).

- **Optional job severity**: Jobs marked `optional: true` in ci-operator config should never be labeled as "Blocker" or "Critical". Label as "Non-blocking (optional)" regardless of failure mode. Optional job failures should not change the PR's overall verdict from PASS to FAIL.

### Phase 3: Review Comments + Full Design

**Goal**: Complete the target architecture with review comment handling, the `/oape:pr-agent` command, and rollout to all target repos.

| File                                           | Purpose                                                                                    | Maps to Subtasks |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------ | ---------------- |
| `scripts/pr-agent/review-handler.sh`           | Review comment monitoring/response with restricted `--allowedTools`                         | 5                |
| `scripts/pr-agent/report.sh`                   | Extracted reporting logic (unified for CI monitor + PR agent)                               | 8                |
| `plugins/oape/skills/pr-agent-safety/SKILL.md` | Safety rules skill for Claude                                                              | 7                |
| `plugins/oape/commands/pr-agent.md`            | `/oape:pr-agent` command for interactive + headless use                                    | 10               |

**Scope adds**: Review comment handling, `/oape:pr-agent` command, rollout of `oape-ci-monitor` presubmit to all repos in `team-repos.csv` (by adding ci-operator config snippets to each repo's config in `openshift/release`), full test suite.

---

## Support and Feedback

- **Slack channel**: #oape-support
- **Feedback**: File issues with label `pr-agent-feedback`
- **Urgent issues**: Contact OAPE team directly

