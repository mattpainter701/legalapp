export function automationStatus(event) {
  switch (event.outcome) {
    case "planned":
      return "Planned a run for review — open the matter to review and approve";
    case "pending":
      return "Pending — waiting for planning";
    case "running":
      return "Planning — no work applied";
    case "retrying":
      return `Retry scheduled — attempt ${event.attempts} of ${event.max_attempts}`;
    case "failed":
      return "Planning failed — open the matter and create a manual preview";
    default:
      return `Blocked: ${event.detail?.message || event.detail?.failure_code || "context unavailable"}. Review current facts and create a new manual preview.`;
  }
}
