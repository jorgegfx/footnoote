"""Run the golden questions against the real pipeline and score the answers.

    uv run python -m tests.evals.run [--category contested] [--limit 5] [--no-judge]

Hits the real source APIs and LLM, so it needs a configured `.env`. Questions run one at a
time to respect source rate limits. Per-question rows go to `results/<stamp>.jsonl`
(gitignored); the summary goes to `results/<stamp>-summary.json`, which is meant to be
committed so runs can be compared.

Citation precision re-checks the final answer's cited sentences with the `strong` model
(the same check the verify node runs), so it measures what verification let through rather
than being an independent judge.
"""

import argparse
import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from footnote.citations import cited_indices, split_sentences
from footnote.config import Settings, SourcesConfig, get_settings
from footnote.graph.nodes.plan import EmptyPlanError
from footnote.graph.nodes.verify import judge_claims
from footnote.graph.state import GraphState
from footnote.llm import ModelFactory, model_factory
from footnote.main import Runtime, finish, open_runtime
from footnote.models import Answer
from footnote.observability import configure_logging
from tests.evals.scoring import (
    Question,
    QuestionScore,
    distinct_outlets,
    load_questions,
    says_insufficient,
    summarize,
    whitelist_violations,
)

RESULTS_DIR = Path(__file__).parent / "results"


async def check_citations(models: ModelFactory, question: str, answer: Answer) -> tuple[int, int]:
    """(citations checked, citations supported) for the answer's cited sentences."""
    claims = [(s, cited_indices(s)) for s in split_sentences(answer.text) if cited_indices(s)]
    if not claims or not answer.sources:
        return 0, 0
    verdicts = await judge_claims(models, question, answer.sources, claims)
    checked = sum(len(cites) for _, cites in claims)
    supported = sum(len(verdicts.get(i, set())) for i in range(1, len(claims) + 1))
    return checked, supported


async def score_question(
    runtime: Runtime, models: ModelFactory | None, sources: SourcesConfig, q: Question
) -> tuple[QuestionScore, Answer | None]:
    initial: GraphState = {"question": q.question, "max_results": 12}
    start = time.perf_counter()
    try:
        answer = finish(dict(await runtime.graph.ainvoke(initial)), start)
    except Exception as exc:  # an eval run records failures instead of stopping
        latency = int((time.perf_counter() - start) * 1000)
        # An empty plan is a correct refusal for a question with no good answer.
        refused = isinstance(exc, EmptyPlanError)
        score = QuestionScore(
            id=q.id,
            category=q.category,
            latency_ms=latency,
            error=None if refused else type(exc).__name__,
            said_insufficient=refused,
            insufficient_correct=refused == q.insufficient,
        )
        return score, None

    insufficient = says_insufficient(answer)
    score = QuestionScore(
        id=q.id,
        category=q.category,
        latency_ms=answer.timings_ms.get("total", 0),
        sources=len(answer.sources),
        distinct_outlets=distinct_outlets(answer),
        whitelist_violations=whitelist_violations(answer, sources),
        said_insufficient=insufficient,
        insufficient_correct=insufficient == q.insufficient,
    )
    if models is not None:
        try:
            checked, supported = await check_citations(models, q.question, answer)
            score = score.model_copy(
                update={"citations_checked": checked, "citations_supported": supported}
            )
        except Exception as exc:
            logging.warning("citation check failed for %s (%s)", q.id, type(exc).__name__)
    return score, answer


def row(score: QuestionScore, answer: Answer | None) -> dict[str, Any]:
    data = score.model_dump() | {"citation_precision": score.citation_precision}
    if answer is not None:
        data |= {
            "answer": answer.text,
            "source_urls": [s.url for s in answer.sources],
            "source_errors": answer.source_errors,
            "unsupported_claims_removed": answer.unsupported_claims_removed,
        }
    return data


def print_summary(summary: dict[str, Any]) -> None:
    precision = summary["citation_precision"]
    print(f"\nquestions:             {summary['questions']} ({summary['errors']} errors)")
    print(f"citation precision:    {'n/a' if precision is None else f'{precision:.2f}'}")
    print(f"whitelist violations:  {summary['whitelist_violations']}  (must be 0)")
    print(f"insufficient accuracy: {summary['insufficient_accuracy']:.2f}")
    print(f"mean distinct outlets: {summary['mean_outlets']:.1f}")
    p50, p95 = summary["latency_ms_p50"], summary["latency_ms_p95"]
    print(f"latency p50 / p95:     {p50:.0f} / {p95:.0f} ms")
    for category, stats in summary["by_category"].items():
        print(
            f"  {category:<20} n={stats['questions']:<3} "
            f"insufficient_acc={stats['insufficient_accuracy']:.2f} "
            f"outlets={stats['mean_outlets']:.1f}"
        )


async def run(args: argparse.Namespace, settings: Settings) -> int:
    questions = load_questions()
    if args.category:
        questions = [q for q in questions if q.category == args.category]
    if args.limit:
        questions = questions[: args.limit]

    models = model_factory(settings)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    RESULTS_DIR.mkdir(exist_ok=True)
    rows_path = RESULTS_DIR / f"{stamp}.jsonl"

    scores: list[QuestionScore] = []
    async with open_runtime(settings, models) as runtime:
        with rows_path.open("w", encoding="utf-8") as rows:
            for i, q in enumerate(questions, start=1):
                judge = None if args.no_judge else models
                score, answer = await score_question(runtime, judge, runtime.sources, q)
                scores.append(score)
                rows.write(json.dumps(row(score, answer), default=str) + "\n")
                status = score.error or ("insufficient" if score.said_insufficient else "answered")
                print(f"[{i}/{len(questions)}] {q.id}: {status} ({score.latency_ms} ms)")

    summary = summarize(scores) | {
        "run": stamp,
        "llm": f"{settings.llm_provider}:{settings.llm_model_fast}/{settings.llm_model_strong}",
        "search_provider": settings.search_provider,
    }
    (RESULTS_DIR / f"{stamp}-summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print_summary(summary)
    print(f"\nrows:    {rows_path}\nsummary: {RESULTS_DIR / f'{stamp}-summary.json'}")
    return 1 if summary["whitelist_violations"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--category",
        choices=["recent_news", "established_science", "contested", "no_good_answer"],
    )
    parser.add_argument("--limit", type=int, help="Run only the first N questions.")
    parser.add_argument("--no-judge", action="store_true", help="Skip the citation check.")
    args = parser.parse_args()

    load_dotenv()
    settings = get_settings()
    configure_logging("WARNING", "text", settings.secret_values())
    return asyncio.run(run(args, settings))


if __name__ == "__main__":
    raise SystemExit(main())
