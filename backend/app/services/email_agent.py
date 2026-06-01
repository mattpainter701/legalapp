import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


class EmailAgent:
    """
    Orchestrates email reading + LLM classification + draft response generation.
    Uses per-user delegated OAuth tokens.
    """

    LLM_CLASSIFICATION_PROMPT = """You are a legal email triage assistant for a law firm. Analyze the following email and classify it with a structured JSON response.

EMAIL SUBJECT: {subject}
FROM: {sender}
BODY PREVIEW: {body}

Respond with a JSON object only (no markdown, no explanation):
{{
  "category": "legal_query|client_communication|court_filing|billing|administrative|spam|other",
  "urgency": "critical|high|medium|low",
  "summary": "1-2 sentence summary of the email",
  "action_needed": "specific action the attorney needs to take, or null",
  "deadline_mentioned": "any deadline date mentioned, or null",
  "requires_response": true/false,
  "suggested_response": "concise suggested reply text, or null if no reply needed"
}}"""

    LLM_DRAFT_PROMPT = """You are a legal assistant drafting an email response for an attorney. Use the attorney's practice profile for context. Be professional, concise, and legally precise.

PRACTICE CONTEXT: {practice_context}

ORIGINAL EMAIL:
Subject: {subject}
From: {sender}
Body: {body}

CLASSIFICATION: {classification}

Draft a professional response email. The attorney will review before sending. Do not include placeholder greetings like "[Your Name]". End with the attorney's standard signature block."""
    async def classify_email(
        self,
        email: dict,
        llm_service: Any,
        tenant_name: str,
    ) -> dict:
        prompt = self.LLM_CLASSIFICATION_PROMPT.format(
            subject=email.get("subject", ""),
            sender=email.get("from", ""),
            body=email.get("body_preview", "")[:3000],
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            response_text, _, _ = await llm_service.complete(
                messages, tenant_name, context="", use_premium=False
            )
            response_text = response_text.strip()
            if response_text.startswith("```"):
                response_text = response_text.split("\n", 1)[1]
                if response_text.endswith("```"):
                    response_text = response_text[:-3]
            return json.loads(response_text)
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("Email classification failed: %s", exc)
            return {
                "category": "other",
                "urgency": "low",
                "summary": email.get("subject", "No subject"),
                "action_needed": None,
                "deadline_mentioned": None,
                "requires_response": False,
                "suggested_response": None,
            }

    async def draft_response(
        self,
        email: dict,
        classification: dict,
        llm_service: Any,
        tenant_name: str,
        practice_context: str = "General legal practice",
    ) -> str:
        prompt = self.LLM_DRAFT_PROMPT.format(
            subject=email.get("subject", ""),
            sender=email.get("from", ""),
            body=email.get("body_preview", "")[:3000],
            classification=json.dumps(classification, indent=2),
            practice_context=practice_context,
        )
        messages = [{"role": "user", "content": prompt}]
        try:
            response_text, _, _ = await llm_service.complete(
                messages, tenant_name, context="", use_premium=True
            )
            return response_text.strip()
        except Exception as exc:
            logger.warning("Draft response failed: %s", exc)
            return ""

    async def process_emails(
        self,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        provider: str,
        llm_service: Any,
        tenant_name: str,
        max_emails: int = 20,
    ) -> list[dict]:
        results = []

        if provider == "microsoft":
            from app.services.microsoft_mail import ms_read_mail_user
            emails = await ms_read_mail_user(db, tenant_id, user_id, max_results=max_emails)
        elif provider == "google":
            from app.services.google_mail import gmail_read_mail
            emails = await gmail_read_mail(db, tenant_id, user_id, max_results=max_emails)
        else:
            raise ValueError(f"Unknown email provider: {provider}")

        for email in emails:
            classification = await self.classify_email(email, llm_service, tenant_name)

            draft_response = None
            if classification.get("requires_response"):
                draft_response = await self.draft_response(
                    email, classification, llm_service, tenant_name
                )

            results.append({
                "email_id": email.get("id"),
                "subject": email.get("subject"),
                "from": email.get("from"),
                "received": email.get("received") or email.get("date"),
                "classification": classification,
                "draft_response": draft_response,
            })

        return results


email_agent = EmailAgent()
