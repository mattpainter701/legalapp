import { useCallback, useEffect, useMemo, useState } from "react";
import {
  listWorkflowAutomations,
  createWorkflowAutomation,
  updateWorkflowAutomation,
  activateWorkflowAutomation,
  archiveWorkflowAutomation,
  getWorkflowAutomationEvents,
} from "../../api";

const TRIGGERS = [
  ["matter_created", "A matter is opened"],
  ["matter_stage_changed", "A matter enters a stage"],
];
const asItems = (value) =>
  Array.isArray(value) ? value : value?.items || value?.rules || [];
const errText = (e, fallback) => {
  const detail = e?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  return e?.message || fallback;
};
const blankForm = () => ({
  name: "",
  trigger_event: "matter_created",
  trigger_stage: "",
  match_matter_type: "",
  match_practice_area: "",
  template_id: "",
});

export default function WorkflowAutomationRules({ user, templates = [] }) {
  const canManage = (user?.capabilities || []).includes("manage_workflows");
  const canApprove = (user?.capabilities || []).includes("approve_legal_work");
  const [rules, setRules] = useState([]);
  const [events, setEvents] = useState({});
  const [form, setForm] = useState(blankForm);
  const [editing, setEditing] = useState(null);
  const [message, setMessage] = useState(null);
  const [working, setWorking] = useState(false);

  // Only a template with an approved version can be planned, so only those
  // are offered here. Activation re-checks this server-side.
  const approvedTemplates = useMemo(() => {
    const seen = new Map();
    for (const template of templates) {
      const id = template.template_id || template.id;
      if (!id || template.status !== "approved") continue;
      if (!seen.has(id))
        seen.set(id, template.template_name || template.name || id);
    }
    return [...seen.entries()];
  }, [templates]);

  const load = useCallback(async () => {
    try {
      setRules(asItems(await listWorkflowAutomations({})));
    } catch (e) {
      setMessage({ type: "error", text: errText(e, "Unable to load rules.") });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const run = async (action, success) => {
    setWorking(true);
    setMessage(null);
    try {
      await action();
      await load();
      setMessage({ type: "success", text: success });
      return true;
    } catch (e) {
      setMessage({ type: "error", text: errText(e, "Unable to save rule.") });
      return false;
    } finally {
      setWorking(false);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    const payload = {
      name: form.name.trim(),
      trigger_event: form.trigger_event,
      trigger_stage:
        form.trigger_event === "matter_stage_changed"
          ? form.trigger_stage.trim() || null
          : null,
      match_matter_type: form.match_matter_type.trim() || null,
      match_practice_area: form.match_practice_area.trim() || null,
      template_id: form.template_id,
    };
    const saved = await run(
      () =>
        editing
          ? updateWorkflowAutomation(editing, payload)
          : createWorkflowAutomation(payload),
      editing
        ? "Rule saved as a draft. It needs approval before it runs again."
        : "Draft rule created. It runs only after approval.",
    );
    if (saved) {
      setForm(blankForm());
      setEditing(null);
    }
  };

  const showEvents = async (rule) => {
    try {
      const data = await getWorkflowAutomationEvents(rule.id);
      setEvents((current) => ({ ...current, [rule.id]: asItems(data) }));
    } catch (e) {
      setMessage({
        type: "error",
        text: errText(e, "Unable to load rule activity."),
      });
    }
  };

  return (
    <section aria-labelledby="workflow-automations-title" className="space-y-3">
      <h2 id="workflow-automations-title" className="font-semibold">
        Automation rules
      </h2>
      <p className="text-sm">
        An active rule prepares a workflow preview when a matter event matches
        it. It never applies the workflow: a reviewer still approves and applies
        the planned run.
      </p>
      {message && (
        <p
          role="status"
          className={
            message.type === "error" ? "text-red-700" : "text-green-700"
          }
        >
          {message.text}
        </p>
      )}
      {canManage && (
        <form onSubmit={submit} className="grid gap-2 border rounded p-3">
          <label htmlFor="automation-name" className="grid gap-1 text-sm">
            <span>Rule name</span>
            <input
              id="automation-name"
              value={form.name}
              required
              maxLength={120}
              onChange={(e) =>
                setForm((f) => ({ ...f, name: e.target.value }))
              }
            />
          </label>
          <label htmlFor="automation-trigger" className="grid gap-1 text-sm">
            <span>When</span>
            <select
              id="automation-trigger"
              value={form.trigger_event}
              onChange={(e) =>
                setForm((f) => ({ ...f, trigger_event: e.target.value }))
              }
            >
              {TRIGGERS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {form.trigger_event === "matter_stage_changed" && (
            <label htmlFor="automation-stage" className="grid gap-1 text-sm">
              <span>Stage</span>
              <input
                id="automation-stage"
                value={form.trigger_stage}
                required
                maxLength={200}
                onChange={(e) =>
                  setForm((f) => ({ ...f, trigger_stage: e.target.value }))
                }
              />
            </label>
          )}
          <label htmlFor="automation-matter-type" className="grid gap-1 text-sm">
            <span>Only for matter type (optional)</span>
            <input
              id="automation-matter-type"
              value={form.match_matter_type}
              maxLength={100}
              onChange={(e) =>
                setForm((f) => ({ ...f, match_matter_type: e.target.value }))
              }
            />
          </label>
          <label
            htmlFor="automation-practice-area"
            className="grid gap-1 text-sm"
          >
            <span>Only for practice area (optional)</span>
            <input
              id="automation-practice-area"
              value={form.match_practice_area}
              maxLength={200}
              onChange={(e) =>
                setForm((f) => ({ ...f, match_practice_area: e.target.value }))
              }
            />
          </label>
          <label htmlFor="automation-template" className="grid gap-1 text-sm">
            <span>Plan this approved template</span>
            <select
              id="automation-template"
              value={form.template_id}
              required
              onChange={(e) =>
                setForm((f) => ({ ...f, template_id: e.target.value }))
              }
            >
              <option value="">Choose a template…</option>
              {approvedTemplates.map(([id, name]) => (
                <option key={id} value={id}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <div className="flex gap-2">
            <button type="submit" disabled={working}>
              {editing ? "Save draft" : "Create draft rule"}
            </button>
            {editing && (
              <button
                type="button"
                onClick={() => {
                  setForm(blankForm());
                  setEditing(null);
                }}
              >
                Cancel edit
              </button>
            )}
          </div>
        </form>
      )}
      {rules.length === 0 ? (
        <p>No automation rules yet.</p>
      ) : (
        rules.map((rule) => (
          <article key={rule.id} className="border-t py-2 text-sm space-y-1">
            <p>
              <strong>{rule.name}</strong> · {rule.status} ·{" "}
              {rule.trigger_event === "matter_stage_changed"
                ? `enters ${rule.trigger_stage}`
                : "matter opened"}{" "}
              → {rule.template_name || rule.template_id}
            </p>
            {(rule.match_matter_type || rule.match_practice_area) && (
              <p>
                Only when{" "}
                {[rule.match_matter_type, rule.match_practice_area]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            )}
            <p className="font-mono text-xs break-all">
              Definition fingerprint: {rule.definition_sha256}
            </p>
            <div className="flex gap-2 flex-wrap">
              {canApprove && rule.status === "draft" && (
                <button
                  type="button"
                  disabled={working}
                  onClick={() =>
                    run(
                      () =>
                        activateWorkflowAutomation(rule.id, {
                          definition_sha256: rule.definition_sha256,
                          confirm_activate: true,
                        }),
                      "Rule approved. It will plan reviewable runs.",
                    )
                  }
                >
                  Approve and activate
                </button>
              )}
              {canManage && rule.status !== "archived" && (
                <>
                  <button
                    type="button"
                    onClick={() => {
                      setEditing(rule.id);
                      setForm({
                        name: rule.name,
                        trigger_event: rule.trigger_event,
                        trigger_stage: rule.trigger_stage || "",
                        match_matter_type: rule.match_matter_type || "",
                        match_practice_area: rule.match_practice_area || "",
                        template_id: rule.template_id,
                      });
                    }}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    disabled={working}
                    onClick={() =>
                      run(
                        () => archiveWorkflowAutomation(rule.id),
                        "Rule archived.",
                      )
                    }
                  >
                    Archive
                  </button>
                </>
              )}
              <button type="button" onClick={() => showEvents(rule)}>
                Show activity
              </button>
            </div>
            {events[rule.id] && (
              <ul aria-label={`Activity for ${rule.name}`}>
                {events[rule.id].length === 0 ? (
                  <li>This rule has not matched a matter yet.</li>
                ) : (
                  events[rule.id].map((event) => (
                    <li key={event.id}>
                      {event.outcome === "planned"
                        ? "Planned a run"
                        : `Blocked: ${event.detail?.message || event.detail?.failure_code || "see evidence"}`}{" "}
                      · {event.created_at}
                    </li>
                  ))
                )}
              </ul>
            )}
          </article>
        ))
      )}
    </section>
  );
}
