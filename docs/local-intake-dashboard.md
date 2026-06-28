# Local Intake Dashboard

The intake dashboard is an in-platform Clarity Legal module for receptionist call handling. It is intended to replace a legacy desktop/.NET dashboard after a parallel validation period.

## Deployment Model

Use one server or office VM on the LAN. Do not install a separate localhost app on each Windows workstation.

Recommended local launch:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

Receptionists should open the LAN URL served by nginx, or use a browser desktop shortcut pinned to `/intake/dashboard`.

Vite localhost remains a development-only workflow.

## Module-Only Tenant Setup

For a customer buying only the intake dashboard, set module visibility on the tenant through the platform API:

```json
{
  "enabled_modules": ["intake-dashboard"],
  "default_module": "intake-dashboard"
}
```

After login, `/api/auth/me` returns:

```json
{
  "enabled_modules": ["intake-dashboard"],
  "default_route": "/intake/dashboard"
}
```

The React shell uses those fields to:

- land the user directly on `/intake/dashboard`;
- hide unrelated sidebar modules;
- redirect direct navigation to hidden routes back to the tenant default route.

Existing tenants with no explicit module config retain the full platform by default.

This is view-level and frontend route-level gating. Full backend RBAC/permission enforcement across every API should be implemented as the next hardening step using the existing `TASKS.md` RBAC backlog direction.

## Legacy Call Archive Import

Historical records are imported into `legacy_call_records`, not into active contacts or leads. This keeps 20 years of low-conversion call records searchable without polluting the active CRM.

### SQL Server Discovery Over Tailscale

When the old app server is reachable over Tailscale, inspect the SQL Server databases with the read-only helper:

```bash
py -m pip install -r backend/scripts/requirements-sqlserver.txt
```

Use a read-only SQL login if possible. Either pass a full ODBC connection string:

```bash
py backend/scripts/inspect_legacy_sqlserver.py \
  --connection-string "DRIVER={ODBC Driver 18 for SQL Server};SERVER=100.x.y.z,1433;DATABASE=LegacyIntake;UID=readonly;PWD=...;Encrypt=yes;TrustServerCertificate=yes;" \
  inspect \
  --out docs/customer-legacy-intake-schema.json \
  --markdown docs/customer-legacy-intake-schema.md
```

Or use environment variables:

```powershell
$env:LEGACY_SQLSERVER_HOST = "100.x.y.z,1433"
$env:LEGACY_SQLSERVER_DATABASE = "LegacyIntake"
$env:LEGACY_SQLSERVER_USER = "readonly"
$env:LEGACY_SQLSERVER_PASSWORD = "..."
py backend/scripts/inspect_legacy_sqlserver.py inspect --out docs/customer-legacy-intake-schema.json --markdown docs/customer-legacy-intake-schema.md
```

Useful commands:

```bash
py backend/scripts/inspect_legacy_sqlserver.py --server 100.x.y.z,1433 --username readonly --password ... list-databases
py backend/scripts/inspect_legacy_sqlserver.py --server 100.x.y.z,1433 --database LegacyIntake --username readonly --password ... inspect --samples 3
```

Samples are redacted by default. Only use `--unredacted-samples` when you have explicit permission and a secure output location.

The inspector produces:

- table/column inventory;
- estimated row counts;
- likely call/intake/lead/contact tables ranked by keyword score;
- Tabs3/PracticeMaster hints such as client, matter, time, invoice, ledger, trust, and billing columns;
- optional redacted sample rows for likely tables.

### Export From SQL Server To Import CSV

After identifying the legacy call table and field names, export a canonical CSV:

```bash
py backend/scripts/inspect_legacy_sqlserver.py \
  --server 100.x.y.z,1433 \
  --database LegacyIntake \
  --username readonly \
  --password ... \
  export-calls \
  --table dbo.CallHistory \
  --map source_row_id=CallID \
  --map caller_name=CallerName \
  --map phone=PhoneNumber \
  --map call_date=CreatedDate \
  --map practice_area=CaseType \
  --map purpose=Reason \
  --map prior_attorney_name=Attorney \
  --map notes=Notes \
  --out calls-export.csv
```

Then dry-run and import into Clarity:

```bash
py backend/scripts/import_legacy_call_records.py calls-export.csv --tenant-id <tenant-uuid> --dry-run
py backend/scripts/import_legacy_call_records.py calls-export.csv --tenant-id <tenant-uuid> --import
```

The SQL Server helper only reads from SQL Server. It writes local reports/CSV files only.

For the Armor Interactive `Professional Services Dashboard` legacy app, use the dedicated
Windows exporter instead. It decrypts the installed app config locally, connects to SQL
Server, and maps `dbo.Calls` into the canonical CSV:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\backend\scripts\export_professional_services_dashboard.ps1 `
  -AppDirectory "C:\Program Files (x86)\Armor Interactive\Professional Services Dashboard" `
  -ServerOverride "$env:LEGACY_SQLSERVER_HOST" `
  -OutCsv .\legacy-dashboard-calls.csv `
  -SchemaOutJson .\legacy-dashboard-schema.json `
  -IncludeEmployeesCsv
```

Discovered schema notes for this customer database:

- SQL Server endpoint and database name are env-owned (`LEGACY_SQLSERVER_HOST`, `LEGACY_SQLSERVER_DATABASE`); observed engine was SQL Server 2016 Express.
- `dbo.Calls` contains 26,100 rows dated from April 12, 2010 through June 16, 2026.
- `dbo.Calls` has `CallId`, `IntendedToId`, `AssignedToId`, `AnsweredById`, `CallTime`, `CallLast`, `CallFirst`, `CallReason`, `AssignedReason`, `IntendedToId2`, and `AssignedToId2`.
- There is no structured caller phone column in the legacy `Calls` table. Some phone numbers appear to be embedded inside `CallReason`; keep the raw reason text in the archive and do not treat it as normalized phone data unless a later extraction pass is approved.
- `dbo.EmployeeStatuses` contains activity/status history and should not be imported into `legacy_call_records`; it can inform a later presence dashboard if needed.
- Lookup tables are `Statuses` (`Available`, `Busy`, `Away`) and `Groups` (`User`, `Queue`, `Admin`, `Assoc`).

### Tabs3 / PracticeMaster Database

If the same SQL Server hosts Tabs3 Billing and PracticeMaster in a separate database, inspect it separately first:

```bash
py backend/scripts/inspect_legacy_sqlserver.py \
  --server 100.x.y.z,1433 \
  --database Tabs3OrPracticeMasterDb \
  --username readonly \
  --password ... \
  inspect \
  --out docs/customer-tabs3-schema.json \
  --markdown docs/customer-tabs3-schema.md
```

Do not import Tabs3/PracticeMaster data into `legacy_call_records`. Use the schema report to understand client, matter, billing, time, trust, and conflict-check fields. Any future Tabs3 import should be a separate mapped importer with explicit destination models.

Expected CSV columns can use any of these aliases:

| Field | Accepted headers |
|-|-|
| source row id | `source_row_id`, `id`, `record_id`, `call_id`, `legacy_id` |
| caller name | `caller_name`, `name`, `client_name`, `prospect_name` |
| phone | `phone`, `caller_phone`, `telephone`, `phone_number`, `number` |
| call date | `call_date`, `date`, `created_at`, `called_at`, `timestamp` |
| practice area | `practice_area`, `case_type`, `matter_type`, `area` |
| purpose | `purpose`, `reason`, `call_purpose`, `description` |
| prior attorney | `prior_attorney_name`, `attorney`, `lawyer`, `partner`, `assigned_attorney` |
| notes | `notes`, `note`, `comments`, `memo` |

Dry run first:

```bash
py backend/scripts/import_legacy_call_records.py calls.csv --tenant-id <tenant-uuid> --dry-run
```

Import after validation:

```bash
py backend/scripts/import_legacy_call_records.py calls.csv --tenant-id <tenant-uuid> --import
```

The importer prints row counts, duplicate source ids, validation errors, and a sample. It does not inspect `.env` or scrape SQL Express credentials.
