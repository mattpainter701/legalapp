---
slug: document-automation-and-esignature
title: Document automation & e-signature
description: Build controlled templates, generate reviewable drafts, and manage signature requests from the matter.
order: 90
read_time: 9 min
icon: sparkles
---

# Document automation & e-signature

[Document Automation](/templates) turns approved Word or PDF source files into reusable templates and generated drafts. Matter-level signature tools then track requests and the signature queue.

## Template library

The **Templates** tab lists available templates and their readiness. Search by title or description, narrow the library by status or category, and move through the paged results instead of loading the entire firm library at once. The health cards distinguish templates that are ready, still in draft, or missing a retained source file. A **Needs source** template must be recreated from the original document before it can generate anything.

Before activation, verify the name, practice context, source file, detected fields, variable schema, branding, warnings, and preview. Keep inactive templates out of production generation until a responsible reviewer approves them.

Use clear variable names and stable field meanings. A field such as `client_name` should not alternate between an individual, organization, and billing contact. For PDFs, confirm field placement and appearance on every affected page.

LawHand supports ordinary PDFs, AcroForm controls, scans/images converted to a safe PDF, and supported DOCX sources. It is a controlled template and field-placement workflow, not a general PDF authoring tool. Password-protected files, dynamic XFA forms, PDF scripts/actions, embedded attachments, and other active content are rejected. Export those files to a standard static PDF or DOCX before creating a template.

### Plaintiff and defendant field methodology

Caption variables come from contacts assigned the exact **Plaintiff** or **Defendant** role on the matter's **Parties** tab:

| Canonical variable | Meaning |
| --- | --- |
| `{{plaintiff_name}}` / `{{defendant_name}}` | The primary contact for that role, or the first listed contact when no primary is marked |
| `{{plaintiff_names}}` / `{{defendant_names}}` | Every contact for that role, with the primary first and remaining contacts in listed order, separated by semicolons |
| `{{plaintiff_email}}`, `{{plaintiff_phone}}` and address fields | Contact details for the singular plaintiff selected above |
| `{{defendant_email}}`, `{{defendant_phone}}` and address fields | Contact details for the singular defendant selected above |

The address suffixes are `street`, `city`, `state`, `zip`, and `country`. For example, use `{{defendant_city}}`. The shorter `{{plaintiff}}` and `{{defendant}}` aliases are accepted, but new templates should use the explicit `_name` variables.

`client_name` always means the matter's client contact; it does not mean plaintiff. `counterparty` is the matter's general counterparty summary and does not mean defendant. For an older matter with no structured caption parties, Smart Fill may infer a plaintiff/defendant pair only when **Represented Side / Our Role** explicitly identifies one side. Those inferred values have reduced confidence, require review, and should be replaced by structured Parties data.

## Generate and Smart Fill

The **Generate / Smart Fill** tab gathers values and prepares a draft. Select the correct template and matter, review proposed values, resolve missing required fields, and inspect the generated preview.

Before saving:

1. compare names, pronouns, entities, dates, amounts, and addresses with source records;
2. verify that conditional sections appeared correctly;
3. inspect page breaks, tables, signatures, headers, and footers;
4. remove placeholders and drafting notes; and
5. confirm the destination matter and filename.

Smart Fill and AI analysis accelerate assembly; they do not approve legal content.

## Version and activation discipline

When source language changes, create or update the controlled template through the supported workflow. Record what changed and re-test representative scenarios. Do not replace a template file in a way that makes prior generated documents impossible to explain.

## E-signature from a matter

Open [My Matters](/matters), choose the matter, and use its document/signature area to create a request. Select the final approved document, recipients, signing order if supported, message, and completion expectations.

Verify recipient email addresses and authority before sending. A signature queue can show pending and completed work, but the responsible professional must still confirm execution requirements, identity, attachments, and any notarization or witness rules.

## Failed or revised requests

If a document changes after sending, do not ask a recipient to sign a superseded version. Cancel or supersede the request through the approved process, preserve the history, generate the corrected document, and send a new request with a clear explanation.

Treat downloaded signed documents and audit evidence according to the matter's retention and filing policy.
