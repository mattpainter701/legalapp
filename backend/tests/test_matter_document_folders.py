"""End-to-end coverage for the matter document explorer.

Folders and tags are what turn a matter's flat upload list into something a
firm can navigate, so these drive the real ASGI app: the tree rules (sibling
names, depth, cycles, protected system folders), the filing and filtering the
explorer UI depends on, the storage mirroring that keeps the firm's cloud share
in the same shape, and the tenant fences around all of it.
"""

import io
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.matter_document import MatterDocument
from app.models.matter_document_folder import MatterDocumentFolder
from app.models.plugin import Matter
from app.models.tenant import Tenant
from app.models.user import User
from app.services.matter_document_organization import (
    SYSTEM_FOLDER_CLIENT_UPLOADS,
    DocumentOrganizationError,
    ensure_system_folder,
    normalize_folder_name,
    storage_routing_for_folder,
)

API = "/api/matters"


async def _make_matter(db_session, tenant_id, user_id, name="Matter"):
    row = Matter(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        slug=f"folder-matter-{uuid.uuid4().hex[:8]}",
        matter_name=name,
        matter_type="litigation",
        status="open",
    )
    db_session.add(row)
    await db_session.commit()
    # Requests that reject a folder operation roll the shared test session
    # back, which expires persistent ORM objects; returning plain values keeps
    # every later assertion off the expired instance.
    return SimpleNamespace(id=str(row.id), slug=row.slug)


@pytest_asyncio.fixture
async def tenant_id(test_tenant):
    return test_tenant.id


@pytest_asyncio.fixture
async def matter(db_session, test_tenant, test_user):
    return await _make_matter(
        db_session,
        test_tenant.id,
        test_user.id,
        "Alvarez v. Brightline Logistics",
    )


async def _create_folder(client, matter, name, parent_id=None):
    resp = await client.post(
        f"{API}/{matter.id}/document-folders",
        json={"name": name, "parent_id": str(parent_id) if parent_id else None},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _upload(client, matter, filename, *, folder_id=None, **form):
    data = dict(form)
    if folder_id:
        data["folder_id"] = str(folder_id)
    resp = await client.post(
        f"{API}/{matter.id}/documents/upload",
        files={"file": (filename, io.BytesIO(b"%PDF-1.4 body"), "application/pdf")},
        data=data,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Folder tree rules ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_folders_nest_and_report_their_materialized_path(client, matter):
    discovery = await _create_folder(client, matter, "Discovery")
    depositions = await _create_folder(
        client, matter, "Depositions", parent_id=discovery["id"]
    )

    assert discovery["path"] == "Discovery"
    assert discovery["depth"] == 0
    assert depositions["path"] == "Discovery/Depositions"
    assert depositions["depth"] == 1
    assert depositions["parent_id"] == discovery["id"]

    listing = await client.get(f"{API}/{matter.id}/document-folders")
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 2
    assert [f["path"] for f in body["items"]] == ["Discovery", "Discovery/Depositions"]


@pytest.mark.asyncio
async def test_sibling_folder_names_are_unique_case_insensitively(client, matter):
    await _create_folder(client, matter, "Pleadings")

    resp = await client.post(
        f"{API}/{matter.id}/document-folders", json={"name": "pleadings"}
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "folder_name_taken"


@pytest.mark.asyncio
async def test_the_same_folder_name_is_allowed_under_different_parents(client, matter):
    discovery = await _create_folder(client, matter, "Discovery")
    trial = await _create_folder(client, matter, "Trial")

    first = await _create_folder(client, matter, "Exhibits", parent_id=discovery["id"])
    second = await _create_folder(client, matter, "Exhibits", parent_id=trial["id"])

    assert first["path"] == "Discovery/Exhibits"
    assert second["path"] == "Trial/Exhibits"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,code",
    [
        ("   ", "folder_name_required"),
        ("Discovery/Depositions", "folder_name_invalid"),
        ("Trial: Exhibits", "folder_name_invalid"),
        ("Exhibits.", "folder_name_invalid"),
        ("x" * 200, "folder_name_too_long"),
    ],
)
async def test_folder_names_are_validated(client, matter, name, code):
    resp = await client.post(
        f"{API}/{matter.id}/document-folders", json={"name": name}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == code


@pytest.mark.asyncio
async def test_a_trailing_space_is_collapsed_rather_than_rejected(client, matter):
    # Inner runs collapse; only a leading/trailing dot or space is a hard error.
    folder = await _create_folder(client, matter, "Expert   Reports")
    assert folder["name"] == "Expert Reports"


@pytest.mark.asyncio
async def test_folders_cannot_be_nested_past_the_depth_limit(client, matter):
    parent_id = None
    for level in range(9):
        folder = await _create_folder(client, matter, f"L{level}", parent_id=parent_id)
        parent_id = folder["id"]

    resp = await client.post(
        f"{API}/{matter.id}/document-folders",
        json={"name": "TooDeep", "parent_id": parent_id},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "folder_depth_exceeded"


@pytest.mark.asyncio
async def test_renaming_a_folder_repaths_its_whole_subtree(client, matter):
    discovery = await _create_folder(client, matter, "Discovery")
    depositions = await _create_folder(
        client, matter, "Depositions", parent_id=discovery["id"]
    )
    await _create_folder(client, matter, "Transcripts", parent_id=depositions["id"])

    resp = await client.patch(
        f"{API}/{matter.id}/document-folders/{discovery['id']}",
        json={"name": "Written Discovery"},
    )
    assert resp.status_code == 200, resp.text

    listing = await client.get(f"{API}/{matter.id}/document-folders")
    assert [f["path"] for f in listing.json()["items"]] == [
        "Written Discovery",
        "Written Discovery/Depositions",
        "Written Discovery/Depositions/Transcripts",
    ]


@pytest.mark.asyncio
async def test_moving_a_folder_repaths_and_redepths_its_subtree(client, matter):
    discovery = await _create_folder(client, matter, "Discovery")
    trial = await _create_folder(client, matter, "Trial")
    exhibits = await _create_folder(client, matter, "Exhibits", parent_id=trial["id"])
    await _create_folder(client, matter, "Photos", parent_id=exhibits["id"])

    resp = await client.patch(
        f"{API}/{matter.id}/document-folders/{exhibits['id']}",
        json={"parent_id": discovery["id"]},
    )
    assert resp.status_code == 200, resp.text

    listing = await client.get(f"{API}/{matter.id}/document-folders")
    by_path = {f["path"]: f for f in listing.json()["items"]}
    assert by_path["Discovery/Exhibits"]["depth"] == 1
    assert by_path["Discovery/Exhibits/Photos"]["depth"] == 2
    assert "Trial/Exhibits" not in by_path


@pytest.mark.asyncio
async def test_a_folder_cannot_be_moved_into_its_own_descendant(client, matter):
    discovery = await _create_folder(client, matter, "Discovery")
    depositions = await _create_folder(
        client, matter, "Depositions", parent_id=discovery["id"]
    )

    resp = await client.patch(
        f"{API}/{matter.id}/document-folders/{discovery['id']}",
        json={"parent_id": depositions["id"]},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "folder_move_into_descendant"

    # The rejected move left the tree exactly as it was.
    listing = await client.get(f"{API}/{matter.id}/document-folders")
    assert [f["path"] for f in listing.json()["items"]] == [
        "Discovery",
        "Discovery/Depositions",
    ]


@pytest.mark.asyncio
async def test_a_folder_cannot_be_moved_into_itself(client, matter):
    discovery = await _create_folder(client, matter, "Discovery")

    resp = await client.patch(
        f"{API}/{matter.id}/document-folders/{discovery['id']}",
        json={"parent_id": discovery["id"]},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "folder_move_into_self"


@pytest.mark.asyncio
async def test_a_rename_only_patch_does_not_move_the_folder_to_the_root(
    client, matter
):
    discovery = await _create_folder(client, matter, "Discovery")
    depositions = await _create_folder(
        client, matter, "Depositions", parent_id=discovery["id"]
    )

    resp = await client.patch(
        f"{API}/{matter.id}/document-folders/{depositions['id']}",
        json={"name": "Depos"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["parent_id"] == discovery["id"]
    assert resp.json()["path"] == "Discovery/Depos"


@pytest.mark.asyncio
async def test_an_explicit_null_parent_moves_a_folder_to_the_root(client, matter):
    discovery = await _create_folder(client, matter, "Discovery")
    depositions = await _create_folder(
        client, matter, "Depositions", parent_id=discovery["id"]
    )

    resp = await client.patch(
        f"{API}/{matter.id}/document-folders/{depositions['id']}",
        json={"parent_id": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["parent_id"] is None
    assert resp.json()["path"] == "Depositions"
    assert resp.json()["depth"] == 0


# ── Deleting folders ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deleting_an_empty_folder_removes_its_subtree(client, matter):
    discovery = await _create_folder(client, matter, "Discovery")
    await _create_folder(client, matter, "Depositions", parent_id=discovery["id"])

    resp = await client.delete(
        f"{API}/{matter.id}/document-folders/{discovery['id']}"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["documents_moved"] == 0

    listing = await client.get(f"{API}/{matter.id}/document-folders")
    assert listing.json()["total"] == 0


@pytest.mark.asyncio
async def test_deleting_a_folder_that_still_holds_documents_is_refused(
    client, matter
):
    discovery = await _create_folder(client, matter, "Discovery")
    await _upload(client, matter, "interrogatories.pdf", folder_id=discovery["id"])

    resp = await client.delete(
        f"{API}/{matter.id}/document-folders/{discovery['id']}"
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "folder_not_empty"

    listing = await client.get(f"{API}/{matter.id}/documents")
    assert listing.json()["total"] == 1


@pytest.mark.asyncio
async def test_deleting_a_folder_can_refile_its_documents_into_the_parent(
    client, matter, db_session
):
    discovery = await _create_folder(client, matter, "Discovery")
    depositions = await _create_folder(
        client, matter, "Depositions", parent_id=discovery["id"]
    )
    document = await _upload(
        client, matter, "smith-depo.pdf", folder_id=depositions["id"]
    )

    resp = await client.delete(
        f"{API}/{matter.id}/document-folders/{depositions['id']}"
        "?move_documents_to_parent=true"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["documents_moved"] == 1
    assert body["moved_to_folder_id"] == discovery["id"]

    # The document survived the folder and is now filed one level up.
    row = (
        await db_session.execute(
            select(MatterDocument).where(MatterDocument.id == uuid.UUID(document["id"]))
        )
    ).scalar_one()
    assert str(row.folder_id) == discovery["id"]


@pytest.mark.asyncio
async def test_a_system_folder_cannot_be_renamed_or_deleted(
    client, matter, db_session, tenant_id
):
    folder = await ensure_system_folder(
        db_session,
        tenant_id=tenant_id,
        matter_id=uuid.UUID(matter.id),
        system_key=SYSTEM_FOLDER_CLIENT_UPLOADS,
    )
    await db_session.commit()
    folder_id = str(folder.id)

    rename = await client.patch(
        f"{API}/{matter.id}/document-folders/{folder_id}", json={"name": "Whatever"}
    )
    assert rename.status_code == 409
    assert rename.json()["detail"]["code"] == "folder_is_system"

    removal = await client.delete(f"{API}/{matter.id}/document-folders/{folder_id}")
    assert removal.status_code == 409
    assert removal.json()["detail"]["code"] == "folder_is_system"


@pytest.mark.asyncio
async def test_deleting_a_parent_cannot_take_a_system_folder_with_it(
    client, matter, db_session, tenant_id
):
    parent = await _create_folder(client, matter, "Intake")
    folder = await ensure_system_folder(
        db_session,
        tenant_id=tenant_id,
        matter_id=uuid.UUID(matter.id),
        system_key=SYSTEM_FOLDER_CLIENT_UPLOADS,
    )
    folder.parent_id = uuid.UUID(parent["id"])
    folder.path = f"Intake/{folder.name}"
    folder.depth = 1
    await db_session.commit()

    resp = await client.delete(f"{API}/{matter.id}/document-folders/{parent['id']}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "folder_is_system"

    listing = await client.get(f"{API}/{matter.id}/document-folders")
    assert listing.json()["total"] == 2


# ── Filing and listing documents ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_uploading_into_a_folder_files_and_mirrors_the_document(
    client, matter, db_session
):
    discovery = await _create_folder(client, matter, "Discovery")
    depositions = await _create_folder(
        client, matter, "Depositions", parent_id=discovery["id"]
    )

    document = await _upload(
        client, matter, "smith-depo.pdf", folder_id=depositions["id"]
    )
    assert document["folder_id"] == depositions["id"]
    assert document["folder_path"] == "Discovery/Depositions"

    # The stored copy mirrors the same tree, so the firm's share and the
    # explorer never disagree about where a file lives.
    row = (
        await db_session.execute(
            select(MatterDocument).where(MatterDocument.id == uuid.UUID(document["id"]))
        )
    ).scalar_one()
    stored = (row.storage_path or "").replace("\\", "/")
    assert stored.endswith("Discovery/Depositions/smith-depo.pdf"), stored


@pytest.mark.asyncio
async def test_uploading_without_a_folder_keeps_the_historical_layout(client, matter):
    document = await _upload(
        client, matter, "complaint.pdf", document_category="pleading"
    )
    assert document["folder_id"] is None
    assert document["folder_path"] is None


@pytest.mark.asyncio
async def test_uploading_into_an_unknown_folder_is_rejected(client, matter):
    resp = await client.post(
        f"{API}/{matter.id}/documents/upload",
        files={"file": ("x.pdf", io.BytesIO(b"body"), "application/pdf")},
        data={"folder_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "folder_not_found"


@pytest.mark.asyncio
async def test_documents_can_be_filed_and_unfiled_in_bulk(client, matter):
    discovery = await _create_folder(client, matter, "Discovery")
    first = await _upload(client, matter, "rog-set-one.pdf")
    second = await _upload(client, matter, "rog-set-two.pdf")

    moved = await client.post(
        f"{API}/{matter.id}/documents/move",
        json={
            "document_ids": [first["id"], second["id"]],
            "folder_id": discovery["id"],
        },
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["moved"] == 2
    assert {d["folder_path"] for d in moved.json()["items"]} == {"Discovery"}

    back = await client.post(
        f"{API}/{matter.id}/documents/move",
        json={"document_ids": [first["id"]], "folder_id": None},
    )
    assert back.status_code == 200
    assert back.json()["items"][0]["folder_id"] is None


@pytest.mark.asyncio
async def test_moving_a_document_from_another_matter_is_refused(
    client, matter, db_session, test_tenant, test_user
):
    other = await _make_matter(
        db_session, test_tenant.id, test_user.id, "Unrelated"
    )

    stray = await _upload(client, other, "unrelated.pdf")
    folder = await _create_folder(client, matter, "Discovery")

    resp = await client.post(
        f"{API}/{matter.id}/documents/move",
        json={"document_ids": [stray["id"]], "folder_id": folder["id"]},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_listing_scopes_to_a_folder_and_optionally_its_subfolders(
    client, matter
):
    discovery = await _create_folder(client, matter, "Discovery")
    depositions = await _create_folder(
        client, matter, "Depositions", parent_id=discovery["id"]
    )
    await _upload(client, matter, "root.pdf")
    await _upload(client, matter, "rogs.pdf", folder_id=discovery["id"])
    await _upload(client, matter, "depo.pdf", folder_id=depositions["id"])

    everything = await client.get(f"{API}/{matter.id}/documents")
    assert everything.json()["total"] == 3

    root_only = await client.get(f"{API}/{matter.id}/documents?folder_id=root")
    assert [d["filename"] for d in root_only.json()["items"]] == ["root.pdf"]

    direct = await client.get(f"{API}/{matter.id}/documents?folder_id={discovery['id']}")
    assert [d["filename"] for d in direct.json()["items"]] == ["rogs.pdf"]

    recursive = await client.get(
        f"{API}/{matter.id}/documents"
        f"?folder_id={discovery['id']}&include_subfolders=true"
    )
    assert sorted(d["filename"] for d in recursive.json()["items"]) == [
        "depo.pdf",
        "rogs.pdf",
    ]


@pytest.mark.asyncio
async def test_listing_rejects_a_malformed_folder_filter(client, matter):
    resp = await client.get(f"{API}/{matter.id}/documents?folder_id=not-a-uuid")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_listing_can_search_and_sort(client, matter):
    await _upload(client, matter, "alpha-motion.pdf", description="Motion to compel")
    await _upload(client, matter, "beta-order.pdf", description="Scheduling order")

    search = await client.get(f"{API}/{matter.id}/documents?q=compel")
    assert [d["filename"] for d in search.json()["items"]] == ["alpha-motion.pdf"]

    by_name = await client.get(
        f"{API}/{matter.id}/documents?sort=filename&order=asc"
    )
    assert [d["filename"] for d in by_name.json()["items"]] == [
        "alpha-motion.pdf",
        "beta-order.pdf",
    ]


@pytest.mark.asyncio
async def test_a_search_wildcard_is_matched_literally(client, matter):
    await _upload(client, matter, "quarterly-100%-report.pdf")
    await _upload(client, matter, "unrelated.pdf")

    resp = await client.get(f"{API}/{matter.id}/documents?q=100%25-report")
    assert [d["filename"] for d in resp.json()["items"]] == [
        "quarterly-100%-report.pdf"
    ]


# ── Tags ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tags_are_firm_wide_and_uniquely_named(client):
    created = await client.post(
        "/api/document-tags", json={"name": "Privileged", "color": "rose"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["color"] == "rose"

    duplicate = await client.post("/api/document-tags", json={"name": "privileged"})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "tag_name_taken"

    listing = await client.get("/api/document-tags")
    assert [t["name"] for t in listing.json()["items"]] == ["Privileged"]


@pytest.mark.asyncio
async def test_a_tag_color_outside_the_palette_is_rejected(client):
    resp = await client.post(
        "/api/document-tags", json={"name": "Hot", "color": "#ff0000"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "tag_color_invalid"


@pytest.mark.asyncio
async def test_a_tag_can_be_renamed_and_recolored(client):
    tag = (
        await client.post("/api/document-tags", json={"name": "Draft"})
    ).json()

    resp = await client.patch(
        f"/api/document-tags/{tag['id']}", json={"name": "Working Draft", "color": "amber"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Working Draft"
    assert resp.json()["color"] == "amber"


@pytest.mark.asyncio
async def test_assigning_tags_replaces_the_previous_set(client, matter):
    signed = (await client.post("/api/document-tags", json={"name": "Signed"})).json()
    filed = (await client.post("/api/document-tags", json={"name": "Filed"})).json()
    document = await _upload(client, matter, "settlement.pdf")

    first = await client.put(
        f"{API}/{matter.id}/documents/{document['id']}/tags",
        json={"tag_ids": [signed["id"], filed["id"]]},
    )
    assert first.status_code == 200, first.text
    assert sorted(t["name"] for t in first.json()["items"]) == ["Filed", "Signed"]

    second = await client.put(
        f"{API}/{matter.id}/documents/{document['id']}/tags",
        json={"tag_ids": [filed["id"]]},
    )
    assert [t["name"] for t in second.json()["items"]] == ["Filed"]

    listing = await client.get(f"{API}/{matter.id}/documents")
    assert [t["name"] for t in listing.json()["items"][0]["tags"]] == ["Filed"]


@pytest.mark.asyncio
async def test_assigning_an_unknown_tag_is_refused(client, matter):
    document = await _upload(client, matter, "settlement.pdf")

    resp = await client.put(
        f"{API}/{matter.id}/documents/{document['id']}/tags",
        json={"tag_ids": [str(uuid.uuid4())]},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "tag_not_found"


@pytest.mark.asyncio
async def test_filtering_by_tags_requires_every_requested_tag(client, matter):
    signed = (await client.post("/api/document-tags", json={"name": "Signed"})).json()
    filed = (await client.post("/api/document-tags", json={"name": "Filed"})).json()
    both = await _upload(client, matter, "both.pdf")
    one = await _upload(client, matter, "one.pdf")

    await client.put(
        f"{API}/{matter.id}/documents/{both['id']}/tags",
        json={"tag_ids": [signed["id"], filed["id"]]},
    )
    await client.put(
        f"{API}/{matter.id}/documents/{one['id']}/tags",
        json={"tag_ids": [signed["id"]]},
    )

    single = await client.get(f"{API}/{matter.id}/documents?tag_ids={signed['id']}")
    assert sorted(d["filename"] for d in single.json()["items"]) == [
        "both.pdf",
        "one.pdf",
    ]

    conjunctive = await client.get(
        f"{API}/{matter.id}/documents?tag_ids={signed['id']}&tag_ids={filed['id']}"
    )
    assert [d["filename"] for d in conjunctive.json()["items"]] == ["both.pdf"]


@pytest.mark.asyncio
async def test_deleting_a_tag_keeps_the_documents_it_labelled(client, matter):
    tag = (await client.post("/api/document-tags", json={"name": "Signed"})).json()
    document = await _upload(client, matter, "settlement.pdf")
    await client.put(
        f"{API}/{matter.id}/documents/{document['id']}/tags",
        json={"tag_ids": [tag["id"]]},
    )

    removed = await client.delete(f"/api/document-tags/{tag['id']}")
    assert removed.status_code == 204

    listing = await client.get(f"{API}/{matter.id}/documents")
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["tags"] == []


# ── Tenant isolation ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_another_tenants_folder_and_tag_are_invisible(
    client, matter, db_session, test_tenant
):
    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Other Firm",
        domain=f"other-{uuid.uuid4().hex[:8]}.com",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other_tenant)
    await db_session.flush()
    other_user = User(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        email=f"lawyer-{uuid.uuid4().hex[:8]}@other.com",
        full_name="Other Attorney",
        role="admin",
        oauth_provider="google",
        oauth_subject=f"google-{uuid.uuid4().hex[:8]}",
        is_active=True,
    )
    db_session.add(other_user)
    other_matter_id = uuid.uuid4()
    other_matter = Matter(
        id=other_matter_id,
        tenant_id=other_tenant.id,
        user_id=other_user.id,
        slug=f"other-{uuid.uuid4().hex[:8]}",
        matter_name="Other Firm Matter",
        matter_type="litigation",
        status="open",
    )
    db_session.add(other_matter)
    await db_session.flush()
    foreign_folder_id = uuid.uuid4()
    foreign_folder = MatterDocumentFolder(
        id=foreign_folder_id,
        tenant_id=other_tenant.id,
        matter_id=other_matter_id,
        name="Their Discovery",
        path="Their Discovery",
        depth=0,
    )
    db_session.add(foreign_folder)
    await db_session.commit()

    # The other firm's matter is not even addressable from this session.
    listing = await client.get(f"{API}/{other_matter_id}/document-folders")
    assert listing.status_code == 404

    # Nor can its folder be borrowed as a filing target inside our own matter.
    document = await _upload(client, matter, "ours.pdf")
    move = await client.post(
        f"{API}/{matter.id}/documents/move",
        json={"document_ids": [document["id"]], "folder_id": str(foreign_folder_id)},
    )
    assert move.status_code == 404
    assert move.json()["detail"]["code"] == "folder_not_found"

    upload = await client.post(
        f"{API}/{matter.id}/documents/upload",
        files={"file": ("x.pdf", io.BytesIO(b"body"), "application/pdf")},
        data={"folder_id": str(foreign_folder_id)},
    )
    assert upload.status_code == 404

    # And our tag vocabulary stays ours.
    await client.post("/api/document-tags", json={"name": "Ours"})
    ours = await client.get("/api/document-tags")
    assert [t["name"] for t in ours.json()["items"]] == ["Ours"]


# ── Service-level rules ──────────────────────────────────────────────────────


def test_normalize_folder_name_collapses_internal_whitespace():
    assert normalize_folder_name("  Expert \t Reports  ") == "Expert Reports"


def test_system_folders_route_to_their_provisioned_cloud_subfolder():
    system = MatterDocumentFolder(
        name="Client Uploads",
        path="Client Uploads",
        depth=0,
        kind="system",
        system_key=SYSTEM_FOLDER_CLIENT_UPLOADS,
    )
    category, path = storage_routing_for_folder(system)
    assert category == SYSTEM_FOLDER_CLIENT_UPLOADS
    assert path is None

    user_folder = MatterDocumentFolder(
        name="Depositions", path="Discovery/Depositions", depth=1, kind="user"
    )
    category, path = storage_routing_for_folder(user_folder)
    assert category is None
    assert path == ["Discovery", "Depositions"]

    assert storage_routing_for_folder(None) == (None, None)


@pytest.mark.asyncio
async def test_ensure_system_folder_is_idempotent_and_adopts_a_matching_folder(
    db_session, test_tenant, matter
):
    hand_made = MatterDocumentFolder(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        matter_id=uuid.UUID(matter.id),
        name="Client Uploads",
        path="Client Uploads",
        depth=0,
    )
    db_session.add(hand_made)
    await db_session.commit()

    adopted = await ensure_system_folder(
        db_session,
        tenant_id=test_tenant.id,
        matter_id=uuid.UUID(matter.id),
        system_key=SYSTEM_FOLDER_CLIENT_UPLOADS,
    )
    assert adopted.id == hand_made.id
    assert adopted.kind == "system"

    again = await ensure_system_folder(
        db_session,
        tenant_id=test_tenant.id,
        matter_id=uuid.UUID(matter.id),
        system_key=SYSTEM_FOLDER_CLIENT_UPLOADS,
    )
    assert again.id == hand_made.id


@pytest.mark.asyncio
async def test_ensure_system_folder_rejects_an_unknown_key(
    db_session, test_tenant, matter
):
    with pytest.raises(DocumentOrganizationError) as excinfo:
        await ensure_system_folder(
            db_session,
            tenant_id=test_tenant.id,
            matter_id=uuid.UUID(matter.id),
            system_key="not_a_real_folder",
        )
    assert excinfo.value.code == "unknown_system_folder"
