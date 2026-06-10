# /oape.pr - Create a draft pull request

## Purpose

Submit changes as a draft pull request. Handles authentication, fork workflows,
remote configuration, and cross-repo PR creation. Reusable across all 3 PR
phases (API types, controller, E2E tests).

## Arguments

- `$ARGUMENTS`: `<base-branch>` (optional context about which PR phase)

## Critical Rules

- **Never push directly to upstream.** Always use a fork remote.
- **Never ask the user for git credentials.** Use `gh auth status` to check.
- **Never skip pre-flight checks.**
- **Always create a draft PR.**
- **Always work in the project repo directory**, not the workflow directory.

## Process

### Placeholders

| Placeholder | Source | Example |
| --- | --- | --- |
| `AUTH_TYPE` | Step 0 | `user-token` / `github-app` / `none` |
| `GH_USER` | Step 0 | `jsmith` |
| `UPSTREAM_OWNER/REPO` | Step 2c | `openshift/cert-manager-operator` |
| `DEFAULT_BRANCH` | Step 2c | `main` |
| `UPSTREAM_REMOTE` | Step 2b | `origin` |
| `FORK_OWNER` | Step 3 | `jsmith` |
| `FORK_REMOTE` | Step 4 | `fork` |
| `BRANCH_NAME` | Step 5 | `feature/ep-1234-api-types` |

### Step 0: Determine Auth Context

```bash
gh auth status
```

Determine identity:

```bash
# Normal user tokens:
gh api user --jq .login 2>/dev/null

# If that fails (403), running as GitHub App/bot:
gh api /installation/repositories --jq '.repositories[0].owner.login'
```

Record `GH_USER` and `AUTH_TYPE`:

- `gh api user` succeeded → `AUTH_TYPE` = `user-token`
- `gh api user` failed but `/installation/repositories` worked → `AUTH_TYPE` = `github-app`
- `gh auth status` failed → try recovering from expired token via git credential
  helper, then `gh auth login --with-token`. If recovery fails → `AUTH_TYPE` = `none`

### Step 1: Locate the Project Repository

Find the project repo (typically in `/workspace/repos/` or identified from
session context). `cd` into it before proceeding.

### Step 2: Pre-flight Checks

**2a. Git configuration:**

```bash
git config user.name
git config user.email
```

If missing, set from `GH_USER`.

**2b. Inventory remotes:**

```bash
git remote -v
```

**2c. Identify upstream repo and default branch:**

```bash
gh repo view --json nameWithOwner,defaultBranchRef --jq '{nameWithOwner, defaultBranch: .defaultBranchRef.name}'
```

**Do not assume the default branch is `main`.**

**2d. Check changes:**

```bash
git status
git diff --stat
```

**2e. Pre-flight gate (REQUIRED):**

Print filled-in placeholder table before proceeding.

### Step 3: Ensure Fork Exists

```bash
gh repo list GH_USER --fork --json nameWithOwner,parent --jq '.[] | select(.parent.owner.login == "UPSTREAM_OWNER" and .parent.name == "REPO") | .nameWithOwner'
```

If no fork → **HARD STOP**. Ask user to create one at
`https://github.com/UPSTREAM_OWNER/REPO/fork`. Wait for confirmation.

### Step 4: Configure Fork Remote

```bash
git remote add fork https://github.com/FORK_OWNER/REPO.git
```

**Check fork sync status** — if `.github/workflows/` files differ between fork
and upstream, sync the fork:

```bash
gh api --method POST repos/FORK_OWNER/REPO/merge-upstream -f branch=DEFAULT_BRANCH
```

If sync fails, guide user to sync manually via GitHub UI.

### Step 5: Create Branch

Branch naming by PR phase:

- PR #1: `feature/ep-{number}-api-types-{short-description}`
- PR #2: `feature/ep-{number}-controller-{short-description}`
- PR #3: `feature/ep-{number}-e2e-tests-{short-description}`

```bash
git checkout -b BRANCH_NAME
```

### Step 6: Stage and Commit

Stage changes selectively, then commit:

```bash
git commit -m "TYPE(SCOPE): SHORT_DESCRIPTION

DETAILED_DESCRIPTION

Ref: openshift/enhancements#EP_NUMBER"
```

Commit types by phase:

- PR #1: `feat(api): add {Kind} type definitions for EP-{number}`
- PR #2: `feat(controller): implement {Kind} reconciler for EP-{number}`
- PR #3: `test(e2e): add E2E tests for {Kind} EP-{number}`

Include PR description in commit body so GitHub auto-fills the PR form.

### Step 7: Push to Fork

```bash
gh auth setup-git
git push -u FORK_REMOTE BRANCH_NAME
```

### Step 8: Create Draft PR

```bash
gh pr create \
  --draft \
  --repo UPSTREAM_OWNER/REPO \
  --head FORK_OWNER:BRANCH_NAME \
  --base DEFAULT_BRANCH \
  --title "TITLE" \
  --body "BODY"
```

PR title format:

- PR #1: `[EP-{number}] API: Add {Kind} type definitions and integration tests`
- PR #2: `[EP-{number}] Controller: Implement {Kind} reconciler`
- PR #3: `[EP-{number}] E2E: Add end-to-end tests for {Kind}`

PR body should reference: EP URL, what was generated, files changed, review
verdicts, and links to related PRs.

**If `gh pr create` fails** (403, "Resource not accessible by integration"):

1. Write PR description to `artifacts/operator-feature-dev/{api|impl|e2e}/pr-description.md`
2. Provide pre-filled GitHub compare URL:

   ```text
   https://github.com/UPSTREAM_OWNER/REPO/compare/DEFAULT_BRANCH...FORK_OWNER:BRANCH_NAME?expand=1&title=URL_ENCODED_TITLE&body=URL_ENCODED_BODY
   ```

3. Provide clone-and-checkout commands for local testing

### Step 9: Confirm and Report

Summarize: PR URL (or compare URL), what was included, target branch,
follow-up actions.

## Fallback Ladder

1. **Fix and Retry** — diagnose the specific cause and retry
2. **Manual PR via Compare URL** — branch is pushed but `gh pr create` failed
3. **User Creates Fork** — automated forking failed, user creates manually
4. **Patch File** — absolute last resort if all else fails

## Error Recovery

| Symptom | Cause | Fix |
| --- | --- | --- |
| `gh auth status` fails | Not logged in | `gh auth login` |
| `git push` permission denied | Pushing to upstream | Switch to fork remote |
| `git push` workflow permission error | Fork out of sync | Sync fork first |
| `gh pr create` 403 | Bot lacks upstream access | Use compare URL |
| Branch not found on remote | Push failed silently | Re-run `git push` |

## Output

- PR URL printed to user
- PR description saved to `artifacts/operator-feature-dev/{api|impl|e2e}/pr-description.md`
