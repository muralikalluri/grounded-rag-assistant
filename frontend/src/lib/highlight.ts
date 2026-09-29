/**
 * Split text into three plain strings around [start, end) so the caller can render the middle
 * one inside <mark>. Never builds HTML: document text is untrusted and must stay text.
 * Offsets are Unicode code points (the backend is Python), NOT JavaScript UTF-16 units, so text
 * containing emoji or other astral characters is split at the right place.
 * Offsets are clamped, so a bad range degrades to "no highlight" rather than throwing.
 */
export function splitAround(text: string, start: number, end: number): [string, string, string] {
  const cps = Array.from(text);
  const s = Math.max(0, Math.min(cps.length, start));
  const e = Math.max(s, Math.min(cps.length, end));
  return [cps.slice(0, s).join(""), cps.slice(s, e).join(""), cps.slice(e).join("")];
}

/** Only http(s) links may be rendered as hrefs (a stored title/URL is attacker-controlled). */
export function safeHttpUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.toString() : null;
  } catch {
    return null;
  }
}

export function pageLabel(start: number | null, end: number | null): string | null {
  if (start == null) return null;
  return end != null && end !== start ? `pages ${start}-${end}` : `page ${start}`;
}
