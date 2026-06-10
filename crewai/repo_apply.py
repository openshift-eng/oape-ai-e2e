"""
Apply OAPE workflow outputs to the operator repo: create a git branch,
generate code from design + implementation outline + revision summary, write files, and commit.

Used when --apply-to-repo is set after a successful CrewAI run (requires --repo-path).
"""

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def _get_llm():
    """Use same LLM as agents (Vertex or OpenAI) for code generation."""
    from agents import _get_llm as agents_get_llm
    return agents_get_llm()


def _llm_generate_files_prompt(
    design: str,
    implementation_outline: str,
    revision_summary: str,
    repo_layout: Optional[str],
    project_name: str,
) -> str:
    return f"""You are implementing an operator feature from an OAPE design and implementation outline.

**Project:** {project_name}

**Design document:**
```
{design[:12000]}
```

**Implementation outline (files to add/modify, reconciliation logic, unit test plan):**
```
{implementation_outline[:12000]}
```

**Revision summary (resolution of code review items):**
```
{revision_summary[:8000]}
```
""" + (
        f"""
**Repository layout (only create or modify files under these paths; paths are relative to repo root):**
```
{repo_layout[:6000]}
```
"""
        if repo_layout
        else ""
    ) + """

**Task:** Produce the actual code and config changes. Output a single JSON object with this exact shape (no other text):
{"files": [{"path": "relative/path/from/repo/root", "content": "full file content as string"}]}

Rules:
- "path" must be relative to the repository root (e.g. pkg/controller/foo/reconciler.go, config/crd/...).
- "content" must be the complete file content (not a diff). For existing files that you modify, output the full new content.
- Only include files that the implementation outline says to add or modify. Prefer existing paths from the repo layout when modifying.
- Use valid JSON only. Inside each "content" string use \\n for newlines (escaped), so the whole output is one parseable JSON block.
"""


def _llm_fix_compile_prompt(
    compile_error: str,
    repo_path: str,
    files_touched: list[str],
    design: str,
    implementation_outline: str,
    repo_layout: Optional[str],
    project_name: str,
) -> str:
    """Prompt for LLM to fix code so that go build succeeds."""
    files_list = "\n".join(f"  - {p}" for p in files_touched[:30])
    return f"""The Go build failed in the repository. Fix the code so that `go build ./...` succeeds.

**Project:** {project_name}
**Repo path:** {repo_path}

**Build error:**
```
{compile_error[:8000]}
```

**Files that were written (you may fix these or any other file the error points to, e.g. go.mod):**
{files_list or "  (none listed)"}

**Design (for context):**
```
{(design or "")[:4000]}
```

**Implementation outline (for context):**
```
{(implementation_outline or "")[:4000]}
```
""" + (
        f"""
**Repo layout (paths relative to repo root):**
```
{(repo_layout or "")[:3000]}
```
"""
        if repo_layout
        else ""
    ) + """

**Task:** Output a single JSON object. You may use either or both of "files" and "commands":

1. **File edits:** {"files": [{"path": "relative/path", "content": "full file content"}]}
   - Use for code fixes, go.mod version, etc. "path" is relative to repo root. Use \\n for newlines in "content".

2. **Shell commands (run from repo root):** {"commands": ["go mod vendor"], "files": []}
   - Use when the error says to run a command (e.g. "inconsistent vendoring", "run: go mod vendor", "use -mod=mod").
   - Typical fixes: ["go mod vendor"] to sync vendor dir, or ["go mod tidy"] to fix go.mod.
   - You may output both "commands" and "files"; commands run first, then files are written, then build is retried.

Example for vendoring error: {"commands": ["go mod vendor"], "files": []}
Example for code only: {"files": [{"path": "pkg/foo.go", "content": "..."}]}
"""


def _fix_compile_with_llm(
    repo_path: str,
    compile_stderr: str,
    compile_stdout: str,
    files_touched: list[str],
    design: str,
    implementation_outline: str,
    revision_summary: str,
    repo_layout: Optional[str],
    project_name: str,
) -> tuple[list[dict[str, str]], list[str]]:
    """Ask LLM to fix build. Returns (files to write, commands to run from repo root). Agent may suggest file edits and/or shell commands (e.g. go mod vendor)."""
    err_text = (compile_stderr + "\n" + compile_stdout).strip() or "non-zero exit"
    prompt = _llm_fix_compile_prompt(
        compile_error=err_text,
        repo_path=repo_path,
        files_touched=files_touched,
        design=design,
        implementation_outline=implementation_outline,
        repo_layout=repo_layout,
        project_name=project_name,
    )
    try:
        llm = _get_llm()
        if llm is None:
            return [], []
        response = llm.call([{"role": "user", "content": prompt}])
        if not response or not isinstance(response, str):
            return [], []
        return _parse_fix_response(response)
    except Exception:
        return [], []


def _run_commands(repo_path: str, commands: list[str]) -> tuple[bool, str]:
    """Run shell commands from repo root. Returns (success, message). Uses same env as go build."""
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        return False, f"Repo path is not a directory: {repo}"
    env = _go_build_env()
    for cmd_str in commands:
        if not cmd_str:
            continue
        try:
            r = subprocess.run(
                cmd_str,
                cwd=repo,
                shell=True,
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if r.returncode != 0:
                return False, f"Command failed: {cmd_str}\n{(r.stderr or r.stdout or '').strip()}"
        except subprocess.TimeoutExpired:
            return False, f"Command timed out: {cmd_str}"
        except Exception as e:
            return False, f"Command error ({cmd_str}): {e}"
    return True, ""


def _list_written_paths(repo_path: str) -> list[str]:
    """Return list of repo-relative paths that are modified/untracked (so we can tell the LLM what was written)."""
    repo = Path(repo_path).resolve()
    try:
        r = subprocess.run(
            ["git", "status", "--short", "--porcelain"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            return []
        paths = []
        for line in (r.stdout or "").strip().splitlines():
            # format: " M path" or "?? path" or "R  old -> new"
            parts = line.split(maxsplit=1)
            if len(parts) >= 2:
                p = parts[1].strip()
                if " -> " in p:
                    p = p.split(" -> ", 1)[-1].strip()  # rename: use destination path
                paths.append(p)
        return paths
    except Exception:
        return []


def _parse_files_json(response: str) -> list[dict[str, str]]:
    """Extract files list from LLM response (raw JSON or markdown code block)."""
    _files, _ = _parse_fix_response(response)
    return _files


def _parse_fix_response(response: str) -> tuple[list[dict[str, str]], list[str]]:
    """Parse compile-fix LLM response. Returns (files, commands). Agent may return files and/or commands."""
    text = response.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            return [], []
        files = data.get("files")
        if isinstance(files, list):
            files = [f for f in files if isinstance(f, dict) and "path" in f and "content" in f]
        else:
            files = []
        commands = data.get("commands")
        if isinstance(commands, list):
            commands = [str(c).strip() for c in commands if c]
        else:
            commands = []
        return files, commands
    except json.JSONDecodeError:
        pass
    return [], []


def _extract_files_from_markdown(md: str) -> list[dict[str, str]]:
    """Parse markdown with '## path' sections and ``` code blocks into [{path, content}]."""
    files = []
    # Match ## path (optional) then optional text then ```lang? ... ```
    # Section: ## repo/relative/path.go or ## pkg/controller/foo_test.go
    pattern = re.compile(
        r"^##\s+(.+?)\s*$"  # heading with path
        r"(?:[\s\S]*?)"  # any text until code block
        r"```(?:\w*)\s*\n([\s\S]*?)```",
        re.MULTILINE,
    )
    for m in re.finditer(pattern, md):
        path = m.group(1).strip().lstrip("/")
        content = (m.group(2) or "").rstrip()
        if not path or path.lower() in ("design", "implementation", "summary", "output", "rules"):
            continue
        if "/" in path or path.endswith(".go") or path.endswith(".yaml") or path.endswith(".yml"):
            files.append({"path": path, "content": content})
    # Fallback: any ``` block with a path-like first line or filename in previous line
    if not files:
        for m in re.finditer(r"```(?:\w*)\s*\n([\s\S]*?)```", md):
            content = (m.group(1) or "").rstrip()
            if not content.strip():
                continue
            # Try to find a path in the 5 lines before this block
            start = max(0, m.start() - 500)
            before = md[start:m.start()]
            path_m = re.search(r"(?:^|\n)#{1,6}\s+([a-zA-Z0-9_/\.\-]+\.(?:go|yaml|yml))\s*$", before)
            if path_m:
                path = path_m.group(1).strip().lstrip("/")
                files.append({"path": path, "content": content})
    return files


def _go_build_env() -> dict[str, str]:
    """Build env for go build: inherit process env so GOROOT, GOPATH, GO111MODULE are used."""
    env = os.environ.copy()
    # Ensure Go module mode is on if not set
    if "GO111MODULE" not in env:
        env["GO111MODULE"] = "on"
    return env


def verify_compile(repo_path: str) -> tuple[bool, str, str, str]:
    """Run go build ./... or make build. Returns (success, message, stderr, stdout)."""
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        return False, f"Repo path is not a directory: {repo}", "", ""
    env = _go_build_env()
    for cmd, args in [
        (["go", "build", "./..."], "go build ./..."),
        (["make", "build"], "make build"),
    ]:
        try:
            r = subprocess.run(
                cmd, cwd=repo, capture_output=True, text=True, timeout=120, env=env
            )
            stderr = r.stderr or ""
            stdout = r.stdout or ""
            if r.returncode == 0:
                return True, f"Compile OK ({args}).", stderr, stdout
            return False, f"Compile failed ({args}): {stderr or stdout or 'non-zero exit'}", stderr, stdout
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return False, f"Compile timed out ({args}).", "", ""
    return False, "No build command found (tried go build, make build).", "", ""


def create_branch(repo_path: str, branch_name: str) -> tuple[bool, str, Optional[str]]:
    """Create and checkout a new git branch. If name already exists, use a unique suffix (e.g. -1, -2 or -HHMMSS). Returns (success, message, actual_branch_name)."""
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        return False, f"Repo path is not a directory: {repo}", None
    for attempt in range(11):  # base, then -1..-9, then -HHMMSS
        if attempt == 0:
            candidate = branch_name
        elif attempt < 10:
            candidate = f"{branch_name}-{attempt}"
        else:
            candidate = f"{branch_name}-{datetime.utcnow().strftime('%H%M%S')}"
        try:
            subprocess.run(
                ["git", "checkout", "-b", candidate],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            )
            if attempt == 0:
                return True, f"Created and checked out branch: {candidate}", candidate
            return True, f"Branch '{branch_name}' already existed; created and checked out: {candidate}", candidate
        except subprocess.CalledProcessError as e:
            err = (e.stderr or e.stdout or str(e)) or ""
            if "already exists" in err.lower():
                continue
            return False, f"git checkout -b failed: {err}", None
    return False, f"Could not create a unique branch from '{branch_name}' (all attempts already exist).", None


def commit_changes(repo_path: str, message: str) -> tuple[bool, str]:
    """Stage all changes and commit. Returns (success, message)."""
    repo = Path(repo_path).resolve()
    try:
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True, text=True)
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        if not status.stdout.strip():
            return False, "No changes to commit (working tree clean)."
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return True, f"Committed: {message}"
    except subprocess.CalledProcessError as e:
        return False, f"git commit failed: {e.stderr or e.stdout or str(e)}"


def _write_files(repo_path: str, files: list[dict[str, str]]) -> tuple[bool, str, int]:
    """Write a list of {path, content} to repo. Returns (success, message, count)."""
    repo = Path(repo_path).resolve()
    written = 0
    for item in files:
        rel = (item.get("path") or "").strip().lstrip("/")
        content = item.get("content") or ""
        if not rel:
            continue
        dest = repo / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            written += 1
        except Exception as e:
            return False, f"Failed to write {rel}: {e}", written
    return True, f"Wrote {written} file(s).", written


def generate_and_apply_files(
    repo_path: str,
    design: str,
    implementation_outline: str,
    revision_summary: str,
    repo_layout: Optional[str],
    project_name: str,
) -> tuple[bool, str, int]:
    """
    Use LLM to generate file contents from design + outline + revision, then write to repo.
    Returns (success, message, number_of_files_written).
    """
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        return False, f"Repo path is not a directory: {repo}", 0

    prompt = _llm_generate_files_prompt(
        design=design,
        implementation_outline=implementation_outline,
        revision_summary=revision_summary,
        repo_layout=repo_layout,
        project_name=project_name,
    )
    try:
        llm = _get_llm()
        if llm is None:
            return False, "No LLM configured for code generation (set OPENAI_API_KEY or Vertex).", 0
        response = llm.call([{"role": "user", "content": prompt}])
        if not response or not isinstance(response, str):
            return False, "LLM returned no or invalid response.", 0
    except Exception as e:
        return False, f"LLM call failed: {e}", 0

    files = _parse_files_json(response)
    if not files:
        return False, "LLM response did not contain a valid 'files' JSON array.", 0

    return _write_files(repo_path, files)


def apply_to_repo(
    repo_path: str,
    design: str,
    implementation_outline: str,
    revision_summary: str,
    repo_layout: Optional[str],
    project_name: str,
    branch_name: Optional[str] = None,
    commit_message: Optional[str] = None,
    unit_tests_md: Optional[str] = None,
    implementation_code_md: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Create branch, write unit tests first then implementation (or LLM fallback), verify compile, commit.
    Returns (success, message). Code must compile before commit.
    """
    if not branch_name:
        slug = re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-") or "feature"
        date = datetime.utcnow().strftime("%Y%m%d")
        branch_name = f"oape/{slug}-{date}"
    if not commit_message:
        commit_message = f"OAPE: apply design and implementation for {project_name} (tests first, then impl; code compiles)"

    ok, msg, actual_branch = create_branch(repo_path, branch_name)
    if not ok:
        return False, msg
    actual_branch = actual_branch or branch_name

    total_written = 0
    # 1) SQE wrote unit tests first — extract and write
    if unit_tests_md:
        files = _extract_files_from_markdown(unit_tests_md)
        if files:
            ok, msg, n = _write_files(repo_path, files)
            if not ok:
                return False, msg
            total_written += n
    # 2) SSE wrote implementation — extract and write
    if implementation_code_md:
        files = _extract_files_from_markdown(implementation_code_md)
        if files:
            ok, msg, n = _write_files(repo_path, files)
            if not ok:
                return False, msg
            total_written += n
    # 3) Fallback: LLM-generated files if no files from task outputs
    if total_written == 0:
        ok, msg, n = generate_and_apply_files(
            repo_path=repo_path,
            design=design,
            implementation_outline=implementation_outline,
            revision_summary=revision_summary,
            repo_layout=repo_layout,
            project_name=project_name,
        )
        if not ok:
            return False, msg
        total_written = n

    # 4) Code must compile before we commit; on failure, let the agent try to fix (up to N attempts)
    max_fix_attempts = 3
    try:
        env_attempts = os.getenv("OAPE_COMPILE_FIX_ATTEMPTS", "").strip()
        if env_attempts:
            max_fix_attempts = max(1, min(5, int(env_attempts)))
    except ValueError:
        pass

    for attempt in range(max_fix_attempts + 1):
        ok, compile_msg, stderr, stdout = verify_compile(repo_path)
        if ok:
            break
        if attempt >= max_fix_attempts:
            return False, (
                f"Code did not compile after {max_fix_attempts} fix attempt(s). {compile_msg} "
                "Export GOROOT, GOPATH, GO111MODULE if needed; or apply with --no-apply-to-repo."
            )
        print(f"\n[Repo apply] Compile failed (attempt {attempt + 1}/{max_fix_attempts + 1}). Asking agent to fix ...", flush=True)
        files_touched = _list_written_paths(repo_path)
        fix_files, fix_commands = _fix_compile_with_llm(
            repo_path=repo_path,
            compile_stderr=stderr,
            compile_stdout=stdout,
            files_touched=files_touched,
            design=design,
            implementation_outline=implementation_outline,
            revision_summary=revision_summary,
            repo_layout=repo_layout,
            project_name=project_name,
        )
        if not fix_files and not fix_commands:
            return False, f"Compile failed and agent could not produce a fix. {compile_msg}"
        # Run agent-suggested commands first (e.g. go mod vendor), then apply file edits
        if fix_commands:
            ok_cmd, msg_cmd = _run_commands(repo_path, fix_commands)
            if not ok_cmd:
                return False, f"Agent suggested commands but they failed: {msg_cmd}"
            print(f"[Repo apply] Ran agent commands: {fix_commands}", flush=True)
        if fix_files:
            ok_write, msg_write, n = _write_files(repo_path, fix_files)
            if not ok_write:
                return False, f"Compile fix write failed: {msg_write}"
            print(f"[Repo apply] Applied agent file fix ({n} file(s))", flush=True)
        print("[Repo apply] Re-running build ...", flush=True)
        # retry verify_compile on next iteration

    ok, commit_msg = commit_changes(repo_path, commit_message)
    if not ok:
        return False, f"Files written and compile OK, but commit failed: {commit_msg}"

    return True, f"Branch '{actual_branch}' created, {total_written} file(s) written, compile OK, committed."
