# Tabs3 / PracticeMaster Discovery

Read-only discovery for the Tabs3 / PracticeMaster backend. Concrete hostnames
and endpoints are env-owned and must stay in local env files.

## Scope

This discovery inspected server metadata only:

- SQL Server database list via `LEGACY_SQLSERVER_HOST`.
- SMB filesystem metadata for `LEGACY_TABS3_ROOT` and `LEGACY_TABS3_DATABASE_PATH`.
- Installed vendor help/config files related to ODBC and backend access.

No Tabs3/PracticeMaster client, billing, ledger, contact, or matter records were dumped.

## Environment Findings

- Server and SQL endpoint: env-owned (`LEGACY_TABS3_HOST`, `LEGACY_SQLSERVER_HOST`).
- SQL Server only exposed the dashboard DB plus system DBs:
  - `ProfessionalServicesDashboard`
  - `master`
  - `model`
  - `msdb`
  - `tempdb`
- Tabs3 / PracticeMaster data is not a SQL Server database on that instance.
- Tabs3 / PracticeMaster backend path exists at `LEGACY_TABS3_DATABASE_PATH`.
- Tabs3 server endpoint is env-owned (`LEGACY_TABS3_SERVER_ENDPOINT`).
- Tabs3 server port reachability should be checked from the operator workstation before export.

## Storage Format

The active backend is file-based FairCom / c-tree style storage, not MSSQL.

Evidence:

- `T:\STI\Database` contains paired `.dat` and `.idx` files.
- `T:\STI` includes FairCom/c-tree files and config such as:
  - `FAIRCOM.FCS`
  - `ctsrvr.cfg`
  - `stsrvr.cfg`
  - `ctreedbs.dll`
  - `c-treeACEVSSWriter.dll`
- `ctsrvr.cfg` identifies this as the Platinum server config and enables `GUEST_LOGON YES` for ODBC access.
- Vendor help states the product uses FairCom's c-tree storage and ODBC is read-only for third-party access.

## Database Folder Summary

`T:\STI\Database`:

- 120 files.
- 60 `.dat` files.
- 60 `.idx` files.
- Approximate total size: 2.73 GB.

Largest active files:

| File | Approx Size | Meaning / Likely Domain |
|-|-:|-|
| `t3arch.idx` | 1.08 GB | Tabs3 billed/archive transaction index |
| `t3arch.dat` | 412 MB | Tabs3 archive transactions |
| `t3ledger.dat` | 229 MB | Client ledger records |
| `t3ledger.idx` | 227 MB | Client ledger index |
| `t3cntact.dat` | 180 MB | Tabs3 contact data |
| `t3client.dat` | 106 MB | Tabs3 client/matter data |
| `T3STRACK.dat` | 101 MB | Statement/pre-bill tracking |
| `t3stdtal.idx` | 64 MB | Statement detail allocation/index data |
| `t3ldgal.idx` | 34 MB | Client ledger allocation index |
| `t3client.idx` | 33 MB | Client/matter index |

`T:\STI\Database\CMSYSTEM` contains PracticeMaster tables, including:

- `cmclient.dat/.idx` - PracticeMaster client file.
- `cmrelate.dat/.idx` - PracticeMaster contact file.
- `cmrellnk.dat/.idx` - contact link file.
- `cmfee.dat/.idx` - fee transactions.
- `cmcost.dat/.idx` - cost transactions.
- `cmjrnl.dat/.idx` - journal/timer/phone/research/note records.
- `cmcal.dat/.idx` - calendar/event/task records.
- `cmaudit.dat/.idx` - history/audit tracking.
- `cmxref.dat/.idx` - cross-reference data.

## ODBC / Supported Read Path

The install includes `T:\STI\ODBC_Files\ODBCSetup.exe`.

Schema-only ODBC documentation was parsed into:

- [tabs3-odbc-schema.md](tabs3-odbc-schema.md) - human-readable table index and key field lists.
- [tabs3-odbc-schema.json](tabs3-odbc-schema.json) - full machine-readable schema dump.

The dump covers 91 ODBC tables/files and 2,128 documented fields across Tabs3 Billing, PracticeMaster, Trust Accounting, General Ledger, Accounts Payable, and System Configuration. It was generated from installed vendor help files only; no customer `.dat` / `.idx` records were read.

Vendor help files include ODBC dictionaries for:

- Tabs3 Billing: `T:\STI\Help\tbmain\Content\odbc_file_list.htm`.
- PracticeMaster: `T:\STI\Help\cmmain\Content\odbc_file_list.htm`.
- System Configuration: `T:\STI\Help\scmain\Content\odbc_file_list.htm`.
- Accounts Payable: `T:\STI\Help\apmain\Content\odbc___file_list.htm`.

Vendor documentation explicitly warns:

- Use ODBC for retrieving information.
- Do not write directly to Tabs3 data files through ODBC or direct file access.
- ODBC users may have broad access, including secure/restricted client data.

Implication: future integration should be read-only ODBC sync/export first. Do not attempt direct `.dat` parsing or direct writes.

## Useful Data Domains

Tabs3 Billing ODBC files likely useful for migration/integration:

- `CLIENT` - client/matter information.
- `CONTACT` - contact records.
- `FEE` - fee/time transactions.
- `COST` - cost transactions.
- `PAYMENT` - payments.
- `FUND` - client funds/trust-style transactions.
- `LEDGER` - statement/payment ledger records.
- `ARCHIVE` - billed/archive fee, cost, payment, and funds transactions.
- `BILLTO` - bill-to address.
- `EMPLOYEE` - timekeepers.
- `CLIENTNOTE` - client notes.
- `CLIENTRATE` / `COSTRATE` - rate tables.
- `SECCLT` - secure client metadata.
- `STMTDET`, `STMTDETALLOC`, `STMTTRAK` - statement detail/tracking.
- `TASKBILLCODE`, `TCODE` - task/transaction codes.
- `TRUSTREQUEST` - trust/payment requests.

PracticeMaster ODBC files likely useful:

- `CMCLIENT` - client/matter information.
- `CMRELATE` - contacts.
- `CMRELLNK` - contact links.
- `CMFEE` - fee transactions.
- `CMCOST` - cost transactions.
- `CMJRNL` - journal, timer, phone, research, note, and billing-note records.
- `CMCAL` - calendar events/tasks.
- `CMDOCMGT` / `CMDOCVSN` - document management/version records.
- `CMAUDIT` - history tracking.
- `CMEMPL` - timekeepers.
- `CMSECCAS` - secure client metadata.
- `CMXREF` - cross-reference data.

## Integration Approach

Recommended migration path:

1. Install/configure the vendor-supported 32-bit read-only ODBC driver in a controlled Windows environment.
2. Run the vendor "Initialize Tabs3 ODBC" and "Initialize PracticeMaster ODBC" utilities if they have not already been run.
3. Use ODBC metadata calls to enumerate tables/columns and export schema to JSON/Markdown.
4. Export small redacted samples from low-risk lookup tables first, then request explicit approval before exporting client/billing rows.
5. Build separate importers by domain:
   - contacts/clients/matters,
   - timekeepers/users,
   - time/fee entries,
   - costs,
   - payments/ledger,
   - notes/journal/history,
   - calendar/tasks,
   - documents metadata.
6. Keep initial integration one-way/read-only into Clarity staging tables.
7. Add reconciliation reports before any production migration.

Do not merge this directly into `legacy_call_records`; Tabs3/PracticeMaster is a separate system of record and needs separate staging/import models.

## Risks

- ODBC access may expose secure/restricted clients if not scoped carefully.
- Direct writes to `.dat`/`.idx` files can corrupt Tabs3 data.
- File-based data may be actively changing during business hours; export should run from a supported ODBC path and preferably during a low-activity window.
- Some files are large enough that full exports should be batched and checksummed.
- Legacy "client" may map to Clarity contact, matter, billing account, or all three depending on Tabs3 field definitions.

## Next Steps

- Confirm whether the firm owns an ODBC license for Tabs3/PracticeMaster.
- Confirm whether ODBC dictionaries have already been initialized.
- Use a Windows integration host to configure a read-only ODBC DSN against `LEGACY_TABS3_SERVER_ENDPOINT`.
- Generate schema-only reports for Tabs3 Billing and PracticeMaster.
- Only after schema review, export limited sample rows with explicit approval and redaction rules.
