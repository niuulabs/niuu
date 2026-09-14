/**
 * WorkflowStrip — a horizontal, read-only picture of how a workflow runs.
 *
 * Same derivation as the builder's PipelineView (`topologicalSort` over
 * `structuralWorkflowEdges`), laid out left to right for the Simple-mode
 * workflows page: stages are boxes, gates are diamonds, the end node is
 * dashed. Diamonds are where the run stops and waits for a person.
 *
 * Owner: plugin-ting.
 */

import { cn } from '@niuulabs/ui';
import type { WorkflowEdge, WorkflowGateNode, WorkflowNode } from '../domain/workflow';
import { topologicalSort } from '../domain/topologicalSort';
import { stagePersonaIds, structuralWorkflowEdges } from '../domain/workflowSemantics';

export interface WorkflowStripProps {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  className?: string;
}

/** True when a gate waits for a person rather than resolving itself. */
export function gateWaitsForPerson(node: Pick<WorkflowGateNode, 'mode'>): boolean {
  return (node.mode ?? 'human_approval') !== 'automated_approval';
}

function stageSubLine(node: Extract<WorkflowNode, { kind: 'stage' }>): string {
  const personaIds = stagePersonaIds(node);
  if (personaIds.length === 0) return 'no persona yet';
  if (personaIds.length <= 2) return personaIds.join(' · ');
  return `${personaIds.slice(0, 2).join(' · ')} +${personaIds.length - 2}`;
}

function Connector() {
  return (
    <span
      aria-hidden="true"
      className="niuu:self-center niuu:h-px niuu:w-6 niuu:shrink-0 niuu:bg-border"
    />
  );
}

function GateNode({ node }: { node: Extract<WorkflowNode, { kind: 'gate' }> }) {
  const waits = gateWaitsForPerson(node);
  return (
    <div
      data-testid={`workflow-strip-node-${node.id}`}
      data-kind="gate"
      className="niuu:flex niuu:w-[132px] niuu:shrink-0 niuu:flex-col niuu:items-center niuu:gap-2"
    >
      <span
        aria-hidden="true"
        className={cn(
          'niuu:mt-1 niuu:size-7 niuu:rotate-45 niuu:rounded-[3px] niuu:border',
          waits
            ? 'niuu:border-status-amber niuu:bg-status-amber/15'
            : 'niuu:border-border niuu:bg-bg-elevated',
        )}
      />
      <span className="niuu:text-center niuu:text-[11px] niuu:font-semibold niuu:text-text-primary">
        {waits ? 'You approve' : 'Automatic'}
      </span>
      <span className="niuu:text-center niuu:text-[10px] niuu:text-text-muted niuu:leading-snug">
        {node.label}
      </span>
    </div>
  );
}

function BoxNode({ node }: { node: WorkflowNode }) {
  const dashed = node.kind === 'end';
  const sub =
    node.kind === 'stage'
      ? stageSubLine(node)
      : node.kind === 'trigger'
        ? (node.source ?? 'manual dispatch')
        : node.kind === 'cond'
          ? node.predicate || 'condition'
          : node.kind === 'end'
            ? 'run finishes'
            : 'resource';
  return (
    <div
      data-testid={`workflow-strip-node-${node.id}`}
      data-kind={node.kind}
      className={cn(
        'niuu:flex niuu:w-[132px] niuu:shrink-0 niuu:flex-col niuu:gap-1 niuu:rounded-md niuu:px-3 niuu:py-2.5',
        dashed
          ? 'niuu:border niuu:border-dashed niuu:border-border niuu:bg-transparent'
          : 'niuu:border niuu:border-border niuu:bg-bg-elevated',
      )}
    >
      <span className="niuu:truncate niuu:text-[12px] niuu:font-semibold niuu:text-text-primary">
        {node.label}
      </span>
      <span className="niuu:truncate niuu:text-[10px] niuu:font-mono niuu:text-text-faint">
        {sub}
      </span>
    </div>
  );
}

export function WorkflowStrip({ nodes, edges, className }: WorkflowStripProps) {
  const layers = topologicalSort(
    nodes.map((node) => node.id),
    structuralWorkflowEdges(edges),
  );
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const placed = new Set(layers.flatMap((layer) => layer.nodeIds));
  const unplaced = nodes.filter((node) => !placed.has(node.id));

  if (nodes.length === 0) {
    return (
      <p
        data-testid="workflow-strip"
        className="niuu:m-0 niuu:text-sm niuu:text-text-muted niuu:font-sans"
      >
        This workflow has no stages yet. Edit stages to add the first one.
      </p>
    );
  }

  return (
    <div data-testid="workflow-strip" className={cn('niuu:font-sans', className)}>
      <div className="niuu:flex niuu:items-stretch niuu:gap-0 niuu:overflow-x-auto niuu:pb-2">
        {layers.map((layer, index) => (
          <div key={layer.depth} className="niuu:flex niuu:items-stretch">
            {index > 0 ? <Connector /> : null}
            <div className="niuu:flex niuu:flex-col niuu:gap-2">
              {layer.nodeIds.map((id) => {
                const node = nodeById.get(id);
                if (!node) return null;
                if (node.kind === 'gate') return <GateNode key={id} node={node} />;
                return <BoxNode key={id} node={node} />;
              })}
            </div>
          </div>
        ))}

        {unplaced.length > 0 ? (
          <div className="niuu:flex niuu:items-stretch">
            <Connector />
            <div className="niuu:flex niuu:flex-col niuu:gap-2" data-testid="workflow-strip-cycle">
              <span className="niuu:text-[10px] niuu:font-semibold niuu:uppercase niuu:tracking-wide niuu:text-critical">
                Loops back
              </span>
              {unplaced.map((node) =>
                node.kind === 'gate' ? (
                  <GateNode key={node.id} node={node} />
                ) : (
                  <BoxNode key={node.id} node={node} />
                ),
              )}
            </div>
          </div>
        ) : null}
      </div>

      <p className="niuu:m-0 niuu:mt-3 niuu:text-[11px] niuu:text-text-muted">
        Diamonds are where it stops and waits for you. Boxes run on their own.
      </p>
    </div>
  );
}
