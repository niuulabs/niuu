/**
 * Pure presentation logic extracted from `GraphView.tsx`'s node-card
 * components. Every function here is a straight extraction of a ternary
 * chain that used to live inline in a React component — same inputs, same
 * outputs, just testable without rendering SVG. Keep it that way: no DOM,
 * no CSS variables beyond the literal strings the caller passes in, no
 * React imports.
 */

import type { WorkflowResourceNode, WorkflowSubworkflowNode } from './workflow';

export type GraphIssueLevel = 'error' | 'warning' | null;

export interface NodeCardColors {
  /** Border/background used when `issueLevel` is null and the card isn't selected. */
  defaultBackground: string;
  defaultBorder: string;
  /** Used when `issueLevel === 'error'`. */
  errorBackground: string;
  errorBorder: string;
  /** Used when `issueLevel === 'warning'`. */
  warningBackground: string;
  warningBorder: string;
  /** Border color when `selected` and there is no issue-driven border override. */
  selectedBorder: string;
  /**
   * Background used when `selected` is true, taking priority over the
   * issue-level background. Card kinds that ONLY let selection affect the
   * border (Stage/Gate/Cond) omit this so background stays issue-driven.
   */
  selectedBackground?: string;
}

export interface NodeCardVisualState {
  background: string;
  borderColor: string;
  borderWidth: 1 | 2;
}

/**
 * The background/border/border-width a workflow node card renders with,
 * given whether it's selected and its highest validation issue severity.
 * Every node-card component (`StageNode`, `GateNode`, `CondNode`,
 * `TriggerNode`, `WaitNode`, `EndNode`, `ResourceNode`, `IncludeNode`) used
 * to repeat this exact chain of ternaries with its own color literals; this
 * is the one shared decision, parameterized by the kind's colors.
 */
export function resolveNodeCardVisuals(
  selected: boolean,
  issueLevel: GraphIssueLevel,
  colors: NodeCardColors,
): NodeCardVisualState {
  const background =
    selected && colors.selectedBackground !== undefined
      ? colors.selectedBackground
      : issueLevel === 'error'
        ? colors.errorBackground
        : issueLevel === 'warning'
          ? colors.warningBackground
          : colors.defaultBackground;
  const borderColor = selected
    ? colors.selectedBorder
    : issueLevel === 'error'
      ? colors.errorBorder
      : issueLevel === 'warning'
        ? colors.warningBorder
        : colors.defaultBorder;
  return {
    background,
    borderColor,
    borderWidth: selected || issueLevel ? 2 : 1,
  };
}

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

export type NodeMouseDownAction = 'ignore' | 'complete-connect' | 'select-only' | 'start-drag';

/**
 * What a node card's mousedown should do, extracted from `useDragNode`'s
 * `handleMouseDown`: a non-primary or ctrl-modified click is ignored (so
 * ctrl-click still reaches the canvas's own "open add-step menu" handler); a
 * connecting-mode click completes the pending connection instead of
 * starting a drag; a read-only card only selects; otherwise the card starts
 * a real drag (and selects).
 */
export function resolveNodeMouseDownAction(
  button: number,
  ctrlKey: boolean,
  isConnectingMode: boolean,
  readOnly: boolean,
): NodeMouseDownAction {
  if (button !== 0 || ctrlKey) return 'ignore';
  if (isConnectingMode) return 'complete-connect';
  if (readOnly) return 'select-only';
  return 'start-drag';
}

/**
 * Whether an in-progress node drag has moved far enough from its start
 * point to count as an actual drag rather than a click. Used both while
 * dragging (to decide whether to emit a preview) and on mouseup (to decide
 * whether to commit a move at all).
 */
export function exceedsDragThreshold(dx: number, dy: number, threshold = 4): boolean {
  return Math.abs(dx) > threshold || Math.abs(dy) > threshold;
}
