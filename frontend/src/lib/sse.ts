export type SseEvent = { event: string; data: unknown };

/**
 * Parse a server-sent-events byte stream from fetch(). EventSource cannot POST or send
 * headers, so the chat endpoint is read with fetch + a ReadableStream instead.
 * Comment lines (": ping" heartbeats) are ignored. Malformed data lines are skipped.
 */
export async function* parseSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (value) buffer += decoder.decode(value, { stream: true });
      if (done) buffer += decoder.decode();
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const parsed = parseBlock(block);
        if (parsed) yield parsed;
      }
      if (done) {
        const tail = parseBlock(buffer);
        if (tail) yield tail;
        return;
      }
    }
  } finally {
    reader.releaseLock();
  }
}

function parseBlock(block: string): SseEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const raw of block.split("\n")) {
    const line = raw.replace(/\r$/, "");
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
  }
  if (dataLines.length === 0) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}
