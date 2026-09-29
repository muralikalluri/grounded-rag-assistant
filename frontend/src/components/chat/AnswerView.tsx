import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { ChatAnswer, ChatError, Stage } from "@/lib/types";

const STAGE_LABEL: Record<Stage, string> = {
  retrieving: "Searching your documents…",
  generating: "Writing an answer…",
  validating: "Checking every claim against the sources…",
};

export function Progress({ stage }: { stage: Stage }) {
  return (
    <p role="status" data-testid="progress" className="text-sm text-neutral-500">
      {STAGE_LABEL[stage]}
    </p>
  );
}

export function ErrorCard({ error }: { error: ChatError }) {
  return (
    <Card className="border-red-200 bg-red-50" data-testid="error-card">
      <CardContent className="space-y-1">
        <p className="text-sm font-medium text-red-900">Something went wrong</p>
        <p className="text-sm text-red-800">{error.message}</p>
        {error.request_id ? (
          <p className="font-mono text-xs text-red-700">request {error.request_id}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function AnswerView({
  answer,
  selected,
  onSelect,
}: {
  answer: ChatAnswer;
  selected: number | null;
  onSelect: (n: number) => void;
}) {
  if (answer.status === "not_found") {
    // "Not found" is a correct outcome, not an error: styled as information.
    return (
      <Card className="border-amber-200 bg-amber-50" data-testid="not-found">
        <CardContent className="space-y-2">
          <p className="text-sm font-medium text-amber-900">{answer.message}</p>
          {answer.possibly_related.length > 0 ? (
            <div>
              <p className="text-xs text-amber-800">Possibly related documents:</p>
              <ul className="mt-1 list-disc pl-5 text-xs text-amber-900">
                {answer.possibly_related.map((r, i) => (
                  <li key={i}>
                    {r.doc_title}
                    {r.page_start != null ? `, page ${r.page_start}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </CardContent>
      </Card>
    );
  }
  return (
    <Card data-testid="answer">
      <CardContent className="space-y-3">
        {answer.generator === "extractive" ? (
          <Badge variant="warning" data-testid="test-mode-badge">
            Test mode – extracted sentence, no AI model
          </Badge>
        ) : null}
        {/* Sentence text is model output: rendered as plain text only (no HTML, no markdown). */}
        <p className="leading-relaxed">
          {answer.sentences.map((s, i) => (
            <span key={i} data-testid="sentence">
              {s.text}
              {s.citations.map((n) => (
                <sup key={n}>
                  <button
                    type="button"
                    aria-label={`Citation ${n}`}
                    aria-pressed={selected === n}
                    data-testid={`cite-${n}`}
                    onClick={() => onSelect(n)}
                    className="mx-0.5 rounded px-1 text-xs font-semibold text-blue-700 underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-blue-600 aria-pressed:bg-blue-100"
                  >
                    [{n}]
                  </button>
                </sup>
              ))}{" "}
            </span>
          ))}
        </p>
        {answer.sentences.some((s) => s.flags?.includes("weak_overlap")) ? (
          <p className="text-xs text-neutral-500">
            Some statements have loose wording compared with their source. Check the citation.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
