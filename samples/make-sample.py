#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hyland Software, Inc.
"""Generates samples/photo.jpg, the image used by the curl examples in README.md.

The image is synthetic on purpose: it carries no third-party content and raises no licensing
question, yet it has what makes a watermark round-trip meaningful - smooth gradients, hard edges
and fine grain across the whole frequency range. A flat colour chart would not.

Deterministic: the same seed always produces the same bytes.

    python3 samples/make-sample.py                       # samples/photo.jpg, 1200x800
    python3 samples/make-sample.py big.jpg 6000 4000     # a larger one, to test MAX_PIXELS
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

SEED = 20260214
JPEG_QUALITY = 92


def value_noise(rng, height, width, cells):
    """One octave: a small random grid, bicubically upsampled to the full size.

    The grid is resized as 32-bit float (PIL mode "F"), not as uint8: quantising to 256 levels
    before a large upscale leaves visible banding in the smooth areas.
    """
    grid = rng.random((cells + 1, cells + 1)).astype(np.float32)
    small = Image.fromarray(grid, mode="F")
    return np.asarray(small.resize((width, height), Image.BICUBIC), dtype=np.float32)


def fractal_noise(rng, height, width, octaves=6, base_cells=4, decay=0.6):
    """Sum of octaves, each twice as fine and `decay` times as strong.

    The sum is stretched back to [0, 1] at the end: without that, the coarsest octave dominates
    and the result hovers around 0.5 everywhere, which looks like a flat wash rather than texture.
    """
    out = np.zeros((height, width), dtype=np.float32)
    amplitude = 1.0
    for i in range(octaves):
        out += amplitude * value_noise(rng, height, width, base_cells * 2 ** i)
        amplitude *= decay
    low, high = float(out.min()), float(out.max())
    return (out - low) / max(high - low, 1e-6)


def build(width, height):
    rng = np.random.default_rng(SEED)
    ys = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    xs = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]

    # Sky: a smooth vertical gradient. Large flat-ish areas are the hardest case for a watermark,
    # so the image deliberately has a lot of them.
    sky = np.stack([
        0.25 + 0.55 * ys,
        0.45 + 0.40 * ys,
        0.75 + 0.20 * ys,
    ]).transpose(1, 2, 0) * np.ones((height, width, 1), dtype=np.float32)

    # Sun: a hard-edged disc, the kind of high-contrast boundary that JPEG rings around.
    cx, cy, r = 0.72, 0.26, 0.085
    aspect = width / height
    dist = np.sqrt(((xs - cx) * aspect) ** 2 + (ys - cy) ** 2)
    disc = np.clip((r - dist) * height / 3.0, 0.0, 1.0)[:, :, None]
    glow = np.exp(-((dist / (r * 4.0)) ** 2))[:, :, None]
    img = sky + 0.35 * glow * np.array([1.0, 0.85, 0.55], dtype=np.float32)
    img = img * (1.0 - disc) + disc * np.array([1.0, 0.97, 0.88], dtype=np.float32)

    # Three ridge lines, each closer and darker. Haze fades the distant ones (aerial perspective),
    # and each gets its own high-frequency texture so the slopes are not flat bands.
    for base, shade, rough, detail in [
        (0.50, 0.62, 0.09, 0.22),
        (0.62, 0.42, 0.15, 0.34),
        (0.76, 0.22, 0.24, 0.50),
    ]:
        ridge = base - rough * fractal_noise(rng, 1, width, octaves=4, base_cells=2)[0]
        mask = (ys > ridge[None, :]).astype(np.float32)[:, :, None]
        texture = fractal_noise(rng, height, width, octaves=8)[:, :, None]
        colour = np.array([shade, shade * 1.04, shade * 1.18], dtype=np.float32)
        colour = colour * (1.0 - detail + 2.0 * detail * texture)
        img = img * (1.0 - mask) + colour * mask

    # Water: reflects the sky, with horizontal streaks. ys is a column, so the mask has to be
    # widened to the full frame before it can index the (height, width, 3) image.
    water = np.repeat(ys > 0.82, width, axis=1)
    streaks = fractal_noise(rng, height, width, octaves=5, base_cells=2)
    img[water] = (img[water] * 0.55
                  + np.array([0.18, 0.30, 0.42], dtype=np.float32) * 0.45)
    img[water] += (streaks[water][:, None] - 0.5) * 0.12

    # Film grain. Fine, zero-mean noise across the whole frame.
    img += (rng.standard_normal((height, width, 3)).astype(np.float32)) * 0.012

    return Image.fromarray((np.clip(img, 0.0, 1.0) * 255).astype(np.uint8), mode="RGB")


def main():
    args = sys.argv[1:]
    out = Path(args[0]) if args else Path(__file__).with_name("photo.jpg")
    width = int(args[1]) if len(args) > 1 else 1200
    height = int(args[2]) if len(args) > 2 else 800
    build(width, height).save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    print(f"{out} {width}x{height} {out.stat().st_size} bytes")


if __name__ == "__main__":
    main()
