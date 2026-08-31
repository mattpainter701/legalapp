import {
  cleanup,
  render,
  screen,
  waitFor,
  fireEvent,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";
import MatterWorkflowPanel from "./MatterWorkflowPanel";

const api = vi.hoisted(() => ({
  getMatterCustomFields: vi.fn(),
  getMatterWorkflowTemplates: vi.fn(),
  getMatterWorkflowRuns: vi.fn(),
  updateMatterCustomFields: vi.fn(),
  previewMatterWorkflow: vi.fn(),
  applyMatterWorkflow: vi.fn(),
  rollbackMatterWorkflow: vi.fn(),
  listWorkflowFields: vi.fn(),
  listWorkflowTemplates: vi.fn(),
  createWorkflowField: vi.fn(),
  updateWorkflowField: vi.fn(),
  createWorkflowTemplate: vi.fn(),
  approveWorkflowTemplateVersion: vi.fn(),
  archiveWorkflowTemplate: vi.fn(),
}));
vi.mock("../api", () => api);

describe("MatterWorkflowPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("crypto", { randomUUID: vi.fn(() => "preview-key") });
    api.getMatterCustomFields.mockResolvedValue({
      items: [{ id: "f1", label: "Court", field_type: "text", value: "" }],
    });
    api.getMatterWorkflowTemplates.mockResolvedValue({
      items: [
        {
          version_id: "v1",
          template_name: "Opening",
          status: "approved",
          version: 1,
        },
      ],
    });
    api.getMatterWorkflowRuns.mockResolvedValue({ items: [] });
    api.updateMatterCustomFields.mockResolvedValue({ items: [] });
    api.listWorkflowFields.mockResolvedValue({ items: [] });
    api.listWorkflowTemplates.mockResolvedValue({ items: [] });
    api.previewMatterWorkflow.mockResolvedValue({
      run_id: "r1",
      preview_sha256: "a".repeat(64),
      initial_stage: { stage_key: "initial", label: "Initial" },
      missing_required_fields: [],
      can_apply: true,
      tasks: [
        {
          item_key: "t1",
          title: "File petition",
          due_date: "2026-01-02",
          assignee_role: "matter_owner",
        },
      ],
    });
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });
  it("loads fields and gates apply behind approval capability", async () => {
    render(<MatterWorkflowPanel matterId="m1" user={{ capabilities: [] }} />);
    expect(await screen.findByLabelText("Court")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText(/No workflow runs/)).toBeInTheDocument(),
    );
  });
  it("embeds firm configuration only for workflow managers", async () => {
    const { rerender } = render(
      <MatterWorkflowPanel matterId="m1" user={{ capabilities: [] }} />,
    );
    await screen.findByLabelText("Court");
    expect(
      screen.queryByText("Firm workflow configuration"),
    ).not.toBeInTheDocument();

    rerender(
      <MatterWorkflowPanel
        matterId="m1"
        user={{ capabilities: ["manage_workflows"] }}
      />,
    );
    expect(
      await screen.findByText("Firm workflow configuration"),
    ).toBeInTheDocument();
    expect(await screen.findByText("Workflow settings")).toBeInTheDocument();
  });
  it("does not erase a redacted sensitive value unless the user replaces it", async () => {
    api.getMatterCustomFields.mockResolvedValue({
      items: [
        {
          id: "secret-1",
          label: "Tax ID",
          field_type: "text",
          sensitive: true,
          has_value: true,
          value: null,
        },
      ],
    });
    render(<MatterWorkflowPanel matterId="m1" user={{ capabilities: [] }} />);
    expect(
      await screen.findByText(/Sensitive value stored/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Save matter data/i }),
    ).toBeDisabled();
    expect(api.updateMatterCustomFields).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Tax ID"), {
      target: { value: "replacement" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save matter data/i }));
    await waitFor(() =>
      expect(api.updateMatterCustomFields).toHaveBeenCalledWith("m1", {
        values: [{ field_definition_id: "secret-1", value: "replacement" }],
      }),
    );
  });
  it("saves fields and renders a structured preview", async () => {
    render(
      <MatterWorkflowPanel
        matterId="m1"
        user={{ capabilities: ["approve_legal_work"] }}
      />,
    );
    fireEvent.change(await screen.findByLabelText("Court"), {
      target: { value: "Cook" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save matter data/i }));
    await waitFor(() =>
      expect(api.updateMatterCustomFields).toHaveBeenCalledWith("m1", {
        values: [{ field_definition_id: "f1", value: "Cook" }],
      }),
    );
    fireEvent.change(screen.getByLabelText("Approved workflow template"), {
      target: { value: "v1" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Preview workflow/i }));
    await waitFor(() =>
      expect(screen.getByText(/Initial stage:\s*Initial/)).toBeInTheDocument(),
    );
    expect(api.previewMatterWorkflow).toHaveBeenCalledWith(
      "m1",
      "v1",
      "preview-key",
    );
    expect(screen.getByText(/File petition/)).toBeInTheDocument();
  });
  it("retains the preview key for an ordinary retryable failure", async () => {
    api.previewMatterWorkflow.mockRejectedValueOnce({
      response: { status: 503, data: { detail: "Try again" } },
    });
    render(<MatterWorkflowPanel matterId="m1" user={{ capabilities: [] }} />);
    fireEvent.change(
      await screen.findByLabelText("Approved workflow template"),
      { target: { value: "v1" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /Preview workflow/i }));
    await screen.findByText("Try again");
    fireEvent.click(screen.getByRole("button", { name: /Preview workflow/i }));
    await screen.findByLabelText("Workflow preview");
    expect(api.previewMatterWorkflow).toHaveBeenNthCalledWith(
      1,
      "m1",
      "v1",
      "preview-key",
    );
    expect(api.previewMatterWorkflow).toHaveBeenNthCalledWith(
      2,
      "m1",
      "v1",
      "preview-key",
    );
  });
  it("offers a fresh idempotency key after a stale preview", async () => {
    globalThis.crypto.randomUUID
      .mockReturnValueOnce("stale-preview-key")
      .mockReturnValueOnce("fresh-preview-key");
    api.previewMatterWorkflow.mockRejectedValueOnce({
      response: { status: 409 },
    });
    render(<MatterWorkflowPanel matterId="m1" user={{ capabilities: [] }} />);
    fireEvent.change(
      await screen.findByLabelText("Approved workflow template"),
      { target: { value: "v1" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /Preview workflow/i }));
    await screen.findByText(/stale/i);
    expect(
      screen.getByRole("button", { name: /Preview workflow/i }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /New preview/i }));
    fireEvent.click(screen.getByRole("button", { name: /Preview workflow/i }));
    await screen.findByLabelText("Workflow preview");
    expect(api.previewMatterWorkflow).toHaveBeenNthCalledWith(
      1,
      "m1",
      "v1",
      "stale-preview-key",
    );
    expect(api.previewMatterWorkflow).toHaveBeenNthCalledWith(
      2,
      "m1",
      "v1",
      "fresh-preview-key",
    );
  });
  it("applies with the preview hash and offers rollback with stable key", async () => {
    api.getMatterWorkflowRuns.mockResolvedValue({
      items: [{ id: "r0", status: "applied", evidence_sha256: "e" }],
    });
    api.applyMatterWorkflow.mockResolvedValue({});
    api.rollbackMatterWorkflow.mockResolvedValue({});
    render(
      <MatterWorkflowPanel
        matterId="m1"
        user={{ capabilities: ["approve_legal_work"] }}
      />,
    );
    fireEvent.change(
      await screen.findByLabelText("Approved workflow template"),
      { target: { value: "v1" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /Preview workflow/i }));
    await waitFor(() =>
      screen.getByRole("button", { name: /Approve and apply/i }),
    );
    fireEvent.click(screen.getByRole("button", { name: /Approve and apply/i }));
    await waitFor(() =>
      expect(api.applyMatterWorkflow).toHaveBeenCalledWith("m1", "r1", {
        preview_sha256: "a".repeat(64),
        confirm_apply: true,
      }),
    );
    const reason = screen.getByLabelText(/Rollback reason/);
    fireEvent.change(reason, { target: { value: "Correction required" } });
    fireEvent.click(screen.getByRole("button", { name: "Rollback" }));
    await waitFor(() =>
      expect(api.rollbackMatterWorkflow).toHaveBeenCalledWith(
        "m1",
        "r0",
        { reason: "Correction required" },
        "preview-key",
      ),
    );
  });
  it("reloads immutable run evidence when rollback needs compensation", async () => {
    api.getMatterWorkflowRuns
      .mockResolvedValueOnce({ items: [{ id: "r0", status: "applied" }] })
      .mockResolvedValueOnce({
        items: [
          {
            id: "r0",
            status: "compensation_required",
            failure_detail: "task changed after apply",
            events: [{ evidence_sha256: "e".repeat(64) }],
          },
        ],
      });
    api.rollbackMatterWorkflow.mockRejectedValue({
      response: {
        status: 409,
        data: { detail: { message: "Manual compensation required" } },
      },
    });
    render(
      <MatterWorkflowPanel
        matterId="m1"
        user={{ capabilities: ["approve_legal_work"] }}
      />,
    );
    fireEvent.change(await screen.findByLabelText(/Rollback reason/), {
      target: { value: "Review changed task" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Rollback" }));
    expect(
      await screen.findByText("Manual compensation required"),
    ).toBeInTheDocument();
    expect(screen.getByText(/compensation_required/)).toBeInTheDocument();
    expect(screen.getByText("task changed after apply")).toBeInTheDocument();
    expect(api.getMatterWorkflowRuns).toHaveBeenCalledTimes(2);
  });
});
