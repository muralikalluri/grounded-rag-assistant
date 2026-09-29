# 7. Structured JSON logs instead of OpenTelemetry

Status: accepted (MVP)

**Decision.** JSON logs carry request ids, chunk ids, token counts, latency and cost; no tracing backend.

**Consequences.** Simple to run locally. No distributed traces or dashboards until a later milestone.
