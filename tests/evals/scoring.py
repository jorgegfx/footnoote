"""Pure scoring for eval runs: no network, no LLM, so it is unit tested like the rest."""

import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from footnote.config import SourcesConfig
from footnote.graph.nodes.synthesize import NO_RESULTS
from footnote.graph.nodes.verify import NOTHING_SUPPORTED
from footnote.models import Answer, Family
from footnote.whitelist import host_of

QUESTIONS_PATH = Path(__file__).parent / "questions.yaml"

Category = Literal["recent_news", "established_science", "contested", "no_good_answer"]

# Sources whose URLs come from discovery (web search, GDELT) rather than a trusted native API;
# every URL they contribute must be on the whitelist.
DISCOVERY_SOURCES = frozenset({"news_search", "academic_search", "gdelt"})

_INSUFFICIENT = re.compile(
    r"(?i)\b(?:insufficient|not enough|don't contain enough|do not contain enough|"
    r"couldn't find|could not find|no (?:reliable |trusted )?(?:evidence|sources?)|"
    r"can't answer|cannot answer|unable to answer)\b"
)


class Question(BaseModel):
    id: str
    category: Category
    question: str
    insufficient: bool = False
    families: list[Family] | None = None


class QuestionScore(BaseModel):
    id: str
    category: Category
    latency_ms: int
    error: str | None = None
    sources: int = 0
    distinct_outlets: int = 0
    whitelist_violations: list[str] = []
    said_insufficient: bool = False
    insufficient_correct: bool = False
    citations_checked: int = 0
    citations_supported: int = 0

    @property
    def citation_precision(self) -> float | None:
        if not self.citations_checked:
            return None
        return self.citations_supported / self.citations_checked


def load_questions(path: Path = QUESTIONS_PATH) -> list[Question]:
    with path.open(encoding="utf-8") as f:
        return [Question.model_validate(q) for q in yaml.safe_load(f)["questions"]]


def says_insufficient(answer: Answer) -> bool:
    """The answer declines for lack of evidence: a sentinel, no citations, or saying so."""
    if answer.text in (NO_RESULTS, NOTHING_SUPPORTED) or not answer.citations:
        return True
    return bool(_INSUFFICIENT.search(answer.text))


def whitelist_violations(answer: Answer, sources: SourcesConfig) -> list[str]:
    """URLs from discovery sources that the trust list doesn't cover. Must always be empty."""
    bad = []
    for result in answer.sources:
        if result.source_id not in DISCOVERY_SOURCES:
            continue
        for url in [result.url, *result.alt_urls]:
            if not any(sources.search_source_for_url(url, family) for family in Family):
                bad.append(url)
    return bad


def distinct_outlets(answer: Answer) -> int:
    """Diversity: distinct venues/outlets (falling back to host) among the shown sources."""
    return len({(r.venue or host_of(r.url) or r.source_id).lower() for r in answer.sources})


def percentile(values: Sequence[float], pct: float) -> float:
    """Nearest-rank percentile; 0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def summarize(scores: Sequence[QuestionScore]) -> dict[str, object]:
    checked = sum(s.citations_checked for s in scores)
    latencies = [s.latency_ms for s in scores]
    by_category: dict[str, dict[str, object]] = {}
    for category in sorted({s.category for s in scores}):
        group = [s for s in scores if s.category == category]
        by_category[category] = {
            "questions": len(group),
            "insufficient_accuracy": sum(s.insufficient_correct for s in group) / len(group),
            "mean_outlets": sum(s.distinct_outlets for s in group) / len(group),
        }
    return {
        "questions": len(scores),
        "errors": sum(s.error is not None for s in scores),
        "citation_precision": (
            sum(s.citations_supported for s in scores) / checked if checked else None
        ),
        "whitelist_violations": sum(len(s.whitelist_violations) for s in scores),
        "insufficient_accuracy": (
            sum(s.insufficient_correct for s in scores) / len(scores) if scores else None
        ),
        "mean_outlets": sum(s.distinct_outlets for s in scores) / len(scores) if scores else 0,
        "latency_ms_p50": percentile(latencies, 50),
        "latency_ms_p95": percentile(latencies, 95),
        "by_category": by_category,
    }
