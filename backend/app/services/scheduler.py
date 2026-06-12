"""
Scheduled legal agents — run on APScheduler, triggered weekly.

Agents:
  1. renewal-watcher  — Mon 8:00 AM ET: scan renewals within 90 days, email alerts
  2. reg-monitor      — Mon 8:00 AM ET: check Federal Register RSS, email digest
  3. docket-watcher   — Mon 8:00 AM ET: check active matters for approaching deadlines
  4. oc-status        — Mon 9:00 AM ET: weekly portfolio status email to admins

For DB access: jobs create their own AsyncSession (outside FastAPI request context).
Cross-tenant queries deliberately bypass RLS by setting a blank tenant context so
all tenants' data is accessible globally for the scheduler.
"""

import hashlib
import logging
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import or_, select, text

from app.config import get_settings
from app.database import async_session_maker, set_tenant_context
from app.models.plugin import Estate, Matter, MatterEvent, Renewal
from app.models.smb_agent import SmbAgent
from app.models.estate import EstateDeadline
from app.models.scheduler import SchedulerLog
from app.models.task import Task
from app.models.tenant_credential import TenantCredential
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_oauth_token import UserOAuthToken
from app.services.cloud_sync import CloudSyncService
from app.services.email import email_service

settings = get_settings()

logger = logging.getLogger(__name__)

FEDERAL_REGISTER_RSS = "https://www.federalregister.gov/api/v1/articles.rss"

# ─────────────────────────────────────────────────────────────────────────────
# Agent metadata registry
# ─────────────────────────────────────────────────────────────────────────────

AGENT_REGISTRY: List[Dict[str, Any]] = [
    {
        "name": "renewal-watcher",
        "display_name": "Renewal Watcher",
        "description": "Scans contract renewals within 90 days and emails per-tenant alerts.",
        "schedule": "Every Monday at 8:00 AM ET",
    },
    {
        "name": "reg-monitor",
        "display_name": "Regulatory Monitor",
        "description": "Checks Federal Register RSS for the past 7 days and emails relevant regulatory digests.",
        "schedule": "Every Monday at 8:00 AM ET",
    },
    {
        "name": "docket-watcher",
        "display_name": "Docket Watcher",
        "description": "Reviews active matters for key dates with deadlines within 14 days.",
        "schedule": "Every Monday at 8:00 AM ET",
    },
    {
        "name": "oc-status",
        "display_name": "Portfolio Status",
        "description": "Sends weekly portfolio status summary to tenant administrators.",
        "schedule": "Every Monday at 9:00 AM ET",
    },
    {
        "name": "cloud-sync",
        "display_name": "Cloud Metadata Sync",
        "description": "Syncs file/email metadata from connected Google and Microsoft accounts into the cloud index.",
        "schedule": f"Every {settings.CLOUD_METADATA_SYNC_INTERVAL_MIN} minutes",
    },
    {
        "name": "email-agent",
        "display_name": "Email Matter Filing",
        "description": "Scans connected user mailboxes and auto-links matter-related emails.",
        "schedule": "Every 30 minutes",
    },
    {
        "name": "calendar-sync",
        "display_name": "Calendar Deadline Sync",
        "description": "Pushes matter key dates to connected user calendars.",
        "schedule": "Every hour",
    },
    {
        "name": "estate-deadline-watcher",
        "display_name": "Estate Deadline Watcher",
        "description": "Scans estate tax filings and court deadlines due within 14 days and emails reminders.",
        "schedule": "Daily at 8:05 AM ET",
    },
    {
        "name": "user-sync",
        "display_name": "Directory User Sync",
        "description": "Pulls directory users from connected Google/Microsoft tenants nightly; new users land on the free tier.",
        "schedule": "Daily at 2:00 AM ET",
    },
    {
        "name": "smb-heartbeat",
        "display_name": "SMB Agent Heartbeat",
        "description": "Checks for stale SMB relay agents (no heartbeat in 15 minutes) and marks them paused.",
        "schedule": "Every 15 minutes",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _bypass_rls(session) -> None:
    """
    Set a blank tenant context so RLS policies evaluate to true for all rows.
    This allows cross-tenant queries inside scheduled jobs.
    """
    await session.execute(text("SET LOCAL app.current_tenant_id = ''"))


async def _get_tenant_admins(session, tenant_id: uuid.UUID) -> List[User]:
    """Return all active admin users for a given tenant."""
    result = await session.execute(
        select(User).where(
            User.tenant_id == tenant_id,
            User.role == "admin",
            User.is_active,
        )
    )
    return list(result.scalars().all())


async def _log_start(session, agent_name: str) -> SchedulerLog:
    """Create a 'running' SchedulerLog entry and return it."""
    log = SchedulerLog(
        agent_name=agent_name,
        run_at=datetime.now(timezone.utc),
        status="running",
    )
    session.add(log)
    await session.commit()
    await session.refresh(log)
    return log


async def _log_complete(session, log: SchedulerLog, summary: str) -> None:
    log.status = "completed"
    log.summary = summary
    await session.commit()


async def _log_failed(session, log: SchedulerLog, error: str) -> None:
    log.status = "failed"
    log.error_message = error
    await session.commit()


def _days_until(target_date) -> int:
    """Return days from today until target_date (may be date or datetime)."""
    today = date.today()
    if isinstance(target_date, datetime):
        target_date = target_date.date()
    return (target_date - today).days


# ─────────────────────────────────────────────────────────────────────────────
# Cluster-wide job guard (Postgres advisory lock)
# ─────────────────────────────────────────────────────────────────────────────


def _lock_key(name: str) -> int:
    """Derive a stable 63-bit signed-int advisory-lock key from a job name.

    Postgres advisory locks take a bigint key. We hash the job name and take
    the top 60 bits (15 hex chars) which always fits in a signed bigint and is
    stable across processes/restarts so every runner maps a job to the same key.
    """
    h = hashlib.sha256(name.encode()).hexdigest()
    return int(h[:15], 16)


@asynccontextmanager
async def job_lock(name: str):
    """Yield ``True`` iff this process acquired the cluster-wide lock for ``name``.

    Uses a dedicated session + ``pg_try_advisory_lock`` so a second concurrent
    runner (e.g. an accidental extra scheduler process under ``uvicorn
    --workers``) skips the run instead of double-firing. The lock is released on
    exit only if we acquired it. ``pg_try_advisory_lock`` is non-blocking: it
    returns immediately with false rather than waiting.
    """
    key = _lock_key(name)
    async with async_session_maker() as session:
        got = (
            await session.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
        ).scalar()
        try:
            yield bool(got)
        finally:
            if got:
                await session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                await session.commit()


async def _run_guarded(name: str, coro_fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``coro_fn`` only if this process holds the cluster-wide lock for ``name``.

    If the lock is held by another runner, the coroutine is NOT awaited and we
    log a skip and return ``None``. Any exception raised by the job is logged via
    ``logger.exception`` and swallowed so a single failing job can never crash
    the APScheduler event loop. (Each job additionally manages its own
    SchedulerLog row + the advisory lock here is the primary dedupe mechanism.)
    """
    async with job_lock(name) as acquired:
        if not acquired:
            logger.info("[%s] skipped — another runner holds the lock", name)
            return None
        try:
            return await coro_fn()
        except Exception:
            logger.exception("[%s] guarded run failed", name)
            return None


# ─────────────────────────────────────────────────────────────────────────────
# RSS Feed Fetcher
# ─────────────────────────────────────────────────────────────────────────────


async def _fetch_federal_register_rss() -> List[Dict[str, str]]:
    """
    Fetch and parse the Federal Register RSS feed.
    Returns list of {title, summary, published, url, agency}.
    On error, logs and returns [].
    """
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(FEDERAL_REGISTER_RSS)
            resp.raise_for_status()
            raw_xml = resp.text
    except Exception as exc:
        logger.warning("Failed to fetch Federal Register RSS: %s", exc)
        return []

    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        logger.warning("Failed to parse Federal Register RSS XML: %s", exc)
        return []

    # RSS 2.0 namespace awareness
    ns = {
        "dc": "http://purl.org/dc/elements/1.1/",
    }

    channel = root.find("channel")
    if channel is None:
        logger.warning("Federal Register RSS: no <channel> element found")
        return []

    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = (item.findtext("description") or "").strip()
        pub_date_str = (item.findtext("pubDate") or "").strip()
        # Agency may be in dc:subject or title
        agency = (item.findtext("dc:subject", namespaces=ns) or "").strip()

        # Parse pubDate
        published = None
        if pub_date_str:
            for fmt in (
                "%a, %d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S GMT",
            ):
                try:
                    published = datetime.strptime(pub_date_str, fmt)
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue

        # Filter to last 7 days
        if published and published < cutoff:
            continue

        articles.append(
            {
                "title": title,
                "summary": description[:500] if description else "",
                "published": pub_date_str,
                "url": link,
                "agency": agency,
            }
        )

    logger.info(
        "Federal Register RSS: fetched %d articles from last 7 days", len(articles)
    )
    return articles


def _filter_rss_for_tenant(
    articles: List[Dict[str, str]],
    practice_profile_content: Optional[str],
) -> List[Dict[str, str]]:
    """
    Simple relevance filter: keep articles whose title/agency appears in the
    tenant's practice profile. If no profile content, return all articles.
    """
    if not practice_profile_content:
        return articles

    profile_lower = practice_profile_content.lower()

    # Extract watched agencies from profile (look for common patterns)
    relevant = []
    for article in articles:
        agency_lower = article.get("agency", "").lower()
        title_lower = article.get("title", "").lower()

        # Check if any meaningful word from the article matches the profile
        match_terms = set(agency_lower.split() + title_lower.split())
        # Filter out short/stop words
        match_terms = {t for t in match_terms if len(t) > 4}

        if any(term in profile_lower for term in match_terms):
            relevant.append(article)

    # If nothing matched, return top 10 by default so digest isn't empty
    return relevant if relevant else articles[:10]


def _build_reg_digest_markdown(articles: List[Dict[str, str]]) -> str:
    """Build a markdown digest from RSS articles."""
    if not articles:
        return "No relevant regulatory updates found this week."

    lines = [
        "# Regulatory Monitor — Weekly Digest\n",
        f"_Retrieved {len(articles)} relevant article(s) from the Federal Register_\n",
    ]
    for i, art in enumerate(articles, 1):
        lines.append(f"## {i}. {art['title']}")
        if art.get("agency"):
            lines.append(f"**Agency:** {art['agency']}")
        if art.get("published"):
            lines.append(f"**Published:** {art['published']}")
        if art.get("summary"):
            lines.append(f"\n{art['summary']}")
        if art.get("url"):
            lines.append(f"\n[View full document]({art['url']})\n")
        lines.append("---")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# LegalScheduler
# ─────────────────────────────────────────────────────────────────────────────


class LegalScheduler:
    """
    APScheduler-based wrapper for all legal agent cron jobs.
    Intended to be started once at app startup and shut down gracefully.
    """

    def __init__(self) -> None:
        self.scheduler = AsyncIOScheduler(timezone="America/New_York")

    def _guarded(self, job_id: str, method: Callable[[], Awaitable[Any]]):
        """Return a zero-arg coroutine fn that runs ``method`` under the
        cluster-wide advisory lock for ``job_id``. APScheduler calls this; the
        lock ensures a stray second scheduler process skips the run instead of
        double-firing client emails / invoices.
        """

        async def _runner() -> Any:
            return await _run_guarded(job_id, method)

        return _runner

    def start(self) -> None:
        """Register all agent jobs and start the scheduler.

        Every job entrypoint is wrapped in ``_run_guarded`` so it only fires in
        the process that holds the Postgres advisory lock for that job id. This
        is the primary defense against duplicate emails/invoices when more than
        one scheduler process is running.
        """
        # renewal-watcher: Mon 8:00 AM ET
        self.scheduler.add_job(
            self._guarded("renewal-watcher", self.run_renewal_watcher),
            CronTrigger(day_of_week="mon", hour=8, minute=0),
            id="renewal-watcher",
            name="Renewal Watcher",
            replace_existing=True,
        )

        # reg-monitor: Mon 8:00 AM ET
        self.scheduler.add_job(
            self._guarded("reg-monitor", self.run_reg_monitor),
            CronTrigger(day_of_week="mon", hour=8, minute=0),
            id="reg-monitor",
            name="Regulatory Monitor",
            replace_existing=True,
        )

        # docket-watcher: Mon 8:00 AM ET
        self.scheduler.add_job(
            self._guarded("docket-watcher", self.run_docket_watcher),
            CronTrigger(day_of_week="mon", hour=8, minute=0),
            id="docket-watcher",
            name="Docket Watcher",
            replace_existing=True,
        )

        # oc-status: Mon 9:00 AM ET
        self.scheduler.add_job(
            self._guarded("oc-status", self.run_oc_status),
            CronTrigger(day_of_week="mon", hour=9, minute=0),
            id="oc-status",
            name="Portfolio Status",
            replace_existing=True,
        )

        # task-reminder: every hour
        self.scheduler.add_job(
            self._guarded("task-reminder", self._check_task_reminders),
            "interval",
            hours=1,
            id="task-reminder",
            name="Task Reminder",
            replace_existing=True,
        )

        # estate-deadline-watcher: daily 8:05 AM ET
        self.scheduler.add_job(
            self._guarded("estate-deadline-watcher", self.run_estate_deadline_watcher),
            CronTrigger(hour=8, minute=5),
            id="estate-deadline-watcher",
            name="Estate Deadline Watcher",
            replace_existing=True,
        )

        # user-sync: daily at 2:00 AM ET
        self.scheduler.add_job(
            self._guarded("user-sync", self.run_user_sync),
            CronTrigger(hour=2, minute=0),
            id="user-sync",
            name="Directory User Sync",
            replace_existing=True,
        )

        # email-agent: every 30 minutes
        self.scheduler.add_job(
            self._guarded("email-agent", self.run_email_agent),
            "interval",
            minutes=30,
            id="email-agent",
            name="Email Matter Filing",
            replace_existing=True,
        )

        # calendar-sync: every hour
        self.scheduler.add_job(
            self._guarded("calendar-sync", self.run_calendar_sync),
            "interval",
            hours=1,
            id="calendar-sync",
            name="Calendar Deadline Sync",
            replace_existing=True,
        )

        agent_count = 8

        # cloud-sync: every CLOUD_METADATA_SYNC_INTERVAL_MIN minutes (if enabled)
        if settings.CLOUD_SEARCH_ENABLED:
            self.scheduler.add_job(
                self._guarded("cloud-sync", self.run_cloud_sync),
                "interval",
                minutes=settings.CLOUD_METADATA_SYNC_INTERVAL_MIN,
                id="cloud-sync",
                name="Cloud Metadata Sync",
                replace_existing=True,
            )
            agent_count += 1

        # smb-heartbeat: every 15 minutes
        self.scheduler.add_job(
            self._guarded("smb-heartbeat", self._check_smb_agent_heartbeats),
            CronTrigger(minute="*/15"),
            id="smb-heartbeat",
            name="SMB Agent Heartbeat",
            replace_existing=True,
        )

        self.scheduler.start()
        logger.info("LegalScheduler started with %d agents", agent_count)

    def shutdown(self) -> None:
        """Graceful shutdown of the scheduler."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("LegalScheduler shut down")

    def get_job(self, agent_name: str):
        """Return the APScheduler job object for a given agent name."""
        return self.scheduler.get_job(agent_name)

    # ─── Agent: renewal-watcher ───────────────────────────────────────────────

    async def run_renewal_watcher(self) -> None:
        """
        1. Query all active renewals where renewal_date <= 90 days from today
        2. Group by urgency: critical (0-13), high (14-30), medium (31-60), low (31-90)
        3. For each tenant: find admin users, send renewal alert email
        4. Also post Slack webhook if configured
        5. Log run to scheduler_logs
        """
        logger.info("[renewal-watcher] Starting run")
        async with async_session_maker() as session:
            log = await _log_start(session, "renewal-watcher")
            try:
                await _bypass_rls(session)

                cutoff = date.today() + timedelta(days=90)
                today = date.today()

                # Query all renewals within 90 days that aren't cancelled/renewed
                result = await session.execute(
                    select(Renewal)
                    .where(
                        Renewal.renewal_date <= cutoff,
                        Renewal.renewal_date >= today,
                        Renewal.status.notin_(["cancelled", "renewed", "closed"]),
                    )
                    .order_by(Renewal.renewal_date.asc())
                )
                renewals = list(result.scalars().all())

                if not renewals:
                    await _log_complete(
                        session, log, "No renewals within 90 days — nothing to send."
                    )
                    logger.info("[renewal-watcher] No renewals to alert.")
                    return

                # Group by tenant_id
                by_tenant: Dict[uuid.UUID, List[Renewal]] = defaultdict(list)
                for r in renewals:
                    by_tenant[r.tenant_id].append(r)

                emails_sent = 0
                for tenant_id, tenant_renewals in by_tenant.items():
                    await _bypass_rls(session)

                    # Build alert dicts
                    alerts = []
                    for r in tenant_renewals:
                        cancel_by = r.notice_deadline or r.renewal_date
                        days = _days_until(cancel_by)
                        alerts.append(
                            {
                                "contract_name": r.contract_name,
                                "vendor": r.vendor,
                                "value": (
                                    str(r.contract_value_annual)
                                    if r.contract_value_annual
                                    else ""
                                ),
                                "cancel_by": (
                                    cancel_by.strftime("%Y-%m-%d") if cancel_by else "—"
                                ),
                                "business_owner": r.business_owner or "—",
                                "days_until": days,
                                "status": r.status,
                            }
                        )

                    # Get admin users for this tenant
                    admins = await _get_tenant_admins(session, tenant_id)
                    if not admins:
                        logger.warning(
                            "[renewal-watcher] No admin users for tenant %s — skipping",
                            tenant_id,
                        )
                        continue

                    for admin in admins:
                        sent = await email_service.send_renewal_alert(
                            admin.email, alerts
                        )
                        if sent:
                            emails_sent += 1

                # Slack notification
                total_critical = sum(
                    1
                    for r in renewals
                    if _days_until(r.notice_deadline or r.renewal_date) <= 13
                )
                slack_msg = (
                    f":rotating_light: *Renewal Watcher*: {len(renewals)} contract(s) need attention "
                    f"in the next 90 days ({total_critical} critical). "
                    f"Sent {emails_sent} alert email(s)."
                )
                await email_service.send_slack_webhook(slack_msg)

                summary = (
                    f"Found {len(renewals)} renewal(s) across {len(by_tenant)} tenant(s). "
                    f"Sent {emails_sent} alert email(s). "
                    f"Critical (≤13 days): {total_critical}."
                )
                await _log_complete(session, log, summary)
                logger.info("[renewal-watcher] Complete. %s", summary)

            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception("[renewal-watcher] Unhandled error: %s", error_msg)
                await _log_failed(session, log, error_msg)

    # ─── Agent: reg-monitor ───────────────────────────────────────────────────

    async def run_reg_monitor(self) -> None:
        """
        1. Fetch Federal Register RSS feed for last 7 days
        2. For each tenant with a regulatory-legal practice profile:
           a. Filter RSS entries by relevance to their profile
           b. Build digest of relevant regulatory changes
        3. Send digest email to tenant admin(s)
        4. Post to Slack if configured
        """
        logger.info("[reg-monitor] Starting run")
        async with async_session_maker() as session:
            log = await _log_start(session, "reg-monitor")
            try:
                # Fetch RSS once — shared across all tenants
                articles = await _fetch_federal_register_rss()

                await _bypass_rls(session)

                # Find all tenants with a regulatory-legal practice profile
                from app.models.plugin import PracticeProfile

                result = await session.execute(
                    select(PracticeProfile).where(
                        PracticeProfile.plugin_name == "regulatory-legal",
                        PracticeProfile.is_complete,
                    )
                )
                profiles = list(result.scalars().all())

                emails_sent = 0
                tenants_with_profile = set()

                for profile in profiles:
                    tenant_id = profile.tenant_id
                    tenants_with_profile.add(tenant_id)

                    await _bypass_rls(session)

                    # Filter articles for this tenant's profile
                    relevant_articles = _filter_rss_for_tenant(
                        articles, profile.profile_content
                    )

                    digest_md = _build_reg_digest_markdown(relevant_articles)

                    admins = await _get_tenant_admins(session, tenant_id)
                    if not admins:
                        logger.warning(
                            "[reg-monitor] No admin users for tenant %s — skipping",
                            tenant_id,
                        )
                        continue

                    for admin in admins:
                        sent = await email_service.send_agent_digest(
                            admin.email, "Regulatory Monitor", digest_md
                        )
                        if sent:
                            emails_sent += 1

                # If no profiles, send the global digest to all tenant admins who want it
                # (graceful: log and skip)
                if not profiles:
                    logger.info(
                        "[reg-monitor] No tenants with completed regulatory-legal profile."
                    )

                slack_msg = (
                    f":newspaper: *Regulatory Monitor*: Fetched {len(articles)} article(s) from Federal Register. "
                    f"Sent digests to {emails_sent} admin(s) across {len(tenants_with_profile)} tenant(s)."
                )
                await email_service.send_slack_webhook(slack_msg)

                summary = (
                    f"Fetched {len(articles)} article(s). "
                    f"Emailed {emails_sent} digest(s) to {len(tenants_with_profile)} tenant(s)."
                )
                await _log_complete(session, log, summary)
                logger.info("[reg-monitor] Complete. %s", summary)

            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception("[reg-monitor] Unhandled error: %s", error_msg)
                await _log_failed(session, log, error_msg)

    # ─── Agent: docket-watcher ────────────────────────────────────────────────

    async def run_docket_watcher(self) -> None:
        """
        1. Query all active (non-closed) matters
        2. For each matter: check key_dates for deadlines within 14 days
        3. Build alert list grouped by urgency
        4. Send alerts to matter owners (internal_owners from matter record)
        5. Include conflicts_status bypass matters in alert
        """
        logger.info("[docket-watcher] Starting run")
        async with async_session_maker() as session:
            log = await _log_start(session, "docket-watcher")
            try:
                await _bypass_rls(session)

                result = await session.execute(
                    select(Matter).where(Matter.is_closed.is_(False))
                )
                matters = list(result.scalars().all())

                deadline_cutoff = date.today() + timedelta(days=14)
                today = date.today()

                # Collect matters with imminent deadlines, grouped by tenant
                # Structure: { tenant_id: [{ matter info + deadline info }] }
                alerts_by_tenant: Dict[uuid.UUID, List[Dict]] = defaultdict(list)

                for matter in matters:
                    key_dates = matter.key_dates or {}
                    if not key_dates:
                        continue

                    imminent_deadlines = []
                    for label, date_val in key_dates.items():
                        if not date_val:
                            continue
                        try:
                            if isinstance(date_val, str):
                                d = date.fromisoformat(date_val)
                            elif isinstance(date_val, date):
                                d = date_val
                            else:
                                continue
                        except ValueError:
                            continue

                        if today <= d <= deadline_cutoff:
                            days = (d - today).days
                            imminent_deadlines.append(
                                {
                                    "label": label,
                                    "date": d.isoformat(),
                                    "days_until": days,
                                }
                            )

                    if imminent_deadlines:
                        internal_owners = matter.internal_owners or {}
                        alert_entry: Dict[str, Any] = {
                            "matter_id": str(matter.id),
                            "matter_name": matter.matter_name,
                            "matter_type": matter.matter_type,
                            "risk_level": matter.risk_level or "unknown",
                            "status": matter.status,
                            "conflicts_bypass": matter.conflicts_status == "bypass",
                            "internal_owners": internal_owners,
                            "deadlines": sorted(
                                imminent_deadlines, key=lambda x: x["days_until"]
                            ),
                        }
                        alerts_by_tenant[matter.tenant_id].append(alert_entry)

                emails_sent = 0
                for tenant_id, tenant_alerts in alerts_by_tenant.items():
                    await _bypass_rls(session)

                    admins = await _get_tenant_admins(session, tenant_id)
                    if not admins:
                        logger.warning(
                            "[docket-watcher] No admin users for tenant %s", tenant_id
                        )
                        continue

                    digest_md = self._build_docket_digest(tenant_alerts)

                    for admin in admins:
                        sent = await email_service.send_agent_digest(
                            admin.email, "Docket Watcher", digest_md
                        )
                        if sent:
                            emails_sent += 1

                total_deadlines = sum(
                    len(a["deadlines"])
                    for alerts in alerts_by_tenant.values()
                    for a in alerts
                )
                slack_msg = (
                    f":calendar: *Docket Watcher*: Found {total_deadlines} deadline(s) within 14 days "
                    f"across {len(alerts_by_tenant)} tenant(s). Sent {emails_sent} alert email(s)."
                )
                await email_service.send_slack_webhook(slack_msg)

                summary = (
                    f"Scanned {len(matters)} active matter(s). "
                    f"Found deadlines in {sum(len(v) for v in alerts_by_tenant.values())} matter(s) "
                    f"across {len(alerts_by_tenant)} tenant(s). "
                    f"Sent {emails_sent} alert email(s)."
                )
                await _log_complete(session, log, summary)
                logger.info("[docket-watcher] Complete. %s", summary)

            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception("[docket-watcher] Unhandled error: %s", error_msg)
                await _log_failed(session, log, error_msg)

    # ─── Agent: oc-status ─────────────────────────────────────────────────────

    async def run_oc_status(self) -> None:
        """
        Weekly portfolio status email for each tenant:
        1. Load all active matters
        2. Summarize: total active, by risk level, by type, upcoming deadlines
        3. List matters with stale updates (no event in 14+ days)
        4. Send formatted email to tenant admin users
        """
        logger.info("[oc-status] Starting run")
        async with async_session_maker() as session:
            log = await _log_start(session, "oc-status")
            try:
                await _bypass_rls(session)

                # All active matters across all tenants
                result = await session.execute(
                    select(Matter).where(Matter.is_closed.is_(False))
                )
                matters = list(result.scalars().all())

                # Group by tenant
                by_tenant: Dict[uuid.UUID, List[Matter]] = defaultdict(list)
                for m in matters:
                    by_tenant[m.tenant_id].append(m)

                # Get latest event per matter for stale detection
                stale_cutoff = datetime.now(timezone.utc) - timedelta(days=14)

                emails_sent = 0

                for tenant_id, tenant_matters in by_tenant.items():
                    await _bypass_rls(session)

                    # Count by risk level
                    by_risk: Dict[str, int] = defaultdict(int)
                    for m in tenant_matters:
                        by_risk[m.risk_level or "unknown"] += 1

                    # Count by type
                    by_type: Dict[str, int] = defaultdict(int)
                    for m in tenant_matters:
                        by_type[m.matter_type] += 1

                    # Upcoming deadlines in next 14 days
                    upcoming_deadlines: List[Dict] = []
                    today = date.today()
                    deadline_cutoff = today + timedelta(days=14)
                    for m in tenant_matters:
                        key_dates = m.key_dates or {}
                        for label, date_val in key_dates.items():
                            if not date_val:
                                continue
                            try:
                                if isinstance(date_val, str):
                                    d = date.fromisoformat(date_val)
                                elif isinstance(date_val, date):
                                    d = date_val
                                else:
                                    continue
                            except ValueError:
                                continue
                            if today <= d <= deadline_cutoff:
                                upcoming_deadlines.append(
                                    {
                                        "matter_name": m.matter_name,
                                        "deadline_label": label,
                                        "deadline_date": d.isoformat(),
                                        "days_until": (d - today).days,
                                    }
                                )

                    upcoming_deadlines.sort(key=lambda x: x["days_until"])

                    # Stale matters: check last event timestamp
                    stale_matters: List[Dict] = []
                    matter_ids = [m.id for m in tenant_matters]

                    if matter_ids:
                        # Get latest event per matter
                        for m in tenant_matters:
                            evt_result = await session.execute(
                                select(MatterEvent)
                                .where(MatterEvent.matter_id == m.id)
                                .order_by(MatterEvent.created_at.desc())
                                .limit(1)
                            )
                            latest_event = evt_result.scalar_one_or_none()

                            if (
                                latest_event is None
                                or latest_event.created_at < stale_cutoff
                            ):
                                days_ago = (
                                    (
                                        datetime.now(timezone.utc)
                                        - latest_event.created_at
                                    ).days
                                    if latest_event
                                    else None
                                )
                                stale_matters.append(
                                    {
                                        "matter_name": m.matter_name,
                                        "matter_type": m.matter_type,
                                        "risk_level": m.risk_level or "unknown",
                                        "days_since_update": (
                                            days_ago
                                            if days_ago is not None
                                            else "never"
                                        ),
                                    }
                                )

                    # Get tenant name from admin user's tenant relationship
                    admins = await _get_tenant_admins(session, tenant_id)
                    if not admins:
                        logger.warning(
                            "[oc-status] No admin users for tenant %s", tenant_id
                        )
                        continue

                    tenant_name = (
                        admins[0].tenant.name if admins[0].tenant else str(tenant_id)
                    )

                    for admin in admins:
                        sent = await email_service.send_oc_status(
                            recipient=admin.email,
                            tenant_name=tenant_name,
                            total_active=len(tenant_matters),
                            by_risk=dict(by_risk),
                            by_type=dict(by_type),
                            upcoming_deadlines=upcoming_deadlines,
                            stale_matters=stale_matters,
                        )
                        if sent:
                            emails_sent += 1

                slack_msg = (
                    f":briefcase: *Portfolio Status*: Sent weekly OC status to {emails_sent} admin(s) "
                    f"across {len(by_tenant)} tenant(s). "
                    f"Total active matters: {len(matters)}."
                )
                await email_service.send_slack_webhook(slack_msg)

                summary = (
                    f"Summarized {len(matters)} active matter(s) across {len(by_tenant)} tenant(s). "
                    f"Sent {emails_sent} status email(s)."
                )
                await _log_complete(session, log, summary)
                logger.info("[oc-status] Complete. %s", summary)

            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception("[oc-status] Unhandled error: %s", error_msg)
                await _log_failed(session, log, error_msg)

    # ─── Agent: task-reminder ─────────────────────────────────────────────────

    async def _check_task_reminders(self) -> None:
        """Email reminders for tasks due within the next 24 hours that are not completed/cancelled."""
        logger.info("[task-reminder] Starting run")
        async with async_session_maker() as session:
            log = await _log_start(session, "task-reminder")
            try:
                await _bypass_rls(session)

                now_utc = datetime.now(timezone.utc)
                today = now_utc.date()
                tomorrow = today + timedelta(days=1)

                # Tasks due within the next 24 hours, not completed/cancelled,
                # and no reminder sent in the last 23 hours (dedup guard).
                reminder_cutoff = now_utc - timedelta(hours=23)
                result = await session.execute(
                    select(Task).where(
                        Task.due_date >= today,
                        Task.due_date <= tomorrow,
                        Task.status.notin_(["completed", "cancelled"]),
                        Task.assigned_to_user_id.isnot(None),
                        or_(
                            Task.reminder_sent_at.is_(None),
                            Task.reminder_sent_at < reminder_cutoff,
                        ),
                    )
                )
                tasks = list(result.scalars().all())

                if not tasks:
                    await _log_complete(
                        session, log, "No tasks due within 24 hours — nothing to send."
                    )
                    logger.info("[task-reminder] No tasks to remind.")
                    return

                emails_sent = 0
                skipped_no_assignee = 0

                for task in tasks:
                    if not task.assigned_to_user_id:
                        skipped_no_assignee += 1
                        continue

                    # Look up assignee
                    user_result = await session.execute(
                        select(User).where(User.id == task.assigned_to_user_id)
                    )
                    assignee = user_result.scalar_one_or_none()
                    if not assignee or not assignee.email:
                        skipped_no_assignee += 1
                        continue

                    due_str = task.due_date.isoformat() if task.due_date else "Unknown"

                    try:
                        sent = await email_service.send_task_reminder(
                            to_email=assignee.email,
                            task_title=task.title,
                            due_date=due_str,
                            assignee_name=(
                                assignee.full_name
                                if hasattr(assignee, "full_name")
                                else None
                            ),
                        )
                        if sent:
                            emails_sent += 1
                            task.reminder_sent_at = now_utc
                            await session.commit()
                    except Exception as exc:
                        logger.error(
                            "[task-reminder] Failed to send reminder for task %s to %s: %s",
                            task.id,
                            assignee.email,
                            exc,
                        )

                summary = (
                    f"Found {len(tasks)} task(s) due within 24 hours. "
                    f"Sent {emails_sent} reminder(s). "
                    f"Skipped {skipped_no_assignee} (no assignee/email)."
                )
                await _log_complete(session, log, summary)
                logger.info("[task-reminder] Complete. %s", summary)

            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception("[task-reminder] Unhandled error: %s", error_msg)
                await _log_failed(session, log, error_msg)

    # ─── Agent: estate-deadline-watcher ───────────────────────────────────────

    async def run_estate_deadline_watcher(self) -> None:
        """Email reminders for estate tax/court deadlines due within 14 days.

        Reminders go to the assigned user when set, otherwise to each tenant's
        admins. A 6-day dedup guard (reminder_sent_at) prevents repeat emails for
        the same deadline within a window.
        """
        logger.info("[estate-deadline-watcher] Starting run")
        async with async_session_maker() as session:
            log = await _log_start(session, "estate-deadline-watcher")
            try:
                await _bypass_rls(session)

                now_utc = datetime.now(timezone.utc)
                today = now_utc.date()
                horizon = today + timedelta(days=14)
                reminder_cutoff = now_utc - timedelta(days=6)

                result = await session.execute(
                    select(EstateDeadline).where(
                        EstateDeadline.due_date >= today,
                        EstateDeadline.due_date <= horizon,
                        EstateDeadline.status.notin_(["complete", "na", "cancelled"]),
                        or_(
                            EstateDeadline.reminder_sent_at.is_(None),
                            EstateDeadline.reminder_sent_at < reminder_cutoff,
                        ),
                    )
                )
                deadlines = list(result.scalars().all())

                if not deadlines:
                    await _log_complete(
                        session, log, "No estate deadlines due within 14 days."
                    )
                    logger.info("[estate-deadline-watcher] Nothing to remind.")
                    return

                # Cache estate titles and per-tenant admin lists to avoid re-querying.
                estate_titles: Dict[uuid.UUID, str] = {}
                admin_cache: Dict[uuid.UUID, List[User]] = {}
                emails_sent = 0

                for dl in deadlines:
                    if dl.estate_id not in estate_titles:
                        est = await session.execute(
                            select(Estate).where(Estate.id == dl.estate_id)
                        )
                        est = est.scalar_one_or_none()
                        estate_titles[dl.estate_id] = (
                            (est.estate_name or est.title) if est else "Estate"
                        )
                    estate_label = estate_titles[dl.estate_id]
                    due_str = dl.due_date.isoformat()

                    # Determine recipients: assignee, else tenant admins.
                    recipients: List[User] = []
                    if dl.assigned_to:
                        u = await session.execute(
                            select(User).where(User.id == dl.assigned_to)
                        )
                        u = u.scalar_one_or_none()
                        if u and u.email:
                            recipients = [u]
                    if not recipients:
                        if dl.tenant_id not in admin_cache:
                            admin_cache[dl.tenant_id] = await _get_tenant_admins(
                                session, dl.tenant_id
                            )
                        recipients = admin_cache[dl.tenant_id]

                    sent_any = False
                    for recipient in recipients:
                        if not getattr(recipient, "email", None):
                            continue
                        try:
                            sent = await email_service.send_task_reminder(
                                to_email=recipient.email,
                                task_title=f"{estate_label}: {dl.title} ({dl.deadline_type})",
                                due_date=due_str,
                                assignee_name=getattr(recipient, "full_name", None),
                            )
                            if sent:
                                emails_sent += 1
                                sent_any = True
                        except Exception as exc:
                            logger.error(
                                "[estate-deadline-watcher] Failed to email %s for deadline %s: %s",
                                recipient.email,
                                dl.id,
                                exc,
                            )

                    if sent_any:
                        dl.reminder_sent_at = now_utc
                        await session.commit()

                summary = (
                    f"Found {len(deadlines)} estate deadline(s) within 14 days. "
                    f"Sent {emails_sent} reminder email(s)."
                )
                await _log_complete(session, log, summary)
                logger.info("[estate-deadline-watcher] Complete. %s", summary)

            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "[estate-deadline-watcher] Unhandled error: %s", error_msg
                )
                await _log_failed(session, log, error_msg)

    # ─── Agent: cloud-sync ────────────────────────────────────────────────────

    async def run_cloud_sync(self) -> None:
        """Sync cloud file/email metadata for every tenant with active credentials.

        Iterates tenants that have at least one active TenantCredential and calls
        CloudSyncService.sync_all for each. Per-tenant failures are isolated so one
        tenant cannot abort the whole run.
        """
        logger.info("[cloud-sync] Starting run")
        async with async_session_maker() as session:
            log = await _log_start(session, "cloud-sync")
            try:
                await _bypass_rls(session)

                result = await session.execute(
                    select(TenantCredential.tenant_id)
                    .where(TenantCredential.is_active)
                    .distinct()
                )
                tenant_ids = [row[0] for row in result.all()]

                if not tenant_ids:
                    await _log_complete(
                        session,
                        log,
                        "No tenants with active credentials — nothing to sync.",
                    )
                    logger.info("[cloud-sync] No connected tenants.")
                    return

                sync_svc = CloudSyncService()
                tenants_synced = 0
                total_records = 0

                for tenant_id in tenant_ids:
                    try:
                        counts = await sync_svc.sync_all(session, str(tenant_id))
                        tenants_synced += 1
                        for provider_counts in counts.values():
                            total_records += sum(provider_counts.values())
                    except Exception as tenant_err:
                        logger.warning(
                            "[cloud-sync] Sync failed for tenant %s: %s",
                            tenant_id,
                            tenant_err,
                        )

                # Restore bypass context for the final log commit (sync_all set a
                # tenant-scoped context on the shared session).
                await _bypass_rls(session)

                summary = (
                    f"Synced {tenants_synced}/{len(tenant_ids)} tenant(s); "
                    f"{total_records} record(s) upserted."
                )
                await _log_complete(session, log, summary)
                logger.info("[cloud-sync] Complete. %s", summary)

            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception("[cloud-sync] Unhandled error: %s", error_msg)
                await _bypass_rls(session)
                await _log_failed(session, log, error_msg)

    # ─── Agent: email-agent ───────────────────────────────────────────────────

    async def run_email_agent(self) -> None:
        """Auto-file matter emails for users with delegated mail credentials."""
        logger.info("[email-agent] Starting run")
        from app.services.email_agent import email_agent
        from app.services.llm import LLMService
        from app.services.llm_routing import resolve_llm_route

        async with async_session_maker() as session:
            log = await _log_start(session, "email-agent")
            try:
                await _bypass_rls(session)

                result = await session.execute(
                    select(
                        UserOAuthToken.tenant_id,
                        UserOAuthToken.user_id,
                        UserOAuthToken.provider,
                        Tenant.name,
                    )
                    .join(Tenant, Tenant.id == UserOAuthToken.tenant_id)
                    .where(UserOAuthToken.provider.in_(["microsoft", "google"]))
                )
                token_rows = result.all()

                if not token_rows:
                    await _log_complete(
                        session, log, "No delegated mail tokens -- nothing to scan."
                    )
                    logger.info("[email-agent] No delegated mail tokens.")
                    return

                llm = LLMService()
                scanned = 0
                failed = 0
                processed = 0

                for tenant_id, user_id, provider, tenant_name in token_rows:
                    try:
                        await set_tenant_context(session, str(tenant_id))
                        standard_route = await resolve_llm_route(
                            session, str(tenant_id), use_premium=False
                        )
                        premium_route = await resolve_llm_route(
                            session, str(tenant_id), use_premium=True
                        )
                        results = await email_agent.process_emails(
                            db=session,
                            tenant_id=str(tenant_id),
                            user_id=str(user_id),
                            provider=provider,
                            llm_service=llm,
                            tenant_name=tenant_name or "Clarity Legal",
                            max_emails=20,
                            standard_model=standard_route.model,
                            premium_model=premium_route.model,
                        )
                        scanned += 1
                        processed += len(results)
                    except Exception as tenant_err:
                        failed += 1
                        logger.warning(
                            "[email-agent] Scan failed for tenant=%s user=%s provider=%s: %s",
                            tenant_id,
                            user_id,
                            provider,
                            tenant_err,
                        )
                        await _bypass_rls(session)

                await _bypass_rls(session)
                summary = (
                    f"Scanned {scanned}/{len(token_rows)} mailbox connection(s); "
                    f"processed {processed} email(s); {failed} failed."
                )
                await _log_complete(session, log, summary)
                logger.info("[email-agent] Complete. %s", summary)

            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception("[email-agent] Unhandled error: %s", error_msg)
                await _bypass_rls(session)
                await _log_failed(session, log, error_msg)

    # ─── Agent: calendar-sync ────────────────────────────────────────────────

    async def run_calendar_sync(self) -> None:
        """Push matter key dates to users with delegated calendar credentials."""
        logger.info("[calendar-sync] Starting run")
        from app.services.calendar_sync import calendar_sync

        async with async_session_maker() as session:
            log = await _log_start(session, "calendar-sync")
            try:
                await _bypass_rls(session)

                result = await session.execute(
                    select(
                        UserOAuthToken.tenant_id,
                        UserOAuthToken.user_id,
                        UserOAuthToken.provider,
                    ).where(UserOAuthToken.provider.in_(["microsoft", "google"]))
                )
                token_rows = result.all()

                if not token_rows:
                    await _log_complete(
                        session, log, "No delegated calendar tokens -- nothing to sync."
                    )
                    logger.info("[calendar-sync] No delegated calendar tokens.")
                    return

                synced = 0
                failed = 0
                created = 0

                for tenant_id, user_id, provider in token_rows:
                    try:
                        result_data = await calendar_sync.sync_deadlines_to_calendar(
                            session, str(tenant_id), str(user_id), provider
                        )
                        synced += 1
                        created += result_data.get("created", 0)
                    except Exception as tenant_err:
                        failed += 1
                        logger.warning(
                            "[calendar-sync] Sync failed for tenant=%s user=%s provider=%s: %s",
                            tenant_id,
                            user_id,
                            provider,
                            tenant_err,
                        )
                        await _bypass_rls(session)

                await _bypass_rls(session)
                summary = (
                    f"Synced {synced}/{len(token_rows)} calendar connection(s); "
                    f"created {created} deadline event(s); {failed} failed."
                )
                await _log_complete(session, log, summary)
                logger.info("[calendar-sync] Complete. %s", summary)

            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception("[calendar-sync] Unhandled error: %s", error_msg)
                await _bypass_rls(session)
                await _log_failed(session, log, error_msg)

    # ─── Agent: user-sync ─────────────────────────────────────────────────────

    async def run_user_sync(self) -> None:
        """Sync directory users for every tenant with active credentials.

        Per-tenant/per-provider failures are isolated so one bad token cannot
        abort the whole run. Synced users land on the free tier (license_active
        is left to the UserSyncService default of False for new users).
        """
        logger.info("[user-sync] Starting run")
        from app.services.user_sync import user_sync as user_sync_svc

        async with async_session_maker() as session:
            log = await _log_start(session, "user-sync")
            try:
                await _bypass_rls(session)

                result = await session.execute(
                    select(TenantCredential.tenant_id, TenantCredential.provider).where(
                        TenantCredential.is_active
                    )
                )
                pairs = result.all()

                if not pairs:
                    await _log_complete(
                        session, log, "No active credentials -- nothing to sync."
                    )
                    logger.info("[user-sync] No connected tenants.")
                    return

                synced = 0
                failed = 0
                total_users = 0

                for tenant_id, provider in pairs:
                    try:
                        if provider == "microsoft":
                            res = await user_sync_svc.sync_microsoft_users(
                                session, str(tenant_id)
                            )
                        elif provider == "google":
                            res = await user_sync_svc.sync_google_users(
                                session, str(tenant_id)
                            )
                        else:
                            continue
                        synced += 1
                        total_users += res.get("total", 0)
                    except Exception as tenant_err:
                        failed += 1
                        logger.warning(
                            "[user-sync] %s sync failed for tenant %s: %s",
                            provider,
                            tenant_id,
                            tenant_err,
                        )
                        await _bypass_rls(session)
                        try:
                            await user_sync_svc.record_sync_failure(
                                session, str(tenant_id), provider, str(tenant_err)
                            )
                        except Exception as record_err:
                            logger.warning(
                                "[user-sync] Could not record failure for tenant %s: %s",
                                tenant_id,
                                record_err,
                            )

                await _bypass_rls(session)
                summary = (
                    f"Synced {synced} credential(s); {total_users} user(s) seen; "
                    f"{failed} failed."
                )
                await _log_complete(session, log, summary)
                logger.info("[user-sync] Complete. %s", summary)

            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception("[user-sync] Unhandled error: %s", error_msg)
                await _bypass_rls(session)
                await _log_failed(session, log, error_msg)

    # ─── Agent: smb-heartbeat ──────────────────────────────────────────────────

    async def _check_smb_agent_heartbeats(self) -> None:
        """Mark active SMB agents as paused when their last heartbeat is older than 15 minutes (or null)."""
        logger.info("[smb-heartbeat] Starting run")
        async with async_session_maker() as session:
            log = await _log_start(session, "smb-heartbeat")
            try:
                await _bypass_rls(session)

                cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)

                result = await session.execute(
                    select(SmbAgent).where(
                        SmbAgent.status == "active",
                        or_(
                            SmbAgent.last_heartbeat.is_(None),
                            SmbAgent.last_heartbeat < cutoff,
                        ),
                    )
                )
                stale_agents = list(result.scalars().all())

                if not stale_agents:
                    await _log_complete(session, log, "No stale SMB agents found.")
                    logger.info("[smb-heartbeat] No stale agents.")
                    return

                paused_count = 0
                for agent in stale_agents:
                    agent.status = "paused"
                    paused_count += 1

                await session.commit()

                summary = f"Paused {paused_count} stale SMB agent(s) (no heartbeat in 15 min)."
                await _log_complete(session, log, summary)
                logger.info("[smb-heartbeat] Complete. %s", summary)

            except Exception as exc:
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.exception("[smb-heartbeat] Unhandled error: %s", error_msg)
                await _log_failed(session, log, error_msg)

    # ─── Manual trigger ───────────────────────────────────────────────────────

    async def run_agent_manually(self, agent_name: str) -> Dict[str, Any]:
        """
        Manually trigger any agent by name. Used by the admin API.
        Returns a status dict with the result.
        """
        agent_map = {
            "renewal-watcher": self.run_renewal_watcher,
            "reg-monitor": self.run_reg_monitor,
            "docket-watcher": self.run_docket_watcher,
            "oc-status": self.run_oc_status,
            "task-reminder": self._check_task_reminders,
            "cloud-sync": self.run_cloud_sync,
            "email-agent": self.run_email_agent,
            "calendar-sync": self.run_calendar_sync,
            "estate-deadline-watcher": self.run_estate_deadline_watcher,
            "user-sync": self.run_user_sync,
            "smb-heartbeat": self._check_smb_agent_heartbeats,
        }

        fn = agent_map.get(agent_name)
        if fn is None:
            return {
                "success": False,
                "error": f"Unknown agent: {agent_name}. Valid names: {list(agent_map.keys())}",
            }

        logger.info("[manual-trigger] Running agent: %s", agent_name)
        try:
            # Run under the same cluster-wide advisory lock as the scheduled job
            # so a manual trigger can't double-fire alongside a scheduled run.
            async with job_lock(agent_name) as acquired:
                if not acquired:
                    logger.info(
                        "[manual-trigger] %s skipped — another runner holds the lock",
                        agent_name,
                    )
                    return {
                        "success": False,
                        "agent": agent_name,
                        "skipped": True,
                        "error": "Another runner holds the lock for this agent.",
                    }
                await fn()
            return {
                "success": True,
                "agent": agent_name,
                "triggered_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "[manual-trigger] Agent %s failed: %s", agent_name, error_msg
            )
            return {"success": False, "agent": agent_name, "error": error_msg}

    # ─── Private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _build_docket_digest(alerts: List[Dict]) -> str:
        """Build a markdown digest for docket-watcher alerts."""
        if not alerts:
            return "No imminent deadlines found."

        lines = [
            "# Docket Watcher — Deadline Alert\n",
            f"_{len(alerts)} matter(s) with deadlines in the next 14 days_\n",
        ]

        # Sort by soonest deadline
        alerts_sorted = sorted(
            alerts,
            key=lambda a: (
                min(d["days_until"] for d in a["deadlines"]) if a["deadlines"] else 999
            ),
        )

        for alert in alerts_sorted:
            lines.append(f"## {alert['matter_name']}")
            lines.append(
                f"**Type:** {alert['matter_type']} | **Risk:** {alert['risk_level'].title()}"
            )
            if alert.get("conflicts_bypass"):
                lines.append(
                    "> **NOTE:** This matter has a conflicts check bypass — review carefully."
                )
            lines.append("\n**Upcoming Deadlines:**")
            for dl in alert["deadlines"]:
                urgency = "URGENT" if dl["days_until"] <= 3 else ""
                lines.append(
                    f"- **{dl['label']}**: {dl['date']} ({dl['days_until']} days away) {urgency}"
                )
            internal = alert.get("internal_owners") or {}
            if internal:
                lines.append(
                    f"\n**Internal Owners:** {', '.join(str(v) for v in internal.values() if v)}"
                )
            lines.append("---")

        return "\n".join(lines)
