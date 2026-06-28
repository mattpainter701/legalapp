# CourtListener Jetson Pipeline

This script-level note is now a compatibility pointer. The active CourtListener
MCP + Jetson runbook is `docs/courtlistener_mcp_jetson.md`.

Current behavior:

- `scripts/jetson_embed_worker.py` delegates to `mcp-server/mcp_server/jetson_worker.py`.
- The worker embeds `opinion_chunks`, not `public_chunks`.
- The model is `mixedbread-ai/mxbai-embed-large-v1`.
- Embeddings are 1024-dimensional.
- Default Jetson batch size is 32.

Launch workers with:

```bash
JETSON_HOSTS="jetson-a.local jetson-b.local jetson-c.local" \
VECTORDB_URL="postgresql://courtlistener:<password>@<skynet-ip>:5432/courtlistener" \
bash scripts/trigger_jetson_workers.sh
```
