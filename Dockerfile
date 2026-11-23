# livres — standard library only: nothing is installed from PyPI (supply chain, box Decision #17).
# Base image pinned by digest (python:3.13-slim, 2026-10-31). Dependabot (docker, cooldown 7 days) bumps it.
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

ARG VERSION=0.0.0-dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="livres" \
      org.opencontainers.image.description="French ebook catalogue (OPDS 2.0) for Ma liseuse" \
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
