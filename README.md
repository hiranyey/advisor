docker buildx build \
  --provenance=false --sbom=false \
  -t asia-southeast1-docker.pkg.dev/project-97144aca-7cda-48e5-a37/advisor-gpu/advisor:1.0 \
  --push .