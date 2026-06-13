"""Trust accounting router — IOLTA trust accounts, transactions, three-way reconciliation."""

import csv
import io
import logging
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, set_tenant_context
from app.middleware.tenant import get_current_user
from app.models.trust_accounting import (
    TrustAccount,
    TrustTransaction,
    TrustBankAccount,
    TrustReconciliation,
)
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
    TrustBankAccountCreate,
    TrustBankAccountUpdate,
    TrustBankAccountResponse,
    TrustBankAccountListResponse,
    PooledReconciliationRequest,
    TrustReconciliationSnapshot,
    TrustLedgerStatementLine,
    TrustLedgerStatementResponse,
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
    request: Request,
    matter_id: str | None = Query(None),
    is_active: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> TrustAccountListResponse:
    """List trust accounts with optional filters."""
    user = await get_current_user(request, db)

    stmt = select(TrustAccount).where(TrustAccount.tenant_id == user.tenant_id)
    if matter_id:
        stmt = stmt.where(TrustAccount.matter_id == matter_id)
    if is_active is not None:
        stmt = stmt.where(TrustAccount.is_active.is_(is_active))

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
        if key == "bank_account_id" and value is not None:
            value = uuid.UUID(value)
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
        select(TrustAccount)
        .where(
            TrustAccount.id == body.trust_account_id,
            TrustAccount.tenant_id == user.tenant_id,
        )
        .with_for_update()
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
    elif body.transaction_type == "adjustment":
        if body.amount > 0:
            account.current_balance += body.amount
        elif body.amount < 0:
            account.current_balance -= abs(body.amount)

    await db.commit()
    await db.refresh(transaction)

    return TrustTransactionResponse.model_validate(transaction)


@router.get("/transactions")
async def list_trust_transactions(
    request: Request,
    trust_account_id: str | None = Query(None),
    transaction_type: str | None = Query(None),
    is_reconciled: bool | None = Query(None),
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
        stmt = stmt.where(TrustTransaction.is_reconciled.is_(is_reconciled))

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

    # Persist a reconciliation snapshot (trust_account_id set, bank_account_id
    # left null — this is a per-matter client-ledger reconciliation).
    snapshot = TrustReconciliation(
        tenant_id=user.tenant_id,
        bank_account_id=None,
        trust_account_id=account.id,
        as_of_date=as_of_date,
        bank_balance=body.bank_balance,
        book_balance=trust_liability,
        trust_liability=trust_liability,
        unallocated=unallocated,
        outstanding_deposits=body.outstanding_deposits,
        outstanding_disbursements=body.outstanding_disbursements,
        adjusted_bank_balance=adjusted_bank,
        difference=difference,
        is_reconciled=is_reconciled,
        reconciling_items=[line.model_dump(mode="json") for line in reconciling_items],
        notes=body.notes,
        reconciled_by=user.id,
    )
    db.add(snapshot)
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


# ── Pooled Trust Bank Accounts ───────────────────────────────────────────────
#
# A pooled bank account represents a single real-world IOLTA bank account.
# Multiple per-matter client ledgers (TrustAccount rows) can be linked to one
# pooled bank account via TrustAccount.bank_account_id. The "book balance" of
# a pooled bank account is the sum of the current_balance of every linked
# client ledger — i.e. the firm's total trust liability for that bank account.


async def _bank_account_book_balance(
    db: AsyncSession, tenant_id: uuid.UUID, bank_account_id: uuid.UUID
) -> tuple[Decimal, int]:
    """Compute (book_balance, client_ledger_count) for a pooled bank account."""
    result = await db.execute(
        select(
            func.coalesce(func.sum(TrustAccount.current_balance), 0),
            func.count(TrustAccount.id),
        ).where(
            TrustAccount.tenant_id == tenant_id,
            TrustAccount.bank_account_id == bank_account_id,
        )
    )
    total, count = result.one()
    return Decimal(total), int(count)


def _bank_account_response(
    account: "TrustBankAccount", book_balance: Decimal, count: int
) -> TrustBankAccountResponse:
    """Build a TrustBankAccountResponse from an ORM row + computed balances."""
    return TrustBankAccountResponse(
        id=str(account.id),
        tenant_id=str(account.tenant_id),
        account_name=account.account_name,
        bank_name=account.bank_name,
        account_number_masked=account.account_number_masked,
        is_active=account.is_active,
        notes=account.notes,
        created_at=account.created_at,
        updated_at=account.updated_at,
        book_balance=book_balance,
        client_ledger_count=count,
    )


@router.post("/bank-accounts", status_code=201)
async def create_trust_bank_account(
    body: TrustBankAccountCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TrustBankAccountResponse:
    """Create a pooled IOLTA bank account."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    bank_account = TrustBankAccount(
        tenant_id=user.tenant_id,
        account_name=body.account_name,
        bank_name=body.bank_name,
        account_number_masked=body.account_number_masked,
        notes=body.notes,
    )
    db.add(bank_account)
    await db.commit()
    await db.refresh(bank_account)

    return _bank_account_response(bank_account, Decimal("0"), 0)


@router.get("/bank-accounts")
async def list_trust_bank_accounts(
    request: Request,
    is_active: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> TrustBankAccountListResponse:
    """List pooled IOLTA bank accounts with computed book balances."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    stmt = select(TrustBankAccount).where(TrustBankAccount.tenant_id == user.tenant_id)
    if is_active is not None:
        stmt = stmt.where(TrustBankAccount.is_active.is_(is_active))
    stmt = stmt.order_by(TrustBankAccount.account_name)

    result = await db.execute(stmt)
    accounts = result.scalars().all()

    items = []
    total_book_balance = Decimal("0")
    for account in accounts:
        book_balance, count = await _bank_account_book_balance(
            db, user.tenant_id, account.id
        )
        total_book_balance += book_balance
        items.append(_bank_account_response(account, book_balance, count))

    return TrustBankAccountListResponse(
        items=items,
        total=len(items),
        total_book_balance=total_book_balance,
    )


@router.get("/bank-accounts/{bank_account_id}")
async def get_trust_bank_account(
    bank_account_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TrustBankAccountResponse:
    """Get a single pooled bank account with computed book balance."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(TrustBankAccount).where(
            TrustBankAccount.id == bank_account_id,
            TrustBankAccount.tenant_id == user.tenant_id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Trust bank account not found")

    book_balance, count = await _bank_account_book_balance(
        db, user.tenant_id, account.id
    )

    return _bank_account_response(account, book_balance, count)


@router.patch("/bank-accounts/{bank_account_id}")
async def update_trust_bank_account(
    bank_account_id: str,
    body: TrustBankAccountUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TrustBankAccountResponse:
    """Update a pooled bank account."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(TrustBankAccount).where(
            TrustBankAccount.id == bank_account_id,
            TrustBankAccount.tenant_id == user.tenant_id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Trust bank account not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(account, key, value)

    await db.commit()
    await db.refresh(account)

    book_balance, count = await _bank_account_book_balance(
        db, user.tenant_id, account.id
    )

    return _bank_account_response(account, book_balance, count)


# ── Pooled Three-Way Reconciliation ─────────────────────────────────────────
#
# For a pooled bank account:
#
#   book_balance = trust_liability = sum of current_balance across all client
#                                     ledgers linked to this bank account
#
#   unallocated  = funds received into the pool but not yet credited to any
#                   client ledger. This pass has no separate "unallocated
#                   holding" construct distinct from the client ledgers
#                   themselves (every deposit transaction is posted directly
#                   to a client ledger), so unallocated is always 0 here.
#                   The field is retained for forward-compatibility with a
#                   future "unallocated funds" holding account, and to keep
#                   the same bank == book + unallocated identity used by the
#                   per-account reconciliation.
#
#   adjusted_bank_balance = bank_balance + outstanding_deposits
#                            - outstanding_disbursements
#
#   difference = adjusted_bank_balance - (trust_liability + unallocated)
#   is_reconciled = (difference == 0)


@router.post("/bank-accounts/{bank_account_id}/reconcile")
async def reconcile_trust_bank_account(
    bank_account_id: str,
    body: PooledReconciliationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TrustReconciliationSnapshot:
    """Perform and persist a three-way reconciliation for a pooled bank account."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    result = await db.execute(
        select(TrustBankAccount).where(
            TrustBankAccount.id == bank_account_id,
            TrustBankAccount.tenant_id == user.tenant_id,
        )
    )
    bank_account = result.scalar_one_or_none()
    if not bank_account:
        raise HTTPException(status_code=404, detail="Trust bank account not found")

    as_of_date = body.as_of_date or date.today()

    # Trust liability / book balance = sum of all linked client ledgers
    ledgers_result = await db.execute(
        select(TrustAccount).where(
            TrustAccount.tenant_id == user.tenant_id,
            TrustAccount.bank_account_id == bank_account.id,
        )
    )
    ledgers = ledgers_result.scalars().all()
    trust_liability = sum((a.current_balance for a in ledgers), Decimal("0"))
    book_balance = trust_liability

    # Unallocated — see note above: always 0 in this pass (no separate
    # unallocated-funds holding account exists yet).
    unallocated = Decimal("0")

    adjusted_bank = (
        body.bank_balance + body.outstanding_deposits - body.outstanding_disbursements
    )
    difference = adjusted_bank - (trust_liability + unallocated)
    is_reconciled = difference == Decimal("0")

    # Reconciling items: one line per linked client ledger, plus any
    # outstanding deposit/disbursement adjustments supplied by the user.
    reconciling_items: list[ReconciliationLine] = []
    for ledger in ledgers:
        reconciling_items.append(
            ReconciliationLine(
                description=f"[{ledger.account_name}] client ledger balance",
                amount=ledger.current_balance,
                is_outstanding=False,
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

    snapshot = TrustReconciliation(
        tenant_id=user.tenant_id,
        bank_account_id=bank_account.id,
        trust_account_id=None,
        as_of_date=as_of_date,
        bank_balance=body.bank_balance,
        book_balance=book_balance,
        trust_liability=trust_liability,
        unallocated=unallocated,
        outstanding_deposits=body.outstanding_deposits,
        outstanding_disbursements=body.outstanding_disbursements,
        adjusted_bank_balance=adjusted_bank,
        difference=difference,
        is_reconciled=is_reconciled,
        reconciling_items=[line.model_dump(mode="json") for line in reconciling_items],
        notes=body.notes,
        reconciled_by=user.id,
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)

    return TrustReconciliationSnapshot(
        id=str(snapshot.id),
        bank_account_id=str(snapshot.bank_account_id),
        trust_account_id=None,
        as_of_date=snapshot.as_of_date,
        bank_balance=snapshot.bank_balance,
        book_balance=snapshot.book_balance,
        trust_liability=snapshot.trust_liability,
        unallocated=snapshot.unallocated,
        outstanding_deposits=snapshot.outstanding_deposits,
        outstanding_disbursements=snapshot.outstanding_disbursements,
        adjusted_bank_balance=snapshot.adjusted_bank_balance,
        difference=snapshot.difference,
        is_reconciled=snapshot.is_reconciled,
        reconciling_items=reconciling_items,
        notes=snapshot.notes,
        reconciled_by=str(snapshot.reconciled_by) if snapshot.reconciled_by else None,
        created_at=snapshot.created_at,
    )


@router.get("/bank-accounts/{bank_account_id}/reconciliations")
async def list_trust_bank_account_reconciliations(
    bank_account_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> list[TrustReconciliationSnapshot]:
    """List persisted reconciliation snapshots for a pooled bank account, newest first."""
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    bank_result = await db.execute(
        select(TrustBankAccount).where(
            TrustBankAccount.id == bank_account_id,
            TrustBankAccount.tenant_id == user.tenant_id,
        )
    )
    if not bank_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Trust bank account not found")

    result = await db.execute(
        select(TrustReconciliation)
        .where(
            TrustReconciliation.tenant_id == user.tenant_id,
            TrustReconciliation.bank_account_id == uuid.UUID(bank_account_id),
        )
        .order_by(TrustReconciliation.created_at.desc())
    )
    snapshots = result.scalars().all()

    return [
        TrustReconciliationSnapshot(
            id=str(s.id),
            bank_account_id=str(s.bank_account_id) if s.bank_account_id else None,
            trust_account_id=str(s.trust_account_id) if s.trust_account_id else None,
            as_of_date=s.as_of_date,
            bank_balance=s.bank_balance,
            book_balance=s.book_balance,
            trust_liability=s.trust_liability,
            unallocated=s.unallocated,
            outstanding_deposits=s.outstanding_deposits,
            outstanding_disbursements=s.outstanding_disbursements,
            adjusted_bank_balance=s.adjusted_bank_balance,
            difference=s.difference,
            is_reconciled=s.is_reconciled,
            reconciling_items=[
                ReconciliationLine(**item) for item in (s.reconciling_items or [])
            ],
            notes=s.notes,
            reconciled_by=str(s.reconciled_by) if s.reconciled_by else None,
            created_at=s.created_at,
        )
        for s in snapshots
    ]


# ── Client Ledger Statement ─────────────────────────────────────────────────


@router.get("/accounts/{account_id}/statement")
async def get_trust_account_statement(
    account_id: str,
    request: Request,
    start: date | None = Query(None),
    end: date | None = Query(None),
    format: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get a client (per-matter) trust ledger statement.

    Returns a running-balance statement of all transactions in the optional
    [start, end] date window (inclusive). ``opening_balance`` is the signed
    sum of all transactions strictly before ``start`` (or 0 if ``start`` is
    omitted). Pass ``?format=csv`` for a CSV download.
    """
    user = await get_current_user(request, db)
    await set_tenant_context(db, str(user.tenant_id))

    account_result = await db.execute(
        select(TrustAccount).where(
            TrustAccount.id == account_id,
            TrustAccount.tenant_id == user.tenant_id,
        )
    )
    account = account_result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Trust account not found")

    def _signed_amount(txn: TrustTransaction) -> Decimal:
        if txn.transaction_type in CREDIT_TYPES:
            return txn.amount
        if txn.transaction_type in DEBIT_TYPES:
            return -txn.amount
        # adjustment — sign carried by the stored amount itself
        return txn.amount

    # Opening balance = signed sum of all transactions strictly before `start`
    opening_balance = Decimal("0")
    if start is not None:
        opening_result = await db.execute(
            select(TrustTransaction).where(
                TrustTransaction.trust_account_id == uuid.UUID(account_id),
                TrustTransaction.transaction_date < start,
            )
        )
        for txn in opening_result.scalars().all():
            opening_balance += _signed_amount(txn)

    # Transactions within [start, end] (inclusive), ordered chronologically
    stmt = select(TrustTransaction).where(
        TrustTransaction.trust_account_id == uuid.UUID(account_id)
    )
    if start is not None:
        stmt = stmt.where(TrustTransaction.transaction_date >= start)
    if end is not None:
        stmt = stmt.where(TrustTransaction.transaction_date <= end)
    stmt = stmt.order_by(
        TrustTransaction.transaction_date.asc(), TrustTransaction.created_at.asc()
    )

    result = await db.execute(stmt)
    transactions = result.scalars().all()

    lines: list[TrustLedgerStatementLine] = []
    running_balance = opening_balance
    total_credits = Decimal("0")
    total_debits = Decimal("0")
    for txn in transactions:
        signed = _signed_amount(txn)
        running_balance += signed
        if signed >= 0:
            total_credits += signed
        else:
            total_debits += -signed
        lines.append(
            TrustLedgerStatementLine(
                transaction_date=txn.transaction_date,
                transaction_type=txn.transaction_type,
                description=txn.description,
                amount=signed,
                running_balance=running_balance,
                reference_number=txn.reference_number,
            )
        )

    closing_balance = running_balance

    if format == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=[
                "transaction_date",
                "transaction_type",
                "description",
                "amount",
                "running_balance",
                "reference_number",
            ],
        )
        writer.writeheader()
        for line in lines:
            writer.writerow(
                {
                    "transaction_date": line.transaction_date.isoformat(),
                    "transaction_type": line.transaction_type,
                    "description": line.description,
                    "amount": str(line.amount),
                    "running_balance": str(line.running_balance),
                    "reference_number": line.reference_number or "",
                }
            )
        buffer.seek(0)
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=trust_statement.csv"},
        )

    return TrustLedgerStatementResponse(
        trust_account_id=str(account.id),
        account_name=account.account_name,
        period_start=start,
        period_end=end,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        total_credits=total_credits,
        total_debits=total_debits,
        lines=lines,
    )
