import { useCallback, useEffect, useRef, useState } from "react";

type RunEvent = { type: string; message: string; timestamp: string };
type SubQuestion = { id: string; question: string; rationale: string };
type Critique = {
  gaps: string[];
  contradictions: string[];
  follow_up_questions: string[];
  ready_to_synthesize: boolean;
};
type Finding = {
  sub_question_id: string;
  summary: string;
  confidence: string;
  rag_hits?: RagHit[];
};
type RagHit = {
  score?: number;
  vector_score?: number;
  swag_score?: number;
  metadata_boost?: number;
  swag_explanation?: string;
  swag_rank?: number;
  relevance?: string;
  text: string;
  source?: string;
  page?: number;
  topic?: string;
  chunk_index?: number;
  document_id?: string;
  id?: string;
};
type SearchMode = "baseline" | "swag" | "crag" | "swag_crag";
type RankChange = {
  key: string;
  baseline_rank: number | null;
  augmented_rank: number | null;
  rank_delta: number | null;
};
type DocumentRow = {
  source: string;
  topic?: string;
  document_id?: string;
  chunk_count: number;
  page_count?: number;
};
type PreviewData = {
  filename: string;
  pages_extracted: number;
  pdf_page_count?: number;
  total_chars: number;
  chunk_count: number;
  avg_chunk_chars: number;
  empty_extraction: boolean;
  preview_chunks: { page?: number; chunk_index: number; char_count: number; text_preview: string }[];
  preview_truncated: boolean;
};

const SEARCH_MODE_LABELS: Record<SearchMode, string> = {
  baseline: "Vector only",
  swag: "Vector → SWAG",
  crag: "Vector → CRAG",
  swag_crag: "Vector → SWAG → CRAG",
};

const PIPELINE_STEPS: Record<SearchMode, string> = {
  baseline: "Vector search",
  swag: "Vector search → SWAG rerank",
  crag: "Vector search → CRAG filter",
  swag_crag: "Vector search → SWAG rerank → CRAG filter",
};

const DEFAULT_COLLECTION = "research_docs";
const SESSION_THREAD_KEY = "researchThreadId";
const SESSION_CHECKPOINT_KEY = "researchCheckpoint";
type Tab = "research" | "ingest" | "search" | "library";

function hitPreview(text: string, max = 180): string {
  const trimmed = text.trim();
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, max - 1)}…`;
}

function renderHitScores(hit: RagHit, mode: SearchMode): string {
  const parts: string[] = [];
  if (hit.score != null) parts.push(`score ${hit.score.toFixed(3)}`);
  if (mode !== "baseline" && hit.vector_score != null) {
    parts.push(`vec ${hit.vector_score.toFixed(3)}`);
  }
  if ((mode === "swag" || mode === "swag_crag") && hit.swag_score != null) {
    parts.push(`swag ${hit.swag_score.toFixed(3)}`);
  }
  if (hit.relevance) parts.push(hit.relevance);
  return parts.join(" · ");
}

function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value
      .map((block) => {
        if (typeof block === "string") return block;
        if (block && typeof block === "object" && "text" in block) {
          return String((block as { text?: unknown }).text ?? "");
        }
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }
  if (value == null) return "";
  return String(value);
}

function normalizeSubQuestions(raw: unknown): SubQuestion[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((item, idx) => {
    const sq = item as Record<string, unknown>;
    return {
      id: asText(sq.id) || `sq-${idx + 1}`,
      question: asText(sq.question),
      rationale: asText(sq.rationale),
    };
  });
}

function normalizeFindings(raw: unknown): Finding[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((item, idx) => {
    const finding = item as Record<string, unknown>;
    return {
      sub_question_id: asText(finding.sub_question_id) || `finding-${idx + 1}`,
      summary: asText(finding.summary),
      confidence: asText(finding.confidence) || "medium",
      rag_hits: Array.isArray(finding.rag_hits)
        ? finding.rag_hits.map((hit) => {
            const h = hit as Record<string, unknown>;
            return {
              score: typeof h.score === "number" ? h.score : undefined,
              text: asText(h.text),
              source: asText(h.source) || undefined,
              page: typeof h.page === "number" ? h.page : undefined,
            };
          })
        : [],
    };
  });
}

function normalizeCritique(raw: unknown): Critique | null {
  if (!raw || typeof raw !== "object") return null;
  const critique = raw as Record<string, unknown>;
  return {
    gaps: Array.isArray(critique.gaps) ? critique.gaps.map(asText) : [],
    contradictions: Array.isArray(critique.contradictions)
      ? critique.contradictions.map(asText)
      : [],
    follow_up_questions: Array.isArray(critique.follow_up_questions)
      ? critique.follow_up_questions.map(asText)
      : [],
    ready_to_synthesize: Boolean(critique.ready_to_synthesize),
  };
}

export default function App() {
  const [tab, setTab] = useState<Tab>("research");
  const [topic, setTopic] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState("");
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [ingestStatus, setIngestStatus] = useState("");
  const [langsmithUrl, setLangsmithUrl] = useState("");
  const [threadId, setThreadId] = useState<string | null>(null);
  const [checkpoint, setCheckpoint] = useState<string | null>(null);
  const [planQuestions, setPlanQuestions] = useState<SubQuestion[]>([]);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [critique, setCritique] = useState<Critique | null>(null);
  const [researchIteration, setResearchIteration] = useState(0);
  const [editedReport, setEditedReport] = useState("");
  const [ragHits, setRagHits] = useState<RagHit[]>([]);
  const [preview, setPreview] = useState<PreviewData | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchMode, setSearchMode] = useState<SearchMode>("swag_crag");
  const [compareBaseline, setCompareBaseline] = useState(true);
  const [searchResults, setSearchResults] = useState<RagHit[]>([]);
  const [searchBaseline, setSearchBaseline] = useState<RagHit[]>([]);
  const [searchCrag, setSearchCrag] = useState<Record<string, unknown> | null>(null);
  const [searchComparison, setSearchComparison] = useState<{
    baseline_count: number;
    augmented_count: number;
    rank_changes: RankChange[];
  } | null>(null);
  const [searchBusy, setSearchBusy] = useState(false);
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [stats, setStats] = useState<{ total_points: number; document_count: number; topics: string[] } | null>(null);
  const [selectedSource, setSelectedSource] = useState<string | null>(null);
  const [selectedChunks, setSelectedChunks] = useState<RagHit[]>([]);
  const hitlRef = useRef<HTMLElement | null>(null);

  const applyHitlState = useCallback((cp: string, state: Record<string, unknown>) => {
    if (cp === "plan") {
      setPlanQuestions(normalizeSubQuestions(state.sub_questions));
    } else if (cp === "critique") {
      setFindings(normalizeFindings(state.findings));
      setCritique(normalizeCritique(state.critique));
    } else if (cp === "report") {
      const nextReport = asText(state.report);
      setEditedReport(nextReport);
      setReport(nextReport);
    }
  }, []);

  const loadHitlState = useCallback(
    async (tid: string, cp?: string | null) => {
      try {
        const resp = await fetch(`/api/research/state?thread_id=${encodeURIComponent(tid)}`);
        if (!resp.ok) return false;
        const data = await resp.json();
        const nextCheckpoint = (cp || data.checkpoint) as string | null;
        if (nextCheckpoint) {
          setCheckpoint(nextCheckpoint);
          sessionStorage.setItem(SESSION_CHECKPOINT_KEY, nextCheckpoint);
        }
        applyHitlState(nextCheckpoint || cp || "", (data.state || {}) as Record<string, unknown>);
        const iteration = (data.state as Record<string, unknown> | undefined)?.iteration;
        if (typeof iteration === "number") setResearchIteration(iteration);
        return true;
      } catch {
        return false;
      }
    },
    [applyHitlState]
  );

  const clearResearchSession = useCallback(() => {
    sessionStorage.removeItem(SESSION_THREAD_KEY);
    sessionStorage.removeItem(SESSION_CHECKPOINT_KEY);
  }, []);

  const loadLibrary = useCallback(async () => {
    const [statsResp, docsResp] = await Promise.all([
      fetch(`/api/rag/stats?collection=${DEFAULT_COLLECTION}`),
      fetch(`/api/rag/documents?collection=${DEFAULT_COLLECTION}${topic ? `&topic=${encodeURIComponent(topic)}` : ""}`),
    ]);
    if (statsResp.ok) setStats(await statsResp.json());
    if (docsResp.ok) {
      const data = await docsResp.json();
      setDocuments(data.documents || []);
    }
  }, [topic]);

  useEffect(() => {
    if (tab === "library") loadLibrary();
  }, [tab, loadLibrary]);

  useEffect(() => {
    const savedThreadId = sessionStorage.getItem(SESSION_THREAD_KEY);
    const savedCheckpoint = sessionStorage.getItem(SESSION_CHECKPOINT_KEY);
    if (savedThreadId && savedCheckpoint) {
      setThreadId(savedThreadId);
      setCheckpoint(savedCheckpoint);
      void loadHitlState(savedThreadId, savedCheckpoint);
    }
  }, [loadHitlState]);

  useEffect(() => {
    if (checkpoint && hitlRef.current) {
      hitlRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [checkpoint, planQuestions.length, findings.length, critique, editedReport]);

  async function previewDocument() {
    if (!file || !topic) return;
    setIngestStatus("Analyzing...");
    setPreview(null);
    const form = new FormData();
    form.append("file", file);
    form.append("topic", topic);
    const resp = await fetch("/api/ingest/preview", { method: "POST", body: form });
    const data = await resp.json();
    if (!resp.ok) {
      setIngestStatus(data.detail || "Preview failed");
      return;
    }
    setPreview(data);
    setIngestStatus("Preview ready — review chunks, then approve indexing.");
  }

  async function uploadDocument() {
    if (!file || !topic) return;
    setIngestStatus("Indexing…");
    const form = new FormData();
    form.append("file", file);
    form.append("topic", topic);
    form.append("collection", DEFAULT_COLLECTION);
    const resp = await fetch("/api/ingest/file", { method: "POST", body: form });
    const data = await resp.json();
    if (!resp.ok) {
      setIngestStatus(data.detail || "Upload failed");
      return;
    }
    setIngestStatus(`Indexed ${data.chunks_indexed} chunks from ${file.name}`);
    setPreview(null);
    if (tab === "library") loadLibrary();
  }

  async function runSearch() {
    if (!searchQuery.trim()) return;
    setSearchBusy(true);
    setSearchResults([]);
    setSearchBaseline([]);
    setSearchCrag(null);
    setSearchComparison(null);
    const resp = await fetch("/api/rag/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: searchQuery,
        collection: DEFAULT_COLLECTION,
        top_k: 8,
        topic: topic || undefined,
        mode: searchMode,
        compare: compareBaseline,
      }),
    });
    const data = await resp.json();
    setSearchBusy(false);
    if (!resp.ok) {
      return;
    }
    setSearchResults(data.hits || []);
    setSearchBaseline(data.baseline_hits || []);
    setSearchCrag(data.crag || null);
    setSearchComparison(data.comparison || null);
  }

  async function loadChunks(source: string) {
    setSelectedSource(source);
    const resp = await fetch(
      `/api/rag/chunks?collection=${DEFAULT_COLLECTION}&source=${encodeURIComponent(source)}&limit=50`
    );
    if (!resp.ok) return;
    const data = await resp.json();
    setSelectedChunks(data.chunks || []);
  }

  async function deleteDocument(source: string) {
    if (!confirm("Delete all chunks for this document from the vector store?")) return;
    const resp = await fetch(
      `/api/rag/documents?collection=${DEFAULT_COLLECTION}&source=${encodeURIComponent(source)}`,
      { method: "DELETE" }
    );
    if (resp.ok) {
      setSelectedSource(null);
      setSelectedChunks([]);
      loadLibrary();
    }
  }

  function pushEvent(type: string, message: string) {
    setEvents((prev) => [...prev, { type, message, timestamp: new Date().toISOString() }]);
  }

  async function consumeSse(resp: Response) {
    if (!resp.ok || !resp.body) {
      setBusy(false);
      pushEvent("error", "Request failed");
      return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        try {
          const evt = JSON.parse(line.slice(6));
          if (evt.type === "thread") {
            setThreadId(evt.thread_id);
            sessionStorage.setItem(SESSION_THREAD_KEY, evt.thread_id);
          } else if (evt.type === "interrupt") {
            setBusy(false);
            setResuming(false);
            setThreadId(evt.thread_id);
            setCheckpoint(evt.checkpoint);
            sessionStorage.setItem(SESSION_THREAD_KEY, evt.thread_id);
            sessionStorage.setItem(SESSION_CHECKPOINT_KEY, evt.checkpoint);
            const loaded = await loadHitlState(evt.thread_id, evt.checkpoint);
            if (!loaded && evt.state) {
              applyHitlState(evt.checkpoint, evt.state as Record<string, unknown>);
            }
            pushEvent("interrupt", `Waiting for your review (${evt.checkpoint})`);
          } else if (evt.type === "report") {
            setReport(evt.message);
            setEditedReport(evt.message);
          } else if (evt.type === "rag_hits") {
            setRagHits(evt.hits || []);
          } else if (evt.type === "done") {
            setBusy(false);
            setCheckpoint(null);
            clearResearchSession();
          } else if (evt.type === "langsmith_url") {
            setLangsmithUrl(evt.message);
          } else if (evt.type === "error") {
            pushEvent("error", evt.message);
            setBusy(false);
            return;
          } else {
            pushEvent(evt.type, evt.message || evt.type);
          }
        } catch {
          /* ignore */
        }
      }
    }
    setBusy(false);
  }

  async function startResearch() {
    if (!topic) return;
    setBusy(true);
    setReport("");
    setEditedReport("");
    setEvents([]);
    setLangsmithUrl("");
    setThreadId(null);
    setCheckpoint(null);
    clearResearchSession();
    setPlanQuestions([]);
    setFindings([]);
    setCritique(null);
    setResearchIteration(0);
    setRagHits([]);

    const resp = await fetch("/api/research/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic, collection: DEFAULT_COLLECTION }),
    });
    await consumeSse(resp);
  }

  async function resumeResearch(payload: Record<string, unknown>) {
    if (!threadId || !checkpoint || busy || resuming) return;
    if (checkpoint === "plan" && planQuestions.length === 0) {
      pushEvent("error", "Plan not loaded yet — click Reload review panel");
      void loadHitlState(threadId, checkpoint);
      return;
    }
    setResuming(true);
    setBusy(true);
    const resp = await fetch("/api/research/resume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: threadId, checkpoint, ...payload }),
    });
    if (!resp.ok || !resp.body) {
      setBusy(false);
      setResuming(false);
      let message = "Resume failed — try again in a few seconds";
      if (resp.status === 429) {
        message = "Too many clicks — wait a few seconds, then try again";
      } else {
        try {
          const data = await resp.json();
          if (data.detail) message = String(data.detail);
        } catch {
          /* ignore */
        }
      }
      pushEvent("error", message);
      return;
    }
    await consumeSse(resp);
    setResuming(false);
  }

  function renderHitlActions(className = "actions") {
    if (!checkpoint) return null;
    const disabled = busy || resuming;
    if (checkpoint === "plan") {
      return (
        <div className={className}>
          <button
            disabled={disabled || planQuestions.length === 0}
            onClick={() => resumeResearch({ sub_questions: planQuestions })}
          >
            {resuming ? "Running researchers…" : "Approve plan & continue"}
          </button>
        </div>
      );
    }
    if (checkpoint === "critique") {
      return (
        <div className={className}>
          <button
            className="secondary"
            disabled={disabled}
            onClick={() => resumeResearch({ synthesize_now: false })}
          >
            More research
          </button>
          <button disabled={disabled} onClick={() => resumeResearch({ synthesize_now: true })}>
            {resuming ? "Synthesizing…" : "Synthesize report now"}
          </button>
        </div>
      );
    }
    if (checkpoint === "report") {
      return (
        <div className={className}>
          <button
            disabled={disabled}
            onClick={() => resumeResearch({ edited_report: editedReport })}
          >
            {resuming ? "Finishing…" : "Approve report"}
          </button>
        </div>
      );
    }
    return null;
  }

  return (
    <div className="app">
      <header>
        <h1>Research Agent Showcase</h1>
        <p>Human-in-the-loop research, RAG ingestion preview, and direct vector search</p>
      </header>

      <nav className="tabs">
        {(["research", "ingest", "search", "library"] as Tab[]).map((t) => (
          <button
            key={t}
            className={tab === t ? "tab active" : "tab"}
            onClick={() => setTab(t)}
            type="button"
          >
            {t === "research" ? "Research" : t === "ingest" ? "Ingest" : t === "search" ? "Search RAG" : "Library"}
          </button>
        ))}
      </nav>

      {tab === "research" && (
        <div className={`grid${checkpoint ? " hitl-active" : ""}`}>
          {checkpoint && (
            <div className="hitl-banner full-width" role="status">
              <strong>Action required — {checkpoint} review</strong>
              {checkpoint === "plan" && (
                <span>
                  Approve the research plan to run researchers on your topic.
                </span>
              )}
              {checkpoint === "critique" && (
                <span>
                  Iteration {researchIteration || 1} of 2. Synthesize the report or run one more
                  research pass.
                </span>
              )}
              {checkpoint === "report" && (
                <span>Edit the draft report, then approve to finish.</span>
              )}
              {renderHitlActions("actions banner-actions")}
            </div>
          )}

          {checkpoint === "plan" && (
            <section ref={hitlRef} className="card hitl full-width">
              <h2>Review research plan</h2>
              <p className="hint">Expand each sub-question to read and edit.</p>
              {planQuestions.length === 0 && (
                <p className="hint warn">Plan data not loaded — click Reload review panel above.</p>
              )}
              {planQuestions.map((sq, i) => (
                <details key={sq.id} className="hitl-details" open>
                  <summary>
                    <span className="hitl-summary-id">{sq.id}</span>
                    <span className="hitl-summary-preview">{sq.question || "Empty question"}</span>
                  </summary>
                  <div className="hitl-details-body">
                    <label htmlFor={`sq-q-${sq.id}`}>Question</label>
                    <textarea
                      id={`sq-q-${sq.id}`}
                      className="hitl-textarea"
                      rows={5}
                      value={sq.question}
                      onChange={(e) => {
                        const next = [...planQuestions];
                        next[i] = { ...sq, question: e.target.value };
                        setPlanQuestions(next);
                      }}
                    />
                    <label htmlFor={`sq-r-${sq.id}`}>Rationale</label>
                    <textarea
                      id={`sq-r-${sq.id}`}
                      className="hitl-textarea"
                      rows={4}
                      value={sq.rationale}
                      onChange={(e) => {
                        const next = [...planQuestions];
                        next[i] = { ...sq, rationale: e.target.value };
                        setPlanQuestions(next);
                      }}
                      placeholder="Why this sub-question matters..."
                    />
                  </div>
                </details>
              ))}
              {renderHitlActions()}
            </section>
          )}

          {checkpoint === "critique" && (
            <section ref={hitlRef} className="card hitl full-width">
              <h2>Review findings</h2>
              <p className="hint">Expand sections below to read full agent output.</p>
              {!critique && findings.length === 0 && (
                <p className="hint warn">Findings not loaded — click Reload review panel above.</p>
              )}
              {critique && (
                <details className="hitl-details" open>
                  <summary>
                    <span className="hitl-summary-id">Critic</span>
                    <span className="hitl-summary-preview">
                      {critique.ready_to_synthesize ? "Ready to synthesize" : "More research suggested"}
                    </span>
                  </summary>
                  <div className="hitl-details-body critique">
                    {critique.gaps?.length > 0 && (
                      <>
                        <strong>Gaps</strong>
                        <ul>
                          {critique.gaps.map((g, idx) => (
                            <li key={idx}>{g}</li>
                          ))}
                        </ul>
                      </>
                    )}
                    {critique.contradictions?.length > 0 && (
                      <>
                        <strong>Contradictions</strong>
                        <ul>
                          {critique.contradictions.map((c, idx) => (
                            <li key={idx}>{c}</li>
                          ))}
                        </ul>
                      </>
                    )}
                    {critique.follow_up_questions?.length > 0 && (
                      <>
                        <strong>Follow-up questions</strong>
                        <ul>
                          {critique.follow_up_questions.map((q, idx) => (
                            <li key={idx}>{q}</li>
                          ))}
                        </ul>
                      </>
                    )}
                  </div>
                </details>
              )}
              {findings.map((f, idx) => {
                const summary = f.summary || "";
                return (
                  <details key={`${f.sub_question_id}-${idx}`} className="hitl-details" open>
                    <summary>
                      <span className="hitl-summary-id">{f.sub_question_id}</span>
                      <span className="hitl-summary-preview">
                        ({f.confidence || "medium"}) {summary.slice(0, 120)}
                        {summary.length > 120 ? "…" : ""}
                      </span>
                    </summary>
                    <div className="hitl-details-body">
                      <pre className="hitl-readout">{summary || "No summary returned for this finding."}</pre>
                      {f.rag_hits && f.rag_hits.length > 0 && (
                        <div className="hitl-nested">
                          <strong>RAG evidence ({f.rag_hits.length} hits)</strong>
                          {f.rag_hits.map((h, i) => (
                            <details key={i} className="hitl-details nested">
                              <summary>
                                <span className="score">{(h.score ?? 0).toFixed(3)}</span>
                                {(h.text || "").slice(0, 100)}
                                {(h.text?.length ?? 0) > 100 ? "…" : ""}
                              </summary>
                              <pre className="hitl-readout">{h.text || ""}</pre>
                              {h.source && <small className="hitl-source">{h.source}</small>}
                            </details>
                          ))}
                        </div>
                      )}
                    </div>
                  </details>
                );
              })}
              {renderHitlActions()}
            </section>
          )}

          {checkpoint === "report" && (
            <section ref={hitlRef} className="card hitl full-width">
              <h2>Edit final report</h2>
              <p className="hint">Review and edit the full report below.</p>
              <details className="hitl-details" open>
                <summary>
                  <span className="hitl-summary-id">Report draft</span>
                  <span className="hitl-summary-preview">
                    {editedReport.slice(0, 120)}
                    {editedReport.length > 120 ? "…" : ""}
                  </span>
                </summary>
                <div className="hitl-details-body">
                  <textarea
                    className="hitl-textarea report-edit"
                    rows={24}
                    value={editedReport}
                    onChange={(e) => setEditedReport(e.target.value)}
                  />
                </div>
              </details>
              {renderHitlActions()}
            </section>
          )}

          <section className={`card${checkpoint ? " compact" : ""}`}>
            <h2>Research topic</h2>
            <label htmlFor="topic">Topic</label>
            <textarea
              id="topic"
              placeholder="Research topic..."
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
            />
            <button onClick={startResearch} disabled={busy || !topic || !!checkpoint}>
              {busy ? "Running..." : "Start research run"}
            </button>
            {checkpoint && threadId && (
              <div className="actions">
                <button
                  type="button"
                  className="secondary"
                  disabled={busy || resuming}
                  onClick={() => void loadHitlState(threadId, checkpoint)}
                >
                  Reload review panel
                </button>
              </div>
            )}
            {langsmithUrl && (
              <div className="links">
                <a href={langsmithUrl} target="_blank" rel="noreferrer">
                  View trace in LangSmith →
                </a>
              </div>
            )}
          </section>

          <section className="card">
            <h2>Live progress</h2>
            <ul className="events">
              {events.length === 0 && <li>No events yet.</li>}
              {events.map((e, i) => (
                <li key={i}>
                  <span className="badge">{e.type}</span>
                  {e.message}
                </li>
              ))}
            </ul>
          </section>

          {ragHits.length > 0 && (
            <section className="card">
              <h2>RAG evidence (latest researchers pass)</h2>
              {ragHits.slice(0, 8).map((h, i) => (
                <div key={i} className="hit">
                  <span className="score">{(h.score ?? 0).toFixed(3)}</span>
                  <div>{h.text}</div>
                  <small>{h.source}</small>
                </div>
              ))}
            </section>
          )}

          <section className="card full-width">
            <h2>Research report</h2>
            <div className="report">{report || "Report will appear when synthesis completes."}</div>
          </section>
        </div>
      )}

      {tab === "research" && checkpoint && (
        <div className="hitl-sticky-bar">{renderHitlActions("actions sticky-actions")}</div>
      )}

      {tab === "ingest" && (
        <div className="grid">
          <section className="card">
            <h2>Ingest documents</h2>
            <label htmlFor="ingest-topic">Topic tag (groups documents in RAG)</label>
            <input id="ingest-topic" value={topic} onChange={(e) => setTopic(e.target.value)} />
            <label htmlFor="file">Upload PDF or text</label>
            <input
              id="file"
              type="file"
              accept=".pdf,.txt,.md,.html"
              onChange={(e) => {
                setFile(e.target.files?.[0] || null);
                setPreview(null);
              }}
            />
            <div className="actions">
              <button className="secondary" onClick={previewDocument} disabled={!file || !topic}>
                Preview quality
              </button>
              <button onClick={uploadDocument} disabled={!file || !topic}>
                Approve & index
              </button>
            </div>
            <p className="status">{ingestStatus}</p>
          </section>

          {preview && (
            <section className="card full-width">
              <h2>Ingest preview — {preview.filename}</h2>
              <div className="metrics">
                <span>Pages extracted: {preview.pages_extracted}{preview.pdf_page_count ? ` / ${preview.pdf_page_count} PDF pages` : ""}</span>
                <span>Chars: {preview.total_chars.toLocaleString()}</span>
                <span>Chunks: {preview.chunk_count}</span>
                <span>Avg chunk: {preview.avg_chunk_chars} chars</span>
                {preview.empty_extraction && <span className="warn">Warning: no text extracted</span>}
              </div>
              {preview.preview_chunks.map((c, i) => (
                <details key={i} className="chunk-preview">
                  <summary>
                    Page {c.page ?? "—"} · chunk {c.chunk_index} · {c.char_count} chars
                  </summary>
                  <pre>{c.text_preview}</pre>
                </details>
              ))}
              {preview.preview_truncated && <p className="hint">Showing first chunks only.</p>}
            </section>
          )}
        </div>
      )}

      {tab === "search" && (
        <div className="grid">
          <section className="card">
            <h2>Search indexed documents</h2>
            <p className="hint">
              Upload indexes chunks once (vectors + semantic metadata). Each search runs the
              selected pipeline automatically — SWAG reranks before CRAG when both are enabled.
            </p>
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Query your indexed documents..."
              onKeyDown={(e) => e.key === "Enter" && runSearch()}
            />
            <fieldset className="mode-selector">
              <legend>Retrieval pipeline</legend>
              {(Object.keys(SEARCH_MODE_LABELS) as SearchMode[]).map((mode) => (
                <label key={mode} className="mode-option">
                  <input
                    type="radio"
                    name="search-mode"
                    value={mode}
                    checked={searchMode === mode}
                    onChange={() => setSearchMode(mode)}
                  />
                  {SEARCH_MODE_LABELS[mode]}
                </label>
              ))}
            </fieldset>
            <label className="compare-toggle">
              <input
                type="checkbox"
                checked={compareBaseline}
                onChange={(e) => setCompareBaseline(e.target.checked)}
              />
              Compare against baseline vector ranking
            </label>
            <button onClick={runSearch} disabled={searchBusy || !searchQuery.trim()}>
              {searchBusy ? "Searching…" : `Search (${SEARCH_MODE_LABELS[searchMode]})`}
            </button>
          </section>
          <section className="card full-width">
            <h2>Results</h2>
            <p className="hint pipeline-status">
              <strong>Pipeline:</strong> {PIPELINE_STEPS[searchMode]}
              {searchCrag?.action && (searchMode === "crag" || searchMode === "swag_crag") && (
                <>
                  {" · CRAG "}
                  <strong>{String(searchCrag.action)}</strong>
                  {" · kept "}
                  {String(searchCrag.kept ?? 0)}/{String(searchCrag.retrieved ?? 0)}
                  {searchCrag.rewritten_query
                    ? ` · query rewritten: ${String(searchCrag.rewritten_query)}`
                    : ""}
                </>
              )}
            </p>
            {searchResults.length === 0 && !searchBusy && (
              <p className="hint">No results yet.</p>
            )}
            {searchResults.map((h, i) => (
              <div key={`${h.id || h.source}-${i}`} className="hit">
                <span className="score">#{i + 1} · {renderHitScores(h, searchMode)}</span>
                <div>{h.text}</div>
                <small>
                  {h.source} {h.page ? `· p.${h.page}` : ""}
                </small>
              </div>
            ))}
          </section>
          {compareBaseline && searchBaseline.length > 0 && (
            <section className="card full-width">
              <h2>Baseline comparison (vector only)</h2>
              <p className="hint">
                Side-by-side with un-augmented vector search
                {searchComparison
                  ? ` · ${searchComparison.baseline_count} baseline vs ${searchComparison.augmented_count} augmented hits`
                  : ""}
              </p>
              <div className="compare-grid">
                <div className="compare-column">
                  <h3>Baseline</h3>
                  {searchBaseline.map((h, i) => (
                    <div key={`base-${h.id || i}`} className="hit compact">
                      <span className="score">#{i + 1} · {h.score?.toFixed(3) ?? "—"}</span>
                      <div>{hitPreview(h.text)}</div>
                      <small>{h.source?.split("/").pop()}</small>
                    </div>
                  ))}
                </div>
                <div className="compare-column">
                  <h3>{SEARCH_MODE_LABELS[searchMode]}</h3>
                  {searchResults.map((h, i) => (
                    <div key={`aug-${h.id || i}`} className="hit compact">
                      <span className="score">#{i + 1} · {renderHitScores(h, searchMode)}</span>
                      <div>{hitPreview(h.text)}</div>
                      <small>{h.source?.split("/").pop()}</small>
                    </div>
                  ))}
                </div>
              </div>
              {searchComparison && searchComparison.rank_changes.length > 0 && (
                <div className="rank-changes">
                  <h3>Rank changes</h3>
                  <ul>
                    {searchComparison.rank_changes
                      .filter(
                        (change) => change.rank_delta != null && change.rank_delta !== 0
                      )
                      .slice(0, 12)
                      .map((change) => (
                        <li key={change.key}>
                          <code>{change.key.split(":")[0].slice(-24)}</code>
                          {" · baseline #"}
                          {change.baseline_rank ?? "—"}
                          {" → augmented #"}
                          {change.augmented_rank ?? "—"}
                          {change.rank_delta != null && change.rank_delta !== 0 && (
                            <span className={change.rank_delta > 0 ? "rank-up" : "rank-down"}>
                              {" "}
                              ({change.rank_delta > 0 ? "+" : ""}
                              {change.rank_delta})
                            </span>
                          )}
                        </li>
                      ))}
                  </ul>
                </div>
              )}
            </section>
          )}
        </div>
      )}

      {tab === "library" && (
        <div className="grid">
          <section className="card">
            <h2>Vector store library</h2>
            {stats && (
              <div className="metrics">
                <span>{stats.total_points} chunks</span>
                <span>{stats.document_count} documents</span>
                <span>Topics: {stats.topics.join(", ") || "—"}</span>
              </div>
            )}
            <button className="secondary" onClick={loadLibrary}>
              Refresh
            </button>
          </section>
          <section className="card">
            <h2>Documents</h2>
            {documents.length === 0 && <p className="hint">No documents indexed yet.</p>}
            <ul className="doc-list">
              {documents.map((d) => (
                <li key={d.source}>
                  <button type="button" className="linkish" onClick={() => loadChunks(d.source)}>
                    {d.source.split("/").pop()}
                  </button>
                  <span>
                    {d.chunk_count} chunks · topic: {d.topic || "—"}
                  </span>
                  <button type="button" className="danger" onClick={() => deleteDocument(d.source)}>
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          </section>
          {selectedSource && (
            <section className="card full-width">
              <h2>Chunks — {selectedSource.split("/").pop()}</h2>
              {selectedChunks.map((c, i) => (
                <details key={i} className="chunk-preview">
                  <summary>
                    Page {c.page ?? "—"} · chunk {c.chunk_index} · score n/a
                  </summary>
                  <pre>{c.text}</pre>
                </details>
              ))}
            </section>
          )}
        </div>
      )}
    </div>
  );
}
