# 5. Local embeddings and reranker

Status: accepted

**Decision.** `BAAI/bge-small-en-v1.5` embeddings and `BAAI/bge-reranker-base` run locally; only the
answer LLM is a hosted provider (OpenAI or Anthropic through LiteLLM).

**Consequences.** Ingestion and retrieval need no API key and no document text leaves the machine until
generation. Torch calls share one lock (concurrent inference crashed on macOS). Model downloads are cached.
