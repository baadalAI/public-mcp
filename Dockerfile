FROM python:3.11-slim AS builder

WORKDIR /app
COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin/computeedge* /usr/local/bin/
COPY src/ src/

ENV PYTHONPATH=/app/src
ENV COMPUTEEDGE_TRANSPORT=stdio
ENV COMPUTEEDGE_DB_PATH=/data/computeedge.db

VOLUME /data

EXPOSE 8080

ENTRYPOINT ["python", "-m", "computeedge.server"]
