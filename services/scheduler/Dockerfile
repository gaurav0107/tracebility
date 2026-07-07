FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install the workspace-shared langprobe-api package first. uv treats it as a
# workspace member; pip needs it pre-installed locally so the subsequent
# ``pip install .`` finds it (not published to PyPI).
COPY services/api /tmp/langprobe-api
RUN pip install --no-cache-dir /tmp/langprobe-api && rm -rf /tmp/langprobe-api

COPY services/scheduler/pyproject.toml ./pyproject.toml
COPY services/scheduler/langprobe_scheduler ./langprobe_scheduler

RUN pip install --no-cache-dir .

CMD ["python", "-m", "langprobe_scheduler"]
