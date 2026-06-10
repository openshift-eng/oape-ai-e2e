# Operator Feature Dev Workflow

Multi-PR OpenShift operator feature development from Enhancement Proposals:

1. **Init** (`/oape.init`) — Clone repo, validate operator, detect framework
2. **API Generate** (`/oape.api-generate`) — Generate API type definitions from EP
3. **API Generate Tests** (`/oape.api-generate-tests`) — Generate integration tests for API types
4. **Review** (`/oape.review`) — Review and auto-fix issues
5. **PR** (`/oape.pr`) — Create pull request
6. **API Implement** (`/oape.api-implement`) — Generate controller/reconciler code
7. **E2E Generate** (`/oape.e2e-generate`) — Generate E2E test artifacts
8. **Speedrun** (`/oape.speedrun`) — Run all remaining phases without stopping
9. **Summary** — Synthesize all artifacts into a final status report

Commands handle atomic operations. The controller skill manages phase
transitions and recommendations. The speedrun command runs all remaining
phases autonomously. Artifacts go in `artifacts/operator-feature-dev/`.

## Principles

- Show code, not concepts. Link to `file:line`, not abstract descriptions.
- Derive from conventions, not memory. Fetch and read OpenShift/Kubernetes API conventions on every run.
- Never guess. If the Enhancement Proposal is ambiguous about API details, stop and ask.
- Be thorough and complete: When generating API types, search for all existing types in the package and match style exactly.
- Don't assume tools are missing. Check for version managers (`uv`, `pyenv`, `nvm`) before concluding a runtime isn't available.

## Hard Limits

- No direct commits to `main` — always use feature branches
- No token or secret logging — use `len(token)`, redact in logs
- No force-push, hard reset, or destructive git operations
- No modifying security-critical code without human review
- No skipping CI checks (`--no-verify`, `--no-gpg-sign`)
- Never generate code with TODOs — produce production-ready implementations
- Never hardcode operator-specific values — discover from the repository

## Safety

- Show your plan with TodoWrite before executing
- Indicate confidence: High (90-100%), Medium (70-89%), Low (<70%)
- Flag risks and assumptions upfront
- Provide rollback instructions for every change

## Quality

- Follow the project's existing coding standards and conventions
- Zero tolerance for test failures — fix them, don't skip them
- Conventional commits: `type(scope): description`
- All PRs include EP reference (e.g., `Ref: openshift/enhancements#1234`)

## Escalation

Stop and request human guidance when:

- Enhancement Proposal is ambiguous about API design
- Multiple valid API approaches exist with unclear trade-offs
- Convention-compliant design conflicts with EP requirements
- An architectural decision is required
- The change affects API contracts or introduces breaking changes
- A security or compliance concern arises
- Confidence on the proposed approach is below 80%

## Working With the Project

This workflow gets deployed into different operator repos. Respect the target project:

- Read and follow the project's own `CLAUDE.md` if one exists
- Adopt the project's coding style, not your own preferences
- Match existing controller patterns exactly
- Use the project's existing test framework and patterns
- When in doubt about project conventions, check git history and existing code
