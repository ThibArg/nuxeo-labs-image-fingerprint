nuxeo-labs-image-fingerprint
============================

> [!IMPORTANT]
> This is **work in progress**. Do not use as is.
>
> This repo will be forked to Nuxeo Sandbox presales repository when ready


### TL;DR

* Provides a Docker container that adds/read (via a HTTP service) a fingerprint to/from an image, using
  [Trustmark](https://github.com/adobe/trustmark), MIT License.

* The fingerprint is an **invisible watermark**: up to 8 characters (`A-Z`, `0-9`) encoded in the pixels
  themselves. It survives re-compression, resizing and format conversion, and it is not metadata, so it
  does not disappear when a tool strips EXIF.

* You build yourself a Docker image ("image-fingerprint") and deploy a container alongside Nuxeo (The Nuxeo
  image is never modified.)

* Nuxeo calls the service using `curl` from a `CommandLineConverter` contribution (Failures are returned
  as plain text with a meaningful HTTP status code) and `Blob.RunConverter` provides the desired rendition.

* [Deployment](#build-deploy)/[Usage](#usage-with-nuxeo):

  * Deployment:
    * Copy the `image-fingerprint` folder next to your `docker-compose.yml`
    * Add the `image-fingerprint` service to your `docker-compose.yml`
    * Then either deploy the whole stack or just this container if the rest is already running)
      * The first build downloads about 800 MB and the image is about 1.12 GB
  * Usage: Copy the misc. XML declarations and use `Blob.RunConverter`.

* **Security**. In current version, the service has no authentication and it processes files uploaded by users:
  * Never give it a `ports:` mapping in production
  * Only the containers of the compose network must reach it.

* Unlike the sibling `nuxeo-labs-ifc-converter`, every component in this image is permissively licensed
  (MIT / BSD / Apache-2.0). There is **no restriction on redistributing the built image** beyond the
  ordinary Debian base-image attribution. See [Licenses](#licenses).

## Build-Deploy

### 1. Setup for Docker Compose

  * Copy the whole `image-fingerprint` folder to your docker-compose folder, next to your `docker-compose.yml`
    (for example, clone the repository into that folder). The folder is self-contained: `Dockerfile`,
    `app.py` and `THIRD-PARTY-NOTICES.md`, nothing else is needed to build.
  * Update your `docker-compose.yml` file, add the `image-fingerprint` service:

```yml
services:
  nuxeo:
    . . .
  image-fingerprint:
    build:
      context: ./image-fingerprint
    image: image-fingerprint
    hostname: image-fingerprint
    restart: unless-stopped
    # logging: requires "x-logging: &default-logging" to be defined in your docker-compose.yml
    # (top of the file). If yours doesn't, compose fails with:
    #     "unknown anchor 'default-logging' referenced"
    # Either add the x-logging part or comment out the line below.
    logging: *default-logging
    expose:
      # HTTP API, reachable only by the other containers (no "ports:" on purpose: no authentication)
      - 8080
    # 4g is not padding: a 30 MP image (the MAX_PIXELS default) peaks at about 2.7 GB.
    # Measured: with mem_limit 2g the container is OOM-killed on such an image.
    # See "Memory" below before lowering this or raising MAX_PIXELS.
    mem_limit: 4g
    cpus: 2
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    volumes:
      - image-fingerprint-tmp:/tmp
    # Optional, defaults are:
    # environment:
    #   - MAX_UPLOAD_MB=100
    #   - CONVERT_TIMEOUT_S=60
    #   - MAX_CONCURRENT=1
    #   - TORCH_THREADS=2
    #   - MAX_PIXELS=30000000
  . . . other services . . .

volumes:
  . . . other volumes . . .
  image-fingerprint-tmp:
```

  * Optional: add `image-fingerprint` to the `depends_on:` list of the `nuxeo` service (short form), so that
   `docker compose up nuxeo` also starts it. Don't use `condition: service_healthy`: Nuxeo would not start
   when the service is unhealthy, while it only needs it to fingerprint images.

### 2. Build/Start

If your Nuxeo/Mongo/etc. containers were already running:

```bash
docker compose up -d --build image-fingerprint
```

> [!NOTE]
> The first build downloads about 800 MB (CPU-only PyTorch is the biggest part, then the 65 MB of
> TrustMark model weights); the image is about 1.12 GB. The weights are baked in at build time, so the
> running container never needs network access.

**Check Nuxeo can reach it** (here, the service name `nuxeo`)):

```bash
docker compose exec nuxeo curl -s http://image-fingerprint:8080/health
```

Expected: `{"status":"ok","trustmark":"0.9.2","model":"Q"}`

### 3. API

Both endpoints take the image as the **raw request body** (not multipart).

| Endpoint | Parameters | Returns |
|---|---|---|
| `POST /embed` | `payload` (**required**): 1 to 8 characters, `A-Z` and `0-9` only.<br>`format`: `jpeg` (default), `png` or `webp`.<br>`strength`: 0.5 to 2.0, default 1.0. Higher = more robust but more visible. | The fingerprinted image, with the matching `Content-Type` |
| `POST /decode` | none | `application/json`: `{"present":true,"payload":"K7Q2M9XA"}` or `{"present":false,"payload":null}` |
| `GET /health` | none | `{"status":"ok","trustmark":"0.9.2","model":"Q"}` (used by the Docker healthcheck) |

Accepted input formats: JPEG, PNG, WebP, TIFF and anything else Pillow can open. The shortest side must
be at least 150 px (`MIN_SIDE`), below which the watermark cannot be recovered.

> [!IMPORTANT]
> `/decode` answers **200 with `"present":false`** when there is no watermark. That is not an error:
> only a malformed request or a failure gives a non-2xx status.

#### Why only 8 characters

TrustMark carries a 100-bit payload. The default `BCH_5` schema spends 35 bits on error correction and 4
on the schema version, leaving 61 bits. Text mode packs 7 bits per character, so 8 characters (56 bits)
fit and 9 (63 bits) do not.

This is enough because the payload is meant to be a **token**, not data: store the token in Nuxeo
alongside the document id, the recipient, the date, and look it up when you decode. 8 characters of
`A-Z0-9` is about 2.8 x 10^12 combinations.

### 4. `curl` examples (from the Nuxeo container)**

```bash
# Fingerprint
curl -sS --fail-with-body -H "Expect:" \
     --data-binary @/tmp/photo.jpg \
     "http://image-fingerprint:8080/embed?payload=K7Q2M9XA&format=jpeg" \
     -o /tmp/photo-wm.jpg

# Check value
curl -sS --fail-with-body -H "Expect:" \
     --data-binary @/tmp/photo-wm.jpg \
     "http://image-fingerprint:8080/decode"
# Should be {"present":true,"payload":"K7Q2M9XA"}
```

### 5. Errors

Errors come back as plain text explaining the problem. Use `--fail-with-body` so `curl` exits non-zero
*and* keeps the message (plain `-f` discards it).

| Status | When |
|---|---|
| 400 | Empty body; `payload` missing or not 1-8 `A-Z0-9` characters; unknown `format`; `strength` outside 0.5-2.0; not a supported image; shortest side below `MIN_SIDE` (150 px) |
| 413 | Body larger than `MAX_UPLOAD_MB` (100), or image larger than `MAX_PIXELS` (30 MP). The pixel check runs on the header, before anything is decoded |
| 500 | Unexpected failure; the message points at the container logs |
| 504 | Processing took longer than `CONVERT_TIMEOUT_S`. The caller must allow more time than that, otherwise it gives up before this answer is sent |


## Usage with Nuxeo

Nuxeo needs contributions to three services:

* The Mime-Type service (only to name the `/decode` rendition `.json` reliably)
* The CommandLine service
* And the converter service

In all cases, create the XML and paste the contributions below as-is. You can - of course - centralize everying
in a single contribution if you prefer

> [!TIP]
> Conversions are cached by Nuxeo on the blob plus the parameters: fingerprinting the same image twice with the same
> token runs `curl` only once (as long as the file is still in the cache).

### 1. Register the mime type for the decode result

`application/json` is **not** in Nuxeo's default mime type registry. Without this, the JSON rendition
produced by `fingerprint-decode` falls back to `application/octet-stream`, which has no registered
extension, and the file ends up named from the temp file rather than from the source document.

```xml
<extension target="org.nuxeo.ecm.platform.mimetype.service.MimetypeRegistryService" point="mimetype">
  <mimetype normalized="application/json" binary="false" iconPath="file.png">
    <mimetypes>
      <mimetype>application/json</mimetype>
    </mimetypes>
    <extensions>
      <extension>json</extension>
    </extensions>
  </mimetype>
</extension>
```

`image/jpeg` and `image/png` are already registered by the platform, so the embed converters need nothing.
`image/webp` is **not** registered: if you add a WebP command line, contribute it the same way.

### 2. Register the command lines

The `image-fingerprint` HTTP service is not multipart, so everything must be passed in one shot as query parameters (can't use `-F` switch of `curl`for example).

> [!TIP]
> Prefixing the output with `nxblob-...` makes Nuxeo name the file with the original file + the correct extension. No need to pass a `destFileName` parameter 

> [!IMPORTANT]
> Of course, you can tune these as you wish. For example, the url is hard coded, you can adapt it or even pass it as a parameter (like `#{serviceUrl}`))

```xml
<!-- Timeout chain: CONVERT_TIMEOUT_S (60) < curl max-time (80) < <timeout> (90s) < transaction timeout -->
<extension target="org.nuxeo.ecm.platform.commandline.executor.service.CommandLineExecutorComponent"
            point="command">

  <!-- Fingerprint an image, JPEG output
       Requires the "payload" parameter: 1 to 8 chars, A-Z 0-9 -->
  <command name="fingerprint-embed-jpeg" enabled="true">
    <commandLine>curl</commandLine>
    <parameterString>-sS --fail-with-body --max-time 80 -H "Expect:" -o #{outDirPath}/nxblob-fingerprinted.jpg --data-binary @#{sourceFilePath} "http://image-fingerprint:8080/embed?format=jpeg&amp;payload=#{payload}"</parameterString>
    <timeout>90s</timeout>
    <installationDirective>
      Start the "image-fingerprint" service and make sure it is reachable at http://image-fingerprint:8080/ from the Nuxeo container.
    </installationDirective>
  </command>

  <!-- Same, PNG output: lossless, and keeps an alpha channel that JPEG would flatten -->
  <command name="fingerprint-embed-png" enabled="true">
    <commandLine>curl</commandLine>
    <parameterString>-sS --fail-with-body --max-time 80 -H "Expect:" -o #{outDirPath}/nxblob-fingerprinted.png --data-binary @#{sourceFilePath} "http://image-fingerprint:8080/embed?format=png&amp;payload=#{payload}"</parameterString>
    <timeout>90s</timeout>
    <installationDirective>
      Start the "image-fingerprint" service and make sure it is reachable at http://image-fingerprint:8080/ from the Nuxeo container.
    </installationDirective>
  </command>

  <!-- Decode the fingerprint -->
  <command name="fingerprint-decode" enabled="true">
    <commandLine>curl</commandLine>
    <parameterString>-sS --fail-with-body --max-time 80 -H "Expect:" -o #{outDirPath}/nxblob-fingerprinted.json --data-binary @#{sourceFilePath} "http://image-fingerprint:8080/decode"</parameterString>
    <timeout>90s</timeout>
    <installationDirective>
      Start the "image-fingerprint" service and make sure it is reachable at http://image-fingerprint:8080/ from the Nuxeo container.
    </installationDirective>
  </command>

</extension>
```

Points that matter:

- `enabled="true"` is **required**: the default is `false` and the command would be silently ignored.
- `-o #{outDirPath}/nxblob-fingerprinted...etc..` is **required** too: Without it, the `CommandLineConverter`
   lists the content of `outDirPath`, turning every file found there into a result blob.
- `--fail-with-body` keeps the service's plain-text error; plain `-f` would throw it away.
- `-H "Expect:"` avoids curl's 100-continue handshake on large bodies.
- **The timeouts form a chain, keep them in this order**:

  | Step | Value | Set in |
  |---|---|---|
  | The service gives up and answers `504` | `CONVERT_TIMEOUT_S` = 60s | `docker-compose.yml` (default) |
  | `curl` gives up | `--max-time 80` | the `<parameterString>` above |
  | Nuxeo kills `curl` | `<timeout>90s</timeout>` | the `<command>` above |
  | The transaction expires | 300s | `nuxeo.db.transactiontimeout` |

  Each step must be longer than the one above it, so that a conversion that takes too long comes back as the
  service's plain-text `504` instead of `curl` being killed first and leaving nothing to report.

> [!IMPORTANT]
> Two traps when you change these numbers:
> * `<timeout>` is parsed as a duration, so the unit is **mandatory**: `90s`, not `90`.
> * Nuxeo applies `min(<timeout>, time left in the transaction)` by prefixing the command with
>   `timeout -s 9`. If the transaction is already half spent when the converter runs, the 90s above is
>   silently shortened.

> [!NOTE]
> Under load the chain can still break: `CONVERT_TIMEOUT_S` only starts counting once the request gets one of
> the `MAX_CONCURRENT` slots, not when it arrives. A request waiting behind others can therefore exceed
> `--max-time` before the service even begins converting. If that happens, raise `MAX_CONCURRENT` (and the
> container's CPU/memory) rather than just stretching the timeouts.

### 3. Register the Converters

(A reminder that the mimetypes must have been contributed)

```xml
<extension target="org.nuxeo.ecm.core.convert.service.ConversionServiceImpl" point="converter">

  <converter name="fingerprint-embed-jpeg" class="org.nuxeo.ecm.platform.convert.plugins.CommandLineConverter">
    <sourceMimeType>image/jpeg</sourceMimeType>
    <sourceMimeType>image/png</sourceMimeType>
    <sourceMimeType>image/webp</sourceMimeType>
    <sourceMimeType>image/tiff</sourceMimeType>
    <destinationMimeType>image/jpeg</destinationMimeType>
    <parameters>
      <parameter name="CommandLineName">fingerprint-embed-jpeg</parameter>
    </parameters>
  </converter>

  <converter name="fingerprint-embed-png" class="org.nuxeo.ecm.platform.convert.plugins.CommandLineConverter">
    <sourceMimeType>image/jpeg</sourceMimeType>
    <sourceMimeType>image/png</sourceMimeType>
    <sourceMimeType>image/webp</sourceMimeType>
    <sourceMimeType>image/tiff</sourceMimeType>
    <destinationMimeType>image/png</destinationMimeType>
    <parameters>
      <parameter name="CommandLineName">fingerprint-embed-png</parameter>
    </parameters>
  </converter>

  <converter name="fingerprint-decode" class="org.nuxeo.ecm.platform.convert.plugins.CommandLineConverter">
    <sourceMimeType>image/jpeg</sourceMimeType>
    <sourceMimeType>image/png</sourceMimeType>
    <sourceMimeType>image/webp</sourceMimeType>
    <sourceMimeType>image/tiff</sourceMimeType>
    <destinationMimeType>application/json</destinationMimeType>
    <parameters>
      <parameter name="CommandLineName">fingerprint-decode</parameter>
    </parameters>
  </converter>
</extension>
```

### 4. Call it from Automation: `Blob.RunConverter`

In all the examples, `imageBlob` holds the image, mainly jpeg or png ((maybe a simple prior call to `imageBlob = input["file:content"];`).

> [!WARNING]
> `parameters` **must be a `key=value` string**, never a JavaScript object.
> `{'parameters': {'payload': 'K7Q2M9XA'}}` fails silently: the map arrives empty and `#{payload}` is
> never substituted. `curl` then treats the leftover `#` as a URL fragment delimiter and sends
> `payload=` empty, so the call comes back as `400 payload must be 1 to 8 characters, A-Z and 0-9 only`.
> Use `{'parameters': 'payload=K7Q2M9XA'}`. For several parameters, separate them with `\n`.

```js
// Embed
var wmJpeg = Blob.RunConverter(imageBlob, {
  'converter': 'fingerprint-embed-jpeg',
  'parameters': 'payload=K7Q2M9XA'
});

// Decode
var resultBlob = Blob.RunConverter(wmJpeg, {'converter': 'fingerprint-decode'});
var result = JSON.parse(resultBlob.getString());
if (result.present) {
  Console.log("Watermark found: " + result.payload);   // "K7Q2M9XA"
  // Look up the token in Nuxeo to find docId, company, user, date
} else {
  Console.log("No watermark found");
}
```

## Tune your Docker-Compose

### Settings (environment variables)

All are read once at import, so changing one needs a container restart.

| Variable | Default | Meaning |
|---|---|---|
| `MAX_UPLOAD_MB` | 100 | Maximum request body size |
| `MAX_PIXELS` | 30000000 | Maximum image size in pixels (30 MP). Checked on the header, before decoding. See "Memory" below |
| `CONVERT_TIMEOUT_S` | 60 | Maximum time for one embed or decode. Whoever calls the service must allow more time than this, see the timeout chain in [Usage with Nuxeo](#usage-with-nuxeo) |
| `MAX_CONCURRENT` | 1 | Requests processed at the same time; extra ones wait their turn. All requests share a single model instance, so leave this at 1 unless you have tested concurrency; scale with replicas instead. `CONVERT_TIMEOUT_S` starts only once a request gets a slot |
| `TORCH_THREADS` | 2 | CPU threads PyTorch uses. Match it to `cpus:` |
| `MIN_SIDE` | 150 | Reject images whose shortest side is below this; the watermark would not be recoverable |
| `TM_MODEL` | `Q` | TrustMark variant. **Changing this orphans every image already fingerprinted**, see "Good to know" |
| `TM_STRENGTH` | 1.0 | Default watermark strength when the request does not pass `strength` |

Each request is logged on one line: `embed format=jpeg in=117498B status=200 time=0.13s`
(`docker compose logs image-fingerprint`). The payload is deliberately **not** logged: the
token-to-recipient mapping belongs in Nuxeo, not in container logs.

### Memory

Measured on the built image, embedding one image at a time:

| Image | Peak container memory |
|---|---|
| idle, model loaded | ~450 MB |
| 1 MP (1200x800) | ~630 MB |
| 12 MP | ~1.5 GB |
| 24 MP | ~2.5 GB |
| 30 MP (the `MAX_PIXELS` default) | ~2.7 GB |

Rule of thumb: **~450 MB plus about 90 bytes per pixel**. TrustMark upsamples the watermark residual to
full resolution as `float32`, then mixes it with the cover image as `float64`, so the cost is driven by
the pixel count, not by the file size.

This is why `mem_limit` is `4g`. With `2g`, a 30 MP image gets the container **OOM-killed** (verified):
the client sees a dropped connection rather than a clean error. If you raise `MAX_PIXELS`, raise
`mem_limit` with it.

For reference, timings on an 8-core arm64 laptop, `TORCH_THREADS=2`:

| Image | embed | decode |
|---|---|---|
| 1 MP | 0.13 s | 0.07 s |
| 24 MP | 0.56 s | 0.16 s |

### Content

| File | Role |
|---|---|
| `image-fingerprint/Dockerfile` | Debian 13 / Python 3.12 image with CPU-only PyTorch, [TrustMark](https://github.com/adobe/trustmark) and the service. The model weights are downloaded at **build** time, so the container needs no network. Builds for the host architecture (arm64 on a Mac, x86_64 on EC2). |
| `image-fingerprint/app.py` | The HTTP service (FastAPI + uvicorn) on port 8080. |
| `image-fingerprint/THIRD-PARTY-NOTICES.md` | Licensing of everything the build downloads (summary: all permissive). |
| `samples/photo.jpg` | A generated test image, so the `curl` examples run out of the box. |
| `samples/make-sample.py` | Regenerates it. |

The `image-fingerprint/` folder is the Docker build context and is self-contained: `samples/` and
`README.md` stay at the repository root and are not part of the image.

## Try image-fingerprint without a Nuxeo Container

From the repository root (the `Dockerfile` is in the `image-fingerprint` folder, hence the build context):

```bash
docker build -t image-fingerprint ./image-fingerprint

docker run --rm -d --name image-fingerprint -p 127.0.0.1:18080:8080 image-fingerprint

curl -s http://localhost:18080/health
# {"status":"ok","trustmark":"0.9.2","model":"Q"}

# embed
curl -sS --fail-with-body --data-binary @samples/photo.jpg \
     "http://localhost:18080/embed?payload=K7Q2M9XA" -o /tmp/photo-wm.jpg

# read it back
curl -sS --fail-with-body --data-binary @/tmp/photo-wm.jpg "http://localhost:18080/decode"
# {"present":true,"payload":"K7Q2M9XA"}

# the original has no watermark
curl -sS --data-binary @samples/photo.jpg "http://localhost:18080/decode"
# {"present":false,"payload":null}

docker stop image-fingerprint
```

Compare `samples/photo.jpg` and `/tmp/photo-wm.jpg` side by side: they should look identical.

The port is published on `127.0.0.1` only, for this test. In a real deployment, don't publish it at all.

## Good to know

- **The payload is a token, not data.** 8 characters of `A-Z0-9`. Keep the mapping
  token -> (document, recipient, date) in Nuxeo and look it up after decoding.
- **A `true` result is not a proof.** The BCH error correction can occasionally "correct" random noise
  into a well-formed payload, so an image that was never fingerprinted can come back as
  `present: true` with a meaningless token. **Always check the decoded token against your own table**
  and treat an unknown token as "not found".
- **`TM_MODEL` is a one-way door.** An image encoded with variant `Q` cannot be decoded by `C`, `B` or
  `P`. Changing it on a deployment makes every already-fingerprinted image unreadable. Pick one and
  keep it.
- **Panoramas are only partly covered.** When the aspect ratio is above 2:1, TrustMark watermarks the
  centre square and leaves the rest untouched. Decoding looks at the same square, so the round-trip
  works, but a crop taken from the edges of a panorama will not decode.
- **Metadata is lost.** The image is re-encoded, so EXIF and the ICC profile are dropped, and the EXIF
  orientation is baked into the pixels (the image is watermarked as it is displayed). If you need the
  metadata, copy it back from the source blob afterwards.
- **What destroys the watermark**: resizing below ~150 px on the shortest side, heavy cropping, strong
  re-compression, and of course re-encoding from a screenshot. Moderate JPEG re-compression, resizing
  and format conversion are survived.
- **Alpha is preserved but not watermarked.** For a PNG/WebP with transparency, the alpha channel is
  put back untouched after the RGB channels are marked. JPEG output flattens it - use
  `format=png` for images with transparency.
- **Prefer PNG when the rendition will be processed again.** JPEG output re-compresses, and each
  successive generation weakens the mark.
- **The weights are baked into the image.** The container never downloads anything at runtime, which is
  what lets it run `read_only`. The flip side: if Adobe ever replaces a model file upstream, the
  checksum check at startup would try to re-download it, fail on the read-only filesystem, and the
  container would restart-loop. Rebuild the image in that case.
- **Scaling**: one worker holds one model in memory and `MAX_CONCURRENT` is 1. To go faster, add
  replicas rather than workers or threads.


## Support
**These features are not part of the Nuxeo Production platform.**

These solutions are provided for inspiration and we encourage customers to use them as code samples and learning
resources.

This is a moving project (no API maintenance, no deprecation process, etc.) If any of these solutions are found to be
useful for the Nuxeo Platform in general, they will be integrated directly into platform, not maintained here.

## Licenses

### This Repository's Code

This repository's code is licensed under the [Apache License, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0).

That includes `samples/photo.jpg`, which is generated by `samples/make-sample.py` and contains no
third-party content.

### Other

- The repository contains no third-party software. Building the image downloads open-source components,
  each under its own license: TrustMark and its model weights (MIT, Adobe), PyTorch and torchvision
  (BSD-3-Clause), Lightning/torchmetrics (Apache-2.0), Pillow (MIT-CMU), NumPy (BSD-3-Clause),
  FastAPI (MIT), uvicorn (BSD-3-Clause), Debian packages, etc.
  The full list is in [THIRD-PARTY-NOTICES.md](image-fingerprint/THIRD-PARTY-NOTICES.md).
- **Everything in the Python and ML layer is permissive** - MIT, BSD, Apache-2.0, PSF. There is no GPL,
  LGPL or AGPL component, so building the image and running it inside your organization creates no
  obligation, and **redistributing the built image is allowed**.
- The only caveat if you do redistribute: the `python:3.12-slim` base is Debian, which ships GPL system
  tools like every Debian-derived image. You then carry the ordinary Debian distributor obligations
  (keep `/usr/share/doc/*/copyright`, be able to provide the corresponding sources).


## About Nuxeo

Nuxeo Platform is an open source highly scalable, cloud-native, enterprise content management product with rich multimedia support, written in Java. Data can be stored in both SQL & NoSQL databases.

The development of the Nuxeo Platform is mostly done by Nuxeo employees with an open development model.

The source code, documentation, roadmap, issue tracker, testing, benchmarks are all public.

More information is available at [Hyland/Nuxeo](https://www.hyland.com/en/solutions/products/nuxeo-platform).
