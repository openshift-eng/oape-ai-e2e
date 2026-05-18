"""Standalone worker entrypoint for K8s Job execution.

Reads EP_URL, REPO, BASE_BRANCH from environment variables and runs the full
operator feature development workflow. All output is printed to stdout
as JSON (one message per line), which the orchestrator streams as pod logs.
"""

import asyncio
import json
import os
import sys

from rich import print_json

from agent import run_workflow
from ci_monitor import run_ci_monitor


async def main():
    workflow_type = os.environ.get("WORKFLOW_TYPE", "")

    if workflow_type == "ci-monitor":
        pr_urls_raw = os.environ.get("PR_URLS", "")
        if not pr_urls_raw:
            print("ERROR: PR_URLS environment variable is required for ci-monitor", file=sys.stderr)
            sys.exit(1)

        pr_urls = pr_urls_raw.split()
        print(f"Starting ci-monitor: pr_urls={pr_urls}", flush=True)

        result = await run_ci_monitor(pr_urls, on_message=lambda msg: print_json(data=msg))

        if result.success:
            print(f"CI_MONITOR_SUCCESS cost=${result.cost_usd:.4f}", flush=True)
            sys.exit(0)
        else:
            print(f"CI_MONITOR_FAILED: {result.error}", file=sys.stderr, flush=True)
            sys.exit(1)
    else:
        ep_url = os.environ.get("EP_URL")
        repo = os.environ.get("REPO_URL")
        base_branch = os.environ.get("BASE_BRANCH")

        if not ep_url or not repo or not base_branch:
            print("ERROR: EP_URL, REPO_URL, BASE_BRANCH environment variables are required", file=sys.stderr)
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
