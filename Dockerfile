FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY mender ./mender
RUN pip install . && useradd --system --uid 10001 mender
USER mender

EXPOSE 8000 9100
CMD ["mender", "serve", "--port", "8000"]
