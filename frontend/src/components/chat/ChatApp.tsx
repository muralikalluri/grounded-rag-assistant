"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { AnswerView, ErrorCard, Progress } from "@/components/chat/AnswerView";
import { CitationPanel } from "@/components/chat/CitationPanel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { parseSse } from "@/lib/sse";
import type { ChatAnswer, ChatError, Citation, Stage } from "@/lib/types";

type Msg = {
  id: number;
  question: string;
  stage?: Stage;
  answer?: ChatAnswer;
  error?: ChatError;
};

const noopSubscribe = () => () => {};

export function ChatApp({ username }: { username: string }) {
  // false while rendering on the server and during hydration, true afterwards: the input only
  // reports data-ready once React is actually listening to it.
  const ready = useSyncExternalStore(noopSubscribe, () => true, () => false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [selected, setSelected] = useState<{ msg: number; n: number } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const nextId = useRef(1);

  useEffect(() => () => abortRef.current?.abort(), []);

  function patch(id: number, p: Partial<Msg>) {
    setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...p } : m)));
  }

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    const question = input.trim();
    if (!question || busy || !ready) return;
    const id = nextId.current++;
    setMessages((ms) => [...ms, { id, question, stage: "retrieving" }]);
    setInput("");
    setBusy(true);
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json", "x-requested-with": "fetch" },
        body: JSON.stringify({ question }),
        signal: ac.signal,
      });
      if (res.status === 401) {
        patch(id, {
          stage: undefined,
          error: { code: "unauthorized", message: "Your session has expired. Please sign in again." },
        });
        return;
      }
      if (!res.ok || !res.body) {
        patch(id, { stage: undefined, error: { code: "http_error", message: "The request failed." } });
        return;
      }
      for await (const ev of parseSse(res.body)) {
        if (ev.event === "status") patch(id, { stage: (ev.data as { stage: Stage }).stage });
        else if (ev.event === "answer") patch(id, { stage: undefined, answer: ev.data as ChatAnswer });
        else if (ev.event === "error") patch(id, { stage: undefined, error: ev.data as ChatError });
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        patch(id, { stage: undefined, error: { code: "network", message: "Connection lost." } });
      }
    } finally {
      setBusy(false);
    }
  }

  const selectedCitation: Citation | null =
    selected == null
      ? null
      : (messages.find((m) => m.id === selected.msg)?.answer?.citations.find((c) => c.n === selected.n) ?? null);

  return (
    <div className="mx-auto grid max-w-6xl gap-6 px-4 py-6 lg:grid-cols-[minmax(0,1fr)_28rem]">
      <section className="space-y-4" aria-label="Conversation">
        <header className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h1 className="text-xl font-semibold">Grounded RAG Assistant</h1>
            <p className="text-sm text-neutral-600">
              Answers only from your documents, with a citation for every claim.
            </p>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <span data-testid="signed-in-as">
              Signed in as <strong>{username}</strong>
            </span>
            <form method="post" action="/auth/logout">
              <Button type="submit" variant="outline" size="sm" data-testid="sign-out">
                Sign out
              </Button>
            </form>
          </div>
        </header>

        <ol className="space-y-6" aria-live="polite">
          {messages.map((m) => (
            <li key={m.id} className="space-y-2" data-testid="message">
              <p className="rounded-md bg-neutral-100 px-3 py-2 text-sm" data-testid="question">
                {m.question}
              </p>
              {m.stage ? <Progress stage={m.stage} /> : null}
              {m.error ? <ErrorCard error={m.error} /> : null}
              {m.answer ? (
                <AnswerView
                  answer={m.answer}
                  selected={selected?.msg === m.id ? selected.n : null}
                  onSelect={(n) => setSelected({ msg: m.id, n })}
                />
              ) : null}
            </li>
          ))}
        </ol>

        <form onSubmit={ask} className="sticky bottom-4 flex gap-2 bg-neutral-50 pt-2">
          <Input
            aria-label="Your question"
            data-testid="question-input"
            data-ready={String(ready)}
            placeholder="Ask about your documents…"
            value={input}
            maxLength={1000}
            onChange={(e) => setInput(e.target.value)}
            disabled={busy}
          />
          <Button type="submit" disabled={busy || !ready || !input.trim()} data-testid="ask-button">
            Ask
          </Button>
        </form>
      </section>

      <aside aria-label="Source" className="lg:sticky lg:top-6 lg:self-start">
        <CitationPanel
          key={selectedCitation ? `${selectedCitation.chunk_id}:${selectedCitation.char_start}` : "none"}
          citation={selectedCitation}
        />
      </aside>
    </div>
  );
}
