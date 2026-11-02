# 墨舟 BookMate 运行镜像（仅 CPU；模型全部走在线 API）
# 默认官方 python:3.11-slim；离线/内网构建可通过 --build-arg BASE_IMAGE=<本地镜像> 覆盖。
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE} AS base

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG BASE_APT_MIRROR=
# 国内网络可用 --build-arg BASE_APT_MIRROR=mirrors.tencent.com 加速 apt
RUN if [ -n "$BASE_APT_MIRROR" ]; then \
      sed -i "s|http://deb.debian.org|http://$BASE_APT_MIRROR|" /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
      sed -i "s|http://deb.debian.org|http://$BASE_APT_MIRROR|" /etc/apt/sources.list; \
    fi

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

# curl 供 HEALTHCHECK；构建工具供少数需要编译的依赖
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app/__init__.py app/__init__.py
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" -e ".[dev]"

COPY app ./app
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY scripts ./scripts
COPY web ./web
COPY data ./data
COPY eval ./eval
COPY tests ./tests

RUN useradd --create-home --uid 10001 appuser \
 && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
