// Footnote web UI: consumes /api/search/stream (SSE over fetch) and renders progress,
// the streamed answer with clickable [n] citations, and source cards.
// All retrieved and generated text is escaped before it touches the DOM.
"use strict";

const SOURCE_NAMES = {
  guardian: "The Guardian",
  nyt: "New York Times",
  gdelt: "GDELT",
  news_search: "News web search",
};

const $ = (id) => document.getElementById(id);
const els = {
  form: $("search"), q: $("q"), submit: $("submit"), notice: $("notice"),
  progress: $("progress"), phase: $("phase"), queries: $("queries"), status: $("source-status"),
  answerSection: $("answer-section"), answer: $("answer"), draftLabel: $("draft-label"),
  answerMeta: $("answer-meta"), sourcesSection: $("sources-section"), sources: $("sources"),
};

let controller = null;

function sourceName(id) {
  return SOURCE_NAMES[id] || id;
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function safeUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

// --- Minimal, escape-first markdown: paragraphs, lists, headings, bold/italic/code, [n] ---

function inline(text, sourceCount) {
  let html = escapeHtml(text);
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
  html = html.replace(/\[(\d+)\]/g, (match, n) => {
    const index = Number(n);
    if (sourceCount && (index < 1 || index > sourceCount)) return "";
    return `<a class="cite" href="#source-${index}" data-index="${index}" aria-label="Source ${index}">[${index}]</a>`;
  });
  return html;
}

function renderMarkdown(text, sourceCount) {
  const out = [];
  let list = null; // {tag, items}
  let para = [];

  const flushPara = () => {
    if (para.length) out.push(`<p>${inline(para.join(" "), sourceCount)}</p>`);
    para = [];
  };
  const flushList = () => {
    if (list) out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i, sourceCount)}</li>`).join("")}</${list.tag}>`);
    list = null;
  };

  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const bullet = line.match(/^[-*+]\s+(.*)$/);
    const numbered = line.match(/^\d+[.)]\s+(.*)$/);
    const heading = line.match(/^#{1,6}\s+(.*)$/);
    if (!line) {
      flushPara(); flushList();
    } else if (bullet || numbered) {
      flushPara();
      const tag = bullet ? "ul" : "ol";
      if (list && list.tag !== tag) flushList();
      if (!list) list = { tag, items: [] };
      list.items.push((bullet || numbered)[1]);
    } else if (heading) {
      flushPara(); flushList();
      out.push(`<h3>${inline(heading[1], sourceCount)}</h3>`);
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara(); flushList();
  return out.join("");
}

// --- Rendering ---

function show(el, visible = true) {
  el.hidden = !visible;
}

function setPhase(text, busy = true) {
  els.phase.textContent = text;
  els.phase.classList.toggle("busy", busy);
}

function setNotice(text) {
  els.notice.textContent = text || "";
  show(els.notice, Boolean(text));
}

function statusItem(id) {
  let item = els.status.querySelector(`[data-source="${CSS.escape(id)}"]`);
  if (!item) {
    item = document.createElement("li");
    item.dataset.source = id;
    els.status.appendChild(item);
  }
  return item;
}

function formatDate(value) {
  if (!value) return null;
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function renderSources(sources) {
  els.sources.innerHTML = "";
  sources.forEach((source, i) => {
    const index = i + 1;
    const url = safeUrl(source.url);
    const outlet = source.venue || sourceName(source.source_id);
    const meta = [
      `<span>${escapeHtml(outlet)}</span>`,
      source.published_at ? `<span>${escapeHtml(formatDate(source.published_at))}</span>` : "",
      source.authors && source.authors.length
        ? `<span>${escapeHtml(source.authors.slice(0, 3).join(", "))}${source.authors.length > 3 ? " et al." : ""}</span>`
        : "",
      source.alt_urls && source.alt_urls.length
        ? `<span>also at ${source.alt_urls.length} other link${source.alt_urls.length > 1 ? "s" : ""}</span>`
        : "",
    ].join("");
    const title = escapeHtml(source.title);
    const li = document.createElement("li");
    li.className = "source";
    li.id = `source-${index}`;
    li.innerHTML = `
      <span class="source-index">[${index}]</span>
      ${url ? `<a class="source-title" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${title}</a>` : `<span class="source-title">${title}</span>`}
      <div class="source-meta">${meta}</div>
      ${source.snippet ? `<p class="source-snippet">${escapeHtml(truncate(source.snippet, 240))}</p>` : ""}`;
    els.sources.appendChild(li);
  });
  show(els.sourcesSection, sources.length > 0);
}

function truncate(text, max) {
  return text.length <= max ? text : `${text.slice(0, max).replace(/\s+\S*$/, "")}…`;
}

function renderAnswer(text, sourceCount, draft) {
  els.answer.innerHTML = renderMarkdown(text, sourceCount);
  els.answer.classList.toggle("draft", draft);
  show(els.draftLabel, draft);
  show(els.answerSection, Boolean(text));
}

// --- Streaming ---

function reset() {
  setNotice("");
  els.queries.innerHTML = "";
  els.status.innerHTML = "";
  els.answer.innerHTML = "";
  els.answerMeta.textContent = "";
  els.sources.innerHTML = "";
  show(els.answerSection, false);
  show(els.sourcesSection, false);
  show(els.progress, true);
  setPhase("Planning the search…");
}

function handlers() {
  let draft = "";
  let sourceCount = 0;
  let pending = 0;

  return {
    plan(plan) {
      els.queries.innerHTML = "";
      for (const queries of Object.values(plan.queries)) {
        for (const query of queries) {
          const li = document.createElement("li");
          li.textContent = query;
          els.queries.appendChild(li);
        }
      }
      setPhase("Searching trusted sources…");
    },
    source_started({ source }) {
      pending += 1;
      statusItem(source).textContent = `${sourceName(source)} …`;
    },
    source_done({ source, count }) {
      pending -= 1;
      const item = statusItem(source);
      item.className = "done";
      item.textContent = `${sourceName(source)} · ${count} result${count === 1 ? "" : "s"}`;
      if (pending === 0) setPhase("Ranking results…");
    },
    source_error({ source, error }) {
      pending -= 1;
      const item = statusItem(source);
      item.className = "error";
      item.textContent = `${sourceName(source)} ${error}`;
      if (pending === 0) setPhase("Ranking results…");
    },
    ranked({ sources }) {
      sourceCount = sources.length;
      renderSources(sources);
      setPhase(sources.length ? "Writing the answer…" : "Nothing relevant found.", sources.length > 0);
    },
    token({ text }) {
      draft += text;
      renderAnswer(draft, sourceCount, true);
    },
    answer(answer) {
      renderSources(answer.sources);
      renderAnswer(answer.text, answer.sources.length, false);
      const parts = [];
      if (answer.unsupported_claims_removed) {
        const n = answer.unsupported_claims_removed;
        parts.push(`${n} unsupported claim${n === 1 ? "" : "s"} removed during verification`);
      }
      if (answer.timings_ms && answer.timings_ms.total) {
        parts.push(`${(answer.timings_ms.total / 1000).toFixed(1)}s`);
      }
      els.answerMeta.textContent = parts.join(" · ");
      setPhase(`${answer.sources.length} source${answer.sources.length === 1 ? "" : "s"} used.`, false);
    },
    error({ message }) {
      setNotice(message);
      setPhase("", false);
    },
    done() {
      els.phase.classList.remove("busy");
    },
  };
}

function parseEvent(block) {
  let event = "message";
  const data = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  return data.length ? { event, data: JSON.parse(data.join("\n")) } : null;
}

async function errorMessage(response) {
  if (response.status === 429) return "Too many searches in a short time. Please wait a moment.";
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail[0]) return body.detail[0].msg;
  } catch { /* fall through */ }
  return `Search failed (HTTP ${response.status}).`;
}

async function search(question) {
  if (controller) controller.abort();
  controller = new AbortController();
  reset();
  els.submit.disabled = true;

  const params = new URLSearchParams({ q: question });
  const on = handlers();

  try {
    const response = await fetch(`/api/search/stream?${params}`, {
      headers: { Accept: "text/event-stream" },
      signal: controller.signal,
    });
    if (!response.ok) {
      on.error({ message: await errorMessage(response) });
      return;
    }
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value.replace(/\r\n/g, "\n");
      let split;
      while ((split = buffer.indexOf("\n\n")) !== -1) {
        const parsed = parseEvent(buffer.slice(0, split));
        buffer = buffer.slice(split + 2);
        if (parsed && on[parsed.event]) on[parsed.event](parsed.data);
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") on.error({ message: "Lost the connection to the server. Please try again." });
  } finally {
    els.submit.disabled = false;
    els.phase.classList.remove("busy");
  }
}

els.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = els.q.value.trim();
  if (question.length < 3) {
    setNotice("Please enter a question of at least 3 characters.");
    return;
  }
  const url = new URL(window.location.href);
  url.searchParams.set("q", question);
  history.replaceState(null, "", url);
  search(question);
});

// Citation clicks scroll to the source card and flash it.
els.answer.addEventListener("click", (event) => {
  const link = event.target.closest(".cite");
  if (!link) return;
  event.preventDefault();
  const card = $(`source-${link.dataset.index}`);
  if (!card) return;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.classList.add("flash");
  setTimeout(() => card.classList.remove("flash"), 1200);
});

// Support shareable links: /?q=...
const initial = new URLSearchParams(window.location.search).get("q");
if (initial) {
  els.q.value = initial;
  els.form.requestSubmit();
}
