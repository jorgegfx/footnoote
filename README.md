# Footnote

Footnote is an AI search engine that answers questions **only from trusted news sources**. It queries a curated set of sources in parallel (The Guardian, NYT, GDELT, plus a web search restricted to whitelisted outlets), ranks and deduplicates the results, and writes an answer in which every claim carries a numbered citation. A second pass checks each cited sentence against its sources and removes anything they don't support.

If the trusted sources don't contain enough evidence, Footnote says so instead of filling the gap.

## Quick start

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                     # install dependencies
cp .env.example .env        # then fill in .env (see below)
uv add langchain-anthropic  # the integration package for your LLM provider
uv run uvicorn footnote.main:app --reload
```

Open <http://localhost:8000>, type a question, and press **Search**.

### Minimum configuration

Edit `.env`. To get answers you need an LLM and a web search key:

```dotenv
# Any provider supported by LangChain's init_chat_model. Example: Anthropic
LLM_PROVIDER=anthropic
LLM_MODEL_FAST=claude-haiku-5-5      # planning and relevance scoring
LLM_MODEL_STRONG=claude-sonnet-5-5   # writing and fact-checking the answer
ANTHROPIC_API_KEY=...

SEARCH_PROVIDER=tavily               # the only provider implemented so far
SEARCH_API_KEY=...                   # https://tavily.com

CONTACT_EMAIL=you@example.org        # sent in the User-Agent
```

For another provider, set `LLM_PROVIDER` (for example `openai`), install its package (`uv add langchain-openai`), and set its API key variable.

To run against a local model in [LM Studio](https://lmstudio.ai/), start its server (Developer tab, Start Server), load the models, and use its OpenAI-compatible endpoint:

```dotenv
LLM_PROVIDER=openai
LLM_BASE_URL=http://localhost:1234/v1
LLM_MODEL_FAST=qwen2.5-7b-instruct   # model ids as listed by GET /v1/models
LLM_MODEL_STRONG=qwen/qwen3-14b
OPENAI_API_KEY=lm-studio             # any non-empty value; LM Studio ignores it
```

Pick models that support structured (JSON schema) output; planning, ranking and verification depend on it.

To use [Ollama Cloud](https://ollama.com/), install `uv add langchain-ollama` and create an API key at ollama.com/settings/keys:

```dotenv
LLM_PROVIDER=ollama
LLM_BASE_URL=https://ollama.com
LLM_MODEL_FAST=gemma4:31b
LLM_MODEL_STRONG=gemma4:31b   # models must write citations as [n]
LLM_STRUCTURED_OUTPUT_METHOD=function_calling   # Ollama Cloud doesn't enforce JSON schemas
OLLAMA_API_KEY=...
```

If the logs show `relevance scoring failed` or `verification failed (OutputParserException)`, the model isn't returning the requested structure; try `LLM_STRUCTURED_OUTPUT_METHOD=function_calling` or another model.

### Optional source keys

GDELT works without a key. The Guardian and NYT are skipped until you add theirs:

| Variable | Source | Notes |
|---|---|---|
| `GUARDIAN_API_KEY` | The Guardian | Required for this source. Free at open-platform.theguardian.com |
| `NYT_API_KEY` | New York Times | Required for this source. developer.nytimes.com |

To see which sources are ready, check `GET /health`.

## Using it

### Web UI

The page at `/` streams the whole search as it happens:

1. **Plan**: the search queries Footnote decided to run.
2. **Sources**: each source reports back with its result count, or a quiet error such as "timed out". One failing source never fails the search.
3. **Answer**: the draft streams in, labeled *checking citations*. It is then replaced by the verified answer, which notes how many unsupported claims were removed.
4. **Source cards**: title, outlet, date, and how many other outlets carried the same story. Click any `[n]` in the answer to jump to its card.

Searches are shareable as links: `http://localhost:8000/?q=what+did+the+fed+decide`.

### API

**One-shot answer**

```bash
curl -s localhost:8000/api/search \
  -H 'Content-Type: application/json' \
  -d '{"question": "What did the Fed decide at its last meeting?"}'
```

Optional body fields:
- `max_results`: number of sources to keep, 1–30 (default 12).
- `time_window`: for example `{"start": "2026-01-01", "end": "2026-06-30"}`.

The response contains:
- `text`: markdown with `[n]` citations.
- `citations` and `sources`: what each `[n]` points to.
- `unsupported_claims_removed`: how many sentences verification removed.
- `source_errors` and `timings_ms`.

**Streaming (Server-Sent Events)**

```bash
curl -N 'localhost:8000/api/search/stream?q=What+is+the+latest+on+EU+AI+regulation%3F'
```

Query parameters are `q`, `max_results`, `start` and `end`. Events arrive in this order:

| Event | Meaning |
|---|---|
| `plan` | The queries to run and any time window |
| `source_started`, `source_done`, `source_error` | Progress for each source |
| `ranked` | The final numbered source list |
| `token` | Draft answer text, before verification |
| `answer` | The final verified answer (same shape as `POST /api/search`) |
| `error` | Something went wrong; includes `status` and `message` |
| `done` | The stream is finished |

**Other endpoints**

| Endpoint | Purpose |
|---|---|
| `GET /api/sources` | Enabled sources, with family, trust weight and domains |
| `GET /health` | Liveness, whether the LLM is configured, and which sources have credentials |
| `GET /docs` | Interactive OpenAPI docs |

Questions must be 3–500 characters. If the question can't be turned into a search, you get a 422 with a hint to rephrase. Each client is limited to 30 searches per minute by default; over the limit you get a 429 with `Retry-After`.

## Configuring trusted sources

Sources live in [`config/sources.yaml`](config/sources.yaml). You can change them without touching code:

```yaml
- id: reuters
  family: news
  kind: search          # native | search
  domains: [reuters.com]
  trust: 0.95           # 0–1, multiplied into the ranking score
  enabled: true
```

- To turn a source off, set `enabled: false`.
- To change how much a source counts in ranking, change `trust`.
- To trust a new outlet for web search, add a `kind: search` entry with its `domains`.

Every web search result is re-checked against this list, and anything off-list is dropped. Restart the server after editing the file.

## Other settings

| Variable | Default | Purpose |
|---|---|---|
| `ADAPTER_TIMEOUT_SECONDS` | 8 | Time limit for each source |
| `CACHE_TTL_SECONDS` | 900 | How long successful source results are cached in memory |
| `HTTP_MAX_RETRIES` | 2 | Retries on connection errors and 429/5xx responses |
| `SEARCH_RATE_LIMIT_PER_MINUTE` | 30 | Searches per client IP per minute; `0` disables the limit |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `text` | Set `LOG_FORMAT=json` for structured logs. API keys are always redacted |

## Development

```bash
uv run pytest                                                      # unit and graph tests (offline)
uv run ruff check . && uv run ruff format --check . && uv run mypy footnote
uv run python -m tests.evals.run --limit 5                         # eval suite (real APIs; needs keys)
```

The eval suite runs 30 golden questions and scores citation precision, whitelist violations (must be 0), "insufficient evidence" handling, source diversity and latency. Use `--category contested` to run one group, or `--no-judge` to skip the citation check. Summaries are written to `tests/evals/results/`.

For architecture, conventions and how to add a source, see [CLAUDE.md](CLAUDE.md).
