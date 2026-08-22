# Cloud Run job image for the C1 corpus freshness refresh (SPEC §2).
#
# Runs on a schedule (Cloud Scheduler) inside the asia-southeast1 (Singapore) project:
# re-fetches expired regulatory sources into Agent Search and updates the AlloyDB
# freshness ledger. Installs the package with its [gcp] extra so the managed-service
# adapters (Agent Search ingestion, AlloyDB ledger, DLP redaction) are available.
#
# Build from the repo root so the build context includes pyproject.toml and src/:
#   docker build -f src/compliance_advisory/pipelines/Dockerfile \
#       -t REGION-docker.pkg.dev/PROJECT/compliance/refresh-job:latest .

FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

# Fail fast, no .pyc, unbuffered logs (so Cloud Logging captures the refresh summary live).
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Region is pinned for data residency; surfaced as defaults the adapters can read.
ENV COMPLIANCE_PROFILE=gcp \
    CLOUDSDK_CORE_DISABLE_PROMPTS=1

WORKDIR /app

# Copy the build metadata first so the dependency layer is cached across code changes.
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY config/ ./config/

# Install the package with the GCP managed-service adapters.
RUN python -m pip install --upgrade pip \
    && python -m pip install ".[gcp]"

# Run as a non-root user (least privilege).
RUN useradd --create-home --uid 1001 refresher \
    && chown -R refresher:refresher /app
USER refresher

# The Cloud Run job invokes the refresh entrypoint. Append "--full" via the job's args
# override to force a complete re-ingest of the registry.
ENTRYPOINT ["python", "-m", "compliance_advisory.pipelines.refresh_job"]
