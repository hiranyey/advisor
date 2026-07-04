"""Environment config. Loaded once at import; read everywhere via `settings`."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


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

    # Monte Carlo defaults (used later; kept here so config has one home).
    mc_n_paths: int = int(os.getenv("MC_N_PATHS", "50000"))
    mc_seed: int = int(os.getenv("MC_SEED", "42"))
    mc_steps_per_year: int = int(os.getenv("MC_STEPS_PER_YEAR", "12"))


settings = Settings()
