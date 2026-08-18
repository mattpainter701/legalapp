# Merge quality and security controls

CI exposes these stable branch-protection checks:

- `PR policy and documentation impact` verifies the PR attestation template,
  requires refreshed SBOM tracking files when tracked inputs change, and rejects
  newly added GitHub Actions that are not pinned to a full commit SHA.
- `Security, dependency, secret, and SBOM controls` scans only added diff lines
  for high-confidence credentials, requires exact pins for newly added Python
  requirements, keeps the frontend lockfile paired with its manifest, and
  verifies regenerated SBOM tracking inventory when applicable.
- `Merge Gate` is the aggregate required status; it includes the existing six
  CI checks and the applicable PR/security controls. The PR-only policy is
  intentionally allowed to be skipped on pushes to `main`.

The controls are ratchets. They do not fail an unrelated merge for historical
dependency, image-pinning, or secret-scanning backlog; remediation work can be
planned and then protected from regression.

Repository administrators should enable GitHub code scanning, Dependabot alerts
and updates, secret scanning, and push protection. Those services provide the
continuous vulnerability and secret intelligence that deterministic repository
checks cannot replace. Configure branch protection to require `Merge Gate` and,
after confirming the `CODEOWNERS` mapping, require code-owner review for
high-risk paths.

No LLM review is a required check. Human reviewers remain responsible for
product, legal, security, and data-handling judgment.
