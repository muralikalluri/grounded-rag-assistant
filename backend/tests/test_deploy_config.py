"""Ties the Terraform (infra/azure) to the application it deploys, without needing Azure."""

import re
import subprocess

import pytest
from sqlalchemy.engine import make_url

from app.config import Settings
from tests.conftest import REPO_ROOT

TF = REPO_ROOT / "infra" / "azure"
APPS = (TF / "apps.tf").read_text()
SECRETS = (TF / "secrets.tf").read_text()

# Stand-ins for the Terraform variables/secrets that feed each env var.
SAMPLE = {
    "OIDC_ISSUER": "https://sso.example.com/realms/northwind",
    "OIDC_JWKS_URL": "https://sso.example.com/realms/northwind/protocol/openid-connect/certs",
    "OIDC_AUDIENCE": "rag-api",
    "OIDC_CLIENT_ID": "rag-web",
    "OIDC_GROUPS_REQUIRE_LEADING_SLASH": "false",
    "LLM_PROVIDER": "anthropic",
    "ANTHROPIC_MODEL": "claude-opus-5-5",
}


def block(text: str, name: str) -> str:
    """Body of `name = { ... }` inside a locals block (closing brace at two-space indent)."""
    m = re.search(rf"^  {re.escape(name)}\s*=\s*\{{\n(.*?)^  \}}", text, re.S | re.M)
    assert m, f"{name} not found"
    return m.group(1)


def env_names(text: str, name: str) -> dict[str, str | None]:
    """NAME -> literal value ("..." string) or None when it comes from a variable/local."""
    out: dict[str, str | None] = {}
    for line in block(text, name).splitlines():
        m = re.match(r"\s*\(?(?:local\.)?([A-Za-z_][A-Za-z0-9_]*)\)?\s*=\s*(.+?)\s*$", line)
        if not m or line.strip().startswith("#"):
            continue
        key, rhs = m.group(1), re.sub(r"\s+#.*$", "", m.group(2))
        lit = re.fullmatch(r'"([^"$]*)"', rhs)
        out[key] = lit.group(1) if lit else None
    return out


def backend_env() -> dict[str, str]:
    plain = env_names(APPS, "backend_env")
    values = {k: (v if v is not None else SAMPLE[k]) for k, v in plain.items()}
    values["ANTHROPIC_MODEL"] = SAMPLE["ANTHROPIC_MODEL"]  # backend_env_llm
    return values


def test_every_backend_env_var_is_a_real_setting_and_the_prod_validators_accept_it() -> None:
    env = backend_env()
    assert env["APP_ENV"] == "prod"
    fields = set(Settings.model_fields)
    # HF_HOME is read by the Hugging Face libraries, not by our Settings.
    external = {"HF_HOME"}
    unknown = [k for k in env if k.lower() not in fields and k not in external]
    assert unknown == [], f"Terraform sets variables the app does not read (typo?): {unknown}"
    # secrets arrive from Key Vault; give them realistic values and let the app's own prod
    # validators (OIDC required, real audit key, no dev auth, no test mode, strict crawler) judge.
    kwargs = {k.lower(): v for k, v in env.items() if k not in external}
    kwargs["audit_hmac_key"] = "x" * 48
    kwargs["database_url"] = "postgresql+asyncpg://u:p@h:5432/db?ssl=require"
    kwargs["redis_url"] = "redis://:p@redis:6379/0"
    for numeric in ("oidc_groups_require_leading_slash",):
        kwargs[numeric] = kwargs[numeric] == "true"
    s = Settings(**kwargs)  # would raise if a prod validator were violated
    assert s.app_env == "prod" and not s.dev_auth_enabled and not s.crawl_allow_private_networks
    assert s.llm_provider != "extractive" and s.expose_stage1_scores is False


def test_secret_env_names_are_settings_and_point_at_secrets_that_exist() -> None:
    secret_env = env_names(APPS, "backend_secret_env")
    assert {"DATABASE_URL", "REDIS_URL", "AUDIT_HMAC_KEY"} <= set(secret_env)
    assert all(
        k.lower() in Settings.model_fields
        or k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "llm_key_env_name")
        for k in secret_env
    )
    created = set(
        re.findall(r'^\s+"([a-z0-9-]+)"\s*=\s*(?:local|random_password|var)\.', SECRETS, re.M)
    )
    for secret_name in (v for v in secret_env.values() if v):
        assert secret_name in created, f"{secret_name} is referenced but never created in Key Vault"
    web_secret = env_names(APPS, "web_secret_env")
    for secret_name in (v for v in web_secret.values() if v):
        assert secret_name in created


def test_web_env_matches_what_the_frontend_reads() -> None:
    web = {**env_names(APPS, "web_env"), **env_names(APPS, "web_secret_env")}
    src = "\n".join(
        p.read_text()
        for p in (REPO_ROOT / "frontend" / "src").rglob("*.ts*")
        if ".test." not in p.name
    )
    read = set(re.findall(r"process\.env\.([A-Z_]+)", src)) | set(
        re.findall(r'need\("([A-Z_]+)"\)', src)
    )
    required_by_frontend = set(re.findall(r'need\("([A-Z_]+)"\)', src))
    assert required_by_frontend <= set(web), (
        f"frontend requires env vars Terraform does not set: {required_by_frontend - set(web)}"
    )
    unused = [k for k in web if k not in read and k != "NODE_ENV"]
    assert unused == [], f"Terraform sets web env vars the frontend never reads: {unused}"


def test_database_password_alphabets_are_url_safe_and_the_url_round_trips() -> None:
    # No special characters: nothing to percent-encode, no spaces (urlencode() turns ' ' into '+').
    for name in ("postgres", "redis", "session", "audit_hmac"):
        body = re.search(rf'resource "random_password" "{name}" \{{(.*?)\n\}}', SECRETS, re.S)
        assert body and "special = false" in body.group(1), (
            f"random_password.{name} must not use special characters"
        )
    password = "aB3" * 10 + "Zz"
    url = make_url(
        f"postgresql+asyncpg://ragadmin:{password}@psql-x.private.postgres.database.azure.com:5432/rag?ssl=require"
    )
    assert url.password == password and url.database == "rag" and url.query == {"ssl": "require"}
    # asyncpg takes `ssl=` (libpq's `sslmode=` is not understood)
    _, opts = url.get_dialect()().create_connect_args(url)
    assert opts["ssl"] == "require" and "sslmode" not in opts
    code = "\n".join(ln for ln in SECRETS.splitlines() if not ln.strip().startswith("#"))
    assert '?ssl=require"' in code and "sslmode" not in code


@pytest.mark.parametrize("password", ["p@ss:w/rd?#%+", "pa ss", "100%", "a+b", "é✓"])
def test_percent_encoded_passwords_round_trip_through_the_url_parser(password: str) -> None:
    """Complements the alphabet rule above: even awkward characters decode back if encoded."""
    from urllib.parse import quote

    url = make_url(f"postgresql+asyncpg://u:{quote(password, safe='')}@h:5432/d")
    assert url.password == password


def test_terraform_state_and_real_tfvars_are_git_ignored_but_the_lock_file_is_not() -> None:
    def git(*args: str) -> int:
        cmd = ["git", *args]
        return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, check=False).returncode  # noqa: S603

    def ignored(path: str) -> bool:
        return git("check-ignore", "-q", path) == 0

    if git("rev-parse") != 0:
        pytest.skip("not a git checkout")
    assert ignored("infra/azure/terraform.tfstate")
    assert ignored("infra/azure/terraform.tfstate.backup")
    assert ignored("infra/azure/terraform.tfvars")
    assert ignored("infra/azure/prod.auto.tfvars")
    assert ignored("infra/azure/.terraform/providers")
    assert not ignored("infra/azure/terraform.tfvars.example")
    assert not ignored("infra/azure/.terraform.lock.hcl")


def test_no_terraform_file_contains_a_literal_secret() -> None:
    key_like = re.compile(
        r"sk-[A-Za-z0-9_-]{12,}|AKIA[0-9A-Z]{12,}|ghp_[A-Za-z0-9]{20,}|-----BEGIN|password\s*=\s*\"[^\"$\s]{6,}\"",
        re.I,
    )
    for f in [*TF.glob("*.tf"), *TF.glob("*.tfvars.example"), *TF.glob("tests/*.hcl")]:
        for line in f.read_text().splitlines():
            if key_like.search(line) and "registry_password" not in line:
                raise AssertionError(f"{f.name}: possible literal secret: {line.strip()[:80]}")
