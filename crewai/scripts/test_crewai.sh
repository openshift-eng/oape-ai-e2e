#!/usr/bin/env bash
# Quick tests for the OAPE CrewAI workflow.
# Run from oape-ai-e2e/crewai: ./scripts/test_crewai.sh [smoke|context|output-dir|apply]

set -e
CREWAI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CREWAI_DIR"

export OAPE_MAX_REASONING_ATTEMPTS="${OAPE_MAX_REASONING_ATTEMPTS:-3}"

case "${1:-smoke}" in
  smoke)
    echo "=== Smoke test: default scope, 11 tasks (no context file) ==="
    python main.py
    ;;
  context)
    echo "=== Test with context file (example_scope.txt) ==="
    python main.py --context-file example_scope.txt
    ;;
  output-dir)
    echo "=== Test with --output-dir (writes task_1..task_11 to /tmp/oape-test) ==="
    python main.py --context-file example_scope.txt --output-dir /tmp/oape-test
    echo "Outputs in: /tmp/oape-test"
    ;;
  apply)
    REPO_PATH="${REPO_PATH:-}"
    if [[ -z "$REPO_PATH" ]]; then
      echo "Usage: REPO_PATH=/path/to/operator-repo $0 apply"
      echo "  Creates branch, writes code from SQE/SSE tasks, runs go build, commits."
      exit 1
    fi
    echo "=== Test with --apply-to-repo (branch + code + compile + commit) ==="
    python main.py --context-file example_scope.txt --repo-path "$REPO_PATH" --apply-to-repo
    ;;
  *)
    echo "Usage: $0 {smoke|context|output-dir|apply}"
    echo "  smoke       - default scope, 11 tasks (fastest)"
    echo "  context     - example_scope.txt"
    echo "  output-dir  - write task outputs to /tmp/oape-test"
    echo "  apply       - apply to repo (set REPO_PATH=/path/to/operator-repo)"
    exit 1
    ;;
esac
