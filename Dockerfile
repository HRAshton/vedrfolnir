# Official python:3.15-slim was not published when 3.15.0 shipped
# (2026-10-09). Build the stable upstream release with a verified SHA256
# instead of shipping a Python release-candidate interpreter.
FROM debian:bookworm-slim AS python-build
ARG PYTHON_VERSION=3.15.0
ARG PYTHON_SHA256=ba4bed1ba346b916890b76d9e320451420aa69f6408997d33c66482eeae3d575
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates curl xz-utils \
    libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
    libffi-dev liblzma-dev libgdbm-dev libncursesw5-dev uuid-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp
RUN curl -fL --retry 3 "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tar.xz" \
      -o python.tar.xz \
    && echo "${PYTHON_SHA256}  python.tar.xz" | sha256sum -c - \
    && tar -xf python.tar.xz \
    && cd "Python-${PYTHON_VERSION}" \
    && ./configure --prefix=/opt/python --with-ensurepip=install \
    && make -j2 \
    && make install

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates passwd libssl3 zlib1g libbz2-1.0 libreadline8 \
    libsqlite3-0 libffi8 liblzma5 libgdbm6 libgdbm-compat4 \
    libncursesw6 libuuid1 libexpat1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=python-build /opt/python /opt/python
ENV PATH=/opt/python/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN ln -s /opt/python/bin/python3.15 /opt/python/bin/python \
    && ln -s /opt/python/bin/pip3.15 /opt/python/bin/pip \
    && python -c "import sys, ssl, sqlite3, zlib; assert sys.version_info[:3] == (3, 15, 0)"

WORKDIR /srv
COPY pyproject.toml README.md ./
COPY src/ ./src/
ARG INSTALL_MSSQL=false
RUN if [ "$INSTALL_MSSQL" = "true" ]; then \
        python -m pip install --no-cache-dir '.[mssql]'; \
    else \
        python -m pip install --no-cache-dir .; \
    fi && useradd --system --uid 10001 --create-home vedrfolnir

COPY queries/ ./queries/
COPY examples/ ./examples/
RUN mkdir -p /data && chown -R vedrfolnir:vedrfolnir /data
USER vedrfolnir
EXPOSE 8080
CMD ["uvicorn", "vedrfolnir.api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
