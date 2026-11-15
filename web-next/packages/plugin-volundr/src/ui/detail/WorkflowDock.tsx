import { Workflow, X } from 'lucide-react';
import type { ReactNode } from 'react';
import type { RunWorkflowView } from '../../ports/IRunWorkflowView';
import { TerminalDock } from './TerminalDock';
import './WorkflowDock.css';

/**
 * A run's workflow under its coordinator's conversation, docked as the terminal is: the run
 * drawn on its workflow with where it is at each step, closed here or from Display.
 */
export function WorkflowDock({
  View,
  runId,
  height,
  edge,
  onOpenSession,
  onClose,
}: {
  View: RunWorkflowView;
  runId: string;
  height: number;
  edge?: ReactNode;
  onOpenSession: (sessionId: string) => void;
  onClose: () => void;
}) {
  return (
    <TerminalDock height={height} edge={edge} label="Workflow" testId="workflow-dock">
      <div className="forge-workflow-dock">
        <header className="forge-workflow-dock__head">
          <Workflow className="forge-workflow-dock__icon" aria-hidden="true" />
          <span className="forge-workflow-dock__title">Workflow</span>
          <span className="forge-workflow-dock__run" title={runId}>
            {runId}
          </span>
          <button
            type="button"
            className="forge-workflow-dock__close"
            onClick={onClose}
            aria-label="Close the workflow panel"
            title="Close the workflow panel (Display shows it again)"
          >
            <X aria-hidden="true" />
          </button>
        </header>
        <div className="forge-workflow-dock__body">
          <View runId={runId} onOpenSession={onOpenSession} />
        </div>
      </div>
    </TerminalDock>
  );
}
