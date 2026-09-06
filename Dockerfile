FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm AS builder

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN sed -i \
        -e 's|http://deb.debian.org/debian|http://mirrors.cloud.aliyuncs.com/debian|g' \
        -e 's|http://security.debian.org/debian-security|http://mirrors.cloud.aliyuncs.com/debian-security|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        g++ \
        libpq-dev \
    && python -m venv "$VIRTUAL_ENV" \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /tmp/requirements.txt

RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r /tmp/requirements.txt

FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN sed -i \
        -e 's|http://deb.debian.org/debian|http://mirrors.cloud.aliyuncs.com/debian|g' \
        -e 's|http://security.debian.org/debian-security|http://mirrors.cloud.aliyuncs.com/debian-security|g' \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libgomp1 \
        libpq5 \
    && useradd --system --create-home --home-dir /home/agent --user-group agent \
    && mkdir -p /app /data/wiki-index /data/semantic-sync \
    && chown -R agent:agent /app /data \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY --chown=agent:agent . /app

USER agent
WORKDIR /app

EXPOSE 8090
VOLUME ["/data/wiki-index", "/data/semantic-sync"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/health', timeout=8)" || exit 1

CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
