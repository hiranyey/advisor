# gpu_worker

The RunPod serverless image. Thin — `handler.py` just dispatches into `sim_kernel.jobs`
(the same package the backend imports for local/no-RunPod runs).

## Build & push

Build context is the repo root (not `gpu_worker/`) because the image needs the sibling
`sim_kernel/` package. Push target is `<repo>/<image>` inside the Artifact Registry
*repository* named `advisor-gpu` — i.e. the repo and image both being called
`advisor-gpu` is expected, not a typo:

```sh
gcloud auth configure-docker asia-southeast1-docker.pkg.dev   # one-time, per machine

docker buildx build \
  --provenance=false --sbom=false \
  -f gpu_worker/Dockerfile \
  -t asia-southeast1-docker.pkg.dev/project-97144aca-7cda-48e5-a37/advisor-gpu/advisor-gpu:1.0 \
  --push .
```

`--provenance=false --sbom=false` matters: recent `docker buildx` attaches provenance/SBOM
attestation manifests by default, and Artifact Registry rejects those with an opaque
`400 Bad Request` on the manifest push — the image layers upload fine, then the final
manifest push fails. Without `--push`, use a plain `docker build ... -t ...` followed by
`docker push ...` instead (same flags apply to `buildx build`, not to `docker push`).

## RunPod endpoint setup

- GPU: any CUDA 12.x card RunPod offers — the workload is 14x14 matrices and
  `(paths, categories)` blocks, not memory-hungry; a cheap GPU tier is fine.
- **Execution timeout**: set well above RunPod's default (~90s). `client_insights` and
  `whatif` are single-client and fast, but `book_analysis` loops the whole book and can
  run for minutes — that's why the backend calls it via `/run` + poll, not `/runsync`.
- **Active workers**: 0 is fine to start (cold start eats a few seconds per call, which
  matters for the live `client_insights`/`whatif` paths but not the nightly batch). If
  the live what-if demo needs to feel instant, set `min workers: 1` to keep one warm.
- **Max workers**: if `book_analysis` payload ever grows past RunPod's 10MB `/run` limit
  (a few hundred clients' worth of 14-vectors, currently nowhere close), the fix is
  chunking the client list into several `book_analysis` calls from
  `app/gpu/client.py:book_analysis` — RunPod will run the chunks on separate workers in
  parallel. Not needed yet; noted so the ceiling is visible.

## Backend wiring

Set on the backend, not here:

```
RUNPOD_API_KEY=...
RUNPOD_ENDPOINT_ID=...
```

Unset (either one) and `app/gpu/client.py` runs every job in-process with local numpy —
useful for dev without a RunPod account.
