# ReviewMind — application image.
#
# Python 3.12 rather than the host's 3.14: every pinned dependency has mature
# 3.12 wheels, which removes the build risk that made 3.14 awkward on the host.
# The experiment's results do not depend on interpreter version (temperature 0,
# pinned model), so this does not affect reproducibility of the numbers.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# git: the experiment runner records the commit a result came from.
# curl: used by the container healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl \
 && rm -rf /var/lib/apt/lists/*

# Dependencies first, so editing code does not invalidate the slow install layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir jinja2

COPY reviewmind/ ./reviewmind/
COPY experiments/ ./experiments/
COPY api/ ./api/
COPY scripts/ ./scripts/
COPY README.md ./

RUN mkdir -p results

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=25s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
