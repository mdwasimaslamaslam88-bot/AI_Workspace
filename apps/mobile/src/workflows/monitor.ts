import type { Workflow, WorkflowStatus } from "@work-station/shared";

const TERMINAL_WORKFLOW_STATUSES = new Set<WorkflowStatus>([
  "completed",
  "failed",
  "cancelled",
  "timed_out",
]);

export const DEFAULT_WORKFLOW_POLL_ATTEMPTS = 130;
export const DEFAULT_WORKFLOW_POLL_INTERVAL_MS = 500;

export class WorkflowPollingTimeoutError extends Error {
  constructor() {
    super("Workflow status could not be confirmed within its deadline.");
    this.name = "WorkflowPollingTimeoutError";
  }
}

export function isWorkflowTerminal(status: WorkflowStatus): boolean {
  return TERMINAL_WORKFLOW_STATUSES.has(status);
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve(false);
      return;
    }
    const timeout = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve(true);
    }, milliseconds);
    const abort = () => {
      clearTimeout(timeout);
      resolve(false);
    };
    signal.addEventListener("abort", abort, { once: true });
  });
}

export async function pollWorkflowUntilTerminal(
  workflowId: string,
  signal: AbortSignal,
  getWorkflow: (workflowId: string, signal: AbortSignal) => Promise<Workflow>,
  onUpdate: (workflow: Workflow) => void,
  options: {
    attempts?: number;
    intervalMilliseconds?: number;
    wait?: (milliseconds: number, signal: AbortSignal) => Promise<boolean>;
  } = {},
): Promise<Workflow | null> {
  const attempts = options.attempts ?? DEFAULT_WORKFLOW_POLL_ATTEMPTS;
  const interval = options.intervalMilliseconds ?? DEFAULT_WORKFLOW_POLL_INTERVAL_MS;
  const wait = options.wait ?? abortableDelay;
  if (!Number.isInteger(attempts) || attempts < 1 || attempts > DEFAULT_WORKFLOW_POLL_ATTEMPTS) {
    throw new Error("Workflow polling attempts are invalid.");
  }
  if (!Number.isFinite(interval) || interval < 0 || interval > 1_000) {
    throw new Error("Workflow polling interval is invalid.");
  }

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (signal.aborted) return null;
    const workflow = await getWorkflow(workflowId, signal);
    if (signal.aborted) return null;
    onUpdate(workflow);
    if (isWorkflowTerminal(workflow.status)) return workflow;
    if (attempt + 1 < attempts && !(await wait(interval, signal))) return null;
  }
  throw new WorkflowPollingTimeoutError();
}
