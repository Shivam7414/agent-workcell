FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[dev]"

COPY reference_erp ./reference_erp
COPY tests ./tests

RUN useradd --create-home workcell
USER workcell
