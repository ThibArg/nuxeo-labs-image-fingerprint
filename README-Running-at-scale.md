nuxeo-labs-image-fingerprint - Running at scale
===============================================

Everything in this document is **tuning**. The defaults shipped in `README.md` are fine for a
presales demo, a proof of concept, and most production installations that fingerprint renditions on
publication. Read this when the defaults stop fitting - large images, high volume, or a need for the
fingerprinting to survive the service being briefly unavailable.

- [Sizing and tuning](#sizing-and-tuning) - the section below, in four parts:
  - [Sizing the host](#sizing-the-host) - memory budget, host examples
  - [Memory](#memory), [CPU and threads](#cpu-and-threads) - what the limits are made of
  - [Scaling out](#scaling-out-replicas-not-workers-or-threads) - replicas, and why not workers
- [Deploying as a shared service](#deploying-as-a-shared-service) - off-box, shared by several Nuxeo nodes
- [Calling it asynchronously](#calling-it-asynchronously) - queues, retries, and what actually works

> [!IMPORTANT]
> **The figures in the sizing sections are estimates, not benchmarks.** They were taken on a
> development machine - Docker Desktop on an arm64 laptop, 8 CPUs allocated to the Docker VM, one
> container at a time, synthetic test images, and no Nuxeo load running alongside. They show the
> *shape* of the problem (memory grows with pixels, CPU barely helps) and give orders of magnitude to
> plan with. Absolute numbers will differ on x86_64 server hardware, with real photographs, and under
> concurrent load - the timings in particular will not transfer from arm64 to x86_64. The memory
> relationship is the one that travels best, because it is driven by array sizes rather than by the
> CPU. **Measure on your own target before committing to a size.**
>
> The [asynchronous](#calling-it-asynchronously) section is different: those findings were **run
> against a real Nuxeo LTS 2025 instance**, and each is marked with what was observed.

## Sizing and tuning

### Sizing the host

*Estimates from a development machine, not benchmarks - see the note above. The Nuxeo-side figures
below are a measured idle floor plus an explicitly flagged projection.*

Start from the memory budget, because that is what fails hard:

```
per container  <=  450 MB  +  MAX_CONCURRENT x 90 bytes x MAX_PIXELS
```

Size with that, but read it as an **upper bound rather than a prediction**: measured peaks sit a
little under it at the top of the range (it gives 3.15 GB for 30 MP, where the measurement is about
2.7 GB). Erring high is the right direction here - the failure mode is an OOM kill, not a slowdown.

| `MAX_PIXELS` | Typical source | Measured peak per container | Suggested `mem_limit` |
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

Putting the two together, for a host shared with Nuxeo. `cpus` is a quota, not a reservation, and a
large embed only manages to use ~1.6 cores, so a little CPU oversubscription is fine - but it is what
decides whether a bulk run crawls:

| Host | Nuxeo side (estimated, under load) | Left over | Realistic fingerprinting capacity |
|---|---|---|---|
| 4 vCPU / 16 GB (e.g. `t3.xlarge`) | ~7 GB, 2-3 vCPU | ~8 GB, 1-2 vCPU | **1 replica** at `mem_limit: 4g`, `cpus: 2`. Fine for occasional fingerprinting; CPU-contended for bulk runs |
| 8 vCPU / 32 GB (e.g. `m5.2xlarge`) | ~7 GB, 2-3 vCPU | ~24 GB, 5 vCPU | **2-3 replicas** comfortably |
| 4 vCPU / 16 GB, `MAX_PIXELS=8000000` | ~7 GB, 2-3 vCPU | ~8 GB, 1-2 vCPU | **2-3 replicas** at `mem_limit: 2g` fit in the *memory* - the same box, by accepting smaller images. The CPU does not grow with them, so they absorb bursts rather than run faster |

The last row is the point worth taking away: **lowering `MAX_PIXELS` buys more capacity than buying a
bigger instance**, as long as your images really are that size. A 30 MP ceiling on a library of 12 MP
photos reserves memory that is never used.

> [!NOTE]
> `t3` is a burstable instance family. A one-off bulk run over thousands of images will drain its CPU
> credits and then be throttled to the baseline. For sustained work, prefer a fixed-performance
> family (`m5`, `c5`, ...).

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

Rule of thumb for sizing: **450 MB plus up to about 90 bytes per pixel**. TrustMark upsamples the
watermark residual to full resolution as `float32`, then mixes it with the cover image as `float64`, so
the cost is driven by the pixel count, not by the file size. A 3 MB JPEG and a 3 MB PNG of very
different dimensions cost very different amounts of memory.

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
4 workers x 30 MP x ~90 bytes/pixel  ~=  10.8 GB   inside a 4 GB container
```

And you no longer control what dies: the OOM killer fires on the **cgroup**, which is the whole
container, and the kernel picks the victim. Usually that is the worker that overran, and uvicorn
restarts it - but it can be another worker, or the master, in which case the container goes down and
every request in it with it.

**Why replicas.** Each container is its own cgroup with its own `mem_limit`, so the budget is *per
replica*. `MAX_CONCURRENT=1` keeps meaning what you sized it to mean: one image in flight, one memory
ceiling. An OOM kills one replica, the others keep serving, and `restart: unless-stopped` brings it
back - one failed request, and a failure domain you can actually reason about.

```yml
  image-fingerprint:
    build:
      context: ./image-fingerprint
    image: image-fingerprint
    # No "container_name:" - Compose refuses to scale a service that declares one.
    # "hostname:" is allowed but pointless here: every replica would get the same
    # host name, and it is the service name that other containers resolve.
    restart: unless-stopped
    expose:
      # No published host port either: three replicas cannot share one host port.
      - 8080
    mem_limit: 4g
    cpus: 2
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    # tmpfs, not the named volume of README.md: replicas must not share one /tmp.
    # A named volume is mounted into *every* replica, which would hand them a common
    # scratch and cache directory (HOME is /tmp) and quietly make the service stateful.
    tmpfs:
      - /tmp
    scale: 3
```

`scale` is a plain service attribute and is the simplest form. Two equivalents exist:
`deploy: { replicas: 3 }` (same effect; the `deploy` section carries some historical "Swarm only"
baggage, and when both are set they must agree), and `docker compose up -d --scale
image-fingerprint=3`, which changes nothing in the file. There is also `docker compose scale
image-fingerprint=3` for a running stack.

You get three ordinary containers, named `<project>-<service>-<index>`, so `docker ps` will show
`myproject-image-fingerprint-1`, `-2` and `-3`.

**No load balancer is needed.** Docker's embedded DNS resolves the service name to all replica
addresses and shuffles them, so successive lookups land on different containers. Every Nuxeo conversion
is a fresh, short-lived `curl` process doing a fresh DNS lookup, so the load spreads by itself. Verified
with two replicas: consecutive calls to `http://image-fingerprint:8080/health` alternated between
`172.20.0.2` and `172.20.0.3`.

Two caveats. The shuffle is **not load-aware** - there is no least-connections logic, so a request can
land on a busy replica while another sits idle. And it is a convenience, not a contract: the Compose
specification says of a name shared by several containers that *"exactly which container the name
resolves to is not guaranteed"*. This also works *because* `curl` is short-lived; a client with a
persistent connection pool would pin itself to one replica and need a real balancer.

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

## Deploying as a shared service

Everything above assumes the sidecar layout of `README.md`: the service runs in the same Compose
project as Nuxeo. That is the right shape for a demo, a proof of concept and a single-node install.

For a **cluster of several Nuxeo nodes** it is usually the wrong shape, and the reason is not the idle
cost - 450 MB per node is noise on a Nuxeo box. It is the **headroom you have to plan for**, counted
here at the measured 30 MP peak of ~2.7 GB per instance:

| Layout, 4 Nuxeo nodes | RAM to plan for | Actually used |
|---|---|---|
| A sidecar on every node | 4 x 2.7 GB = **~11 GB**, spread one lump per node | rarely, and a burst on one node cannot borrow another node's lump |
| Shared, 2 instances (HA) | **5.4 GB**, pooled | the same work |
| Shared, 1 instance | **2.7 GB** | the same work |

With sidecars you budget peak capacity on *every* node for something that runs occasionally, and an
idle container on node 1 cannot absorb a burst on node 2. Deployed on its own, the capacity is pooled.
This is the same reasoning that puts MongoDB and OpenSearch on their own hosts.

The counterweight: a sidecar costs no extra host, while a shared service needs somewhere to run. If
the Nuxeo nodes have the memory to spare anyway, sidecars are cheaper and simpler - the comparison
above only becomes an argument when that headroom is what forces you to a bigger instance type.

It also makes a temporary scale-up practical: for a one-off campaign over thousands of images, scale
the shared service up and back down again. Doing that to a sidecar on every Nuxeo node is far more
work.

The service is **stateless** - no database, no shared volume, and the model weights are baked into the
image - so there is nothing to coordinate. Point several Nuxeo nodes at one endpoint and it works.

### Making the service URL configurable

`README.md` hardcodes `http://image-fingerprint:8080` in the command lines, which keeps the quick
start readable. For any other topology, replace the URL with a property:

```xml
<parameterString>-sS --fail-with-body --max-time 80 -H "Expect:" -o #{outDirPath}/nxblob-fingerprinted.jpg --data-binary @#{sourceFilePath} "${nuxeo.fingerprint.baseurl:=http://image-fingerprint:8080}/embed?format=jpeg&amp;payload=#{payload}"</parameterString>
```

Then in `nuxeo.conf`:

```
nuxeo.fingerprint.baseurl=https://fingerprint.internal.example.com
```

Keep the **scheme inside the property**, not just host and port: moving to HTTPS then becomes a
configuration change rather than an XML edit.

This version is **backward compatible**. The `:=` default is the Compose service name, so with no
`nuxeo.conf` entry at all it behaves exactly like the hardcoded one - you can adopt it from the start
and decide on the topology later.

Why this works, since it is not obvious: Nuxeo reads a contribution file **as a whole string and
expands `${...}` before parsing the XML** (`DefaultRuntimeContext.createRegistrationInfo` calls
`Framework.expandVars` on the source). The substitution therefore happens before `ShellExecutor`
tokenises the parameter string, so it cannot interfere with the quoting rules. The same
`${property:=default}` form is used by the platform's own contributions.

### Security

The trust boundary changes. As a sidecar, only containers in the same Compose project could reach the
service. Off-box, anything that can route to the port can use it - and the service still has **no
authentication**.

- Restrict the security group to **the Nuxeo instances' security group** on port 8080. That is the
  control that replaces the Docker network boundary, and it is the one that matters.
- Never give it a public address or a public load balancer. It belongs on a private subnet, reached
  the same way Nuxeo reaches MongoDB or OpenSearch.
- Keep everything the container already does: non-root, read-only filesystem, dropped capabilities,
  and the upload, pixel and concurrency limits.

Remember what `/decode` gives away: anyone who can reach it can test whether an image carries a
fingerprint and read the token out of it. For a forensic watermark that is the whole secret.

### The payload travels in the query string

`?payload=K7Q2M9XA` is convenient and needs no escaping, and as a sidecar nothing sat between Nuxeo
and the service. Once there is a load balancer or a reverse proxy in the path, that changes: **the
full request line, query string included, lands in its access log.**

The service itself never logs the payload - that is deliberate, the token-to-recipient mapping belongs
in Nuxeo. Intermediaries have no such scruples.

So when a load balancer is involved, either turn off or sanitise its access logs, or accept that the
tokens are recorded there. This is the main argument for TLS on this hop, more than eavesdropping is.

### Does it need HTTPS?

Not strictly - by the same standard that Nuxeo talks to MongoDB and OpenSearch in plain text inside a
private network, which is what most deployments do.

- **No load balancer, tight security group, same availability zone**: plain HTTP is defensible, and is
  the same posture as the MongoDB link.
- **Anything in between**: use TLS, or deal with the access-log point above.
- **A compliance context**: use TLS. "The hypervisor encrypts traffic between instances" may well be
  true of your instance types, but it is an infrastructure property an auditor cannot see, whereas TLS
  is visible and provable.

TLS terminates at the load balancer or at a reverse proxy in front of the service. The service itself
speaks plain HTTP and has no TLS configuration - keep it that way.

### One more link in the timeout chain

A load balancer adds its own timeout, and the defaults collide with ours. **An AWS ALB idles out at
60 seconds, and `CONVERT_TIMEOUT_S` is also 60.** Between the client finishing its upload and the
service answering, no data flows, which an ALB counts as idle. It can therefore return *its* 504
before the service's plain-text 504 arrives - destroying exactly the error reporting the timeout chain
exists to preserve.

The chain itself is unchanged, and the load balancer is not a step in it - Nuxeo's `<timeout>` kills
`curl`, it says nothing about the hop in between:

```
CONVERT_TIMEOUT_S (60)  <  curl --max-time (80)  <  <timeout> (90s)  <  transaction timeout (300s)
```

The load balancer is a **separate constraint on that hop**: its idle timeout must be longer than
`curl --max-time`, so that `curl` is always the one that decides to give up and the service's own
error is what comes back. With the values above, anything from 120 s up works. A network load
balancer sidesteps the HTTP idle timeout, but still applies a TCP one - 350 s by default on AWS,
comfortably clear of the chain.

### Getting the image onto the host

The repository ships source; each user builds their own image. For a standalone deployment either
build on the target host, or build once and push to a private registry such as ECR.

Pushing to a private registry is **unencumbered here**: the whole Python and ML layer is permissively
licensed (MIT / BSD / Apache-2.0 / PSF), with no copyleft component and so no source-offer obligation
attached to any of it. The one thing you do carry is the ordinary Debian one - the `python:3.12-slim`
base ships GPL system tools like every Debian-derived image, so a redistributor keeps the usual
distributor obligations. See `image-fingerprint/THIRD-PARTY-NOTICES.md`.

Health checks move from Docker's `HEALTHCHECK` to whatever the platform uses. `GET /health` is already
suitable for a load balancer health check: it returns 200 with
`{"status":"ok","trustmark":"0.9.2","model":"Q"}` only once the model is loaded and the service is
ready to work.

> [!WARNING]
> **Do not put this on a scale-to-zero platform** for synchronous calls. The image is about 1.12 GB
> and the model takes roughly 3 seconds to load, so a cold start is far too slow for an operation a
> user is waiting on. Keep at least one instance warm.

### Which layout to choose

| Situation | Layout |
|---|---|
| Demo, proof of concept, single Nuxeo node | **Sidecar**, exactly as `README.md` describes. No extra operational surface |
| Cluster of 3-4 nodes, occasional fingerprinting | **Shared, 2 instances** behind an internal load balancer. Budgets 5.4 GB once instead of ~11 GB spread thin, and survives losing one |
| Cluster, shared, single instance | Works, and is cheap, but it is a cluster-wide single point of failure. How badly that bites depends on *how* you call it - see the note below |
| One-off campaign over thousands of images | Shared, scaled up temporarily, then back down. Pair it with the Bulk Action Framework, see [Calling it asynchronously](#calling-it-asynchronously) |

**How a failure shows up depends on the combination.** The topology decides *how much* breaks; the
invocation style decides whether you ever find out.

| How you call it | A fingerprinting outage looks like |
|---|---|
| Synchronously (`Blob.RunConverter`, the default) | The operation fails immediately, with the service's plain-text error. Loud, and in front of whoever triggered it |
| Async post-commit handler | **Silent.** The user's action succeeded, the three attempts are spent in 48 ms, and nothing reaches the caller |
| Bulk Action Framework | Visible as `errorCount` in the command status, and with `failOnError=true` it retries with backoff and usually recovers |

The one combination to avoid is **a single shared instance called from an async post-commit
handler**: the whole cluster depends on one container, the retries are spent in milliseconds, and
nobody is told. Running two instances, using the Bulk Action Framework, or keeping the call
synchronous - any one of the three removes the trap.


## Calling it asynchronously

By default `Blob.RunConverter` runs **synchronously, inside the caller's transaction**. If the
service is down, the operation that triggered it fails. If several requests arrive together,
`MAX_CONCURRENT=1` serialises them and the callers wait.

The obvious reaction is "put it behind a Nuxeo work queue and get retries for free". That is only
half true, and the half that is false matters. **Everything below was tested against a real Nuxeo
LTS 2025 instance**, not just read in the sources.

### First: should it be asynchronous at all?

| Your flow | Recommendation |
|---|---|
| Fingerprint on publish, the user waits, the file must be fingerprinted before it is delivered | **Stay synchronous.** An embed takes well under a second, and a failure is reported to the caller instead of disappearing. Asynchronous processing only adds eventual-consistency complexity to something the user is waiting for |
| Fingerprint on publish, but delivery is a separate, later step | **Asynchronous is a clear win.** Publishing stops depending on the service being up - but see what a failure then looks like, because nobody is told |
| "Send these 500 images to a partner, each with its own token" | **Bulk Action Framework**, see below |

Fingerprinting is normally applied only to the renditions you publish or send, so the volumes are
small. Reliability, not throughput, is what you are buying here.

Whichever option you pick, remember that `MAX_CONCURRENT=1` makes concurrent callers queue, that the
queue wait does **not** count against `CONVERT_TIMEOUT_S` but does count against `curl --max-time`,
and that the answer to a persistent queue is replicas rather than longer timeouts. The numbers are in
[Scaling out](#scaling-out-replicas-not-workers-or-threads).

### Option A: an asynchronous post-commit automation handler

An automation chain bound to an event with `postCommit="true"` runs in a `ListenerWork`, off the
user's transaction.

```xml
<extension target="org.nuxeo.ecm.core.operation.OperationServiceComponent" point="event-handlers">
  <handler chainId="myFingerprintChain" postCommit="true">
    <event>documentCreated</event>
    <filters>
      <doctype>File</doctype>
      <pathStartsWith>/default-domain/workspaces/somewhere</pathStartsWith>
    </filters>
  </handler>
</extension>
```

**Confirmed by testing:** the REST call that created the document returned in 0.13 s and the chain
ran afterwards on a `Nuxeo-Work-common-2` thread. The caller is genuinely not blocked.

**Confirmed by testing, and this is the catch:** with the service stopped, the work was attempted
exactly **three times** - the initial run plus the two retries of `ListenerWork`'s default
`retryCount` - and all three happened **within 48 milliseconds**:

```
23:45:47,733   attempt 1
23:45:47,763   attempt 2
23:45:47,781   attempt 3
```

There is **no backoff between retries**. A container restart takes seconds, so by the time the
service is back, all three attempts have been spent. These retries protect against an instantaneous
blip and nothing else.

> [!WARNING]
> Do not rely on `ListenerWork` retries to ride out a restart of the fingerprinting service. They
> are exhausted in milliseconds.

Raising the count does not help, and it is not raised where you would expect. `retryCount` is **not
an attribute of `<handler>`** - `EventHandler` does not declare it, so XMap drops it silently and you
get no warning. It belongs to the *listener*, and every automation post-commit handler in the
instance shares one, `opchainpclistener`:

```xml
<extension target="org.nuxeo.ecm.core.event.EventServiceComponent" point="listener">
  <!-- Instance-wide: this is THE listener behind every postCommit automation handler -->
  <listener name="opchainpclistener" retryCount="5"/>
</extension>
```

Two reasons not to bother: the extra attempts still have no delay between them, and because that one
listener dispatches to every matching handler, a retry re-runs **all** of them on the same event, not
just the chain that failed.

### Option B: the Bulk Action Framework, with `failOnError` turned on

`Bulk.RunAction` with the built-in `automation` action runs a chain over an NXQL selection,
asynchronously, with a real retry policy. **But the stock contribution does not retry**, because
`failOnError` defaults to `false`: a failing operation is recorded as an error and swallowed, and the
`maxRetries` policy never engages.

**Confirmed by testing:** with the stock configuration and the service down, the command finished
immediately as `COMPLETED processed=1 errorCount=1`, and the service was never contacted.

`failOnError` is a **stream processor option**, not a parameter of the `Bulk.RunAction` call, so it
has to be contributed. Redeclare the stock processor and add the option, keeping everything else as
the platform has it - in particular the two `${...:=N}` defaults, which are what makes concurrency
and partitions tunable from `nuxeo.conf`:

```xml
<component name="com.example.fingerprint.bulk">

  <require>org.nuxeo.ecm.core.automation.features.bulk</require>

  <extension target="org.nuxeo.runtime.stream.service" point="streamProcessor">
    <streamProcessor name="automation"
      class="org.nuxeo.ecm.automation.core.operations.services.bulk.AutomationBulkAction"
      defaultConcurrency="${nuxeo.bulk.action.automation.defaultConcurrency:=2}"
      defaultPartitions="${nuxeo.bulk.action.automation.defaultPartitions:=4}">
      <policy name="default" maxRetries="3" delay="1s" continueOnFailure="true" />
      <option name="failOnError">true</option>
    </streamProcessor>
  </extension>

</component>
```

The `<policy>` is the stock one, copied as is: `delay="1s"` with the default `maxDelay` of 10s gives
an exponential backoff of roughly 1s, 2s, 4s over the three retries.

> [!NOTE]
> This overrides the platform's shared `automation` processor, so it changes the behaviour of
> **every** `Bulk.RunAction` using that action, not just fingerprinting. If that is too broad,
> contribute your own action and processor instead.

> [!IMPORTANT]
> `failOnError=true` makes the failure roll the **transaction** back and rethrow, so the retry is of
> the whole batch, not of the one document that failed. Documents already processed in that batch are
> processed again - which is fine here only because an embed is idempotent (see below). Check that
> before turning this on for a chain that does something else.

**Confirmed by testing:** with that contribution, the command was submitted while the service was
down, the service was started 2 seconds later, and the command **recovered**:

```
command submitted          service DOWN
+2s   service started
+1..6s  state=RUNNING  processed=0   <- retrying with backoff
+7s   state=COMPLETED processed=1 errorCount=0
```

The service log confirms it received the embed once it was back. **This is the only configuration
tested that survived the fingerprinting service being unavailable.**

Launch it with:

```
POST /nuxeo/site/automation/Bulk.RunAction
{"params":{"query":"SELECT * FROM Document WHERE ...",
           "action":"automation",
           "parameters":"{\"operationId\":\"myFingerprintChain\"}"}}
```

and poll `GET /nuxeo/api/v1/bulk/{commandId}` for `state`, `processed` and `errorCount`.

### Don't draw conclusions from a development stack

Whether in-flight work survives a Nuxeo restart is a property of the stream backend, not of this
service. **A production Nuxeo runs on Kafka** - it is the supported queueing backend and the platform
documentation requires it. With Kafka there is nothing to think about here: a bulk command caught by
a restart resumes.

A development stack usually has `kafka.enabled=false` and falls back to in-memory streams, which
Nuxeo says plainly at every startup:

```
In-Memory type for log config "default" is ONLY for testing purpose. Use Kafka for production.
```

**Confirmed by testing**, and worth knowing because of *how* it fails: a bulk command was submitted,
Nuxeo was restarted while it was in flight, and the fingerprinting service was brought back up. Sixty
seconds later the command was still reported as:

```
state=RUNNING  processed=0  errorCount=0
```

It never resumed, and the service never received a request. The queued work died with the JVM, but
the command's *status* is stored separately and durably, and only terminal states expire - so it
stays `RUNNING` forever. The failure is not even visible as a failure, and anything polling that
command waits indefinitely.

So: test the retry behaviour of [Option B](#option-b-the-bulk-action-framework-with-failonerror-turned-on)
on a dev stack by all means, but don't conclude anything about resilience from it, and don't ship
without Kafka.

### Idempotency, and why retries are safe here

Retrying an embed is harmless: TrustMark's encoder is deterministic, so the same image with the same
payload and strength produces the same output. Re-running costs CPU, not correctness.

Nuxeo's conversion cache helps too, keyed on
`converterName : blobDigest [ : paramName : paramValue ]*`.

**Confirmed by testing, accidentally:** a document created with an image that had already been
fingerprinted succeeded **while the service was stopped**, because the cached result was returned and
`curl` never ran.

But do not mistake that for an idempotency guarantee. The cache is a node-local in-memory index over
a local directory, it defaults to **10 MiB**, and it is **cleared on restart**. For "fingerprint this
exactly once", keep a flag on the document.

### What was not tested

Verified by reading the LTS 2025 sources, but not exercised:

- The dead letter queue, and replaying it with the `WorkManager.RunWorkInFailure` operation. A failed
  work should be appended to the `work/dlq` stream.
- `retryCount="5"` on `opchainpclistener`. The attribute and its effect are established from the
  sources (`EventListenerDescriptor`, `AsyncEventExecutor.ListenerWork`), but the contribution above
  was not run - and since it adds attempts without adding delay, it would not change the conclusion.
- Saving the fingerprinted blob back onto the document. The test chain deliberately only ran the
  converter and logged the result, which avoids the re-entrancy question entirely. **If you do save
  the result, your own save will re-fire `documentModified` and re-trigger the handler** - use a
  context-data guard and a disable flag, the way `PictureViewsHelper` does in the platform.

### A gotcha worth knowing

Contributions dropped into `nxserver/config/` are only picked up if the file name ends in
`-config.xml` or `-bundle.xml`:

```java
// OSGiRuntimeService.java
if (name.endsWith("-config.xml") || name.endsWith("-bundle.xml")) {
```

A file named `something-contrib.xml` there is **silently ignored** - no error, no warning, the
contribution simply never exists. This cost time during testing.
