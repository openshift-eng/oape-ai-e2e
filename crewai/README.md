# OAPE Workflow (Adapter: CrewAI vs Claude SDK)

Project-agnostic workflow for OAPE with an **adapter design** so you can switch between two backends:

| Backend      | What it does |
|-------------|---------------|
| **crewai**  | Full 11-task pipeline: design → design review → test plan → implementation outline → **unit tests (SQE)** → **implementation (SSE)** → quality → code review → address review → write-up → customer doc. Code must compile when using `--apply-to-repo`. Uses skills from `plugins/oape/skills/`. |
| **claude-sdk** | Calls the OAPE server (Claude Agent SDK) for **api-implement**: generate controller/reconciler code from an enhancement proposal. Requires server running and an EP URL. |

Same **scope** (context file, EP URL, env) and same **entrypoint** (`main.py`); switch via `--backend` or `OAPE_BACKEND`.

## Features

- **Adapter pattern:** One interface (`WorkflowAdapter.run(scope) -> WorkflowResult`), two implementations; switch backends without changing caller code.
- **Project-agnostic:** Scope is set at runtime (env, CLI, context file, or EP URL).
- **Skills-driven (CrewAI):** All `plugins/oape/skills/<name>/SKILL.md` are loaded and injected when using the CrewAI backend.

## Setup

From the **oape-ai-e2e** repo root:

```bash
cd crewai
pip install -r requirements.txt
```

Set scope via env or CLI (see below). For OpenAI (default), set `OPENAI_API_KEY`. For Vertex Claude, set `OAPE_CREWAI_USE_VERTEX=1` and Vertex env vars (`ANTHROPIC_VERTEX_PROJECT_ID`, `CLOUD_ML_REGION`, optional `VERTEX_CLAUDE_MODEL`).

## Switching backends

- **CrewAI (default):** `python main.py ...` or `OAPE_BACKEND=crewai python main.py ...`
- **Claude SDK:** Start the OAPE server (e.g. `cd server && uvicorn server:app`), set `OAPE_CLAUDE_SDK_SERVER_URL` if not `http://localhost:8000`, then:
  ```bash
  python main.py --backend claude-sdk --ep-url https://github.com/openshift/enhancements/pull/1234 --project-name "My Operator" --repo-url https://github.com/openshift/my-operator
  ```
  The Claude SDK backend requires an EP URL (via `--ep-url` or `OAPE_EP_URL` or in the context file). Operator repo path: `OAPE_OPERATOR_CWD`.

## Running

**Using environment variables:**

```bash
export OAPE_PROJECT_NAME="My Operator"
export OAPE_REPO_URL="https://github.com/openshift/my-operator"
export OAPE_SCOPE_DESCRIPTION="Add a new CRD and controller for Foo resource; follow controller-runtime."
# optional:
export OAPE_EXTRA_CONTEXT="Link to enhancement: https://github.com/openshift/enhancements/pull/1234"

python main.py
```

**Using CLI:**

```bash
python main.py \
  --project-name "My Operator" \
  --repo-url "https://github.com/openshift/my-operator" \
  --scope "Add a new CRD and controller for Foo resource; follow controller-runtime."
```

**Using a context file (e.g. scope.txt):**

The file can be plain text (entire file = scope description) or use optional headers:

```text
PROJECT_NAME=My Operator
REPO_URL=https://github.com/openshift/my-operator
---
Add a new CRD and controller for Foo resource. Follow controller-runtime.
Include validation, reconciliation, and unit tests.
```

Then:

```bash
python main.py --context-file path/to/scope.txt
```

**Using a local repo path (so the SSE uses real paths):**

If you have a local clone, pass its path so the workflow injects the **directory layout** into scope. The design and implementation outline will then suggest only files/packages that exist in that tree (no invented directories).

```bash
python main.py --context-file scope.txt --repo-path /path/to/openshift-zero-trust-workload-identity-manager
# or
export OAPE_REPO_PATH=/path/to/your-repo
python main.py --context-file scope.txt
```

`OAPE_OPERATOR_CWD` is also read as a fallback for the repo path.

An example file is provided: `example_scope.txt`. You can also set `OAPE_CONTEXT_FILE=path/to/scope.txt` in the environment. If `PROJECT_NAME` and `REPO_URL` are omitted in the file, set them via env or CLI, or the default scope names are used.

**Using a GitHub Enhancement Proposal (EP) URL:**

Fetches the PR title and body from `openshift/enhancements` and adds it as extra context. Requires the [GitHub CLI](https://cli.github.com/) (`gh`) to be installed and authenticated (`gh auth login`).

```bash
python main.py --ep-url https://github.com/openshift/enhancements/pull/1234 --project-name "My Operator" --repo-url https://github.com/openshift/my-operator
```

You can combine **context file** and **EP URL**: e.g. `--context-file scope.txt --ep-url https://github.com/openshift/enhancements/pull/1234` uses the file for project/scope and appends the EP content as additional context. Env vars `OAPE_CONTEXT_FILE` and `OAPE_EP_URL` can be used instead of CLI flags.

If no scope is provided, a default example scope is used so the crew still runs (useful for testing).

## How to test

**Prerequisites (from `oape-ai-e2e/crewai`):**

```bash
cd /path/to/oape-ai-e2e/crewai
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...   # or use Vertex: OAPE_CREWAI_USE_VERTEX=1 + ANTHROPIC_VERTEX_PROJECT_ID, CLOUD_ML_REGION
```

**Quick test script (from `crewai/`):**

```bash
./scripts/test_crewai.sh              # smoke test (default scope)
./scripts/test_crewai.sh context      # with example_scope.txt
./scripts/test_crewai.sh output-dir   # write outputs to /tmp/oape-test
REPO_PATH=/path/to/repo ./scripts/test_crewai.sh apply   # apply to repo (branch + code + compile + commit)
```

**1. Minimal smoke test (default scope, 11-task pipeline)**

No context file needed; uses built-in default scope. You should see reasoning, 11 tasks (design → review → test plan → outline → **unit tests** → **implementation** → quality → code review → address review → write-up → customer doc), and the TRACE section at the end.

```bash
python main.py
```

**2. Test with a context file**

```bash
python main.py --context-file example_scope.txt
```

**3. Test with ZTWIM scope and local repo**

Point at a local operator clone so the workflow uses real paths. SQE writes unit tests first, then SSE writes implementation; if you use `--apply-to-repo`, the code must compile before commit.

```bash
# With repo path only (no apply):
python main.py --context-file scope_ztwim_upstream_authority.txt --repo-path /path/to/your/operator-repo

# Write all task outputs to a directory:
python main.py --context-file scope_ztwim_upstream_authority.txt --repo-path /path/to/repo --output-dir /tmp/oape-out

# Apply to repo: new branch, write code, verify compile, commit (requires Go/make):
python main.py --context-file scope_ztwim_upstream_authority.txt --repo-path /path/to/repo --apply-to-repo
```

Optional: `--branch-name oape/my-feature` and `OAPE_OUTPUT_DIR`, `OAPE_APPLY_TO_REPO`, `OAPE_BRANCH_NAME` as env equivalents.

**4. Avoid "prompt is too long" (200k token limit)**

Task outputs are truncated when used as context for later tasks so the total prompt stays under the model limit. Default: 8000 characters per task output. Override if needed:

```bash
OAPE_CONTEXT_MAX_CHARS_PER_TASK=10000 python main.py --context-file scope_ztwim_upstream_authority.txt --repo-path /path/to/repo
```

**5. More reasoning (debugging)**

Agents use `max_reasoning_attempts=20` by default. Override:

```bash
OAPE_MAX_REASONING_ATTEMPTS=20 python main.py --context-file example_scope.txt
```

**6. See full LLM request/response**

```bash
CREWAI_DEBUG_LLM=1 python main.py --context-file example_scope.txt
```

**7. See full reasoning plan before each task**

```bash
CREWAI_DEBUG_REASONING=1 python main.py --context-file example_scope.txt
```

**8. Test with CLI scope (no file)**

```bash
python main.py --project-name "Test Operator" --repo-url "https://github.com/openshift/example" --scope "Add a simple CRD and reconciler for Bar resource."
```

**9. Claude SDK backend (requires OAPE server running)**

In one terminal start the server (from `oape-ai-e2e`):

```bash
cd server && uvicorn server:app --reload
```

In another, from `oape-ai-e2e/crewai`:

```bash
python main.py --backend claude-sdk --ep-url https://github.com/openshift/enhancements/pull/1234 --project-name "My Operator" --repo-url https://github.com/openshift/my-operator
```

Set `OAPE_CLAUDE_SDK_SERVER_URL` if the server is not at `http://localhost:8000`.

## Trace ID and dashboard

To see **TraceID** and open the run in the CrewAI dashboard:

1. **Enable tracing**  
   The CrewAI backend already uses `tracing=True`. You can also set:
   ```bash
   export CREWAI_TRACING_ENABLED=true
   ```

2. **Log in to CrewAI** (required for traces to be sent and viewable):
   ```bash
   crewai login
   ```
   Use a free account at [app.crewai.com](https://app.crewai.com).

3. **Run the workflow**  
   After `crew.kickoff()` finishes, the run is sent to CrewAI AMP. You get:
   - **Trace ID (session ID)** – printed in the green “Trace batch finalized” panel by CrewAI, and also in our summary.
   - **Trace URL** – we put it in the result artifacts and print it at the end, e.g.:
     ```
     Trace ID: <uuid>
     View trace: https://app.crewai.com/crewai_plus/trace_batches/<uuid>
     ```

4. **View in the dashboard**  
   Open the URL above, or go to [app.crewai.com](https://app.crewai.com) → Traces and select the run. You’ll see agent decisions, task order, LLM calls, and token usage.

If you don’t run `crewai login`, tracing still runs locally but no TraceID or URL is shown (the batch isn’t sent to the backend).

## Skills

Skills live under **`plugins/oape/skills/<name>/SKILL.md`**. Each `SKILL.md` is loaded and appended to a shared “skills context” that is injected into task descriptions. Agents are instructed to “apply the following skills and conventions where relevant.”

**Adding a new skill:**

1. Create a directory under `plugins/oape/skills/`, e.g. `plugins/oape/skills/api-conventions/`.
2. Add `SKILL.md` with clear sections (Purpose, When This Skill Applies, Guidelines, References).
3. Re-run the workflow; the new skill is picked up automatically.

No code changes are required in the CrewAI setup when you add a skill.

## Optional: Vertex AI

To use Claude on Vertex instead of OpenAI:

```bash
export OAPE_CREWAI_USE_VERTEX=1
export ANTHROPIC_VERTEX_PROJECT_ID=your-gcp-project
export CLOUD_ML_REGION=us-east5
export VERTEX_CLAUDE_MODEL=claude-3-5-haiku@20241022
# authenticate
gcloud auth application-default login
python main.py ...
```

## Layout

| File / dir | Purpose |
|------------|--------|
| `adapters/` | Adapter layer: `base.py` (WorkflowAdapter, WorkflowResult), `crewai_adapter.py`, `claude_sdk_adapter.py`, `factory.py` (get_adapter). |
| `skills_loader.py` | Loads all `SKILL.md` from `plugins/oape/skills/` and returns a single context string. |
| `context.py` | Project-agnostic scope (project name, repo URL, scope description, extra context). |
| `personas.py` | Generic SSE, PSE, SQE, Technical Writer personas. |
| `agents.py` | CrewAI agents (optionally with Vertex LLM). |
| `tasks.py` | Builds the 9 tasks with scope and skills context injected. |
| `main.py` | Entry point: parses scope and `--backend`, runs `get_adapter(backend).run(scope)`. |
| `llm_vertex.py` | Optional Vertex Claude LLM for CrewAI. |

## Relation to OAPE commands

The existing OAPE slash commands (`/oape:api-generate`, `/oape:api-implement`, `/oape:review`, etc.) are single-command, single-agent runs that generate or review **code** in the repo. This CrewAI workflow is **document-focused** (design doc, test plan, implementation outline, customer doc) and **multi-agent** (four roles, nine tasks). It reuses the same **skills** so that conventions (e.g. Effective Go) apply consistently whether you use the slash commands or the CrewAI pipeline.
