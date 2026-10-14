nuxeo-labs-image-fingerprint
============================

> [!IMPORTANT]
> This is **work in progress**. Do not use as is.
>
> This repo will be forked to Nuxeo Sandbox presales repository when ready


### TL;DR

* Provides a Docker container that adds/read (via a HTTP service) a fingerprint to/from an image, using
  [Trustmark](https://github.com/adobe/trustmark), MIT License.

* You build yourself a Docker image ("image-fingerprint") and deploy a container alongside Nuxeo (The Nuxeo
  image is never modified.)

* Nuxeo calls the service using `curl` from a `CommandLineConverter` contribution (Failures are returned
  as plain text with a meaningful HTTP status code) and `Blob.RunConverter` provides the desired rendition.

* [Deployment](#build-deploy)/[Usage](#usage-with-nuxeo):

  * Deployment:
    * Copy the `image-fingerprint` folder next to your `docker-compose.yml`
    * Add the `image-fingerprint` service to your `docker-compose.yml`
    * Then either deploy the whole stack or just this container if the rest is already running)
      * The first build downloads about 1 GB and the image is about 1.4 GB
  * Usage: Copy the misc. XML declarations and use `Blob.RunConverter`.

* **Security**. In current version, the service has no authentication and it processes files uploaded by users:
  * Never give it a `ports:` mapping in production
  * Only the containers of the compose network must reach it.

## Build-Deploy

### 1. Setup for Docker Compose

  * Copy the whole `image-fingerprint` folder to your docker-compose folder, next to your `docker-compose.yml`
    (for example, clone the repository into that folder).
  * Update your `docker-compose.yml` file, add the `image-fingerprint` service:

```yml
services:
  nuxeo:
    . . .
  image-fingerprint:
    watermark:
    build:
      context: ./watermark-service
    image: watermark
    hostname: watermark
    restart: unless-stopped
    logging: *default-logging
    expose:
      - 8080
    mem_limit: 2g
    cpus: 2
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    volumes:
      - image-fingerprint-tmp:/tmp
  . . . other services . . .

volumes:
  . . . other volumes . . .
  image-fingerprint-tmp:
```

  * Optional: add `image-fingerprint` to the `depends_on:` list of the `nuxeo` service (short form), so that
   `docker compose up nuxeo` also starts it. Don't use `condition: service_healthy`: Nuxeo would not start
   when the converter is unhealthy, while it only needs it to convert IFC files.

### 2. Build/Start

If your Nuxeo/Mongo/etc. containers were already running:

```bash
docker compose up -d --build image-fingerprint
```

**Check Nuxeo can reach it** (here, the service name `nuxeo`)):

```bash
docker compose exec nuxeo curl -s http://image-fingerprint:8080/health
```

Expected: `{"status":"ok","HERE-A-KEY":"HERE VERISON IFNORMATION OF Trustmark"}`

### 3. API

. . .

`GET /health` returns `{"status":"ok", ...}` (used by the Docker healthcheck).

### 4. `curl` examples (from the Nuxeo container)**


```bash
# Fingerprint
curl -sS --fail-with-body -H "Expect:" \
     --data-binary @/tmp/photo.jpg \
     "http://watermark:8080/embed?payload=12345678&format=jpeg" \
     -o /tmp/photo-wm.jpg

# Check value
curl -sS --fail-with-body -H "Expect:" \
     --data-binary @/tmp/photo-wm.jpg \
     "http://watermark:8080/decode"
# Should be {"present":true,"payload":"12345678"}
```

### 5. Errors

. . .


## Usage with Nuxeo


Nuxeo two contributions to three services:

* The CommandLine service
* And the converter service

In all cases, create the XML and paste the contributions below as-is. You can - of course - centralize everying
in a single contribution if you prefer

> [!TIP]
> Conversions are cached by Nuxeo on the blob plus the parameters: fingerprinting the same IFC twice with the same
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

  <!-- Fingerprint an image
       Requires the "payload" parameter: 1 to 8 chars, A-Z 0-9 -->
  <command name="fingerprint-embed-jpeg" enabled="true">
    <commandLine>curl</commandLine>
    <parameterString>-sS --fail-with-body --max-time 80 -H "Expect:" -o #{outDirPath}/nxblob-fingerprinted.jpg --data-binary @#{sourceFilePath} "http://image-fingerprint:8080/embed?format=jpeg&amp;payload=#{payload}"</parameterString>
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
- **The timeouts form a chain, keep them in this order**:

  | Step | Value | Set in |
  |---|---|---|
  | The service gives up and answers `504` | `CONVERT_TIMEOUT_S` = 150s | `docker-compose.yml` (default) |
  | `curl` gives up | `--max-time 170` | the `<parameterString>` above |
  | Nuxeo kills `curl` | `<timeout>180s</timeout>` | the `<command>` above |
  | The transaction expires | 300s | `nuxeo.db.transactiontimeout` |

  Each step must be longer than the one above it, so that a conversion that takes too long comes back as the
  service's plain-text `504` instead of `curl` being killed first and leaving nothing to report.

> [!IMPORTANT]
> Two traps when you change these numbers:
> * `<timeout>` is parsed as a duration, so the unit is **mandatory**: `180s`, not `180`.
> * Nuxeo applies `min(<timeout>, time left in the transaction)`. If the transaction is already half spent
>   when the converter runs, the 180s above is silently shortened.

> [!NOTE]
> Under load the chain can still break: `CONVERT_TIMEOUT_S` only starts counting once the request gets one of
> the `MAX_CONCURRENT` slots, not when it arrives. A request waiting behind others can therefore exceed
> `--max-time` before the service even begins converting. If that happens, raise `MAX_CONCURRENT` (and the
> container's CPU/memory) rather than just stretching the timeouts.

### 2. Register the Converters

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


```js
// Embed
var wmJpeg = Blob.RunConverter(imageBlob, {
  'converter': 'fingerprint-embed-jpeg',
  'parameters': 'payload=12345678'
});

// Decode
var resultBlob = Blob.RunConverter(wmJpeg, {'converter': 'fingerprint-decode'});
var result = JSON.parse(resultBlob.getString());
if (result.present) {
  Console.log("Watermark found: " + result.payload);   // "12345678"
  // Look up the token in Nuxeo to find docId, company, user, date
} else {
  Console.log("No watermark found");
}
```

## Tune your Docker-Compose

### Settings (environment variables)

. . .

### Content

. . .

## Try image-fingerprint without a Nuxeo Container

. . .

## Good to know

. . .


## Support
**These features are not part of the Nuxeo Production platform.**

These solutions are provided for inspiration and we encourage customers to use them as code samples and learning
resources.

This is a moving project (no API maintenance, no deprecation process, etc.) If any of these solutions are found to be
useful for the Nuxeo Platform in general, they will be integrated directly into platform, not maintained here.

## Licenses

### This Repository's Code

This repository's code is licensed under the [Apache License, Version 2.0](http://www.apache.org/licenses/LICENSE-2.0).

### Other

. . .


## About Nuxeo

Nuxeo Platform is an open source highly scalable, cloud-native, enterprise content management product with rich multimedia support, written in Java. Data can be stored in both SQL & NoSQL databases.

The development of the Nuxeo Platform is mostly done by Nuxeo employees with an open development model.

The source code, documentation, roadmap, issue tracker, testing, benchmarks are all public.

More information is available at [Hyland/Nuxeo](https://www.hyland.com/en/solutions/products/nuxeo-platform).

