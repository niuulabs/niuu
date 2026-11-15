import type { ComponentType } from 'react';

/** What a run's workflow view is given: the run, and a way to open a member's session. */
export interface RunWorkflowViewProps {
  /** The run's slug, as a session names its run (`Session.runId`). */
  runId: string;
  onOpenSession?: (sessionId: string) => void;
}

/**
 * The view that draws a workflow run on its workflow. Forge does not know workflows: the host
 * wires the one its workflow plugin provides (Ting's `RunWorkflowGraph`), and without one a
 * run's coordinator has no workflow panel.
 */
export type RunWorkflowView = ComponentType<RunWorkflowViewProps>;

/** The service key the host registers its run workflow view under. */
export const RUN_WORKFLOW_VIEW_SERVICE = 'volundr.runWorkflowView';
