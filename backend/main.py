"""Entrypoint shim so `uv run fastapi dev main.py` runs the real app in app/main.py."""

from app.main import app

__all__ = ["app"]
