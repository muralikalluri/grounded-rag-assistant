"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { pageLabel, safeHttpUrl, splitAround } from "@/lib/highlight";
import type { ChunkView, Citation, LocateResult } from "@/lib/types";

type PanelState =
  | { kind: "loading" }
  | { kind: "gone" }
  | { kind: "error" }
  | { kind: "ready"; chunk: ChunkView; locate: LocateResult | null };

const base = (c: Citation) => `/api/documents/${c.document_id}/chunks/${c.chunk_id}`;

export function CitationPanel({ citation }: { citation: Citation | null }) {
  const [state, setState] = useState<PanelState>({ kind: "loading" });
  const [segIdx, setSegIdx] = useState(0);
  const [imageFailed, setImageFailed] = useState(false);

  useEffect(() => {
    if (!citation) return;
    // A request token per citation: clicking [1] then [2] must never show [1]'s page under [2].
    // The parent remounts this component per citation (key), so state starts fresh: no
    // synchronous resets are needed here.
    const ac = new AbortController();
    (async () => {
      try {
        const chunkRes = await fetch(base(citation), { signal: ac.signal });
        if (chunkRes.status === 404) return setState({ kind: "gone" });
        if (!chunkRes.ok) return setState({ kind: "error" });
        const chunk = (await chunkRes.json()) as ChunkView;
        let locate: LocateResult | null = null;
        if (chunk.is_pdf) {
          const qs = new URLSearchParams({
            start: String(citation.char_start),
            end: String(citation.char_end),
          });
          const locRes = await fetch(`${base(citation)}/locate?${qs}`, { signal: ac.signal });
          if (locRes.status === 404) return setState({ kind: "gone" });
          if (locRes.ok) locate = (await locRes.json()) as LocateResult;
        }
        setState({ kind: "ready", chunk, locate });
      } catch (e) {
        if ((e as Error).name !== "AbortError") setState({ kind: "error" });
      }
    })();
    return () => ac.abort();
  }, [citation]);

  if (!citation) {
    return (
      <Card className="border-dashed">
        <CardContent className="text-sm text-neutral-500">
          Click a citation like <span className="font-semibold text-blue-700">[1]</span> to see the
          source page with the quoted text highlighted.
        </CardContent>
      </Card>
    );
  }

  const source = safeHttpUrl(citation.source_url);
  return (
    <Card data-testid="citation-panel" data-citation={citation.n}>
      <CardContent className="space-y-3">
        <header className="space-y-1">
          {/* Title and section come from document metadata: attacker-controlled, text only. */}
          <h2 className="text-sm font-semibold" data-testid="citation-title">
            {citation.doc_title}
          </h2>
          <p className="text-xs text-neutral-500">
            {[citation.section_path, pageLabel(citation.page_start, citation.page_end)]
              .filter(Boolean)
              .join(" · ")}
            {citation.source_date ? ` · dated ${citation.source_date}` : ""}
          </p>
          {citation.flags.includes("injection_flagged") ? (
            <Badge variant="warning">This passage contains instruction-like text</Badge>
          ) : null}
          {source ? (
            <a href={source} target="_blank" rel="noopener noreferrer" className="text-xs text-blue-700 underline">
              Open original
            </a>
          ) : null}
        </header>

        {state.kind === "loading" ? <p className="text-sm text-neutral-500">Loading source…</p> : null}
        {state.kind === "gone" ? (
          <p className="text-sm text-amber-800" data-testid="citation-gone">
            This source is no longer available to you, or it has changed since the answer was written.
          </p>
        ) : null}
        {state.kind === "error" ? (
          <p className="text-sm text-red-800">Could not load the source. Try again.</p>
        ) : null}

        {state.kind === "ready" ? (
          <SourceView
            citation={citation}
            chunk={state.chunk}
            locate={state.locate}
            segIdx={segIdx}
            setSegIdx={setSegIdx}
            imageFailed={imageFailed}
            onImageError={() => setImageFailed(true)}
          />
        ) : null}

        <figure className="rounded-md bg-yellow-50 p-3 text-sm ring-1 ring-yellow-200">
          <figcaption className="mb-1 text-xs font-medium text-yellow-900">Quoted text</figcaption>
          <blockquote data-testid="citation-quote" className="whitespace-pre-wrap">
            {citation.quote}
          </blockquote>
        </figure>
      </CardContent>
    </Card>
  );
}

function SourceView({
  citation,
  chunk,
  locate,
  segIdx,
  setSegIdx,
  imageFailed,
  onImageError,
}: {
  citation: Citation;
  chunk: ChunkView;
  locate: LocateResult | null;
  segIdx: number;
  setSegIdx: (i: number) => void;
  imageFailed: boolean;
  onImageError: () => void;
}) {
  if (!chunk.is_pdf) {
    // Non-PDF sources have no pages: show the passage with the quote marked (three text nodes).
    const [before, hit, after] = splitAround(chunk.text, citation.char_start, citation.char_end);
    return (
      <p className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md border p-3 text-sm" data-testid="citation-text">
        {before}
        <mark data-testid="text-highlight" className="bg-yellow-200">
          {hit}
        </mark>
        {after}
      </p>
    );
  }

  const found = locate?.found === true;
  const segments = found ? locate!.segments : [];
  const seg = segments[Math.min(segIdx, segments.length - 1)];
  const page = seg?.page ?? chunk.page_start ?? citation.page_start ?? 1;
  const rects = seg?.rects ?? [];
  const src = `/api/documents/${citation.document_id}/chunks/${citation.chunk_id}/page?page=${page}`;

  return (
    <div className="space-y-2">
      {!found ? (
        <p className="text-xs text-amber-800" data-testid="locate-note">
          {locate?.ambiguous
            ? "This wording appears more than once on the page, so it is not highlighted."
            : "Could not pinpoint the quote on the page. The quoted text is shown below."}
        </p>
      ) : null}
      {segments.length > 1 ? (
        <div className="flex items-center gap-2 text-xs">
          <span>The quote continues on the next page.</span>
          {segments.map((s, i) => (
            <button
              key={s.page}
              type="button"
              onClick={() => setSegIdx(i)}
              aria-pressed={i === segIdx}
              className="rounded border px-2 py-0.5 aria-pressed:bg-neutral-900 aria-pressed:text-white"
            >
              Page {s.page}
            </button>
          ))}
        </div>
      ) : null}
      {imageFailed ? (
        <p className="text-sm text-red-800">The page image could not be loaded.</p>
      ) : (
        <div className="relative overflow-hidden rounded-md border bg-white">
          {/* eslint-disable-next-line @next/next/no-img-element -- ACL-protected, per-request image */}
          <img
            key={src}
            src={src}
            alt={`Page ${page} of ${citation.doc_title}`}
            data-testid="citation-page"
            data-page={page}
            data-chunk-id={chunk.chunk_id}
            data-found={String(found)}
            data-highlights={rects.length}
            onError={onImageError}
            className="block h-auto w-full"
          />
          {rects.map((r, i) => (
            <div
              key={i}
              data-testid="highlight-rect"
              aria-hidden="true"
              className="pointer-events-none absolute bg-yellow-300/50 ring-1 ring-yellow-500 mix-blend-multiply"
              style={{
                left: `${r[0] * 100}%`,
                top: `${r[1] * 100}%`,
                width: `${(r[2] - r[0]) * 100}%`,
                height: `${(r[3] - r[1]) * 100}%`,
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
