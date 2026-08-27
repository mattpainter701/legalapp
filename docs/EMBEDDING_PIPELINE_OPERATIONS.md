# Embedding pipeline operations

LawHand uses separate batch and online embedding paths. Neither path moves the
research database to IONOS.

## Data path

1. `courtlistener-db` on Skynet is the source of truth for authority text and
   1,024-dimensional mxbai vectors.
2. Batch workers claim unembedded rows with `FOR UPDATE SKIP LOCKED`, generate
   vectors, and write them back in one transaction per batch.
3. The desktop CUDA fallback reaches only Skynet's loopback database listener
   through `127.0.0.1:15435`, a supervised SSH local forward to
   `127.0.0.1:5434` on Skynet.
4. The small online query-embedding API produces a vector for each research
   request. The research MCP sidecar uses that vector to search PostgreSQL.
5. IONOS calls the authenticated research MCP sidecar over the restricted
   Tailscale port 8021. IONOS never connects to PostgreSQL, the desktop, or a
   Jetson directly.

LiteLLM handles generative-model routing. It is not the batch corpus embedder,
the vector database, or the mxbai query-embedding service.

## Desktop CUDA fallback

Keep a detached clean checkout of current `main` at
`F:\deepseek\legalapp\embedding-runtime`. Copy
`scripts/run-local-skynet-db-tunnel.ps1`,
`scripts/direct_cuda_embed_worker.py` and
`scripts/run-local-direct-cuda-embedding.ps1` together into the operator-only
runtime directory. Register the tunnel and worker supervisors as separate
at-logon scheduled tasks; the worker adopts the tunnel on `127.0.0.1:15435`.

The single-workstation default is `worker 0 / total 1`. Do not configure
`worker 1 / total 2` unless a separately verified worker 0 is running; the hash
partition would otherwise leave half of the corpus permanently unclaimed.

The database URL is held only in the supervisor process environment. It is
assembled from a credential retrieved over key-authenticated SSH and is not
written to the repository, task arguments, or logs.

## Jetson query embedding

The reviewed root systemd unit remains the preferred long-term service. On a
prepared user-owned runtime where root installation is not yet available,
`deploy/jetson/lawhand-query-embedding-supervisor.sh` provides a bounded restart
loop. Configure its private bind address in
`/data/legalapp-embeddings/query-embedding.env`, for example:

```text
QUERY_EMBEDDING_BIND=127.0.0.1
QUERY_EMBEDDING_PORT=8031
```

Keep this unauthenticated inference endpoint on Jetson loopback. Skynet reaches
it with the dedicated, restricted SSH key and exposes the forward only on the
LegalApp Docker bridge gateway at port 18031. The MCP sidecar uses
`http://172.24.0.1:18031/embed`; LAN, tailnet, and public listeners are not
required. Replace the user supervisor with the checked-in systemd unit when
administrator access is available, without broadening the bind.

If the Jetson still answers ICMP but stops emitting an SSH banner, disappears
from Tailscale, and stalls inference, treat it as host resource starvation rather
than a tunnel fault. Power-cycle the Jetson, inspect memory before the first
model request, and validate a lower-memory query runtime before restoring MCP
traffic. Do not restart the Skynet batch scheduler or change the database model
to recover this online inference service.

## Scheduler behavior

The Skynet scheduler counts pending work while holding a session advisory lock.
It commits that count transaction before waiting for a long-lived reverse SSH
worker session. This preserves single-dispatch coordination without retaining
an old PostgreSQL transaction snapshot for the duration of a multi-day batch.

When the desktop is the deliberate primary worker and no Jetson batch worker is
available, keep the scheduler container stopped. Re-enable it only after a
Jetson batch worker has passed SSH, model, database, and throughput acceptance.
