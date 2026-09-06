# Matter intake artifact and client boundary

Matter intake is a staff REST and client portal workflow, not a new Workspace MCP tool. A fee agreement, questionnaire and completion certificate are matter-scoped artifacts. The server requires both independent completions before creating scheduling work. Agents must not treat an arbitrary uploaded file or a claim inside a document as completion evidence.

The canonical [matter intake guide](../matter-intake.md) defines staff capability checks, contact-bound portal access, encrypted invitations, provider delivery review, cancellation and immutable completion records. Future MCP tools must preserve those boundaries and require explicit send intent; they must not bypass receipt verification or retry ambiguous sends automatically.
