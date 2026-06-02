from typing import List, Tuple

import anthropic
from openai import AsyncOpenAI

from app.config import get_settings

settings = get_settings()

SYSTEM_PROMPT_TEMPLATE = """You are a senior paralegal and legal analyst working for {tenant_name}. You support attorneys with research, drafting, and analysis. You are precise, discreet, and bound by professional ethics.

CAPABILITIES:
- You draw on the firm's document library (provided as CONTEXT below), uploaded attachments, and your own legal reasoning.
- You synthesise information from all available sources but clearly distinguish what comes from the firm's materials vs. your own knowledge.
- You leverage user history and preferences (provided in USER CONTEXT below) to tailor your responses.

RULES:
- **Accuracy first.** If you are uncertain about a point, say so rather than guessing. Never fabricate case names, citations, or statutes.
- **Cite when you can.** When the CONTEXT contains supporting authority, cite it with case name and legal citation. When you reason from general principles without a specific source, flag it.
- **Confidence tags.** Tag substantive claims: [settled] for black-letter law, [verify] for points an attorney should confirm, [model knowledge] for general legal reasoning not tied to a provided source.
- **No speculation.** Do not predict what a court will do. Outline the legal framework and let the attorney assess.
- **Scope.** You answer legal questions, analyse documents, draft correspondence, and summarise authority — but you do not provide final legal advice. Always remind the attorney to review.
- **Privacy.** You never share information about {tenant_name} or its clients with anyone outside this conversation. All data stays within the firm.
- **Identity.** You are a legal assistant, not an AI. Do not mention what model or system powers you. If asked, say you are a legal research tool built for {tenant_name}.
- End every substantive response with: "\\n\\n---\\n*Prepared for {tenant_name}. Attorney review recommended before reliance.*"

USER CONTEXT (history of interactions, preferences, and patterns):
{memory_context}

FIRM CONTEXT (firm documents and relevant authority — may be empty):
{context}
"""

GEMINI_SYSTEM_INSTRUCTION = """You are a legal research assistant. You help attorneys research case law and draft legal documents.

RULES:
- Answer ONLY using the provided legal sources.
- ALWAYS cite the exact case name and legal citation for every substantive statement.
- If the provided sources do not support an answer, respond with: "I could not find relevant authority in the provided materials."
- Do NOT predict case outcomes or provide legal advice.
- Use a formal, authoritative tone.
- End every response with: "\\n\\n*This is not legal advice. Please consult a qualified attorney.*"
"""


class LLMService:
    def __init__(self):
        self.deepseek_client = AsyncOpenAI(
            api_key=settings.DEEPSEEK_API_KEY or settings.OPENCODE_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
        )
        self.anthropic_client = anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
        )
        self.azure_client = None
        if settings.AZURE_OPENAI_ENDPOINT and settings.AZURE_OPENAI_KEY:
            self.azure_client = AsyncOpenAI(
                api_key=settings.AZURE_OPENAI_KEY,
                base_url=f"{settings.AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/{settings.AZURE_OPENAI_DEPLOYMENT}",
                default_query={"api-version": "2024-08-01-preview"},
            )
        # OpenRouter — OpenAI-compatible, free model access
        self.openrouter_client = None
        if settings.OPENROUTER_API_KEY:
            self.openrouter_client = AsyncOpenAI(
                api_key=settings.OPENROUTER_API_KEY,
                base_url=settings.OPENROUTER_BASE_URL,
            )

    def _build_messages(
        self,
        system: str,
        conversation_history: List[dict],
        new_message: str,
    ) -> List[dict]:
        """Build the message list for the LLM, injecting system as first message."""
        messages = []
        for msg in conversation_history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": new_message})
        return messages

    async def complete(
        self,
        messages: List[dict],
        tenant_name: str,
        context: str,
        memory_context: str = "",
        use_premium: bool = False,
        provider: str = "default",
    ) -> Tuple[str, int, int]:
        """
        Generate a completion.
        Returns (response_text, tokens_in, tokens_out).

        Provider routing:
          - "openrouter" → OpenRouter (free models: google/gemma-4-31b-it:free etc.)
          - "gemini" → Google Gemini
          - "azure" → Azure OpenAI (GPT-4o)
          - premium/Claude → Anthropic
          - default → DeepSeek/OpenCode
        """
        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            tenant_name=tenant_name,
            memory_context=memory_context or "No user memory available.",
            context=context,
        )

        if provider == "gemini":
            return await self._complete_gemini(messages, system_prompt)
        if provider == "azure":
            return await self._complete_azure(messages, system_prompt)
        if provider == "openrouter":
            model = settings.PREMIUM_LLM if use_premium else settings.PRIMARY_LLM
            return await self._complete_openrouter(messages, system_prompt, model)

        if use_premium:
            model = settings.PREMIUM_LLM.lower()
            if model.startswith("claude") or model.startswith("anthropic"):
                return await self._complete_anthropic(messages, system_prompt)
            return await self._complete_deepseek(
                messages, system_prompt, model=settings.PREMIUM_LLM
            )
        else:
            return await self._complete_deepseek(messages, system_prompt)

    async def _complete_deepseek(
        self,
        messages: List[dict],
        system_prompt: str,
        model: str = None,
    ) -> Tuple[str, int, int]:
        """Call DeepSeek via OpenAI-compatible endpoint."""
        all_messages = [{"role": "system", "content": system_prompt}] + messages

        response = await self.deepseek_client.chat.completions.create(
            model=model or settings.PRIMARY_LLM,
            messages=all_messages,
            temperature=0.1,
            max_tokens=4096,
        )

        response_text = response.choices[0].message.content or ""
        tokens_in = response.usage.prompt_tokens if response.usage else 0
        tokens_out = response.usage.completion_tokens if response.usage else 0

        return response_text, tokens_in, tokens_out

    async def _complete_gemini(
        self,
        messages: List[dict],
        system_prompt: str,
    ) -> Tuple[str, int, int]:
        """Call Google Gemini via the generateContent API."""
        import httpx

        api_key = settings.GEMINI_API_KEY
        if not api_key:
            raise ValueError("GEMINI_API_KEY not configured")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"

        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            content_text = msg.get("content", "")
            if isinstance(content_text, str):
                contents.append(
                    {
                        "role": role,
                        "parts": [{"text": f"[{msg['role']}] {content_text}"}],
                    }
                )

        body = {
            "contents": contents,
            "systemInstruction": {
                "parts": [{"text": system_prompt}],
            },
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 4096,
            },
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=body)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Gemini API error {resp.status_code}: {resp.text[:300]}"
                )

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                return "", 0, 0

            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            response_text = "".join(p.get("text", "") for p in parts)

            usage = data.get("usageMetadata", {})
            tokens_in = usage.get("promptTokenCount", 0)
            tokens_out = usage.get("candidatesTokenCount", 0)

            return response_text, tokens_in, tokens_out

    async def _complete_azure(
        self,
        messages: List[dict],
        system_prompt: str,
    ) -> Tuple[str, int, int]:
        """Call Azure OpenAI (GPT-4o) for enterprise-grade responses."""
        if not self.azure_client:
            raise ValueError("Azure OpenAI not configured")

        all_messages = [{"role": "system", "content": system_prompt}] + messages

        response = await self.azure_client.chat.completions.create(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            messages=all_messages,
            temperature=0.1,
            max_tokens=4096,
        )

        response_text = response.choices[0].message.content or ""
        tokens_in = response.usage.prompt_tokens if response.usage else 0
        tokens_out = response.usage.completion_tokens if response.usage else 0

        return response_text, tokens_in, tokens_out

    async def _complete_openrouter(
        self,
        messages: List[dict],
        system_prompt: str,
        model: str,
    ) -> Tuple[str, int, int]:
        """Call OpenRouter — OpenAI-compatible API for free/cheap models."""
        if not self.openrouter_client:
            raise ValueError("OpenRouter not configured — set OPENROUTER_API_KEY")
        all_messages = [{"role": "system", "content": system_prompt}] + messages

        response = await self.openrouter_client.chat.completions.create(
            model=model,
            messages=all_messages,
            temperature=0.1,
            max_tokens=4096,
        )

        response_text = response.choices[0].message.content or ""
        tokens_in = response.usage.prompt_tokens if response.usage else 0
        tokens_out = response.usage.completion_tokens if response.usage else 0

        return response_text, tokens_in, tokens_out

    async def _complete_anthropic(
        self,
        messages: List[dict],
        system_prompt: str,
    ) -> Tuple[str, int, int]:
        """Call Anthropic Claude for premium responses."""
        # Convert messages to Anthropic format — system is passed separately
        anthropic_messages = []
        for msg in messages:
            anthropic_messages.append({"role": msg["role"], "content": msg["content"]})

        response = await self.anthropic_client.messages.create(
            model=settings.PREMIUM_LLM,
            system=system_prompt,
            messages=anthropic_messages,
            temperature=0.1,
            max_tokens=4096,
        )

        response_text = response.content[0].text if response.content else ""
        tokens_in = response.usage.input_tokens if response.usage else 0
        tokens_out = response.usage.output_tokens if response.usage else 0

        return response_text, tokens_in, tokens_out
