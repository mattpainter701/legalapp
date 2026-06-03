# Standard vs Premium Tiers

## Overview

Two billing tiers for the legal AI platform. Standard is a flat monthly subscription with free models included — covers 80%+ of daily legal work. Premium is pay-as-you-go (PAYG) with frontier models, multimodal input, and higher limits.

| | Standard | Premium |
|-|-|-|
| **Billing** | Flat subscription (Stripe, per-seat) | PAYG (token-based, 10× model cost markup) |
| **Rate limit** | 1,000 LLM calls/day | 10,000 LLM calls/day |
| **Models** | Free-tier models included | All models available |
| **Modalities** | Text + document parsing | Text + OCR + vision + audio |
| **Context window** | Up to 128K tokens | Up to 200K+ tokens |
| **Support** | Community/email | Priority |

---

## Models Available

### Standard (included in subscription)

| Provider | Model | Cost to platform |
|-|-|-|
| OpenRouter | `google/gemma-4-31b-it:free` | Free |
| OpenRouter | `meta-llama/llama-4-maverick:free` | Free |
| OpenRouter | `deepseek/deepseek-r1:free` | Free |
| OpenRouter | `qwen/qwen3-235b-a22b:free` | Free |
| OpenCode Zen | `deepseek-chat` (via Zen proxy) | Free |
| DeepSeek | `deepseek-chat` | ~$0.27/M tokens |

If OpenRouter or OpenCode Zen keys aren't configured, fall back to DeepSeek.

### Premium (PAYG — billed per token)

| Provider | Model | Notes |
|-|-|-|
| Anthropic | `claude-opus-4-8` | Best for contract reasoning, compliance |
| Anthropic | `claude-sonnet-4-6` | Faster, cheaper than Opus |
| Azure OpenAI | GPT-4o / deployment-specific | Enterprise compliance, VPC |
| Google Gemini | `gemini-2.0-flash` | Best native multimodal, 2M context |
| Google Gemini | `gemini-2.5-pro` | Reasoning-heavy legal work |
| OpenRouter | Any paid model | Pass-through pricing |

---

## Modalities

### Standard — Text + Document Parsing

| Input | How |
|-|-|
| Plain text | Direct chat input |
| PDF | `pypdf` text extraction |
| DOCX | `python-docx` text extraction |
| TXT | Direct read |
| XLSX | `openpyxl` extraction |
| PPTX | `python-pptx` extraction |

These are all **text extraction** — parsing bytes into strings. No visual understanding, no audio processing.

### Premium — Adds Multimodal

| Input | How | Use case |
|-|-|-|
| OCR (scanned PDFs, faxes, handwritten notes) | Tesseract or Azure Document Intelligence | Legacy documents, discovery |
| Images (photos, screenshots, exhibits) | Claude Vision / GPT-4o / Gemini | Evidence review, photo analysis |
| Audio (depositions, dictation, calls) | Whisper or cloud STT | Deposition analysis, voice notes |
| Video | Gemini / Azure Content Understanding | Bodycam, surveillance (niche) |

---

## Provider Selection Flow

```
User sends message (provider="default")
  │
  ├─ Tenant has default_llm_provider set?
  │   ├─ Yes → use that provider + model
  │   └─ No  → fall back to DeepSeek (deepseek-chat)
  │
  ├─ Standard tier?
  │   ├─ Provider is a free/cost-included model → proceed
  │   └─ Provider is a premium model → block or upsell
  │
  └─ Premium tier?
      └─ Any provider allowed — billed at PAYG rates
```

Tier enforcement at the chat endpoint: if a standard-tier tenant attempts to use a premium-only model, return 402 with an upsell message.

---

## Feature Gating Implementation

Feature flags on `TenantSettings` (future columns / `custom_config`):

```json
{
  "features": {
    "ocr_enabled": false,
    "vision_enabled": false,
    "audio_transcription": false,
    "audio_analysis": false,
    "video_analysis": false,
    "max_context_tokens": 128000,
    "max_file_size_mb": 50,
    "batch_processing": false,
    "custom_workflows": false,
    "priority_support": false
  }
}
```

Standard tenants get all `false` except `max_context_tokens: 128000` and `max_file_size_mb: 50`.
Premium tenants get `true` for all modalities, `max_context_tokens: 200000`, `max_file_size_mb: 100`.

---

## Revenue Model

| Tier | Platform cost | User price | Margin |
|-|-|-|-|
| Standard | ~$0 (free models) to ~$0.27/M tokens (DeepSeek fallback) | Flat subscription (e.g., $49/seat/mo) | High — fixed cost, variable included |
| Premium | Model API cost (Claude: ~$15/M in, $75/M out) | 10× model cost markup (PAYG) | 90% gross margin on tokens |

Standard tier cost is near-zero when OpenRouter free models or OpenCode Zen are used. DeepSeek fallback costs ~$0.27/M tokens — acceptable for the flat subscription model.

---

## Open Questions

- [ ] Flat subscription price point ($/seat/mo)?
- [ ] Free trial tier (e.g., 50 free messages before requiring subscription)?
- [ ] Standard tier: allow Anthropic/Claude with user-provided API key (customer-owned key)?
- [ ] Premium PAYG: pre-paid credits or post-paid invoice?
- [ ] OCR provider: self-hosted Tesseract (free, lower quality) or Azure Document Intelligence (better, per-page cost)?
