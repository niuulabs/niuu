/**
 * Pure presentation logic extracted from `GraphView.tsx`'s node-card
 * components. Every function here is a straight extraction of a ternary
 * chain that used to live inline in a React component — same inputs, same
 * outputs, just testable without rendering SVG. Keep it that way: no DOM,
 * no CSS variables beyond the literal strings the caller passes in, no
 * React imports.
 */

import type { WorkflowResourceNode, WorkflowSubworkflowNode } from './workflow';

/**
 * Truncate a node/port label for display, appending an ellipsis. `keepLength`
 * is the number of characters kept before the ellipsis — the card components
 * each used their own (maxLength, keepLength) pair, so both are parameters
 * rather than one derived from the other.
 */
export function truncateLabel(label: string, maxLength: number, keepLength: number): string {
  if (label.length <= maxLength) return label;
  return `${label.slice(0, keepLength)}…`;
}

/** Summarize a subworkflow node's offered templates for its card subtitle:
 *  the sole child workflow's alias when there is exactly one template, a
 *  short name list for a couple more, and a compact count once the list
 *  would no longer fit the card. */
export function subworkflowTemplatesSummary(node: WorkflowSubworkflowNode): string {
  const entries = Object.entries(node.templates ?? {});
  if (entries.length === 0) return 'choose a child workflow';
  if (entries.length === 1) {
    const [templateName, alias] = entries[0]!;
    return alias || `${templateName}: choose a child workflow`;
  }
  const names = entries.map(([templateName]) => templateName).sort();
  const joined = names.join(', ');
  return joined.length <= 24 ? joined : `${names.length} child workflows`;
}

/** The event a trigger/subworkflow card's single output port carries. */
export function triggerOutputEvent(node: { kind: string; dispatchEvent?: string | null }): string {
  return node.kind === 'subworkflow'
    ? 'children.completed'
    : (node.dispatchEvent ?? 'code.requested');
}

/** The mono subtitle line under a trigger/subworkflow card's title. */
export function triggerCardSubtitle(
  node: WorkflowSubworkflowNode | { kind: string; maxChildren?: never },
  outputEvent: string,
): string {
  if (node.kind === 'subworkflow') {
    const subworkflow = node as WorkflowSubworkflowNode;
    return `1–${subworkflow.maxChildren} · ${subworkflowTemplatesSummary(subworkflow)}`;
  }
  return outputEvent;
}

export interface ResourceModeVisuals {
  modeLabel: 'EPHEMERAL' | 'REGISTRY';
  modeStroke: string;
  modeFill: string;
}

/** The pill styling on a resource card distinguishing an ephemeral local
 *  binding from a shared registry one. */
export function resourceModeVisuals(
  bindingMode: WorkflowResourceNode['bindingMode'],
): ResourceModeVisuals {
  const ephemeral = bindingMode === 'ephemeral_local';
  return {
    modeLabel: ephemeral ? 'EPHEMERAL' : 'REGISTRY',
    modeStroke: ephemeral ? 'var(--status-emerald)' : 'var(--color-brand)',
    modeFill: ephemeral
      ? 'color-mix(in srgb, var(--status-emerald) 14%, var(--color-bg-primary))'
      : 'color-mix(in srgb, var(--color-brand) 14%, var(--color-bg-primary))',
  };
}

/** The `<workflow · N node(s)>` subtitle on an include card. */
export function includeCardSubtitle(workflow: string | undefined, nodeCount: number): string {
  return `${workflow || 'choose a workflow'} · ${nodeCount} node${nodeCount === 1 ? '' : 's'}`;
}

export type NodeMouseDownAction = 'ignore' | 'complete-connect' | 'select';

/**
 * What a mousedown on a node card does: a non-primary or ctrl-modified click is ignored (so
 * ctrl-click still reaches the canvas's own "open add-step menu" handler); while a wire is
 * drawn it completes the wire; otherwise it selects the card. The canvas lays cards out
 * itself, so a card is never dragged, editable or not.
 */
export function resolveNodeMouseDownAction(
  button: number,
  ctrlKey: boolean,
  isConnectingMode: boolean,
): NodeMouseDownAction {
  if (button !== 0 || ctrlKey) return 'ignore';
  if (isConnectingMode) return 'complete-connect';
  return 'select';
}
