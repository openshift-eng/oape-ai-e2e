#!/usr/bin/env bash
# classify.sh — Sourced library of CI-failure classification helpers shared by
# log-analyzer.sh (coarse failure buckets) and auto-fix.sh (lint sub-categories).
# Source this file; do not execute directly.
#
# Keeping both classifiers here is the single source of truth for failure-pattern
# regexes so the two callers cannot silently drift apart.
#
# Usage: source scripts/pr-agent/classify.sh

# Guard against direct execution
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "ERROR: classify.sh must be sourced, not executed directly" >&2
  exit 1
fi

# Lint tools recognized identically by both classifiers.
CLASSIFY_LINT_TOOLS='golangci-lint|golint|staticcheck|revive'

# ---------------------------------------------------------------------------
# classify_log — coarse CI failure bucket for a whole log file.
#   Buckets: install-failure | infra-flake | build-failure | lint-failure |
#            test-failure | unknown
#   Reads the last ${MAX_LOG_LINES:-1000} lines of the log.
# ---------------------------------------------------------------------------
classify_log() {
  local log_file="$1"
  local content
  content=$(tail -n "${MAX_LOG_LINES:-1000}" "$log_file")

  # Mode A: Install failure
  if echo "$content" | grep -qiE 'level=fatal.*installer|cluster creation failed|bootstrap.*timed out|waiting for bootstrapComplete|cluster-install.*fail'; then
    echo "install-failure"
    return
  fi

  # Mode E: Infrastructure / transient
  if echo "$content" | grep -qiE 'ImagePullBackOff|i/o timeout|connection refused|etcdserver: request timed out|lease lost|quota exceeded|InsufficientInstanceCapacity|registry.*timeout|context deadline exceeded'; then
    if ! echo "$content" | grep -qiE 'FAIL:.*Test|--- FAIL'; then
      echo "infra-flake"
      return
    fi
  fi

  # Mode C: Build / compile failure
  if echo "$content" | grep -qiE 'cannot find package|undefined:|syntax error.*\.go|imported and not used|make: \*\*\* .* Error'; then
    echo "build-failure"
    return
  fi

  # Mode D: Lint / static analysis
  if echo "$content" | grep -qiE "${CLASSIFY_LINT_TOOLS}|gofmt|goimports|formatting differs|generated code is out of date|make generate|make manifests|deepcopy-gen|boilerplate"; then
    echo "lint-failure"
    return
  fi

  # Mode B: Test failure
  if echo "$content" | grep -qiE 'FAIL:.*Test|--- FAIL|FAIL\s+\S+/|test.*failed'; then
    echo "test-failure"
    return
  fi

  echo "unknown"
}

# ---------------------------------------------------------------------------
# refine_lint_category — refine a coarse "lint-failure" into an auto-fixable
#   sub-type by reading the actual CI log files.
#   Sub-types: trivial-generated-files | trivial-import | trivial-lint |
#              trivial-format (default)
# ---------------------------------------------------------------------------
refine_lint_category() {
  local log_dir="$1"
  local job="$2"

  if [[ -z "$log_dir" || ! -d "$log_dir" ]]; then
    echo "trivial-format"
    return
  fi

  local log_id
  log_id=$(echo "$job" | tr '/ ' '__')
  local log_file="${log_dir}/log-${log_id}.txt"

  if [[ ! -s "$log_file" ]]; then
    for f in "${log_dir}"/log-*.txt; do
      [[ -s "$f" ]] && log_file="$f" && break
    done
  fi

  if [[ ! -s "$log_file" ]]; then
    echo "trivial-format"
    return
  fi

  local content
  content=$(cat "$log_file")

  if echo "$content" | grep -qiE 'generated code is out of date|make generate|make manifests|deepcopy-gen|zz_generated|boilerplate'; then
    echo "trivial-generated-files"
  elif echo "$content" | grep -qiE 'imported and not used|could not import|import ordering'; then
    echo "trivial-import"
  elif echo "$content" | grep -qiE "${CLASSIFY_LINT_TOOLS}"; then
    echo "trivial-lint"
  elif echo "$content" | grep -qiE 'gofmt|goimports|formatting differs|diff.*\.go'; then
    echo "trivial-format"
  else
    echo "trivial-format"
  fi
}
