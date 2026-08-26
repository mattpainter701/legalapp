from pathlib import Path


ROOT = Path(__file__).parents[1]
WXS = ROOT / "packaging" / "windows" / "lawhand-agent.wxs"
CONFIG = ROOT / "clarity_agent" / "config.py"
LINUX_PACKAGING = ROOT / "packaging" / "linux"


def test_pairing_code_is_hidden_from_msi_logs():
    text = WXS.read_text(encoding="utf-8")
    assert '<Property Id="PAIRING_CODE" Secure="yes" Hidden="yes"' in text


def test_upgrade_preserves_state_and_does_not_repair():
    text = WXS.read_text(encoding="utf-8")
    assert 'UpgradeCode="d3f674f5-b516-4ecc-b82d-b2495b4aa260"' in text
    assert 'Component Id="DataFolderComponent"' in text
    assert 'Directory="DATAFOLDER" Permanent="yes"' in text
    assert "NOT WIX_UPGRADE_DETECTED" in text
    assert 'Stop="both"' in text
    assert 'Start="install"' in text
    assert 'Wait="yes"' in text


def test_upgrade_instructions_cover_direct_install_and_custom_account():
    text = (ROOT / "packaging" / "windows" / "UPGRADE.md").read_text(encoding="utf-8")
    assert "Do not uninstall the old MSI first" in text
    assert "Start-Process msiexec.exe" in text
    assert "SERVICE_ACCOUNT" in text
    assert "SERVICE_PASSWORD" in text


def test_overtop_upgrade_discovers_existing_service_account_without_password():
    text = WXS.read_text(encoding="utf-8")
    assert '<MajorUpgrade Schedule="afterInstallExecute"' in text
    assert 'Component Id="AgentExeComponent" Guid=' not in text
    assert 'Component Id="DataFolderComponent" Guid=' not in text
    assert '<Property Id="SERVICE_ACCOUNT" Secure="yes" />' in text
    assert '<Property Id="EXISTING_SERVICE_ACCOUNT">' in text
    assert '<RegistrySearch Id="ExistingLawHandServiceAccount"' in text
    assert 'Key="SYSTEM\\CurrentControlSet\\Services\\LawHandAgent"' in text
    assert 'Name="ObjectName"' in text
    assert 'Type="raw"' in text
    assert 'Value="[EXISTING_SERVICE_ACCOUNT]"' in text
    assert 'Condition="NOT SERVICE_ACCOUNT AND EXISTING_SERVICE_ACCOUNT"' in text
    assert 'Value="LocalSystem"' in text
    assert 'Condition="NOT SERVICE_ACCOUNT"' in text
    # Password must remain an input for clean custom-account installs, but an
    # omitted value on an overtop upgrade must not be replaced by a persisted
    # secret or exposed through a custom action.
    assert 'Property Id="SERVICE_PASSWORD" Secure="yes" Hidden="yes"' in text
    assert "No password is read or persisted" in text


def test_release_workflow_publishes_update_manifest():
    workflow = (ROOT.parent / ".github" / "workflows" / "agent-release.yml").read_text(
        encoding="utf-8"
    )
    assert 'printf \'{"schema_version":1,"version":"%s"' in workflow
    assert '"windows-x86_64"' in workflow
    assert '"linux-x86_64"' in workflow
    assert '"sha256":"%s"' in workflow
    assert "agent-update.json" in workflow


def test_windows_upgrade_smoke_is_opt_in_and_runs_after_build():
    script = (ROOT / "packaging" / "windows" / "test-upgrade.ps1").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT.parent / ".github" / "workflows" / "agent-release.yml").read_text(
        encoding="utf-8"
    )
    assert "if (-not $Run)" in script
    assert "New-LocalUser" in script
    assert "SeServiceLogonRight" in script
    assert "SERVICE_PASSWORD" in script
    assert "SCM rejected the retained service credential" in script
    assert "test-upgrade.ps1 -Run" in workflow
    assert workflow.index("Build agent installers") < workflow.index(
        "test-upgrade.ps1 -Run"
    )


def test_msi_grants_data_directory_to_service_identity():
    text = WXS.read_text(encoding="utf-8")
    assert (
        '<util:PermissionEx User="SYSTEM" GenericAll="yes" Inheritable="yes" />' in text
    )
    assert (
        '<util:PermissionEx User="Administrators" GenericAll="yes" Inheritable="yes" />'
        in text
    )
    assert (
        '<util:PermissionEx User="[SERVICE_ACCOUNT]" GenericAll="yes" '
        'Inheritable="yes" />' in text
    )


def test_windows_acl_preserves_service_account_on_secret_files():
    text = CONFIG.read_text(encoding="utf-8")
    assert '["icacls", str(path), "/reset"]' in text
    assert '"/inheritance:r"' in text
    assert '"*S-1-5-18:(OI)(CI)F"' in text
    assert '"*S-1-5-32-544:(OI)(CI)F"' in text
    assert 'f"{identity}:(OI)(CI)F"' in text


def test_linux_portal_updates_use_root_owned_systemd_handoff():
    installer = (LINUX_PACKAGING / "install.sh").read_text(encoding="utf-8")
    helper = (LINUX_PACKAGING / "lawhand-agent-update").read_text(encoding="utf-8")
    path_unit = (LINUX_PACKAGING / "lawhand-agent-update.path").read_text(
        encoding="utf-8"
    )
    assert "lawhand-agent-update.path" in installer
    assert "/etc/lawhand-agent-updater/prefix" in installer
    assert "PathExists=/etc/lawhand-agent/update.request" in path_unit
    assert "pkexec" not in helper
    assert 'STATUS_FILE="${UPDATER_CONFIG_DIR}/update.status"' in helper
    assert 'mv -fT "$REQUEST_FILE" "$PROCESSING_FILE"' in helper
    assert 'rm -f "$PROCESSING_FILE"' in helper
    assert 'chown --reference="$CONFIG_DIR"' not in helper
    assert "Refusing to downgrade" in helper
    assert "rollback" in helper
    assert "unsupported archive member" in helper
