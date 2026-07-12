-- Reviewed one-time repair for the production schema drift discovered while
-- pinning LiteLLM 1.93.0. The corresponding raw Prisma diff SHA-256 is:
-- e151961addd5f1146dd1c8fbd98b69cb4b3f599dc580b5e0ff128eb3dadd62e0
--
-- This file is executed only when reconcile_schema.sh sees that exact diff.
-- It is transactional and refuses to discard a populated legacy MCP field.
BEGIN;

DO $guard$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM "LiteLLM_MCPServerTable"
    WHERE "spec_version" IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'refusing to drop populated LiteLLM_MCPServerTable.spec_version';
  END IF;
END
$guard$;

ALTER TABLE "LiteLLM_BudgetTable" ADD COLUMN "allowed_models" TEXT[] DEFAULT ARRAY[]::TEXT[];

ALTER TABLE "LiteLLM_DailyTagSpend" ADD COLUMN "endpoint" TEXT,
ADD COLUMN "mcp_namespaced_tool_name" TEXT,
ADD COLUMN "request_id" TEXT,
ALTER COLUMN "model" DROP NOT NULL;

ALTER TABLE "LiteLLM_DailyTeamSpend" ADD COLUMN "endpoint" TEXT,
ADD COLUMN "mcp_namespaced_tool_name" TEXT,
ALTER COLUMN "model" DROP NOT NULL;

ALTER TABLE "LiteLLM_DailyUserSpend" ADD COLUMN "endpoint" TEXT,
ADD COLUMN "mcp_namespaced_tool_name" TEXT,
ALTER COLUMN "model" DROP NOT NULL;

ALTER TABLE "LiteLLM_EndUserTable" ADD COLUMN "object_permission_id" TEXT;

ALTER TABLE "LiteLLM_GuardrailsTable" ADD COLUMN "reviewed_at" TIMESTAMP(3),
ADD COLUMN "status" TEXT NOT NULL DEFAULT 'active',
ADD COLUMN "submitted_at" TIMESTAMP(3),
ADD COLUMN "team_id" TEXT;

ALTER TABLE "LiteLLM_MCPServerTable" DROP COLUMN "spec_version",
ADD COLUMN "allow_all_keys" BOOLEAN NOT NULL DEFAULT false,
ADD COLUMN "allowed_tools" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN "approval_status" TEXT DEFAULT 'active',
ADD COLUMN "args" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN "audience" TEXT,
ADD COLUMN "authorization_url" TEXT,
ADD COLUMN "available_on_public_internet" BOOLEAN NOT NULL DEFAULT true,
ADD COLUMN "byok_api_key_help_url" TEXT,
ADD COLUMN "byok_description" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN "command" TEXT,
ADD COLUMN "credentials" JSONB DEFAULT '{}',
ADD COLUMN "delegate_auth_to_upstream" BOOLEAN NOT NULL DEFAULT false,
ADD COLUMN "env" JSONB DEFAULT '{}',
ADD COLUMN "env_vars" JSONB DEFAULT '[]',
ADD COLUMN "extra_headers" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN "health_check_error" TEXT,
ADD COLUMN "instructions" TEXT,
ADD COLUMN "is_byok" BOOLEAN NOT NULL DEFAULT false,
ADD COLUMN "last_health_check" TIMESTAMP(3),
ADD COLUMN "max_concurrent_requests" INTEGER,
ADD COLUMN "mcp_access_groups" TEXT[],
ADD COLUMN "mcp_info" JSONB DEFAULT '{}',
ADD COLUMN "oauth2_flow" TEXT,
ADD COLUMN "oauth_passthrough" BOOLEAN NOT NULL DEFAULT false,
ADD COLUMN "registration_url" TEXT,
ADD COLUMN "review_notes" TEXT,
ADD COLUMN "reviewed_at" TIMESTAMP(3),
ADD COLUMN "server_name" TEXT,
ADD COLUMN "source_url" TEXT,
ADD COLUMN "spec_path" TEXT,
ADD COLUMN "static_headers" JSONB DEFAULT '{}',
ADD COLUMN "status" TEXT DEFAULT 'unknown',
ADD COLUMN "subject_token_type" TEXT,
ADD COLUMN "submitted_at" TIMESTAMP(3),
ADD COLUMN "submitted_by" TEXT,
ADD COLUMN "timeout" DOUBLE PRECISION,
ADD COLUMN "token_exchange_endpoint" TEXT,
ADD COLUMN "token_exchange_profile" TEXT,
ADD COLUMN "token_url" TEXT,
ADD COLUMN "tool_name_to_description" JSONB DEFAULT '{}',
ADD COLUMN "tool_name_to_display_name" JSONB DEFAULT '{}',
ALTER COLUMN "url" DROP NOT NULL;

ALTER TABLE "LiteLLM_ManagedFileTable" ADD COLUMN "storage_backend" TEXT,
ADD COLUMN "storage_url" TEXT,
ADD COLUMN "team_id" TEXT,
ALTER COLUMN "file_object" DROP NOT NULL;

ALTER TABLE "LiteLLM_ManagedObjectTable" ADD COLUMN "batch_processed" BOOLEAN NOT NULL DEFAULT false,
ADD COLUMN "status" TEXT,
ADD COLUMN "team_id" TEXT;

ALTER TABLE "LiteLLM_ManagedVectorStoresTable" ADD COLUMN "litellm_params" JSONB,
ADD COLUMN "team_id" TEXT,
ADD COLUMN "user_id" TEXT;

ALTER TABLE "LiteLLM_ObjectPermissionTable" ADD COLUMN "agent_access_groups" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN "agents" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN "blocked_tools" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN "mcp_access_groups" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN "mcp_tool_permissions" JSONB,
ADD COLUMN "mcp_tool_search_enabled" BOOLEAN,
ADD COLUMN "mcp_toolsets" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN "models" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN "search_tools" TEXT[] DEFAULT ARRAY[]::TEXT[];

ALTER TABLE "LiteLLM_ProxyModelTable" ADD COLUMN "blocked" BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE "LiteLLM_SpendLogs" ADD COLUMN "agent_id" TEXT,
ADD COLUMN "mcp_namespaced_tool_name" TEXT,
ADD COLUMN "organization_id" TEXT,
ADD COLUMN "request_duration_ms" INTEGER;

ALTER TABLE "LiteLLM_TeamMembership" ADD COLUMN "total_spend" DOUBLE PRECISION NOT NULL DEFAULT 0.0;

ALTER TABLE "LiteLLM_TeamTable" ADD COLUMN "access_group_ids" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN "allow_team_guardrail_config" BOOLEAN NOT NULL DEFAULT false,
ADD COLUMN "budget_limits" JSONB,
ADD COLUMN "default_team_member_models" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN "policies" TEXT[] DEFAULT ARRAY[]::TEXT[],
ADD COLUMN "router_settings" JSONB DEFAULT '{}',
ADD COLUMN "soft_budget" DOUBLE PRECISION;

ALTER TABLE "LiteLLM_UserTable" ADD COLUMN "policies" TEXT[] DEFAULT ARRAY[]::TEXT[];

CREATE INDEX "LiteLLM_DailyTagSpend_tag_date_idx" ON "LiteLLM_DailyTagSpend"("tag", "date");
CREATE INDEX "LiteLLM_DailyTagSpend_mcp_namespaced_tool_name_idx" ON "LiteLLM_DailyTagSpend"("mcp_namespaced_tool_name");
CREATE INDEX "LiteLLM_DailyTagSpend_endpoint_idx" ON "LiteLLM_DailyTagSpend"("endpoint");
CREATE UNIQUE INDEX "LiteLLM_DailyTagSpend_tag_date_api_key_model_custom_llm_pro_key" ON "LiteLLM_DailyTagSpend"("tag", "date", "api_key", "model", "custom_llm_provider", "mcp_namespaced_tool_name", "endpoint");
CREATE INDEX "LiteLLM_DailyTeamSpend_team_id_date_idx" ON "LiteLLM_DailyTeamSpend"("team_id", "date");
CREATE INDEX "LiteLLM_DailyTeamSpend_mcp_namespaced_tool_name_idx" ON "LiteLLM_DailyTeamSpend"("mcp_namespaced_tool_name");
CREATE INDEX "LiteLLM_DailyTeamSpend_endpoint_idx" ON "LiteLLM_DailyTeamSpend"("endpoint");
CREATE UNIQUE INDEX "LiteLLM_DailyTeamSpend_team_id_date_api_key_model_custom_ll_key" ON "LiteLLM_DailyTeamSpend"("team_id", "date", "api_key", "model", "custom_llm_provider", "mcp_namespaced_tool_name", "endpoint");
CREATE INDEX "LiteLLM_DailyUserSpend_user_id_date_idx" ON "LiteLLM_DailyUserSpend"("user_id", "date");
CREATE INDEX "LiteLLM_DailyUserSpend_mcp_namespaced_tool_name_idx" ON "LiteLLM_DailyUserSpend"("mcp_namespaced_tool_name");
CREATE INDEX "LiteLLM_DailyUserSpend_endpoint_idx" ON "LiteLLM_DailyUserSpend"("endpoint");
CREATE UNIQUE INDEX "LiteLLM_DailyUserSpend_user_id_date_api_key_model_custom_ll_key" ON "LiteLLM_DailyUserSpend"("user_id", "date", "api_key", "model", "custom_llm_provider", "mcp_namespaced_tool_name", "endpoint");
CREATE INDEX "LiteLLM_GuardrailsTable_status_idx" ON "LiteLLM_GuardrailsTable"("status");
CREATE INDEX "LiteLLM_MCPServerTable_approval_status_idx" ON "LiteLLM_MCPServerTable"("approval_status");
CREATE INDEX "LiteLLM_ManagedFileTable_team_id_created_at_idx" ON "LiteLLM_ManagedFileTable"("team_id", "created_at" DESC);
CREATE INDEX "LiteLLM_ManagedObjectTable_team_id_created_at_idx" ON "LiteLLM_ManagedObjectTable"("team_id", "created_at" DESC);
CREATE INDEX "LiteLLM_ManagedVectorStoresTable_team_id_idx" ON "LiteLLM_ManagedVectorStoresTable"("team_id");
CREATE INDEX "LiteLLM_ManagedVectorStoresTable_user_id_idx" ON "LiteLLM_ManagedVectorStoresTable"("user_id");
CREATE INDEX "LiteLLM_SpendLogs_startTime_request_id_idx" ON "LiteLLM_SpendLogs"("startTime", "request_id");
CREATE INDEX "LiteLLM_TeamTable_organization_id_idx" ON "LiteLLM_TeamTable"("organization_id");
CREATE INDEX "LiteLLM_TeamTable_team_alias_idx" ON "LiteLLM_TeamTable"("team_alias");
CREATE INDEX "LiteLLM_TeamTable_created_at_idx" ON "LiteLLM_TeamTable"("created_at");
CREATE INDEX "LiteLLM_VerificationToken_user_id_team_id_idx" ON "LiteLLM_VerificationToken"("user_id", "team_id");
CREATE INDEX "LiteLLM_VerificationToken_team_id_idx" ON "LiteLLM_VerificationToken"("team_id");
CREATE INDEX "LiteLLM_VerificationToken_budget_reset_at_expires_idx" ON "LiteLLM_VerificationToken"("budget_reset_at", "expires");

ALTER TABLE "LiteLLM_AgentsTable" ADD CONSTRAINT "LiteLLM_AgentsTable_object_permission_id_fkey" FOREIGN KEY ("object_permission_id") REFERENCES "LiteLLM_ObjectPermissionTable"("object_permission_id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_ProjectTable" ADD CONSTRAINT "LiteLLM_ProjectTable_team_id_fkey" FOREIGN KEY ("team_id") REFERENCES "LiteLLM_TeamTable"("team_id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_ProjectTable" ADD CONSTRAINT "LiteLLM_ProjectTable_budget_id_fkey" FOREIGN KEY ("budget_id") REFERENCES "LiteLLM_BudgetTable"("budget_id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_ProjectTable" ADD CONSTRAINT "LiteLLM_ProjectTable_object_permission_id_fkey" FOREIGN KEY ("object_permission_id") REFERENCES "LiteLLM_ObjectPermissionTable"("object_permission_id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_VerificationToken" ADD CONSTRAINT "LiteLLM_VerificationToken_project_id_fkey" FOREIGN KEY ("project_id") REFERENCES "LiteLLM_ProjectTable"("project_id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_JWTKeyMapping" ADD CONSTRAINT "LiteLLM_JWTKeyMapping_token_fkey" FOREIGN KEY ("token") REFERENCES "LiteLLM_VerificationToken"("token") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_EndUserTable" ADD CONSTRAINT "LiteLLM_EndUserTable_object_permission_id_fkey" FOREIGN KEY ("object_permission_id") REFERENCES "LiteLLM_ObjectPermissionTable"("object_permission_id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_TagTable" ADD CONSTRAINT "LiteLLM_TagTable_budget_id_fkey" FOREIGN KEY ("budget_id") REFERENCES "LiteLLM_BudgetTable"("budget_id") ON DELETE SET NULL ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_WorkflowEvent" ADD CONSTRAINT "LiteLLM_WorkflowEvent_run_id_fkey" FOREIGN KEY ("run_id") REFERENCES "LiteLLM_WorkflowRun"("run_id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "LiteLLM_WorkflowMessage" ADD CONSTRAINT "LiteLLM_WorkflowMessage_run_id_fkey" FOREIGN KEY ("run_id") REFERENCES "LiteLLM_WorkflowRun"("run_id") ON DELETE RESTRICT ON UPDATE CASCADE;

COMMIT;
