---
description: Monitor CI/Prow job status for one or more PRs with adaptive polling, SHA-tracking, and optional fix-push-rewatch loop
argument-hint: <pr1-url-or-number> [pr2-url-or-number] [pr3-url-or-number] [--repo <owner/name>] [--timeout-min <n>] [--max-fix-rounds <n>] [--fast]
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

- **Adaptive polling**: Polls at 60s for fast jobs (lint/unit/verify), backs off to 120s during cluster provisioning (saves ~44% API calls), and tightens to 60s when slow jobs approach completion.
- **SHA-anchored**: Tracks the PR head SHA on every poll. When a new commit is pushed, all stale results are discarded and polling restarts after a 90s settle period.
- **Retest-aware**: Detects `/retest` and `/test <job>` (no SHA change) by comparing `started_at` timestamps. If a terminal context reappears as pending or has a newer timestamp, it is treated as restarted.
- **Fix-push-rewatch loop**: In agent mode, can apply a fix, push, detect the SHA change, and re-poll CI automatically (up to `max-fix-rounds` times).
- **Prow-native**: Treats `ci/prow/*` commit-status contexts as first-class signals alongside GitHub Actions checks.
- **Artifact collection**: Downloads `build-log.txt`, `finished.json`, `junit*.xml`, and step-level logs from GCS for each failed Prow job.
- **Failure-mode routing**: Classifies each failure as install failure, test failure, lint/build failure, boilerplate/tooling failure, or infra flake.
- **Flake detection**: Cross-references test names against Sippy for historical pass rates and known open bugs.
- **Stage-aware summary**: When three PRs are provided, correlates failures across API / implementation / e2e stages.

### API Budget

Each poll iteration costs **3 GitHub API calls per PR** (SHA check + statusCheckRollup + commit status). GCS artifact downloads and Sippy queries are free (separate services).

| Scenario | Calls/round | Typical budget usage |
|---|---|---|
| 1 PR, lint/unit only (30 min) | ~90 | 1.8% of 5,000/hr |
| 1 PR, e2e + cluster install (120 min) | ~240 | 4.8% |
| 3 PRs, e2e + cluster, 2 fix rounds | ~1,800 | 36% |

## Arguments

- Positional args (`$1`, `$2`, `$3`): PR references. Each value may be:
  - PR number (for example: `123`)
  - Full PR URL (for example: `https://github.com/org/repo/pull/123`)
- `--repo <owner/name>` (optional): repository override. If omitted, infer from PR URL or `git remote origin`.
- `--timeout-min <n>` (optional): maximum wait time per monitoring round. Default: `120`. Auto-adjusts down if no e2e/cluster jobs are detected.
- `--max-fix-rounds <n>` (optional): max push-and-rewatch cycles. Default: `2`. Set to `0` for report-only mode (no auto-fix loop).
- `--sha-settle-sec <n>` (optional): seconds to wait after detecting a SHA change before resuming polls. Default: `90`.
- `--fast` (optional): skip deep artifact downloads (must-gather, full junit parsing). Produces a faster but shallower report.

## Implementation

### Phase 0: Prechecks

All prechecks must pass before polling CI.

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

1. Parse flags (`--repo`, `--timeout-min`, `--max-fix-rounds`, `--sha-settle-sec`, `--fast`).
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

---

### Phase 1: SHA-Anchored Adaptive Polling

#### 1.1 Record Initial State

```bash
TRACKED_SHA=$(gh pr view "$PR_NUMBER" --repo "$REPO" --json headRefOid --jq '.headRefOid')
FIX_ATTEMPT=0
ROUND_START=$(date +%s)
```

For each PR, record:
- `tracked_sha`: the HEAD SHA we are monitoring
- `signals`: map of context name → {state, started_at, target_url, provider}

#### 1.2 Adaptive Interval Selection

After the first successful poll, classify all pending contexts:

- **Fast contexts**: names matching `lint`, `vet`, `verify`, `verify-deps`, `unit`, `test` (not `e2e`), `images`, `bundle`, `validate-boilerplate`, `coverage`
- **Slow contexts**: names matching `e2e`, `install`, or job names containing cloud providers (`aws`, `gcp`, `azure`, `metal`, `vsphere`)

Select polling phase:

| Condition | Interval |
|---|---|
| Any fast context still pending | **60s** (fast phase) |
| Only slow contexts pending, running < 45 min | **120s** (slow phase) |
| Only slow contexts pending, running >= 45 min | **60s** (finishing phase) |
| All contexts terminal | Exit polling |

This saves ~44% of API calls during the 30-60 min cluster provisioning window where nothing changes.

#### 1.3 SHA Change Detection (new commit pushed)

On every poll iteration, before checking context states:

```bash
CURRENT_SHA=$(gh pr view "$PR_NUMBER" --repo "$REPO" --json headRefOid --jq '.headRefOid')

if [ "$CURRENT_SHA" != "$TRACKED_SHA" ]; then
    echo "SHA changed: $TRACKED_SHA -> $CURRENT_SHA"
    TRACKED_SHA="$CURRENT_SHA"
    # Clear ALL accumulated results — they are stale
    clear_all_signals()
    # Wait for Prow to register new contexts
    sleep $SHA_SETTLE_SEC
    # Reset round timer
    ROUND_START=$(date +%s)
    continue
fi
```

**Why 90s settle**: After a push, Prow needs 30-60s to start registering new status contexts on the new SHA. Polling immediately would see "0 pending, 0 failed" and incorrectly conclude everything passed.

#### 1.4 Retest Detection (no SHA change)

When `/retest` or `/test <job>` is commented, the SHA stays the same but Prow creates new job runs. Detect by tracking `started_at`:

```python
for context in current_poll_results:
    prev = signals.get(context.name)
    if prev and prev.state in TERMINAL_STATES:
        if context.state in ("pending", "queued"):
            # Context was terminal, now pending again → restarted
            log(f"Retest detected: {context.name} restarted")
            signals[context.name] = context  # reset to pending
        elif context.started_at > prev.started_at:
            # Same state but newer timestamp → completed a rerun
            signals[context.name] = context  # update with new result
```

#### 1.5 Unified Signal Tracking

Merge both Checks API (`statusCheckRollup`) and commit status contexts into a single list. For each entry track:
- `name` / `context`
- `state` (`queued`, `in_progress`, `pending`, `success`, `failure`, `cancelled`, `timed_out`, `action_required`, `skipped`)
- `provider` (`github-actions`, `github-check`, `prow-status-context`)
- `target_url` / `details_url`
- `started_at`

Classify non-test contexts separately:
- `tide` — merge-gate status, not a test. Report its description but do not treat as failure.
- `CodeRabbit` — code review bot, not CI.

#### 1.6 Termination Conditions

Stop polling a PR when:
1. No checks AND no status contexts are `pending` or `in_progress` or `queued`.
2. Timeout reached — mark unresolved signals as `timed_out`.
3. PR state changed to `CLOSED` or `MERGED`.

#### 1.7 Auto-Adjusted Timeout

After the first poll, if only fast contexts are detected (no e2e/cluster jobs), auto-reduce timeout:
- Only lint/verify/unit/build/images → cap at **30 min**
- e2e without cluster install → cap at **60 min**
- e2e with cluster install → use full `--timeout-min` value (default 120)

---

### Phase 2: Collect Failure Evidence

For every signal in a terminal failure state (`failure`, `cancelled`, `timed_out`), gather evidence using the strategy appropriate to its provider.

#### 2.1 GitHub Actions Failures

Extract the run ID from the details URL and fetch failed logs:

```bash
gh run view "$RUN_ID" --repo "$REPO" --log-failed
```

#### 2.2 Prow Status Context Failures

For each failed `ci/prow/*` context:

1. **Parse the Prow job URL** from `target_url`. Extract:
   - GCS bucket path (e.g., `gs/test-platform-results/pr-logs/pull/.../<build_id>`)
   - Job name (e.g., `pull-ci-openshift-must-gather-operator-master-validate-boilerplate`)
   - Build ID

2. **Derive artifact base URL** from the `target_url`:

```bash
ARTIFACT_BASE=$(echo "$TARGET_URL" | sed 's|https://prow.ci.openshift.org/view/gs/|https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/|')
```

3. **Download key artifacts** (skip in `--fast` mode except `build-log.txt`):

| Artifact | Path | Purpose |
|---|---|---|
| `build-log.txt` | `$ARTIFACT_BASE/build-log.txt` | Primary CI log — always fetch |
| `finished.json` | `$ARTIFACT_BASE/finished.json` | Exit code, timestamps, result |
| `prowjob.json` | `$ARTIFACT_BASE/prowjob.json` | Full Prow job spec |
| `junit*.xml` | `$ARTIFACT_BASE/artifacts/<step>/junit*.xml` | Per-test pass/fail with error messages |
| `must-gather.tar` | `$ARTIFACT_BASE/artifacts/*/must-gather.tar` | Cluster diagnostics (e2e jobs) |

4. **Parse JUnit XML** (unless `--fast`):
   - Enumerate every `<testcase>` with a `<failure>` or `<error>` child.
   - Extract: test name, class name, failure message, stack trace snippet (first 40 lines).
   - Count total tests, passed, failed, skipped, errored.

5. **Detect must-gather availability** (unless `--fast`):
   - If present, note the download URL in the report.

6. If any artifact fetch fails, mark evidence as `partial` and continue.

---

### Phase 3: Classify Failure Mode

For each failed Prow job, classify into one of the following failure modes:

#### Failure Mode A: Install Failure

**Detection**: JUnit contains a test matching `install should succeed: *`.

**Analysis focus**: Identify install stage, extract installer errors.

#### Failure Mode B: Test Failure (e2e, unit, integration)

**Detection**: JUnit contains failed `<testcase>` entries that are NOT install tests.

**Analysis focus**: List failed tests, classify isolation/co-failure/mass-failure patterns.

#### Failure Mode C: Build / Compile Failure

**Detection**: Build log contains compile errors.

**Analysis focus**: Extract compiler errors, map to PR diff.

#### Failure Mode D: Lint / Static Analysis / Boilerplate

**Detection**: Job name contains `lint`, `vet`, `verify`, `validate-boilerplate`, `verify-deps`.

**Analysis focus**: Extract lint/validation errors, distinguish CI image issues from actual drift.

#### Failure Mode E: CI Infrastructure / Transient

**Detection**: Build log contains infra error patterns with no test-level errors.

**Analysis focus**: Mark as `probable-infra-flake`, recommend `/retest`.

---

### Phase 4: Deep Analysis and Flake Detection

#### 4.1 Sippy Historical Pass Rate Lookup

For each failed test name, query Sippy (public API, no auth, not counted against GitHub rate limit):

```bash
curl -sf "https://sippy.dptools.openshift.org/api/tests?release=<release>&filter=..."
```

Classification:
- Pass rate >= 95% and now failing → **likely genuine regression**
- Pass rate < 95% with `open_bugs > 0` → **known flaky test**
- Pass rate < 95% with `open_bugs == 0` → **unstable test**

#### 4.2 Prow Job History / Pass Sequence Analysis

Classify pass sequence pattern (left = newest):
- `FFFFFFFFFF` → Permafail (High priority)
- `FFFFSSSSSS` → Recent regression (High)
- `SSSSSFFFFF` → Resolved (Low)
- `SFSFSFSFSF` → Flaky (Medium)

#### 4.3 Failure Output Consistency

Compare error messages across multiple failures:
- **Highly consistent** (>90%): single cascading root cause
- **Moderately consistent** (50-90%): primary issue with secondaries
- **Inconsistent** (<50%): multiple issues or environmental instability

#### 4.4 Disruption / Cluster Health Correlation (e2e jobs only, unless `--fast`)

Check for cluster-level disruption: `ci-cluster-network-liveness` failures, operator degradation, etcd issues.

---

### Phase 5: Stage-Aware Summary (PR1/PR2/PR3)

If exactly three PRs were provided, summarize by stage:
- **PR #1 (API)**: schema/codegen/validation risks, boilerplate/generation drift
- **PR #2 (Implementation)**: controller logic, build, unit test, RBAC consistency
- **PR #3 (E2E)**: scenario coverage, e2e environment, cluster install stability

Cross-stage dependency detection:
- PR #2 compile failure referencing PR #1 types → fix PR #1 first
- PR #3 "CRD not found" → PRs #1/#2 not merged yet

---

### Phase 6: Return Report

Return a structured markdown report:

```text
=== CI Monitor Report ===

Repository: <owner/repo>
PR Head SHA: <sha>
Monitoring: adaptive polling (60s/120s/60s) | Timeout: <N>m
Fix Round: <N> of <max> | SHA Changes Detected: <N>
Signals Observed: <checks-count> checks, <status-context-count> status contexts
Mode: <comprehensive | fast>

────────────────────────────────────────
PR Results
────────────────────────────────────────
PR #<n> "<title>" — PASS | FAIL | TIMED_OUT
  Checks:  <pass>/<total> passed
  Prow:    <pass>/<total> passed
  Failed:  <list of failed context names>

────────────────────────────────────────
Failure Analysis
────────────────────────────────────────
1) [PR #<n>] <check-name>
   Provider: <github-actions | prow-status-context>
   Failure Mode: <install | test | build | lint/boilerplate | infra-flake>
   Job URL: <prow view url or actions url>
   Artifacts: <gcsweb artifact browser url>

   Evidence:
     <key log excerpt — max 20 lines>

   JUnit Summary (if available):
     Total: <N> | Passed: <N> | Failed: <N> | Skipped: <N>

   Sippy Flake Check (if available):
     - <test name>: pass_rate=<N>% trend=<dir> open_bugs=<N>

   Root Cause Hypothesis: <text>
   Confidence: <high | medium | low>
   Fixable by agent: <yes | no — reason>

   Suggested Fixes:
     1. <most targeted fix>
     2. <alternative>

   Validation:
     - <command to verify fix locally>
     - <command to rerun CI>

────────────────────────────────────────
Prow Job Breakdown
────────────────────────────────────────
| Context | State | Mode | Flake? | Action |
|---|---|---|---|---|
| ci/prow/<name> | failure | test | no (98%) | fix required |
| ci/prow/<name> | failure | infra | yes (72%) | /retest |
| tide | pending | gate | — | needs: lgtm, approved |

────────────────────────────────────────
Recommended Next Actions
────────────────────────────────────────
1. <highest priority fix>
2. <second action>
3. <rerun plan>
```

---

### Phase 7: Fix-Push-Rewatch Loop (Agent Mode)

When `--max-fix-rounds > 0` and the analysis identifies fixable failures (Mode B/C/D but NOT Mode A install failures or Mode E infra flakes):

1. **Determine if fix is feasible**: Only attempt auto-fix when:
   - Failure Mode is B (test), C (build), or D (lint/boilerplate)
   - Root cause hypothesis has `high` or `medium` confidence
   - The fix can be applied to files in the current working directory

2. **Apply fix**:
   - Make the code change based on the suggested fix
   - Run local verification (`go build ./...`, `go vet ./...`, `make verify`)
   - If local verification fails, revert and report without pushing

3. **Commit and push**:
   ```bash
   git add -A
   git commit -m "fix: <description of CI fix>"
   git push
   ```

4. **Detect SHA change and re-poll**:
   - The push changes HEAD SHA
   - Phase 1.3 detects this, clears stale results, waits `sha-settle-sec` (90s)
   - Polling restarts for the new SHA
   - Increment `FIX_ATTEMPT`

5. **Termination**:
   - If `FIX_ATTEMPT >= max-fix-rounds`: stop, produce final report with all rounds summarized
   - If new round passes: report success
   - If new round fails with a DIFFERENT error: attempt another fix (if rounds remain)
   - If new round fails with the SAME error: stop (fix didn't work), report

6. **Never auto-fix**:
   - Install failures (Mode A) — require cluster-level investigation
   - Infra flakes (Mode E) — recommend `/retest` only
   - Repo-wide failures (same error across all open PRs) — not caused by this PR
   - Low-confidence hypotheses — report only, let user decide

---

## Behavioral Rules

1. **Collect everything first**: Never stop after the first failure. Gather evidence across all PRs and all failed jobs before producing the report.
2. **No destructive operations**: Never propose force-push, branch deletion, or history rewriting.
3. **Fix before retry**: Prefer deterministic fixes over blind retries. Only recommend `/retest` when evidence strongly suggests infra flake.
4. **Explicit confidence**: Always state confidence level. If evidence is insufficient, say so and recommend deeper tools.
5. **Stage-aware ordering**: When multiple PRs are involved, recommend fixing upstream PR failures first.
6. **Budget-conscious**: Use adaptive polling to minimize API call consumption. Log the total calls made in the report footer.
7. **Link to deeper tools**: When built-in analysis is insufficient, recommend:
   - Install failures → `/ci:analyze-prow-job-install-failure <prow-url>`
   - Test failures → `/ci:analyze-prow-job-test-failure <prow-url>`
   - Disruption → `/ci:analyze-disruption <prow-url>`
   - Resource lifecycle → `/ci:analyze-prow-job-resource <prow-url> <resource>`
   - Cluster diagnostics → `/ci:extract-prow-job-must-gather <prow-url>`
   - Flake investigation → `/ci:ask-sippy "Why is test X failing?"`

## Critical Failure Conditions

Fail immediately if:
1. No PR references are provided.
2. `gh` is missing or unauthenticated.
3. Repository cannot be resolved.
4. A provided PR reference cannot be resolved to an accessible PR.

## Exit Conditions

- **Success**: All checks pass (possibly after fix rounds). Report produced.
- **Partial Success**: Timeout reached or max-fix-rounds exhausted. Partial report produced with recommendations.
- **Failure**: Precheck or resolution failure before monitoring begins.
