# LawHand Practice Scenario Demo Pack

This directory contains fictional, generated documents for the LawHand prospect
demonstration. Every document is marked `SYNTHETIC DEMO - NOT LEGAL ADVICE`.
No real client, counterparty, person, address, email, or transaction is used.

`manifest.json` maps 48 documents into 15 lively, common matters across every
shipped practice module. Each matter includes a fictional client profile and
address, primary contact, opposing party, demo prompt, suggested tasks, and
multiple structured source documents. The manifest is preparation evidence only:
it is not a production database seed and the documents have not been uploaded to
`cybersafeadvisor.com`.

Regenerate the pack with:

```text
python scripts/build_cybersafeadvisor_demo_pack.py
```

Research inputs are listed inside each document. They informed document
structure and issue selection; source text was not copied into the fixtures.

The generated packages pass metadata/structure tests and the document-skill
accessibility audit with no findings. Canonical LibreOffice rendering was not
available on this Windows host, and Word's unattended PDF export did not
complete, so page-image visual QA remains an explicit pre-upload check.
