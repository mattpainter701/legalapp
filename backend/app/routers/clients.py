"""Dedicated, tenant-scoped client CRM API."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user, require_admin
from app.models.contact import Contact
from app.models.matter_party import MatterParty
from app.models.plugin import Matter
from app.models.tenant import Tenant
from app.schemas.client import (
    ClientCreate,
    ClientImportError,
    ClientImportResponse,
    ClientListResponse,
    ClientQBOSyncResponse,
    ClientRelatedContactResponse,
    ClientResponse,
    ClientSummaryResponse,
    ClientUpdate,
)
from app.services.operator_audit import record_operator_audit

router = APIRouter(prefix="/api/clients", tags=["clients", "crm"])

CLIENT_CONTACT_TYPES = ("client", "prospect")
MAX_CSV_BYTES = 5 * 1024 * 1024
MAX_CSV_ROWS = 10_000
EXTERNAL_ID_FIELDS = {"qbo_customer_id", "stripe_customer_id"}

CSV_FIELDS = (
    "client_number",
    "client_status",
    "entity_type",
    "first_name",
    "last_name",
    "preferred_name",
    "organization_name",
    "email",
    "phone",
    "secondary_phone",
    "date_of_birth",
    "client_since",
    "address_street",
    "address_street2",
    "address_city",
    "address_state",
    "address_zip",
    "address_country",
    "preferred_contact_method",
    "preferred_contact_window",
    "preferred_contact_timezone",
    "preferred_language",
    "sms_opt_in",
    "email_opt_in",
    "emergency_contact_name",
    "emergency_contact_relationship",
    "emergency_contact_phone",
    "emergency_contact_email",
    "referral_source",
    "preferred_payment_method",
    "billing_delivery_method",
    "payment_terms_days",
    "billing_notes",
    "qbo_customer_id",
    "stripe_customer_id",
    "internal_notes",
    "tags",
)


def _demo_qbo_customer_id(client_id: uuid.UUID) -> str:
    """Return an ephemeral, obviously synthetic demo-operation identifier.

    It is returned to the caller and audit log, never persisted as a provider
    mapping on the canonical client record.
    """
    return f"DEMO-{client_id.hex.upper()}"


def _client_response(contact: Contact) -> ClientResponse:
    data = {
        column.name: getattr(contact, column.name)
        for column in contact.__table__.columns
    }
    data["display_name"] = contact.display_name
    return ClientResponse(**data)


async def _load_client(
    db: AsyncSession, tenant_id: uuid.UUID, client_id: uuid.UUID
) -> Contact:
    result = await db.execute(
        select(Contact).where(
            Contact.id == client_id,
            Contact.tenant_id == tenant_id,
            Contact.contact_type.in_(CLIENT_CONTACT_TYPES),
            Contact.client_account_id.is_(None),
        )
    )
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Client not found")
    return contact


async def _ensure_unique_client_number(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    client_number: str | None,
    exclude_id: uuid.UUID | None = None,
) -> None:
    if not client_number:
        return
    stmt = select(Contact.id).where(
        Contact.tenant_id == tenant_id,
        Contact.client_number == client_number,
    )
    if exclude_id:
        stmt = stmt.where(Contact.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Client number already exists")


def _apply_sms_consent(contact: Contact, sms_opt_in: bool) -> None:
    if sms_opt_in and not contact.sms_opt_in:
        contact.sms_opt_in_at = datetime.now(timezone.utc)
    elif not sms_opt_in:
        contact.sms_opt_in_at = None
    contact.sms_opt_in = sms_opt_in


def _validate_client_name(contact: Contact) -> None:
    if contact.entity_type == "organization":
        if not contact.organization_name:
            raise HTTPException(
                status_code=422,
                detail="organization_name is required for organizations",
            )
    elif not (contact.first_name or contact.last_name):
        raise HTTPException(
            status_code=422,
            detail="first_name or last_name is required for people",
        )


@router.get("", response_model=ClientListResponse)
async def list_clients(
    q: str | None = None,
    status: str | None = None,
    entity_type: str | None = None,
    sms_opt_in: bool | None = None,
    active_only: bool = True,
    sort: str = Query("name", pattern="^(name|newest|recently-contacted)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    stmt = select(Contact).where(
        Contact.tenant_id == tenant_id,
        Contact.contact_type.in_(CLIENT_CONTACT_TYPES),
        Contact.client_account_id.is_(None),
    )
    if active_only:
        stmt = stmt.where(Contact.is_active.is_(True))
    if status:
        stmt = stmt.where(Contact.client_status == status)
    if entity_type:
        stmt = stmt.where(Contact.entity_type == entity_type)
    if sms_opt_in is not None:
        stmt = stmt.where(Contact.sms_opt_in.is_(sms_opt_in))
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Contact.first_name.ilike(pattern),
                Contact.last_name.ilike(pattern),
                Contact.preferred_name.ilike(pattern),
                Contact.organization_name.ilike(pattern),
                Contact.email.ilike(pattern),
                Contact.phone.ilike(pattern),
                Contact.secondary_phone.ilike(pattern),
                Contact.client_number.ilike(pattern),
            )
        )

    total = (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    if sort == "newest":
        stmt = stmt.order_by(Contact.created_at.desc())
    elif sort == "recently-contacted":
        stmt = stmt.order_by(
            Contact.last_contacted_at.desc().nullslast(), Contact.updated_at.desc()
        )
    else:
        stmt = stmt.order_by(
            Contact.last_name, Contact.first_name, Contact.organization_name
        )
    contacts = (await db.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return ClientListResponse(
        items=[_client_response(c) for c in contacts], total=total
    )


@router.get("/summary", response_model=ClientSummaryResponse)
async def client_summary(
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    base = (
        Contact.tenant_id == tenant_id,
        Contact.contact_type.in_(CLIENT_CONTACT_TYPES),
        Contact.client_account_id.is_(None),
        Contact.is_active.is_(True),
    )
    row = (
        await db.execute(
            select(
                func.count(Contact.id),
                func.count(Contact.id).filter(Contact.client_status == "active"),
                func.count(Contact.id).filter(Contact.client_status == "prospect"),
                func.count(Contact.id).filter(Contact.client_status == "inactive"),
                func.count(Contact.id).filter(Contact.client_status == "former"),
                func.count(Contact.id).filter(Contact.sms_opt_in.is_(True)),
            ).where(*base)
        )
    ).one()
    return ClientSummaryResponse(
        total=row[0],
        active=row[1],
        prospects=row[2],
        inactive=row[3],
        former=row[4],
        sms_opted_in=row[5],
    )


@router.post("", response_model=ClientResponse, status_code=201)
async def create_client(
    payload: ClientCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = current_user.tenant_id
    await set_tenant_context(db, str(tenant_id))
    if EXTERNAL_ID_FIELDS.intersection(
        payload.model_fields_set
    ) and current_user.role not in {"admin", "accountant"}:
        raise HTTPException(
            status_code=403, detail="Billing integration fields require finance access"
        )
    await _ensure_unique_client_number(db, tenant_id, payload.client_number)
    data = payload.model_dump(exclude_none=True)
    sms_opt_in = data.pop("sms_opt_in", False)
    status = data.get("client_status", "active")
    contact = Contact(
        tenant_id=tenant_id,
        created_by_user_id=current_user.id,
        contact_type="prospect" if status == "prospect" else "client",
        **data,
    )
    _apply_sms_consent(contact, sms_opt_in)
    db.add(contact)
    await db.commit()
    await set_tenant_context(db, str(tenant_id))
    await db.refresh(contact)
    return _client_response(contact)


@router.get("/export.csv")
async def export_clients_csv(
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Export tenant client data. Admin-only because it contains sensitive PII."""
    await set_tenant_context(db, str(admin.tenant_id))
    contacts = (
        (
            await db.execute(
                select(Contact)
                .where(
                    Contact.tenant_id == admin.tenant_id,
                    Contact.contact_type.in_(CLIENT_CONTACT_TYPES),
                    Contact.is_active.is_(True),
                )
                .order_by(
                    Contact.last_name, Contact.first_name, Contact.organization_name
                )
            )
        )
        .scalars()
        .all()
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for contact in contacts:
        address = contact.address or {}
        emergency = contact.emergency_contact or {}
        writer.writerow(
            {
                "client_number": _csv_safe(contact.client_number),
                "client_status": contact.client_status or "",
                "entity_type": contact.entity_type,
                "first_name": _csv_safe(contact.first_name),
                "last_name": _csv_safe(contact.last_name),
                "preferred_name": _csv_safe(contact.preferred_name),
                "organization_name": _csv_safe(contact.organization_name),
                "email": _csv_safe(contact.email),
                "phone": _csv_safe(contact.phone),
                "secondary_phone": _csv_safe(contact.secondary_phone),
                "date_of_birth": contact.date_of_birth.isoformat()
                if contact.date_of_birth
                else "",
                "client_since": contact.client_since.isoformat()
                if contact.client_since
                else "",
                "address_street": _csv_safe(address.get("street")),
                "address_street2": _csv_safe(address.get("street2")),
                "address_city": _csv_safe(address.get("city")),
                "address_state": _csv_safe(address.get("state")),
                "address_zip": _csv_safe(address.get("zip")),
                "address_country": _csv_safe(address.get("country")),
                "preferred_contact_method": contact.preferred_contact_method or "",
                "preferred_contact_window": _csv_safe(contact.preferred_contact_window),
                "preferred_contact_timezone": _csv_safe(
                    contact.preferred_contact_timezone
                ),
                "preferred_language": _csv_safe(contact.preferred_language),
                "sms_opt_in": str(contact.sms_opt_in).lower(),
                "email_opt_in": str(contact.email_opt_in).lower(),
                "emergency_contact_name": _csv_safe(emergency.get("name")),
                "emergency_contact_relationship": _csv_safe(
                    emergency.get("relationship")
                ),
                "emergency_contact_phone": _csv_safe(emergency.get("phone")),
                "emergency_contact_email": _csv_safe(emergency.get("email")),
                "referral_source": _csv_safe(contact.referral_source),
                "preferred_payment_method": contact.preferred_payment_method or "",
                "billing_delivery_method": contact.billing_delivery_method,
                "payment_terms_days": contact.payment_terms_days,
                "billing_notes": _csv_safe(contact.billing_notes),
                "qbo_customer_id": _csv_safe(contact.qbo_customer_id),
                "stripe_customer_id": _csv_safe(contact.stripe_customer_id),
                "internal_notes": _csv_safe(contact.notes),
                "tags": _csv_safe(";".join(contact.tags or [])),
            }
        )
    filename = f"clients-{datetime.now(timezone.utc).date().isoformat()}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import.csv", response_model=ClientImportResponse)
async def import_clients_csv(
    file: UploadFile = File(...),
    update_existing: bool = Form(False),
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Import a bounded CSV, matching existing clients by client number or email."""
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="A .csv file is required")
    raw = await file.read(MAX_CSV_BYTES + 1)
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail="Client CSV exceeds the 5 MB limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400, detail="Client CSV must be UTF-8 encoded"
        ) from exc

    await set_tenant_context(db, str(admin.tenant_id))
    created = updated = skipped = 0
    errors: list[ClientImportError] = []
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(
            status_code=400, detail="Client CSV is missing a header row"
        )

    for row_number, row in enumerate(reader, start=2):
        if row_number - 1 > MAX_CSV_ROWS:
            raise HTTPException(
                status_code=413, detail="Client CSV exceeds the 10,000-row limit"
            )
        try:
            payload = ClientCreate.model_validate(_csv_row_payload(row))
            match_conditions = []
            if payload.client_number:
                match_conditions.append(Contact.client_number == payload.client_number)
            if payload.email:
                match_conditions.append(
                    func.lower(Contact.email) == str(payload.email).lower()
                )
            existing = None
            if match_conditions:
                existing = (
                    (
                        await db.execute(
                            select(Contact).where(
                                Contact.tenant_id == admin.tenant_id,
                                Contact.contact_type.in_(CLIENT_CONTACT_TYPES),
                                or_(*match_conditions),
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
            if existing and not update_existing:
                skipped += 1
                continue
            data = payload.model_dump(exclude_unset=True, exclude_none=True)
            sms_value = data.pop("sms_opt_in", None)
            if existing:
                await _ensure_unique_client_number(
                    db, admin.tenant_id, payload.client_number, existing.id
                )
                for field, value in data.items():
                    setattr(existing, field, value)
                if sms_value is not None:
                    _apply_sms_consent(existing, sms_value)
                existing.contact_type = (
                    "prospect" if existing.client_status == "prospect" else "client"
                )
                updated += 1
            else:
                await _ensure_unique_client_number(
                    db, admin.tenant_id, payload.client_number
                )
                status = data.get("client_status", "active")
                contact = Contact(
                    tenant_id=admin.tenant_id,
                    created_by_user_id=admin.id,
                    contact_type="prospect" if status == "prospect" else "client",
                    **data,
                )
                _apply_sms_consent(contact, bool(sms_value))
                db.add(contact)
                created += 1
            await db.flush()
        except (ValidationError, ValueError, HTTPException) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            errors.append(ClientImportError(row=row_number, detail=str(detail)[:500]))
            skipped += 1
            if len(errors) >= 100:
                break
    await db.commit()
    return ClientImportResponse(
        created=created, updated=updated, skipped=skipped, errors=errors
    )


@router.post("/{client_id}/sync/quickbooks", response_model=ClientQBOSyncResponse)
async def sync_client_quickbooks(
    client_id: uuid.UUID,
    request: Request,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Sync a live QBO customer or return an audited, ephemeral demo simulation."""
    await set_tenant_context(db, str(admin.tenant_id))
    billing_tier = await db.scalar(
        select(Tenant.billing_tier).where(Tenant.id == admin.tenant_id)
    )
    tenant_is_demo = billing_tier == "demo"
    claim_values = [
        value == "demo"
        for value in (
            getattr(request.state, "signed_plan", None),
            getattr(request.state, "signed_billing_tier", None),
        )
        if value is not None
    ]
    signed_demo = (
        claim_values[0]
        if claim_values and all(value == claim_values[0] for value in claim_values[1:])
        else None
    )
    operation_id = str(
        request.headers.get("x-idempotency-key")
        or getattr(request.state, "request_id", None)
        or uuid.uuid4()
    )[:200]
    if signed_demo is None or signed_demo != tenant_is_demo:
        await record_operator_audit(
            db,
            request,
            action="client.quickbooks_sync_denied",
            resource_type="client",
            resource_id=str(client_id),
            actor_type="user",
            actor_id=str(admin.id),
            metadata={
                "tenant_id": str(admin.tenant_id),
                "operation_id": operation_id,
                "signed_plan": getattr(request.state, "signed_plan", None),
                "signed_billing_tier": getattr(
                    request.state, "signed_billing_tier", None
                ),
                "database_billing_tier": billing_tier,
                "reason": "signed_claim_mismatch",
            },
        )
        await db.commit()
        raise HTTPException(
            status_code=403,
            detail=("Workspace access changed; sign in again before using QuickBooks"),
        )

    contact = await _load_client(db, admin.tenant_id, client_id)

    if tenant_is_demo:
        # Simulate the operation without creating a provider-looking mapping.
        # The audit record is durable; canonical QuickBooks fields remain the
        # sole evidence of a real provider-backed synchronization.
        simulated_id = _demo_qbo_customer_id(contact.id)
        simulated_at = datetime.now(timezone.utc)
        await record_operator_audit(
            db,
            request,
            action="client.quickbooks_sync_simulated",
            resource_type="client",
            resource_id=str(contact.id),
            actor_type="user",
            actor_id=str(admin.id),
            metadata={
                "tenant_id": str(admin.tenant_id),
                "operation_id": operation_id,
                "provider": "quickbooks",
                "provider_contacted": False,
                "is_simulated": True,
                "simulated_customer_id": simulated_id,
                "prior_qbo_customer_id": contact.qbo_customer_id,
                "canonical_mapping_changed": False,
            },
        )
        await db.commit()
        return ClientQBOSyncResponse(
            status="demo_simulated",
            client_id=contact.id,
            qbo_customer_id=simulated_id,
            synced_at=simulated_at,
            is_simulated=True,
            detail=(
                f"Simulated QuickBooks sync as customer {simulated_id}. "
                "No client mapping was changed and QuickBooks was not contacted."
            ),
        )

    from app.routers.qbo import _get_fresh_qbo_token, _get_qbo_integration
    from app.services.qbo_sync import QBOSyncService

    access_token = await _get_fresh_qbo_token(db, str(admin.tenant_id))
    integration = await _get_qbo_integration(db, str(admin.tenant_id))
    if not access_token or not integration or not integration.qbo_realm_id:
        raise HTTPException(status_code=400, detail="QuickBooks is not connected")

    service = QBOSyncService(
        db,
        str(admin.tenant_id),
        access_token,
        sandbox=integration.sandbox_mode,
    )
    customer = None
    if contact.qbo_customer_id:
        current = await service._request(
            "GET",
            service._api_url(
                integration.qbo_realm_id, "customer", contact.qbo_customer_id
            ),
        )
        customer = current.get("Customer") if current else None
    if not customer:
        safe_name = service._safe_qbo_string(contact.display_name)
        existing = await service._request(
            "GET",
            service._api_url(integration.qbo_realm_id, "query"),
            params={
                "query": f"SELECT * FROM Customer WHERE DisplayName = '{safe_name}'"
            },
        )
        matches = (
            existing.get("QueryResponse", {}).get("Customer", []) if existing else []
        )
        customer = matches[0] if matches else None

    payload = _qbo_customer_payload(contact)
    if customer:
        payload.update(
            {
                "Id": customer["Id"],
                "SyncToken": customer.get("SyncToken", "0"),
                "sparse": True,
            }
        )
    result = await service._request(
        "POST",
        service._api_url(integration.qbo_realm_id, "customer"),
        json_data=payload,
    )
    synced = result.get("Customer") if result else None
    if not synced or not synced.get("Id"):
        raise HTTPException(status_code=502, detail="QuickBooks client sync failed")
    contact.qbo_customer_id = synced["Id"]
    contact.qbo_sync_token = synced.get("SyncToken")
    contact.qbo_synced_at = datetime.now(timezone.utc)
    await db.commit()
    return ClientQBOSyncResponse(
        status="synced",
        client_id=contact.id,
        qbo_customer_id=contact.qbo_customer_id,
        synced_at=contact.qbo_synced_at,
    )


@router.get("/{client_id}", response_model=ClientResponse)
async def get_client(
    client_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    return _client_response(await _load_client(db, current_user.tenant_id, client_id))


@router.get("/{client_id}/contacts", response_model=list[ClientRelatedContactResponse])
async def client_related_contacts(
    client_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    await _load_client(db, current_user.tenant_id, client_id)
    contacts = (
        (
            await db.execute(
                select(Contact)
                .where(
                    Contact.tenant_id == current_user.tenant_id,
                    Contact.client_account_id == client_id,
                    Contact.is_active.is_(True),
                )
                .order_by(
                    Contact.is_primary_client_contact.desc(),
                    Contact.last_name,
                    Contact.first_name,
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        ClientRelatedContactResponse(
            id=contact.id,
            entity_type=contact.entity_type,
            first_name=contact.first_name,
            last_name=contact.last_name,
            preferred_name=contact.preferred_name,
            organization_name=contact.organization_name,
            display_name=contact.display_name,
            email=contact.email,
            phone=contact.phone,
            secondary_phone=contact.secondary_phone,
            preferred_contact_method=contact.preferred_contact_method,
            client_contact_role=contact.client_contact_role,
            is_primary_client_contact=contact.is_primary_client_contact,
            client_contact_authorization=contact.client_contact_authorization,
        )
        for contact in contacts
    ]


@router.patch("/{client_id}", response_model=ClientResponse)
async def update_client(
    client_id: uuid.UUID,
    payload: ClientUpdate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    contact = await _load_client(db, current_user.tenant_id, client_id)
    if EXTERNAL_ID_FIELDS.intersection(
        payload.model_fields_set
    ) and current_user.role not in {"admin", "accountant"}:
        raise HTTPException(
            status_code=403, detail="Billing integration fields require finance access"
        )
    if "client_number" in payload.model_fields_set:
        await _ensure_unique_client_number(
            db, current_user.tenant_id, payload.client_number, contact.id
        )
    data = payload.model_dump(exclude_unset=True)
    sms_value = data.pop("sms_opt_in", None)
    for field, value in data.items():
        setattr(contact, field, value)
    if sms_value is not None:
        _apply_sms_consent(contact, sms_value)
    _validate_client_name(contact)
    if contact.client_status:
        contact.contact_type = (
            "prospect" if contact.client_status == "prospect" else "client"
        )
    await db.commit()
    await set_tenant_context(db, str(current_user.tenant_id))
    await db.refresh(contact)
    return _client_response(contact)


@router.delete("/{client_id}", status_code=204)
async def archive_client(
    client_id: uuid.UUID,
    admin=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(admin.tenant_id))
    contact = await _load_client(db, admin.tenant_id, client_id)
    contact.is_active = False
    contact.client_status = "inactive"
    await db.commit()


@router.get("/{client_id}/matters")
async def client_matters(
    client_id: uuid.UUID,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await set_tenant_context(db, str(current_user.tenant_id))
    await _load_client(db, current_user.tenant_id, client_id)
    related_contact_ids = select(Contact.id).where(
        Contact.tenant_id == current_user.tenant_id,
        Contact.client_account_id == client_id,
    )
    related_matter_ids = select(MatterParty.matter_id).where(
        MatterParty.tenant_id == current_user.tenant_id,
        MatterParty.contact_id.in_(related_contact_ids),
    )
    matters = (
        (
            await db.execute(
                select(Matter)
                .where(
                    Matter.tenant_id == current_user.tenant_id,
                    or_(
                        Matter.client_contact_id == client_id,
                        Matter.id.in_(related_matter_ids),
                    ),
                )
                .order_by(Matter.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(matter.id),
            "matter_name": matter.matter_name,
            "matter_type": matter.matter_type,
            "status": matter.status,
            "jurisdiction": matter.jurisdiction,
            "created_at": matter.created_at.isoformat(),
        }
        for matter in matters
    ]


def _csv_safe(value) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def _parse_bool(value: str, field: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"{field} must be true/false, yes/no, or 1/0")


def _csv_row_payload(row: dict) -> dict:
    values = {
        (key or "").strip().lower(): (value or "").strip() for key, value in row.items()
    }
    aliases = {
        "phone_1": "phone",
        "phone_2": "secondary_phone",
        "dob": "date_of_birth",
        "notes": "internal_notes",
    }
    for source, target in aliases.items():
        if values.get(source) and not values.get(target):
            values[target] = values[source]

    scalar_fields = (
        "client_number",
        "client_status",
        "entity_type",
        "first_name",
        "last_name",
        "preferred_name",
        "organization_name",
        "email",
        "phone",
        "secondary_phone",
        "date_of_birth",
        "client_since",
        "preferred_contact_method",
        "preferred_contact_window",
        "preferred_contact_timezone",
        "preferred_language",
        "referral_source",
        "preferred_payment_method",
        "billing_delivery_method",
        "billing_notes",
        "qbo_customer_id",
        "stripe_customer_id",
    )
    data = {field: values[field] for field in scalar_fields if values.get(field)}
    if "entity_type" not in data:
        data["entity_type"] = (
            "organization"
            if values.get("organization_name")
            and not (values.get("first_name") or values.get("last_name"))
            else "person"
        )
    if values.get("payment_terms_days"):
        data["payment_terms_days"] = int(values["payment_terms_days"])
    for field in ("sms_opt_in", "email_opt_in"):
        if values.get(field):
            data[field] = _parse_bool(values[field], field)
    if values.get("internal_notes"):
        data["notes"] = values["internal_notes"]
    if values.get("tags"):
        data["tags"] = [tag.strip() for tag in values["tags"].split(";") if tag.strip()]
    address = {
        field: values.get(f"address_{field}") or None
        for field in ("street", "street2", "city", "state", "zip", "country")
    }
    if any(address.values()):
        data["address"] = address
    emergency = {
        "name": values.get("emergency_contact_name") or None,
        "relationship": values.get("emergency_contact_relationship") or None,
        "phone": values.get("emergency_contact_phone") or None,
        "email": values.get("emergency_contact_email") or None,
    }
    if any(emergency.values()):
        data["emergency_contact"] = emergency
    return data


def _qbo_customer_payload(contact: Contact) -> dict:
    payload: dict = {"DisplayName": contact.display_name[:100]}
    if contact.entity_type == "organization":
        payload["CompanyName"] = (contact.organization_name or contact.display_name)[
            :100
        ]
    else:
        if contact.first_name:
            payload["GivenName"] = contact.first_name[:100]
        if contact.last_name:
            payload["FamilyName"] = contact.last_name[:100]
    if contact.email:
        payload["PrimaryEmailAddr"] = {"Address": contact.email}
    if contact.phone:
        payload["PrimaryPhone"] = {"FreeFormNumber": contact.phone}
    address = contact.address or {}
    if any(address.values()):
        bill_address = {
            "Line1": address.get("street"),
            "Line2": address.get("street2"),
            "City": address.get("city"),
            "CountrySubDivisionCode": address.get("state"),
            "PostalCode": address.get("zip"),
            "Country": address.get("country"),
        }
        payload["BillAddr"] = {
            key: value for key, value in bill_address.items() if value
        }
    return payload
