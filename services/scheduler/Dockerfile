FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY services/scheduler/pyproject.toml ./pyproject.toml
COPY services/scheduler/langprobe_scheduler ./langprobe_scheduler

RUN pip install --no-cache-dir .

CMD ["python", "-m", "langprobe_scheduler"]
