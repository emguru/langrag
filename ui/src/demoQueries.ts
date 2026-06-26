export type SearchMode = "baseline" | "swag" | "crag" | "swag_crag";

export type DemoScenario = {
  id: string;
  label: string;
  query: string;
  topic: string;
  mode: SearchMode;
  compare: boolean;
  blurb: string;
  group: "swag" | "optional";
};

/** Frozen from ingestion/benchmarks/validation_qrels.json — SWAG proof cases first. */
export const DEMO_SCENARIOS: DemoScenario[] = [
  {
    id: "chrome-rag-body",
    label: "Chrome trap (RAG body)",
    query: "what is retrieval augmented generation",
    topic: "validation-chrome",
    mode: "swag",
    compare: true,
    blurb: "Vector ranks README chrome; SWAG promotes body paragraphs.",
    group: "swag",
  },
  {
    id: "chrome-chunking",
    label: "Chunking quality",
    query: "How does chunking affect RAG quality?",
    topic: "validation-chrome",
    mode: "swag",
    compare: true,
    blurb: "Same chrome trap — substantive chunk should win top-1.",
    group: "swag",
  },
  {
    id: "ap-three-way",
    label: "AP · three-way match",
    query: "three-way matching purchase order",
    topic: "ap-procurement",
    mode: "swag",
    compare: true,
    blurb: "Topic lane keeps procurement docs; SWAG ranks the right PO chunk.",
    group: "swag",
  },
  {
    id: "cafeteria-safety",
    label: "Cafeteria · food safety",
    query: "food safety basics for cafeteria operations",
    topic: "cafeteria-ops",
    mode: "swag",
    compare: true,
    blurb: "Domain lane demo — not mixed-corpus noise.",
    group: "swag",
  },
  {
    id: "cross-invoice-cafeteria",
    label: "CRAG · cross-domain bait",
    query: "invoice cafeteria menu",
    topic: "",
    mode: "swag_crag",
    compare: true,
    blurb: "Optional: full stack may return empty — intentional safety, not a SWAG failure.",
    group: "optional",
  },
];
