"""Credential selection and session arguments for per-share authentication."""

from clarity_agent.smb_auth import (
    GUEST,
    KERBEROS,
    MACHINE,
    NTLM,
    ShareCredential,
    connect,
    session_kwargs,
)


class _FakeSmbClient:
    def __init__(self):
        self.calls = []

    def register_session(self, server, **kwargs):
        self.calls.append((server, kwargs))
        return f"session:{server}"


def test_share_credential_prefers_saas_delivered_credential():
    share = {
        "credential": {
            "credential_id": "c-1",
            "name": "svc-lawhand",
            "auth_method": "ntlm",
            "domain": "CORP",
            "username": "svc-lawhand",
            "password": "s3cret",
        }
    }

    class Config:
        smb_username = "local-user"
        smb_password = "local-pass"
        smb_domain = "LOCAL"

    credential = ShareCredential.from_share(share, Config())

    assert credential.username == "svc-lawhand"
    assert credential.domain == "CORP"
    assert credential.credential_id == "c-1"


def test_share_credential_falls_back_to_local_config():
    class Config:
        smb_username = "local-user"
        smb_password = "local-pass"
        smb_domain = "LOCAL"

    credential = ShareCredential.from_share({}, Config())

    assert credential.auth_method == NTLM
    assert credential.username == "local-user"
    assert credential.name == "local config"


def test_share_credential_falls_back_to_service_account():
    class Config:
        smb_username = ""
        smb_password = ""
        smb_domain = ""

    credential = ShareCredential.from_share({}, Config())

    assert credential.auth_method == MACHINE
    assert credential.username is None


def test_repr_and_describe_never_leak_the_password():
    credential = ShareCredential(
        auth_method=NTLM, domain="CORP", username="svc", password="s3cret"
    )

    assert "s3cret" not in repr(credential)
    assert "s3cret" not in credential.describe
    assert credential.describe == "CORP\\svc (ntlm)"


def test_session_kwargs_per_auth_method():
    ntlm = session_kwargs(
        ShareCredential(auth_method=NTLM, domain="CORP", username="svc", password="pw")
    )
    assert ntlm["username"] == "CORP\\svc"
    assert ntlm["password"] == "pw"
    assert ntlm["auth_protocol"] == "ntlm"

    assert session_kwargs(ShareCredential(auth_method=KERBEROS)) == {
        "auth_protocol": "kerberos"
    }
    assert session_kwargs(ShareCredential(auth_method=GUEST)) == {
        "username": "guest",
        "password": "",
    }
    # The service account negotiates with no explicit identity.
    assert session_kwargs(ShareCredential(auth_method=MACHINE)) == {}


def test_ntlm_without_domain_passes_bare_username():
    kwargs = session_kwargs(
        ShareCredential(auth_method=NTLM, username="svc", password="pw")
    )

    assert kwargs["username"] == "svc"


def test_connect_registers_a_session_with_the_credential():
    fake = _FakeSmbClient()
    credential = ShareCredential(auth_method=NTLM, username="svc", password="pw")

    session = connect("fileserver", credential, smbclient_module=fake)

    assert session == "session:fileserver"
    assert fake.calls == [("fileserver", {"username": "svc", "password": "pw", "auth_protocol": "ntlm"})]
