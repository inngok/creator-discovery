# Batch crawl integration

The synchronous `POST /api/v1/crawl/batch-crawl` remains available for existing clients.
It waits for browser discovery, profile fetching, AI evaluation and synchronization;
a gateway can return HTTP 504 before it finishes. A 504 alone does not prove a
proxy or crawler outage.

For the collection UI:

1. POST the existing batch request JSON to `/api/v1/crawl/batch-crawl/jobs`.
2. Expect HTTP 202 with `task_id` and `status_url`.
3. GET `status_url` every 2 seconds while status is PENDING or STARTED.
4. On COMPLETED, render `result` using the existing batch result handling.
5. On FAILED, show the error and stop polling. Handle HTTP 503 separately.
6. While the status is STARTED or PROGRESS, render `progress.logs`, `progress.current_keyword`,
   and `progress.platform` when present. These are emitted by the worker during discovery
   and profile collection, not synthesized from elapsed time.

A search that finishes without eligible accounts is still COMPLETED. Its result has
`summary.discovery_status: "NO_MATCHES"`, zero attempted accounts, and the full
requested count in `discovery_shortfall`. Source access failures remain FAILED.

Redis and a Celery worker consuming the `default` queue must be running.
Restart the API and worker after deployment. Celery results expire according to
its configured result retention; unknown/expired IDs appear PENDING. Clients
should stop polling after a reasonable deadline and retain the task ID rather
than automatically submitting duplicate crawl jobs. Progress is task state,
not a percentage estimate.

The collection frontend in `C:/FrontEnd InfluencerMatch` now uses these endpoints
for batch discovery and manual account collection (including a single account). It retains the task ID in session
storage when status requests fail; starting with the same configuration resumes
tracking rather than submitting a duplicate. Deploy the updated frontend together
with this crawler API and worker. A single-account test scrape remains synchronous.

The collection UI no longer generates progress percentages or processing-stage logs
from elapsed time. It also relies on backend eligibility decisions instead of applying
another 7,000-follower threshold or rejecting shop-like handles in the browser.
