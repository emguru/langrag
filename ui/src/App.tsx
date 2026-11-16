import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { apiFetch, AuthRequiredError } from "./api";
import {
  type AuthConfig,
  type AuthSession,
  authRequired,
  completeNewPassword,
  loadAuthConfig,
  readSession,
  signIn,
  signOut,
  writeSession,
} from "./auth";

type Tab = "ask" | "search" | "corpus" | "ingest";

type RagHit = {
  score?: number;
  text: string;
  source?: string;
  page?: number;
  topic?: string;
  chunk_index?: number;
  document_id?: string;
  id?: string;
};

type RosesPlan = {
  role?: string;
  objective?: string;
  scenario?: {
    status?: string;
    chunk_count?: number;
    sources?: string[];
    summary?: string;
  };
  expected_solution?: string;
  steps?: string[];
  where?: string;
};

type AskResult = {
  thread_id: string;
  status: "completed" | "interrupted";
  answer?: string;
  chunks?: RagHit[];
  roses?: RosesPlan;
  interrupt?: {
    type: string;
    question: string;
    chunks: RagHit[];
    message: string;
    roses?: RosesPlan;
    assessment?: {
      ambiguous?: boolean;
      reasons?: string[];
      senses?: string[];
      suggestions?: { question: string; why: string; cost: string }[];
      cheapest_path?: string;
    };
    confidence?: {
      level?: string;
      top_score?: number;
      message?: string;
    };
    actions?: { id: string; label: string; cost: string }[];
  };
};

type PreviewData = {
  filename: string;
  pages_extracted: number;
  total_chars: number;
  chunk_count: number;
  avg_chunk_chars: number;
  empty_extraction: boolean;
  preview_chunks: {
    page?: number;
    chunk_index: number;
    char_count: number;
    text_preview: string;
  }[];
};

type CorpusStats = {
  collection: string;
  total_points: number;
  document_count: number;
  topics: string[];
};

type DocumentRow = {
  source: string;
  topic?: string;
  document_id?: string;
  chunk_count: number;
  page_count?: number;
};

type PipelineStep =
  | "idle"
  | "retrieve"
  | "vector"
  | "hitl"
  | "generate"
  | "done"
  | "error";

const DEFAULT_COLLECTION = "research_docs";
const LANGSMITH_PROJECT = "rag-demo";
const LANGSMITH_URL = `https://smith.langchain.com`;

function hitPreview(text: string, max = 180): string {
  const trimmed = text.trim();
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, max - 1)}…`;
}

function shortSource(source?: string): string {
  if (!source) return "unknown";
  const parts = source.split("/");
  return parts[parts.length - 1] || source;
}

function KnobTip({ text }: { text: string }) {
  return (
    <span className="knob-help" tabIndex={0} aria-label={text}>
      ?
      <span className="knob-tooltip" role="tooltip">
        {text}
      </span>
    </span>
  );
}

export default function App() {
  const [authConfig, setAuthConfig] = useState<AuthConfig | null>(null);
  const [session, setSession] = useState<AuthSession | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [tab, setTab] = useState<Tab>("ask");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Tuning (shared)
  const [topK, setTopK] = useState(5);
  const [minScore, setMinScore] = useState<number | "">("");
  const [hnswEf, setHnswEf] = useState(64);
  const [exactSearch, setExactSearch] = useState(false);
  const [candidateMult, setCandidateMult] = useState(2);
  const [topicFilter, setTopicFilter] = useState("");
  const [hitlEnabled, setHitlEnabled] = useState(true);

  // Ask
  const [question, setQuestion] = useState("");
  const [askResult, setAskResult] = useState<AskResult | null>(null);
  const [approvedChunks, setApprovedChunks] = useState<RagHit[]>([]);
  const [pipelineStep, setPipelineStep] = useState<PipelineStep>("idle");

  // Search
  const [searchQuery, setSearchQuery] = useState("");
  const [searchHits, setSearchHits] = useState<RagHit[]>([]);

  // Corpus
  const [stats, setStats] = useState<CorpusStats | null>(null);
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [selectedDoc, setSelectedDoc] = useState<DocumentRow | null>(null);
  const [docChunks, setDocChunks] = useState<RagHit[]>([]);
  const [corpusTopic, setCorpusTopic] = useState("");

  // Ingest
  const [ingestTopic, setIngestTopic] = useState("");
  const [ingestFile, setIngestFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<PreviewData | null>(null);
  const [ingestMessage, setIngestMessage] = useState("");

  // Hugging Face
  const [hfDataset, setHfDataset] = useState("rag-datasets/rag-mini-wikipedia");
  const [hfConfig, setHfConfig] = useState("text-corpus");
  const [hfSplit, setHfSplit] = useState("passages");
  const [hfTopic, setHfTopic] = useState("hf-rag-wiki");
  const [hfFields, setHfFields] = useState("passage");
  const [hfMaxRows, setHfMaxRows] = useState(25);
  const [hfMessage, setHfMessage] = useState("");
  const [hfOptions, setHfOptions] = useState<{ config: string; split: string }[]>([
    { config: "text-corpus", split: "passages" },
  ]);
  const [hfFieldOptions, setHfFieldOptions] = useState<string[]>([]);
  const [hfPresets, setHfPresets] = useState<
    { id: string; config: string; split: string; text_fields: string[]; topic: string; label: string }[]
  >([]);
  const [hfResolvedFor, setHfResolvedFor] = useState("rag-datasets/rag-mini-wikipedia");

  const clearError = () => setError("");
  const fail = (e: unknown) => {
    if (e instanceof AuthRequiredError) {
      writeSession(null);
      setSession(null);
    }
    setError(e instanceof Error ? e.message : String(e));
  };

  useEffect(() => {
    void loadAuthConfig().then((cfg) => {
      setAuthConfig(cfg);
      setSession(readSession());
      setAuthReady(true);
    });
  }, []);

  const loadCorpus = useCallback(async (topic?: string) => {
    clearError();
    try {
      const topicQ = topic ? `&topic=${encodeURIComponent(topic)}` : "";
      const [statsResp, docsResp] = await Promise.all([
        apiFetch(`/rag/stats?collection=${DEFAULT_COLLECTION}`),
        apiFetch(`/rag/documents?collection=${DEFAULT_COLLECTION}${topicQ}`),
      ]);
      const statsData = await statsResp.json();
      const docsData = await docsResp.json();
      if (!statsResp.ok) throw new Error(statsData.detail || "Failed to load stats");
      if (!docsResp.ok) throw new Error(docsData.detail || "Failed to load documents");
      setStats(statsData);
      setDocuments(docsData.documents || []);
    } catch (e) {
      fail(e);
    }
  }, []);

  useEffect(() => {
    if (tab === "corpus") {
      void loadCorpus(corpusTopic || undefined);
    }
  }, [tab, corpusTopic, loadCorpus]);

  useEffect(() => {
    if (tab !== "ingest") return;
    apiFetch("/ingest/huggingface/presets")
      .then((r) => r.json())
      .then((d) => setHfPresets(d.presets || []))
      .catch(() => setHfPresets([]));
  }, [tab]);

  const applyHfPreset = useCallback(
    (id: string) => {
      const preset = hfPresets.find((p) => p.id === id);
      if (!preset) return;
      setHfDataset(preset.id);
      setHfConfig(preset.config);
      setHfSplit(preset.split);
      setHfTopic(preset.topic);
      setHfFields((preset.text_fields || []).join(","));
      setHfOptions([{ config: preset.config, split: preset.split }]);
      setHfFieldOptions(preset.text_fields || []);
      setHfResolvedFor(preset.id);
      setHfMessage("");
    },
    [hfPresets],
  );

  const resetHfCustom = useCallback(() => {
    setHfDataset("");
    setHfConfig("");
    setHfSplit("");
    setHfTopic("");
    setHfFields("");
    setHfOptions([]);
    setHfFieldOptions([]);
    setHfResolvedFor("");
    setHfMessage("");
  }, []);

  const knownHfPair = useCallback(
    (config: string, split: string) =>
      hfOptions.some((o) => o.config === config && o.split === split),
    [hfOptions],
  );

  const onHfDatasetChange = useCallback((next: string) => {
    setHfDataset(next);
    if (next.trim() === hfResolvedFor) return;
    setHfConfig("");
    setHfSplit("");
    setHfOptions([]);
    setHfFieldOptions([]);
    setHfResolvedFor("");
  }, [hfResolvedFor]);

  const lookupHuggingFace = useCallback(async () => {
    clearError();
    setBusy(true);
    setHfMessage("");
    try {
      const params = new URLSearchParams({ dataset: hfDataset.trim() });
      // Never send leftover preset config (e.g. text-corpus) with a custom dataset.
      if (knownHfPair(hfConfig, hfSplit)) {
        if (hfConfig.trim()) params.set("config", hfConfig.trim());
        if (hfSplit.trim()) params.set("split", hfSplit.trim());
      }
      const resp = await apiFetch(`/ingest/huggingface/inspect?${params}`);
      const data = await resp.json();
      if (!resp.ok) {
        const detail = data.detail;
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      setHfDataset(data.dataset);
      setHfConfig(data.config);
      setHfSplit(data.split);
      setHfOptions(data.available || []);
      setHfFieldOptions(data.fields || []);
      setHfResolvedFor(data.dataset);
      if (data.suggested_text_fields?.length) {
        setHfFields(data.suggested_text_fields.join(","));
      }
      if (!hfTopic.trim()) {
        setHfTopic(`hf-${String(data.dataset).split("/").pop()}`);
      }
      setHfMessage(
        `Found ${data.dataset} → ${data.config}/${data.split}. ` +
          `Fields: ${(data.fields || []).join(", ") || "(none)"}. ` +
          (data.sample_preview ? `Preview: ${data.sample_preview.slice(0, 120)}…` : ""),
      );
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }, [hfDataset, hfConfig, hfSplit, hfTopic, knownHfPair]);

  const submitHuggingFace = useCallback(async () => {
    clearError();
    setBusy(true);
    setHfMessage("");
    try {
      const pairKnown = knownHfPair(hfConfig, hfSplit);
      const resp = await apiFetch("/ingest/huggingface", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dataset: hfDataset.trim(),
          config: pairKnown ? hfConfig.trim() : "",
          split: pairKnown ? hfSplit.trim() : "",
          topic: hfTopic.trim() || `hf-${hfDataset.trim().split("/").pop() || "corpus"}`,
          text_fields: pairKnown
            ? hfFields.split(",").map((s) => s.trim()).filter(Boolean)
            : [],
          max_rows: hfMaxRows,
          offset: 0,
          id_field: "id",
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        const detail = data.detail;
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      setHfConfig(data.config);
      setHfSplit(data.split);
      setHfResolvedFor(data.dataset);
      if (data.text_fields?.length) setHfFields(data.text_fields.join(","));
      setHfMessage(
        `Indexed ${data.documents_indexed} docs / ${data.chunks_indexed} chunks from ${data.dataset} ` +
          `(${data.config}/${data.split}; fetched ${data.rows_fetched}, skipped ${data.rows_skipped})`,
      );
      await loadCorpus();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }, [hfDataset, hfConfig, hfSplit, hfTopic, hfFields, hfMaxRows, knownHfPair, loadCorpus]);

  const openDocument = useCallback(async (doc: DocumentRow) => {
    setSelectedDoc(doc);
    setDocChunks([]);
    try {
      const params = new URLSearchParams({
        collection: DEFAULT_COLLECTION,
        source: doc.source,
        limit: "50",
      });
      const resp = await apiFetch(`/rag/chunks?${params}`);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || resp.statusText);
      setDocChunks(data.chunks || []);
    } catch (e) {
      fail(e);
    }
  }, []);

  const deleteDocument = useCallback(
    async (doc: DocumentRow) => {
      if (!confirm(`Delete ${shortSource(doc.source)} and its passages?`)) return;
      clearError();
      setBusy(true);
      try {
        const params = new URLSearchParams({
          source: doc.source,
          collection: DEFAULT_COLLECTION,
        });
        const resp = await apiFetch(`/rag/documents?${params}`, { method: "DELETE" });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || resp.statusText);
        setSelectedDoc(null);
        setDocChunks([]);
        await loadCorpus(corpusTopic || undefined);
      } catch (e) {
        fail(e);
      } finally {
        setBusy(false);
      }
    },
    [corpusTopic, loadCorpus],
  );

  const runAsk = useCallback(async () => {
    clearError();
    setBusy(true);
    setAskResult(null);
    setPipelineStep("retrieve");
    try {
      // Visual pipeline pacing for demo
      await new Promise((r) => setTimeout(r, 400));
      setPipelineStep("vector");
      const resp = await apiFetch("/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          hitl_enabled: hitlEnabled,
          topic: topicFilter || undefined,
          top_k: topK,
          min_score: minScore === "" ? undefined : minScore,
          hnsw_ef: hnswEf,
          exact_search: exactSearch,
        }),
      });
      const data = (await resp.json()) as AskResult & { error?: string };
      if (!resp.ok) throw new Error(data.error || resp.statusText);
      setAskResult(data);
      if (data.status === "interrupted" && data.interrupt) {
        setApprovedChunks(data.interrupt.chunks || []);
        setPipelineStep("hitl");
      } else {
        setPipelineStep("generate");
        await new Promise((r) => setTimeout(r, 200));
        setPipelineStep("done");
      }
    } catch (e) {
      setPipelineStep("error");
      fail(e);
    } finally {
      setBusy(false);
    }
  }, [question, hitlEnabled, topicFilter, topK, minScore, hnswEf, exactSearch]);

  const resumeAsk = useCallback(
    async (action: "generate" | "ungrounded" | "abort" = "generate") => {
      if (!askResult?.thread_id) return;
      clearError();
      setBusy(true);
      setPipelineStep(action === "abort" ? "done" : "generate");
      try {
        const resume =
          action === "generate"
            ? { action: "generate", chunks: approvedChunks }
            : { action };
        const resp = await apiFetch("/resume", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            thread_id: askResult.thread_id,
            resume,
          }),
        });
        const data = (await resp.json()) as AskResult & { error?: string };
        if (!resp.ok) throw new Error(data.error || resp.statusText);
        setAskResult(data);
        setPipelineStep("done");
      } catch (e) {
        setPipelineStep("error");
        fail(e);
      } finally {
        setBusy(false);
      }
    },
    [askResult, approvedChunks],
  );

  const applySuggestion = useCallback((suggested: string) => {
    setQuestion(suggested);
    setAskResult(null);
    setApprovedChunks([]);
    setPipelineStep("idle");
  }, []);

  const runSearch = useCallback(async () => {
    clearError();
    setBusy(true);
    setSearchHits([]);
    setPipelineStep("vector");
    try {
      const resp = await apiFetch("/rag/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: searchQuery,
          top_k: topK,
          topic: topicFilter || undefined,
          collection: DEFAULT_COLLECTION,
          min_score: minScore === "" ? undefined : minScore,
          hnsw_ef: hnswEf,
          exact_search: exactSearch,
          candidate_multiplier: candidateMult,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || resp.statusText);
      setSearchHits(data.hits || []);
      setPipelineStep("done");
    } catch (e) {
      setPipelineStep("error");
      fail(e);
    } finally {
      setBusy(false);
    }
  }, [searchQuery, topicFilter, topK, minScore, hnswEf, exactSearch, candidateMult]);

  const previewIngest = useCallback(async () => {
    if (!ingestFile || !ingestTopic) return;
    clearError();
    setBusy(true);
    setPreview(null);
    try {
      const form = new FormData();
      form.append("file", ingestFile);
      form.append("topic", ingestTopic);
      const resp = await apiFetch("/ingest/preview", { method: "POST", body: form });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || resp.statusText);
      setPreview(data);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }, [ingestFile, ingestTopic]);

  const submitIngest = useCallback(async () => {
    if (!ingestFile || !ingestTopic) return;
    clearError();
    setBusy(true);
    setIngestMessage("");
    try {
      const form = new FormData();
      form.append("file", ingestFile);
      form.append("topic", ingestTopic);
      form.append("collection", DEFAULT_COLLECTION);
      const resp = await apiFetch("/ingest/file", { method: "POST", body: form });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || resp.statusText);
      setIngestMessage(
        `Indexed ${data.chunks_indexed} chunks from ${data.source} (${data.document_id})`,
      );
      setPreview(null);
      setIngestFile(null);
      await loadCorpus();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }, [ingestFile, ingestTopic, loadCorpus]);

  const topicCounts = useMemo(() => {
    const map = new Map<string, number>();
    for (const doc of documents) {
      const t = doc.topic || "untagged";
      map.set(t, (map.get(t) || 0) + doc.chunk_count);
    }
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [documents]);

  const maxTopicChunks = Math.max(1, ...topicCounts.map(([, n]) => n));

  if (!authReady) {
    return (
      <div className="app boot">
        <p className="hint">Loading…</p>
      </div>
    );
  }

  if (authRequired(authConfig) && !session) {
    return (
      <LoginScreen
        config={authConfig!}
        onSignedIn={(next) => {
          writeSession(next);
          setSession(next);
          setError("");
        }}
      />
    );
  }

  return (
    <div className="app">
      <header>
        <div className="header-row">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">
              R
            </span>
            <div>
              <h1>RAG Demo</h1>
              <p>Answers from your documents, with sources you can check.</p>
            </div>
          </div>
          <div className="header-actions">
            <a
              className="quiet-link"
              href={LANGSMITH_URL}
              target="_blank"
              rel="noreferrer"
              title={`Open traces for ${LANGSMITH_PROJECT}`}
            >
              Traces
            </a>
            {session && (
              <button
                type="button"
                className="ghost"
                onClick={() => {
                  signOut();
                  setSession(null);
                }}
              >
                Sign out
              </button>
            )}
          </div>
        </div>
      </header>

      <details className="card tune-bar advanced">
        <summary>Advanced search settings</summary>
        <div className="tune-grid tune-grid-wide">
          <label>
            <span className="knob-label">
              Top K
              <KnobTip text="How many passages to use. Higher can add useful context or noise. Start at 3–5; raise to 8–12 if answers feel thin." />
            </span>
            <input
              type="number"
              min={1}
              max={20}
              value={topK}
              onChange={(e) => setTopK(Number(e.target.value))}
            />
          </label>
          <label>
            <span className="knob-label">
              Score floor override
              <KnobTip text="Leave blank so each passage type uses its own cutoff. Set a number only if you want one floor for everything." />
            </span>
            <input
              type="number"
              min={0}
              max={1}
              step={0.01}
              value={minScore}
              placeholder="auto"
              onChange={(e) =>
                setMinScore(e.target.value === "" ? "" : Number(e.target.value))
              }
            />
          </label>
          <label>
            <span className="knob-label">
              Search depth
              <KnobTip text="How thoroughly to search the index. Higher finds more possible matches and is slower. 64 is a good default; try 128–256 if useful passages seem missing." />
            </span>
            <input
              type="number"
              min={16}
              max={512}
              step={16}
              value={hnswEf}
              onChange={(e) => setHnswEf(Number(e.target.value))}
            />
          </label>
          <label>
            <span className="knob-label">
              Candidate ×
              <KnobTip text="Search only: look at this many times Top K candidates, then keep the best Top K after the min score filter. 2× is a safe default." />
            </span>
            <input
              type="number"
              min={1}
              max={5}
              value={candidateMult}
              onChange={(e) => setCandidateMult(Number(e.target.value))}
            />
          </label>
          <label>
            <span className="knob-label">
              Topic filter
              <KnobTip text="Limit results to one topic tag. Leave empty to search everything. Wrong topic can return nothing useful." />
            </span>
            <input
              type="text"
              value={topicFilter}
              onChange={(e) => setTopicFilter(e.target.value)}
              placeholder="e.g. hf-rag-wiki"
            />
          </label>
          <label className="checkbox-row tune-check">
            <input
              type="checkbox"
              checked={exactSearch}
              onChange={(e) => setExactSearch(e.target.checked)}
            />
            <span className="knob-label">
              Exact search
              <KnobTip text="Compare against every passage instead of an approximate index. More complete, slower on large libraries." />
            </span>
          </label>
          <label className="checkbox-row tune-check">
            <input
              type="checkbox"
              checked={hitlEnabled}
              onChange={(e) => setHitlEnabled(e.target.checked)}
            />
            <span className="knob-label">
              Review sources first
              <KnobTip text="Pause after search so you can check the passages before an answer is generated. Leave on unless you want an answer immediately." />
            </span>
          </label>
        </div>
        <p className="hint">
          Hover <span className="knob-help-static">?</span> for details. Leave these alone unless results look off.
        </p>
      </details>

      <nav className="tabs">
        {(["ask", "search", "corpus", "ingest"] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            className={tab === t ? "tab active" : "tab"}
            onClick={() => setTab(t)}
          >
            {t === "ask"
              ? "Ask"
              : t === "search"
                ? "Search"
                : t === "corpus"
                  ? "Library"
                  : "Ingest"}
          </button>
        ))}
      </nav>

      {error && (
        <div className="card error-card">
          <p>{error}</p>
        </div>
      )}

      {(tab === "ask" || tab === "search") && (
        <PipelineViz
          step={pipelineStep}
          hitl={tab === "ask" && hitlEnabled}
          mode={tab}
        />
      )}

      {tab === "ask" && (
        <div className="grid">
          <section className="card">
            <h2>Ask</h2>
            <label htmlFor="question">Question</label>
            <textarea
              id="question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="What is retrieval augmented generation (RAG)?"
            />
            <button type="button" disabled={busy || !question.trim()} onClick={runAsk}>
              {busy ? "Searching…" : "Ask"}
            </button>
          </section>

          <section className="card">
            <h2>
              {askResult?.status === "interrupted"
                ? "Review sources"
                : "Answer"}
            </h2>
            {!askResult && (
              <p className="hint">Sources appear here so you can review them before an answer is generated.</p>
            )}
            {askResult?.status === "completed" && (
              <>
                <RosesPanel roses={askResult.roses} />
                <CitedAnswer
                  text={askResult.answer || ""}
                  chunks={askResult.chunks || []}
                />
                {askResult.chunks && askResult.chunks.length > 0 && (
                  <HitList hits={askResult.chunks} title="Sources used" />
                )}
              </>
            )}
            {askResult?.status === "interrupted" && askResult.interrupt && (
              <>
                <RosesPanel roses={askResult.interrupt.roses || askResult.roses} />
                <p className="hint">{askResult.interrupt.message}</p>

                {askResult.interrupt.confidence && (
                  <div
                    className={`banner ${
                      askResult.interrupt.confidence.level === "ok" ? "ok" : "warn"
                    }`}
                  >
                    <strong>Retrieval:</strong> {askResult.interrupt.confidence.message}
                  </div>
                )}

                {askResult.interrupt.assessment?.ambiguous && (
                  <div className="banner warn">
                    <strong>Ambiguity:</strong>
                    <ul className="assess-list">
                      {(askResult.interrupt.assessment.reasons || []).map((r) => (
                        <li key={r}>{r}</li>
                      ))}
                    </ul>
                    {askResult.interrupt.assessment.senses &&
                      askResult.interrupt.assessment.senses.length > 0 && (
                        <>
                          <p>
                            <strong>Possible senses</strong>
                          </p>
                          <ul className="assess-list">
                            {askResult.interrupt.assessment.senses.map((s) => (
                              <li key={s}>{s}</li>
                            ))}
                          </ul>
                        </>
                      )}
                    <p className="hint">{askResult.interrupt.assessment.cheapest_path}</p>
                  </div>
                )}

                {(askResult.interrupt.assessment?.suggestions || []).length > 0 && (
                  <div className="suggestions">
                    <h3>Cheaper / clearer asks</h3>
                    <ul>
                      {(askResult.interrupt.assessment?.suggestions || []).map((s) => (
                        <li key={s.question}>
                          <button
                            type="button"
                            className="link-btn"
                            onClick={() => applySuggestion(s.question)}
                          >
                            {s.question}
                          </button>
                          <span className="hint">
                            {" "}
                            — {s.why} ({s.cost})
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                <HitList
                  hits={approvedChunks}
                  title={
                    approvedChunks.length
                      ? `${approvedChunks.length} passage${approvedChunks.length === 1 ? "" : "s"} — remove any you do not want used`
                      : "No passages found"
                  }
                  editable
                  onChange={setApprovedChunks}
                />

                <div className="button-row">
                  <button
                    type="button"
                    disabled={busy || approvedChunks.length === 0}
                    onClick={() => resumeAsk("generate")}
                  >
                    Generate from selected passages
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    disabled={busy}
                    onClick={() => resumeAsk("abort")}
                  >
                    Stop and rephrase
                  </button>
                  <button
                    type="button"
                    className="secondary danger-outline"
                    disabled={busy}
                    onClick={() => resumeAsk("ungrounded")}
                  >
                    Answer without sources
                  </button>
                </div>
              </>
            )}
          </section>
        </div>
      )}

      {tab === "search" && (
        <div className="grid">
          <section className="card">
            <h2>Search</h2>
            <p className="hint">Find passages in your documents.</p>
            <label htmlFor="search-query">Query</label>
            <input
              id="search-query"
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="vendor contract liability"
            />
            <button type="button" disabled={busy || !searchQuery.trim()} onClick={runSearch}>
              {busy ? "Searching…" : "Search"}
            </button>
          </section>
          <section className="card">
            <h2>Passages</h2>
            {searchHits.length === 0 ? (
              <p className="hint">No hits yet.</p>
            ) : (
              <HitList hits={searchHits} />
            )}
          </section>
        </div>
      )}

      {tab === "corpus" && (
        <div className="corpus-layout">
          <section className="card">
            <div className="header-row">
              <h2>Library</h2>
              <button type="button" className="link-btn" onClick={() => loadCorpus(corpusTopic || undefined)}>
                Refresh
              </button>
            </div>
            {stats ? (
              <>
                <div className="stat-strip">
                  <div className="stat">
                    <span className="stat-value">{stats.total_points}</span>
                    <span className="stat-label">passages</span>
                  </div>
                  <div className="stat">
                    <span className="stat-value">{stats.document_count}</span>
                    <span className="stat-label">documents</span>
                  </div>
                  <div className="stat">
                    <span className="stat-value">{stats.topics?.length ?? 0}</span>
                    <span className="stat-label">topics</span>
                  </div>
                </div>
                <p className="hint">Passages grouped by topic.</p>
                <div className="vector-viz" aria-label="Vectors by topic">
                  {topicCounts.map(([topic, count]) => (
                    <button
                      key={topic}
                      type="button"
                      className={`topic-bar ${corpusTopic === topic ? "active" : ""}`}
                      onClick={() => setCorpusTopic(corpusTopic === topic ? "" : topic)}
                      title={`${topic}: ${count} chunks`}
                    >
                      <span className="topic-name">{topic}</span>
                      <span
                        className="topic-fill"
                        style={{ width: `${Math.max(8, (count / maxTopicChunks) * 100)}%` }}
                      />
                      <span className="topic-count">{count}</span>
                    </button>
                  ))}
                  {topicCounts.length === 0 && <p className="hint">No documents yet.</p>}
                </div>
                <label htmlFor="corpus-topic">Filter topic</label>
                <input
                  id="corpus-topic"
                  type="text"
                  value={corpusTopic}
                  onChange={(e) => setCorpusTopic(e.target.value)}
                  placeholder="Click a bar or type a topic"
                />
              </>
            ) : (
              <p className="hint">Loading…</p>
            )}
          </section>

          <section className="card">
            <h2>Documents</h2>
            {documents.length === 0 ? (
              <p className="hint">No documents for this filter.</p>
            ) : (
              <ul className="doc-list">
                {documents.map((doc) => (
                  <li key={doc.document_id || doc.source}>
                    <button
                      type="button"
                      className={`doc-btn ${selectedDoc?.source === doc.source ? "active" : ""}`}
                      onClick={() => openDocument(doc)}
                    >
                      <strong>{shortSource(doc.source)}</strong>
                      <span>
                        {doc.topic || "—"} · {doc.chunk_count} chunks
                      </span>
                    </button>
                    <button
                      type="button"
                      className="link-btn danger-text"
                      disabled={busy}
                      onClick={() => deleteDocument(doc)}
                    >
                      Delete
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card corpus-chunks">
            <h2>Passages</h2>
            {!selectedDoc && <p className="hint">Select a document to see its passages.</p>}
            {selectedDoc && (
              <>
                <p className="hint">
                  {shortSource(selectedDoc.source)} · {docChunks.length} loaded
                </p>
                <HitList hits={docChunks} />
              </>
            )}
          </section>
        </div>
      )}

      {tab === "ingest" && (
        <div className="grid">
          <section className="card">
            <h2>Upload document</h2>
            <p className="hint">Add a PDF, HTML, or text file to the library.</p>
            <label htmlFor="ingest-topic">Topic</label>
            <input
              id="ingest-topic"
              type="text"
              value={ingestTopic}
              onChange={(e) => setIngestTopic(e.target.value)}
              placeholder="e.g. langgraph, bedrock"
            />
            <label htmlFor="ingest-file">File (PDF, HTML, TXT)</label>
            <input
              id="ingest-file"
              type="file"
              onChange={(e) => setIngestFile(e.target.files?.[0] ?? null)}
            />
            <div className="button-row">
              <button
                type="button"
                disabled={busy || !ingestFile || !ingestTopic}
                onClick={previewIngest}
              >
                Preview chunks
              </button>
              <button
                type="button"
                disabled={busy || !ingestFile || !ingestTopic}
                onClick={submitIngest}
              >
                Add to library
              </button>
            </div>
            {ingestMessage && <p className="success">{ingestMessage}</p>}
          </section>

          <section className="card">
            <h2>Hugging Face dataset</h2>
            <p className="hint">
              Enter a dataset id or URL, then look up to fill config, split, and text columns.
            </p>
            {hfPresets.length > 0 && (
              <>
                <label htmlFor="hf-preset">Preset</label>
                <select
                  id="hf-preset"
                  value={hfPresets.some((p) => p.id === hfDataset) ? hfDataset : ""}
                  onChange={(e) => {
                    if (e.target.value) applyHfPreset(e.target.value);
                    else resetHfCustom();
                  }}
                >
                  <option value="">Custom…</option>
                  {hfPresets.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </>
            )}
            <label htmlFor="hf-dataset">Dataset (org/name or HF URL)</label>
            <input
              id="hf-dataset"
              type="text"
              value={hfDataset}
              onChange={(e) => onHfDatasetChange(e.target.value)}
              placeholder="rajpurkar/squad or https://huggingface.co/datasets/…"
            />
            <div className="button-row" style={{ marginBottom: "0.75rem" }}>
              <button
                type="button"
                className="secondary"
                disabled={busy || !hfDataset.trim()}
                onClick={lookupHuggingFace}
              >
                {busy ? "Looking up…" : "Look up"}
              </button>
            </div>
            <div className="tune-grid">
              <label>
                Config
                {hfOptions.length > 0 ? (
                  <select
                    value={`${hfConfig}||${hfSplit}`}
                    onChange={(e) => {
                      const [c, s] = e.target.value.split("||");
                      setHfConfig(c);
                      setHfSplit(s);
                    }}
                  >
                    {hfOptions.map((o) => (
                      <option key={`${o.config}/${o.split}`} value={`${o.config}||${o.split}`}>
                        {o.config} / {o.split}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={hfConfig}
                    onChange={(e) => setHfConfig(e.target.value)}
                    placeholder="auto"
                  />
                )}
              </label>
              <label>
                Split
                <input
                  value={hfSplit}
                  onChange={(e) => setHfSplit(e.target.value)}
                  placeholder="auto"
                />
              </label>
              <label>
                Max rows
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={hfMaxRows}
                  onChange={(e) => setHfMaxRows(Number(e.target.value))}
                />
              </label>
            </div>
            <label htmlFor="hf-fields">Text fields (comma-separated)</label>
            <input
              id="hf-fields"
              type="text"
              value={hfFields}
              onChange={(e) => setHfFields(e.target.value)}
              placeholder="passage,text,context"
            />
            {hfFieldOptions.length > 0 && (
              <p className="hint">
                Available columns:{" "}
                {hfFieldOptions.map((f) => (
                  <button
                    key={f}
                    type="button"
                    className="link-btn"
                    style={{ marginRight: "0.5rem" }}
                    onClick={() => setHfFields(f)}
                  >
                    {f}
                  </button>
                ))}
              </p>
            )}
            <label htmlFor="hf-topic">Topic tag</label>
            <input
              id="hf-topic"
              type="text"
              value={hfTopic}
              onChange={(e) => setHfTopic(e.target.value)}
            />
            <button
              type="button"
              disabled={busy || !hfDataset.trim()}
              onClick={submitHuggingFace}
            >
              {busy ? "Pulling from Hugging Face…" : "Ingest from Hugging Face"}
            </button>
            {hfMessage && <p className="success">{hfMessage}</p>}
          </section>

          <section className="card corpus-chunks">
            <h2>Upload preview</h2>
            {!preview && <p className="hint">Preview a file before adding it.</p>}
            {preview && (
              <>
                <p>
                  {preview.filename}: {preview.chunk_count} chunks, {preview.total_chars} chars
                  {preview.empty_extraction && " (empty extraction — check file)"}
                </p>
                <ul className="hit-list">
                  {preview.preview_chunks.map((c) => (
                    <li key={c.chunk_index}>
                      <small>
                        chunk {c.chunk_index}
                        {c.page != null ? ` · p.${c.page}` : ""} · {c.char_count} chars
                      </small>
                      <p>{c.text_preview}</p>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

function PipelineViz({
  step,
  hitl,
  mode,
}: {
  step: PipelineStep;
  hitl: boolean;
  mode: "ask" | "search";
}) {
  const steps =
    mode === "search"
      ? [
          { id: "retrieve", label: "Query" },
          { id: "vector", label: "Search" },
          { id: "done", label: "Results" },
        ]
      : [
          { id: "retrieve", label: "Understand" },
          { id: "vector", label: "Find sources" },
          ...(hitl ? [{ id: "hitl", label: "Review" }] : []),
          { id: "generate", label: "Answer" },
          { id: "done", label: "Done" },
        ];

  const order = steps.map((s) => s.id);
  const activeIdx = order.indexOf(step === "error" ? "retrieve" : step);

  return (
    <section className={`pipeline ${step === "error" ? "pipeline-error" : ""}`} aria-label="Pipeline">
      {steps.map((s, i) => {
        const state =
          step === "idle"
            ? "idle"
            : step === "error"
              ? "error"
              : i < activeIdx
                ? "done"
                : i === activeIdx
                  ? "active"
                  : "idle";
        return (
          <div key={s.id} className="pipeline-item">
            <div className={`pipeline-node ${state}`}>
              <span className="pipeline-dot" />
              <span>{s.label}</span>
            </div>
            {i < steps.length - 1 && <div className={`pipeline-edge ${i < activeIdx ? "done" : ""}`} />}
          </div>
        );
      })}
    </section>
  );
}

function LoginScreen({
  config,
  onSignedIn,
}: {
  config: AuthConfig;
  onSignedIn: (session: AuthSession) => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [challenge, setChallenge] = useState<{ session: string; username: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (challenge) {
        const session = await completeNewPassword(config, challenge.username, newPassword, challenge.session);
        onSignedIn(session);
        return;
      }
      const result = await signIn(config, email, password);
      if (result.newPasswordRequired) {
        setChallenge(result.newPasswordRequired);
        return;
      }
      if (result.session) onSignedIn(result.session);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app login-app">
      <form className="card login-card" onSubmit={submit}>
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            R
          </span>
          <div>
            <h1>RAG Demo</h1>
            <p>Sign in to ask questions about your documents.</p>
          </div>
        </div>
        {error && <div className="banner warn">{error}</div>}
        {!challenge ? (
          <>
            <label htmlFor="email">Email</label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </>
        ) : (
          <>
            <p className="hint">Choose a new password to finish signing in.</p>
            <label htmlFor="new-password">New password</label>
            <input
              id="new-password"
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
              minLength={12}
            />
            <p className="hint">At least 12 characters, with upper, lower, a number, and a symbol.</p>
          </>
        )}
        <button type="submit" disabled={busy}>
          {busy ? "Signing in…" : challenge ? "Save password" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

function RosesPanel({ roses }: { roses?: RosesPlan | null }) {
  if (!roses?.role && !roses?.objective) return null;
  const scenario = roses.scenario;
  return (
    <details className="roses">
      <summary>Plan</summary>
      <dl>
        <dt>Role</dt>
        <dd>{roses.role}</dd>
        <dt>Objective</dt>
        <dd>{roses.objective}</dd>
        {scenario && (
          <>
            <dt>Scenario</dt>
            <dd>
              {scenario.summary}
              {scenario.sources && scenario.sources.length > 0
                ? ` (${scenario.chunk_count} chunks)`
                : ""}
            </dd>
          </>
        )}
        {roses.expected_solution && (
          <>
            <dt>Expected solution</dt>
            <dd>{roses.expected_solution}</dd>
          </>
        )}
        {roses.steps && roses.steps.length > 0 && (
          <>
            <dt>Steps</dt>
            <dd>
              <ol>
                {roses.steps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
            </dd>
          </>
        )}
      </dl>
    </details>
  );
}

function CitedAnswer({ text, chunks }: { text: string; chunks: RagHit[] }) {
  const parts = text.split(/(\[\d+\]|\[source:[^\]]+\])/g);
  return (
    <p className="answer">
      {parts.map((part, i) => {
        const numbered = part.match(/^\[(\d+)\]$/);
        if (numbered) {
          const idx = Number(numbered[1]) - 1;
          const hit = chunks[idx];
          if (!hit) return <span key={i}>{part}</span>;
          return (
            <button
              key={i}
              type="button"
              className="cite-btn"
              title={shortSource(hit.source)}
              onClick={() =>
                document.getElementById(`source-${idx}`)?.scrollIntoView({
                  behavior: "smooth",
                  block: "center",
                })
              }
            >
              [{numbered[1]}]
            </button>
          );
        }
        const named = part.match(/^\[source:\s*([^\]]+)\]$/i);
        if (named) {
          const needle = named[1].trim();
          const idx = chunks.findIndex((chunk) => {
            const src = chunk.source || "";
            return src === needle || src.endsWith(needle) || shortSource(src) === needle;
          });
          if (idx < 0) return <span key={i}>{part}</span>;
          return (
            <button
              key={i}
              type="button"
              className="cite-btn"
              title={shortSource(chunks[idx].source)}
              onClick={() =>
                document.getElementById(`source-${idx}`)?.scrollIntoView({
                  behavior: "smooth",
                  block: "center",
                })
              }
            >
              [{idx + 1}]
            </button>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </p>
  );
}

function HitList({
  hits,
  title,
  editable,
  onChange,
}: {
  hits: RagHit[];
  title?: string;
  editable?: boolean;
  onChange?: (hits: RagHit[]) => void;
}) {
  const remove = (idx: number) => {
    if (!onChange) return;
    onChange(hits.filter((_, i) => i !== idx));
  };

  return (
    <div className="hit-panel">
      {title && <h3>{title}</h3>}
      <ul className="hit-list">
        {hits.map((hit, idx) => (
          <li
            id={`source-${idx}`}
            key={`${hit.id || hit.source}-${hit.chunk_index}-${idx}`}
          >
            <div className="hit-meta">
              <span>
                <span className="cite-index">[{idx + 1}]</span>{" "}
                {shortSource(hit.source)}
                {hit.topic ? ` · ${hit.topic}` : ""}
                {hit.score != null ? ` · score ${hit.score.toFixed(3)}` : ""}
              </span>
              {editable && (
                <button type="button" className="link-btn" onClick={() => remove(idx)}>
                  Remove
                </button>
              )}
            </div>
            <p>{hitPreview(hit.text)}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
