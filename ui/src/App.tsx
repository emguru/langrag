import { useState } from "react";

type RunEvent = { type: string; message: string; timestamp: string };

export default function App() {
  const [topic, setTopic] = useState("");
  const [collection, setCollection] = useState("research_docs");
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState("");
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [ingestStatus, setIngestStatus] = useState("");
  const [langsmithUrl, setLangsmithUrl] = useState("");

  async function uploadDocument() {
    if (!file || !topic) return;
    setIngestStatus("Uploading...");
    const form = new FormData();
    form.append("file", file);
    form.append("topic", topic);
    form.append("collection", collection);

    const resp = await fetch("/api/ingest/file", { method: "POST", body: form });
    const data = await resp.json();
    if (!resp.ok) {
      setIngestStatus(data.detail || "Upload failed");
      return;
    }
    setIngestStatus(`Indexed ${data.chunks_indexed} chunks from ${file.name}`);
  }

  async function startResearch() {
    if (!topic) return;
    setBusy(true);
    setReport("");
    setEvents([]);
    setLangsmithUrl("");

    const resp = await fetch("/api/research/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topic, collection }),
    });

    if (!resp.ok || !resp.body) {
      setBusy(false);
      setEvents([{ type: "error", message: "Failed to start research run", timestamp: new Date().toISOString() }]);
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
          if (evt.type === "report") {
            setReport(evt.message);
          } else if (evt.type === "langsmith_url") {
            setLangsmithUrl(evt.message);
          } else {
            setEvents((prev) => [
              ...prev,
              { type: evt.type, message: evt.message, timestamp: new Date().toISOString() },
            ]);
          }
        } catch {
          /* ignore malformed SSE chunks */
        }
      }
    }

    setBusy(false);
  }

  return (
    <div className="app">
      <header>
        <h1>Research Agent Showcase</h1>
        <p>Multi-agent research with RAG ingestion and LangSmith tracing</p>
      </header>

      <div className="grid">
        <section className="card">
          <h2>1. Configure topic</h2>
          <label htmlFor="topic">Research topic</label>
          <textarea
            id="topic"
            placeholder="e.g. Impact of retrieval-augmented generation on enterprise search..."
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
          />
          <label htmlFor="collection">Vector collection</label>
          <input
            id="collection"
            type="text"
            value={collection}
            onChange={(e) => setCollection(e.target.value)}
          />
          <button onClick={startResearch} disabled={busy || !topic}>
            {busy ? "Researching..." : "Start research run"}
          </button>
          {langsmithUrl && (
            <div className="links">
              <a href={langsmithUrl} target="_blank" rel="noreferrer">
                View trace in LangSmith →
              </a>
            </div>
          )}
        </section>

        <section className="card">
          <h2>2. Ingest documents (RAG)</h2>
          <label htmlFor="file">Upload PDF or text</label>
          <input
            id="file"
            type="file"
            accept=".pdf,.txt,.md,.html"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
          <button className="secondary" onClick={uploadDocument} disabled={!file || !topic}>
            Upload & index
          </button>
          <p className="status">{ingestStatus}</p>
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

        <section className="card">
          <h2>Research report</h2>
          <div className="report">{report || "Report will appear here when the run completes."}</div>
        </section>
      </div>
    </div>
  );
}
