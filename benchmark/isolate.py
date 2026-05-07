"""Bias-free isolation engine for benchmark runs.

Resolves multi-PR timelines and creates sealed generation environments
where the EP implementation code does not exist.
"""

import json
import logging
import subprocess
import tempfile
from pathlib import Path

from models import IsolatedEnv, PRDetail, PRTimeline

logger = logging.getLogger(__name__)


def _run(cmd: list[str], retries: int = 3, **kwargs) -> subprocess.CompletedProcess:
    logger.debug("Running: %s", " ".join(cmd))
    for attempt in range(retries):
        result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
        if result.returncode == 0:
            return result
        if attempt < retries - 1:
            import time
            wait = 5 * (attempt + 1)
            logger.warning("Command failed (attempt %d/%d), retrying in %ds: %s",
                           attempt + 1, retries, wait, result.stderr[:200])
            time.sleep(wait)
    result.check_returncode()
    return result


def _gh_json(args: list[str]) -> dict | list:
    result = _run(["gh"] + args)
    return json.loads(result.stdout)


def _extract_repo_nwo(repo_url: str) -> str:
    """Extract owner/repo from a GitHub URL."""
    url = repo_url.rstrip("/").removesuffix(".git")
    parts = url.split("/")
    return f"{parts[-2]}/{parts[-1]}"


def resolve_pr_timeline(repo_url: str, pr_numbers: list[int]) -> PRTimeline:
    """Fetch merge info for each PR and build a sorted timeline.

    Returns the earliest parent SHA (pre-EP baseline) and latest merge SHA
    (complete implementation), plus all changed files.
    """
    nwo = _extract_repo_nwo(repo_url)
    details: list[PRDetail] = []

    for pr_num in pr_numbers:
        logger.info("Fetching PR #%d from %s...", pr_num, nwo)

        pr_data = _gh_json([
            "pr", "view", "--repo", nwo, str(pr_num),
            "--json", "mergeCommit,files,mergedAt",
        ])

        merge_sha = pr_data["mergeCommit"]["oid"]
        merged_at = pr_data["mergedAt"]
        files = [f["path"] for f in pr_data.get("files", [])]

        commit_data = _gh_json([
            "api", f"repos/{nwo}/commits/{merge_sha}",
        ])
        parent_sha = commit_data["parents"][0]["sha"]

        details.append(PRDetail(
            number=pr_num,
            merge_sha=merge_sha,
            parent_sha=parent_sha,
            merged_at=merged_at,
            files=files,
        ))

    details.sort(key=lambda d: d.merged_at)

    all_files = []
    seen = set()
    for d in details:
        for f in d.files:
            if f not in seen:
                all_files.append(f)
                seen.add(f)

    return PRTimeline(
        earliest_parent_sha=details[0].parent_sha,
        latest_merge_sha=details[-1].merge_sha,
        all_changed_files=all_files,
        pr_details=details,
    )


def prepare_generation_env(repo_url: str, timeline: PRTimeline) -> IsolatedEnv:
    """Clone the repo at the pre-EP baseline commit in an isolated temp dir.

    Runs verification checks to ensure no EP code is present.
    """
    gen_dir = Path(tempfile.mkdtemp(prefix="oape-bench-gen-"))
    baseline = timeline.earliest_parent_sha

    logger.info("Cloning %s at %s into %s", repo_url, baseline[:12], gen_dir)
    _run(["git", "clone", "--quiet", repo_url, str(gen_dir)])
    _run(["git", "checkout", "--quiet", baseline], cwd=gen_dir)

    warnings: list[str] = []

    merge_shas = {d.merge_sha for d in timeline.pr_details}
    try:
        log_result = _run(["git", "log", "--oneline", "--format=%H"], cwd=gen_dir)
        commit_history = set(log_result.stdout.strip().split("\n"))
        leaked = merge_shas & commit_history
        if leaked:
            msg = f"BIAS WARNING: merge commits found in history: {leaked}"
            logger.warning(msg)
            warnings.append(msg)
    except subprocess.CalledProcessError:
        warnings.append("Could not verify git log for merge commit absence")

    for fpath in timeline.all_changed_files:
        full = gen_dir / fpath
        if not full.exists():
            continue
        if fpath.endswith("_types.go") or "types_" in fpath:
            logger.info("Baseline file exists (expected for modifications): %s", fpath)

    logger.info("Isolated environment ready at %s (baseline %s)", gen_dir, baseline[:12])
    return IsolatedEnv(
        path=gen_dir,
        baseline_sha=baseline,
        all_changed_files=timeline.all_changed_files,
        warnings=warnings,
    )
