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
    api.listWorkflowFields.mockResolvedValue({ items: [] });
    api.listWorkflowTemplates.mockResolvedValue({
      items: [
        {
          template_id: "t1",
          template_name: "Opening",
          version_id: "v1",
          version: 1,
          status: "draft",
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
