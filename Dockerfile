FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PARO_ENV=production \
    PARO_COOKIE_SECURE=true \
    PARO_DATABASE_PATH=/data/paro_web.sqlite3 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple

WORKDIR /app
RUN groupadd --system --gid 10001 paro \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app --no-create-home paro
# 依赖层只依赖这三个入口：改 app/ 里的代码不会让它失效。
# wheels/ 用来放离线 wheel，有就优先用，没有则回退到索引。
COPY --chown=paro:paro pyproject.toml requirements.txt ./
COPY --chown=paro:paro wheels/ /wheels/
RUN pip install --no-cache-dir --find-links=/wheels -r requirements.txt
COPY --chown=paro:paro app ./app
COPY --chown=paro:paro content ./content
# 不再 pip install 本项目：uvicorn 在 /app 下直接 import app.main。
RUN mkdir -p /data && chown paro:paro /data

USER paro

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; request = urllib.request.Request('http://127.0.0.1:8000/healthz', headers={'Host': 'akitoya.top'}); urllib.request.urlopen(request, timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
