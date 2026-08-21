import pytest

from app.services.matter_file_store import _validated_provider_open_url
from app.services.provider_http import ProviderError


@pytest.mark.parametrize(
    ("url", "backend"),
    [
        ("https://drive.google.com/file/d/item/view", "google_drive"),
        ("https://docs.google.com/document/d/item/edit", "google_drive"),
        ("https://firm.sharepoint.com/:w:/r/sites/matter/doc", "sharepoint"),
        ("https://firm-my.sharepoint.com/personal/user/doc", "onedrive"),
        ("https://1drv.ms/w/s!item", "onedrive"),
    ],
)
def test_provider_open_url_allowlist(url, backend):
    assert _validated_provider_open_url(url, backend) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://drive.google.com/file/d/item/view",
        "https://drive.google.com.evil.example/item",
        "https://example.com/redirect",
        "javascript:alert(1)",
    ],
)
def test_provider_open_url_rejects_untrusted_schemes_and_hosts(url):
    with pytest.raises(ProviderError):
        _validated_provider_open_url(url, "google_drive")
