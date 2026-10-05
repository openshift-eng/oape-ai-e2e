---
description: "Run the PR Lifecycle Agent: monitor CI, auto-fix failures, and address review comments"
argument-hint: <PR-URL> [--dry-run] [--monitor-only] [--review]
---

## Name
oape:pr-agent

## Synopsis
```shell
/oape:pr-agent <PR-URL> [--dry-run] [--monitor-only] [--review]
```

## Description

The `oape:pr-agent` command runs the PR Lifecycle Agent against a single pull request. It orchestrates a multi-phase pipeline:

| Phase | Script | What it does |
|-------|--------|--------------|
| 1 | `monitor.sh` | Poll CI checks, collect logs from Prow GCS, classify failures deterministically |
| 2 | `dispatch.sh` | Route failures to auto-fix, retest, or Claude-powered analysis |
| 3 | `review-handler.sh` | When CI is green, address pending review comments via Claude |
| 4 | (entrypoint) | Generate and post structured status report |

Phase 3 only runs when `--review` is passed and all CI checks are green.

## Arguments

- `$1` (`PR-URL`): Full GitHub PR URL, e.g. `https://github.com/openshift/cert-manager-operator/pull/123`. **Required.**
- `--dry-run`: Run the full analysis pipeline without posting PR comments or pushing commits.
- `--monitor-only`: Skip auto-fix and review handling (report-only mode).
- `--review`: Enable Phase 3 review handler (address pending review comments when CI passes).

## Implementation

### Step 0: Parse Arguments

Parse the user's input. The first positional argument is the PR URL. Remaining arguments are flags.

```bash
PR_URL="$1"
DRY_RUN_FLAG=""
MONITOR_FLAG=""
REVIEW_FLAG=""

for arg in "${@:2}"; do
  case "$arg" in
    --dry-run)       DRY_RUN_FLAG="--dry-run" ;;
    --monitor-only)  MONITOR_FLAG="--monitor-only" ;;
    --review)        REVIEW_FLAG="--review" ;;
  esac
done
```

### Step 1: Validate PR URL

Verify the PR URL matches the expected format:
```bash
if [[ ! "$PR_URL" =~ ^https://github.com/[^/]+/[^/]+/pull/[0-9]+$ ]]; then
  echo "ERROR: Invalid PR URL format. Expected: https://github.com/<owner>/<repo>/pull/<number>"
  exit 1
fi
```

### Step 2: Validate Repo Allowlist

Extract owner/repo from the URL and verify the repository is listed in `deploy/config/team-repos.csv`:
```bash
OWNER=$(echo "$PR_URL" | sed 's|https://github.com/||;s|/pull/.*||' | cut -d/ -f1)
REPO=$(echo "$PR_URL" | sed 's|https://github.com/||;s|/pull/.*||' | cut -d/ -f2)
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)

if ! grep -q "https://github.com/${OWNER}/${REPO}" "${REPO_ROOT}/deploy/config/team-repos.csv" 2>/dev/null; then
  echo "ERROR: ${OWNER}/${REPO} is not in the allowed repos list (deploy/config/team-repos.csv)"
  exit 1
fi
```

### Step 3: Run the Agent

Invoke `scripts/pr-agent/entrypoint.sh` in on-demand mode:

```bash
export RUNNER_TEMP="${RUNNER_TEMP:-/tmp}"
export GH_TOKEN="${GH_TOKEN:-$(gh auth token 2>/dev/null || echo '')}"

"${REPO_ROOT}/scripts/pr-agent/entrypoint.sh" \
  --mode on-demand \
  --pr-url "$PR_URL" \
  $MONITOR_FLAG \
  $DRY_RUN_FLAG \
  $REVIEW_FLAG
```

### Step 4: Display Results

After the script completes, read and display the generated report:

```bash
PR_NUMBER=$(echo "$PR_URL" | grep -oP '[0-9]+$')
REPORT_FILE="${RUNNER_TEMP}/pr-agent-report-${OWNER}-${REPO}-${PR_NUMBER}.md"

if [[ -f "$REPORT_FILE" ]]; then
  echo ""
  echo "=========================================="
  echo "  PR Agent Report"
  echo "=========================================="
  cat "$REPORT_FILE"
else
  echo "No report file generated. Check the output above for errors."
fi
```

Also display a summary of the failure analysis if available:

```bash
ANALYSIS_FILE="${RUNNER_TEMP}/failure-analysis-${OWNER}-${REPO}-${PR_NUMBER}.json"
if [[ -f "$ANALYSIS_FILE" ]]; then
  TOTAL=$(jq 'length' "$ANALYSIS_FILE" 2>/dev/null || echo 0)
  TRIVIAL=$(jq '[.[] | select(.category | startswith("trivial-"))] | length' "$ANALYSIS_FILE" 2>/dev/null || echo 0)
  FLAKES=$(jq '[.[] | select(.category == "infra-flake")] | length' "$ANALYSIS_FILE" 2>/dev/null || echo 0)
  UNKNOWN=$(jq '[.[] | select(.category == "unknown")] | length' "$ANALYSIS_FILE" 2>/dev/null || echo 0)

  echo ""
  echo "Failure breakdown: ${TOTAL} total — ${TRIVIAL} trivial-fixable, ${FLAKES} infra-flakes, ${UNKNOWN} unknown"
fi
```

### Step 5: Offer Scheduled Re-check (optional)

Ask the user if they want to schedule a periodic re-check:

> Would you like to schedule a re-check in 10 minutes? I can use CronCreate to poll the PR again after CI has had time to re-run.

If the user accepts, create a one-shot cron job:
```
CronCreate(cron: "<minute+10> <hour> <dom> <month> *", recurring: false,
  prompt: "/oape:pr-agent <PR-URL> --review")
```

## Examples

1. **Full pipeline (monitor + auto-fix + review handler)**:
   ```shell
   /oape:pr-agent https://github.com/openshift/cert-manager-operator/pull/456 --review
   ```

2. **Monitor-only (report without fixes or review handling)**:
   ```shell
   /oape:pr-agent https://github.com/openshift/cert-manager-operator/pull/456 --monitor-only
   ```

3. **Dry-run analysis (no mutations)**:
   ```shell
   /oape:pr-agent https://github.com/openshift/cert-manager-operator/pull/456 --dry-run --review
   ```

4. **Default (monitor + auto-fix, no review handler)**:
   ```shell
   /oape:pr-agent https://github.com/openshift/must-gather-operator/pull/123
   ```
