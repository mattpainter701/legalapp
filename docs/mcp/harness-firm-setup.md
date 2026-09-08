# Connect a firm's assistant to LawHand

Workspace MCP is a supported way to work with LawHand using the assistant account
your firm has approved. LawHand supplies access policy, reviewable work, and the
audit record. Your assistant supplies its own reasoning. You can continue using
LawHand Chat and metered features alongside it.

## Choose the service

| Work | Connection |
| --- | --- |
| Firm, client, intake, matter, task, document and workflow capabilities | `https://mcp.getlawhand.com/api/mcp/workspace` |
| Public legal authority and Research MCP capabilities | `https://research.getlawhand.com` |

The bare Workspace hostname `https://mcp.getlawhand.com` is also supported.
Research has its own entitlement, scopes, and usage accounting. Connecting either
service does not authorize access to the other. LawHand premium PDF preparation
and Standard/Premium research AI retain their existing metering when invoked.
Ordinary Workspace reads and deterministic proposals do not debit an AI balance
merely because they arrived through an external assistant.

## Administrator setup

1. Decide which assistant vendors and accounts the firm permits for confidential
   work. A personal subscription by itself is not a LawHand assurance of adequate
   confidentiality. Apply the firm's own retention and vendor requirements.
2. In **MCP Servers**, enable Platform MCP for the firm and enable the intended
   users in **Manage existing users**. Each user still needs an active license
   and the LawHand permissions for the requested work.
3. Have each user connect the Workspace URL and complete browser consent with
   their own LawHand account. Review the requested scopes before authorizing.
4. Inspect **Firm assistant activity** in MCP Servers. It identifies connection
   owners, successful and refused calls, proposal tasks, workflow runs, and exact
   artifact review evidence. Firm settings, matter, and document permissions are
   required to inspect this firm-wide view.

Privacy Mode blocks external workspace access and revokes existing grants. An
administrator cannot override it. After the user disables Privacy Mode, they
must connect and consent again. The user can also inspect and revoke individual
connections from Profile; revocation does not sign them out of LawHand.

## Client setup

For Codex, run `codex mcp add lawhandWorkspace --url
https://mcp.getlawhand.com/api/mcp/workspace`, then `codex mcp login
lawhandWorkspace`. For Claude Code, run `claude mcp add --transport http --scope
user lawhand https://mcp.getlawhand.com/api/mcp/workspace`, then authenticate in
`/mcp`. These commands use browser OAuth, not a static token.

For OpenCode, run `opencode mcp add`, select a remote server named `lawhand`, and
enter the Workspace URL. Run `opencode mcp auth lawhand`, complete browser
consent, then inspect `opencode mcp list`.

For desktop and browser clients, follow the
[client-specific connection guide](../workspace_mcp_adapter.md). Client versions
and account policies can affect connector availability. Setup references:
[OpenAI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli),
[Claude Code MCP](https://code.claude.com/docs/en/mcp), and
[OpenCode MCP](https://opencode.ai/docs/mcp-servers/).

## Working and reviewing

Ask the assistant to find the matter, inspect its authorized context, and propose
the work. For a sequence, ask it to propose a bounded workflow run. Open the
resulting task in LawHand to review. Runs pause for missing input or review and
retain their completed steps when continued. The assistant cannot approve legal
work, file, send, or deliver it directly.

The catalog shown in MCP Servers reflects the tools currently published for the
user's scopes and permissions. The broader channel goal does not imply that
every portal administration action is a published tool.

## Read budgets and activity evidence

Workspace applies a per-grant call limit and a maximum read response size. It
also atomically admits response bytes across a grant and across the entire firm
each minute, including durable MCP run steps. Defaults are 60 calls/minute per
grant, 1 MiB per response, 8 MiB/minute per grant, and 64 MiB/minute per firm.
These infrastructure budgets are separate from AI usage and billing. A token
refresh or an additional connection cannot reset the firm's byte budget.

When a limit is reached, narrow the request or wait for the indicated retry
window. Redis outages fail closed in production. Failed or oversized reads do
not return partial evidence. Proposal responses are not refused after a cloud
artifact has been created.

The activity view reads the existing tamper-evident audit ledger and review
records. It displays tool names, byte counts, outcomes, IDs, and review hashes;
it does not retain search phrases, prompts, or document bodies. Older audit
events without proposal IDs remain visible but cannot be retroactively linked
to an artifact. An artifact's current status and historical revision-specific
review decisions are shown separately. Revocation preserves this history.
