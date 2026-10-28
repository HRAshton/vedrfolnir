# Official python:3.15-slim was not published when 3.15.0 shipped
# (2026-10-09). Build the stable upstream release with a verified SHA256
# instead of shipping a Python release-candidate interpreter.
FROM debian:trixie-slim@sha256:f6b08b09a05725f9431502d3b5714f5c4576e82c1a84fa1debe0c24a7e0c3ec4 AS python-build
ARG PYTHON_VERSION=3.15.0
ARG PYTHON_SHA256=ba4bed1ba346b916890b76d9e320451420aa69f6408997d33c66482eeae3d575
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    build-essential ca-certificates curl xz-utils pkg-config \
    libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
    libffi-dev liblzma-dev libgdbm-dev libncurses-dev uuid-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp
ARG EXPAT_VERSION=2.9.0
ARG EXPAT_SHA256=1e6371862cc31999b368c3b89b49994f0677e1bab5f1b2b85ae3741f5d803051
RUN curl -fL --retry 3 "https://github.com/libexpat/libexpat/releases/download/R_$(printf %s "$EXPAT_VERSION" | tr . _)/expat-${EXPAT_VERSION}.tar.xz" -o expat.tar.xz \
    && echo "${EXPAT_SHA256}  expat.tar.xz" | sha256sum -c - \
    && tar -xf expat.tar.xz \
    && cd "expat-${EXPAT_VERSION}" \
    && ./configure --prefix=/opt/expat --libdir=/opt/expat/lib --disable-static --without-xmlwf \
    && make -j2 && make install
RUN curl -fL --retry 3 "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tar.xz" \
      -o python.tar.xz \
    && echo "${PYTHON_SHA256}  python.tar.xz" | sha256sum -c - \
    && tar -xf python.tar.xz \
    && cd "Python-${PYTHON_VERSION}" \
    && LIBEXPAT_CFLAGS="-I/opt/expat/include" LIBEXPAT_LIBS="-L/opt/expat/lib -lexpat" \
       CPPFLAGS="-I/opt/expat/include" LDFLAGS="-L/opt/expat/lib -Wl,-rpath,/opt/expat/lib" \
       ./configure --prefix=/opt/python --with-ensurepip=install --with-system-expat \
    && make -j2 \
    && make install

FROM debian:trixie-slim@sha256:f6b08b09a05725f9431502d3b5714f5c4576e82c1a84fa1debe0c24a7e0c3ec4
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    ca-certificates passwd libssl3t64 zlib1g libbz2-1.0 libreadline8t64 \
    libsqlite3-0 libffi8 liblzma5 libgdbm6t64 libgdbm-compat4t64 \
    libncursesw6 libuuid1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=python-build /opt/expat /opt/expat
COPY --from=python-build /opt/python /opt/python
ENV PATH=/opt/python/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN ln -s /opt/python/bin/python3.15 /opt/python/bin/python \
    && ln -s /opt/python/bin/pip3.15 /opt/python/bin/pip \
    && python -c "import sys, ssl, sqlite3, zlib, pyexpat; assert pyexpat.version_info >= (2, 9, 0); assert sys.version_info[:3] == (3, 15, 0)"

WORKDIR /srv
COPY pyproject.toml README.md ./
COPY requirements/container-constraints.txt ./requirements/container-constraints.txt
COPY src/ ./src/
ARG INSTALL_MSSQL=false
RUN if [ "$INSTALL_MSSQL" = "true" ]; then \
        python -m pip install --no-cache-dir --constraint requirements/container-constraints.txt '.[mssql]'; \
    else \
        python -m pip install --no-cache-dir --constraint requirements/container-constraints.txt .; \
    fi && python -m pip check && useradd --system --uid 10001 --create-home vedrfolnir

COPY queries/ ./queries/
COPY examples/ ./examples/
RUN mkdir -p /data && chown -R vedrfolnir:vedrfolnir /data
USER vedrfolnir
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8080/health', timeout=4)" || exit 1
EXPOSE 8080
CMD ["uvicorn", "vedrfolnir.api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
