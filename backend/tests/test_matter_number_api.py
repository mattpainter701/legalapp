"""The matter number over HTTP: stamped at creation, readable, resolvable.

These cover what issue #407 asks for -- a number that appears in every matter
response and turns a human-readable URL back into a matter -- plus the bound
that makes it safe to put in a URL at all: resolution never crosses a tenant.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.plugin import Matter
from app.models.tenant import Tenant
from app.models.user import User


async def _create_matter(client, name="Smith v. Jones"):
    response = await client.post("/api/matters", json={"matter_name": name})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_a_new_matter_is_numbered_on_creation(client, test_tenant):
    body = await _create_matter(client)
    # "Test Law Firm" -> TEST, first matter -> 0001.
    assert body["matter_number"] == "TEST0001"


@pytest.mark.asyncio
async def test_numbers_advance_across_matters(client):
    first = await _create_matter(client, "First")
    second = await _create_matter(client, "Second")
    assert first["matter_number"] == "TEST0001"
    assert second["matter_number"] == "TEST0002"


@pytest.mark.asyncio
async def test_the_number_travels_with_every_matter_view(client):
    created = await _create_matter(client)

    detail = await client.get(f"/api/matters/{created['id']}")
    assert detail.json()["matter_number"] == created["matter_number"]

    listing = await client.get("/api/matters")
    assert listing.status_code == 200
    numbers = [item["matter_number"] for item in listing.json()["items"]]
    assert created["matter_number"] in numbers

    mine = await client.get("/api/matters/my")
    assert mine.status_code == 200
    assert created["matter_number"] in [item["matter_number"] for item in mine.json()]


@pytest.mark.asyncio
async def test_a_number_resolves_to_its_matter(client):
    created = await _create_matter(client)

    response = await client.get(f"/api/matters/by-number/{created['matter_number']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert response.json()["matter_name"] == "Smith v. Jones"


@pytest.mark.asyncio
async def test_a_number_resolves_however_it_was_typed(client):
    created = await _create_matter(client)

    # Lowercased by an address bar, hyphenated by an email client, both.
    for typed in ("test0001", "TEST-0001", "test-0001"):
        response = await client.get(f"/api/matters/by-number/{typed}")
        assert response.status_code == 200, typed
        assert response.json()["id"] == created["id"]


@pytest.mark.asyncio
async def test_an_unissued_number_is_a_miss_not_an_error(client):
    response = await client.get("/api/matters/by-number/TEST9999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_malformed_number_is_a_miss_not_an_error(client):
    # Never a 500: the value comes straight from a URL anyone can type.
    response = await client.get("/api/matters/by-number/not-a-number")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_non_uuid_matter_id_is_a_miss_not_an_error(client):
    # Previously this reached Postgres as a uuid cast and failed there.
    response = await client.get("/api/matters/SMIT0001")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_one_firms_number_never_resolves_inside_another(
    client, db_session, test_tenant
):
    """The number is in the URL, so tenant scoping is the whole safety story.

    Two firms both start at 0001. Signed in as one, the other's identical
    number must be a miss -- not someone else's matter.
    """
    created = await _create_matter(client)

    other_tenant = Tenant(
        id=uuid.uuid4(),
        name="Test Law Firm",
        domain="other-testfirm.example",
        billing_tier="payg",
        is_active=True,
    )
    db_session.add(other_tenant)
    await db_session.flush()
    other_user = User(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        email="attorney@other-testfirm.example",
        full_name="Other Attorney",
        role="admin",
        oauth_provider="google",
        oauth_subject="google-sub-other",
        is_active=True,
    )
    db_session.add(other_user)
    await db_session.flush()

    other_matter = Matter(
        tenant_id=other_tenant.id,
        user_id=other_user.id,
        slug="other-firm-matter",
        matter_name="Someone else's matter",
        # Deliberately the same string the signed-in firm issued.
        matter_number=created["matter_number"],
        matter_number_seq=1,
    )
    db_session.add(other_matter)
    await db_session.commit()

    response = await client.get(f"/api/matters/by-number/{created['matter_number']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert response.json()["id"] != str(other_matter.id)


@pytest.mark.asyncio
async def test_the_number_cannot_be_set_or_changed_through_the_api(client, db_session):
    created = await _create_matter(client)
    issued = created["matter_number"]

    # No create or update schema carries the field, so a client sending one is
    # ignored rather than obeyed.
    response = await client.patch(
        f"/api/matters/{created['id']}",
        json={"matter_name": "Renamed", "matter_number": "HACK0001"},
    )
    assert response.status_code == 200
    assert response.json()["matter_name"] == "Renamed"
    assert response.json()["matter_number"] == issued

    stored = (
        await db_session.execute(
            select(Matter.matter_number).where(Matter.id == uuid.UUID(created["id"]))
        )
    ).scalar_one()
    assert stored == issued


@pytest.mark.asyncio
async def test_a_created_matter_records_its_sequence(client, db_session):
    created = await _create_matter(client)
    stored = (
        await db_session.execute(
            select(Matter.matter_number_seq).where(
                Matter.id == uuid.UUID(created["id"])
            )
        )
    ).scalar_one()
    assert stored == 1


@pytest.mark.asyncio
async def test_the_tenant_counter_tracks_what_was_issued(
    client, db_session, test_tenant
):
    await _create_matter(client, "First")
    await _create_matter(client, "Second")

    counter = (
        await db_session.execute(
            select(Tenant.matter_sequence_counter).where(Tenant.id == test_tenant.id)
        )
    ).scalar_one()
    assert counter == 2
