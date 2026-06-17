# Tabs3 Phase 1 Export / Import Runbook

Phase 1 uses a customer-side export and Clarity web/API import. Clarity Cloud
does not connect directly to the customer's LAN or Tabs3 server.

## Customer-Side Prerequisites

- Windows host with access to Tabs3 ODBC. This can be the Tabs3 server or a
  workstation that can reach the Tabs3 server over the customer network/Tailscale.
- Vendor Tabs3 ODBC driver and DSN configured.
- Python installed with the same bitness as the Tabs3 ODBC driver. Tabs3 installs
  are commonly 32-bit, so 32-bit Python may be required.
- Dependencies:

```powershell
python -m pip install -r scripts\tabs3_export\requirements.txt
```

## Schema-Only Check

Run this first. It validates the DSN and table metadata without exporting client
or billing rows.

```powershell
python scripts\tabs3_export\export_tabs3.py `
  --dsn "Tabs3" `
  --groups core `
  --schema-only `
  --allow-plaintext `
  --output-dir C:\ClarityTabs3Exports
```

## Redacted/Rehearsal Export

Use a row limit for a small import rehearsal.

```powershell
python scripts\tabs3_export\export_tabs3.py `
  --dsn "Tabs3" `
  --groups core billing `
  --row-limit 100 `
  --passphrase "REPLACE_WITH_SHARED_SECRET" `
  --output-dir C:\ClarityTabs3Exports
```

## Full Export

Run during a low-activity window. The large tables are `ARCHIVE` and `LEDGER`;
expect the export to take longer on older Tabs3 servers.

```powershell
python scripts\tabs3_export\export_tabs3.py `
  --dsn "Tabs3" `
  --groups core billing rates_codes trust practicemaster_optional `
  --passphrase "REPLACE_WITH_SHARED_SECRET" `
  --output-dir C:\ClarityTabs3Exports
```

The result is a `.tabs3bundle` encrypted package. Upload it to Clarity with the
same passphrase. Treat every bundle as confidential legal/accounting data.

## Notes

- The exporter only issues `SELECT` statements through ODBC.
- It does not parse Tabs3 `.dat` / `.idx` files and does not write back to Tabs3.
- ODBC access may expose secure/restricted clients; coordinate export timing and
  handling with the firm.
- After Clarity confirms a successful import and reconciliation, delete local
  export bundles from the customer workstation/server.
