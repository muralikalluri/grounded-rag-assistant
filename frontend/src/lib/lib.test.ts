import { describe, expect, it } from "vitest";

import { pageLabel, safeHttpUrl, splitAround } from "./highlight";
import { parseSse } from "./sse";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
}

async function collect(chunks: string[]) {
  const out = [];
  for await (const ev of parseSse(streamOf(chunks))) out.push(ev);
  return out;
}

describe("parseSse", () => {
  it("parses events, ignores heartbeats, and handles chunk boundaries anywhere", async () => {
    const wire =
      'event: status\ndata: {"stage": "retrieving"}\n\n: ping\n\nevent: answer\ndata: {"status": "answered"}\n\n';
    for (const size of [1, 3, 7, wire.length]) {
      const pieces = wire.match(new RegExp(`[\\s\\S]{1,${size}}`, "g")) ?? [];
      expect(await collect(pieces)).toEqual([
        { event: "status", data: { stage: "retrieving" } },
        { event: "answer", data: { status: "answered" } },
      ]);
    }
  });

  it("does not split multi-byte characters across chunks", async () => {
    const bytes = new TextEncoder().encode('event: answer\ndata: {"t": "café ✓"}\n\n');
    const mid = bytes.indexOf(0xc3) + 1; // between the two bytes of "é"
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, mid));
        c.enqueue(bytes.slice(mid));
        c.close();
      },
    });
    const out = [];
    for await (const ev of parseSse(stream)) out.push(ev);
    expect(out).toEqual([{ event: "answer", data: { t: "café ✓" } }]);
  });

  it("skips malformed data and keeps going", async () => {
    const out = await collect(["event: x\ndata: {not json\n\n", 'event: y\ndata: {"ok": true}\n\n']);
    expect(out).toEqual([{ event: "y", data: { ok: true } }]);
  });

  it("flushes a final event that has no trailing blank line", async () => {
    expect(await collect(['event: answer\ndata: {"a": 1}'])).toEqual([
      { event: "answer", data: { a: 1 } },
    ]);
  });
});

describe("splitAround", () => {
  it("splits into plain text pieces and never builds markup", () => {
    expect(splitAround("abcdef", 2, 4)).toEqual(["ab", "cd", "ef"]);
    expect(splitAround("<b>x</b>", 0, 3)).toEqual(["", "<b>", "x</b>"]);
  });
  it("treats offsets as code points, not UTF-16 units (emoji before the quote)", () => {
    const text = "a😀b quote here"; // "😀" is one code point but two UTF-16 units
    expect(splitAround(text, 4, 9)).toEqual(["a😀b ", "quote", " here"]);
  });
  it("clamps bad ranges instead of throwing", () => {
    expect(splitAround("abc", -5, 99)).toEqual(["", "abc", ""]);
    expect(splitAround("abc", 3, 1)).toEqual(["abc", "", ""]);
  });
});

describe("safeHttpUrl / pageLabel", () => {
  it("only allows http(s)", () => {
    expect(safeHttpUrl("https://example.com/a")).toBe("https://example.com/a");
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("upload://file.pdf")).toBeNull();
    expect(safeHttpUrl(null)).toBeNull();
  });
  it("labels single pages and ranges", () => {
    expect(pageLabel(4, 4)).toBe("page 4");
    expect(pageLabel(3, 5)).toBe("pages 3-5");
    expect(pageLabel(null, null)).toBeNull();
  });
});
