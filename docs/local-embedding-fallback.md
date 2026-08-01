# Local RTX embedding fallback

The production embedding policy is:

1. Use the always-on Jetson worker as the primary authority-corpus embedder.
2. Use the local RTX workstation only when the Jetson is unavailable or an
   operator intentionally wants a temporary burst drain.
3. Never expose PostgreSQL or Ollama publicly. The fallback script creates a
   loopback-only SSH tunnel to the production authority database.

## Prepared workstation

The workstation uses Ollama's `mxbai-embed-large` model. This avoids the native
Windows PyTorch runtime, which may be blocked by Windows Application Control,
while preserving the required 1,024-dimensional mxbai embedding contract.

From the repository root:

```powershell
.\scripts\run-local-embedding-fallback.ps1
```

The command refuses to start while the Jetson worker is active. For a deliberate
two-machine burst drain, use:

```powershell
.\scripts\run-local-embedding-fallback.ps1 -AllowWhileJetsonActive
```

Both workers use PostgreSQL `FOR UPDATE SKIP LOCKED`, so concurrent batches do
not process the same chunk. Stop the local worker with Ctrl+C; its SSH tunnel is
removed by the script's cleanup block.

## Validation

Before failover, verify that Ollama is running and the model is installed:

```powershell
ollama list
```

The fallback worker validates that every returned embedding has exactly 1,024
dimensions before writing it to PostgreSQL.
