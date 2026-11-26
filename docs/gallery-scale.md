# Gallery scale on the operator host

This run exercised the actual SQLite gallery API, compiled operator UI and exact production matcher on isolated synthetic directories. The live gallery stayed separate. Both sizes returned 24 people per first page. Chrome could open a person, follow a versioned link and return to the same scrolled list on a desktop and a narrow RTL viewport.

## Fixture and environment

The fixed seed 34117 generates pseudo-person IDs, 32 abstract JPEG tiles, deterministic 128-dimensional L2-normalized float32 vectors, accepted and rejected references, and two immutable membership snapshots. Every seventeenth person has only rejected references. Some people have two or three references. The first snapshot covers eligible references from the first half of the IDs, and the second covers all eligible references. Shared physical tiles reduce disk working set and image entropy. These synthetic vectors support timing and numerical parity checks. Recognition accuracy requires independent labeled enrollments and probes.

| Measurement | 100,000 people | 300,000 people |
|---|---|---|
| Draft references | 129,412 | 388,236 |
| Accepted / rejected references | 103,530 / 25,882 | 310,589 / 77,647 |
| Snapshot 1 / 2 reference counts | 51,765 / 103,530 | 155,295 / 310,589 |
| SQLite before / after browse projection (MiB) | 114.7 / 226.1 | 344.0 / 678.3 |

The server bound to loopback and ran the repository's Waitress application. Chrome used the same host and HTTP without network shaping. The tests used CPU affinity on four of the host's 16 logical CPUs and a single BLAS thread for vector timing. The existing feed and recognition worker continued running. The fixture variant served read-only gallery pages. Browser timings include JavaScript execution, layout, an actual first decoded thumbnail and two animation frames. All latency pairs below are nearest-rank p50 / p95 in milliseconds.

## Prior behavior

The baseline is commit `e936d4f8356baae3405a85e8e1a9d4aa2b3efccc`. Its gallery endpoint serialized every reference. The React grid filtered the full result for every person to calculate each reference count.

| Measured baseline | 100,000 | 300,000 |
|---|---|---|
| Entire gallery JSON (bytes) | 57,640,812 | 172,921,022 |
| Loopback response p50 / p95, n=4 | 2,217 / 2,423 | 8,118 / 8,532 |
| Chrome first populated tile at 30 s, n=1 | Timed out | Timed out |
| Portrait requests by deadline | 0 | 0 |
| Browser proportional memory by deadline (MiB) | 477 | 761 |

Chrome received each entire JSON response before the deadline. Both baseline browser samples were censored at 30 seconds, so there is no measured p50 or p95 for a completed baseline render.

## Paged gallery

Browser-cold samples use fresh contexts with the HTTP cache disabled on each journey. Browser-warm samples reuse one primed context with the normal HTTP cache. Static resources, thumbnails and database pages can be warm on the host. The test leaves the OS cache intact. A process-cold API sample is the first identity request to a newly started Waitress process after an HTTP health check, with completed database migrations. Warm API samples use the existing server.

| API or browser action, p50 / p95 (ms) | 100,000 | 300,000 |
|---|---|---|
| First list API, process cold (6) | 12.9 / 35.1 | 5.4 / 28.9 |
| First list API, warm (20) | 2.6 / 7.0 | 3.7 / 11.7 |
| Selective exact-ID API, warm (20) | 5.1 / 20.2 | 3.4 / 13.1 |
| Selective name-prefix API, warm (20) | 3.1 / 17.7 | 4.3 / 7.7 |
| Person detail API, warm (20) | 3.3 / 7.2 | 5.2 / 9.7 |
| First tile, desktop cold (8) | 406 / 565 | 353 / 395 |
| First tile, desktop warm (16) | 203 / 266 | 203 / 220 |
| First tile, mobile cold (3) | 284 / 392 | 358 / 362 |
| First tile, mobile warm (3) | 222 / 222 | 193 / 196 |
| Next page, desktop warm (16) | 170 / 226 | 168 / 256 |
| Third → second page, desktop warm (16) | 127 / 179 | 128 / 329 |
| Search after typing, desktop warm (16) | 352 / 378 | 347 / 363 |
| Rejected filter, desktop warm (16) | 111 / 163 | 124 / 177 |
| Open person, desktop warm (16) | 113 / 130 | 113 / 129 |
| Return to scroll position, desktop warm (16) | 66.6 / 100 | 65.2 / 94.6 |
| Open versioned deep link, desktop warm (16) | 213 / 262 | 225 / 261 |

The search timing starts with typing and includes the UI debounce. Navigation actions wait for the expected person, page or selection to paint. The known high-numbered enrolled person was absent from the older snapshot and available in version 2. Each trial also checked a two-page forward/back traversal, 12-step scroll, selected-person browser history, RTL direction, and horizontal viewport overflow. Desktop used 1365 × 900 CSS pixels and mobile used 390 × 844 in headless Chrome. These are same-host browser timings. Latency on a physical phone depends on its memory, browser and processor.

| Observed first screen, desktop median unless indicated | 100,000 | 300,000 |
|---|---|---|
| People list JSON (bytes) | 4,390 | 4,390 |
| People + versions body bytes, cold | 5,077 | 5,080 |
| All wire bytes through first tile, cold / warm | 404,862 / 7,125 | 404,864 / 7,128 |
| Requests / thumbnail requests, cold | 30 / 24 | 30 / 24 |
| Person cards / DOM elements, cold | 24 / 153 | 24 / 153 |
| DOM elements with person detail open | 158 | 158 |
| JS heap first screen / detail (MiB) | 3.5 / 4.9 | 3.5 / 4.9 |
| Browser process-tree PSS first screen (MiB) | 353 | 353 |
| Chrome scroll animation frame interval p95 (ms), median of 16 warm trials | 16.7 | 16.7 |
| Restored scroll offset in CSS pixels, desktop / mobile | 690 / 2747 | 690 / 2747 |

Chrome network events counted encoded transfer bytes through the paint check. Image requests may still be inflight at that instant. Browser memory sums proportional set size across Chrome processes, and JS heap is a point-in-time V8 measure. Chrome may prefetch nearby lazy images within its viewport margin. The grid requested at most its current page of 24 portraits.

## SQLite work and exact matching

The browse path uses indexed person and name prefixes, bounded thumbnail pages, a scoped continuation cursor and indexed person detail. The harness captured executed statements from the Flask endpoints, their actual query plans, and the approximate SQLite VM instruction counts in 100-instruction increments. At both sizes the first 24 cards used about 600 instructions, an actual second cursor page about 700, a selective ID about 200 and a name prefix about 700. A request with a seeded unfiltered near-tail predecessor also took about 700 at both sizes, after measuring 1.30 million and 3.90 million with the earlier range bound. The actual prefix cursor, invalid cross-query cursor, excessive page limit, status filter, immutable membership and absent result all passed. Backfilling the projection on the preexisting 300k fixture took 17.53 s once.

The production CPU worker caches one contiguous matrix per immutable gallery version. For each query it scans the full float32 matrix, selects the maximum and recomputes that candidate's scalar cosine. The following measures the matching call independently of I/O, alignment, face feature extraction, web browsing and the live feed's downstream queues.

| Exact lookup, one BLAS thread | 100,000 | 300,000 |
|---|---|---|
| Active reference vectors | 103,530 | 310,589 |
| Prior scalar p50 / p95 (ms), 10 queries | 715 / 749 | 2,180 / 2,397 |
| Production cached matrix p50 / p95 (ms), 20 queries | 3.9 / 6.0 | 12.5 / 14.0 |
| Load one version from SQLite (ms) | 371 | 1,333 |
| Same-version cache check (µs) | 6.4 | 6.7 |
| Matrix / retained RSS increase (MiB) | 50.6 / 57.6 | 151.7 / 173.5 |

All 20 self queries at each scale returned the expected reference. Four independent noisy-member and nonmember probes at each scale matched the prior scalar top-1 ID, score and decision. This isolated benchmark measures random vectors and a single snapshot. A populated human gallery needs a separately labeled quality evaluation and a sustained feed-capacity test.

## Reproduce

Install the repository's operator and browser test dependencies and build the operator UI. The fixture and artifacts live under the ignored `private/gallery-scale` directory. The benchmark server sets `GALLERY_FIXTURE=1`, binds to loopback and reads the size-specific fixture.

```sh
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
export CHROME_BIN=/path/to/chrome
(cd apps/operator/frontend && npm ci && npm run build)
python=./.venv/bin/python
size=300000
"$python" tools/gallery_scale.py fixture --size "$size"
"$python" tools/gallery_scale.py serve --size "$size" --port 19818
```

Use a second terminal for the measurements. Repeat with size 100000 and the matching fixture server.

```sh
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CHROME_BIN=/path/to/chrome
python=./.venv/bin/python
size=300000
"$python" tools/gallery_scale.py plans --size "$size"
"$python" tools/gallery_scale.py api --size "$size" --port 19818 --repetitions 20
"$python" tools/gallery_scale.py cold-api --size "$size" --repetitions 6
"$python" tools/gallery_scale.py browser --size "$size" --port 19818 --repetitions 16
"$python" tools/gallery_scale.py browser --size "$size" --port 19818 --repetitions 3 --mobile
"$python" tools/gallery_scale.py worker-vectors --size "$size" --repetitions 20
```

To reproduce the historical path, export the baseline commit into an isolated private directory, build its `operator/dist`, and pass `--code /absolute/path/to/baseline` to the fixture server and `vectors` action. Pass `--mode baseline` to `api` and `browser` and point them at that server. Private outputs include complete browser trials, response bytes, approximate VM counts and plans, vector checks, and fixture counts in each size directory.
