FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_AGENT_DEMO_MODE=1 \
    DATA_AGENT_PROJECT_ROOT=/app

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY knowledge ./knowledge
COPY skills ./skills
COPY eval ./eval
COPY app.py ./app.py
RUN python -m pip install --no-cache-dir .

EXPOSE 8000 8501
CMD ["data-agent", "serve", "--host", "0.0.0.0", "--port", "8000"]
