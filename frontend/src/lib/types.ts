// Shapes returned by the backend (see backend/app/generation/response.py and app/api/preview.py).

export type Citation = {
  n: number;
  chunk_id: string;
  document_id: string;
  doc_title: string;
  page_start: number | null;
  page_end: number | null;
  section_path: string | null;
  quote: string;
  char_start: number;
  char_end: number;
  source_date: string | null;
  source_url: string | null;
  flags: string[];
};

export type Sentence = { text: string; citations: number[]; flags?: string[] };

export type Related = { doc_title: string; page_start: number | null; section_path: string | null };

export type ChatAnswer = {
  request_id: string;
  generator: string;
  status: "answered" | "not_found";
  message: string | null;
  sentences: Sentence[];
  citations: Citation[];
  possibly_related: Related[];
  grounding: {
    sentences_total: number;
    sentences_dropped: number;
    abstained_reason: string | null;
    drop_reasons: Record<string, number>;
    stage1: Record<string, unknown>;
  };
};

export type ChatError = { code: string; message: string; request_id?: string };

export type Stage = "retrieving" | "generating" | "validating";

export type ChunkView = {
  chunk_id: string;
  document_id: string;
  doc_title: string;
  section_path: string | null;
  page_start: number | null;
  page_end: number | null;
  text: string;
  is_pdf: boolean;
  source_date: string | null;
  flags: string[];
};

export type LocateResult = {
  found: boolean;
  ambiguous: boolean;
  method: string;
  segments: { page: number; rects: [number, number, number, number][] }[];
};
