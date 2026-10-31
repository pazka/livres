# livres — standard library only: nothing is installed from PyPI (supply chain, box Decision #17).
# Base image pinned by digest (python:3.13-slim, 2026-10-31). Dependabot (docker, cooldown 7 days) bumps it.
FROM python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb

ARG VERSION=0.0.0-dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="livres" \
      org.opencontainers.image.description="Legal French ebook catalogue (OPDS 2.0) for Ma liseuse" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    LIVRES_VERSION=${VERSION} \
    LIVRES_SOURCES_DIR=/app/sources \
    LIVRES_DATA_DIR=/data

RUN groupadd --system --gid 10001 livres \
 && useradd --system --uid 10001 --gid 10001 --home-dir /nonexistent --shell /usr/sbin/nologin livres \
 && mkdir -p /data && chown 10001:10001 /data

WORKDIR /app
COPY --chown=root:root src/ /app/src/
COPY --chown=root:root sources/ /app/sources/
RUN python -m compileall -q /app/src

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "-m", "livres"]
