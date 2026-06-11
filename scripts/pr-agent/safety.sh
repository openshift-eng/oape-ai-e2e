#!/usr/bin/env bash
# safety.sh — Sourced utility library providing shared guardrail functions
# for the OAPE PR Lifecycle Agent. Source this file; do not execute directly.
#
# Usage: source scripts/pr-agent/safety.sh

# Guard against direct execution
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "ERROR: safety.sh must be sourced, not executed directly" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Configuration (overridable via environment)
# ---------------------------------------------------------------------------
MAX_COMMITS_PER_RUN="${MAX_COMMITS_PER_RUN:-10}"
MAX_COMMITS_PER_PR="${MAX_COMMITS_PER_PR:-3}"
MAX_DIFF_LINES="${MAX_DIFF_LINES:-500}"
COMMIT_COUNTER_FILE="${RUNNER_TEMP:-/tmp}/pr-agent-commit-count.txt"
AUDIT_LOG="${RUNNER_TEMP:-/tmp}/pr-agent-audit-${GITHUB_RUN_ID:-local}.jsonl"

# ---------------------------------------------------------------------------
# File blocklist patterns
# ---------------------------------------------------------------------------
# Protect actual secret storage files, CI/container configs, RBAC manifests,
# and dependency lock files. Go source files that operate on Kubernetes
# Secret/Token resources are NOT blocked — only files that store secrets.

# Default patterns (go.mod and go.sum blocked)
BLOCKED_PATTERNS='\.(key|pem|crt|cert|p12|pfx)$'
BLOCKED_PATTERNS+='|\.env$'
BLOCKED_PATTERNS+='|credentials\.'
BLOCKED_PATTERNS+='|(^|/)kubeconfig$'
BLOCKED_PATTERNS+='|(^|/)Dockerfile$|(^|/)Containerfile$|\.dockerignore$'
BLOCKED_PATTERNS+='|\.github/workflows|\.tekton/'
BLOCKED_PATTERNS+='|(^|/)Makefile$'
BLOCKED_PATTERNS+='|rbac/.*\.yaml|clusterrole.*\.yaml'
BLOCKED_PATTERNS+='|go\.mod$|go\.sum$'

# Relaxed patterns for trivial-generated-files (go.mod/go.sum allowed
# because make generate legitimately runs go mod tidy)
BLOCKED_PATTERNS_GENERATED='\.(key|pem|crt|cert|p12|pfx)$'
BLOCKED_PATTERNS_GENERATED+='|\.env$'
BLOCKED_PATTERNS_GENERATED+='|credentials\.'
BLOCKED_PATTERNS_GENERATED+='|(^|/)kubeconfig$'
BLOCKED_PATTERNS_GENERATED+='|(^|/)Dockerfile$|(^|/)Containerfile$|\.dockerignore$'
BLOCKED_PATTERNS_GENERATED+='|\.github/workflows|\.tekton/'
BLOCKED_PATTERNS_GENERATED+='|(^|/)Makefile$'
BLOCKED_PATTERNS_GENERATED+='|rbac/.*\.yaml|clusterrole.*\.yaml'

# ---------------------------------------------------------------------------
# check_blocklist — returns 0 (safe) or 1 (blocked)
#   $1: newline-separated file paths to check
#   $2: (optional) failure category — "trivial-generated-files" relaxes go.mod/go.sum
# ---------------------------------------------------------------------------
check_blocklist() {
  local files="$1"
  local category="${2:-}"
  local patterns="$BLOCKED_PATTERNS"

  if [[ "$category" == "trivial-generated-files" ]]; then
    patterns="$BLOCKED_PATTERNS_GENERATED"
  fi

  if [[ -z "$files" ]]; then
    return 0
  fi

  if echo "$files" | grep -qE "$patterns"; then
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# audit_log — append a structured JSONL entry
#   $1: action  (auto-fix, blocked, skipped, reverted, dry-run, error, info)
#   $2: category (trivial-format, trivial-generated-files, etc.)
#   $3: files   (space-separated list)
#   $4: commit  (SHA or empty)
#   $5: outcome (human-readable description)
# ---------------------------------------------------------------------------
audit_log() {
  local action="${1:-}" category="${2:-}" files="${3:-}" commit="${4:-}" outcome="${5:-}"
  local ts
  ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  local files_json
  files_json=$(echo "$files" | tr ' ' '\n' | jq -R -s 'split("\n") | map(select(. != ""))' 2>/dev/null || echo '[]')

  printf '{"ts":"%s","pr":"%s","action":"%s","type":"%s","files":%s,"commit":"%s","outcome":"%s"}\n' \
    "$ts" "${CURRENT_PR_URL:-}" "$action" "$category" \
    "$files_json" "$commit" "$outcome" \
    >> "$AUDIT_LOG"
}

# ---------------------------------------------------------------------------
# check_commit_limit — returns 0 (within limits) or 1 (limit reached)
#   $1: per-PR commit count for the current PR
# ---------------------------------------------------------------------------
check_commit_limit() {
  local pr_commits="${1:-0}"
  local total_commits
  total_commits=$(cat "$COMMIT_COUNTER_FILE" 2>/dev/null || echo 0)

  if [[ "$total_commits" -ge "$MAX_COMMITS_PER_RUN" ]]; then
    echo "GUARDRAIL: Run commit limit reached (${total_commits}/${MAX_COMMITS_PER_RUN})" >&2
    return 1
  fi
  if [[ "$pr_commits" -ge "$MAX_COMMITS_PER_PR" ]]; then
    echo "GUARDRAIL: Per-PR commit limit reached (${pr_commits}/${MAX_COMMITS_PER_PR})" >&2
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# increment_commit_count — bump the shared counter file by 1, echo new total
# ---------------------------------------------------------------------------
increment_commit_count() {
  local total
  total=$(cat "$COMMIT_COUNTER_FILE" 2>/dev/null || echo 0)
  total=$((total + 1))
  echo "$total" > "$COMMIT_COUNTER_FILE"
  echo "$total"
}

# ---------------------------------------------------------------------------
# check_diff_size — returns 0 (within limit) or 1 (too large)
#   Checks staged + unstaged changes against MAX_DIFF_LINES.
# ---------------------------------------------------------------------------
check_diff_size() {
  local diff_lines
  diff_lines=$(git diff --numstat | awk '{s+=$1+$2} END {print s+0}')
  # Include untracked files that would be staged
  local untracked_lines
  untracked_lines=$(git ls-files --others --exclude-standard -z 2>/dev/null \
    | xargs -0 wc -l 2>/dev/null | tail -1 | awk '{print $1+0}' || echo 0)
  diff_lines=$((diff_lines + untracked_lines))

  if [[ "$diff_lines" -gt "$MAX_DIFF_LINES" ]]; then
    echo "GUARDRAIL: Diff too large (${diff_lines} lines > ${MAX_DIFF_LINES} limit)" >&2
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# gh_retry — retry a command with exponential backoff
#   All arguments are passed through as the command to execute.
#   Retries 3 times at 5s / 15s / 45s intervals.
# ---------------------------------------------------------------------------
gh_retry() {
  local retries=3 delay=5
  for ((i = 1; i <= retries; i++)); do
    if "$@"; then
      return 0
    fi
    if [[ "$i" -lt "$retries" ]]; then
      echo "[retry] Attempt ${i}/${retries} failed, waiting ${delay}s..." >&2
      sleep "$delay"
      delay=$((delay * 3))
    fi
  done
  echo "[retry] All ${retries} attempts failed for: $*" >&2
  return 1
}

# ---------------------------------------------------------------------------
# Initialize commit counter file if it doesn't exist
# ---------------------------------------------------------------------------
if [[ ! -f "$COMMIT_COUNTER_FILE" ]]; then
  echo 0 > "$COMMIT_COUNTER_FILE"
fi
