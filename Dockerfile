FROM python:3.12-slim

RUN adduser --uid 1000 --disabled-password --gecos "" app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

RUN mkdir -p /app/data && chown app:app /app/data

USER app

# живість: бот торкається /app/data/heartbeat щохвилини
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
    CMD python -c "import os,sys,time; sys.exit(0 if time.time()-os.path.getmtime('/app/data/heartbeat')<180 else 1)"

CMD ["python", "-m", "app.main"]
