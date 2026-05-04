"""Shared data models for the benchmark pipeline."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PRDetail:
    number: int
    merge_sha: str
    parent_sha: str
    merged_at: str
    files: list[str]


@dataclass
class PRTimeline:
    earliest_parent_sha: str
    latest_merge_sha: str
    all_changed_files: list[str]
    pr_details: list[PRDetail]


@dataclass
class IsolatedEnv:
    path: Path
    baseline_sha: str
    all_changed_files: list[str]
    warnings: list[str] = field(default_factory=list)


@dataclass
class GroundTruth:
    path: Path
    combined_diff: str
    files_added: list[str]
    files_modified: list[str]
    diff_hunks: dict[str, str] = field(default_factory=dict)


@dataclass
class GenerationResult:
    iteration: int
    diff: str
    files_created: list[str]
    files_modified: list[str]
    build_success: bool
    gen_dir: Path
    agent_log: str = ""
    cost_usd: float = 0.0
    tool_version: str = "original"  # "original", "improved-v1", "improved-v2", ...


@dataclass
class ToolImprovement:
    iteration: int
    files_changed: dict[str, str]  # filename -> unified diff of changes
    analysis_summary: str = ""
    improvement_cost_usd: float = 0.0


@dataclass
class ASTElement:
    name: str
    kind: str  # "struct", "field", "function", "const", "marker"
    details: dict = field(default_factory=dict)


@dataclass
class OutperformanceFinding:
    category: str  # "markers", "validation", "naming", "docs", "tests"
    severity: str  # "informational", "actionable"
    file: str
    description: str
    generated_snippet: str = ""
    truth_snippet: str = ""


@dataclass
class FileClassification:
    """Classification of extra files (generated but not in ground truth)."""
    auto_generated: list[str] = field(default_factory=list)
    formatting_only: list[str] = field(default_factory=list)
    valuable_extra: list[str] = field(default_factory=list)
    genuinely_wrong: list[str] = field(default_factory=list)


@dataclass
class IterationScore:
    iteration: int
    completeness: float
    precision: float       # raw precision (all extras penalized)
    adjusted_precision: float  # excludes auto-generated and formatting-only extras
    convention_compliance: float
    build_success: bool
    file_true_positives: int = 0
    file_false_negatives: int = 0
    file_false_positives: int = 0
    file_classification: FileClassification = field(default_factory=FileClassification)


@dataclass
class BenchmarkResult:
    ep_url: str
    repo_url: str
    description: str
    implementation_prs: list[int]
    iteration_scores: list[IterationScore] = field(default_factory=list)
    outperformance_findings: list[OutperformanceFinding] = field(default_factory=list)
    stable_elements: list[str] = field(default_factory=list)
    unstable_elements: list[str] = field(default_factory=list)
    score_variance: dict[str, float] = field(default_factory=dict)
    best_iteration: int = 0
    median_completeness: float = 0.0
    median_precision: float = 0.0
    tool_improvements: list[ToolImprovement] = field(default_factory=list)
    mode: str = "feedback_loop"  # "feedback_loop" or "measurement"


@dataclass
class BenchmarkCase:
    ep_url: str
    repo_url: str
    description: str
    implementation_prs: list[int]


@dataclass
class BenchmarkConfig:
    cases: list[BenchmarkCase]
    tools_to_benchmark: list[str]
    iterations: int
    output_dir: str
    parallel: bool
    model: str = "claude-opus-4-6"
    effort: str = "max"
