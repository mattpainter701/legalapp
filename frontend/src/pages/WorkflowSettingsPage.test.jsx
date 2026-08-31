import {
  cleanup,
  render,
  screen,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import WorkflowSettingsPage from "./WorkflowSettingsPage";

const api = vi.hoisted(() => ({
  listWorkflowFields: vi.fn(() => Promise.resolve({ items: [] })),
  listWorkflowTemplates: vi.fn(() =>
    Promise.resolve({
      items: [
        {
          template_id: "t1",
          template_name: "Opening",
          version_id: "v1",
          version: 1,
          status: "draft",
          template_active: true,
          initial_stage_key: "initial",
          stages: [{ stage_key: "initial", label: "Initial" }],
          checklist: [
            {
              item_key: "review",
              stage_key: "initial",
              title: "Review file",
              task_type: "review",
              priority: "medium",
              due_offset_days: 2,
              assignee_role: "matter_owner",
            },
          ],
          required_fields: [{ id: "field-1" }],
        },
      ],
    }),
  ),
  createWorkflowField: vi.fn(() => Promise.resolve({})),
  updateWorkflowField: vi.fn(() => Promise.resolve({})),
  createWorkflowTemplate: vi.fn(() => Promise.resolve({})),
  createWorkflowTemplateVersion: vi.fn(),
  approveWorkflowTemplateVersion: vi.fn(() => Promise.resolve({})),
  archiveWorkflowTemplate: vi.fn(() => Promise.resolve({})),
}));
vi.mock("../api", () => api);

describe("WorkflowSettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.createWorkflowTemplateVersion.mockResolvedValue({});
    api.approveWorkflowTemplateVersion.mockResolvedValue({});
    api.archiveWorkflowTemplate.mockResolvedValue({});
    api.listWorkflowFields.mockResolvedValue({ items: [] });
    api.listWorkflowTemplates.mockResolvedValue({
      items: [
        {
          template_id: "t1",
          template_name: "Opening",
          version_id: "v1",
          version: 1,
          status: "draft",
          template_active: true,
          initial_stage_key: "initial",
          stages: [{ stage_key: "initial", label: "Initial" }],
          checklist: [
            {
              item_key: "review",
              stage_key: "initial",
              title: "Review file",
              task_type: "review",
              priority: "medium",
              due_offset_days: 2,
              assignee_role: "matter_owner",
            },
          ],
          required_fields: [{ id: "field-1" }],
        },
      ],
    });
  });
  afterEach(cleanup);
  it("requires manage_workflows and explains approval separation", () => {
    render(<WorkflowSettingsPage user={{ capabilities: [] }} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/manage_workflows/);
  });
  it("shows exactly five bounded starter presets to managers", async () => {
    render(
      <WorkflowSettingsPage user={{ capabilities: ["manage_workflows"] }} />,
    );
    expect(
      await screen.findByRole("button", {
        name: /Use New matter opening preset/i,
      }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Use / })).toHaveLength(5);
    const approvalCapability = screen.getByText("approve_legal_work");
    expect(approvalCapability.parentElement).toHaveTextContent(
      /approve_legal_work is intentionally separate from manage_workflows/i,
    );
  });
  it("uses flat template version IDs for approval and archive", async () => {
    render(
      <WorkflowSettingsPage
        user={{ capabilities: ["manage_workflows", "approve_legal_work"] }}
      />,
    );
    await screen.findByText("Opening");
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(api.approveWorkflowTemplateVersion).toHaveBeenCalledWith(
        "t1",
        "v1",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Archive template" }));
    await waitFor(() =>
      expect(api.archiveWorkflowTemplate).toHaveBeenCalledWith("t1"),
    );
  });
  it("submits bounded field options and editable preset data", async () => {
    render(
      <WorkflowSettingsPage user={{ capabilities: ["manage_workflows"] }} />,
    );
    await screen.findByRole("button", { name: /Create field definition/i });
    fireEvent.change(screen.getByLabelText("Field key"), {
      target: { value: "court" },
    });
    fireEvent.change(screen.getByLabelText("Field label"), {
      target: { value: "Court" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /Create field definition/i }),
    );
    await waitFor(() =>
      expect(api.createWorkflowField).toHaveBeenCalledWith(
        expect.objectContaining({ field_key: "court", options: [] }),
      ),
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Use New matter opening preset/i }),
    );
    expect(screen.getByDisplayValue("New matter opening")).toBeInTheDocument();
  });
  it("lets approval-only users discover, review, and approve drafts", async () => {
    render(
      <WorkflowSettingsPage user={{ capabilities: ["approve_legal_work"] }} />,
    );
    expect(await screen.findByText("Opening")).toBeInTheDocument();
    expect(api.listWorkflowFields).not.toHaveBeenCalled();
    expect(
      screen.queryByRole("button", { name: "Create template draft" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Archive template" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(api.approveWorkflowTemplateVersion).toHaveBeenCalledWith(
        "t1",
        "v1",
      ),
    );
  });
  it("creates a bounded next version with optimistic concurrency", async () => {
    api.createWorkflowTemplateVersion.mockResolvedValue({});
    render(
      <WorkflowSettingsPage user={{ capabilities: ["manage_workflows"] }} />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Create next version" }),
    );
    expect(screen.getByText(/Editing from v1/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Checklist title 1"), {
      target: { value: "Review updated file" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Create next version draft" }),
    );
    await waitFor(() =>
      expect(api.createWorkflowTemplateVersion).toHaveBeenCalledWith("t1", {
        expected_latest_version: 1,
        initial_stage_key: "initial",
        stages: [{ stage_key: "initial", label: "Initial" }],
        checklist: [
          expect.objectContaining({
            item_key: "review",
            title: "Review updated file",
            due_offset_days: 2,
          }),
        ],
        required_field_definition_ids: ["field-1"],
      }),
    );
  });
  it("reloads and exits version editing after a stale conflict", async () => {
    api.createWorkflowTemplateVersion.mockRejectedValue({
      response: { status: 409, data: { detail: "stale" } },
    });
    render(
      <WorkflowSettingsPage user={{ capabilities: ["manage_workflows"] }} />,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Create next version" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Create next version draft" }),
    );
    expect(
      await screen.findByText(/settings were reloaded.*Start the new version/i),
    ).toBeInTheDocument();
    expect(api.listWorkflowTemplates).toHaveBeenCalledTimes(2);
    expect(
      screen.getByRole("button", { name: "Create template draft" }),
    ).toBeInTheDocument();
  });
  it("shows lifecycle errors and reloads conflicts", async () => {
    api.approveWorkflowTemplateVersion.mockRejectedValue({
      response: { status: 409, data: { detail: "Approval became stale" } },
    });
    render(
      <WorkflowSettingsPage
        user={{ capabilities: ["manage_workflows", "approve_legal_work"] }}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    expect(await screen.findByText("Approval became stale")).toBeInTheDocument();
    expect(api.listWorkflowTemplates).toHaveBeenCalledTimes(2);
  });
  it("keeps inactive fields visible so they can be reactivated", async () => {
    api.listWorkflowFields
      .mockResolvedValueOnce({
        items: [
          {
            id: "field-1",
            label: "Court",
            active: true,
            schema_version: 1,
          },
        ],
      })
      .mockResolvedValueOnce({
        items: [
          {
            id: "field-1",
            label: "Court",
            active: false,
            schema_version: 2,
          },
        ],
      })
      .mockResolvedValueOnce({
        items: [
          {
            id: "field-1",
            label: "Court",
            active: true,
            schema_version: 3,
          },
        ],
      });
    render(
      <WorkflowSettingsPage user={{ capabilities: ["manage_workflows"] }} />,
    );
    expect(await screen.findByText(/Court · active/)).toBeInTheDocument();
    expect(api.listWorkflowFields).toHaveBeenCalledWith({
      include_inactive: true,
    });
    fireEvent.click(screen.getByRole("button", { name: /Toggle active/i }));
    await waitFor(() =>
      expect(api.updateWorkflowField).toHaveBeenNthCalledWith(1, "field-1", {
        active: false,
        expected_schema_version: 1,
      }),
    );
    expect(await screen.findByText(/Court · inactive/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Toggle active/i }));
    await waitFor(() =>
      expect(api.updateWorkflowField).toHaveBeenNthCalledWith(2, "field-1", {
        active: true,
        expected_schema_version: 2,
      }),
    );
    expect(await screen.findByText(/Court · active/)).toBeInTheDocument();
  });
  it("binds required fields and checklist stages into a bounded draft", async () => {
    api.listWorkflowFields.mockResolvedValue({
      items: [
        {
          id: "field-1",
          entity_type: "matter",
          label: "Court deadline",
          active: true,
        },
      ],
    });
    render(
      <WorkflowSettingsPage user={{ capabilities: ["manage_workflows"] }} />,
    );
    fireEvent.click(
      await screen.findByRole("button", {
        name: /Use New matter opening preset/i,
      }),
    );
    fireEvent.click(screen.getByLabelText("Court deadline"));
    fireEvent.change(screen.getByLabelText("Stage 1 key"), {
      target: { value: "conflict_review" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /Create template draft/i }),
    );
    await waitFor(() =>
      expect(api.createWorkflowTemplate).toHaveBeenCalledWith(
        expect.objectContaining({
          initial_stage_key: "conflict_review",
          required_field_definition_ids: ["field-1"],
          stages: expect.arrayContaining([
            expect.objectContaining({ stage_key: "conflict_review" }),
          ]),
          checklist: expect.arrayContaining([
            expect.objectContaining({
              stage_key: "conflict_review",
              due_offset_days: 0,
            }),
          ]),
        }),
      ),
    );
  });
});
