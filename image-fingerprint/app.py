# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hyland Software, Inc.
"""image-fingerprint: embeds or reads an invisible TrustMark watermark in an image sent as the raw request body.
    POST /embed?payload=<TOKEN>[&format=jpeg|png|webp][&strength=1.0]
    POST /decode
    GET  /health
Examples:
    curl -sf --data-binary @photo.jpg "http://image-fingerprint:8080/embed?payload=K7Q2M9XA" -o photo-wm.jpg
    curl -sf --data-binary @photo-wm.jpg "http://image-fingerprint:8080/decode"
The model is loaded once at startup; this is the reason the service exists (a CLI would reload it each call).
"""
import asyncio
import io
import logging
import os
import re
import time
from importlib.metadata import version

import torch
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from trustmark import TrustMark

MAX_UPLOAD_MB = float(os.environ.get("MAX_UPLOAD_MB", "100"))
CONVERT_TIMEOUT_S = float(os.environ.get("CONVERT_TIMEOUT_S", "60"))
# Requests share one model instance: keep 1 unless you have tested concurrency; scale with replicas instead.
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "1"))
TORCH_THREADS = int(os.environ.get("TORCH_THREADS", "2"))
TM_MODEL = os.environ.get("TM_MODEL", "Q")          # Images encoded with one variant can't be decoded with another
DEFAULT_STRENGTH = float(os.environ.get("TM_STRENGTH", "1.0"))
MIN_SIDE = int(os.environ.get("MIN_SIDE", "150"))   # TrustMark needs about 150 px on the shortest side
# Measured peak for one embed: about 450 MB idle plus ~90 bytes per pixel. TrustMark upsamples the
# watermark residual to full resolution as float32, then mixes it with the cover image as float64.
# 30 MP peaks at ~2.7 GB, which is why the README suggests mem_limit 4g; at 2g it is OOM-killed.
MAX_PIXELS = int(os.environ.get("MAX_PIXELS", "30000000"))

# TrustMark carries a 100-bit payload. The default BCH_5 schema spends 35 bits on error correction
# and 4 on the schema version, leaving 61 bits of data. Text mode packs 7 bits per character, so
# 8 characters (56 bits) fit and 9 (63 bits) do not. TrustMark does not reject a longer value, it
# truncates it to 61 bits silently, so the 9th character comes back corrupted with present=True.
# Letters and digits only: anything else would either need URL encoding (& # % + ?) or be lost at
# decode time, where TrustMark applies .rstrip("\x00").strip() and drops leading/trailing spaces.
# The payload is case-sensitive: BCH error correction restores the exact bits or nothing at all.
PAYLOAD_RE = re.compile(r"^[A-Za-z0-9]{1,8}$")

FORMATS = {"jpeg": ("JPEG", "image/jpeg"), "png": ("PNG", "image/png"), "webp": ("WEBP", "image/webp")}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("image-fingerprint")

torch.set_num_threads(TORCH_THREADS)
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # decompression-bomb guard, see open_image() for the real limit

# loadRemover=False: the remover model (trustmark_rm_Q.ckpt, 77 MB) only serves remove_watermark(),
# which this service never calls. The Dockerfile pre-downloads the weights with the same flag, so
# nothing is fetched here. That matters: the container runs on a read-only filesystem.
TM = TrustMark(verbose=False, model_type=TM_MODEL, loadRemover=False)
TM_VERSION = version("trustmark")

app = FastAPI(title="image-fingerprint")
semaphore = asyncio.Semaphore(MAX_CONCURRENT)


class ConversionError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


async def read_body(request):
    """Read the request body, enforcing the size limit."""
    limit = MAX_UPLOAD_MB * 1024 * 1024
    if int(request.headers.get("content-length") or 0) > limit:
        raise ConversionError(413, f"file larger than {MAX_UPLOAD_MB:g} MB")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise ConversionError(413, f"file larger than {MAX_UPLOAD_MB:g} MB")
    if not buf:
        raise ConversionError(400, "empty body: send the image as the raw request body (curl --data-binary @image.jpg)")
    return bytes(buf)


def open_image(data):
    try:
        img = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError:
        raise ConversionError(413, f"image larger than the {MAX_PIXELS:,} pixel limit")
    except (UnidentifiedImageError, OSError):
        raise ConversionError(400, "not a supported image (JPEG, PNG, WebP, TIFF...)")
    # Image.open() only reads the header, so the size is known before any pixel is decoded.
    # Pillow's own bomb guard only fires above 2 x MAX_IMAGE_PIXELS; this check is what actually
    # enforces MAX_PIXELS, and it rejects the request before load() allocates anything.
    width, height = img.size
    if width * height > MAX_PIXELS:
        raise ConversionError(413, f"image is {width}x{height}, over the {MAX_PIXELS:,} pixel limit")
    try:
        img.load()
    except Image.DecompressionBombError:
        raise ConversionError(413, f"image larger than the {MAX_PIXELS:,} pixel limit")
    except (UnidentifiedImageError, OSError):
        raise ConversionError(400, "truncated or corrupt image file")
    img = ImageOps.exif_transpose(img)  # watermark the image as it is displayed
    if min(img.size) < MIN_SIDE:
        raise ConversionError(400, f"image too small: shortest side must be at least {MIN_SIDE} px")
    return img


def do_embed(data, payload, fmt, strength):
    img = open_image(data)
    if img.mode == "P":
        img = img.convert("RGBA")
    alpha = img.getchannel("A") if "A" in img.getbands() else None
    out = TM.encode(img.convert("RGB"), payload, MODE="text", WM_STRENGTH=strength)
    if alpha is not None and fmt != "jpeg":
        out.putalpha(alpha)  # alpha is preserved but not watermarked
    pil_format, _ = FORMATS[fmt]
    buf = io.BytesIO()
    out.save(buf, format=pil_format, **({"quality": 95} if fmt in ("jpeg", "webp") else {}))
    return buf.getvalue()


def do_decode(data):
    img = open_image(data)
    secret, present, _schema = TM.decode(img.convert("RGB"), MODE="text")
    return {"present": bool(present), "payload": secret if present else None}


async def run(fn, *args):
    """Run CPU work in a thread, one slot per MAX_CONCURRENT.
    Note: a thread can't be killed. On timeout the caller gets a 504 but the encode finishes in background."""
    async with semaphore:
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=CONVERT_TIMEOUT_S)
        except TimeoutError:
            raise ConversionError(504, f"processing timed out after {CONVERT_TIMEOUT_S:g}s")


@app.get("/health")
def health():
    return {"status": "ok", "trustmark": TM_VERSION, "model": TM_MODEL}


@app.post("/embed")
async def embed(request: Request):
    started = time.monotonic()
    payload = request.query_params.get("payload", "")
    fmt = request.query_params.get("format", "jpeg").lower()
    strength_param = request.query_params.get("strength", str(DEFAULT_STRENGTH))
    status, received = 500, 0
    try:
        if not PAYLOAD_RE.match(payload):
            raise ConversionError(400, "payload must be 1 to 8 characters, A-Z, a-z and 0-9 only")
        if fmt not in FORMATS:
            raise ConversionError(400, f"format must be one of: {', '.join(FORMATS)}")
        try:
            strength = float(strength_param)
        except ValueError:
            strength = -1
        if not 0.5 <= strength <= 2.0:
            raise ConversionError(400, "strength must be a number between 0.5 and 2.0")
        data = await read_body(request)
        received = len(data)
        result = await run(do_embed, data, payload, fmt, strength)
        status = 200
        return Response(result, media_type=FORMATS[fmt][1])
    except ConversionError as e:
        status = e.status
        return PlainTextResponse(e.message + "\n", status_code=e.status)
    except Exception:
        log.exception("unexpected error")
        return PlainTextResponse("internal error, see the watermark logs\n", status_code=500)
    finally:
        # The payload is not logged: the token-to-recipient mapping lives in Nuxeo only
        log.info("embed format=%s in=%dB status=%d time=%.2fs", fmt or "-", received, status, time.monotonic() - started)


@app.post("/decode")
async def decode(request: Request):
    started = time.monotonic()
    status, received = 500, 0
    try:
        data = await read_body(request)
        received = len(data)
        result = await run(do_decode, data)
        status = 200
        return JSONResponse(result)
    except ConversionError as e:
        status = e.status
        return PlainTextResponse(e.message + "\n", status_code=e.status)
    except Exception:
        log.exception("unexpected error")
        return PlainTextResponse("internal error, see the watermark logs\n", status_code=500)
    finally:
        log.info("decode in=%dB status=%d time=%.2fs", received, status, time.monotonic() - started)
