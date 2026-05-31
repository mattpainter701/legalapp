.PHONY: dev dev-build prod-up prod-down prod-build migrate logs shell \
        sync-public-db deploy-prod backup-db restore-db embed-bulk \
        jetson-embed lint test clean

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

# ── Code quality ─────────────────────────────────────────────────────────────
lint:
	docker compose exec backend ruff check app/

test:
	docker compose exec backend pytest tests/ -v

clean:
	docker compose down -v
	docker system prune -f
