---
description: Monitor CI/Prow job status for one or more PRs with adaptive polling, SHA-tracking, and optional fix-push-rewatch loop
argument-hint: <pr1-url-or-number> [pr2-url-or-number] [pr3-url-or-number] [--repo <owner/name>] [--timeout-min <n>] [--max-fix-rounds <n>] [--fast] [--no-auto-retest]
---

## Name
oape:ci-monitor

## Synopsis
```shell
# Monitor all three staged PRs (autonomous workflow mode)
/oape:ci-monitor https://github.com/org/repo/pull/101 https://github.com/org/repo/pull/102 https://github.com/org/repo/pull/103 --timeout-min 120 --max-fix-rounds 2

# Monitor one PR in current repo
/oape:ci-monitor 101

# Monitor OpenShift Prow jobs for a specific PR
/oape:ci-monitor https://github.com/openshift/must-gather-operator/pull/342

# Report-only mode (no auto-fix loop)
/oape:ci-monitor 342 --repo openshift/must-gather-operator --max-fix-rounds 0

# Fast mode — skip deep artifact analysis
/oape:ci-monitor https://github.com/openshift/must-gather-operator/pull/342 --fast
```

## Description
The `oape:ci-monitor` command watches GitHub CI checks **and** OpenShift Prow status contexts for one or more pull requests using **adaptive polling intervals**, waits until they finish (or timeout), then performs deep failure analysis. When running in agent mode with `--max-fix-rounds > 0`, it can apply fixes, push, and re-watch CI automatically.

This command is designed for the staged OAPE workflow (PR #1 API, PR #2 implementation, PR #3 e2e), but works with any PR list.

### Key Capabilities

- **Context-aware**: Fetches the ci-operator config from `openshift/release` to classify jobs authoritatively (required/optional, fast/slow, cloud provider), resolve step registry references for step-level failure mapping, and auto-detect the OCP release version for Sippy queries.
- **Adaptive polling**: Polls at 60s for fast jobs (lint/unit/verify), backs off to 120s during cluster provisioning (saves ~44% API calls), and tightens to 60s when slow jobs approach completion.
- **SHA-anchored**: Tracks the PR head SHA on every poll. When a new commit is pushed, all stale results are discarded and polling restarts after a 90s settle period.
- **Retest-aware**: Detects `/retest` and `/test <job>` (no SHA change) by comparing `started_at` timestamps. If a terminal context reappears as pending or has a newer timestamp, it is treated as restarted.
- **Fix-push-rewatch loop**: In agent mode, can apply a fix, push, detect the SHA change, and re-poll CI automatically (up to `max-fix-rounds` times). Uses error signature hashing to deterministically detect when a fix was ineffective.
- **Prow-native**: Treats `ci/prow/*` commit-status contexts as first-class signals alongside GitHub Actions checks.
- **Artifact collection**: Downloads `build-log.txt`, `finished.json`, `junit*.xml`, and step-level logs from GCS for each failed Prow job.
- **Failure-mode routing**: Classifies each failure as install failure, test failure, lint/build failure, boilerplate/tooling failure, or infra flake using multi-signal detection (JUnit patterns, build-log regex, `finished.json` fields, and ci-operator step metadata).
- **Flake detection**: Cross-references test names against Sippy for historical pass rates and known open bugs, using the OCP release version resolved from the ci-operator config.
- **Auto-retest**: When all failures on a PR are infra flakes (Mode E), automatically posts `/retest` as a PR comment (max 2 per session). Disable with `--no-auto-retest`.
- **Progress reporting**: Emits a one-line status after every poll cycle and a detailed milestone summary every 10 minutes during long monitoring sessions.
- **Parallel PR polling**: When monitoring multiple PRs, polls all PRs in each cycle (not sequentially), skipping PRs that have already completed.
- **Stage-aware summary**: When three PRs are provided, correlates failures across API / implementation / e2e stages.

### API Budget

Each poll iteration costs **3 GitHub API calls per active PR** (SHA check + statusCheckRollup + commit status). One-time setup costs are incurred before polling starts. GCS artifact downloads and Sippy queries are free (separate services).

**One-time setup calls** (before polling):
- Release context: 2-5 calls (ci-operator config + step registry refs)
- Operator repo context (GitHub strategy): 2-3 calls (go.mod + Makefile + tree)
- PR change context: 1 call per PR (changed file list)
- Auto-retest comments: 1 call per `/retest` posted (max 2 per session)

| Scenario | Polling calls | Setup calls | Total | Budget usage |
|---|---|---|---|---|
| 1 PR, lint/unit only (30 min) | ~90 | ~8 | ~98 | 2.0% of 5,000/hr |
| 1 PR, e2e + cluster install (120 min) | ~240 | ~8 | ~248 | 5.0% |
| 3 PRs, e2e + cluster, 2 fix rounds | ~1,800 | ~14 | ~1,814 | 36% |

## Arguments

- Positional args (`$1`, `$2`, `$3`): PR references. Each value may be:
  - PR number (for example: `123`)
  - Full PR URL (for example: `https://github.com/org/repo/pull/123`)
- `--repo <owner/name>` (optional): repository override. If omitted, infer from PR URL or `git remote origin`.
- `--timeout-min <n>` (optional): maximum wait time per monitoring round. Default: `120`. Auto-adjusts down if no e2e/cluster jobs are detected.
- `--max-fix-rounds <n>` (optional): max push-and-rewatch cycles. Default: `2`. Set to `0` for report-only mode (no auto-fix loop).
- `--sha-settle-sec <n>` (optional): seconds to wait after detecting a SHA change before resuming polls. Default: `90`.
- `--fast` (optional): skip deep artifact downloads (must-gather, full junit parsing). Produces a faster but shallower report.
- `--no-auto-retest` (optional): disable automatic `/retest` posting for infra flakes. By default, when all failures on a PR are infra flakes (Mode E), the agent posts `/retest` automatically (max 2 per session).

## Implementation

### Phase 0: Prechecks

All prechecks must pass before polling CI. If ANY precheck fails, STOP immediately and report the failure.

#### Precheck 1 — Validate Inputs

At least one PR reference must be provided.

```bash
if [ -z "$ARGUMENTS" ]; then
  echo "PRECHECK FAILED: Missing PR reference."
  echo "Usage: /oape:ci-monitor <pr1-url-or-number> [pr2-url-or-number] [pr3-url-or-number]"
  exit 1
fi
```

#### Precheck 2 — Verify Required Tools

```bash
MISSING_TOOLS=""
command -v gh >/dev/null 2>&1 || MISSING_TOOLS="$MISSING_TOOLS gh"
command -v jq >/dev/null 2>&1 || MISSING_TOOLS="$MISSING_TOOLS jq"
command -v git >/dev/null 2>&1 || MISSING_TOOLS="$MISSING_TOOLS git"
command -v curl >/dev/null 2>&1 || MISSING_TOOLS="$MISSING_TOOLS curl"

if [ -n "$MISSING_TOOLS" ]; then
  echo "PRECHECK FAILED: Missing required tools:$MISSING_TOOLS"
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "PRECHECK FAILED: GitHub CLI is not authenticated."
  echo "Run: gh auth login"
  exit 1
fi
```

#### Precheck 3 — Resolve Repository and PR Numbers

1. Parse flags (`--repo`, `--timeout-min`, `--max-fix-rounds`, `--sha-settle-sec`, `--fast`, `--no-auto-retest`).
2. Resolve repository:
   - First from `--repo`.
   - Else from PR URL (`github.com/<owner>/<repo>/pull/<n>`).
   - Else from `git remote origin`.
3. Resolve each PR reference to an integer PR number.
4. Validate each PR is accessible:

```bash
gh pr view "$PR_NUMBER" --repo "$REPO" --json number,title,url,state
```

If any PR cannot be resolved or accessed, fail immediately.

#### Precheck 4 — Gather Operator Repo Context

Gather context about the operator repository. Try the local clone first (faster, no API calls). If not available, fetch from the PR's GitHub repo via `gh api`. This is non-blocking -- if both fail, the skill falls back gracefully.

```bash
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
OPERATOR_CONTEXT_SOURCE="none"

# Strategy 1: Local clone (free, no API calls)
if [ -n "$REPO_ROOT" ] && [ -f "$REPO_ROOT/go.mod" ]; then
    GO_MODULE=$(head -1 "$REPO_ROOT/go.mod" | awk '{print $2}')

    HAS_CR=false; HAS_LIBGO=false
    grep -q "sigs.k8s.io/controller-runtime" "$REPO_ROOT/go.mod" && HAS_CR=true
    grep -q "github.com/openshift/library-go" "$REPO_ROOT/go.mod" && HAS_LIBGO=true

    if [ "$HAS_CR" = true ]; then FRAMEWORK="controller-runtime"
    elif [ "$HAS_LIBGO" = true ]; then FRAMEWORK="library-go"
    else FRAMEWORK="unknown"
    fi

    TEST_DIRS=$(find "$REPO_ROOT" -type d \( -name 'e2e' -o -name 'test' \) \
        -not -path '*/vendor/*' 2>/dev/null | head -5)
    HAS_MAKEFILE=$(test -f "$REPO_ROOT/Makefile" && echo "true" || echo "false")
    OPERATOR_CONTEXT_SOURCE="local"

# Strategy 2: Fetch from GitHub (costs 2-3 API calls)
else
    PR_HEAD_SHA=$(gh pr view "$PR_NUMBER" --repo "$REPO" --json headRefOid --jq '.headRefOid' 2>/dev/null)

    GO_MOD_CONTENT=$(gh api "repos/$REPO/contents/go.mod?ref=$PR_HEAD_SHA" \
        --jq '.content' 2>/dev/null | base64 -d 2>/dev/null || echo "")

    if [ -n "$GO_MOD_CONTENT" ]; then
        GO_MODULE=$(echo "$GO_MOD_CONTENT" | head -1 | awk '{print $2}')

        HAS_CR=false; HAS_LIBGO=false
        echo "$GO_MOD_CONTENT" | grep -q "sigs.k8s.io/controller-runtime" && HAS_CR=true
        echo "$GO_MOD_CONTENT" | grep -q "github.com/openshift/library-go" && HAS_LIBGO=true

        if [ "$HAS_CR" = true ]; then FRAMEWORK="controller-runtime"
        elif [ "$HAS_LIBGO" = true ]; then FRAMEWORK="library-go"
        else FRAMEWORK="unknown"
        fi

        HAS_MAKEFILE=$(gh api "repos/$REPO/contents/Makefile?ref=$PR_HEAD_SHA" \
            --jq '.name' 2>/dev/null && echo "true" || echo "false")

        TEST_DIRS=$(gh api "repos/$REPO/git/trees/$PR_HEAD_SHA?recursive=1" \
            --jq '[.tree[] | select(.type=="tree") | select(.path | test("(^|/)e2e$|(^|/)test$"))] | .[0:5] | .[].path' \
            2>/dev/null || echo "")

        OPERATOR_CONTEXT_SOURCE="github"
    else
        GO_MODULE=""; FRAMEWORK="unknown"; TEST_DIRS=""; HAS_MAKEFILE="false"
        echo "WARNING: Could not fetch operator repo context. Continuing without it."
    fi
fi

echo "Operator context: source=$OPERATOR_CONTEXT_SOURCE module=$GO_MODULE framework=$FRAMEWORK makefile=$HAS_MAKEFILE"
```

#### Precheck 5 — Gather PR Change Context

Fetch the list of changed files and a summary of the diff for each PR. This tells the skill what the PR actually changed, enabling error-to-file correlation and change-type classification.

```bash
for PR_NUMBER in "${PR_NUMBERS[@]}"; do
    # Changed file paths (1 API call per PR)
    PR_CHANGED_FILES=$(gh pr view "$PR_NUMBER" --repo "$REPO" \
        --json files --jq '.files[].path')

    # Classify change type from file paths
    PR_HAS_API_CHANGES=$(echo "$PR_CHANGED_FILES" | grep -cE '(_types\.go|types_.*\.go)$' || true)
    PR_HAS_CONTROLLER_CHANGES=$(echo "$PR_CHANGED_FILES" | grep -cE '(controller|reconcil).*\.go$' || true)
    PR_HAS_TEST_CHANGES=$(echo "$PR_CHANGED_FILES" | grep -cE '_test\.go$' || true)
    PR_HAS_CRD_CHANGES=$(echo "$PR_CHANGED_FILES" | grep -cE '(crd|crds)/.*\.yaml$' || true)
    PR_HAS_RBAC_CHANGES=$(echo "$PR_CHANGED_FILES" | grep -cE 'rbac.*\.yaml$' || true)

    echo "PR #$PR_NUMBER changes: files=$(echo "$PR_CHANGED_FILES" | wc -l)" \
         "api=$PR_HAS_API_CHANGES controller=$PR_HAS_CONTROLLER_CHANGES" \
         "test=$PR_HAS_TEST_CHANGES crd=$PR_HAS_CRD_CHANGES rbac=$PR_HAS_RBAC_CHANGES"
done
```

**If ALL prechecks above passed, proceed to Phase 1.**
**If ANY precheck FAILED (exit 1), STOP. Do NOT proceed further.**

---

### Phase 1-7: CI Monitoring, Analysis, and Fix Loop

Load and execute the **ci-monitor skill** (`plugins/oape/skills/ci-monitor/SKILL.md`) for all subsequent phases. Pass the resolved context from Phase 0:

- `REPO`, `PR_NUMBERS[]`, and all parsed flags
- Operator repo context: `GO_MODULE`, `FRAMEWORK`, `TEST_DIRS`, `HAS_MAKEFILE`, `OPERATOR_CONTEXT_SOURCE`
- PR change context: `PR_CHANGED_FILES`, change type counts (api, controller, test, crd, rbac)

The skill handles:

1. **Release Repo Discovery** -- fetches the ci-operator config from `openshift/release` for the target repo and branch. Parses job definitions (required/optional, fast/slow, cluster profile, release version) and resolves step registry references for failed jobs on demand.

2. **SHA-Anchored Adaptive Polling** (Phase 1) -- records the HEAD SHA, polls GitHub checks and Prow commit statuses at adaptive intervals (60s/120s), detects SHA changes (clears stale results, waits settle period), and detects retests (timestamp comparison). Classifies jobs as fast/slow using ci-operator config when available, falling back to a name-based pattern table.

3. **Failure Evidence Collection** (Phase 2) -- for each failed context, fetches GitHub Actions logs or Prow GCS artifacts (build-log, finished.json, JUnit XML, must-gather). When release context is available, maps failing steps to their step registry entries (container image, commands script).

4. **Failure Classification** (Phase 3) -- classifies each failure into one of five modes: install (A), test (B), build (C), lint/boilerplate (D), or infra flake (E). Uses multi-signal detection (JUnit patterns, build-log regex, finished.json fields, ci-operator step metadata).

5. **Deep Analysis and Flake Detection** (Phase 4) -- queries Sippy for historical pass rates using the OCP release version from the ci-operator config. Analyzes pass/fail sequences, error message consistency, and cluster health disruption patterns.

6. **Stage-Aware Summary** (Phase 5) -- when exactly three PRs are provided, correlates failures across API / implementation / e2e stages and detects cross-stage dependencies.

7. **Report Generation** (Phase 6) -- produces a structured markdown report including enriched job metadata (required/optional, cluster profile, step ref, commands) when release context is available.

8. **Fix-Push-Rewatch Loop** (Phase 7) -- when `--max-fix-rounds > 0`, verifies the local branch matches the PR branch, applies fixes, runs local verification, pushes, and re-polls. Uses error signature hashing (normalized + SHA-256) to deterministically detect when a fix was ineffective (>= 75% hash match = same error = stop loop).

---

## Behavioral Rules

1. **Collect everything first**: Never stop after the first failure. Gather evidence across all PRs and all failed jobs before producing the report.
2. **No destructive operations**: Never propose force-push, branch deletion, or history rewriting.
3. **Fix before retry**: Prefer deterministic fixes over blind retries. Only recommend `/retest` when evidence strongly suggests infra flake.
4. **Explicit confidence**: Always state confidence level. If evidence is insufficient, say so and recommend deeper tools.
5. **Stage-aware ordering**: When multiple PRs are involved, recommend fixing upstream PR failures first.
6. **Budget-conscious**: Use adaptive polling to minimize API call consumption. Log the total calls made in the report footer.
7. **Context-first**: Fetch release repo context before polling. Use ci-operator config for job classification when available. Fall back to name-based heuristics only when release context is unavailable.

## Critical Failure Conditions

Fail immediately if:
1. No PR references are provided.
2. `gh` or `curl` is missing or `gh` is unauthenticated.
3. Repository cannot be resolved.
4. A provided PR reference cannot be resolved to an accessible PR.

## Exit Conditions

- **Success**: All checks pass (possibly after fix rounds). Report produced.
- **Partial Success**: Timeout reached or max-fix-rounds exhausted. Partial report produced with recommendations.
- **Failure**: Precheck or resolution failure before monitoring begins.
