"""Standalone worker entrypoint for K8s Job execution.

Reads EP_URL, REPO, BASE_BRANCH from environment variables and runs the full
operator feature development workflow. All output is printed to stdout
as JSON (one message per line), which the orchestrator streams as pod logs.
"""

import asyncio
import os
import sys

from rich import print_json

from phases import run_workflow


async def main():
    ep_url = os.environ.get("EP_URL")
    repo = os.environ.get("REPO_URL")
    base_branch = os.environ.get("BASE_BRANCH")

    if not ep_url or not repo or not base_branch:
        print("ERROR: EP_URL, REPO, BASE_BRANCH environment variables are required", file=sys.stderr)
        sys.exit(1)

    print(f"Starting workflow: ep_url={ep_url} repo={repo}", flush=True)

    result = await run_workflow(ep_url, repo, base_branch, on_message=lambda msg: print_json(data=msg))

    if result.success:
        print(f"WORKFLOW_SUCCESS cost=${result.cost_usd:.4f}", flush=True)
        for pr in result.prs:
            print(f"PR_CREATED: {pr.pr_url}", flush=True)
        sys.exit(0)
    else:
        print(f"WORKFLOW_FAILED: {result.error}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
