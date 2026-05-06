/**
 * NodeInspector — Dialog showing details for a right-clicked workflow node.
 *
 * Displays node kind, label, and kind-specific properties.
 * For stage nodes, shows assigned personas and raid linkage.
 *
 * Owner: plugin-tyr (WorkflowBuilder).
 */

import { cn, Dialog, DialogContent } from '@niuulabs/ui';
import type { WorkflowNode } from '../../domain/workflow';
import type { WorkflowBuilderActions } from './useWorkflowBuilder';

export interface NodeInspectorProps {
  node: WorkflowNode;
  onClose: () => void;
  onUpdateLabel: WorkflowBuilderActions['updateNodeLabel'];
  onRemovePersona: WorkflowBuilderActions['removePersonaFromStage'];
}

export function NodeInspector({
  node,
  onClose,
  onUpdateLabel,
  onRemovePersona,
}: NodeInspectorProps) {
  const kindLabel = node.kind === 'stage' ? 'Stage' : node.kind === 'gate' ? 'Gate' : 'Condition';

  return (
    <Dialog
      open
      onOpenChange={(open: boolean) => {
        if (!open) onClose();
      }}
    >
      <DialogContent title={`${kindLabel}: ${node.label}`} description={`Node ID: ${node.id}`}>
        <div data-testid="node-inspector" className="niuu-font-sans niuu-text-sm">
          {/* Kind badge */}
          <div className="niuu-mb-3 niuu-flex niuu-gap-2 niuu-items-center">
            <span
              className={cn(
                'niuu-text-xs niuu-font-semibold niuu-uppercase niuu-tracking-wide niuu-bg-bg-elevated niuu-px-2 niuu-py-0.5 niuu-rounded',
                node.kind === 'stage'
                  ? 'niuu-text-brand'
                  : node.kind === 'gate'
                    ? 'niuu-text-status-amber'
                    : 'niuu-text-status-cyan',
              )}
            >
              {kindLabel}
            </span>
          </div>

          {/* Label editor */}
          <label className="niuu-block niuu-mb-3">
            <span className="niuu-block niuu-text-xs niuu-text-text-muted niuu-mb-1">Label</span>
            <input
              data-testid="inspector-label"
              defaultValue={node.label}
              onBlur={(e) => onUpdateLabel(node.id, e.currentTarget.value)}
              className="niuu-w-full niuu-bg-bg-secondary niuu-border niuu-border-border niuu-rounded niuu-px-2 niuu-py-1.5 niuu-text-text-primary niuu-text-sm niuu-font-sans niuu-box-border"
            />
          </label>

          {/* Kind-specific fields */}
          {node.kind === 'gate' && (
            <div className="niuu-mb-3">
              <span className="niuu-block niuu-text-xs niuu-text-text-muted niuu-mb-1">
                Condition
              </span>
              <div
                data-testid="inspector-condition"
                className="niuu-bg-bg-secondary niuu-border niuu-border-border niuu-rounded niuu-px-2 niuu-py-1.5 niuu-text-text-secondary niuu-text-xs niuu-font-mono"
              >
                {node.condition || <em className="niuu-text-text-muted">not set</em>}
              </div>
            </div>
          )}

          {node.kind === 'cond' && (
            <div className="niuu-mb-3">
              <span className="niuu-block niuu-text-xs niuu-text-text-muted niuu-mb-1">
                Predicate
              </span>
              <div
                data-testid="inspector-predicate"
                className="niuu-bg-bg-secondary niuu-border niuu-border-border niuu-rounded niuu-px-2 niuu-py-1.5 niuu-text-text-secondary niuu-text-xs niuu-font-mono"
              >
                {node.predicate || <em className="niuu-text-text-muted">not set</em>}
              </div>
            </div>
          )}

          {node.kind === 'stage' && (
            <>
              {/* Raid linkage */}
              <div className="niuu-mb-3">
                <span className="niuu-block niuu-text-xs niuu-text-text-muted niuu-mb-1">
                  Raid ID
                </span>
                <div
                  data-testid="inspector-raid-id"
                  className={cn(
                    'niuu-bg-bg-secondary niuu-border niuu-border-border niuu-rounded niuu-px-2 niuu-py-1.5 niuu-text-xs niuu-font-mono',
                    node.raidId ? 'niuu-text-text-secondary' : 'niuu-text-text-muted',
                  )}
                >
                  {node.raidId ?? 'unassigned'}
                </div>
              </div>

              {/* Personas */}
              <div className="niuu-mb-3">
                <span className="niuu-block niuu-text-xs niuu-text-text-muted niuu-mb-1">
                  Personas
                </span>
                {(node.personaIds ?? []).length === 0 ? (
                  <div className="niuu-text-text-muted niuu-text-xs">None assigned</div>
                ) : (
                  <div className="niuu-flex niuu-flex-wrap niuu-gap-1">
                    {(node.personaIds ?? []).map((pid) => (
                      <span
                        key={pid}
                        data-testid={`inspector-persona-${pid}`}
                        className="niuu-inline-flex niuu-items-center niuu-gap-1 niuu-bg-bg-elevated niuu-border niuu-border-border niuu-rounded niuu-px-2 niuu-py-0.5 niuu-text-xs niuu-text-text-primary niuu-font-sans"
                      >
                        {pid}
                        <button
                          data-testid={`remove-persona-${pid}`}
                          onClick={() => onRemovePersona(node.id, pid)}
                          className="niuu-bg-transparent niuu-border-none niuu-cursor-pointer niuu-text-text-muted niuu-p-0 niuu-text-xs niuu-leading-none"
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
