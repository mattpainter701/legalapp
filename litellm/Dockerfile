# Pin the exact multi-arch image that matches the production Prisma schema.
# Never downgrade this independently of a rehearsed LiteLLM database restore.
FROM docker.litellm.ai/berriai/litellm:main-latest@sha256:60f548df23a82b7f83444e2f01d2ccafe00f9f5808c4110a32e397083d09fb17

# Prisma looks for migrations beside /app/schema.prisma. LiteLLM 1.93 packages
# them under litellm_proxy_extras, so expose that immutable directory without
# copying or modifying vendor SQL.
RUN ln -s /app/.venv/lib/python3.13/site-packages/litellm_proxy_extras/migrations /app/migrations

COPY litellm_config.yaml /app/config.yaml
COPY litellm/reconcile_schema.sh /app/legalapp/reconcile_schema.sh
COPY litellm/litellm_schema_repair_v1_93_0.sql /app/legalapp/litellm_schema_repair_v1_93_0.sql
COPY litellm/runtime_entrypoint.sh /app/legalapp/runtime_entrypoint.sh
RUN chmod 0555 /app/legalapp/reconcile_schema.sh /app/legalapp/runtime_entrypoint.sh

ENTRYPOINT ["/app/legalapp/runtime_entrypoint.sh"]
