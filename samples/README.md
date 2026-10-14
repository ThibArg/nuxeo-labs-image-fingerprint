# Sample image

`photo.jpg` (1200x800, ~124 KB) is the image used by the `curl` examples in the repository's
`README.md`.

It is **generated**, not photographed: `make-sample.py` builds it from value noise and gradients
with a fixed seed, so the same script always produces the same file.

```bash
# regenerate it (needs numpy + Pillow; easiest from the built image)
docker run --rm -v "$PWD/samples:/s" --entrypoint python image-fingerprint /s/make-sample.py /s/photo.jpg

# or a larger one, to try the MAX_PIXELS limit
docker run --rm -v "$PWD/samples:/s" --entrypoint python image-fingerprint /s/make-sample.py /s/big.jpg 7000 5000
```

## Why synthetic

Two reasons:

- **No licensing question.** The file contains no third-party content, so it can live in this
  repository and be copied anywhere without attribution or redistribution concerns.
- **It is a fair test.** A watermark round-trip is only meaningful on an image that has the whole
  frequency range: large smooth gradients (the sky), a hard high-contrast edge (the sun disc),
  mid-frequency texture (the ridges) and fine grain. A flat colour chart or a solid-colour image
  would pass trivially and tell you nothing.

## License

`make-sample.py` and `photo.jpg` are part of this repository and are licensed under the
Apache License, Version 2.0, like the rest of the code.
