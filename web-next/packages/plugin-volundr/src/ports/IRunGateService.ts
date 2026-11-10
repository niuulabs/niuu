/** A gate of a workflow run waiting for a person. */
export interface RunGate {
  gateId: string;
  activationId: string;
  /** What is being decided, e.g. "Approve merge". */
  title: string;
  /** What the run did that the person decides on. */
  summary: string;
  /** When the gate opened (ISO 8601). */
  requestedAt: string;
}

/**
 * A workflow run's waiting gate, read and answered for the run's coordinator session, so the
 * session asks for the decision where it asks every other question: above its composer. Forge
 * does not know workflows; the host wires the service its workflow plugin provides (Ting's),
 * and without one a run's gate is answered on the workflow plugin's own pages.
 */
export interface IRunGateService {
  /** The run's gate waiting for a person, or ``null`` when none waits. */
  waitingGate(runId: string): Promise<RunGate | null>;
  decide(
    runId: string,
    gate: Pick<RunGate, 'gateId' | 'activationId'>,
    decision: 'approve' | 'request_changes',
    notes: string,
  ): Promise<void>;
}

/** The service key the host registers its run gate service under. */
export const RUN_GATE_SERVICE = 'volundr.runGates';
