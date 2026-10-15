nuxeo-labs-image-fingerprint
============================

> [!IMPORTANT]
> This is **work in progress**. Do not use as is.
>
> This repo will be forked to Nuxeo Sandbox presales repository when ready


### TL;DR

* Provides a Docker container that adds/read (via a HTTP service) a fingerprint to/from an image, using
  [Trustmark](https://github.com/adobe/trustmark), MIT License.

* The fingerprint is an **invisible watermark**: up to 8 characters (`A-Z`, `a-z`, `0-9`) encoded in the pixels
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
| `POST /embed` | `payload` (**required**): 1 to 8 characters, `A-Z`, `a-z` and `0-9` only, **case-sensitive**.<br>`format`: `jpeg` (default), `png` or `webp`.<br>`strength`: 0.5 to 2.0, default 1.0. Higher = more robust but more visible. | The fingerprinted image, with the matching `Content-Type` |
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

The service rejects a longer payload with a 400 because TrustMark itself does not: it truncates to
61 bits silently, so a 9-character token comes back with its last character corrupted and
`"present":true`, which no caller could detect.

This is enough because the payload is meant to be a **token**, not data: store the token in Nuxeo
alongside the document id, the recipient, the date, and look it up when you decode. 8 characters of
`A-Za-z0-9` is about 2.2 x 10^14 combinations. See
[Generate a unique token](#4-generate-a-unique-token) for a ready-to-paste Automation Script that
produces one with no risk of collision.

> [!WARNING]
> The payload is **case-sensitive**: `K7q2m9xA` and `k7Q2M9Xa` are two different fingerprints, and
> `/decode` returns the exact case that was embedded. Make sure the lookup table on the Nuxeo side is
> case-sensitive too - an `ILIKE` query or a case-insensitive vocabulary would make the two collide.
> Sticking to uppercase-only tokens sidesteps the question entirely.

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
| 400 | Empty body; `payload` missing or not 1-8 `A-Za-z0-9` characters; unknown `format`; `strength` outside 0.5-2.0; not a supported image; shortest side below `MIN_SIDE` (150 px) |
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
       Requires the "payload" parameter: 1 to 8 chars, A-Z a-z 0-9 -->
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

### 4. Generate a unique token

The service does not generate tokens, it only embeds the one you give it. Here is a ready-to-paste
Automation Script that produces a unique 8-character token on every call.

It is built on a sequence rather than on randomness, and that is the whole point: **two calls can
never return the same token**, and no lookup is needed to be sure of it. Drawing 8 characters at
random would instead hit a 1% chance of at least one collision after only ~238,000 tokens
(birthday bound on 32^8), which you would have to detect with a query on every single call.

`Fn.getNextId` returns a sequence number incremented atomically, outside the transaction. A 4-round
Feistel permutation then scrambles it, so that consecutive documents do not produce visibly related
tokens and the volume you have fingerprinted cannot be read off a token.

```js
/*
 * Returns a unique 8-character fingerprint token for the image-fingerprint service.
 * Fn.getNextId yields a sequence number; a 4-round Feistel permutation then scrambles it, so
 * consecutive calls produce unrelated-looking tokens. The permutation is bijective on 0..2^40-1
 * and 32^8 is exactly 2^40, so every sequence number maps to a distinct token, the whole
 * 8-character space is reachable, and uniqueness is guaranteed by construction - never checked
 * against a table. The alphabet drops I, O, 0, 1 and every lowercase letter, so no two characters
 * can be mistaken for one another.
 */
function run(input, params) {

    var ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789",   // 32 chars, and 32^8 === 2^40
        KEYS = [0x5A3C1, 0x0F7E2, 0x31D93, 0x6C2A4],     // change these once, then NEVER again
        OFFSET = 0x6A09E667F3,                           // keeps low sequence numbers out of the left=0 corner
        SPACE = 1099511627776,                           // 2^40
        HALF = 1048576,                                  // 2^20
        MASK = 0xFFFFF,                                  // 20 bits
        seq, token;

    function roundFn(r, k) {
        return ((r * 40503 + k) ^ (r >>> 7)) & MASK;
    }

    /*
     * Feistel network. Bijective on 0 .. 2^40-1 whatever roundFn does, because both halves stay
     * on exactly 20 bits: that is what guarantees two sequence numbers never collide.
     */
    function scramble(n) {
        var left = Math.floor(n / HALF), right = n % HALF, i, t;
        for (i = 0; i < KEYS.length; i++) {
            t = right;
            right = (left ^ roundFn(right, KEYS[i])) & MASK;
            left = t;
        }
        return left * HALF + right;
    }

    function toToken(n) {
        var s = "", i;
        for (i = 0; i < 8; i++) {
            s = ALPHABET.charAt(n % 32) + s;
            n = Math.floor(n / 32);   // not ">>": JS bitwise operators are limited to 32 bits
        }
        return s;
    }

    seq = parseInt(Fn.getNextId("fingerprint"), 10);

    /*
     * Without this guard an unparseable sequence silently becomes NaN, and NaN flows all the way
     * through scramble and toToken to yield one constant token, reused for every document.
     */
    if (!isFinite(seq) || seq < 0 || seq >= SPACE) {
        throw new Error("fingerprint: unusable sequence number: " + seq);
    }

    token = toToken(scramble((seq + OFFSET) % SPACE));   // 1 -> "RHPVBW3L", 2 -> "5HGPUUKA"

    // e.g. Audit.LogEvent / a directory entry: token -> docId, company, user, date
    // Write it in the same unit of work as the embed: a retry calls getNextId again and gets a
    // different token, which would otherwise leave an orphan entry or an untracked image.

    return token;
}
```

Measured over 1,000,000 consecutive calls: no duplicate, and all 32 symbols appear at each of the 8
positions with less than 1% spread. The capacity is 2^40 = 1,099,511,627,776 tokens, about 3,000
years at one million fingerprints a day.

> [!IMPORTANT]
> `KEYS` and `OFFSET` are frozen for good once you are in production: changing them re-issues tokens
> that were already handed out for other documents. They also sit in clear text in the script, so
> anyone who can read the Studio project can invert the permutation and recover the chronological
> order. This is obfuscation, not encryption - which is enough here, since a decoded token must be
> checked against your own table anyway.

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
| `README-Running-at-scale.md` | Tuning: memory budget, host sizing, CPU, replicas, and calling the service asynchronously. |

The `image-fingerprint/` folder is the Docker build context and is self-contained: `samples/` and
`README.md` stay at the repository root and are not part of the image.

## Running in production

**In short.** This service is **memory-bound, not CPU-bound**. Sizing it comes down to one question:
how large an image must it accept?

- **Memory per container** is at most `450 MB + MAX_CONCURRENT x 90 bytes x MAX_PIXELS`. Measured peak
  with the defaults (1 concurrent, 30 MP) is about 2.7 GB, which is why `mem_limit` is `4g`.
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
- **Synchronous is usually the right answer.** `Blob.RunConverter` runs inside the caller's
  transaction, and an embed takes well under a second. Only go asynchronous if the fingerprinting
  must not be able to fail the operation that triggered it - and read
  [the async section](README-Running-at-scale.md#calling-it-asynchronously) first, because the
  obvious route does **not** give you usable retries.

> [!NOTE]
> The sizing figures quoted here are estimates from a development machine, not benchmarks.
> [README-Running-at-scale.md](README-Running-at-scale.md) has the full numbers, the method and the
> caveats. Measure on your own target before committing to a size.

### Sizing, memory, CPU and scaling out

The defaults are sized for a 30 MP ceiling with one conversion at a time, which suits a presales
demo and most production installations. When they stop fitting, see
**[README-Running-at-scale.md](README-Running-at-scale.md)**: the memory budget and host sizing
examples, what the CPU and thread limits are actually made of, how to scale with replicas (and why
not with uvicorn workers), and how to call the service asynchronously with retries.


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

- **The payload is a token, not data.** 8 characters of `A-Za-z0-9`, case-sensitive. Keep the mapping
  token -> (document, recipient, date) in Nuxeo and look it up after decoding. See
  [Generate a unique token](#4-generate-a-unique-token).
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
  rather than uvicorn workers or threads. See
  [README-Running-at-scale.md](README-Running-at-scale.md) for the memory budget, host sizing and the
  measurements behind that.
- **Asynchronous calls need care**: an async post-commit handler retries twice, but with **no
  backoff** - all attempts are spent within milliseconds, so they do not survive a restart of this
  service. The Bulk Action Framework does, but only with `failOnError` enabled. All tested, see
  [Calling it asynchronously](README-Running-at-scale.md#calling-it-asynchronously).


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
