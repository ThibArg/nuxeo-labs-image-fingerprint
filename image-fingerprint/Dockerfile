# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hyland Software, Inc.
#
# image-fingerprint: HTTP service embedding and reading an invisible TrustMark watermark in an image.
# Third-party components downloaded by this build and their licenses: see THIRD-PARTY-NOTICES.md
# Debian-based, like ifc-converter. Builds for the host architecture (arm64 on a Mac, x86_64 on EC2).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /app

# CPU-only PyTorch: much smaller than the default CUDA build. These two versions are the most recent
# ones published for cp312 on BOTH linux_x86_64 and linux_aarch64, so the same pin builds on either
# architecture. "==2.14.1" also matches the "2.14.1+cpu" local version, per PEP 440.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
      torch==2.14.1 \
      torchvision==0.29.1

# trustmark pulls omegaconf, numpy, lightning, torchmetrics, einops and six.
# pillow is NOT one of its dependencies (it arrives via torchvision), but app.py imports PIL
# directly, so it is pinned here rather than inherited.
RUN pip install --no-cache-dir \
      trustmark==0.9.2 \
      fastapi==0.143.1 \
      uvicorn==0.54.0 \
      pillow==12.3.0

# Download the model weights at build time: the container runs read-only, with no download at runtime.
# loadRemover=False skips trustmark_rm_Q.ckpt (77 MB): the remover model is only needed by
# remove_watermark(), which this service never calls. Keep this flag identical to the one in app.py,
# otherwise the service would try to fetch the missing file at startup and fail on the read-only
# filesystem.
RUN python -c "from trustmark import TrustMark; TrustMark(verbose=False, model_type='Q', loadRemover=False)"

COPY app.py THIRD-PARTY-NOTICES.md ./

RUN useradd --system --uid 10001 --home-dir /tmp --shell /usr/sbin/nologin wm
USER wm

EXPOSE 8080

# No curl in python:3.12-slim, so the probe uses urllib. urlopen raises on any non-2xx status.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=4)"

# One worker = one model in memory. Scale with replicas rather than workers.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
