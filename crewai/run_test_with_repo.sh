#!/usr/bin/env bash
# Run OAPE CrewAI workflow with ZTWIM scope and local repo path (so SSE uses real paths).
# Ensure ztwim-repo is cloned (from workspace root: ./scripts/clone_ztwim_repo.sh).

set -e
CREWAI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# ztwim-repo is at multi-agent-orchestration/ztwim-repo; from oape-ai-e2e/crewai that's ../../ztwim-repo
REPO_PATH="${REPO_PATH:-${CREWAI_DIR}/../../ztwim-repo}"

cd "$CREWAI_DIR"
if [[ ! -d "$REPO_PATH" ]]; then
  echo "Repo not found: $REPO_PATH"
  echo "Clone it from workspace root: ./scripts/clone_ztwim_repo.sh"
  exit 1
fi
echo "Using repo path: $REPO_PATH"
python main.py --context-file scope_ztwim_upstream_authority.txt --repo-path "$REPO_PATH" "$@"
