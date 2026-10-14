# SPDX-License-Identifier: Apache-2.0
# Debian-based, like ifc-converter. Builds for the host architecture (arm64 on a Mac, x86_64 on EC2).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /app

# CPU-only PyTorch: much smaller than the default CUDA build
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download the model weights at build time: the container runs read-only, with no download at runtime
RUN python -c "from trustmark import TrustMark; TrustMark(verbose=False, model_type='Q')"

COPY app.py .

RUN useradd -r -u 1001 wm
USER wm

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=4)"

# One worker = one model in memory. Scale with replicas rather than workers.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
