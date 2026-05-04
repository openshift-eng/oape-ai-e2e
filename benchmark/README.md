# OAPE Benchmark Pipeline

A self-improving benchmark pipeline for the OAPE code generation tools. It takes Enhancement Proposals (EPs) that have already been implemented by humans, re-generates the implementation using the OAPE tools, compares the output against the real merged code, and iteratively improves the tool instructions based on the gaps found.

## How It Works

```
EP #1863 + Repo + PR numbers
         │
         ▼
┌──────────────────────────────────────────────────┐
│  Iteration 1: Generate with ORIGINAL tool        │
│  ├── Clone repo at pre-EP commit (bias-free)     │
│  ├── Run /oape:api-generate + /oape:api-implement│
│  ├── Compare output vs human implementation      │
│  └── Score: completeness, precision, conventions │
├──────────────────────────────────────────────────┤
│  Improve: Analyze gaps, edit tool instructions   │
├──────────────────────────────────────────────────┤
│  Iteration 2: Generate with IMPROVED-v1 tool     │
│  ├── Fresh clone at same pre-EP commit           │
│  ├── Run tools with improved instructions        │
│  └── Compare and score again                     │
├──────────────────────────────────────────────────┤
│  Improve: Further refine tool instructions       │
├──────────────────────────────────────────────────┤
│  Iteration 3: Generate with IMPROVED-v2 tool     │
│  └── Final comparison and report                 │
└──────────────────────────────────────────────────┘
         │
         ▼
  Report: score progression, tool diffs, file classification
```

### Bias Prevention

The generating agent never sees the existing implementation. The repo is cloned at the commit **before** the EP was merged, so the EP's code physically does not exist. The agent only receives the EP URL and generates from scratch.

### Scoring

- **Completeness**: What % of the human's structs/fields/functions did the tool also produce?
- **Raw Precision**: What % of generated code matches the human's output? (penalizes all extras)
- **Adjusted Precision**: Same as raw but excludes auto-generated artifacts (`zz_generated`, CRDs, bundle manifests) and valuable extras (tests, sample configs)
- **Convention Compliance**: Kubebuilder marker and naming pattern match rate
- **Build Success**: Does `make build` pass?

Extra files are classified into four categories:
- **Auto-generated**: Output of `make generate`/`make manifests` (not errors)
- **Formatting-only**: Whitespace/import reordering (not errors)
- **Valuable extras**: Tests, validation, sample configs the human didn't write (tool outperformed human)
- **Genuinely wrong**: Files that should not have been touched (real errors)

## Prerequisites

- Python 3.11+
- `gh` CLI authenticated (`gh auth login`)
- `git`
- `go` toolchain
- `claude-agent-sdk` (`pip install claude-agent-sdk`)
- Claude Code CLI with access to `claude-opus-4-6` (or another model)

## Quick Start

### 1. Create a config file

Create `benchmark/config.yaml` with your EP-to-implementation mappings:

```yaml
benchmark_cases:
  - ep_url: "https://github.com/openshift/enhancements/pull/1863"
    repo_url: "https://github.com/openshift/zero-trust-workload-identity-manager"
    description: "SPIRE federation support"
    implementation_prs: [68, 82]

  # Add more EPs here:
  # - ep_url: "https://github.com/openshift/enhancements/pull/XXXX"
  #   repo_url: "https://github.com/openshift/<operator-repo>"
  #   description: "Short description"
  #   implementation_prs: [PR1, PR2]  # all PRs that implement this EP

settings:
  model: "claude-opus-4-6"       # Claude model to use
  effort: "max"                   # Effort level (low/medium/high/max)
  iterations: 3                   # Number of feedback loop iterations
  output_dir: "benchmark/results"
  parallel: false
```

**How to find the right values:**

| Field | How to find it |
|-------|---------------|
| `ep_url` | The Enhancement Proposal PR on `openshift/enhancements` |
| `repo_url` | The upstream `openshift/<operator>` repo where the EP was implemented |
| `implementation_prs` | The PR numbers **on the operator repo** (not the EP repo) that implement this EP. Use `gh pr list --repo openshift/<repo> --state merged --search "<keywords>"` to find them |
| `description` | Short label for reports |

**Important**: The EP URL and implementation PRs are on **different repos**. The EP is on `openshift/enhancements`, the PRs are on `openshift/<operator-name>`.

### 2. Run the benchmark

```bash
cd benchmark/

# Run the feedback loop (iteratively improves the tool)
python benchmark.py run --config config.yaml

# Force re-run if results already exist
python benchmark.py run --config config.yaml --force

# Override number of iterations
python benchmark.py run --config config.yaml --iterations 5

# Ad-hoc single EP (no config file needed)
python benchmark.py run \
  --ep-url "https://github.com/openshift/enhancements/pull/1863" \
  --repo "https://github.com/openshift/zero-trust-workload-identity-manager" \
  --prs 68,82
```

### 3. Review results

Results are written to `benchmark/results/<repo-name>/ep-<number>/`:

```
results/
  zero-trust-workload-identity-manager/
    ep-1863/
      report.md           # Human-readable report with score progression
      report.json          # Machine-readable scores
      truth/               # Ground truth from human implementation
        combined.diff
        files_added.txt
        files_modified.txt
      iter-1/              # Iteration 1 output
        diff.patch         # What the tool generated
        scores.json
      iter-2/              # Iteration 2 (with improved-v1 tool)
        diff.patch
        scores.json
      iter-3/              # Iteration 3 (with improved-v2 tool)
        diff.patch
        scores.json
      tool-backups/        # Tool instruction snapshots
        original/          # Original api-generate.md, api-implement.md
        after-improvement-1/
        after-improvement-2/
      tool-improvement-after-iter-1/  # Exact diffs of tool changes
      tool-improvement-after-iter-2/
```

### 4. Generate aggregate report (when running multiple EPs)

```bash
python benchmark.py report --results-dir benchmark/results/
```

### 5. Push a specific iteration as a PR (optional)

If an iteration produced good output, you can push it as a PR:

```bash
python benchmark.py push \
  --ep 1863 \
  --repo "https://github.com/<your-fork>/<operator>" \
  --iteration 3 \
  --base-branch main \
  --title "feat: implement EP #1863 (OAPE generated)"
```

## Adding Your Own EPs

To benchmark against your team's EPs:

1. **Find EPs with merged implementations**: You need EPs where the code is already merged on the upstream `openshift/` operator repo. The benchmark compares tool output against the merged implementation.

2. **Identify the implementation PRs**: For each EP, find which PRs on the operator repo implement it. There's often no direct link between the EP and the code PRs (different Jira tickets, no EP URL in PR body), so you need to know which PRs are related.

   ```bash
   # Search for related PRs
   gh pr list --repo openshift/<operator> --state merged \
     --search "federation OR SPIRE-54" \
     --json number,title,mergedAt --limit 20
   ```

3. **Add to config.yaml**: Add a new entry with the EP URL, repo URL, and all related PR numbers.

4. **Run**: The pipeline handles everything else -- cloning at the right commit, running the tools, comparing, scoring, and improving.

### Multi-PR EPs

When an EP is implemented across multiple PRs (common for large features), list all PR numbers:

```yaml
- ep_url: "https://github.com/openshift/enhancements/pull/1863"
  repo_url: "https://github.com/openshift/zero-trust-workload-identity-manager"
  description: "SPIRE federation support"
  implementation_prs: [68, 82]  # PR #68 + PR #82 combined as ground truth
```

The pipeline automatically:
- Sorts PRs by merge date
- Uses the parent of the earliest PR as the baseline (pre-EP state)
- Combines all PR diffs as the ground truth
- Handles file overlaps between PRs (uses final state)

## Architecture

```
benchmark.py          CLI entry point, orchestrates the pipeline
isolate.py            Clones repo at pre-EP commit, verifies no bias
ground_truth.py       Extracts combined diff from all implementation PRs
runner.py             Runs OAPE tools + improver agent via Claude Agent SDK
compare.py            Delta-aware diff, scoring, file classification
report.py             Generates markdown + JSON reports
models.py             Shared data models
go_ast_helper/        Go program for AST-level struct/field comparison
config.yaml           Benchmark case definitions (user-provided)
```

### Tool Improvement Loop

The improver agent (a separate Claude invocation) receives:
- The comparison results (scores, missed files, wrong structs)
- The ground truth diff and generated diff
- The current tool instruction files (`api-generate.md`, `api-implement.md`)

It makes **generic** improvements to the instructions -- not EP-specific ones. This means improvements from benchmarking one EP benefit all future EP implementations.

After the benchmark completes, original tool files are **automatically restored**. Improved versions are preserved in `tool-backups/` for you to review and selectively adopt.

## Cost Estimates

| Component | Approximate cost |
|-----------|-----------------|
| Generation (per iteration) | $5-15 |
| Tool improvement (per iteration) | $2-5 |
| Full 3-iteration run | $25-35 |
| 10 EPs x 3 iterations | $250-350 |

Costs vary by EP complexity and model. Using `effort: "max"` is more expensive but produces better results.
