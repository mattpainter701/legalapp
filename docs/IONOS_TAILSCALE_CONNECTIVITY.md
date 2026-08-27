# IONOS to Skynet private research path

The IONOS application core reaches the Skynet research sidecar through one
source-restricted Tailscale listener. This path is independent of the desktop
or Jetson embedding workers and their PostgreSQL SSH tunnels.

## Boundary

- Skynet keeps Docker bindings on `127.0.0.1:8021` for the MCP sidecar and
  `127.0.0.1:5434` for the research database.
- A systemd socket binds only the Skynet Tailscale IPv4 address on port 8021 and
  proxies to `127.0.0.1:8021`.
- An isolated nftables table drops port 8021 traffic unless it arrives on
  `tailscale0` from the exact IONOS Tailscale IPv4 address.
- `MCP_UPSTREAM_API_KEY` remains mandatory. Network identity does not replace
  application-layer authentication.
- Port 5434 is never published to IONOS. Embedding workers retain their existing
  SSH-forward workflow.

## Install on Skynet

Run from the repository checkout using the current node addresses:

```bash
sudo scripts/install_research_tailnet_proxy.sh \
  <skynet-tailscale-ip> <ionos-tailscale-ip>
```

The installer validates both addresses are in Tailscale's `100.64.0.0/10`
range, verifies the localhost MCP health endpoint, installs the systemd units,
loads the source-restricted firewall, and enables the socket.

## Acceptance

From IONOS:

```bash
tailscale ping <skynet-tailscale-ip>
curl -fsS http://<skynet-tailscale-ip>:8021/health
```

Set `MCP_SERVER_URL=http://<skynet-tailscale-ip>:8021` in the IONOS host env,
then run the IONOS stage check. It probes the authenticated `/api/mcp` manifest
inside the backend container without printing the upstream key.

From any different tailnet node, a TCP connection to
`<skynet-tailscale-ip>:8021` must fail. Also verify Skynet's public and LAN
addresses have no port 8021 listener.

Do not use a default Tailscale Serve listener without a node-scoped grant. It
can make the sidecar reachable by every peer already allowed by the tailnet
policy.

## Rollback

On Skynet:

```bash
sudo systemctl disable --now \
  lawhand-research-tailnet-proxy@<skynet-tailscale-ip>.socket
sudo systemctl disable --now law-hand-research-tailnet-firewall.service
```

Rollback stops only the Tailscale proxy and its dedicated nftables table. The
localhost MCP sidecar, research database, authority sync, and embedding workers
remain running.
