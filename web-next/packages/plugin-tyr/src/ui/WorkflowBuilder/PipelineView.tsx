/**
 * PipelineView — read-only vertical topological layout of the workflow.
 *
 * Uses `topologicalSort()` to assign each node a depth layer.
 * Nodes are displayed in horizontal rows by depth with connecting lines.
 *
 * Owner: plugin-tyr (WorkflowBuilder).
 */

import { cn } from '@niuulabs/ui';
import type { WorkflowNode, WorkflowEdge, WorkflowStageNode } from '../../domain/workflow';
import { topologicalSort } from '../../domain/topologicalSort';
import { normalizedStageMembers } from './graphUtils';

export interface PipelineViewProps {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  selectedNodeId?: string | null;
  onSelectNode?: (id: string) => void;
}

const KIND_LABEL: Record<WorkflowNode['kind'], string> = {
  trigger: 'Trigger',
  stage: 'Stage',
  gate: 'Gate',
  cond: 'Cond',
  end: 'End',
  resource: 'Resource',
};

const KIND_BADGE_CLASS: Record<WorkflowNode['kind'], string> = {
  trigger: 'niuu-text-status-cyan',
  stage: 'niuu-text-brand',
  gate: 'niuu-text-status-amber',
  cond: 'niuu-text-status-cyan',
  end: 'niuu-text-status-emerald',
  resource: 'niuu-text-text-secondary',
};

function stageSummary(node: WorkflowStageNode) {
  const members = normalizedStageMembers(node);
  return {
    members,
    mode: node.executionMode ?? 'parallel',
    joinMode: node.joinMode ?? 'all',
    maxConcurrent: node.maxConcurrent ?? 3,
  };
}

export function PipelineView({ nodes, edges, selectedNodeId, onSelectNode }: PipelineViewProps) {
  const layers = topologicalSort(
    nodes.map((n) => n.id),
    edges,
  );

  const nodeById = new Map<string, WorkflowNode>(nodes.map((n) => [n.id, n]));

  // Nodes excluded from topological layers (part of a cycle)
  const layerNodeIds = new Set(layers.flatMap((l) => l.nodeIds));
  const cycleNodes = nodes.filter((n) => !layerNodeIds.has(n.id));

  if (nodes.length === 0) {
    return (
      <div
        data-testid="pipeline-view"
        className="niuu-flex-1 niuu-flex niuu-items-center niuu-justify-center niuu-text-text-muted niuu-text-sm niuu-font-sans"
      >
        No nodes — add stages in the Graph view.
      </div>
    );
  }

  return (
    <div
      data-testid="pipeline-view"
      className="niuu-flex-1 niuu-overflow-y-auto niuu-p-6 niuu-font-sans niuu-bg-bg-primary"
    >
      {layers.map((layer, layerIdx) => (
        <div key={layer.depth}>
          {/* Layer label */}
          <div className="niuu-text-xs niuu-text-text-muted niuu-uppercase niuu-tracking-widest niuu-mb-1.5">
            Layer {layer.depth}
          </div>

          {/* Nodes in this layer */}
          <div className="niuu-flex niuu-flex-wrap niuu-gap-2 niuu-mb-2">
            {layer.nodeIds.map((id) => {
              const node = nodeById.get(id);
              if (!node) return null;
              const isSelected = node.id === selectedNodeId;
              return (
                <button
                  key={id}
                  data-testid={`pipeline-node-${id}`}
                  data-selected={isSelected ? 'true' : undefined}
                  onClick={() => onSelectNode?.(id)}
                  className={cn(
                    'niuu-rounded-sm niuu-px-3.5 niuu-py-2 niuu-cursor-pointer niuu-flex niuu-flex-col niuu-items-start niuu-gap-0.5 niuu-min-w-[120px] niuu-font-sans niuu-border',
                    isSelected
                      ? 'niuu-bg-bg-elevated niuu-border-brand'
                      : 'niuu-bg-bg-secondary niuu-border-border',
                  )}
                >
                  <span
                    className={cn(
                      'niuu-text-[9px] niuu-font-semibold niuu-uppercase niuu-tracking-wide',
                      KIND_BADGE_CLASS[node.kind],
                    )}
                  >
                    {KIND_LABEL[node.kind]}
                  </span>
                  <span className="niuu-text-sm niuu-text-text-primary niuu-font-medium">
                    {node.label}
                  </span>
                  {node.kind === 'trigger' && (
                    <span className="niuu-text-xs niuu-text-text-muted">
                      {node.source ?? 'manual dispatch'}
                    </span>
                  )}
                  {node.kind === 'stage' &&
                    (() => {
                      const summary = stageSummary(node);
                      return (
                        <>
                          <span className="niuu-text-xs niuu-text-text-muted">
                            {summary.members.length} persona
                            {summary.members.length !== 1 ? 's' : ''} · {summary.members.length}{' '}
                            ravn{summary.members.length !== 1 ? 's' : ''} · {summary.mode}
                          </span>
                          <span className="niuu-text-[10px] niuu-font-mono niuu-text-text-faint">
                            join {summary.joinMode} · max {summary.maxConcurrent}
                          </span>
                        </>
                      );
                    })()}
                  {node.kind === 'gate' && (
                    <span className="niuu-text-xs niuu-text-text-muted">
                      {(node.approvers ?? []).length || 1} approver
                      {(node.approvers ?? []).length === 1 ? '' : 's'}
                    </span>
                  )}
                  {node.kind === 'cond' && (
                    <span className="niuu-text-[10px] niuu-font-mono niuu-text-text-faint niuu-max-w-[180px] niuu-truncate">
                      {node.predicate || 'expr …'}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          {/* Connector arrow between layers */}
          {layerIdx < layers.length - 1 && (
            <div className="niuu-ml-4 niuu-mb-2 niuu-text-border niuu-text-lg niuu-leading-none">
              ↓
            </div>
          )}
        </div>
      ))}

      {/* Cycle nodes (excluded from topological sort) */}
      {cycleNodes.length > 0 && (
        <div className="niuu-mt-4">
          <div className="niuu-text-xs niuu-text-critical niuu-uppercase niuu-tracking-widest niuu-mb-1.5">
            ⚠ Cycle nodes (excluded)
          </div>
          <div className="niuu-flex niuu-flex-wrap niuu-gap-2">
            {cycleNodes.map((node) => (
              <button
                key={node.id}
                data-testid={`pipeline-node-${node.id}`}
                onClick={() => onSelectNode?.(node.id)}
                className="niuu-bg-critical-bg niuu-border niuu-border-critical niuu-rounded-sm niuu-px-3.5 niuu-py-2 niuu-cursor-pointer niuu-font-sans niuu-text-critical-fg niuu-text-sm"
              >
                {node.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
