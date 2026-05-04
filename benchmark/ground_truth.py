"""Ground truth extraction from merged implementation PRs.

Clones the repo at the final merge state and extracts the combined diff
across all implementation PRs relative to the pre-EP baseline.
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from models import GroundTruth, PRTimeline

logger = logging.getLogger(__name__)


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kwargs)


def extract_combined_truth(repo_url: str, timeline: PRTimeline) -> GroundTruth:
    """Extract the combined ground truth from all implementation PRs.

    Clones the repo into a separate temp directory, computes
    `git diff earliest_parent..latest_merge`, and categorises files.
    """
    truth_dir = Path(tempfile.mkdtemp(prefix="oape-bench-truth-"))
    output_dir = Path(tempfile.mkdtemp(prefix="oape-bench-truth-files-"))

    baseline = timeline.earliest_parent_sha
    final = timeline.latest_merge_sha

    logger.info("Cloning %s for ground truth extraction...", repo_url)
    _run(["git", "clone", "--quiet", repo_url, str(truth_dir)])

    logger.info("Computing combined diff %s..%s", baseline[:12], final[:12])
    diff_result = _run(
        ["git", "diff", f"{baseline}..{final}"],
        cwd=truth_dir,
    )
    combined_diff = diff_result.stdout

    stat_result = _run(
        ["git", "diff", "--name-status", f"{baseline}..{final}"],
        cwd=truth_dir,
    )

    files_added: list[str] = []
    files_modified: list[str] = []

    for line in stat_result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) < 2:
            continue
        status, filepath = parts[0].strip(), parts[1].strip()
        if status == "A":
            files_added.append(filepath)
        elif status in ("M", "R", "C"):
            files_modified.append(filepath)

    _run(["git", "checkout", "--quiet", final], cwd=truth_dir)
    diff_hunks: dict[str, str] = {}

    for fpath in files_added + files_modified:
        src = truth_dir / fpath
        if src.exists():
            dst = output_dir / fpath
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        try:
            hunk_result = _run(
                ["git", "diff", f"{baseline}..{final}", "--", fpath],
                cwd=truth_dir,
            )
            diff_hunks[fpath] = hunk_result.stdout
        except subprocess.CalledProcessError:
            logger.warning("Could not extract diff hunks for %s", fpath)

    shutil.rmtree(truth_dir, ignore_errors=True)

    logger.info(
        "Ground truth extracted: %d added, %d modified files",
        len(files_added), len(files_modified),
    )
    return GroundTruth(
        path=output_dir,
        combined_diff=combined_diff,
        files_added=files_added,
        files_modified=files_modified,
        diff_hunks=diff_hunks,
    )
