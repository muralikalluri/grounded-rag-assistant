export const API_URL = process.env.API_URL ?? "http://localhost:8000";

export type ApiStatus = "ok" | "degraded" | "unreachable";

/** Server-side backend readiness probe (/ready). Never throws. */
export async function getApiStatus(): Promise<ApiStatus> {
  try {
    const res = await fetch(`${API_URL}/ready`, { cache: "no-store", signal: AbortSignal.timeout(2000) });
    return res.ok ? "ok" : "degraded";
  } catch {
    return "unreachable";
  }
}
