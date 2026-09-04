import {
  cleanup,
  render,
  screen,
  waitFor,
  fireEvent,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WorkflowAutomationRules from "./WorkflowAutomationRules";

const api = vi.hoisted(() => ({
  listWorkflowAutomations: vi.fn(),
  createWorkflowAutomation: vi.fn(),
  updateWorkflowAutomation: vi.fn(),
  activateWorkflowAutomation: vi.fn(),
  archiveWorkflowAutomation: vi.fn(),
  getWorkflowAutomationEvents: vi.fn(),
}));
vi.mock("../../api", () => api);

const TEMPLATES = [
  {
    template_id: "t1",
    template_name: "Matter opening",
    status: "approved",
    version: 2,
  },
  {
    template_id: "t1",
    template_name: "Matter opening",
    status: "draft",
    version: 3,
  },
  {
    template_id: "t2",
    template_name: "Never approved",
    status: "draft",
    version: 1,
  },
];

const DRAFT_RULE = {
  id: "r1",
  name: "Open litigation matters",
  trigger_event: "matter_created",
  trigger_stage: null,
  match_matter_type: "Litigation",
  match_practice_area: null,
  template_id: "t1",
  template_name: "Matter opening",
  status: "draft",
  definition_sha256: "a".repeat(64),
};

describe("WorkflowAutomationRules", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listWorkflowAutomations.mockResolvedValue({ items: [DRAFT_RULE] });
    api.createWorkflowAutomation.mockResolvedValue({ id: "r2" });
    api.updateWorkflowAutomation.mockResolvedValue({ id: "r1" });
    api.activateWorkflowAutomation.mockResolvedValue({
      ...DRAFT_RULE,
      status: "active",
    });
    api.archiveWorkflowAutomation.mockResolvedValue({
      ...DRAFT_RULE,
      status: "archived",
    });
    api.getWorkflowAutomationEvents.mockResolvedValue({ items: [] });
  });
  afterEach(cleanup);

  it("offers only approved templates and never repeats one", async () => {
    render(
      <WorkflowAutomationRules
        user={{ capabilities: ["manage_workflows"] }}
        templates={TEMPLATES}
      />,
    );
    await waitFor(() => expect(api.listWorkflowAutomations).toHaveBeenCalled());

    const select = screen.getByLabelText("Plan this approved template");
    const options = [...select.querySelectorAll("option")].map((o) => o.value);
    expect(options).toEqual(["", "t1"]);
  });

  it("creates a draft rule with only the conditions the firm filled in", async () => {
    render(
      <WorkflowAutomationRules
        user={{ capabilities: ["manage_workflows"] }}
        templates={TEMPLATES}
      />,
    );
    await waitFor(() => expect(api.listWorkflowAutomations).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("Rule name"), {
      target: { value: "  Open litigation matters " },
    });
    fireEvent.change(screen.getByLabelText("Only for matter type (optional)"), {
      target: { value: "Litigation" },
    });
    fireEvent.change(screen.getByLabelText("Plan this approved template"), {
      target: { value: "t1" },
    });
    fireEvent.click(screen.getByText("Create draft rule"));

    await waitFor(() =>
      expect(api.createWorkflowAutomation).toHaveBeenCalledWith({
        name: "Open litigation matters",
        trigger_event: "matter_created",
        trigger_stage: null,
        match_matter_type: "Litigation",
        match_practice_area: null,
        template_id: "t1",
      }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "runs only after approval",
    );
  });

  it("asks for a stage only when the trigger is a stage change", async () => {
    render(
      <WorkflowAutomationRules
        user={{ capabilities: ["manage_workflows"] }}
        templates={TEMPLATES}
      />,
    );
    await waitFor(() => expect(api.listWorkflowAutomations).toHaveBeenCalled());
    expect(screen.queryByLabelText("Stage")).toBeNull();

    fireEvent.change(screen.getByLabelText("When"), {
      target: { value: "matter_stage_changed" },
    });
    fireEvent.change(screen.getByLabelText("Stage"), {
      target: { value: " Discovery " },
    });
    fireEvent.change(screen.getByLabelText("Rule name"), {
      target: { value: "Discovery checklist" },
    });
    fireEvent.change(screen.getByLabelText("Plan this approved template"), {
      target: { value: "t1" },
    });
    fireEvent.click(screen.getByText("Create draft rule"));

    await waitFor(() =>
      expect(api.createWorkflowAutomation).toHaveBeenCalledWith(
        expect.objectContaining({
          trigger_event: "matter_stage_changed",
          trigger_stage: "Discovery",
        }),
      ),
    );
  });

  it("keeps activation to approvers and sends the reviewed fingerprint", async () => {
    const { unmount } = render(
      <WorkflowAutomationRules
        user={{ capabilities: ["manage_workflows"] }}
        templates={TEMPLATES}
      />,
    );
    await waitFor(() => expect(api.listWorkflowAutomations).toHaveBeenCalled());
    expect(screen.queryByText("Approve and activate")).toBeNull();
    unmount();

    render(
      <WorkflowAutomationRules
        user={{ capabilities: ["approve_legal_work"] }}
        templates={TEMPLATES}
      />,
    );
    await waitFor(() =>
      expect(screen.getByText("Approve and activate")).toBeTruthy(),
    );
    // An approver is not an author: no rule form is offered.
    expect(screen.queryByLabelText("Rule name")).toBeNull();

    fireEvent.click(screen.getByText("Approve and activate"));
    await waitFor(() =>
      expect(api.activateWorkflowAutomation).toHaveBeenCalledWith("r1", {
        definition_sha256: "a".repeat(64),
        confirm_activate: true,
      }),
    );
  });

  it("edits an active rule back into a draft and explains why", async () => {
    api.listWorkflowAutomations.mockResolvedValue({
      items: [{ ...DRAFT_RULE, status: "active" }],
    });
    render(
      <WorkflowAutomationRules
        user={{ capabilities: ["manage_workflows"] }}
        templates={TEMPLATES}
      />,
    );
    await waitFor(() => expect(screen.getByText("Edit")).toBeTruthy());

    fireEvent.click(screen.getByText("Edit"));
    expect(screen.getByLabelText("Rule name").value).toBe(
      "Open litigation matters",
    );
    fireEvent.change(screen.getByLabelText("Rule name"), {
      target: { value: "Renamed rule" },
    });
    fireEvent.click(screen.getByText("Save draft"));

    await waitFor(() =>
      expect(api.updateWorkflowAutomation).toHaveBeenCalledWith(
        "r1",
        expect.objectContaining({ name: "Renamed rule", template_id: "t1" }),
      ),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "needs approval before it runs again",
    );
  });

  it("shows why a rule did nothing instead of leaving it silent", async () => {
    api.getWorkflowAutomationEvents.mockResolvedValue({
      items: [
        {
          id: "e1",
          outcome: "blocked",
          created_at: "2026-09-04T10:00:00Z",
          detail: { failure_code: "template_not_approved", message: "No version" },
        },
      ],
    });
    render(
      <WorkflowAutomationRules
        user={{ capabilities: ["manage_workflows"] }}
        templates={TEMPLATES}
      />,
    );
    await waitFor(() => expect(screen.getByText("Show activity")).toBeTruthy());

    fireEvent.click(screen.getByText("Show activity"));
    const activity = await screen.findByLabelText(
      "Activity for Open litigation matters",
    );
    expect(activity.textContent).toContain("Blocked: No version");
  });

  it("surfaces a rejected save without clearing the form", async () => {
    api.createWorkflowAutomation.mockRejectedValue({
      response: { data: { detail: "Another automation rule already uses that name" } },
    });
    render(
      <WorkflowAutomationRules
        user={{ capabilities: ["manage_workflows"] }}
        templates={TEMPLATES}
      />,
    );
    await waitFor(() => expect(api.listWorkflowAutomations).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText("Rule name"), {
      target: { value: "Open litigation matters" },
    });
    fireEvent.change(screen.getByLabelText("Plan this approved template"), {
      target: { value: "t1" },
    });
    fireEvent.click(screen.getByText("Create draft rule"));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "already uses that name",
    );
    expect(screen.getByLabelText("Rule name").value).toBe(
      "Open litigation matters",
    );
  });
});
