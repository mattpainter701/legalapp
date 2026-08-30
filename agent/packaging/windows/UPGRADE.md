# Windows MSI upgrades

The LawHand MSI uses one stable `UpgradeCode` and a permanent ProgramData
component. Installing a newer version over an existing installation therefore
replaces the executable, stops and restarts the `LawHandAgent` service, and
keeps enrollment, `config.toml`, the key material, and the SQLite ledger in:

```text
C:\ProgramData\LawHand\Agent
```

Major upgrades use WiX `Schedule="afterInstallExecute"`. The old product and
service are retained until the replacement has installed successfully. This
late-upgrade invariant is essential for custom service accounts: Windows
Installer cannot recover a deleted service's password. The service component
key paths and auto-generated component identities must remain unchanged across
releases.

Do not uninstall the old MSI first: that creates an unnecessary service and
availability gap and can discard installer-managed state. Run the upgrade
directly with the new package:

```powershell
$msi = "C:\Downloads\lawhand-agent-0.15.3-x64.msi"
$log = "C:\Windows\Temp\lawhand-agent-upgrade.log"
$args = "/i `"$msi`" /qn /norestart /l*v `"$log`""
$p = Start-Process msiexec.exe -ArgumentList $args -Wait -PassThru
if ($p.ExitCode -notin @(0, 1641, 3010)) {
    throw "LawHand agent upgrade failed with exit code $($p.ExitCode). See $log"
}
```

The MSI has no pairing custom action and does not consume a pairing-code property.
Enrollment is retained in the existing ProgramData files and the service starts
with that enrollment after the new binary is installed.

When the late-upgrade invariant is preserved, the MSI reads the existing
`LawHandAgent` service account from the SCM service registry entry. A normal
direct `msiexec /i` upgrade with no account arguments therefore preserves a
custom account and its existing password; passwords are never read, copied, or
logged. Do not use an MSI built with an earlier removal schedule or changed
component identities for this claim. A clean install with a custom account
still requires both properties:

```powershell
$args = "/i `"$msi`" /qn /norestart SERVICE_ACCOUNT=CORP\svc-lawhand SERVICE_PASSWORD=`"$password`" /l*v `"$log`""
Start-Process msiexec.exe -ArgumentList $args -Wait -PassThru
```

The password is a protected/hidden MSI property, but avoid putting it in a
shared command history or process-monitoring environment. The portal updater
continues to update only LocalSystem services automatically; custom-account
hosts should use the direct overtop command above so the existing credential is
preserved. A domain service account is not required when each share uses a
credential from the tenant vault.
