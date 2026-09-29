from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All tunables live here. Override via environment variables or .env."""

    # Later files win: backend/.env overrides the repo-root .env. Both are optional.
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    app_name: str = "grounded-rag-assistant"
    app_env: Literal["dev", "test", "prod"] = "dev"
    # Header-based demo login (X-Dev-User) for dev/test when OIDC is not configured. Never in prod.
    dev_auth_enabled: bool = False
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://rag:rag@localhost:5432/rag"
    redis_url: str = "redis://localhost:6379/0"

    # Timeout for dependency probes on /ready.
    readiness_timeout_seconds: float = 2.0

    # --- Storage -----------------------------------------------------------
    # Raw uploaded files, stored under server-generated names (never the client's).
    storage_dir: Path = Path("./data/files")

    # Demo corpus (manifest + files) and generated result files.
    demo_corpus_dir: Path = Path("../demo-corpus")
    results_dir: Path = Path("../results")

    # --- Embeddings --------------------------------------------------------
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    # Must match the vector(N) column created by the migration.
    embedding_dim: int = 384
    embedding_batch_size: int = 32

    # bge models are trained with this instruction on the *query* side only.
    embedding_query_prefix: str = "Represent this sentence for searching relevant passages: "

    # --- Retrieval ---------------------------------------------------------
    retrieval_vector_k: int = 30
    retrieval_keyword_k: int = 30
    rrf_k: int = 60
    rerank_model: str = "BAAI/bge-reranker-base"
    rerank_candidates: int = 30
    rerank_top_n: int = 6
    rerank_batch_size: int = 16
    rerank_max_length: int = 512
    rerank_max_query_tokens: int = 64
    query_max_chars: int = 1000
    # pgvector HNSW: iterative scans keep scanning until enough rows pass the filters.
    hnsw_ef_search: int = 100
    hnsw_max_scan_tuples: int = 20000
    pgvector_min_version: str = "0.8.0"

    # Demo users for the dev/test header login (only when OIDC is not configured).
    dev_principals: dict[str, list[str]] = {"alice": ["HR"], "bob": ["Engineering"], "admin": []}
    dev_admins: list[str] = ["admin"]

    # --- Authentication (OIDC / Keycloak) -----------------------------------------
    # When oidc_issuer is set the API requires "Authorization: Bearer <access token>" and the
    # X-Dev-User header is ignored entirely. Unset = dev/test mode only (refused in prod).
    oidc_issuer: str | None = None  # the PUBLIC issuer exactly as it appears in tokens' `iss`
    # Where the API fetches signing keys from (internal address of the same realm).
    oidc_jwks_url: str | None = None
    oidc_audience: str = "rag-api"
    oidc_client_id: str = "rag-web"  # tokens must have azp == this
    oidc_groups_claim: str = "groups"
    oidc_admin_role: str = "admin"
    oidc_jwks_ttl_seconds: float = 600.0
    oidc_jwks_min_refetch_seconds: float = 30.0
    oidc_jwks_timeout_seconds: float = 5.0
    # True: groups must arrive as Keycloak full paths ("/HR") - the safe setting for real realms
    # where a sub-group "/Parent/HR" would otherwise collapse into "HR". The demo realm uses
    # full.path=false and leaves this off.
    oidc_groups_require_leading_slash: bool = False
    oidc_leeway_seconds: float = 30.0
    oidc_max_token_bytes: int = 8192
    oidc_max_groups: int = 100

    # --- LLM (LiteLLM: OpenAI + Anthropic in the MVP) ----------------------
    # "extractive" is an offline TEST MODE (quotes the best-matching sentence, no LLM); it is
    # refused when app_env=prod and can never be the fallback.
    llm_provider: Literal["openai", "anthropic", "extractive"] = "anthropic"
    # Model ids are LiteLLM model names without the provider prefix. Verify against your account.
    anthropic_model: str = "claude-opus-5-5"
    openai_model: str = "gpt-4.1-mini"
    llm_fallback_provider: Literal["openai", "anthropic"] | None = None
    llm_fallback_model: str | None = None
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    llm_deadline_seconds: float = 120.0
    llm_backoff_base_seconds: float = 1.0
    llm_backoff_max_seconds: float = 20.0
    # Leaves room for thinking tokens on models that always think.
    llm_max_tokens: int = 4000
    # Not sent to Anthropic: current Claude models reject non-default sampling parameters.
    llm_temperature: float = 0.0

    # --- Generation / grounding -------------------------------------------
    # Stage-1 retrieval gate on the top chunk's rerank *probability* (sigmoid of the logit).
    # Uncalibrated; M5 tunes it from the golden set. Stage 2 handles the grey zone.
    abstain_rerank_threshold: float = 0.19
    abstain_when_rerank_unavailable: bool = False
    flagged_chunk_policy: Literal["keep_warn", "drop"] = "keep_warn"
    quote_fuzzy_min_chars: int = 25
    quote_fuzzy_ratio: float = 90.0
    # Differing alphanumeric tokens tolerated in a fuzzy match. 0 = fuzzy only forgives
    # punctuation/spacing, never a changed word.
    quote_fuzzy_token_tolerance: int = 0
    quote_min_tokens: int = 2
    quote_max_chars: int = 400
    answer_max_sentences: int = 15
    citations_per_sentence_max: int = 4
    possibly_related_max: int = 3
    not_found_message: str = "I couldn't find this in the documents you have access to."
    chat_timeout_seconds: float = 180.0
    chat_heartbeat_seconds: float = 15.0
    generation_invalid_retries: int = 1
    chat_conversation_id_max: int = 100
    chat_max_collection_ids: int = 50
    # Expose gate diagnostics (top score, threshold) in responses. Turn off in prod: they help
    # someone tune adversarial queries.
    expose_stage1_scores: bool = True
    audit_hmac_key: SecretStr = SecretStr("dev-only-placeholder-change-me")

    # --- Evaluation harness (eval/) ---------------------------------------------
    # Judge for correctness rescue + citation precision. None = judge disabled (string match only).
    eval_judge_provider: Literal["openai", "anthropic"] | None = None
    eval_judge_model: str | None = None
    eval_max_cost_usd: float = 5.0
    eval_concurrency: int = 2
    eval_retries_per_question: int = 1
    eval_threshold_margin: float = 0.02
    # Rough per-question token use for the pre-run cost estimate (prompt with 6 chunks + answer).
    eval_est_input_tokens: int = 4500
    eval_est_output_tokens: int = 400

    # --- Web crawl connector ------------------------------------------------------
    crawl_user_agent: str = "GroundedRAGBot/0.1 (document assistant crawler)"
    crawl_max_pages: int = 50
    crawl_max_requests: int = 200  # every request counts (404s, robots.txt, non-HTML too)
    crawl_max_request_seconds: float = 30.0  # one URL incl. redirects and body streaming
    crawl_max_links_per_page: int = 500
    crawl_max_robots_bytes: int = 512 * 1024
    crawl_max_depth: int = 2
    crawl_max_bytes_per_page: int = 5 * 1024 * 1024
    crawl_max_total_bytes: int = 50 * 1024 * 1024
    crawl_request_timeout_seconds: float = 10.0
    crawl_total_timeout_seconds: float = 300.0
    crawl_delay_seconds: float = 1.0
    crawl_max_crawl_delay_seconds: float = 10.0  # cap on a site's own Crawl-delay
    crawl_allowed_ports: list[int] = [80, 443]
    crawl_max_redirects: int = 5
    crawl_max_revalidate: int = 50  # previously ingested pages re-checked for 404/410
    crawl_strip_params: list[str] = [
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
    ]
    # LOCAL DEMOS / TESTS ONLY: lets the crawler reach private addresses (e.g. a docker-compose
    # demo site). Refused when app_env=prod.
    crawl_allow_private_networks: bool = False

    # --- Citation preview -----------------------------------------------------
    # Longest side of a rendered page image. The client cannot choose dpi/zoom.
    preview_max_px: int = 1400
    preview_render_concurrency: int = 2
    preview_render_timeout_seconds: float = 20.0
    preview_hit_max: int = 64
    preview_context_chars: int = 40
    preview_fallback_window_words: int = 6
    preview_fallback_max_hits: int = 4
    preview_fallback_min_coverage: float = 0.6

    # --- Chunking ----------------------------------------------------------
    # bge-small accepts 512 tokens including special tokens; stay below it so nothing
    # is silently truncated at embedding time.
    chunk_max_tokens: int = 480
    chunk_overlap_ratio: float = 0.15

    # --- Upload safety -----------------------------------------------------
    upload_max_bytes: int = 25 * 1024 * 1024
    docx_max_entries: int = 2000
    docx_max_uncompressed_bytes: int = 200 * 1024 * 1024
    docx_max_compression_ratio: float = 100.0
    pdf_max_pages: int = 2000
    parse_timeout_seconds: float = 120.0

    # --- Prompt-injection hygiene -----------------------------------------
    # name -> regex, matched against normalised (NFKC, zero-width stripped, lowercased) text.
    # Chunks are only flagged; text is stored unchanged.
    injection_patterns: dict[str, str] = {
        "ignore_previous": (
            r"\bignore (all |any )?(the )?(previous|prior|above|earlier) "
            r"(instructions|prompts?|rules)"
        ),
        "disregard_instructions": (
            r"\bdisregard (all |any )?(the )?(previous|prior|above|your) (instructions|rules)"
        ),
        "role_override": r"\byou are now\b",
        "system_prompt": r"\b(reveal|print|show|repeat) (your |the )?system prompt",
        "role_tag": r"(<\|?(system|assistant)\|?>|\[/?inst\])",
    }

    @model_validator(mode="after")
    def _no_dev_auth_in_prod(self) -> "Settings":
        if self.app_env == "prod" and self.dev_auth_enabled:
            raise ValueError("dev_auth_enabled must be false when app_env=prod")
        if self.app_env == "prod" and self.crawl_allow_private_networks:
            raise ValueError("crawl_allow_private_networks is refused when app_env=prod (SSRF)")
        if self.oidc_issuer and self.dev_auth_enabled:
            raise ValueError("dev_auth_enabled must be false when oidc_issuer is set")
        if self.oidc_issuer and not self.oidc_jwks_url:
            raise ValueError("oidc_jwks_url is required when oidc_issuer is set")
        if self.app_env == "prod" and not self.oidc_issuer:
            raise ValueError("app_env=prod requires oidc_issuer (real authentication)")
        if self.app_env == "prod" and self.llm_provider == "extractive":
            raise ValueError("llm_provider=extractive is a test mode and is refused in prod")
        if self.app_env == "prod" and "placeholder" in self.audit_hmac_key.get_secret_value():
            raise ValueError("audit_hmac_key must be set to a real secret when app_env=prod")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
