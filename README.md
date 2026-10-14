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

> [!IMPORTANT]
> Metadata is lost: The image is re-encoded, so EXIF and the ICC profile are dropped.

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
  * Only the containers of the compose network must reach it.<br/>
  See [Running in production](#running-in-production) for the full deployment checklist.

* **Licensing**: Every component in this image is permissively licensed (MIT / BSD / Apache-2.0). There is
  **no restriction on redistributing the built image** beyond the ordinary Debian base-image attribution.<br/>
  See [Licenses](#licenses).

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

### 4. `curl` examples (from the Nuxeo container)

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

Nuxeo needs contributions to two services:

* The CommandLine service
* And the converter service

In all cases, create the XML and paste the contributions below as-is. You can - of course - centralize everying
in a single contribution if you prefer

> [!TIP]
> Conversions are cached by Nuxeo on the blob plus the parameters: fingerprinting the same image twice with the same
> token runs `curl` only once (as long as the file is still in the cache).

### 1. Register the command lines

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
- **The extension you put on the `-o` file name is load-bearing.** The `nxblob-` prefix marks the name as a
   throwaway temp name, so Nuxeo renames the rendition to the source document's base name plus *either* the
   first extension registered for the `<destinationMimeType>`, *or* - when that mime type declares none - the
   extension of the `-o` file name (`ConversionServiceImpl.updateResultBlobFileName`):

  | `<destinationMimeType>` | In Nuxeo's registry? | `photo.jpg` becomes | Comes from |
  |---|---|---|---|
  | `image/jpeg` | yes (`jpg`, `jpeg`) | `photo.jpg` | the registry |
  | `image/png` | yes (`png`) | `photo.png` | the registry |
  | `application/json` | no | `photo.json` | the `-o` name |
  | `image/webp` | no | `photo.webp` | the `-o` name |

  So give `-o` the right extension and all four come out correct - **no mime type contribution is needed**,
  not even for `application/json` and `image/webp`, which the platform does not register.
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

### 2. Register the Converters

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

### 3. Call it from Automation: `Blob.RunConverter`

In all the examples, `imageBlob` holds the image, mainly jpeg or png ((maybe a simple prior call to `imageBlob = input["file:content"];`).

> [!WARNING]
> `parameters` **must be a `key=value` string**, never a JavaScript object. This could change in future version of Nuxeo.

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
| `MAX_CONCURRENT` | 1 | Requests processed at the same time; extra ones wait their turn. It multiplies with `MAX_PIXELS` inside the container's memory budget, so raise it only together with a lower `MAX_PIXELS`. All requests also share a single model instance, so leave it at 1 unless you have tested concurrency; scale with replicas instead. `CONVERT_TIMEOUT_S` starts only once a request gets a slot. See [Running in production](#running-in-production) |
| `TORCH_THREADS` | 2 | CPU threads PyTorch uses. Match it to `cpus:`; setting it higher is slightly counter-productive, and on large images the service cannot use much more than 2 cores anyway |
| `MIN_SIDE` | 150 | Reject images whose shortest side is below this; the watermark would not be recoverable |
| `TM_MODEL` | `Q` | TrustMark variant. **Changing this orphans every image already fingerprinted**, see "Good to know" |
| `TM_STRENGTH` | 1.0 | Default watermark strength when the request does not pass `strength` |

Each request is logged on one line: `embed format=jpeg in=117498B status=200 time=0.13s`
(`docker compose logs image-fingerprint`). The payload is deliberately **not** logged: the
token-to-recipient mapping belongs in Nuxeo, not in container logs.

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

## Running in production

**In short.** This service is **memory-bound, not CPU-bound**. Sizing it comes down to one question:
how large an image must it accept?

- **Memory per container** is roughly `450 MB + MAX_CONCURRENT x 90 bytes x MAX_PIXELS`. With the
  defaults (1 concurrent, 30 MP) that is about 2.7 GB, which is why `mem_limit` is `4g`.
- **Need more throughput?** Add **replicas** - not `--workers`, not threads. Each replica gets its own
  memory budget and its own failure domain.
- **Short of RAM?** Lower `MAX_PIXELS` first; it is by far the cheapest lever. At 8 MP a replica fits in
  `mem_limit: 2g`, more than doubling how many fit on a host. Most image libraries never approach 30 MP.
- **Raising `MAX_CONCURRENT`?** Only together with a lower `MAX_PIXELS`: the two multiply inside one
  container's memory budget.
- **More vCPU per container?** Little to gain beyond about 2. On a large image the service only manages
  to use ~1.6 cores no matter how many you give it.
- **Don't forget the neighbours.** Nuxeo, MongoDB and OpenSearch share the host. Budget fingerprinting
  *on top of* them, not inside their headroom.

> [!IMPORTANT]
> **The figures in this section are estimates, not benchmarks.** They were taken on a development
> machine - Docker Desktop on an arm64 laptop, 8 CPUs allocated to the Docker VM, one container at a
> time, synthetic test images, and no Nuxeo load running alongside. They show the *shape* of the problem
> (memory grows with pixels, CPU barely helps) and give orders of magnitude to plan with. Absolute
> numbers will differ on x86_64 server hardware, with real photographs, and under concurrent load - the
> timings in particular will not transfer from arm64 to x86_64. The memory relationship is the one that
> travels best, because it is driven by array sizes rather than by the CPU. **Measure on your own target
> before committing to a size.**

### Sizing the host

*Estimates from a development machine, not benchmarks - see the note above. The Nuxeo-side figures
below are a measured idle floor plus an explicitly flagged projection.*

Start from the memory budget, because that is what fails hard:

```
per container  ~=  450 MB  +  MAX_CONCURRENT x 90 bytes x MAX_PIXELS
```

| `MAX_PIXELS` | Typical source | Peak per container | Suggested `mem_limit` |
|---|---|---|---|
| 30 MP (default) | 60 Mpx-class camera, large scans | ~2.7 GB | `4g` |
| 12 MP | Most phone photos | ~1.5 GB | `2g` |
| 8 MP | Web/press-sized images | ~1.2 GB | `2g` |

Then add what Nuxeo itself needs. For reference, a **development** stack sitting completely idle:

| Container | Idle memory |
|---|---|
| Nuxeo | ~1.2 GB |
| OpenSearch (`-Xmx512m`) | ~1.3 GB |
| MongoDB | ~0.1 GB |
| OpenSearch Dashboards | ~0.2 GB |
| **Total, idle** | **~2.7 GB** |

That is a **floor, measured at rest**. Under real load Nuxeo's heap and OpenSearch's caches grow well
past it; a working estimate of 6-8 GB for the Nuxeo side is more realistic, but that figure is a
projection, not something measured here.

Putting the two together, for a host shared with Nuxeo:

| Host | Nuxeo side (estimated, under load) | Left over | Realistic fingerprinting capacity |
|---|---|---|---|
| 4 vCPU / 16 GB (e.g. `t3.xlarge`) | ~7 GB, 2-3 vCPU | ~8 GB, 1-2 vCPU | **1 replica** at `mem_limit: 4g`, `cpus: 2`. Fine for occasional fingerprinting; CPU-contended for bulk runs |
| 8 vCPU / 32 GB (e.g. `m5.2xlarge`) | ~7 GB, 2-3 vCPU | ~24 GB, 5 vCPU | **2-3 replicas** comfortably |
| 4 vCPU / 16 GB, `MAX_PIXELS=8000000` | ~7 GB | ~8 GB | **2-3 replicas** at `mem_limit: 2g` - the same box, by accepting smaller images |

The last row is the point worth taking away: **lowering `MAX_PIXELS` buys more capacity than buying a
bigger instance**, as long as your images really are that size. A 30 MP ceiling on a library of 12 MP
photos reserves memory that is never used.

### Memory

*Estimates from a development machine, not benchmarks - see the note above.*

Peak container memory, embedding one image at a time:

| Image | Peak container memory |
|---|---|
| idle, model loaded | ~450 MB |
| 1 MP (1200x800) | ~630 MB |
| 12 MP | ~1.5 GB |
| 24 MP | ~2.5 GB |
| 30 MP (the `MAX_PIXELS` default) | ~2.7 GB |

Rule of thumb: **~450 MB plus about 90 bytes per pixel**. TrustMark upsamples the watermark residual to
full resolution as `float32`, then mixes it with the cover image as `float64`, so the cost is driven by
the pixel count, not by the file size. A 3 MB JPEG and a 3 MB PNG of very different dimensions cost
very different amounts of memory.

This is why `mem_limit` is `4g`. With `2g`, a 30 MP image gets the container **OOM-killed**: the client
sees a dropped connection, not a clean error, and every other request in that container dies with it.
If you raise `MAX_PIXELS`, raise `mem_limit` with it.

### CPU and threads

*Estimates from a development machine, not benchmarks - see the note above. The timings in particular
are arm64 and will not transfer to x86_64; the relative shape is what matters here.*

`TORCH_THREADS` and `cpus` matched, median of 5 runs:

| `cpus` = `TORCH_THREADS` | 24 MP embed | 1 MP embed | 24 MP decode |
|---|---|---|---|
| 1 | 0.99 s | 0.38 s | 0.25 s |
| 2 | 0.65 s | 0.18 s | 0.17 s |
| 4 | 0.57 s | 0.13 s | 0.16 s |
| 8 | 0.53 s | 0.10 s | 0.17 s |

Eight times the CPU buys **1.9x** on a 24 MP image, but **3.6x** on a 1 MP one. That looks backwards
until you see where the time goes:

- The model always runs on a **fixed 256x256 tensor**, whatever the input size. That part is PyTorch,
  parallelises well, and dominates *small* images.
- The full-resolution work - upsampling the residual and mixing it into the cover - grows with the pixel
  count, and a large part of it is single-threaded NumPy arithmetic. That dominates *large* images and
  does not parallelise.

Measuring the cores actually used during a sustained loop, with 8 threads and no CPU limit, confirms it:

| Workload | CPU actually used |
|---|---|
| 24 MP embeds | ~1.6 cores |
| 1 MP embeds | ~3.9 cores |

So on large images the service cannot use more than about 2 cores however many you give it.
**`cpus: 2` is the sweet spot**; beyond that you are paying for capacity the process cannot consume.

Setting `TORCH_THREADS` higher than the CPU quota is mildly counter-productive - at `cpus: 2`:

| `TORCH_THREADS` at `cpus: 2` | 24 MP embed |
|---|---|
| 1 | 0.81 s |
| 2 | 0.66 s |
| 4 | 0.66 s |
| 8 | 0.68 s |

Match `TORCH_THREADS` to `cpus` and leave it there.

### Scaling out: replicas, not workers or threads

There are three things you could turn up, and only one of them is the right answer:

| Knob | What it does | Verdict |
|---|---|---|
| `TORCH_THREADS` | More cores on **one** image | Small, sub-linear gain. Match it to `cpus` |
| `uvicorn --workers N` | N processes in **one** container | **Don't** - see below |
| Replicas | N separate containers | **This one** |

**Why not workers.** uvicorn starts workers with the `spawn` multiprocessing context, so each worker is
a fresh interpreter that re-imports `app.py` and builds **its own copy of the model**. There is no
copy-on-write sharing. Four workers cost ~1.8 GB of model before a single request arrives, all inside
the *same* `mem_limit`.

Worse, `MAX_CONCURRENT` is a **per-process** semaphore. Four workers each holding `MAX_CONCURRENT=1`
means four conversions at once, so the limit you set to protect memory quietly stops doing so:

```
4 workers x 30 MP x ~90 bytes/pixel  ~=  9 GB   inside a 4 GB container
```

And the blast radius is wrong: the OOM killer acts on the **cgroup**, which is the whole container, so
the three healthy requests on the other workers die alongside the one that overran.

**Why replicas.** Each container is its own cgroup with its own `mem_limit`, so the budget is *per
replica*. `MAX_CONCURRENT=1` keeps meaning what you sized it to mean: one image in flight, one memory
ceiling. An OOM kills one replica, the others keep serving, and `restart: unless-stopped` brings it
back - one failed request instead of four.

```yml
  image-fingerprint:
    build:
      context: ./image-fingerprint
    image: image-fingerprint
    # no "hostname:" and no "container_name:" when scaling - the service name is what resolves
    restart: unless-stopped
    expose:
      - 8080
    mem_limit: 4g
    cpus: 2
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    volumes:
      - image-fingerprint-tmp:/tmp
    deploy:
      replicas: 3
```

`docker compose up -d --scale image-fingerprint=3` does the same thing without touching the file.

**No load balancer is needed.** Docker's embedded DNS resolves the service name to all replica
addresses and shuffles them, so successive lookups land on different containers. Every Nuxeo conversion
is a fresh, short-lived `curl` process doing a fresh DNS lookup, so the load spreads by itself. Verified
with two replicas: consecutive calls to `http://image-fingerprint:8080/health` alternated between
`172.20.0.2` and `172.20.0.3`.

Two caveats. The shuffle is **not load-aware** - there is no least-connections logic, so a request can
land on a busy replica while another sits idle. And this works *because* `curl` is short-lived; a client
with a persistent connection pool would pin itself to one replica and need a real balancer.

**What a replica buys.** Six concurrent 1 MP requests, `MAX_CONCURRENT=1`, `cpus: 2` each:

| | Request completion times | Wall clock |
|---|---|---|
| 1 replica | 0.22, 0.42, 0.62, 0.82, 1.03, 1.21 s | 1.26 s |
| 2 replicas | 0.22, 0.26, 0.48, 0.51, 0.70, 0.70 s | 0.75 s |

The single-replica staircase, one step of ~0.2 s per request, *is* `MAX_CONCURRENT=1` doing its job:
requests queue rather than compete for memory. A single request takes 0.23 s, so the sixth one spent
about a second waiting before it even started.

That queueing is why the timeout chain matters. `CONVERT_TIMEOUT_S` only starts counting **once a
request gets a slot**, so the wait does not count against it - but it does count against
`curl --max-time 80`. Roughly:

```
queue wait  ~=  (position in queue - 1)  x  per-image time
```

At ~0.66 s per 24 MP image, one replica absorbs a burst of roughly 120 queued requests before the
client's 80 s deadline is at risk. Past that, add replicas rather than stretching the timeouts.

### Security

The service has **no authentication** and it processes files uploaded by users. The deployment is what
keeps it safe:

- **Never give it a `ports:` mapping.** `expose: 8080` only, so that only containers on the compose
  network can reach it. The `127.0.0.1` publishing used in the trial section below is for local testing
  only.
- **Keep `read_only: true`** with a `/tmp` volume, `cap_drop: ALL` and `no-new-privileges`. The process
  runs as a non-root user (uid 10001) and never writes outside `/tmp`.
- **Keep the limits.** `MAX_UPLOAD_MB`, `MAX_PIXELS` and `MAX_CONCURRENT` are what stop a single
  oversized upload from taking the container down.
- **The payload is never logged**, deliberately: the token-to-recipient mapping belongs in Nuxeo.
- The container needs **no network access** at runtime - the model weights are baked into the image at
  build time.

### What to watch

| Signal | Meaning |
|---|---|
| `docker inspect -f '{{.State.OOMKilled}}'` is `true` | An image exceeded the memory budget. Lower `MAX_PIXELS` or raise `mem_limit` |
| A burst of `504`s | Processing exceeded `CONVERT_TIMEOUT_S`. Usually means images far larger than expected |
| `curl` failing before the service answers | Queueing, not slowness. Check the gap between `--max-time` and the per-image time, and add replicas |
| Container restart loop at startup | Almost always the model checksum check failing on the read-only filesystem. Rebuild the image |
| `413` in the logs | Callers are sending images over `MAX_UPLOAD_MB` or `MAX_PIXELS` - expected and harmless, but worth knowing about |

Each request logs one line, with no payload:
`embed format=jpeg in=117498B status=200 time=0.13s`.

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
- **Scaling and sizing**: the service is memory-bound, not CPU-bound, and scales by adding replicas
  rather than uvicorn workers or threads. See [Running in production](#running-in-production) for the
  memory budget, host sizing and the measurements behind that.


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
