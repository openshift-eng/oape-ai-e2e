#!/usr/bin/env python3
"""OAPE Benchmark Pipeline CLI.

Runs OAPE tools against curated EP-to-implementation mappings,
compares generated output vs real merged code, and reports quality.
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

from compare import compare_all_iterations
from ground_truth import extract_combined_truth
from isolate import prepare_generation_env, resolve_pr_timeline
from models import BenchmarkCase, BenchmarkConfig, BenchmarkResult
from report import generate_aggregate_report, generate_ep_report
from runner import run_feedback_loop, PLUGIN_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("benchmark")


def load_config(config_path: str, iterations_override: int | None = None) -> BenchmarkConfig:
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
    iterations = iterations_override or settings.get("iterations", 3)

    return BenchmarkConfig(
        cases=cases,
        tools_to_benchmark=settings.get("tools_to_benchmark", ["api-generate", "api-implement"]),
        iterations=iterations,
        output_dir=settings.get("output_dir", "benchmark/results"),
        parallel=settings.get("parallel", False),
        model=settings.get("model", "claude-opus-4-6"),
        effort=settings.get("effort", "max"),
    )


async def run_single_case(
    case: BenchmarkCase,
    config: BenchmarkConfig,
    output_dir: Path,
    force: bool = False,
) -> BenchmarkResult | None:
    """Run the benchmark pipeline for a single EP case."""
    ep_num = case.ep_url.rstrip("/").split("/")[-1]
    repo_name = case.repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    result_dir = output_dir / repo_name / f"ep-{ep_num}"

    if result_dir.exists() and not force:
        report_json = result_dir / "report.json"
        if report_json.exists():
            logger.info("Skipping EP #%s (already completed; use --force to re-run)", ep_num)
            return None

    logger.info("=" * 60)
    logger.info("FEEDBACK LOOP BENCHMARK: EP #%s - %s", ep_num, case.description)
    logger.info("Repo: %s", case.repo_url)
    logger.info("Implementation PRs: %s", case.implementation_prs)
    logger.info("Iterations: %d (with tool improvement between each)", config.iterations)
    logger.info("Model: %s | Effort: %s", config.model, config.effort)
    logger.info("=" * 60)

    logger.info("Step 1: Resolving PR timeline...")
    timeline = resolve_pr_timeline(case.repo_url, case.implementation_prs)
    logger.info(
        "Timeline: baseline=%s, final=%s, %d files changed",
        timeline.earliest_parent_sha[:12],
        timeline.latest_merge_sha[:12],
        len(timeline.all_changed_files),
    )

    logger.info("Step 2: Extracting ground truth...")
    truth = extract_combined_truth(case.repo_url, timeline)
    logger.info(
        "Ground truth: %d added, %d modified files",
        len(truth.files_added), len(truth.files_modified),
    )

    logger.info("Step 3: Preparing isolated generation environment...")
    source_env = prepare_generation_env(case.repo_url, timeline)
    if source_env.warnings:
        for w in source_env.warnings:
            logger.warning(w)

    backup_dir = output_dir / repo_name / f"ep-{ep_num}" / "tool-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Step 4: Running feedback loop (%d iterations)...", config.iterations)
    gen_results, improvements = await run_feedback_loop(
        ep_url=case.ep_url,
        source_env=source_env,
        truth=truth,
        num_iterations=config.iterations,
        plugin_dir=PLUGIN_DIR,
        backup_dir=backup_dir,
        model=config.model,
        effort=config.effort,
    )

    logger.info("Step 5: Final comparison across all iterations...")
    benchmark_result = compare_all_iterations(
        gen_results=gen_results,
        truth=truth,
        ep_url=case.ep_url,
        repo_url=case.repo_url,
        description=case.description,
        implementation_prs=case.implementation_prs,
    )
    benchmark_result.tool_improvements = improvements
    benchmark_result.mode = "feedback_loop"

    logger.info("Step 6: Generating report...")
    report_path = generate_ep_report(
        result=benchmark_result,
        gen_results=gen_results,
        truth=truth,
        output_dir=output_dir,
    )

    logger.info("Report: %s", report_path)
    logger.info("=" * 40)
    logger.info("SCORE PROGRESSION:")
    for s in benchmark_result.iteration_scores:
        tool_ver = gen_results[s.iteration - 1].tool_version if s.iteration <= len(gen_results) else "?"
        logger.info(
            "  Iter %d (%s): completeness=%.1f%% convention=%.1f%% build=%s wrong=%d extras=%d",
            s.iteration, tool_ver, s.completeness, s.convention_compliance,
            s.build_success, s.genuinely_wrong, s.valuable_extras,
        )
    total_improve_cost = sum(imp.improvement_cost_usd for imp in improvements)
    total_gen_cost = sum(g.cost_usd for g in gen_results)
    logger.info("Total cost: generation=$%.2f + improvement=$%.2f = $%.2f",
                total_gen_cost, total_improve_cost, total_gen_cost + total_improve_cost)
    logger.info("=" * 40)

    if benchmark_result.outperformance_findings:
        logger.info(
            "Tool outperformed human in %d cases",
            len(benchmark_result.outperformance_findings),
        )

    shutil.rmtree(source_env.path, ignore_errors=True)

    return benchmark_result


async def cmd_run(args: argparse.Namespace) -> None:
    config = load_config(args.config, args.iterations)
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[BenchmarkResult] = []

    for case in config.cases:
        result = await run_single_case(case, config, output_dir, force=args.force)
        if result:
            results.append(result)

    if results:
        logger.info("\n" + "=" * 60)
        logger.info("ALL BENCHMARKS COMPLETE")
        for r in results:
            ep_num = r.ep_url.rstrip("/").split("/")[-1]
            logger.info(
                "  EP #%s: completeness=%.1f%%",
                ep_num, r.median_completeness,
            )
        logger.info("=" * 60)

        if len(results) > 1:
            agg_path = generate_aggregate_report(results, output_dir)
            logger.info("Aggregate report: %s", agg_path)


async def cmd_report(args: argparse.Namespace) -> None:
    results_dir = Path(args.results_dir)
    if not results_dir.exists():
        logger.error("Results directory not found: %s", results_dir)
        sys.exit(1)

    results: list[BenchmarkResult] = []
    for report_json in results_dir.rglob("report.json"):
        with open(report_json) as f:
            data = json.load(f)
        from models import IterationScore
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
        logger.error("No benchmark results found in %s", results_dir)
        sys.exit(1)

    agg_path = generate_aggregate_report(results, results_dir)
    logger.info("Aggregate report: %s", agg_path)


def cmd_push(args: argparse.Namespace) -> None:
    results_dir = Path(args.results_dir or "benchmark/results")
    repo_name = args.repo.rstrip("/").split("/")[-1].removesuffix(".git")
    iter_dir = results_dir / repo_name / f"ep-{args.ep}" / f"iter-{args.iteration}"

    if not iter_dir.exists():
        logger.error("Iteration directory not found: %s", iter_dir)
        sys.exit(1)

    patch_file = iter_dir / "diff.patch"
    if not patch_file.exists() or patch_file.stat().st_size == 0:
        logger.error("No diff.patch found or patch is empty in %s", iter_dir)
        sys.exit(1)

    gen_dir = iter_dir / "generated"

    import tempfile
    work_dir = Path(tempfile.mkdtemp(prefix="oape-bench-push-"))

    logger.info("Cloning %s for PR creation...", args.repo)
    subprocess.run(
        ["git", "clone", "--quiet", args.repo, str(work_dir)],
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", f"oape-benchmark/ep-{args.ep}-iter{args.iteration}"],
        cwd=work_dir, check=True,
    )

    logger.info("Applying patch from iteration %d...", args.iteration)
    patch_content = patch_file.read_text()
    result = subprocess.run(
        ["git", "apply", "--verbose", "-"],
        input=patch_content, text=True,
        cwd=work_dir, capture_output=True,
    )
    if result.returncode != 0:
        logger.error("Failed to apply patch: %s", result.stderr)
        sys.exit(1)

    subprocess.run(["git", "add", "-A"], cwd=work_dir, check=True)
    subprocess.run(
        ["git", "commit", "-m", args.title or f"feat: OAPE benchmark EP-{args.ep} iteration {args.iteration}"],
        cwd=work_dir, check=True,
    )

    logger.info("Pushing branch...")
    subprocess.run(
        ["git", "push", "-u", "origin", f"oape-benchmark/ep-{args.ep}-iter{args.iteration}"],
        cwd=work_dir, check=True,
    )

    logger.info("Creating PR...")
    pr_result = subprocess.run(
        ["gh", "pr", "create",
         "--repo", args.repo,
         "--base", args.base_branch,
         "--title", args.title or f"feat: OAPE benchmark EP-{args.ep} iteration {args.iteration}",
         "--body", f"Generated by OAPE benchmark pipeline (EP #{args.ep}, iteration {args.iteration})"],
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
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run benchmark cases")
    run_parser.add_argument("--config", default="benchmark/config.yaml", help="Config YAML file")
    run_parser.add_argument("--iterations", type=int, help="Override iteration count")
    run_parser.add_argument("--force", action="store_true", help="Re-run completed cases")
    run_parser.add_argument("--ep-url", help="Single EP URL (ad-hoc mode)")
    run_parser.add_argument("--repo", help="Repo URL (ad-hoc mode)")
    run_parser.add_argument("--prs", help="Comma-separated PR numbers (ad-hoc mode)")

    report_parser = subparsers.add_parser("report", help="Generate aggregate report")
    report_parser.add_argument("--results-dir", default="benchmark/results", help="Results directory")

    full_parser = subparsers.add_parser("full", help="Run benchmarks + generate aggregate report")
    full_parser.add_argument("--config", default="benchmark/config.yaml", help="Config YAML file")
    full_parser.add_argument("--iterations", type=int, help="Override iteration count")
    full_parser.add_argument("--force", action="store_true", help="Re-run completed cases")

    push_parser = subparsers.add_parser("push", help="Push an iteration as a PR")
    push_parser.add_argument("--ep", required=True, help="EP number")
    push_parser.add_argument("--repo", required=True, help="Repo URL")
    push_parser.add_argument("--iteration", type=int, required=True, help="Iteration number")
    push_parser.add_argument("--base-branch", default="main", help="Base branch for PR")
    push_parser.add_argument("--title", help="PR title")
    push_parser.add_argument("--results-dir", help="Results directory")

    args = parser.parse_args()

    if args.command == "run":
        if args.ep_url and args.repo and args.prs:
            prs = [int(p.strip()) for p in args.prs.split(",")]
            config_dict = {
                "benchmark_cases": [{
                    "ep_url": args.ep_url,
                    "repo_url": args.repo,
                    "description": "Ad-hoc benchmark",
                    "implementation_prs": prs,
                }],
                "settings": {
                    "iterations": args.iterations or 3,
                    "output_dir": "benchmark/results",
                },
            }
            import tempfile
            tmp = Path(tempfile.mktemp(suffix=".yaml"))
            tmp.write_text(yaml.dump(config_dict))
            args.config = str(tmp)

        asyncio.run(cmd_run(args))

    elif args.command == "report":
        asyncio.run(cmd_report(args))

    elif args.command == "full":
        asyncio.run(cmd_run(args))
        args_report = argparse.Namespace(results_dir=load_config(args.config).output_dir)
        asyncio.run(cmd_report(args_report))

    elif args.command == "push":
        cmd_push(args)


if __name__ == "__main__":
    main()
