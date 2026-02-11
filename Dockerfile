FROM python:3.11-slim-bookworm AS base

ARG PIPELINES_URLS
ARG PIPELINES_REQUIREMENTS_PATH
ARG INSTALL_FRONTMATTER_REQUIREMENTS=false

# Install GCC and build tools.
# These are kept in the final image to enable installing packages on the fly.
RUN apt-get update && \
    apt-get install -y gcc build-essential curl git && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY ./requirements.txt .
RUN pip3 install uv && \
    uv pip install --system -r requirements.txt --no-cache-dir


# Layer on for other components
FROM base AS app

ENV PIPELINES_URLS=${PIPELINES_URLS} \
    PIPELINES_REQUIREMENTS_PATH=${PIPELINES_REQUIREMENTS_PATH} \
    INSTALL_FRONTMATTER_REQUIREMENTS=${INSTALL_FRONTMATTER_REQUIREMENTS}

# Copy the application code
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

# Run a docker command if either PIPELINES_URLS or PIPELINES_REQUIREMENTS_PATH is not empty
RUN if [ -n "$PIPELINES_URLS" ] || [ -n "$PIPELINES_REQUIREMENTS_PATH" ]; then \
    echo "Running docker command with PIPELINES_URLS or PIPELINES_REQUIREMENTS_PATH"; \
    ./start.sh --mode setup; \
    fi

# Expose the port
ENV HOST="0.0.0.0"
ENV PORT="9099"

# if we already installed the requirements on build, we can skip this step on run
ENTRYPOINT [ "bash", "start.sh" ]
