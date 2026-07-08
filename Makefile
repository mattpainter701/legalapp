.PHONY: setup dev dev-build dev-down prod-up prod-down prod-build migrate \
        logs shell sync-public-db deploy-prod backup-db restore-db \
        embed-bulk jetson-embed sbom-inventory lint test clean

# ── First-time setup ─────────────────────────────────────────────────────────
setup:
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo ""; \
		echo "  .env created from .env.example."; \
		echo "  Open .env and fill in at minimum:"; \
		echo "    ANTHROPIC_API_KEY   — for Claude Opus (premium)"; \
		echo "    DEEPSEEK_API_KEY    — for DeepSeek (primary LLM)"; \
		echo "    OPENAI_API_KEY      — for embeddings"; \
		echo "    SECRET_KEY          — run: openssl rand -hex 32"; \
		echo ""; \
		echo "  For OAuth login (optional — use /api/dev/login in dev):"; \
		echo "    GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET"; \
		echo "      Redirect URI: http://localhost:8000/api/auth/google/callback"; \
		echo "    MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET"; \
		echo "      Redirect URI: http://localhost:8000/api/auth/microsoft/callback"; \
		echo ""; \
		echo "  Then run: make dev-build"; \
	else \
		echo ".env already exists — edit it directly."; \
	fi

# ── Dev (on-prem hypervisor) ─────────────────────────────────────────────────
dev:
	docker compose up

dev-build:
	docker compose up --build

dev-down:
	docker compose down

# ── Production (cloud VPS) ───────────────────────────────────────────────────
prod-up:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

prod-build:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

prod-down:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml down

prod-logs:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f

# ── Database ─────────────────────────────────────────────────────────────────
migrate:
	docker compose exec backend alembic upgrade head

migrate-down:
	docker compose exec backend alembic downgrade -1

shell-db:
	docker compose exec postgres psql -U legalapp legalapp

shell-backend:
	docker compose exec backend bash

# ── Logging ──────────────────────────────────────────────────────────────────
logs:
	docker compose logs -f

logs-backend:
	docker compose logs -f backend

# ── Data pipeline: on-prem → VPS ─────────────────────────────────────────────
# Syncs the public case-law chunk table (large!) to the production VPS.
# Requires VPS_HOST, VPS_USER, VPS_DB_PASS set in environment or .env.prod
sync-public-db:
	bash scripts/sync_to_vps.sh

deploy-prod:
	bash scripts/deploy_prod.sh

backup-db:
	bash scripts/backup_db.sh

restore-db:
	bash scripts/restore_db.sh

# ── CourtListener ingestion ───────────────────────────────────────────────────
embed-bulk:
	docker compose exec backend python /scripts/ingest_courtlistener.py \
	  --file /data/opinions.json.gz \
	  --db-url $(DATABASE_URL) \
	  --openai-key $(OPENAI_API_KEY)

# Phase 2: trigger Jetson workers (run on each Jetson via SSH)
jetson-embed:
	bash scripts/trigger_jetson_workers.sh

# ── Security / SBOM ─────────────────────────────────────────────────────────
sbom-inventory:
	python scripts/generate_sbom_inventory.py

# ── Code quality ─────────────────────────────────────────────────────────────
lint:
	docker compose exec backend ruff check app/

test:
	docker compose exec backend pytest tests/ -v

clean:
	docker compose down -v
	docker system prune -f
