"""Environment config. Loaded once at import; read everywhere via `settings`."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# GPU presence is a deploy-time fact, declared by env, not auto-detected. It drives
# two things: whether the market model is *derived nightly* from NAV history (GPU) or
# read from the hardcoded fallback table (CPU), and how many Monte Carlo paths we run
# (50k on GPU, a lighter count on CPU so the demo stays snappy).
_GPU = _env_bool("IS_GPU_AVAILABLE", False)


def _db_url() -> str:
    """Normalize the DB URL to force the psycopg (v3) driver."""
    url = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("POSTGRES_URL (or DATABASE_URL) must be set")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    return url


@dataclass(frozen=True)
class Settings:
    database_url: str = _db_url()

    # GPU switch — see _GPU above.
    is_gpu_available: bool = _GPU

    # Monte Carlo controls. n_paths is the main speed/precision lever: full 50k on GPU,
    # a lighter count on CPU (env MC_N_PATHS_CPU) so a local demo stays ~1s.
    mc_n_paths: int = int(
        os.getenv("MC_N_PATHS", "500000") if _GPU else os.getenv("MC_N_PATHS_CPU", "8000")
    )
    mc_seed: int = int(os.getenv("MC_SEED", "42"))
    mc_steps_per_year: int = int(os.getenv("MC_STEPS_PER_YEAR", "12"))
    mc_confidence: float = float(os.getenv("MC_CONFIDENCE", "0.80"))  # required-SIP target
    mc_var_pct: float = float(os.getenv("MC_VAR_PCT", "0.05"))  # tail percentile for VaR/CVaR

    # LLM (Copilot). Gemini via pydantic-ai — the six-tool loop in app/llm/copilot.py.
    # Any OpenAI-compatible / Google model works; the tool schemas are provider-agnostic.
    llm_api_key: str | None = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")
    llm_model: str = os.getenv("LLM_MODEL", "gemini-3.5-flash")

    # GCS via its S3-interoperability API (HMAC keys, not a service-account key file).
    gcs_access_key: str | None = os.getenv("ACCESS_KEY")
    gcs_access_secret: str | None = os.getenv("ACCESS_SECRET")
    gcs_bucket: str = os.getenv("GCS_BUCKET", "advisor-hiranyey")
    gcs_region: str = os.getenv("GCS_REGION", "asia-southeast1")
    gcs_endpoint_url: str = os.getenv("GCS_ENDPOINT_URL", "https://storage.googleapis.com")

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def gcs_configured(self) -> bool:
        return bool(self.gcs_access_key and self.gcs_access_secret)


settings = Settings()
