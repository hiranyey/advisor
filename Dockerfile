# Single image serving both the SvelteKit SPA and the FastAPI backend that serves
# it. Build from the repo root so this can COPY both frontend/ and the sibling
# sim_kernel/ package the backend depends on:
#
#   docker build -t advisoros .
#   docker run --env-file backend/.env -p 8000:8000 advisoros
#
# The app is reachable at http://localhost:8000/ (SPA) and .../api/... (FastAPI).

# ---- frontend build ----------------------------------------------------------
FROM node:22-slim AS frontend

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# Baked in at build time: the container serves the API same-origin under /api.
ENV VITE_API_BASE=/api
RUN npm run build

# ---- backend runtime -----------------------------------------------------------
FROM python:3.14-slim AS backend

RUN pip install --no-cache-dir uv

WORKDIR /app

# sim_kernel is a local path dependency (see backend/pyproject.toml's
# [tool.uv.sources]) and must sit next to backend/ at the same relative layout as
# in the repo.
COPY sim_kernel ./sim_kernel

# Install dependencies first, separately from the app source, so this layer is
# cached across source-only changes.
COPY backend/pyproject.toml backend/uv.lock ./backend/
WORKDIR /app/backend
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ ./
RUN uv sync --frozen --no-dev

# The built SPA — served straight off disk by FastAPI (see app/main.py).
COPY --from=frontend /app/frontend/build /app/frontend_dist

ENV PATH="/app/backend/.venv/bin:${PATH}" \
    FRONTEND_DIST=/app/frontend_dist \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
