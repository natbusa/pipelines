FROM python:3.11-slim-bookworm AS base

RUN apt-get update && \
    apt-get install -y gcc build-essential curl git fonts-hosny-amiri && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /app/fonts && \
    cp /usr/share/fonts/opentype/fonts-hosny-amiri/Amiri-Regular.ttf /app/fonts/arabic.ttf

WORKDIR /app

COPY ./requirements.txt .
RUN pip3 install uv && \
    uv pip install --system -r requirements.txt --no-cache-dir

FROM base AS app

COPY . .

# Fix write permissions for OpenShift / non-root users
RUN set -eux; \
    for d in /app /root /.local /.cache; do \
        mkdir -p "$d"; \
    done; \
    chgrp -R 0 /app /root /.local /.cache || true; \
    chmod -R g+rwX /app /root /.local /.cache || true; \
    find /app -type d -exec chmod g+s {} + || true; \
    find /root -type d -exec chmod g+s {} + || true; \
    find /.local -type d -exec chmod g+s {} + || true; \
    find /.cache -type d -exec chmod g+s {} + || true

# Install per-pipeline requirements (if any pipelines are baked in)
RUN bash start.sh --mode setup

ENV HOST="0.0.0.0"
ENV PORT="9099"

ENTRYPOINT [ "bash", "start.sh" ]
