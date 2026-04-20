FROM python:3.11-slim

WORKDIR /workspace

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libgomp1 \
  && rm -rf /var/lib/apt/lists/*

RUN pip install "numpy<2" pytest
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.2.2

COPY . /workspace/environment

RUN install -m 0755 /workspace/environment/bin/applypatch /usr/local/bin/applypatch
RUN mkdir -p /logs/verifier && chmod -R a+rX /workspace /logs
