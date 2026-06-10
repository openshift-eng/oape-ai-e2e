"""Workflow state and result dataclasses."""

from dataclasses import dataclass, field


@dataclass
class PRResult:
    """Result of a single PR creation."""

    pr_number: int
    pr_url: str
    branch_name: str
    title: str


@dataclass
class WorkflowState:
    """Mutable state passed between phases."""

    ep_url: str
    repo_url: str
    base_branch: str
    working_dir: str

    repo_local_path: str = ""
    api_branch_name: str = ""
    api_pr: PRResult | None = None
    phase1_summary: str = ""

    controller_pr: PRResult | None = None
    e2e_pr: PRResult | None = None

    @property
    def all_prs(self) -> list[PRResult]:
        return [pr for pr in [self.api_pr, self.controller_pr, self.e2e_pr] if pr is not None]


@dataclass
class WorkflowResult:
    """Result returned after running the full workflow."""

    output: str
    cost_usd: float
    error: str | None = None
    conversation: list[dict] = field(default_factory=list)
    prs: list[PRResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.error is None
