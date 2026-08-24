FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -e .

COPY data ./data
COPY evals ./evals

EXPOSE 8000
CMD ["uvicorn", "vakil.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
