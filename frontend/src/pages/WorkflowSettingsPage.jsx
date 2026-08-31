import { useCallback, useEffect, useState } from "react";
import {
  listWorkflowFields,
  createWorkflowField,
  updateWorkflowField,
  listWorkflowTemplates,
  createWorkflowTemplate,
  createWorkflowTemplateVersion,
  approveWorkflowTemplateVersion,
  archiveWorkflowTemplate,
} from "../api";

const TYPES = [
  "text",
  "long_text",
  "number",
  "date",
  "boolean",
  "single_select",
  "multi_select",
  "contact",
];
const TASKS = [
  "deadline",
  "hearing",
  "filing",
  "deposition",
  "call",
  "follow_up",
  "intake",
  "review",
  "general",
];
const ROLES = [
  "matter_owner",
  "attorney_of_record",
  "template_applier",
  "unassigned",
];
const PRIORITIES = ["low", "medium", "high", "urgent"];
const PRESETS = [
  ["New matter opening", "opening", ["conflicts", "engagement", "intake"]],
  [
    "Litigation discovery",
    "discovery",
    ["initial_disclosures", "document_requests", "depositions"],
  ],
  [
    "Transaction closing",
    "closing",
    ["due_diligence", "drafting", "signatures"],
  ],
  [
    "Probate administration",
    "administration",
    ["inventory", "notice", "distribution"],
  ],
  [
    "Matter closeout",
    "closeout",
    ["final_invoice", "archive_review", "client_notice"],
  ],
];
let checklistKey = 0;
const blankRow = (stageKey = "stage_1") => ({
  item_key: `task_${Date.now()}_${++checklistKey}`,
  stage_key: stageKey,
  title: "",
  task_type: "general",
  priority: "medium",
  due_offset_days: 0,
  assignee_role: "unassigned",
});
const asItems = (value) =>
  Array.isArray(value)
    ? value
    : value?.items || value?.fields || value?.templates || [];
const errText = (e, fallback) => {
  const detail = e?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  return e?.message || fallback;
};

export default function WorkflowSettingsPage({ user, embedded = false }) {
  const canManage = (user?.capabilities || []).includes("manage_workflows");
  const canApprove = (user?.capabilities || []).includes("approve_legal_work");
  const canReview = canManage || canApprove;
  const Container = embedded ? "section" : "main";
  const Heading = embedded ? "h3" : "h1";
  const [fields, setFields] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [versionSource, setVersionSource] = useState(null);
  const [message, setMessage] = useState(null);
  const [loading, setLoading] = useState(canReview);
  const [field, setField] = useState({
    entity_type: "matter",
    field_key: "",
    label: "",
    field_type: "text",
    options: "",
    required: false,
    sensitive: false,
  });
  const [form, setForm] = useState({
    name: "",
    description: "",
    initial_stage_key: "stage_1",
    stages: [{ stage_key: "stage_1", label: "Initial" }],
    checklist: [blankRow()],
    required_field_definition_ids: [],
  });
  const load = useCallback(async () => {
    try {
      const [f, t] = await Promise.all([
        canManage
          ? listWorkflowFields({ include_inactive: true })
          : Promise.resolve({ items: [] }),
        listWorkflowTemplates({}),
      ]);
      setFields(asItems(f));
      setTemplates(asItems(t));
    } catch (e) {
      setMessage({
        type: "error",
        text: errText(e, "Unable to load workflow settings."),
      });
    } finally {
      setLoading(false);
    }
  }, [canManage]);
  useEffect(() => {
    if (canReview) load();
  }, [canReview, load]);
  if (!canReview)
    return (
      <Container className="p-6">
        <Heading>Workflow settings</Heading>
        <p role="alert">
          You need manage_workflows or approve_legal_work to review workflow
          definitions.
        </p>
      </Container>
    );
  const setFormValue = (key, value) => setForm((v) => ({ ...v, [key]: value }));
  const resetTemplateForm = () => {
    setVersionSource(null);
    setForm({
      name: "",
      description: "",
      initial_stage_key: "stage_1",
      stages: [{ stage_key: "stage_1", label: "Initial" }],
      checklist: [blankRow()],
      required_field_definition_ids: [],
    });
  };
  const renameStage = (index, stageKey) =>
    setForm((current) => {
      const previousKey = current.stages[index].stage_key;
      return {
        ...current,
        initial_stage_key:
          current.initial_stage_key === previousKey
            ? stageKey
            : current.initial_stage_key,
        stages: current.stages.map((stage, stageIndex) =>
          stageIndex === index ? { ...stage, stage_key: stageKey } : stage,
        ),
        checklist: current.checklist.map((item) =>
          item.stage_key === previousKey
            ? { ...item, stage_key: stageKey }
            : item,
        ),
      };
    });
  const saveField = async (e) => {
    e.preventDefault();
    setMessage(null);
    try {
      await createWorkflowField({
        ...field,
        options: field.options
          .split(",")
          .map((x) => x.trim())
          .filter(Boolean),
      });
      setMessage({ type: "success", text: "Field definition created." });
      setField({ ...field, field_key: "", label: "", options: "" });
      await load();
    } catch (x) {
      setMessage({
        type: "error",
        text: errText(x, "Unable to create field definition."),
      });
    }
  };
  const saveTemplate = async (e) => {
    e.preventDefault();
    setMessage(null);
    try {
      const definition = {
        initial_stage_key: form.initial_stage_key,
        stages: form.stages,
        checklist: form.checklist.map((x) => ({
          ...x,
          due_offset_days: Number(x.due_offset_days),
        })),
        required_field_definition_ids: form.required_field_definition_ids,
      };
      if (versionSource) {
        await createWorkflowTemplateVersion(versionSource.template_id, {
          ...definition,
          expected_latest_version: versionSource.version,
        });
      } else {
        await createWorkflowTemplate({
          ...definition,
          name: form.name,
          description: form.description,
        });
      }
      setMessage({
        type: "success",
        text: versionSource
          ? "New template version created as a draft."
          : "Template draft created. It was not silently approved.",
      });
      resetTemplateForm();
      await load();
    } catch (x) {
      const stale = x?.response?.status === 409 && versionSource;
      setMessage({
        type: "error",
        text: stale
          ? "Workflow template changed; settings were reloaded. Start the new version again."
          : errText(x, "Unable to create template."),
      });
      if (stale) {
        resetTemplateForm();
        await load();
      }
    }
  };
  const startVersion = (template) => {
    setVersionSource({
      template_id: template.template_id,
      template_name: template.template_name,
      version: template.version,
    });
    setForm({
      name: template.template_name,
      description: template.template_description || "",
      initial_stage_key: template.initial_stage_key,
      stages: template.stages.map((stage) => ({ ...stage })),
      checklist: template.checklist.map((item) => ({ ...item })),
      required_field_definition_ids: (template.required_fields || []).map(
        (item) => item.id,
      ),
    });
    setMessage(null);
  };
  const runLifecycle = async (operation, successText) => {
    setMessage(null);
    try {
      await operation();
      setMessage({ type: "success", text: successText });
      await load();
    } catch (error) {
      setMessage({
        type: "error",
        text: errText(error, "Unable to update workflow settings."),
      });
      if (error?.response?.status === 409) await load();
    }
  };
  const preset = (_, key, stages) =>
    setForm((v) => ({
      ...v,
      name: _,
      initial_stage_key: stages[0],
      stages: stages.map((stage_key) => ({
        stage_key,
        label: stage_key.replaceAll("_", " "),
      })),
      checklist: stages.map((stage_key, i) => ({
        ...blankRow(),
        item_key: `${key}_${i + 1}`,
        stage_key,
        title: stages[i].replaceAll("_", " "),
        due_offset_days: i * 7,
      })),
    }));
  return (
    <Container className="p-6 max-w-5xl space-y-6">
      <Heading className="text-2xl font-semibold">Workflow settings</Heading>
      <p className="text-sm">
        Manage definitions and bounded workflow templates.{" "}
        <strong>approve_legal_work</strong> is intentionally separate from
        manage_workflows.
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
      {loading ? (
        <p>Loading…</p>
      ) : (
        <>
          {canManage && (
            <>
          <section className="border rounded p-4">
            <h2 className="font-semibold">Matter and contact fields</h2>
            <form onSubmit={saveField} className="grid gap-2 mt-3">
              <select
                aria-label="Entity type"
                value={field.entity_type}
                onChange={(e) =>
                  setField({ ...field, entity_type: e.target.value })
                }
              >
                <option value="matter">Matter</option>
                <option value="contact">Contact</option>
              </select>
              <input
                required
                aria-label="Field key"
                placeholder="stable_field_key"
                value={field.field_key}
                onChange={(e) =>
                  setField({ ...field, field_key: e.target.value })
                }
              />
              <input
                required
                aria-label="Field label"
                placeholder="Label"
                value={field.label}
                onChange={(e) => setField({ ...field, label: e.target.value })}
              />
              <select
                aria-label="Field type"
                value={field.field_type}
                onChange={(e) =>
                  setField({
                    ...field,
                    field_type: e.target.value,
                    options: "",
                  })
                }
              >
                {TYPES.map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
              {["single_select", "multi_select"].includes(field.field_type) && (
                <input
                  aria-label="Options"
                  placeholder="Options, comma separated"
                  value={field.options}
                  onChange={(e) =>
                    setField({ ...field, options: e.target.value })
                  }
                />
              )}
              <label>
                <input
                  type="checkbox"
                  checked={field.required}
                  onChange={(e) =>
                    setField({ ...field, required: e.target.checked })
                  }
                />{" "}
                Required
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={field.sensitive}
                  onChange={(e) =>
                    setField({ ...field, sensitive: e.target.checked })
                  }
                />{" "}
                Sensitive
              </label>
              <button type="submit">Create field definition</button>
            </form>
            <ul>
              {fields.map((item) => (
                <li key={item.id}>
                  {item.label || item.field_key} ·{" "}
                  {item.active === false ? "inactive" : "active"}{" "}
                  <button
                    type="button"
                    onClick={() =>
                      runLifecycle(
                        () =>
                          updateWorkflowField(item.id, {
                            active: item.active === false,
                            expected_schema_version: item.schema_version || 1,
                          }),
                        "Field status updated.",
                      )
                    }
                  >
                    Toggle active
                  </button>
                </li>
              ))}
            </ul>
          </section>
          <section className="border rounded p-4">
            <h2 className="font-semibold">
              {versionSource
                ? `New version of ${versionSource.template_name}`
                : "New workflow template draft"}
            </h2>
            <div className="flex flex-wrap gap-2 my-3">
              {PRESETS.map(([name, key, stages]) => (
                <button
                  type="button"
                  key={key}
                  onClick={() => preset(name, key, stages)}
                >
                  Use {name} preset
                </button>
              ))}
            </div>
            <form onSubmit={saveTemplate} className="grid gap-3">
              {versionSource ? (
                <p>
                  Editing from v{versionSource.version}; saving creates draft v
                  {versionSource.version + 1}.
                </p>
              ) : (
                <>
                  <input
                    required
                    aria-label="Template name"
                    placeholder="Template name"
                    value={form.name}
                    onChange={(e) => setFormValue("name", e.target.value)}
                  />
                  <textarea
                    aria-label="Description"
                    placeholder="Description"
                    value={form.description}
                    onChange={(e) => setFormValue("description", e.target.value)}
                  />
                </>
              )}
              <label>
                Initial stage
                <select
                  value={form.initial_stage_key}
                  onChange={(e) =>
                    setFormValue("initial_stage_key", e.target.value)
                  }
                >
                  {form.stages.map((s) => (
                    <option key={s.stage_key}>{s.stage_key}</option>
                  ))}
                </select>
              </label>
              <div>
                <h3>Ordered stages</h3>
                {form.stages.map((s, i) => (
                  <div key={s.stage_key} className="flex gap-2">
                    <input
                      aria-label={`Stage ${i + 1} key`}
                      value={s.stage_key}
                      onChange={(e) => renameStage(i, e.target.value)}
                    />
                    <input
                      aria-label={`Stage ${i + 1} label`}
                      value={s.label}
                      onChange={(e) =>
                        setFormValue(
                          "stages",
                          form.stages.map((x, j) =>
                            j === i ? { ...x, label: e.target.value } : x,
                          ),
                        )
                      }
                    />
                  </div>
                ))}
                <button
                  type="button"
                  onClick={() =>
                    setFormValue("stages", [
                      ...form.stages,
                      {
                        stage_key: `stage_${form.stages.length + 1}`,
                        label: "",
                      },
                    ])
                  }
                >
                  Add stage
                </button>
              </div>
              <fieldset>
                <legend>Required matter fields</legend>
                {fields.filter(
                  (item) => item.entity_type === "matter" && item.active !== false,
                ).length === 0 ? (
                  <p className="text-sm">No active matter fields configured.</p>
                ) : (
                  fields
                    .filter(
                      (item) =>
                        item.entity_type === "matter" && item.active !== false,
                    )
                    .map((item) => (
                      <label key={item.id} className="block">
                        <input
                          type="checkbox"
                          checked={form.required_field_definition_ids.includes(
                            item.id,
                          )}
                          onChange={(event) =>
                            setFormValue(
                              "required_field_definition_ids",
                              event.target.checked
                                ? [
                                    ...form.required_field_definition_ids,
                                    item.id,
                                  ]
                                : form.required_field_definition_ids.filter(
                                    (fieldId) => fieldId !== item.id,
                                  ),
                            )
                          }
                        />{" "}
                        {item.label || item.field_key}
                      </label>
                    ))
                )}
              </fieldset>
              <div>
                <h3>Checklist</h3>
                {form.checklist.map((row, i) => (
                  <div key={row.item_key} className="grid grid-cols-2 gap-2">
                    <input
                      required
                      aria-label={`Checklist title ${i + 1}`}
                      placeholder="Task title"
                      value={row.title}
                      onChange={(e) =>
                        setFormValue(
                          "checklist",
                          form.checklist.map((x, j) =>
                            j === i ? { ...x, title: e.target.value } : x,
                          ),
                        )
                      }
                    />
                    <textarea
                      aria-label={`Checklist description ${i + 1}`}
                      placeholder="Optional task instructions"
                      value={row.description || ""}
                      onChange={(e) =>
                        setFormValue(
                          "checklist",
                          form.checklist.map((x, j) =>
                            j === i ? { ...x, description: e.target.value } : x,
                          ),
                        )
                      }
                    />
                    <select
                      aria-label={`Checklist stage ${i + 1}`}
                      value={row.stage_key}
                      onChange={(e) =>
                        setFormValue(
                          "checklist",
                          form.checklist.map((x, j) =>
                            j === i ? { ...x, stage_key: e.target.value } : x,
                          ),
                        )
                      }
                    >
                      {form.stages.map((stage) => (
                        <option key={stage.stage_key} value={stage.stage_key}>
                          {stage.label || stage.stage_key}
                        </option>
                      ))}
                    </select>
                    <select
                      aria-label={`Checklist task type ${i + 1}`}
                      value={row.task_type}
                      onChange={(e) =>
                        setFormValue(
                          "checklist",
                          form.checklist.map((x, j) =>
                            j === i ? { ...x, task_type: e.target.value } : x,
                          ),
                        )
                      }
                    >
                      {TASKS.map((t) => (
                        <option key={t}>{t}</option>
                      ))}
                    </select>
                    <input
                      aria-label={`Checklist offset ${i + 1}`}
                      type="number"
                      min="0"
                      max="3650"
                      value={row.due_offset_days}
                      onChange={(e) =>
                        setFormValue(
                          "checklist",
                          form.checklist.map((x, j) =>
                            j === i
                              ? { ...x, due_offset_days: e.target.value }
                              : x,
                          ),
                        )
                      }
                    />
                    <select
                      aria-label={`Checklist priority ${i + 1}`}
                      value={row.priority}
                      onChange={(e) =>
                        setFormValue(
                          "checklist",
                          form.checklist.map((x, j) =>
                            j === i ? { ...x, priority: e.target.value } : x,
                          ),
                        )
                      }
                    >
                      {PRIORITIES.map((priority) => (
                        <option key={priority}>{priority}</option>
                      ))}
                    </select>
                    <select
                      aria-label={`Checklist assignee ${i + 1}`}
                      value={row.assignee_role}
                      onChange={(e) =>
                        setFormValue(
                          "checklist",
                          form.checklist.map((x, j) =>
                            j === i
                              ? { ...x, assignee_role: e.target.value }
                              : x,
                          ),
                        )
                      }
                    >
                      {ROLES.map((r) => (
                        <option key={r}>{r}</option>
                      ))}
                    </select>
                  </div>
                ))}
                <button
                  type="button"
                  onClick={() =>
                    setFormValue("checklist", [
                      ...form.checklist,
                      blankRow(form.stages[0]?.stage_key),
                    ])
                  }
                >
                  Add checklist item
                </button>
              </div>
              <button type="submit">
                {versionSource ? "Create next version draft" : "Create template draft"}
              </button>
              {versionSource && (
                <button type="button" onClick={resetTemplateForm}>
                  Cancel new version
                </button>
              )}
            </form>
          </section>
            </>
          )}
          <section>
            <h2 className="font-semibold">Templates and versions</h2>
            {templates.map((template) => (
              <article
                key={template.version_id || template.id}
                className="border-t py-2"
              >
                <strong>{template.template_name || template.name}</strong>
                <span className="ml-3">
                  v{template.version} · {template.status}{" "}
                  {canApprove && template.status === "draft" && (
                    <button
                      type="button"
                      onClick={() =>
                        runLifecycle(
                          () =>
                            approveWorkflowTemplateVersion(
                              template.template_id,
                              template.version_id,
                            ),
                          "Template version approved.",
                        )
                      }
                    >
                      Approve
                    </button>
                  )}
                </span>
                <div className="mt-2 text-sm space-y-2">
                  {template.template_description && (
                    <p>{template.template_description}</p>
                  )}
                  <p>
                    Initial stage: {" "}
                    {template.stages?.find(
                      (stage) =>
                        stage.stage_key === template.initial_stage_key,
                    )?.label || template.initial_stage_key}
                  </p>
                  <div>
                    <strong>Stages</strong>
                    <ol>
                      {(template.stages || []).map((stage) => (
                        <li key={stage.stage_key}>
                          {stage.label} ({stage.stage_key})
                        </li>
                      ))}
                    </ol>
                  </div>
                  <div>
                    <strong>Checklist</strong>
                    <ul>
                      {(template.checklist || []).map((item) => (
                        <li key={item.item_key}>
                          {item.title} · {item.stage_key} · due +
                          {item.due_offset_days} days · {item.assignee_role}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <strong>Required fields</strong>
                    {(template.required_fields || []).length ? (
                      <ul>
                        {template.required_fields.map((field) => (
                          <li key={field.id || field.field_definition_id}>
                            {field.label || field.field_key || field.id}
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p>None</p>
                    )}
                  </div>
                </div>
                {canManage && template.version ===
                  Math.max(
                    ...templates
                      .filter(
                        (item) => item.template_id === template.template_id,
                      )
                      .map((item) => item.version),
                  ) && template.template_active !== false && (
                  <button type="button" onClick={() => startVersion(template)}>
                    Create next version
                  </button>
                )}
                {canManage && (
                  <button
                    type="button"
                    onClick={() =>
                      runLifecycle(
                        () => archiveWorkflowTemplate(template.template_id),
                        "Template archived.",
                      )
                    }
                  >
                    Archive template
                  </button>
                )}
              </article>
            ))}
          </section>
        </>
      )}
    </Container>
  );
}
