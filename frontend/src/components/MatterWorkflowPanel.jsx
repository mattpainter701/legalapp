import { useCallback, useEffect, useState } from "react";
import {
  getMatterCustomFields,
  updateMatterCustomFields,
  getMatterWorkflowTemplates,
  previewMatterWorkflow,
  applyMatterWorkflow,
  getMatterWorkflowRuns,
  rollbackMatterWorkflow,
} from "../api";
import WorkflowSettingsPage from "../pages/WorkflowSettingsPage";

const items = (value) =>
  Array.isArray(value)
    ? value
    : value?.items || value?.fields || value?.templates || value?.runs || [];
const errorText = (error, fallback) => {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  return error?.message || fallback;
};
const operationId = () =>
  globalThis.crypto?.randomUUID?.() ||
  `workflow-${Date.now()}-${Math.random().toString(36).slice(2)}`;

function FieldInput({ field, value, onChange }) {
  const id = `matter-field-${field.id || field.field_definition_id || field.field_key}`;
  const type = field.field_type || field.type;
  const options = field.options || [];
  if (type === "contact")
    return (
      <p className="text-sm" id={id}>
        {field.label}: contact-linked values require the tenant-safe CRM/API
        surface and cannot be edited here.
      </p>
    );
  if (type === "boolean")
    return (
      <label htmlFor={id} className="flex gap-2 items-center text-sm">
        <input
          id={id}
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
        {field.label}
      </label>
    );
  if (type === "long_text")
    return (
      <label htmlFor={id} className="grid gap-1 text-sm">
        <span>
          {field.label}
          {field.required && " *"}
        </span>
        <textarea
          id={id}
          rows="3"
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
    );
  if (type === "single_select" || type === "multi_select")
    return (
      <label htmlFor={id} className="grid gap-1 text-sm">
        <span>
          {field.label}
          {field.required && " *"}
        </span>
        <select
          id={id}
          multiple={type === "multi_select"}
          value={value ?? (type === "multi_select" ? [] : "")}
            onChange={(e) =>
              onChange(
                type === "multi_select"
                  ? [...e.target.selectedOptions]
                      .map((o) => o.value)
                      .filter(Boolean)
                  : e.target.value || null,
              )
          }
        >
          <option value="">Select…</option>
          {options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </label>
    );
  return (
    <label htmlFor={id} className="grid gap-1 text-sm">
      <span>
        {field.label}
        {field.required && " *"}
      </span>
      <input
        id={id}
        type={type === "number" ? "number" : type === "date" ? "date" : "text"}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

export default function MatterWorkflowPanel({
  matterId,
  onWorkflowApplied,
  user,
}) {
  const canApproveLegal = (user?.capabilities || []).includes(
    "approve_legal_work",
  );
  const canManageMatters = (user?.capabilities || []).includes("manage_matters");
  const canApprove = canApproveLegal && canManageMatters;
  const canManageWorkflows = (user?.capabilities || []).includes(
    "manage_workflows",
  );
  const [fields, setFields] = useState([]);
  const [values, setValues] = useState({});
  const [templates, setTemplates] = useState([]);
  const [runs, setRuns] = useState([]);
  const [dirtyFields, setDirtyFields] = useState({});
  const [selected, setSelected] = useState("");
  const [preview, setPreview] = useState(null);
  const [previewKey, setPreviewKey] = useState("");
  const [previewStale, setPreviewStale] = useState(false);
  const [rollbackKeys, setRollbackKeys] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState(null);
  const [rollbackReason, setRollbackReason] = useState({});
  const load = useCallback(async () => {
    setLoading(true);
    setMessage(null);
    if (!canManageMatters) {
      setFields([]);
      setValues({});
      setTemplates([]);
      setRuns([]);
      setLoading(false);
      return;
    }
    try {
      const [fieldData, templateData, runData] = await Promise.all([
        getMatterCustomFields(matterId),
        getMatterWorkflowTemplates(matterId),
        getMatterWorkflowRuns(matterId),
      ]);
      const fs = items(fieldData);
      setFields(fs);
      setValues(
        Object.fromEntries(
          fs.map((f) => [
            f.id || f.field_definition_id || f.field_key,
            f.value ?? fieldData?.values?.[f.id],
          ]),
        ),
      );
      setDirtyFields({});
      setTemplates(
        items(templateData).filter(
          (t) => t.status === "approved" || t.approved_at || t.is_approved,
        ),
      );
      setRuns(items(runData));
    } catch (e) {
      setMessage({
        type: "error",
        text: errorText(e, "Unable to load workflow data."),
      });
    } finally {
      setLoading(false);
    }
  }, [canManageMatters, matterId]);
  useEffect(() => {
    load();
  }, [load]);
  const saveFields = async () => {
    const changed = fields.filter(
      (field) => dirtyFields[field.id || field.field_definition_id],
    );
    if (!changed.length) return;
    setSaving(true);
    setMessage(null);
    try {
      await updateMatterCustomFields(matterId, {
        values: changed.map((f) => ({
          field_definition_id: f.id || f.field_definition_id,
          value: values[f.id || f.field_definition_id || f.field_key] ?? null,
        })),
      });
      await load();
      setMessage({ type: "success", text: "Matter data saved." });
    } catch (e) {
      setMessage({
        type: "error",
        text: errorText(e, "Unable to save matter data."),
      });
    } finally {
      setSaving(false);
    }
  };
  const makePreview = async () => {
    if (!selected) return;
    setWorking(true);
    setMessage(null);
    const key = previewKey || operationId();
    setPreviewKey(key);
    try {
      setPreview(await previewMatterWorkflow(matterId, selected, key));
      setPreviewStale(false);
    } catch (e) {
      setPreviewStale(e?.response?.status === 409);
      setMessage({
        type: "error",
        text:
          e?.response?.status === 409
            ? "This preview is stale. Create a new preview before applying."
            : errorText(e, "Unable to preview workflow."),
      });
    } finally {
      setWorking(false);
    }
  };
  const apply = async () => {
    if (!preview || !canApprove) return;
    setWorking(true);
    try {
      await applyMatterWorkflow(matterId, preview.run_id || preview.id, {
        preview_sha256: preview.preview_sha256 || preview.sha256,
        confirm_apply: true,
      });
      setPreview(null);
      setPreviewKey("");
      setPreviewStale(false);
      await load();
      setMessage({ type: "success", text: "Workflow applied." });
      onWorkflowApplied?.();
    } catch (e) {
      setPreviewStale(e?.response?.status === 409);
      setMessage({
        type: "error",
        text:
          e?.response?.status === 409
            ? "This preview is stale. Create a new preview."
            : errorText(e, "Unable to apply workflow."),
      });
    } finally {
      setWorking(false);
    }
  };
  const rollback = async (run) => {
    const id = run.id || run.run_id;
    if (!rollbackReason[id]?.trim())
      return setMessage({
        type: "error",
        text: "A rollback reason is required.",
      });
    setWorking(true);
    const key = rollbackKeys[id] || operationId();
    setRollbackKeys((k) => ({ ...k, [id]: key }));
    try {
      await rollbackMatterWorkflow(
        matterId,
        id,
        { reason: rollbackReason[id] },
        key,
      );
      await load();
      setMessage({ type: "success", text: "Compensating rollback recorded." });
    } catch (e) {
      if (e?.response?.status === 409) await load();
      setMessage({
        type: "error",
        text: errorText(e, "Unable to roll back workflow."),
      });
    } finally {
      setWorking(false);
    }
  };
  if (loading)
    return (
      <section aria-label="Matter workflow" className="p-4">
        Loading workflow data…
      </section>
    );
  return (
    <section
      aria-labelledby="matter-workflow-title"
      className="space-y-5 p-4 border rounded-lg"
    >
      <h2 id="matter-workflow-title" className="text-lg font-semibold">
        Matter workflow
      </h2>
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
      <div className="grid gap-3">
        {fields.map((field) => {
          const fieldId = field.id || field.field_definition_id;
          return (
            <div key={fieldId || field.field_key}>
              <FieldInput
                field={field}
                value={values[fieldId || field.field_key]}
                onChange={(value) => {
                  setValues((v) => ({
                    ...v,
                    [fieldId || field.field_key]: value,
                  }));
                  setDirtyFields((current) => ({
                    ...current,
                    [fieldId]: true,
                  }));
                }}
              />
              {field.sensitive && field.has_value && !dirtyFields[fieldId] && (
                <p className="text-xs text-brand-muted">
                  Sensitive value stored. Enter a new value only to replace it.
                </p>
              )}
            </div>
          );
        })}
      </div>
      {fields.length > 0 && (
        <button
          type="button"
          onClick={saveFields}
          disabled={saving || !Object.keys(dirtyFields).length}
        >
          {saving ? "Saving…" : "Save matter data"}
        </button>
      )}
      <div className="grid gap-2">
        <label htmlFor="workflow-template">Approved workflow template</label>
        <select
          id="workflow-template"
          value={selected}
          onChange={(e) => {
            setSelected(e.target.value);
            setPreview(null);
            setPreviewKey("");
            setPreviewStale(false);
          }}
        >
          <option value="">Choose a template…</option>
          {templates.map((t) => (
            <option key={t.version_id || t.id} value={t.version_id || t.id}>
              {t.template_name || t.name} {t.version ? `v${t.version}` : ""}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={makePreview}
          disabled={!selected || working || previewStale}
        >
          Preview workflow
        </button>
        {previewStale && (
          <button
            type="button"
            onClick={() => {
              setPreview(null);
              setPreviewKey("");
              setPreviewStale(false);
              setMessage(null);
            }}
          >
            New preview
          </button>
        )}
      </div>
      {preview && (
        <div
          aria-label="Workflow preview"
          className="border rounded p-3 space-y-2"
        >
          <h3 className="font-semibold">
            Preview (no matter or task changes made)
          </h3>
          <p className="font-mono text-xs break-all">
            Preview fingerprint: {preview.preview_sha256 || "—"}
          </p>
          <p>
            Initial stage:{" "}
            {preview.initial_stage?.label ||
              preview.initial_stage?.stage_key ||
              "—"}
          </p>
          {preview.missing_required_fields?.length > 0 && (
            <ul className="text-amber-700" aria-label="Missing required fields">
              {preview.missing_required_fields.map((field) => (
                <li key={field.field_definition_id || field.field_key}>
                  {field.label || field.field_key || field.id}
                </li>
              ))}
            </ul>
          )}
          {preview.missing_assignees?.length > 0 && (
            <ul className="text-amber-700" aria-label="Missing assignees">
              {preview.missing_assignees.map((item) => (
                <li key={item.item_key}>
                  {item.title || item.item_key}: {item.assignee_role} is not set
                </li>
              ))}
            </ul>
          )}
          <ul>
            {(preview.tasks || preview.checklist || []).map((task) => (
              <li key={task.item_key || task.id}>
                {task.title || task.name} · due{" "}
                {task.due_date ||
                  task.due_at ||
                  `${task.due_offset_days ?? 0} days`}{" "}
                · {task.assignee_role || "unassigned"}
              </li>
            ))}
          </ul>
          {canApprove ? (
            <button
              type="button"
              onClick={apply}
              disabled={
                working ||
                !preview.can_apply ||
                preview.missing_required_fields?.length > 0 ||
                preview.missing_assignees?.length > 0
              }
            >
              Approve and apply
            </button>
          ) : (
            <p>Approval capability required to apply legal work.</p>
          )}
        </div>
      )}
      <div>
        <h3 className="font-semibold">Run history</h3>
        {runs.length === 0 ? (
          <p>No workflow runs yet.</p>
        ) : (
          runs.map((run) => {
            const id = run.id || run.run_id;
            const latestEvidence = run.events?.at(-1)?.evidence_sha256;
            return (
              <article key={id} className="border-t py-2">
                <p>
                  {run.status} · {run.events?.length || 0} events ·{" "}
                  {run.steps?.length || 0} steps · evidence{" "}
                  {latestEvidence ? latestEvidence.slice(0, 12) : "recorded"}
                </p>
                {run.failure_detail && (
                  <p className="text-amber-700">{run.failure_detail}</p>
                )}
                {run.status === "applied" && canApprove && (
                  <div className="flex gap-2">
                    <input
                      aria-label={`Rollback reason for ${id}`}
                      value={rollbackReason[id] || ""}
                      onChange={(e) =>
                        setRollbackReason((r) => ({
                          ...r,
                          [id]: e.target.value,
                        }))
                      }
                      placeholder="Reason for compensating rollback"
                    />
                    <button
                      type="button"
                      onClick={() => rollback(run)}
                      disabled={working}
                    >
                      Rollback
                    </button>
                  </div>
                )}
              </article>
            );
          })
        )}
      </div>
      {(canManageWorkflows || canApproveLegal) && (
        <details className="border-t pt-4">
          <summary className="cursor-pointer font-semibold">
            Firm workflow configuration
          </summary>
          <WorkflowSettingsPage user={user} embedded />
        </details>
      )}
    </section>
  );
}
