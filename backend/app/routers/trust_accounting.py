"""Trust accounting router — IOLTA trust accounts, transactions, three-way reconciliation."""

import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.trust_accounting import TrustAccount, TrustTransaction
from app.models.plugin import Matter
from app.schemas.trust_accounting import (
    TrustAccountCreate,
    TrustAccountUpdate,
    TrustAccountResponse,
    TrustAccountListResponse,
    TrustTransactionCreate,
    TrustTransactionResponse,
    TrustTransactionListResponse,
    ReconciliationRequest,
    ReconciliationResponse,
    ReconciliationLine,
)

router = APIRouter(prefix="/api/trust", tags=["trust accounting"])
logger = logging.getLogger(__name__)

VALID_TRANSACTION_TYPES = {
    "deposit",
    "disbursement",
    "transfer_in",
    "transfer_out",
    "replenishment",
    "fee",
    "adjustment",
}

# Types that increase the trust balance
CREDIT_TYPES = {"deposit", "transfer_in", "replenishment"}
# Types that decrease the trust balance
DEBIT_TYPES = {"disbursement", "transfer_out", "fee"}


# ── Trust Accounts ───────────────────────────────────────────────────────────


@router.post("/accounts", status_code=201)
async def create_trust_account(
    body: TrustAccountCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TrustAccountResponse:
    """Create a trust (IOLTA) account for a matter."""
    user = await get_current_user(request, db)

    # Verify matter belongs to tenant
    await set_tenant_context(db, str(user.tenant_id))
    matter = await db.execute(
        select(Matter).where(
            Matter.id == body.matter_id,
            Matter.tenant_id == user.tenant_id,
        )
    )
    if not matter.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Matter not found")

    # One trust account per matter per tenant
    existing = await db.execute(
        select(TrustAccount).where(
            TrustAccount.tenant_id == user.tenant_id,
            TrustAccount.matter_id == body.matter_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="A trust account already exists for this matter",
        )

    account = TrustAccount(
        tenant_id=user.tenant_id,
        matter_id=uuid.UUID(body.matter_id),
        account_name=body.account_name,
        bank_name=body.bank_name,
        account_number_masked=body.account_number_masked,
        minimum_balance=body.minimum_balance,
        auto_replenish_enabled=body.auto_replenish_enabled,
        auto_replenish_amount=body.auto_replenish_amount,
        notes=body.notes,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)

    return TrustAccountResponse.model_validate(account)


@router.get("/accounts")
async def list_trust_accounts(
    matter_id: str | None = Query(None),
    is_active: bool | None = Query(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> TrustAccountListResponse:
    """List trust accounts with optional filters."""
    user = await get_current_user(request, db)

    stmt = select(TrustAccount).where(TrustAccount.tenant_id == user.tenant_id)
    if matter_id:
        stmt = stmt.where(TrustAccount.matter_id == matter_id)
    if is_active is not None:
        stmt = stmt.where(TrustAccount.is_active == is_active)

    stmt = stmt.order_by(TrustAccount.account_name)

    result = await db.execute(stmt)
    accounts = result.scalars().all()

    total_balance = sum((a.current_balance for a in accounts), Decimal("0"))

    return TrustAccountListResponse(
        items=[TrustAccountResponse.model_validate(a) for a in accounts],
        total=len(accounts),
        total_balance=total_balance,
    )


@router.get("/accounts/{account_id}")
async def get_trust_account(
    account_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TrustAccountResponse:
    """Get a single trust account by ID."""
    user = await get_current_user(request, db)

    result = await db.execute(
        select(TrustAccount).where(
            TrustAccount.id == account_id,
            TrustAccount.tenant_id == user.tenant_id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Trust account not found")

    return TrustAccountResponse.model_validate(account)


@router.patch("/accounts/{account_id}")
async def update_trust_account(
    account_id: str,
    body: TrustAccountUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TrustAccountResponse:
    """Update a trust account."""
    user = await get_current_user(request, db)

    result = await db.execute(
        select(TrustAccount).where(
            TrustAccount.id == account_id,
            TrustAccount.tenant_id == user.tenant_id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Trust account not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(account, key, value)

    await db.commit()
    await db.refresh(account)
    return TrustAccountResponse.model_validate(account)


@router.post("/accounts/{account_id}/close")
async def close_trust_account(
    account_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TrustAccountResponse:
    """Close a trust account. Only accounts with zero balance can be closed."""
    user = await get_current_user(request, db)

    result = await db.execute(
        select(TrustAccount).where(
            TrustAccount.id == account_id,
            TrustAccount.tenant_id == user.tenant_id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Trust account not found")

    if account.current_balance != 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot close trust account with non-zero balance: ${account.current_balance}",
        )

    account.is_active = False
    await db.commit()
    await db.refresh(account)
    return TrustAccountResponse.model_validate(account)


# ── Trust Transactions ────────────────────────────────────────────────────────


@router.post("/transactions", status_code=201)
async def create_trust_transaction(
    body: TrustTransactionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TrustTransactionResponse:
    """Record a trust account transaction (deposit, disbursement, transfer, etc.)."""
    user = await get_current_user(request, db)

    if body.transaction_type not in VALID_TRANSACTION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid transaction type: {body.transaction_type}. "
            f"Must be one of: {', '.join(sorted(VALID_TRANSACTION_TYPES))}",
        )

    # Verify trust account belongs to tenant
    account_result = await db.execute(
        select(TrustAccount).where(
            TrustAccount.id == body.trust_account_id,
            TrustAccount.tenant_id == user.tenant_id,
        )
    )
    account = account_result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Trust account not found")

    if not account.is_active:
        raise HTTPException(status_code=400, detail="Trust account is closed")

    # Prevent overdraw on debit transactions
    if body.transaction_type in DEBIT_TYPES:
        if account.current_balance < body.amount:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient trust balance. Available: ${account.current_balance}, "
                f"Requested: ${body.amount}",
            )

    txn_date = body.transaction_date or date.today()

    transaction = TrustTransaction(
        tenant_id=user.tenant_id,
        trust_account_id=uuid.UUID(body.trust_account_id),
        transaction_type=body.transaction_type,
        amount=body.amount,
        description=body.description,
        transaction_date=txn_date,
        reference_number=body.reference_number,
        check_number=body.check_number,
        notes=body.notes,
        created_by=user.id,
    )
    db.add(transaction)

    # Update running balance
    if body.transaction_type in CREDIT_TYPES:
        account.current_balance += body.amount
    elif body.transaction_type in DEBIT_TYPES:
        account.current_balance -= body.amount
    # "adjustment" can go either way — handled as a signed amount by caller

    await db.commit()
    await db.refresh(transaction)

    return TrustTransactionResponse.model_validate(transaction)


@router.get("/transactions")
async def list_trust_transactions(
    trust_account_id: str | None = Query(None),
    transaction_type: str | None = Query(None),
    is_reconciled: bool | None = Query(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db),
) -> TrustTransactionListResponse:
    """List trust transactions with optional filters."""
    user = await get_current_user(request, db)

    stmt = select(TrustTransaction).where(TrustTransaction.tenant_id == user.tenant_id)
    if trust_account_id:
        stmt = stmt.where(TrustTransaction.trust_account_id == trust_account_id)
    if transaction_type:
        stmt = stmt.where(TrustTransaction.transaction_type == transaction_type)
    if is_reconciled is not None:
        stmt = stmt.where(TrustTransaction.is_reconciled == is_reconciled)

    stmt = stmt.order_by(
        TrustTransaction.transaction_date.desc(),
        TrustTransaction.created_at.desc(),
    )

    result = await db.execute(stmt)
    transactions = result.scalars().all()

    total_deposits = sum(
        (t.amount for t in transactions if t.transaction_type in CREDIT_TYPES),
        Decimal("0"),
    )
    total_disbursements = sum(
        (t.amount for t in transactions if t.transaction_type in DEBIT_TYPES),
        Decimal("0"),
    )

    return TrustTransactionListResponse(
        items=[TrustTransactionResponse.model_validate(t) for t in transactions],
        total=len(transactions),
        total_deposits=total_deposits,
        total_disbursements=total_disbursements,
        net_change=total_deposits - total_disbursements,
    )


# ── Three-Way Reconciliation ─────────────────────────────────────────────────
#
# The three components of IOLTA three-way reconciliation:
#
#   1. Bank balance         — the balance per the bank statement
#   2. Trust liability      — sum of all matter trust account balances
#                              (the firm's obligation to clients)
#   3. Unallocated funds    — funds received but not yet assigned to a matter
#
#   Reconciled when: bank_balance == trust_liability + unallocated
#
#   Adjustments (outstanding items):
#     adjusted_bank = bank_balance + outstanding_deposits - outstanding_disbursements
#


@router.post("/accounts/{account_id}/reconcile")
async def reconcile_trust_account(
    account_id: str,
    body: ReconciliationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ReconciliationResponse:
    """Perform three-way reconciliation on a trust account."""
    user = await get_current_user(request, db)

    # Load trust account
    account_result = await db.execute(
        select(TrustAccount).where(
            TrustAccount.id == account_id,
            TrustAccount.tenant_id == user.tenant_id,
        )
    )
    account = account_result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Trust account not found")

    as_of_date = body.as_of_date or date.today()

    # Trust liability = current balance of this trust account
    trust_liability = account.current_balance

    # Unallocated = sum of unreconciled deposits (funds in but not yet applied)
    unallocated_result = await db.execute(
        select(func.sum(TrustTransaction.amount)).where(
            TrustTransaction.trust_account_id == uuid.UUID(account_id),
            TrustTransaction.transaction_type == "deposit",
            TrustTransaction.is_reconciled.is_(False),
        )
    )
    unallocated = unallocated_result.scalar() or Decimal("0")

    # Adjusted bank balance
    adjusted_bank = (
        body.bank_balance + body.outstanding_deposits - body.outstanding_disbursements
    )

    difference = adjusted_bank - (trust_liability + unallocated)
    is_reconciled = difference == Decimal("0")

    # Build reconciling items list
    reconciling_items = []

    # Unreconciled transactions
    unreconciled_result = await db.execute(
        select(TrustTransaction)
        .where(
            TrustTransaction.trust_account_id == uuid.UUID(account_id),
            TrustTransaction.is_reconciled.is_(False),
        )
        .order_by(TrustTransaction.transaction_date)
    )
    for txn in unreconciled_result.scalars().all():
        sign = Decimal("1") if txn.transaction_type in CREDIT_TYPES else Decimal("-1")
        reconciling_items.append(
            ReconciliationLine(
                description=f"[{txn.transaction_type}] {txn.description}",
                amount=txn.amount * sign,
                is_outstanding=True,
            )
        )

    if body.outstanding_deposits:
        reconciling_items.append(
            ReconciliationLine(
                description="Outstanding deposits (not yet on bank statement)",
                amount=body.outstanding_deposits,
                is_outstanding=True,
            )
        )

    if body.outstanding_disbursements:
        reconciling_items.append(
            ReconciliationLine(
                description="Outstanding disbursements (not yet cleared)",
                amount=-body.outstanding_disbursements,
                is_outstanding=True,
            )
        )

    # If reconciled, mark all unreconciled transactions as reconciled
    reconciled_at = None
    if is_reconciled:
        reconciled_at = datetime.now(timezone.utc)
        mark_result = await db.execute(
            select(TrustTransaction).where(
                TrustTransaction.trust_account_id == uuid.UUID(account_id),
                TrustTransaction.is_reconciled.is_(False),
            )
        )
        for txn in mark_result.scalars().all():
            txn.is_reconciled = True
            txn.reconciled_at = reconciled_at
        await db.commit()

    return ReconciliationResponse(
        trust_account_id=str(account.id),
        as_of_date=as_of_date,
        bank_balance=body.bank_balance,
        trust_liability=trust_liability,
        unallocated=unallocated,
        outstanding_deposits=body.outstanding_deposits,
        outstanding_disbursements=body.outstanding_disbursements,
        adjusted_bank_balance=adjusted_bank,
        is_reconciled=is_reconciled,
        difference=difference,
        reconciling_items=reconciling_items,
        notes=body.notes,
        reconciled_at=reconciled_at,
    )


@router.get("/accounts/{account_id}/reconciliation")
async def get_reconciliation_status(
    account_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ReconciliationResponse:
    """Get current reconciliation status without performing one."""
    user = await get_current_user(request, db)

    account_result = await db.execute(
        select(TrustAccount).where(
            TrustAccount.id == account_id,
            TrustAccount.tenant_id == user.tenant_id,
        )
    )
    account = account_result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Trust account not found")

    # Unreconciled item counts
    unreconciled_result = await db.execute(
        select(TrustTransaction)
        .where(
            TrustTransaction.trust_account_id == uuid.UUID(account_id),
            TrustTransaction.is_reconciled.is_(False),
        )
        .order_by(TrustTransaction.transaction_date)
    )
    unreconciled = unreconciled_result.scalars().all()

    reconciling_items = []
    for txn in unreconciled:
        sign = Decimal("1") if txn.transaction_type in CREDIT_TYPES else Decimal("-1")
        reconciling_items.append(
            ReconciliationLine(
                description=f"[{txn.transaction_type}] {txn.description}",
                amount=txn.amount * sign,
                is_outstanding=True,
            )
        )

    # Count by type
    outstanding_deposits = sum(
        (t.amount for t in unreconciled if t.transaction_type == "deposit"),
        Decimal("0"),
    )
    outstanding_disbursements = sum(
        (t.amount for t in unreconciled if t.transaction_type == "disbursement"),
        Decimal("0"),
    )

    trust_liability = account.current_balance

    # Unallocated deposits
    unallocated_result = await db.execute(
        select(func.sum(TrustTransaction.amount)).where(
            TrustTransaction.trust_account_id == uuid.UUID(account_id),
            TrustTransaction.transaction_type == "deposit",
            TrustTransaction.is_reconciled.is_(False),
        )
    )
    unallocated = unallocated_result.scalar() or Decimal("0")

    return ReconciliationResponse(
        trust_account_id=str(account.id),
        as_of_date=date.today(),
        bank_balance=account.current_balance,  # placeholder — real bank balance comes from statement
        trust_liability=trust_liability,
        unallocated=unallocated,
        outstanding_deposits=outstanding_deposits,
        outstanding_disbursements=outstanding_disbursements,
        adjusted_bank_balance=account.current_balance
        + outstanding_deposits
        - outstanding_disbursements,
        is_reconciled=len(unreconciled) == 0,
        difference=Decimal("0"),
        reconciling_items=reconciling_items,
    )
