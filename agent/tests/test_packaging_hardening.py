import ast
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
WXS = ROOT / "packaging" / "windows" / "lawhand-agent.wxs"
CONFIG = ROOT / "clarity_agent" / "config.py"
LINUX_PACKAGING = ROOT / "packaging" / "linux"


def test_msi_does_not_accept_or_log_pairing_code():
    text = WXS.read_text(encoding="utf-8")
    assert "PAIRING_CODE" not in text
    assert "RegisterAgent" not in text


def test_upgrade_preserves_state_and_does_not_repair():
    text = WXS.read_text(encoding="utf-8")
    assert 'UpgradeCode="d3f674f5-b516-4ecc-b82d-b2495b4aa260"' in text
    assert 'Component Id="DataFolderComponent"' in text
    assert 'Directory="DATAFOLDER" Permanent="yes"' in text
    assert "WIX_UPGRADE_DETECTED" not in text
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
    assert 'Action="SetServiceAccountFromExisting"' in text
    assert 'Condition="NOT SERVICE_ACCOUNT AND EXISTING_SERVICE_ACCOUNT"' in text
    assert 'Value="LocalSystem"' in text
    assert 'Action="SetServiceAccountDefault"' in text
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


def test_release_workflow_requires_and_verifies_windows_signatures():
    workflow = (ROOT.parent / ".github" / "workflows" / "agent-release.yml").read_text(
        encoding="utf-8"
    )
    assert "WINDOWS_SIGNING_AZURE_CLIENT_ID" in workflow
    assert "WINDOWS_SIGNING_AZURE_TENANT_ID" in workflow
    assert "WINDOWS_SIGNING_AZURE_SUBSCRIPTION_ID" in workflow
    assert "WINDOWS_SIGNING_ENDPOINT" in workflow
    assert "WINDOWS_SIGNING_ACCOUNT_NAME" in workflow
    assert "WINDOWS_SIGNING_CERTIFICATE_PROFILE_NAME" in workflow
    assert "WINDOWS_SIGNING_EXPECTED_SUBJECT" in workflow
    assert (
        "azure/artifact-signing-action@c7ab2a863ab5f9a846ddb8265964877ef296ee82"
        in workflow
    )
    assert "azure/login@7ddb5af1ef8758cf1353cf3b42f940aee27ba21c" in workflow
    assert "timestamp-rfc3161: http://timestamp.acs.microsoft.com" in workflow
    assert "timestamp-digest: SHA256" in workflow
    assert "Get-AuthenticodeSignature" in workflow
    assert "signtool verify /pa /all /tw" in workflow
    assert workflow.count("TimeStamperCertificate") == 2
    assert "Unexpected or invalid Windows signer" in workflow
    assert "id-token: write" in workflow
    assert "-SkipExe" in workflow
    assert "environment:" in workflow and "name: agent-release" in workflow
    assert "deployment: false" in workflow
    assert "repo:mattpainter701/legalapp:environment:agent-release" in workflow
    assert "needs: [windows-sign, linux]" in workflow
    assert "lawhand-agent-windows-unsigned" in workflow
    assert "Upload unsigned executable handoff" in workflow
    assert workflow.index("Windows — unsigned validation") < workflow.index(
        "Windows — sign and package release"
    )
    assert workflow.index("Build unsigned validation installers") < workflow.index(
        "Smoke-test unsigned validation MSI overtop upgrade"
    )
    assert workflow.index(
        "Smoke-test unsigned validation MSI overtop upgrade"
    ) < workflow.index("Sign Windows executable")
    assert workflow.index("Sign Windows executable") < workflow.index("-SkipExe")
    assert workflow.index("-SkipExe") < workflow.index("Sign Windows MSI")
    assert workflow.count("timestamp-rfc3161: http://timestamp.acs.microsoft.com") == 2
    assert workflow.count("timestamp-digest: SHA256") == 2
    assert workflow.count("SignerCertificate.Subject -ne") == 2
    assert workflow.index("Get-AuthenticodeSignature") < workflow.index(
        "Upload signed Windows artifacts"
    )


def test_windows_packaging_can_reuse_signed_executable_for_msi():
    script = (ROOT / "packaging" / "windows" / "build.ps1").read_text(encoding="utf-8")
    assert "[switch]$SkipExe" in script
    assert "-SkipExe requires an existing signed executable" in script
    assert script.index("$SkipExe") < script.index("wix build")


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
    assert "Win32_Service" in script
    assert "$predecessor" in script
    assert "$replacement" in script
    assert "CreationDate" in script
    assert "ExecutablePath" in script
    assert "$installedExe" in script
    assert "[StringComparison]::OrdinalIgnoreCase" in script
    assert "Predecessor service process identity is still alive" in script
    assert "if ($currentMsi -and" in script
    assert "ExpectedSignerSubject" in script
    assert "TimeStamperCertificate" in script
    assert (
        "MSI did not install the expected timestamped Authenticode-signed executable"
        in script
    )
    assert "-ExpectedSignerSubject $env:WINDOWS_SIGNING_EXPECTED_SUBJECT" in workflow
    assert "test-upgrade.ps1 -Run" in workflow
    assert workflow.index("Build unsigned validation installers") < workflow.index(
        "test-upgrade.ps1 -Run"
    )


def test_msi_grants_data_directory_to_service_identity():
    text = WXS.read_text(encoding="utf-8")
    assert 'ServiceSid="unrestricted"' in text
    assert 'OnInstall="yes"' in text
    assert 'OnReinstall="yes"' in text
    assert (
        '<util:PermissionEx User="SYSTEM" GenericAll="yes" Inheritable="yes" />' in text
    )
    assert (
        '<util:PermissionEx User="Administrators" GenericAll="yes" Inheritable="yes" />'
        in text
    )
    assert (
        '<util:PermissionEx Domain="NT SERVICE"' in text
        and 'User="LawHandAgent"' in text
    )
    assert '<util:PermissionEx User="[SERVICE_ACCOUNT]"' not in text


def test_search_node_samples_ship_in_both_release_artifacts():
    wxs = WXS.read_text(encoding="utf-8")
    linux = (LINUX_PACKAGING / "build.sh").read_text(encoding="utf-8")
    expected = {
        "config.example.toml",
        "lawhand.options",
        "opensearch.yml",
        "performance-analyzer.properties",
        "search-node-operations.md",
    }
    for name in expected:
        assert name in wxs
        assert name in linux
    assert 'Directory Id="SearchNodeSamplesFolder" Name="search-node"' in wxs
    assert 'mkdir -p "${STAGE}/search-node"' in linux


def test_performance_analyzer_sample_is_loopback_only():
    sample = (
        ROOT / "packaging" / "search-node" / "performance-analyzer.properties"
    ).read_text(encoding="utf-8")
    assert "webservice-bind-host = 127.0.0.1" in sample
    assert "webservice-bind-host = 0.0.0.0" not in sample


def test_search_node_jvm_overlay_uses_supported_tmp_placeholder():
    overlay = (ROOT / "packaging" / "search-node" / "lawhand.options").read_text(
        encoding="utf-8"
    )
    assert "${OPENSEARCH_TMPDIR}" in overlay
    assert "${LAWHAND_OPENSEARCH_TMP}" not in overlay
    assert not (ROOT / "packaging" / "search-node" / "jvm.options").exists()


def test_cross_platform_search_config_has_no_linux_only_ca_default():
    sample = (ROOT / "packaging" / "search-node" / "config.example.toml").read_text(
        encoding="utf-8"
    )
    assert 'opensearch_ca_path = ""' in sample
    assert 'opensearch_ca_path = "/etc/' not in sample


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
    assert "OfficialRedirectHandler" in helper
    assert "self.redirects > 5" in helper
    python_blocks = re.findall(r"<<'PY'\r?\n(.*?)\r?\nPY", helper, flags=re.DOTALL)
    download_tree = ast.parse(
        next(block for block in python_blocks if "allowed_hosts" in block)
    )
    allowed_hosts = next(
        ast.literal_eval(node.value)
        for node in download_tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "allowed_hosts"
            for target in node.targets
        )
    )
    assert allowed_hosts == {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "github-releases.githubusercontent.com",
    }
    assert "context.minimum_version = ssl.TLSVersion.TLSv1_2" in helper
