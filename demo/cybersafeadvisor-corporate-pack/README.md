# LawHand Practice Scenario Demo Pack

This directory contains fictional, generated documents for the LawHand prospect
demonstration. Every document is marked `SYNTHETIC DEMO - NOT LEGAL ADVICE`.
No real client, counterparty, person, address, email, or transaction is used.

`manifest.json` maps 75 documents into 18 lively, common matters across every
shipped practice module. Each matter includes a fictional client profile and
address, primary and secondary contacts, communication preferences, opposing
party, demo prompt, suggested tasks, and multiple structured source documents.
The seeded workspace adds 36 visible inbound calls (manual and Zoom-style), 18
strategy-call notes, email threads, timelines, and follow-up tasks. The manifest is preparation evidence only:
it is not a production database seed and the documents have not been uploaded to
`cybersafeadvisor.com`.

Regenerate the pack with:

```text
python scripts/build_cybersafeadvisor_demo_pack.py
```

Research inputs are listed inside each document. They informed document
structure and issue selection; source text was not copied into the fixtures.

The generated packages pass metadata/structure tests and the DOCX accessibility
audit. Page-image visual QA is a release check; it could not be run on this
Windows host because LibreOffice is not installed.
