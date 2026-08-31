import { beforeEach, describe, expect, it, vi } from "vitest";

const client = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  interceptors: {
    request: { use: vi.fn() },
    response: { use: vi.fn() },
  },
}));

vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => client),
    post: vi.fn(),
  },
}));

const {
  applyMatterWorkflow,
  approveWorkflowTemplateVersion,
  archiveWorkflowTemplate,
  createWorkflowTemplateVersion,
  previewMatterWorkflow,
  rollbackMatterWorkflow,
  updateMatterCustomFields,
} = await import("./api");

describe("configurable workflow API contracts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    client.get.mockResolvedValue({ data: {} });
    client.post.mockResolvedValue({ data: {} });
    client.put.mockResolvedValue({ data: {} });
    client.patch.mockResolvedValue({ data: {} });
  });

  it("sends typed field values and the caller-owned preview key", async () => {
    const values = {
      values: [{ field_definition_id: "field-1", value: true }],
    };
    await updateMatterCustomFields("matter-1", values);
    await previewMatterWorkflow("matter-1", "version-2", "preview-attempt-1");

    expect(client.put).toHaveBeenCalledWith(
      "/matters/matter-1/custom-fields",
      values,
    );
    expect(client.post).toHaveBeenCalledWith(
      "/matters/matter-1/workflow-runs/preview",
      null,
      {
        params: { template_version_id: "version-2" },
        headers: { "Idempotency-Key": "preview-attempt-1" },
      },
    );
  });

  it("preserves exact approval evidence and rollback idempotency", async () => {
    const approval = { preview_sha256: "a".repeat(64), confirm_apply: true };
    await applyMatterWorkflow("matter-1", "run-1", approval);
    await rollbackMatterWorkflow(
      "matter-1",
      "run-1",
      { reason: "Template selected in error" },
      "rollback-attempt-1",
    );

    expect(client.post).toHaveBeenNthCalledWith(
      1,
      "/matters/matter-1/workflow-runs/run-1/apply",
      approval,
    );
    expect(client.post).toHaveBeenNthCalledWith(
      2,
      "/matters/matter-1/workflow-runs/run-1/rollback",
      { reason: "Template selected in error" },
      { headers: { "Idempotency-Key": "rollback-attempt-1" } },
    );
  });

  it("uses explicit template and version identifiers with bounded version input", async () => {
    const definition = {
      expected_latest_version: 2,
      initial_stage_key: "intake",
      stages: [{ stage_key: "intake", label: "Intake" }],
      checklist: [
        {
          item_key: "review",
          stage_key: "intake",
          title: "Review file",
          task_type: "review",
          priority: "medium",
          due_offset_days: 1,
          assignee_role: "matter_owner",
        },
      ],
      required_field_definition_ids: [],
    };
    await createWorkflowTemplateVersion("template-1", definition);
    await approveWorkflowTemplateVersion("template-1", "version-3");
    await archiveWorkflowTemplate("template-1");

    expect(client.post).toHaveBeenNthCalledWith(
      1,
      "/workflow-config/templates/template-1/versions",
      definition,
    );
    expect(client.post).toHaveBeenNthCalledWith(
      2,
      "/workflow-config/templates/template-1/versions/version-3/approve",
    );
    expect(client.post).toHaveBeenNthCalledWith(
      3,
      "/workflow-config/templates/template-1/archive",
    );
  });
});
