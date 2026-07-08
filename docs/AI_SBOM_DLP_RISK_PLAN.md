# AI, SBOM, DLP, and Customer Data Risk Plan

This plan addresses safety and compliance for customers who process personally identifiable information (PII), privileged client records, and other sensitive customer data through Clarity Legal's AI features and model providers. It is intended as an implementation roadmap for security, engineering, product, legal, and insurance review.

> **Not legal or insurance advice:** use this as an operating plan and issue checklist. Final coverage decisions, regulatory interpretations, and contract language should be reviewed by qualified counsel, a broker, and the company's security leadership.

## 1. Risk objectives

1. **Protect client confidentiality and privilege.** Legal workflows may include attorney-client privileged communications, litigation strategy, medical records, financial records, estate documents, domestic relations records, and minor-related information.
2. **Know every software and model component in the AI supply chain.** The SBOM must include application packages, containers, operating-system packages, AI gateways, model providers, embeddings, vector stores, and document parsers.
3. **Minimize data sent to models.** Only send the minimum context needed, redact or tokenize where possible, and preserve tenant-level controls for opt-in retrieval sources.
4. **Detect and stop data loss.** DLP controls should run before upload, before indexing, before prompt assembly, before outbound model calls, and before logs/telemetry are written.
5. **Create evidence for compliance and insurance.** Controls must generate auditable proof: SBOMs, vulnerability scans, DLP events, access logs, data-processing records, model-provider settings, incident drills, and retention/deletion records.

## 2. Data classification and handling model

|Class|Examples|Default handling|AI/model handling|
|-|-|-|-|
|Public|Court opinions, public statutes, marketing pages|Normal access controls|May be embedded and cached with provenance.|
|Internal|Product docs, runbooks, non-sensitive usage metrics|Employee access only|May be used in internal-only copilots.|
|Confidential customer data|Contracts, matter notes, client emails, invoices, templates|Tenant isolation, encryption, audit logging|Send only with tenant authorization and prompt minimization.|
|Regulated/high-risk PII|SSNs, tax IDs, passport/license numbers, bank data, medical records, children's data, domestic violence records|Strong DLP, restricted access, retention limits|Redact/tokenize by default; require explicit allow policy for model use.|
|Privileged/legal strategy|Attorney-client communications, work product, settlement strategy|Need-to-know, matter-scoped access, strict audit|Treat as highest sensitivity; block training use, prefer private/no-retention endpoints, log metadata only.|
|Secrets/credentials|OAuth tokens, API keys, passwords, private keys|Secret manager only|Never send to models; block and alert.|

## 3. SBOM and AI BOM program

### 3.1 Generate and store SBOMs

- Generate the repository tracking inventory with `py scripts/generate_sbom_inventory.py`; the generated outputs are `docs/SBOM_TRACKING_INVENTORY.md` for reviewers and `sbom/sbom-inventory.json` for automation.
- Generate formal SBOMs for every release artifact:
  - backend Python dependencies;
  - frontend npm dependencies;
  - agent package dependencies;
  - container OS packages and base images;
  - nginx and LiteLLM gateway images/configuration.
- Use CycloneDX or SPDX as the standard output format for formal release SBOMs.
- Attach SBOMs to CI build artifacts and release records.
- Sign release artifacts and SBOMs with Sigstore/cosign or equivalent.
- Store the SBOM digest, image digest, git SHA, build timestamp, and deployment environment in a release ledger.

### 3.2 Extend SBOM into an AI BOM

Traditional SBOMs do not fully capture model risk. Track an **AI BOM** alongside the SBOM with:

- model provider, model name, model version/alias, deployment region, retention/training setting, and contractual data terms;
- embedding model and vector dimension;
- model gateway configuration and routing rules;
- prompt templates, system prompts, tools/function schemas, and retrieval sources used by each AI feature;
- data categories each feature may send to the model;
- redaction/DLP policy applied before each model call;
- evaluation set version, jailbreak/prompt-injection test version, and known limitations;
- fallback behavior if a provider changes model aliases or retention settings.

### 3.3 Vulnerability, license, and supply-chain gates

Implement CI gates that fail releases when:

- HIGH/CRITICAL CVEs are present without an approved exception and expiry date;
- dependencies use licenses incompatible with commercial SaaS distribution;
- package lockfiles are missing or changed without review;
- container base images are unpinned by digest in production;
- model provider configuration lacks no-training/no-retention evidence for privileged workflows;
- SBOM or AI BOM generation fails.

Recommended tools: Syft or CycloneDX generators for SBOM, Grype/Trivy for CVEs, pip-audit/npm audit for package risk, OSV-Scanner for open-source vulnerabilities, and a license scanner such as ScanCode, FOSSA, Snyk, or Mend.

## 4. DLP control points

### 4.1 Inbound upload and ingestion

- Classify files at upload time using content inspection and metadata: file type, owner, matter, source connector, and sensitivity score.
- Detect and tag common sensitive patterns: SSNs, EINs, bank/routing numbers, credit cards, passport numbers, driver's license numbers, dates of birth, medical terms, protected classes, minors, and credentials.
- Detect secrets using entropy and known key patterns.
- Block or quarantine files with secrets by default.
- For regulated PII, allow indexing only under a tenant policy and matter-level authorization.
- Persist DLP tags separately from document text so downstream prompt assembly can enforce policy without re-scanning every time.

### 4.2 Prompt assembly and retrieval

- Re-scan context immediately before outbound model calls because sensitive data can be introduced through chat text, cloud search snippets, retrieved chunks, or memory.
- Apply least-context retrieval: limit top-k, cap characters per source, and exclude sources outside the user's matter/tenant permissions.
- Redact/tokenize regulated identifiers by default before model calls, while preserving reversible mappings only when required for the user's task.
- Strip secrets from prompts and tool outputs unconditionally.
- Add prompt-injection defenses for retrieved documents and cloud content: treat retrieved text as untrusted data, not instructions.
- Log only prompt metadata, model name, policy decision, token counts, redaction counts, and source IDs; avoid raw prompt/completion logging for privileged workflows.

### 4.3 Output and export

- Scan model outputs before they are displayed, emailed, saved to a matter, or exported.
- Warn or require confirmation when an output contains high-risk PII or privileged content and the destination is external.
- Add audit events for copy/export/download/email actions involving sensitive classes.

### 4.4 Logging, telemetry, and support access

- Prohibit raw customer document, prompt, completion, token, OAuth token, and secret values in logs.
- Use structured security events with hashes or IDs instead of raw content.
- Gate support impersonation/admin access behind approval, reason codes, just-in-time access, and immutable audit logs.
- Define retention windows for logs, DLP events, model metadata, and deleted matter data.

## 5. Workflow and governance changes

### 5.1 Secure development lifecycle

- Add threat modeling for every AI feature: data flow, model endpoint, retrieval source, DLP policy, abuse cases, and failure modes.
- Require security review for changes to prompt templates, model routing, document ingestion, cloud connectors, OAuth scopes, logging, and export/email workflows.
- Maintain a model/provider change-management process: approval, privacy review, test results, rollout plan, customer notice if terms materially change.
- Add dependency update SLAs: critical vulnerabilities within 48 hours, high within 7 days, medium within 30 days, unless exception approved.
- Run quarterly incident tabletop exercises covering model data leak, compromised OAuth token, cross-tenant retrieval, and vulnerable dependency exploit.

### 5.2 Customer-facing controls

- Tenant admin settings for:
  - allowed model providers and regions;
  - whether sensitive PII can be sent to models;
  - whether cloud search is enabled;
  - retention/deletion periods;
  - export/email confirmation thresholds;
  - DLP allow/deny lists for matter types and sources.
- Data processing addendum and security whitepaper that explain AI data flows, no-training commitments, subprocessors, retention, encryption, and deletion.
- Matter-level audit reports for AI usage: user, timestamp, feature, sources used, sensitivity tags, model/provider, and policy outcome.

## 6. Insurance to evaluate

Work with a broker who understands SaaS, legal tech, AI, and regulated customer data. Evaluate coverage for:

1. **Cyber liability / network security and privacy liability** for data breaches, ransomware, business interruption, forensic costs, notification, credit monitoring, and regulatory defense.
2. **Technology errors and omissions (Tech E&O)** for claims that the platform failed, produced harmful automation outcomes, lost data, or caused customer business loss.
3. **Media liability** for generated or hosted content claims, depending on product exposure.
4. **Professional liability / miscellaneous E&O** if customers rely on legal workflow automation or document generation outputs.
5. **AI-specific endorsements** where available for model output harm, algorithmic error, data poisoning, prompt-injection-related leakage, and third-party AI service failure.
6. **Crime/social engineering coverage** for invoice redirection, OAuth compromise, and employee impersonation scenarios.
7. **Directors and officers (D&O)** if raising capital or handling enterprise/compliance claims.
8. **Employment practices liability (EPLI)** as the company scales.

Coverage questions to ask:

- Are AI/model-provider incidents excluded?
- Are contractual liability, regulatory investigations, biometric/health/privacy statutes, and PCI/PHI claims covered or sublimited?
- Are cloud-provider outages and dependent business interruption covered?
- Are retroactive dates, waiting periods, panel counsel, breach coach, and forensic vendors acceptable?
- What minimum controls are required: MFA, EDR, backups, vulnerability management, encryption, vendor management, incident response, and security awareness training?

## 7. Code refactors to prioritize

### 7.1 Central AI request pipeline

Create a single AI gateway path in the backend so every model call goes through the same controls:

```
request -> authz/matter scope -> retrieval -> DLP scan -> redaction -> policy decision -> model call -> output DLP -> audit event -> response
```

Avoid direct provider SDK calls outside this service. The service should enforce tenant policy, provider allowlists, redaction, no raw prompt logging, retries, rate limits, and model metadata capture.

### 7.2 DLP service and policy engine

Add a dedicated DLP module with:

- detectors for PII, credentials, legal-sensitive terms, and configurable tenant patterns;
- document-level and chunk-level sensitivity tags;
- policy decisions: allow, redact, warn, require admin approval, quarantine, or block;
- reversible tokenization for workflows that must preserve identifiers;
- tests with synthetic sensitive data and regression fixtures.

### 7.3 Data minimization and retention

- Replace broad prompt construction with typed context objects containing source IDs, sensitivity labels, permissions, and retention class.
- Add hard limits on context size per source and per sensitivity class.
- Store model interaction metadata separately from raw conversation content.
- Add deletion workflows that remove documents, chunks, embeddings, cloud metadata, memory entries, and derived caches for a matter/tenant.

### 7.4 Auditability and tenant isolation

- Ensure all AI events include tenant_id, user_id, matter_id, feature, retrieval sources, model/provider, sensitivity tags, DLP action, and correlation ID.
- Add tests that prove no cross-tenant chunks, memories, cloud metadata, or connected-file content can enter another tenant's prompt.
- Add admin/auditor views and exportable evidence for compliance reviews.

### 7.5 Dependency and container hygiene

- Commit lockfiles and enforce deterministic builds.
- Pin production images by digest.
- Add CI jobs for SBOM generation, vulnerability scanning, license checks, and secret scanning.
- Publish SBOM artifacts with each release and retain them for incident response.

## 8. Implementation roadmap

### Phase 0 — Immediate risk reduction (0-2 weeks)

- Disable raw prompt/completion logging for privileged workflows.
- Add secret scanning to CI and pre-commit.
- Add basic DLP scanning for uploads and outbound prompts.
- Inventory all model calls and provider settings.
- Add SBOM generation for backend, frontend, agent, and container builds.
- Confirm model provider no-training/no-retention terms for customer data.

### Phase 1 — Controlled AI pipeline (2-6 weeks)

- Refactor all model calls through a central AI gateway service.
- Persist AI audit events and sensitivity tags.
- Add tenant admin settings for provider/model/DLP policy.
- Add output DLP scans for save, email, and export flows.
- Add prompt-injection tests for retrieved/cloud content.
- Start broker review for Cyber, Tech E&O, AI endorsements, and crime coverage.

### Phase 2 — Compliance evidence (6-12 weeks)

- Build AI BOM release records and attach them to deployments.
- Add compliance dashboards for DLP events, AI usage, vulnerability status, and SBOM coverage.
- Add deletion verification for documents, chunks, embeddings, memory, and cloud metadata.
- Complete incident tabletop exercises and update the incident-response plan.
- Prepare customer-facing AI data-flow documentation and subprocessor disclosures.

### Phase 3 — Enterprise readiness (12+ weeks)

- Add customer-managed keys or stronger key segregation for enterprise tenants.
- Add region pinning and model-provider routing by tenant policy.
- Add security questionnaire evidence packs: SOC 2 mappings, SBOM samples, DLP policy examples, audit reports, and incident drill summaries.
- Evaluate external penetration testing focused on AI prompt injection, tenant isolation, cloud connector auth, and data exfiltration.

## 9. Open decisions

- Which data classes are categorically prohibited from model calls?
- Which tenants need private model endpoints, regional processing, or customer-managed keys?
- What raw conversation retention period is acceptable for legal customers?
- Should high-risk matter types default to redaction-only mode?
- Which model providers are approved subprocessors for privileged legal data?
- What incident notification SLA will be promised in contracts?
