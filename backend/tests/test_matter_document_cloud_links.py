import uuid

from app.models.matter_document import MatterDocument
from app.services.matter_file_store import (
    _document_folder_for_category,
    _extract_subfolder_id,
)


def test_matter_document_exposes_google_drive_link_metadata():
    doc = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        matter_id=uuid.uuid4(),
        filename="engagement-letter.docx",
        storage_path="https://drive.google.com/file/d/file-123/view",
    )

    assert doc.cloud_url == "https://drive.google.com/file/d/file-123/view"
    assert doc.storage_backend == "google_drive"


def test_matter_document_exposes_onedrive_link_metadata():
    doc = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        matter_id=uuid.uuid4(),
        filename="complaint.pdf",
        storage_path="https://example.sharepoint.com/personal/user/Documents/complaint.pdf",
    )

    assert doc.cloud_url == doc.storage_path
    assert doc.storage_backend == "onedrive"


def test_matter_document_marks_local_files_as_local():
    doc = MatterDocument(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        matter_id=uuid.uuid4(),
        filename="local.pdf",
        storage_path="/uploads/tenant/matter/local.pdf",
    )

    assert doc.cloud_url is None
    assert doc.storage_backend == "local"


def test_document_category_maps_to_provisioned_subfolders():
    assert _document_folder_for_category("pleading") == "pleadings"
    assert _document_folder_for_category("contract") == "documents"
    assert _document_folder_for_category("evidence") == "documents"
    assert _document_folder_for_category(None) == "documents"


def test_subfolder_lookup_accepts_historical_uploads_key():
    cloud_folder = {
        "onedrive": {
            "subfolders": {
                "uploads": "folder-uploads",
            }
        }
    }

    assert _extract_subfolder_id(cloud_folder, "onedrive", "documents") == "folder-uploads"
