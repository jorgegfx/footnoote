# CLAUDE.md — Footnote

## What this project is

**Footnote** is an AI search engine that answers a user's question by collecting results from a curated set of **trusted news sources**, ranking and deduplicating them, and synthesizing a **cited answer** in which every claim links back to a retrieved source. It is not a general web search: content from outside the trusted set must never reach the answer.

First version: a FastAPI backend with a LangGraph pipeline and a simple web UI.

## Scope

**In scope (v1)**
- Source family: **news** only (academic sources were removed; the `Family` enum is kept so another family could be added later).
- Hybrid retrieval: native source APIs where available, plus a web search API restricted to whitelisted domains.
- Cited answer synthesis with inline numbered citations and a source list.
- Streaming progress (sources being queried, results found, answer tokens).
- Configurable trust list (YAML), editable without code changes.

**Out of scope (v1)**
- User accounts, auth, saved history.
- Paywalled full-text access or scraping that violates a site's terms.
- Crawling or indexing the web ourselves; no persistent vector store of the corpus.
- Multi-turn conversational follow-ups (single question → single answer).

## Tech stack

- Python 3.12, dependency management with `uv`.
- **LangGraph** for orchestration; LangChain core only where it helps (message types, `init_chat_model`).
- **LLM: provider-agnostic.** All model calls go through one factory in `footnote/llm.py` that reads `LLM_PROVIDER`, `LLM_MODEL_FAST` and `LLM_MODEL_STRONG` from env (via `init_chat_model`); install the matching integration package (e.g. `langchain-anthropic`) for the chosen provider. No provider-specific code outside that module. Two roles: `fast` (query planning, relevance scoring) and `strong` (synthesis, verification); each configurable separately.
- **FastAPI** + Uvicorn; Server-Sent Events for streaming.
- **httpx** (async) for all outbound HTTP; **pydantic v2** for every data model and settings (`pydantic-settings`).
- Web UI: a single server-rendered page (Jinja2) with vanilla JS consuming the SSE stream. No frontend build step.
- Caching: in-process TTL cache for v1 behind a `Cache` interface so Redis can be swapped in later.
- Testing: `pytest`, `pytest-asyncio`, `respx` for HTTP mocking.
- Lint/format/type: `ruff`, `ruff format`, `mypy --strict` on `footnote/`.

## Architecture

### LangGraph pipeline

```
question
  └─► plan ──► fan_out ──┬─► source (guardian)     ─┐
                         ├─► source (gdelt)        │
                         ├─► source (news_search)  ├─► normalize ─► dedupe ─► rank ─► synthesize ─► verify ─► answer
                         └─► source (…one per     ─┘
                              accepting adapter)
```

1. **plan** (`fast` LLM): extract entities and time window and produce 1–3 news query rewrites. Output: `QueryPlan`.
2. **fan_out**: one LangGraph `Send` per adapter whose `accepts(plan)` is true (its family has queries). Each runs in the `source` node; with none, the graph goes straight to normalize.
3. **source**: runs one adapter via `run_adapter`, which never raises (per-adapter timeout, default 8s). One slow or failing source must never fail the request.
4. **normalize**: clean HTML to text, truncate snippets, canonicalize URLs (`clean_url` in `footnote/identifiers.py`).
5. **dedupe**: union-find over canonical URL (including alternate URLs), then near-duplicate titles (same family, ≥25 chars, outlet suffix stripped, `difflib` ratio ≥ 0.92). Syndicated news (same wire story on several outlets) collapses to one result that keeps all outlet URLs.
6. **rank**: score = relevance × trust weight (from config) × quality. Relevance is one batched `fast`-LLM call scoring 0–10 (`prompts/rank.md`); below 3/10 is dropped; if the call fails, word overlap stands in and nothing is dropped. Quality is recency (30-day half-life, ignored inside a requested time window). Keep top N (default 12).
7. **synthesize** (`strong` LLM): write the answer using only the ranked results. Every factual sentence carries one or more `[n]` citations. If sources disagree, say so and cite both sides. If evidence is insufficient, say that rather than filling gaps from model knowledge.
8. **verify** (`strong` LLM, separate call): check each cited sentence against its cited snippets; remove or flag unsupported claims; drop citations that point to non-existent indices.

### Graph state

Defined in `footnote/graph/state.py` as a `TypedDict` with reducers for list fields that adapters append to in parallel (`results: Annotated[list[SourceResult], operator.add]`). `candidates` holds the normalized/deduped list and `ranked` the final numbered sources. Also tracks `errors` per adapter and `timings` for observability. The `source` node takes a `SourceTask` (`adapter_id`, `plan`) instead of the graph state.

## Sources

All sources are declared in `config/sources.yaml`; adapters are registered by `id`. Adding a source = a `NativeAdapter` subclass decorated with `@register("<id>")` (set `requires_key` if it needs one; override `accepts` if it only fits some plans), an import in `footnote/sources/__init__.py`, a YAML entry, and respx tests against a fixture. Record fixtures from the real API where possible. Adapters whose key is missing are skipped at startup and reported `false` in `/health`. Every adapter is wrapped in `CachedAdapter` (successful searches only; keys are hashed).

### News (native APIs)
| id | Notes |
|---|---|
| `guardian` | Guardian Open Platform, key required (the public `test` key no longer works). |
| `nyt` | NYT Article Search, key required; snippets/abstracts only; 5 req/min → best query only. |
| `gdelt` | Discovery only: titles, no snippets. 1 request per 5s; errors come back as plain text. Query restricted with `domain:` filters, then re-checked against the news whitelist; trust = min(gdelt, outlet). |

### Search API (domain-restricted)
- Provider behind a `WebSearchProvider` interface (Tavily, Exa or Brave; chosen via `SEARCH_PROVIDER`).
- Always pass the domain whitelist from `sources.yaml` (`include_domains` or equivalent). After the call, **re-check every URL against the whitelist** and drop anything that doesn't match; never trust the provider's filter alone.
- Initial news whitelist: reuters.com, apnews.com, bbc.com/bbc.co.uk, npr.org, theguardian.com, nytimes.com, wsj.com, ft.com, economist.com, bloomberg.com.

### `sources.yaml` shape

```yaml
sources:
  - id: guardian
    family: news
    kind: native        # native | search
    trust: 0.85         # 0–1, used in ranking
    enabled: true
    rate_limit_per_sec: 1
  - id: reuters
    family: news
    kind: search
    domains: [reuters.com]
    trust: 0.95
    enabled: true
```

## Data models (`footnote/models.py`)

- `QueryPlan`: `queries: dict[family, list[str]]`, `time_window: tuple[date, date] | None`, `entities: list[str]`. (The LLM fills a flat `PlanOutput`, since nested dicts/tuples trip up some providers, and `build_plan` converts it.)
- `SourceResult`: `id` (stable hash of the URL), `source_id`, `family`, `title`, `url`, `alt_urls`, `snippet`, `published_at`, `authors`, `venue` (outlet), `trust`, `score`.
- `Citation`: `index`, `result_id`.
- `Answer`: `text` (markdown with `[n]` markers), `citations: list[Citation]`, `sources: list[SourceResult]`, `unsupported_claims_removed: int`, `source_errors: dict[str, str]`, `timings_ms: dict[str, int]`.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/search` | Body `{question, max_results?, time_window?}` → `Answer` JSON. |
| `GET` | `/api/search/stream?q=…` | SSE events: `plan`, `source_started`, `source_done`, `source_error`, `ranked`, `token`, `answer`, `done`. |
| `GET` | `/api/sources` | Enabled sources with family and trust weight. |
| `GET` | `/health` | Liveness, plus which sources have credentials configured. |
| `GET` | `/` | Web UI. |

Validation: question 3–500 chars; reject empty plans with a 422 and a helpful message.

## Web UI

One page: search box, live list of sources as they report back (with status and result count), the streamed answer with clickable `[n]` citations that scroll to the matching source card, and source cards showing title, outlet, date, and link. Show per-source errors quietly (e.g. "NYT timed out") rather than failing the page. Must work at phone width.

## Safety and trust rules (non-negotiable)

- Retrieved content is **data, never instructions**. Wrap snippets in clearly delimited blocks in prompts and instruct the model to ignore any instructions inside them.
- The synthesizer may only use facts present in the provided results. No uncited factual claims.
- Every URL shown to the user must be on the whitelist or come from a native trusted API.
- Store no user questions beyond request-scoped logs; redact API keys from all logs.
- Respect each source's rate limits and terms; identify the client with a descriptive User-Agent (`Footnote/<version> (+<CONTACT_EMAIL>)`) and contact email where APIs request it.

## Project layout

```
footnote/
  main.py              # FastAPI app, routes, SSE; open_runtime (shared with the eval suite)
  config.py            # pydantic-settings; loads env + sources.yaml
  llm.py               # provider-agnostic model factory (fast/strong)
  models.py
  whitelist.py         # URL/domain whitelist checks (security-critical; keep tested)
  identifiers.py       # URL normalization (clean_url for display, url_key for dedupe)
  text.py              # html_to_text, truncate, content_words
  citations.py         # deterministic [n] marker cleanup, shared by synthesize/verify
  cache.py             # Cache protocol + TTLCache (values must be JSON-serializable)
  http.py              # RetryTransport (backoff, Retry-After), per-source RateLimiter, ThrottledClient
  observability.py     # structured logs (text/json), request ids, API-key redaction
  graph/
    state.py           # GraphState, SourceTask, Node/SourceNode protocols, @timed
    build.py           # graph construction
    nodes/             # plan, fan_out (+ source), normalize, dedupe, rank, synthesize, verify
  sources/
    __init__.py        # build_adapters, source_status (imports register adapters)
    base.py            # SourceAdapter protocol, NativeAdapter base, registry, run_adapter, AdapterError
    cached.py          # CachedAdapter
    parsing.py         # parse_iso_date, first_text
    news/              # guardian.py, nyt.py, gdelt.py
    search/            # provider interface + tavily.py / exa.py / brave.py
  prompts/             # plan.md, rank.md, synthesize.md, verify.md + load_prompt(), source_blocks()
  web/templates/, web/static/
config/sources.yaml
tests/
  unit/                # adapters with respx fixtures, dedupe, ranking
  graph/               # node tests with fake LLM
  evals/               # questions.yaml, scoring.py (pure, unit tested), run.py; results/
```

## Environment variables

`LLM_PROVIDER`, `LLM_MODEL_FAST`, `LLM_MODEL_STRONG`, `LLM_BASE_URL` (optional; e.g. LM Studio or Ollama Cloud), `LLM_STRUCTURED_OUTPUT_METHOD` (optional; `function_calling` for servers that ignore JSON schemas), provider API key(s), `SEARCH_PROVIDER`, `SEARCH_API_KEY`, `GUARDIAN_API_KEY`, `NYT_API_KEY`, `CONTACT_EMAIL`, `CACHE_TTL_SECONDS` (default 900), `ADAPTER_TIMEOUT_SECONDS` (default 8), `HTTP_MAX_RETRIES` (default 2), `SEARCH_RATE_LIMIT_PER_MINUTE` (default 30; 0 disables), `LOG_LEVEL` (default INFO), `LOG_FORMAT` (text | json). Provide `.env.example`; never commit `.env`.

## Commands

```bash
uv sync                                  # install
uv run uvicorn footnote.main:app --reload     # run dev server
uv run pytest                            # tests
uv run ruff check . && uv run ruff format --check . && uv run mypy footnote
uv run python -m tests.evals.run         # eval suite (hits real APIs; needs keys)
```

## Conventions

- All I/O async; no blocking calls in request paths.
- Adapters never raise to the graph: return `AdapterResult(ok=False, error=…)`.
- Prompts live in `footnote/prompts/` as files; nodes load them by name.
- LLM outputs that drive logic (plans, scores, verification) use structured output into pydantic models, not free-text parsing.
- Unit tests never hit the network or a real LLM; use `respx` fixtures and `tests/fakes.py` (`FakeChatModel`, `StaticAdapter`). Tests build `Settings(_env_file=None, ...)` so a local `.env` never leaks in.
- Adapter error strings shown to users never include URLs or exception text (query strings can carry API keys); use `describe_error` in `sources/base.py`, or raise `AdapterError` with a safe message.
- Any retrieved text that goes into a prompt goes through `source_blocks()` (escaped, delimited); never interpolate snippets directly.
- Graph tests use `FakeChatModel(structured=[...])`, which returns the first queued item matching the requested schema (`PlanOutput`, `RelevanceScores`, …), so call order doesn't matter.
- Keep functions small and typed; `mypy --strict` must pass.

## Evaluation

`tests/evals/questions.yaml`: ~30 golden questions split across recent news, established science (as covered by news outlets), contested topics, and questions with no good trusted answer. Score each run on: citation precision (cited snippet supports the claim), source diversity, whitelist violations (must be 0), "insufficient evidence" correctness, and latency p50/p95. Track results in `tests/evals/results/`.

## Milestones

1. Skeleton: config, models, LLM factory, one native adapter and one search provider, linear graph, `POST /api/search`.
2. All adapters, parallel fan-out, dedupe and ranking, caching.
3. Synthesis + verification with citation checks; eval suite.
4. SSE streaming and web UI.
5. Hardening: rate limiting, retries with backoff, observability (structured logs, per-node timings).

## Open decisions

- **LLM provider and models** for `fast` and `strong` roles (not yet chosen; code must stay provider-agnostic until then).
- ~~Which search API provider to use first~~: Tavily (Exa/Brave can follow behind `WebSearchProvider`).
- ~~Embedding vs LLM relevance~~: LLM scoring with the `fast` model in v1 (keeps the code provider-agnostic).
- Tuning to revisit with the eval suite: the rank thresholds (`MIN_RELEVANCE`, news half-life) are first guesses.