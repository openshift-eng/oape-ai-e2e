"""
Run the OAPE CrewAI workflow (project-agnostic).

Context can be set via:
- Env: OAPE_PROJECT_NAME, OAPE_REPO_URL, OAPE_SCOPE_DESCRIPTION
- CLI: --project-name, --repo-url, --scope
- Context file: --context-file path/to/scope.txt (optional PROJECT_NAME=, REPO_URL=, then body)
- GitHub EP: --ep-url https://github.com/openshift/enhancements/pull/NNNN (fetches PR body)
Skills are loaded from plugins/oape/skills/*/SKILL.md automatically.

To persist outputs in the repo after tasks complete, use --output-dir (or OAPE_OUTPUT_DIR).
Outputs written: customer_doc.md and task_1_design.md through task_9_customer_doc.md.
"""

import argparse
import os
import sys
from pathlib import Path

# Ensure crewai dir is on path so that context, agents, tasks, adapters resolve
_CREWAI_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _CREWAI_DIR.parent
if str(_CREWAI_DIR) not in sys.path:
    sys.path.insert(0, str(_CREWAI_DIR))

from dotenv import load_dotenv

from adapters import get_adapter
from context import (
    ProjectScope,
    default_scope,
    get_repo_layout,
    load_context_from_file,
    load_context_from_ep_url,
)

# Load .env from oape-ai-e2e and from workspace root (multi-agent-orchestration) so CREWAI_TRACING_ENABLED etc. match the ZTWIM POC
load_dotenv(_REPO_ROOT / ".env")
_workspace_root = _REPO_ROOT.parent
load_dotenv(_workspace_root / ".env")
load_dotenv()


def _scope_from_env() -> ProjectScope:
    name = os.getenv("OAPE_PROJECT_NAME", "").strip()
    repo = os.getenv("OAPE_REPO_URL", "").strip()
    desc = os.getenv("OAPE_SCOPE_DESCRIPTION", "").strip()
    extra = os.getenv("OAPE_EXTRA_CONTEXT", "").strip() or None
    if name and repo and desc:
        return ProjectScope(
            project_name=name,
            repo_url=repo,
            scope_description=desc,
            extra_context=extra,
        )
    return default_scope()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run OAPE CrewAI workflow. Context from env, CLI, --context-file, or --ep-url."
    )
    parser.add_argument("--project-name", type=str, help="Project name (or OAPE_PROJECT_NAME)")
    parser.add_argument("--repo-url", type=str, help="Repository URL (or OAPE_REPO_URL)")
    parser.add_argument("--scope", type=str, help="Scope description (or OAPE_SCOPE_DESCRIPTION)")
    parser.add_argument("--extra", type=str, default=None, help="Extra context (or OAPE_EXTRA_CONTEXT)")
    parser.add_argument(
        "--context-file",
        type=str,
        metavar="PATH",
        help="Load scope from a .txt file. Optional headers: PROJECT_NAME=..., REPO_URL=..., then --- or blank line, then body.",
    )
    parser.add_argument(
        "--ep-url",
        type=str,
        metavar="URL",
        help="Load context from GitHub EP PR, e.g. https://github.com/openshift/enhancements/pull/1234 (requires gh CLI). Required for claude-sdk backend.",
    )
    parser.add_argument(
        "--backend",
        type=str,
        choices=["crewai", "claude-sdk"],
        default=os.getenv("OAPE_BACKEND", "crewai"),
        help="Backend to run: crewai (full 9-task doc workflow) or claude-sdk (server api-implement). Default: OAPE_BACKEND or crewai.",
    )
    parser.add_argument(
        "--repo-path",
        type=str,
        metavar="PATH",
        help="Path to local repo clone; layout is injected so agents suggest only existing paths (or OAPE_REPO_PATH / OAPE_OPERATOR_CWD).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        metavar="PATH",
        help="Write workflow outputs to this directory (creates dir if needed). E.g. path inside repo to see changes: --output-dir /path/to/repo/docs/oape. Env: OAPE_OUTPUT_DIR.",
    )
    parser.add_argument(
        "--apply-to-repo",
        action="store_true",
        help="After workflow success: create a git branch in the repo, write generated code, verify compile, and commit. Default: True when --repo-path is set (crewai backend).",
    )
    parser.add_argument(
        "--no-apply-to-repo",
        action="store_true",
        help="Do not apply code to the repo even when --repo-path is set (overrides default).",
    )
    parser.add_argument(
        "--branch-name",
        type=str,
        metavar="NAME",
        help="Git branch name for --apply-to-repo (default: oape/<project>-<date>).",
    )
    args = parser.parse_args()

    # When --repo-path is set (crewai), apply code to that repo by default so code changes go to the same path given
    if getattr(args, "no_apply_to_repo", False):
        args.apply_to_repo = False
    elif args.repo_path and args.backend == "crewai":
        args.apply_to_repo = True

    # Env fallbacks for context file and EP URL
    if not args.context_file and os.getenv("OAPE_CONTEXT_FILE", "").strip():
        args.context_file = os.getenv("OAPE_CONTEXT_FILE", "").strip()
    if not args.ep_url and os.getenv("OAPE_EP_URL", "").strip():
        args.ep_url = os.getenv("OAPE_EP_URL", "").strip()

    scope = _scope_from_env()
    extra_parts = [(scope.extra_context or "").strip()] if scope.extra_context else []

    # Context file: can set project_name, repo_url, and/or scope description
    if args.context_file:
        pname, repourl, desc = load_context_from_file(args.context_file)
        if pname is not None:
            scope = ProjectScope(project_name=pname, repo_url=scope.repo_url, scope_description=scope.scope_description, extra_context=scope.extra_context)
        if repourl is not None:
            scope = ProjectScope(project_name=scope.project_name, repo_url=repourl, scope_description=scope.scope_description, extra_context=scope.extra_context)
        if desc:
            scope = ProjectScope(project_name=scope.project_name, repo_url=scope.repo_url, scope_description=desc, extra_context=scope.extra_context)

    # EP URL: fetch PR body and add to extra context
    if args.ep_url:
        ep_content = load_context_from_ep_url(args.ep_url)
        if ep_content:
            extra_parts.append(f"**Enhancement Proposal ({args.ep_url}):**\n\n{ep_content}")
        else:
            extra_parts.append(f"Enhancement Proposal URL (content could not be fetched; ensure gh CLI is installed and authenticated): {args.ep_url}")

    if extra_parts and any(extra_parts):
        scope = ProjectScope(
            project_name=scope.project_name,
            repo_url=scope.repo_url,
            scope_description=scope.scope_description,
            extra_context="\n\n".join(p for p in extra_parts if p),
        )

    # CLI overrides (highest priority)
    if args.project_name:
        scope = ProjectScope(project_name=args.project_name, repo_url=scope.repo_url, scope_description=scope.scope_description, extra_context=scope.extra_context)
    if args.repo_url:
        scope = ProjectScope(project_name=scope.project_name, repo_url=args.repo_url, scope_description=scope.scope_description, extra_context=scope.extra_context)
    if args.scope:
        scope = ProjectScope(project_name=scope.project_name, repo_url=scope.repo_url, scope_description=args.scope, extra_context=scope.extra_context)
    if args.extra:
        scope = ProjectScope(project_name=scope.project_name, repo_url=scope.repo_url, scope_description=scope.scope_description, extra_context=args.extra)

    # Optional: inject repo layout so design/implementation use only existing paths
    repo_path = (args.repo_path or os.getenv("OAPE_REPO_PATH") or os.getenv("OAPE_OPERATOR_CWD") or "").strip()
    if repo_path:
        repo_layout = get_repo_layout(repo_path)
        if repo_layout:
            scope = ProjectScope(
                project_name=scope.project_name,
                repo_url=scope.repo_url,
                scope_description=scope.scope_description,
                extra_context=scope.extra_context,
                repo_path=repo_path,
                repo_layout=repo_layout,
            )
            print(f"Repo layout loaded from: {repo_path}\n")

    adapter = get_adapter(args.backend)
    print(f"OAPE workflow backend: {adapter.backend_name}")
    print(f"Project: {scope.project_name} | Repo: {scope.repo_url}\n")
    result = adapter.run(scope)

    if not result.success:
        print("Workflow failed:", result.error or "Unknown error")
        if result.output_text:
            print(result.output_text)
        sys.exit(1)

    print("\n" + "=" * 70)
    print("RESULT")
    print("=" * 70)
    print(result.output_text)
    print("=" * 70)

    # Always print TRACE section so it's never missed
    trace_id = result.artifacts.get("trace_id") if result.artifacts else None
    trace_url = result.artifacts.get("trace_url") if result.artifacts else None
    trace_access_code = result.artifacts.get("trace_access_code") if result.artifacts else None
    print("\n" + "=" * 70, flush=True)
    print("TRACE (CrewAI dashboard)", flush=True)
    print("=" * 70, flush=True)
    if trace_id or trace_url:
        print("✅ Trace batch finalized with session ID:", trace_id or "", flush=True)
        print("", flush=True)
        print("🔗 View here:", trace_url or "", flush=True)
        if trace_access_code:
            print("🔑 Access Code:", trace_access_code, flush=True)
    else:
        print("(Not captured. Run: crewai login ; set CREWAI_TRACING_ENABLED=true)", flush=True)
    print("=" * 70, flush=True)

    # Cost (from token usage when available)
    artifacts = result.artifacts or {}
    token_usage = artifacts.get("token_usage")
    cost_usd = artifacts.get("estimated_cost_usd")
    cost_estimate = artifacts.get("cost_estimate")
    if token_usage is not None or cost_usd is not None:
        print("\n" + "=" * 70, flush=True)
        print("COST (estimated from token usage)", flush=True)
        print("=" * 70, flush=True)
        if token_usage:
            print(f"  Prompt tokens:     {token_usage.get('prompt_tokens', 0):,}", flush=True)
            print(f"  Completion tokens: {token_usage.get('completion_tokens', 0):,}", flush=True)
            print(f"  Total tokens:      {token_usage.get('total_tokens', 0):,}", flush=True)
        if cost_usd is not None:
            print(f"  Estimated cost:    ${cost_usd:.4f} USD", flush=True)
        if cost_estimate and cost_estimate.get("model"):
            print(f"  Model (pricing):   {cost_estimate.get('model', 'unknown')}", flush=True)
        print("=" * 70, flush=True)

    if result.artifacts:
        other = [k for k in result.artifacts if k not in ("trace_id", "trace_url", "trace_access_code", "token_usage", "estimated_cost_usd", "cost_estimate")]
        if other:
            print("\nArtifacts:", other)

    # Write outputs to repo/directory when --output-dir is set (so you see code changes after tasks complete)
    output_dir = (args.output_dir or os.getenv("OAPE_OUTPUT_DIR", "").strip()) or None
    if output_dir:
        out_path = Path(output_dir).resolve()
        out_path.mkdir(parents=True, exist_ok=True)
        (out_path / "customer_doc.md").write_text(result.output_text or "", encoding="utf-8")
        print(f"\nWrote customer_doc.md to {out_path}", flush=True)
        if result.artifacts:
            task_names = [
                "design", "design_review", "test_plan", "implementation_outline",
                "unit_tests", "implementation", "quality_feedback", "code_review", "revision_summary",
                "sse_writeup", "customer_doc",
            ]
            for i in range(1, 12):
                key = f"task_{i}"
                if key in result.artifacts and key not in ("trace_id", "trace_url", "trace_access_code"):
                    name = task_names[i - 1] if i <= len(task_names) else key
                    (out_path / f"task_{i}_{name}.md").write_text(
                        result.artifacts.get(key) or "", encoding="utf-8"
                    )
            print(f"Wrote task artifacts (task_1_*.md ... task_11_*.md) to {out_path}", flush=True)

    # Apply design as code changes in the operator repo: new branch, generated files, commit
    if getattr(args, "apply_to_repo", False) or os.getenv("OAPE_APPLY_TO_REPO", "").strip().lower() in ("1", "true", "yes"):
        if not repo_path:
            print("\n⚠ --apply-to-repo requires --repo-path (or OAPE_REPO_PATH). Skipping.", flush=True)
        elif args.backend != "crewai":
            print("\n⚠ --apply-to-repo is only supported with backend=crewai. Skipping.", flush=True)
        elif not result.artifacts:
            print("\n⚠ No artifacts to apply (missing task outputs). Skipping.", flush=True)
        else:
            design = (result.artifacts.get("task_1") or "")[:20000]
            impl = (result.artifacts.get("task_4") or "")[:20000]
            revision = (result.artifacts.get("task_9") or "")[:15000]
            unit_tests_md = (result.artifacts.get("task_5") or "")[:80000]
            impl_code_md = (result.artifacts.get("task_6") or "")[:80000]
            if not design or not impl:
                print("\n⚠ Missing design (task_1) or implementation outline (task_4). Skipping apply.", flush=True)
            else:
                try:
                    from repo_apply import apply_to_repo
                    branch = args.branch_name or os.getenv("OAPE_BRANCH_NAME", "").strip() or None
                    ok, msg = apply_to_repo(
                        repo_path=repo_path,
                        design=design,
                        implementation_outline=impl,
                        revision_summary=revision,
                        repo_layout=scope.repo_layout if hasattr(scope, "repo_layout") else None,
                        project_name=scope.project_name,
                        branch_name=branch,
                        unit_tests_md=unit_tests_md or None,
                        implementation_code_md=impl_code_md or None,
                    )
                    if ok:
                        print("\n" + "=" * 70, flush=True)
                        print("REPO APPLY", flush=True)
                        print("=" * 70, flush=True)
                        print("✅", msg, flush=True)
                        print("=" * 70, flush=True)
                    else:
                        print("\n❌ Repo apply failed:", msg, flush=True)
                except Exception as e:
                    print("\n❌ Repo apply error:", e, flush=True)


if __name__ == "__main__":
    main()
