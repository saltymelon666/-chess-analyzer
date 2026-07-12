FROM python:3.12-slim AS runtime

ARG STOCKFISH_URL=https://github.com/official-stockfish/Stockfish/releases/download/sf_18/stockfish-ubuntu-x86-64.tar

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STOCKFISH_PATH=/usr/local/bin/stockfish \
    PORT=10000

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends --yes ca-certificates curl \
    && curl --fail --location --retry 3 "$STOCKFISH_URL" --output /tmp/stockfish.tar \
    && mkdir -p /tmp/stockfish \
    && tar -xf /tmp/stockfish.tar -C /tmp/stockfish \
    && STOCKFISH_BINARY="$(find /tmp/stockfish -type f -name 'stockfish-ubuntu-x86-64' | head -n 1)" \
    && test -n "$STOCKFISH_BINARY" \
    && install -m 0755 "$STOCKFISH_BINARY" /usr/local/bin/stockfish \
    && rm -rf /var/lib/apt/lists/* /tmp/stockfish /tmp/stockfish.tar

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

COPY app ./app

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 10000

CMD ["sh", "-c", "uvicorn app.api:app --host 0.0.0.0 --port ${PORT}"]

