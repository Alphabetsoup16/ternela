# syntax=docker/dockerfile:1
FROM python:3.15.0-slim-bookworm@sha256:2a41267d9de9d358f77a1de356e0f16ecba8d39827855be0ce9ef4792edce402 AS runtime-base
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
FROM runtime-base AS bundle
WORKDIR /src
COPY . .
# A published application bundle already includes its verified source seed.
RUN if [ -d seed ]; then cp -R seed data; fi \
    && python -m ternela.setup_demo \
    && python -m ternela.distribution build --output /bundle

FROM runtime-base
RUN useradd --uid 1000 --create-home ternela \
    && mkdir /state && chown 1000:1000 /state
COPY --from=bundle /bundle/ternela-v*.tar.gz /opt/bootstrap.tar.gz
COPY ternela/distribution.py ternela/local_service.py ternela/local_control.py ternela/storage_control.py /opt/runtime/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TERNELA_CONTAINER=1 TERNELA_STATE=/state
USER 1000:1000
WORKDIR /opt/runtime
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import json,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=3)); assert r['status']=='ok' and r['sources_present']"
ENTRYPOINT ["python", "-u", "/opt/runtime/local_service.py"]
