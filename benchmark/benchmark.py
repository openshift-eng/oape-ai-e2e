#!/usr/bin/env python3
"""OAPE Benchmark Pipeline CLI.

Two-phase approach:
  Phase 1 (measure): Run all EPs with original tool, collect baselines
  Phase 2 (improve): Analyze all results, make ONE set of concise improvements
  Phase 3 (verify):  Run all EPs again with improved tool to confirm improvements

Or use `full` to run all three phases in sequence.
"""

import argparse
import asyncio
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from compare import compare_all_iterations, compare_iteration
from ground_truth import extract_combined_truth
from isolate import prepare_generation_env, resolve_pr_timeline
from models import BenchmarkCase, BenchmarkConfig, BenchmarkResult, IterationScore
from report import generate_aggregate_report, generate_ep_report
from runner import run_single_iteration, improve_tool_from_all_results, PLUGIN_DIR, TOOL_FILES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("benchmark")


def load_config(config_path: str) -> BenchmarkConfig:
    with open(config_path) as f:
        raw = yaml.safe_load(f)

    cases = []
    for entry in raw.get("benchmark_cases", []):
        cases.append(BenchmarkCase(
            ep_url=entry["ep_url"],
            repo_url=entry["repo_url"],
            description=entry.get("description", ""),
            implementation_prs=entry["implementation_prs"],
        ))

    settings = raw.get("settings", {})

    return BenchmarkConfig(
        cases=cases,
        tools_to_benchmark=settings.get("tools_to_benchmark", ["api-generate", "api-implement"]),
        iterations=settings.get("iterations", 1),
        output_dir=settings.get("output_dir", "benchmark/results"),
        parallel=settings.get("parallel", False),
        model=settings.get("model", "claude-opus-4-6"),
        effort=settings.get("effort", "max"),
    )


def _ep_num(ep_url: str) -> str:
    return ep_url.rstrip("/").split("/")[-1]


def _repo_name(repo_url: str) -> str:
    return repo_url.rstrip("/").split("/")[-1].removesuffix(".git")


async def run_single_ep(
    case: BenchmarkCase,
    config: BenchmarkConfig,
    output_dir: Path,
    phase_label: str = "measure",
    force: bool = False,
) -> BenchmarkResult | None:
    """Run the OAPE tool once on a single EP and score it."""
    ep_num = _ep_num(case.ep_url)
    repo_name = _repo_name(case.repo_url)
    result_dir = output_dir / repo_name / f"ep-{ep_num}"

    if result_dir.exists() and not force:
        report_json = result_dir / "report.json"
        if report_json.exists():
            logger.info("Skipping EP #%s (already completed; use --force to re-run)", ep_num)
            return None

    logger.info("=" * 60)
    logger.info("[%s] EP #%s - %s", phase_label.upper(), ep_num, case.description)
    logger.info("Repo: %s", case.repo_url)
    logger.info("Implementation PRs: %s", case.implementation_prs)
    logger.info("Model: %s | Effort: %s", config.model, config.effort)
    logger.info("=" * 60)

    logger.info("Resolving PR timeline...")
    timeline = resolve_pr_timeline(case.repo_url, case.implementation_prs)
    logger.info(
        "Timeline: baseline=%s, final=%s, %d files changed",
        timeline.earliest_parent_sha[:12],
        timeline.latest_merge_sha[:12],
        len(timeline.all_changed_files),
    )

    logger.info("Extracting ground truth...")
    truth = extract_combined_truth(case.repo_url, timeline)
    logger.info(
        "Ground truth: %d added, %d modified files",
        len(truth.files_added), len(truth.files_modified),
    )

    logger.info("Preparing isolated environment...")
    source_env = prepare_generation_env(case.repo_url, timeline)

    logger.info("Running OAPE tools...")
    gen_result = await run_single_iteration(
        ep_url=case.ep_url,
        source_env=source_env,
        iteration=1,
        plugin_dir=PLUGIN_DIR,
        model=config.model,
        effort=config.effort,
    )

    score, outperf = compare_iteration(gen_result, truth)
    logger.info(
        "Results: completeness=%.1f%% convention=%.1f%% build=%s matched=%d missed=%d wrong=%d extras=%d",
        score.completeness, score.convention_compliance, score.build_success,
        score.files_matched, score.files_missed, score.genuinely_wrong, score.valuable_extras,
    )

    benchmark_result = compare_all_iterations(
        gen_results=[gen_result],
        truth=truth,
        ep_url=case.ep_url,
        repo_url=case.repo_url,
        description=case.description,
        implementation_prs=case.implementation_prs,
    )
    benchmark_result.mode = phase_label

    report_path = generate_ep_report(
        result=benchmark_result,
        gen_results=[gen_result],
        truth=truth,
        output_dir=output_dir,
    )
    logger.info("Report: %s", report_path)

    shutil.rmtree(source_env.path, ignore_errors=True)
    return benchmark_result


async def cmd_measure(args: argparse.Namespace) -> None:
    """Phase 1: Run all EPs with original tool."""
    config = load_config(args.config)
    output_dir = Path(config.output_dir) / "phase1-measure"
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[BenchmarkResult] = []
    for i, case in enumerate(config.cases, 1):
        logger.info("\n>>> EP %d of %d <<<", i, len(config.cases))
        result = await run_single_ep(case, config, output_dir, "measure", force=args.force)
        if result:
            results.append(result)

    if results:
        logger.info("\n" + "=" * 60)
        logger.info("PHASE 1 COMPLETE: %d EPs measured", len(results))
        for r in results:
            best = r.iteration_scores[0] if r.iteration_scores else None
            logger.info(
                "  EP #%s: completeness=%.1f%% wrong=%d extras=%d",
                _ep_num(r.ep_url), r.median_completeness,
                best.genuinely_wrong if best else 0, best.valuable_extras if best else 0,
            )
        logger.info("=" * 60)

        if len(results) > 1:
            generate_aggregate_report(results, output_dir)


async def cmd_improve(args: argparse.Namespace) -> None:
    """Phase 2: Analyze all results and make ONE set of concise improvements."""
    config = load_config(args.config)
    measure_dir = Path(config.output_dir) / "phase1-measure"

    if not measure_dir.exists():
        logger.error("No phase1-measure results found. Run `measure` first.")
        sys.exit(1)

    results_data: list[dict] = []
    for report_json in measure_dir.rglob("report.json"):
        with open(report_json) as f:
            results_data.append(json.load(f))

    if not results_data:
        logger.error("No report.json files found in %s", measure_dir)
        sys.exit(1)

    truth_data: dict[str, dict] = {}
    for rd in results_data:
        ep_num = _ep_num(rd["ep_url"])
        repo_name = _repo_name(rd["repo_url"])
        truth_dir = measure_dir / repo_name / f"ep-{ep_num}" / "truth"
        if truth_dir.exists():
            combined_diff = (truth_dir / "combined.diff").read_text() if (truth_dir / "combined.diff").exists() else ""
            truth_data[ep_num] = {"diff_preview": combined_diff[:5000]}

    iter_diffs: dict[str, str] = {}
    for rd in results_data:
        ep_num = _ep_num(rd["ep_url"])
        repo_name = _repo_name(rd["repo_url"])
        iter_dir = measure_dir / repo_name / f"ep-{ep_num}" / "iter-1"
        if iter_dir and (iter_dir / "diff.patch").exists():
            iter_diffs[ep_num] = (iter_dir / "diff.patch").read_text()[:5000]

    backup_dir = Path(config.output_dir) / "tool-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Analyzing %d EP results and improving tool...", len(results_data))
    improvement = await improve_tool_from_all_results(
        results_data=results_data,
        truth_data=truth_data,
        iter_diffs=iter_diffs,
        plugin_dir=PLUGIN_DIR,
        backup_dir=backup_dir,
        model=config.model,
        effort=config.effort,
    )

    if improvement.files_changed:
        logger.info("Tool improved: %d files changed", len(improvement.files_changed))
        for fname, diff in improvement.files_changed.items():
            added = sum(1 for l in diff.split("\n") if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in diff.split("\n") if l.startswith("-") and not l.startswith("---"))
            logger.info("  %s: +%d/-%d lines", fname, added, removed)
        logger.info("Cost: $%.2f", improvement.improvement_cost_usd)
    else:
        logger.info("No changes made to tool files.")


async def cmd_verify(args: argparse.Namespace) -> None:
    """Phase 3: Run all EPs again with improved tool."""
    config = load_config(args.config)
    output_dir = Path(config.output_dir) / "phase3-verify"
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[BenchmarkResult] = []
    for i, case in enumerate(config.cases, 1):
        logger.info("\n>>> EP %d of %d <<<", i, len(config.cases))
        result = await run_single_ep(case, config, output_dir, "verify", force=args.force)
        if result:
            results.append(result)

    if results:
        logger.info("\n" + "=" * 60)
        logger.info("PHASE 3 COMPLETE: %d EPs verified with improved tool", len(results))
        for r in results:
            best = r.iteration_scores[0] if r.iteration_scores else None
            logger.info(
                "  EP #%s: completeness=%.1f%% wrong=%d extras=%d",
                _ep_num(r.ep_url), r.median_completeness,
                best.genuinely_wrong if best else 0, best.valuable_extras if best else 0,
            )
        logger.info("=" * 60)

        if len(results) > 1:
            generate_aggregate_report(results, output_dir)


async def cmd_full(args: argparse.Namespace) -> None:
    """Run all three phases in sequence."""
    logger.info("=== PHASE 1: MEASURE (original tool) ===")
    await cmd_measure(args)

    logger.info("\n=== PHASE 2: IMPROVE (analyze and edit tool) ===")
    await cmd_improve(args)

    logger.info("\n=== PHASE 3: VERIFY (improved tool) ===")
    await cmd_verify(args)

    config = load_config(args.config)
    logger.info("\n=== FINAL: Restoring original tool files ===")
    backup_dir = Path(config.output_dir) / "tool-backups" / "original"
    if backup_dir.exists():
        for rel in TOOL_FILES:
            src = backup_dir / rel
            dst = Path(PLUGIN_DIR) / rel
            if src.exists():
                shutil.copy2(src, dst)
        logger.info("Original tool files restored. Improved versions saved in tool-backups/.")


def cmd_push(args: argparse.Namespace) -> None:
    results_dir = Path(args.results_dir or "benchmark/results")
    repo_name = args.repo.rstrip("/").split("/")[-1].removesuffix(".git")

    for phase in ["phase3-verify", "phase1-measure"]:
        iter_dir = results_dir / phase / repo_name / f"ep-{args.ep}" / "iter-1"
        if iter_dir.exists():
            break
    else:
        logger.error("No results found for EP #%s in %s", args.ep, results_dir)
        sys.exit(1)

    patch_file = iter_dir / "diff.patch"
    if not patch_file.exists() or patch_file.stat().st_size == 0:
        logger.error("No diff.patch found or patch is empty in %s", iter_dir)
        sys.exit(1)

    import tempfile
    work_dir = Path(tempfile.mkdtemp(prefix="oape-bench-push-"))

    logger.info("Cloning %s for PR creation...", args.repo)
    subprocess.run(["git", "clone", "--quiet", args.repo, str(work_dir)], check=True)
    subprocess.run(
        ["git", "checkout", "-b", f"oape-benchmark/ep-{args.ep}"],
        cwd=work_dir, check=True,
    )

    logger.info("Applying patch...")
    patch_content = patch_file.read_text()
    result = subprocess.run(
        ["git", "apply", "--verbose", "-"],
        input=patch_content, text=True, cwd=work_dir, capture_output=True,
    )
    if result.returncode != 0:
        logger.error("Failed to apply patch: %s", result.stderr)
        sys.exit(1)

    subprocess.run(["git", "add", "-A"], cwd=work_dir, check=True)
    subprocess.run(
        ["git", "commit", "-m", args.title or f"feat: OAPE generated implementation for EP-{args.ep}"],
        cwd=work_dir, check=True,
    )

    logger.info("Pushing branch...")
    subprocess.run(
        ["git", "push", "-u", "origin", f"oape-benchmark/ep-{args.ep}"],
        cwd=work_dir, check=True,
    )

    logger.info("Creating PR...")
    pr_result = subprocess.run(
        ["gh", "pr", "create",
         "--repo", args.repo,
         "--base", args.base_branch,
         "--title", args.title or f"feat: OAPE generated implementation for EP-{args.ep}",
         "--body", f"Generated by OAPE benchmark pipeline (EP #{args.ep})"],
        cwd=work_dir, capture_output=True, text=True,
    )
    if pr_result.returncode == 0:
        logger.info("PR created: %s", pr_result.stdout.strip())
    else:
        logger.error("Failed to create PR: %s", pr_result.stderr)

    shutil.rmtree(work_dir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OAPE Benchmark Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Workflow:
  1. measure  - Run all EPs with original tool, collect baselines
  2. improve  - Analyze all results, make ONE set of concise improvements
  3. verify   - Run all EPs again with improved tool
  Or: full    - Run all three phases in sequence
        """,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    measure_p = subparsers.add_parser("measure", help="Phase 1: Run all EPs with original tool")
    measure_p.add_argument("--config", default="benchmark/config.yaml")
    measure_p.add_argument("--force", action="store_true")

    improve_p = subparsers.add_parser("improve", help="Phase 2: Analyze results, improve tool once")
    improve_p.add_argument("--config", default="benchmark/config.yaml")

    verify_p = subparsers.add_parser("verify", help="Phase 3: Run all EPs with improved tool")
    verify_p.add_argument("--config", default="benchmark/config.yaml")
    verify_p.add_argument("--force", action="store_true")

    full_p = subparsers.add_parser("full", help="Run all three phases")
    full_p.add_argument("--config", default="benchmark/config.yaml")
    full_p.add_argument("--force", action="store_true")

    report_p = subparsers.add_parser("report", help="Generate aggregate report from results")
    report_p.add_argument("--results-dir", default="benchmark/results")

    push_p = subparsers.add_parser("push", help="Push results as a PR")
    push_p.add_argument("--ep", required=True)
    push_p.add_argument("--repo", required=True)
    push_p.add_argument("--base-branch", default="main")
    push_p.add_argument("--title")
    push_p.add_argument("--results-dir")

    args = parser.parse_args()

    if args.command == "measure":
        asyncio.run(cmd_measure(args))
    elif args.command == "improve":
        asyncio.run(cmd_improve(args))
    elif args.command == "verify":
        asyncio.run(cmd_verify(args))
    elif args.command == "full":
        asyncio.run(cmd_full(args))
    elif args.command == "report":
        asyncio.run(cmd_report(args))
    elif args.command == "push":
        cmd_push(args)


async def cmd_report(args: argparse.Namespace) -> None:
    results_dir = Path(args.results_dir)
    if not results_dir.exists():
        logger.error("Results directory not found: %s", results_dir)
        sys.exit(1)

    results: list[BenchmarkResult] = []
    for report_json in results_dir.rglob("report.json"):
        with open(report_json) as f:
            data = json.load(f)
        scores = [
            IterationScore(
                iteration=s["iteration"],
                completeness=s["completeness"],
                convention_compliance=s["convention_compliance"],
                build_success=s["build_success"],
                files_matched=s.get("files_matched", 0),
                files_missed=s.get("files_missed", 0),
                genuinely_wrong=s.get("genuinely_wrong", 0),
                valuable_extras=s.get("valuable_extras", 0),
                auto_generated=s.get("auto_generated", 0),
            )
            for s in data.get("iteration_scores", [])
        ]
        results.append(BenchmarkResult(
            ep_url=data["ep_url"],
            repo_url=data["repo_url"],
            description=data.get("description", ""),
            implementation_prs=data.get("implementation_prs", []),
            iteration_scores=scores,
            median_completeness=data.get("median_completeness", 0),
            best_iteration=data.get("best_iteration", 0),
            score_variance=data.get("score_variance", {}),
        ))

    if not results:
        logger.error("No results found in %s", results_dir)
        sys.exit(1)

    generate_aggregate_report(results, results_dir)


if __name__ == "__main__":
    main()
