/**
 * GraphView — interactive pan/zoom SVG canvas.
 *
 * Renders WorkflowNodes as SVG shapes and WorkflowEdges as bezier curves.
 * Supports:
 *  • Mouse-drag pan on the canvas background
 *  • Scroll-wheel zoom (clamped 0.3×–3×)
 *  • Node drag-to-reposition
 *  • Click to select
 *  • Right-click empty canvas to add a node; right-click nodes to inspect
 *  • "Connect" button on selected node to draw edges
 *  • Add-node toolbar
 *  • Delete-node button on selected node
 *
 * Owner: plugin-ting (WorkflowBuilder).
 */

import { useRef, useState, useEffect, useMemo, useCallback } from 'react';
import { cn } from '@niuulabs/ui';
import './nodeAccent.css';
import './workflowCanvas.css';
import type {
  WorkflowNode,
  WorkflowEdge,
  WorkflowStageNode,
  WorkflowGateNode,
  WorkflowCondNode,
  WorkflowTriggerNode,
  WorkflowSubworkflowNode,
  WorkflowEndNode,
  WorkflowResourceNode,
  WorkflowWaitNode,
  WorkflowIncludeNode,
} from '../../domain/workflow';
import { providedNodeIds } from '../../domain/workflow';
import type { WorkflowIssue } from '../../domain/workflowValidation';
import { isReentryEdge, parseWorkflowEdgeLabel } from '../../domain/workflowSemantics';
import {
  nodeMatchIds,
  nodePortCatalog,
  resolveIncludeEndpoint,
  resolveWorkflowEdgePorts,
  type WorkflowNodePort,
  type WorkflowNodePortCatalog,
} from '../../domain/workflowPorts';
import {
  feedbackLaneAssignments,
  nodeBounds,
  portAnchor,
  type WorkflowSize,
} from '../../domain/workflowGeometry';
import type { WorkflowBuilderActions } from './useWorkflowBuilder';
import { LibraryPanel, type PersonaEntry } from './LibraryPanel';
import {
  MIMIR_MOUNT_MIME,
  parseWorkflowRegistryMount,
  type WorkflowRegistryMount,
} from './mimirRegistry';
import {
  STAGE_WIDTH,
  GATE_SIZE,
  COND_RADIUS,
  TRIGGER_WIDTH,
  TRIGGER_HEIGHT,
  WAIT_WIDTH,
  WAIT_HEIGHT,
  END_RADIUS,
  RESOURCE_WIDTH,
  RESOURCE_HEIGHT,
  INCLUDE_WIDTH,
  INCLUDE_HEIGHT,
  stageNodeHeight,
  normalizedStageMembers,
} from './graphUtils';

// ---------------------------------------------------------------------------
// Zoom / pan constants
// ---------------------------------------------------------------------------

const MIN_ZOOM = 0.3;
const MAX_ZOOM = 3.0;

// ---------------------------------------------------------------------------
// Colour palette (CSS variables)
// ---------------------------------------------------------------------------

const C = {
  bg: 'var(--color-bg-primary)',
  nodeStroke: 'var(--color-border)',
  nodeStrokeSelected: 'var(--color-brand)',
  nodeFill: 'var(--color-bg-secondary)',
  nodeFillConnecting: 'var(--color-bg-elevated)',
  text: 'var(--color-text-primary)',
  textMuted: 'var(--color-text-secondary)',
  edgeStroke: 'var(--color-border)',
  edgeStrokeHover: 'var(--color-text-secondary)',
  gate: 'color-mix(in srgb, var(--color-gate) 20%, var(--color-bg-secondary))',
  gateStroke: 'var(--color-gate)',
  cond: 'color-mix(in srgb, var(--color-accent-teal) 20%, var(--color-bg-secondary))',
  condStroke: 'var(--color-accent-teal)',
  warnStroke: 'var(--status-amber)',
  errorStroke: 'var(--color-critical)',
  warnFill: 'color-mix(in srgb, var(--status-amber) 12%, var(--color-bg-secondary))',
  errorFill: 'color-mix(in srgb, var(--color-critical) 12%, var(--color-bg-secondary))',
};

// ---------------------------------------------------------------------------
// useDragNode — shared drag-to-reposition logic for node components
// ---------------------------------------------------------------------------

function useDragNode({
  x,
  y,
  isConnectingMode,
  onSelect,
  onCompleteConnect,
  onDragPreview,
  onDragEnd,
  canvasScale,
  readOnly,
}: {
  x: number;
  y: number;
  isConnectingMode: boolean;
  onSelect: () => void;
  onCompleteConnect: () => void;
  onDragPreview: (pos: { x: number; y: number }) => void;
  onDragEnd: (pos: { x: number; y: number }) => void;
  canvasScale: number;
  readOnly: boolean;
}) {
  const dragRef = useRef<{
    startX: number;
    startY: number;
    nx: number;
    ny: number;
    dx: number;
    dy: number;
  } | null>(null);

  function handleMouseDown(e: React.MouseEvent) {
    e.stopPropagation();
    if (e.button !== 0 || e.ctrlKey) return;
    if (isConnectingMode) {
      onCompleteConnect();
      return;
    }
    if (readOnly) {
      onSelect();
      return;
    }
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      nx: x,
      ny: y,
      dx: 0,
      dy: 0,
    };
    onSelect();
  }

  function handleMouseMove(e: React.MouseEvent) {
    if (!dragRef.current) return;
    const dx = (e.clientX - dragRef.current.startX) / canvasScale;
    const dy = (e.clientY - dragRef.current.startY) / canvasScale;
    if (Math.abs(dx) <= 4 && Math.abs(dy) <= 4) return;
    dragRef.current.dx = dx;
    dragRef.current.dy = dy;
    onDragPreview({ x: dragRef.current.nx + dx, y: dragRef.current.ny + dy });
  }

  function handleMouseUp() {
    const drag = dragRef.current;
    dragRef.current = null;
    if (!drag) return;
    if (drag.dx === 0 && drag.dy === 0) return;
    onDragEnd({ x: drag.nx + drag.dx, y: drag.ny + drag.dy });
  }

  return { handleMouseDown, handleMouseMove, handleMouseUp };
}

// ---------------------------------------------------------------------------
// DeleteButton — shared SVG button primitive
// ---------------------------------------------------------------------------

function DeleteButton({
  nodeId,
  cx,
  cy,
  onClick,
}: {
  nodeId: string;
  cx: number;
  cy: number;
  onClick: () => void;
}) {
  return (
    <g
      data-testid={`delete-btn-${nodeId}`}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className="niuu:cursor-pointer"
    >
      <circle cx={cx} cy={cy} r={7} fill="var(--color-critical)" />
      <text
        x={cx}
        y={cy}
        textAnchor="middle"
        dominantBaseline="middle"
        fill="var(--color-bg-primary)"
        fontSize={10}
        className="niuu:pointer-events-none"
      >
        ×
      </text>
    </g>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

interface BaseNodeProps<TNode extends WorkflowNode> {
  node: TNode;
  selected: boolean;
  issueLevel: 'error' | 'warning' | null;
  onSelect: () => void;
  onInspect: () => void;
  onStartConnect: (label?: string) => void;
  onCompleteConnect: (inputLabel?: string) => void;
  onDelete: () => void;
  onDragPreview: (position: { x: number; y: number }) => void;
  onDragEnd: (position: { x: number; y: number }) => void;
  isConnectingMode: boolean;
  canvasScale: number;
  readOnly: boolean;
}

/**
 * StageNode — the redesigned card.
 *
 * Renders as real HTML (rounded corners, shadow, flex layout) inside a
 * `<foreignObject>` rather than hand-placed SVG `<rect>`/`<text>` shapes —
 * SVG can't do the rounded-card-with-shadow-and-flex look the redesign
 * calls for. `useDragNode`, the outer `<g>` (testid/data-kind/mouse
 * handlers), `DeleteButton`, and every other node kind are untouched; this
 * is the first of the seven kinds converted, ported from the
 * `docs/mockups/workflow-builder` node-card treatment.
 *
 * The one thing that must stay in lockstep with `graphUtils.ts` is size:
 * `stageNodeHeight`/`renderedStageHeight`'s formulas are unchanged on
 * purpose, so `edgeAnchor`'s stage-port math (and hit-testing) still lines
 * up with what's actually drawn here — the port footer is bottom-anchored
 * inside the card at exactly `portRows * 14` px, same as before.
 */
function StageNode({
  node,
  portCatalog,
  connectingFromLabel,
  selected,
  issueLevel,
  onSelect,
  onInspect,
  onStartConnect,
  onCompleteConnect,
  onDelete,
  onDragPreview,
  onDragEnd,
  isConnectingMode,
  canvasScale,
  readOnly,
}: BaseNodeProps<WorkflowStageNode> & {
  portCatalog: WorkflowNodePortCatalog;
  connectingFromLabel?: string | null;
}) {
  const { x, y } = node.position;
  const stageMembers = normalizedStageMembers(node);
  const knownInputs = portCatalog.inputs.map((port) => port.eventType);
  const knownOutputs = portCatalog.outputs.map((port) => port.eventType);
  const portRows = Math.max(knownInputs.length, knownOutputs.length, 0);
  const headerHeight = stageNodeHeight(node);
  const stageHeight = headerHeight + (portRows > 0 ? 22 + portRows * 14 : 0);
  const { handleMouseDown, handleMouseMove, handleMouseUp } = useDragNode({
    x,
    y,
    isConnectingMode,
    onSelect,
    onCompleteConnect: () => {},
    onDragPreview,
    onDragEnd,
    canvasScale,
    readOnly,
  });
  const borderColor = selected
    ? C.nodeStrokeSelected
    : issueLevel === 'error'
      ? C.errorStroke
      : issueLevel === 'warning'
        ? C.warnStroke
        : C.nodeStroke;
  const background =
    issueLevel === 'error' ? C.errorFill : issueLevel === 'warning' ? C.warnFill : C.nodeFill;
  const title = node.label.length > 18 ? `${node.label.slice(0, 16)}…` : node.label;

  return (
    <g
      data-testid={`workflow-node-${node.id}`}
      data-kind="stage"
      data-selected={selected ? 'true' : undefined}
      role="button"
      tabIndex={0}
      aria-label={`Stage step: ${node.label}`}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onInspect();
        if (event.key === ' ') {
          event.preventDefault();
          onSelect();
        }
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={(e) => {
        e.preventDefault();
        onInspect();
      }}
      style={{ cursor: isConnectingMode ? 'crosshair' : 'grab' }}
    >
      <foreignObject x={x} y={y} width={STAGE_WIDTH} height={stageHeight}>
        <div
          className="workflow-stage-card niuu:h-full niuu:w-full niuu:overflow-hidden niuu:rounded-lg niuu:border niuu:font-sans niuu:shadow-md"
          style={{
            background,
            borderColor,
            borderWidth: selected || issueLevel ? 2 : 1,
            borderLeftColor: 'var(--node-accent)',
            borderLeftWidth: 3,
          }}
        >
          {/* header + members */}
          <div
            className="niuu:flex niuu:select-none niuu:flex-col niuu:overflow-hidden niuu:pb-1 niuu:pl-[11px] niuu:pr-[9px] niuu:pt-[7px]"
            style={{ height: headerHeight }}
          >
            <div className="niuu:mb-[3px] niuu:flex niuu:items-center niuu:gap-[6px]">
              <span
                className="niuu:font-mono niuu:text-[9px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.16em]"
                style={{ color: 'var(--node-accent)' }}
              >
                Stage
              </span>
              <span className="niuu:flex-1" />
              <span className="niuu:whitespace-nowrap niuu:font-mono niuu:text-[8.5px] niuu:text-text-secondary">
                {node.executionMode ?? 'parallel'} · {stageMembers.length}
              </span>
              {issueLevel && (
                <span
                  className="niuu:whitespace-nowrap niuu:font-mono niuu:text-[8.5px] niuu:font-semibold"
                  style={{ color: issueLevel === 'error' ? C.errorStroke : C.warnStroke }}
                >
                  {issueLevel === 'error' ? 'ERR' : 'WARN'}
                </span>
              )}
            </div>
            <div
              className="niuu:mb-[5px] niuu:truncate niuu:text-[13px] niuu:font-semibold niuu:leading-tight"
              style={{ color: C.text }}
              title={node.label}
            >
              {title}
            </div>
            <div className="niuu:flex niuu:min-h-0 niuu:flex-col niuu:gap-[3px] niuu:overflow-hidden">
              {stageMembers.map((member, index) => (
                <div
                  key={`${member.personaId}-${index}`}
                  className="niuu:flex niuu:items-center niuu:gap-[5px] niuu:text-[10px]"
                >
                  <span className="niuu:truncate niuu:font-medium" style={{ color: C.text }}>
                    {member.personaId}
                  </span>
                  <span
                    className="niuu:ml-auto niuu:whitespace-nowrap niuu:font-mono niuu:text-[9px]"
                    style={{ color: C.textMuted }}
                  >
                    {member.model ? member.model : `budget ${member.budget}`}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* typed port footer — bottom-anchored at portRows * 14px, matching edgeAnchor */}
          {portRows > 0 && (
            <div
              hidden
              aria-hidden="true"
              className="niuu:absolute niuu:inset-x-0 niuu:bottom-0 niuu:border-t"
              style={{ height: portRows * 14, borderColor: 'var(--color-border-subtle)' }}
            >
              {knownInputs.map((input, index) => (
                <div
                  key={`in-${input}-${index}`}
                  className="niuu:absolute niuu:left-0 niuu:flex niuu:items-center niuu:gap-[4px] niuu:pl-[6px]"
                  style={{ top: index * 14, height: 14 }}
                >
                  {isConnectingMode && (
                    <button
                      type="button"
                      data-testid={`stage-port-in-${node.id}-${input}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        onCompleteConnect(input);
                      }}
                      className="niuu:h-[8px] niuu:w-[8px] niuu:shrink-0 niuu:cursor-pointer niuu:rounded-full niuu:border niuu:p-0"
                      style={{
                        borderColor: 'var(--color-brand)',
                        background: 'var(--color-bg-primary)',
                      }}
                      aria-label={`Connect input ${input}`}
                    />
                  )}
                  <span
                    className="niuu:select-none niuu:truncate niuu:font-mono niuu:text-[6.5px]"
                    style={{ color: C.textMuted, maxWidth: STAGE_WIDTH / 2 - 20 }}
                  >
                    {input.length > 14 ? `${input.slice(0, 13)}…` : input}
                  </span>
                </div>
              ))}
              {knownOutputs.map((output, index) => (
                <div
                  key={`out-${output}-${index}`}
                  className="niuu:absolute niuu:right-0 niuu:flex niuu:items-center niuu:gap-[4px] niuu:pr-[6px]"
                  style={{ top: index * 14, height: 14 }}
                >
                  <span
                    className="niuu:select-none niuu:truncate niuu:text-right niuu:font-mono niuu:text-[6.5px]"
                    style={{ color: C.text, maxWidth: STAGE_WIDTH / 2 - 20 }}
                  >
                    {output.length > 14 ? `${output.slice(0, 13)}…` : output}
                  </span>
                  <button
                    type="button"
                    data-testid={`stage-port-out-${node.id}-${output}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      onStartConnect(output);
                    }}
                    className="niuu:h-[8px] niuu:w-[8px] niuu:shrink-0 niuu:cursor-pointer niuu:rounded-full niuu:border niuu:p-0"
                    style={{
                      borderColor: 'var(--color-brand)',
                      background:
                        connectingFromLabel === output
                          ? 'var(--color-brand)'
                          : 'var(--color-bg-primary)',
                    }}
                    aria-label={`Connect output ${output}`}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      </foreignObject>
      {selected && !isConnectingMode && !readOnly && (
        <DeleteButton nodeId={node.id} cx={x + STAGE_WIDTH / 2} cy={y - 10} onClick={onDelete} />
      )}
    </g>
  );
}

/** GateNode — same card recipe as StageNode, same GATE_SIZE bounding box (no
 *  ports on gates today, so no port-footer needed). */
function GateNode({
  node,
  selected,
  issueLevel,
  onSelect,
  onInspect,
  onStartConnect: _onStartConnect,
  onCompleteConnect,
  onDelete,
  onDragPreview,
  onDragEnd,
  isConnectingMode,
  canvasScale,
  readOnly,
}: BaseNodeProps<WorkflowGateNode>) {
  const { x, y } = node.position;
  const { handleMouseDown, handleMouseMove, handleMouseUp } = useDragNode({
    x,
    y,
    isConnectingMode,
    onSelect,
    onCompleteConnect,
    onDragPreview,
    onDragEnd,
    canvasScale,
    readOnly,
  });
  const background =
    issueLevel === 'error' ? C.errorFill : issueLevel === 'warning' ? C.warnFill : C.gate;
  const borderColor = selected
    ? C.nodeStrokeSelected
    : issueLevel === 'error'
      ? C.errorStroke
      : issueLevel === 'warning'
        ? C.warnStroke
        : C.gateStroke;
  const title = node.label.length > 8 ? `${node.label.slice(0, 7)}…` : node.label;

  return (
    <g
      data-testid={`workflow-node-${node.id}`}
      data-kind="gate"
      data-selected={selected ? 'true' : undefined}
      role="button"
      tabIndex={0}
      aria-label={`Gate step: ${node.label}`}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onInspect();
        if (event.key === ' ') {
          event.preventDefault();
          onSelect();
        }
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={(e) => {
        e.preventDefault();
        onInspect();
      }}
      style={{ cursor: isConnectingMode ? 'crosshair' : 'grab' }}
    >
      <foreignObject x={x} y={y} width={GATE_SIZE} height={GATE_SIZE}>
        <div
          className="workflow-gate-card niuu:flex niuu:h-full niuu:w-full niuu:select-none niuu:flex-col niuu:items-center niuu:justify-center niuu:overflow-hidden niuu:rounded-lg niuu:border niuu:p-1 niuu:text-center niuu:font-sans niuu:shadow-md"
          style={{
            background,
            borderColor,
            borderWidth: selected || issueLevel ? 2 : 1,
            borderTopColor: 'var(--node-accent)',
            borderTopWidth: 3,
          }}
        >
          <span
            className="niuu:mb-1 niuu:font-mono niuu:text-[9px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.16em]"
            style={{ color: 'var(--node-accent)' }}
          >
            Gate
          </span>
          <span
            className="niuu:text-[12px] niuu:font-semibold niuu:leading-tight"
            style={{ color: C.text }}
            title={node.label}
          >
            {title}
          </span>
        </div>
      </foreignObject>
      {selected && !isConnectingMode && !readOnly && (
        <DeleteButton nodeId={node.id} cx={x + GATE_SIZE / 2} cy={y - 10} onClick={onDelete} />
      )}
    </g>
  );
}

/** CondNode — same recipe, bounding box now a rounded card instead of a
 *  circle (matches the mockup's shape language: kind is read from the accent
 *  hue + eyebrow label, not from the outline shape). */
function CondNode({
  node,
  selected,
  issueLevel,
  onSelect,
  onInspect,
  onStartConnect: _onStartConnect,
  onCompleteConnect,
  onDelete,
  onDragPreview,
  onDragEnd,
  isConnectingMode,
  canvasScale,
  readOnly,
}: BaseNodeProps<WorkflowCondNode>) {
  const { x, y } = node.position;
  const { handleMouseDown, handleMouseMove, handleMouseUp } = useDragNode({
    x,
    y,
    isConnectingMode,
    onSelect,
    onCompleteConnect,
    onDragPreview,
    onDragEnd,
    canvasScale,
    readOnly,
  });
  const size = COND_RADIUS * 2;
  const background =
    issueLevel === 'error' ? C.errorFill : issueLevel === 'warning' ? C.warnFill : C.cond;
  const borderColor = selected
    ? C.nodeStrokeSelected
    : issueLevel === 'error'
      ? C.errorStroke
      : issueLevel === 'warning'
        ? C.warnStroke
        : C.condStroke;
  const title = node.label.length > 6 ? `${node.label.slice(0, 5)}…` : node.label;

  return (
    <g
      data-testid={`workflow-node-${node.id}`}
      data-kind="cond"
      data-selected={selected ? 'true' : undefined}
      role="button"
      tabIndex={0}
      aria-label={`Condition step: ${node.label}`}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onInspect();
        if (event.key === ' ') {
          event.preventDefault();
          onSelect();
        }
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={(e) => {
        e.preventDefault();
        onInspect();
      }}
      style={{ cursor: isConnectingMode ? 'crosshair' : 'grab' }}
    >
      <foreignObject x={x} y={y} width={size} height={size}>
        <div
          className="workflow-cond-card niuu:flex niuu:h-full niuu:w-full niuu:select-none niuu:flex-col niuu:items-center niuu:justify-center niuu:overflow-hidden niuu:rounded-lg niuu:border niuu:p-1 niuu:text-center niuu:font-sans niuu:shadow-md"
          style={{
            background,
            borderColor,
            borderWidth: selected || issueLevel ? 2 : 1,
            borderTopColor: 'var(--node-accent)',
            borderTopWidth: 3,
          }}
        >
          <span
            className="niuu:mb-1 niuu:font-mono niuu:text-[9px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.16em]"
            style={{ color: 'var(--node-accent)' }}
          >
            ?
          </span>
          <span
            className="niuu:text-[12px] niuu:font-semibold niuu:leading-tight"
            style={{ color: C.text }}
            title={node.label}
          >
            {title}
          </span>
        </div>
      </foreignObject>
      {selected && !isConnectingMode && !readOnly && (
        <DeleteButton nodeId={node.id} cx={x + COND_RADIUS} cy={y - 10} onClick={onDelete} />
      )}
    </g>
  );
}

/** Summarize a subworkflow node's offered templates for its card subtitle:
 *  the sole child workflow's alias when there is exactly one template, a
 *  short name list for a couple more, and a compact count once the list
 *  would no longer fit the card. */
function subworkflowTemplatesSummary(node: WorkflowSubworkflowNode): string {
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

/** TriggerNode — handles both `trigger` and `subworkflow` kinds. Same
 *  recipe; one output port (no input — a trigger/subworkflow root has
 *  nothing feeding it). */
function TriggerNode({
  node,
  selected,
  issueLevel,
  onSelect,
  onInspect,
  onCompleteConnect,
  onDelete,
  onDragPreview,
  onDragEnd,
  isConnectingMode,
  canvasScale,
  readOnly,
}: BaseNodeProps<WorkflowTriggerNode | WorkflowSubworkflowNode>) {
  const outputEvent =
    node.kind === 'subworkflow' ? 'children.completed' : (node.dispatchEvent ?? 'code.requested');
  const { x, y } = node.position;
  const { handleMouseDown, handleMouseMove, handleMouseUp } = useDragNode({
    x,
    y,
    isConnectingMode,
    onSelect,
    onCompleteConnect,
    onDragPreview,
    onDragEnd,
    canvasScale,
    readOnly,
  });
  const background = selected
    ? 'var(--color-bg-elevated)'
    : issueLevel === 'error'
      ? C.errorFill
      : issueLevel === 'warning'
        ? C.warnFill
        : 'color-mix(in srgb, var(--color-brand) 14%, var(--color-bg-secondary))';
  const borderColor = selected
    ? C.nodeStrokeSelected
    : issueLevel === 'error'
      ? C.errorStroke
      : issueLevel === 'warning'
        ? C.warnStroke
        : 'var(--color-brand)';
  const title = node.label.length > 20 ? `${node.label.slice(0, 18)}…` : node.label;
  const subtitle =
    node.kind === 'subworkflow'
      ? `1–${node.maxChildren} · ${subworkflowTemplatesSummary(node)}`
      : outputEvent;

  return (
    <g
      data-testid={`workflow-node-${node.id}`}
      data-kind={node.kind}
      data-selected={selected ? 'true' : undefined}
      role="button"
      tabIndex={0}
      aria-label={`${node.kind === 'trigger' ? 'Trigger' : 'Subworkflow'} step: ${node.label}`}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onInspect();
        if (event.key === ' ') {
          event.preventDefault();
          onSelect();
        }
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={(e) => {
        e.preventDefault();
        onInspect();
      }}
      style={{ cursor: isConnectingMode ? 'crosshair' : 'grab' }}
    >
      <foreignObject x={x} y={y} width={TRIGGER_WIDTH} height={TRIGGER_HEIGHT}>
        <div
          className="workflow-trigger-card niuu:h-full niuu:w-full niuu:select-none niuu:overflow-hidden niuu:rounded-lg niuu:border niuu:font-sans niuu:shadow-md"
          style={{
            background,
            borderColor,
            borderWidth: selected || issueLevel ? 2 : 1,
            borderLeftColor: 'var(--node-accent)',
            borderLeftWidth: 3,
          }}
        >
          <div className="niuu:flex niuu:h-full niuu:flex-col niuu:justify-center niuu:overflow-hidden niuu:pl-[13px] niuu:pr-[10px]">
            <div
              className="niuu:truncate niuu:text-[13px] niuu:font-semibold niuu:leading-tight"
              style={{ color: C.text }}
              title={node.label}
            >
              {title}
            </div>
            <div
              className="niuu:truncate niuu:font-mono niuu:text-[10px]"
              style={{ color: C.textMuted }}
            >
              {subtitle}
            </div>
          </div>
        </div>
      </foreignObject>
      {selected && !isConnectingMode && !readOnly && (
        <DeleteButton nodeId={node.id} cx={x + TRIGGER_WIDTH / 2} cy={y - 10} onClick={onDelete} />
      )}
    </g>
  );
}

export function waitPortLists(nodeId: string, edges: WorkflowEdge[]) {
  const inputs = edges
    .filter((edge) => edge.target === nodeId)
    .map((edge) => parseWorkflowEdgeLabel(edge.label)?.targetEventType)
    .filter((eventType): eventType is string => Boolean(eventType));
  const outputs = edges
    .filter((edge) => edge.source === nodeId)
    .map((edge) => parseWorkflowEdgeLabel(edge.label)?.sourceEventType)
    .filter((eventType): eventType is string => Boolean(eventType));
  return {
    incomingEvents: [...new Set(inputs)],
    outgoingEvents: [...new Set(outputs)],
  };
}

function requestWaitEventType(direction: 'incoming' | 'continuation', suggested = '') {
  return window
    .prompt(
      direction === 'incoming'
        ? 'Incoming event type for this wait'
        : 'Continuation event type emitted after this wait',
      suggested,
    )
    ?.trim();
}

/**
 * An `include` card has no single id an edge can use — real edges name one
 * of its provided local ids instead (see `providedNodeIds`). Resolve which
 * one a connect gesture on the card means: reuse the provided id that
 * already carries this exact event on this side, fall back to the sole
 * provided id when there's only one, or ask when several are equally
 * plausible. Returns the node's own id unchanged for every other kind.
 */
function resolveIncludeConnectionId(
  node: WorkflowNode,
  eventType: string,
  direction: 'source' | 'target',
  edges: readonly WorkflowEdge[],
): string | null {
  return resolveIncludeEndpoint(node, eventType, direction, edges, (event, providedIds) =>
    window.prompt(
      `Which included node does "${event}" belong to? (${providedIds.join(', ')})`,
      providedIds[0],
    ),
  );
}

/** WaitNode — same recipe as StageNode's port footer (bottom-anchored,
 *  index * 14px rows) but ports run the full card height since a wait node
 *  has no members block above them. Every testid, `data-event-type`, and the
 *  stopPropagation-on-mousedown-before-click contract (so clicking a port
 *  doesn't also start a node drag) are preserved exactly. */
function WaitNode({
  node,
  selected,
  issueLevel,
  onSelect,
  onInspect,
  onStartConnect,
  onCompleteConnect,
  onDelete,
  onDragPreview,
  onDragEnd,
  isConnectingMode,
  canvasScale,
  readOnly,
  incomingEvents,
  outgoingEvents,
  connectingFromLabel,
}: BaseNodeProps<WorkflowWaitNode> & {
  incomingEvents: string[];
  outgoingEvents: string[];
  connectingFromLabel?: string | null;
}) {
  const { x, y } = node.position;
  const portRows = Math.max(incomingEvents.length, outgoingEvents.length);
  const waitHeight = WAIT_HEIGHT + (portRows > 0 ? 16 + portRows * 14 : 0);
  const completeUnconfiguredConnection = () => {
    if (incomingEvents.length > 0) return;
    const selectedEvent = requestWaitEventType('incoming', connectingFromLabel ?? undefined);
    if (selectedEvent) onCompleteConnect(selectedEvent);
  };
  const { handleMouseDown, handleMouseMove, handleMouseUp } = useDragNode({
    x,
    y,
    isConnectingMode,
    onSelect,
    onCompleteConnect: completeUnconfiguredConnection,
    onDragPreview,
    onDragEnd,
    canvasScale,
    readOnly,
  });
  const background = selected
    ? 'var(--color-bg-elevated)'
    : issueLevel === 'error'
      ? C.errorFill
      : issueLevel === 'warning'
        ? C.warnFill
        : 'color-mix(in srgb, var(--color-accent-amber) 12%, var(--color-bg-secondary))';
  const borderColor = selected
    ? C.nodeStrokeSelected
    : issueLevel === 'error'
      ? C.errorStroke
      : issueLevel === 'warning'
        ? C.warnStroke
        : 'var(--color-accent-amber)';
  const title = node.label.length > 20 ? `${node.label.slice(0, 18)}…` : node.label;

  return (
    <g
      data-testid={`workflow-node-${node.id}`}
      data-kind="wait"
      data-selected={selected ? 'true' : undefined}
      role="button"
      tabIndex={0}
      aria-label={`Wait step: ${node.label}`}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onInspect();
        if (event.key === ' ') {
          event.preventDefault();
          onSelect();
        }
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={(e) => {
        e.preventDefault();
        onInspect();
      }}
      style={{ cursor: isConnectingMode ? 'crosshair' : 'grab' }}
    >
      <foreignObject x={x} y={y} width={WAIT_WIDTH} height={waitHeight}>
        <div
          className="workflow-wait-card niuu:h-full niuu:w-full niuu:select-none niuu:overflow-hidden niuu:rounded-lg niuu:border niuu:border-dashed niuu:font-sans niuu:shadow-md"
          style={{
            background,
            borderColor,
            borderWidth: selected || issueLevel ? 2 : 1,
            borderLeftColor: 'var(--node-accent)',
            borderLeftWidth: 3,
          }}
        >
          <div
            className="niuu:overflow-hidden niuu:pl-[13px] niuu:pr-[10px] niuu:pt-[7px]"
            style={{ height: WAIT_HEIGHT }}
          >
            <div
              className="niuu:truncate niuu:text-[13px] niuu:font-semibold niuu:leading-tight"
              style={{ color: C.text }}
              title={node.label}
            >
              {title}
            </div>
            <div
              className="niuu:truncate niuu:font-mono niuu:text-[10px]"
              style={{ color: C.textMuted }}
            >
              {portRows > 0
                ? `PASSIVE · ${incomingEvents.length} IN · ${outgoingEvents.length} OUT`
                : 'PASSIVE · CONFIGURE EVENTS'}
            </div>
          </div>

          {portRows > 0 && (
            <div
              hidden
              aria-hidden="true"
              className="niuu:absolute niuu:inset-x-0 niuu:bottom-0 niuu:border-t niuu:border-dashed"
              style={{ height: portRows * 14, borderColor: 'var(--color-border-subtle)' }}
            >
              {incomingEvents.map((eventType, index) => (
                <div
                  key={`in-${eventType}`}
                  className="niuu:absolute niuu:left-0 niuu:flex niuu:items-center niuu:gap-[4px] niuu:pl-[6px]"
                  style={{ top: index * 14, height: 14 }}
                >
                  {isConnectingMode && (
                    <button
                      type="button"
                      data-testid={`wait-input-${node.id}${index === 0 ? '' : `-${index}`}`}
                      data-event-type={eventType}
                      onMouseDown={(e) => e.stopPropagation()}
                      onClick={(e) => {
                        e.stopPropagation();
                        onCompleteConnect(eventType);
                      }}
                      className="niuu:h-[8px] niuu:w-[8px] niuu:shrink-0 niuu:cursor-pointer niuu:rounded-full niuu:border niuu:p-0"
                      style={{
                        borderColor: 'var(--color-brand)',
                        background: 'var(--color-bg-primary)',
                      }}
                      aria-label={`Connect input ${eventType}`}
                    />
                  )}
                  <span
                    className="niuu:select-none niuu:truncate niuu:font-mono niuu:text-[6.5px]"
                    style={{ color: C.textMuted, maxWidth: WAIT_WIDTH / 2 - 20 }}
                  >
                    {eventType.length > 14 ? `${eventType.slice(0, 13)}…` : eventType}
                  </span>
                </div>
              ))}
              {outgoingEvents.map((eventType, index) => (
                <div
                  key={`out-${eventType}`}
                  className="niuu:absolute niuu:right-0 niuu:flex niuu:items-center niuu:gap-[4px] niuu:pr-[6px]"
                  style={{ top: index * 14, height: 14 }}
                >
                  <span
                    className="niuu:select-none niuu:truncate niuu:text-right niuu:font-mono niuu:text-[6.5px]"
                    style={{ color: C.text, maxWidth: WAIT_WIDTH / 2 - 20 }}
                  >
                    {eventType.length > 14 ? `${eventType.slice(0, 13)}…` : eventType}
                  </span>
                  <button
                    type="button"
                    data-testid={`wait-output-${node.id}${index === 0 ? '' : `-${index}`}`}
                    data-event-type={eventType}
                    onMouseDown={(e) => e.stopPropagation()}
                    onClick={(e) => {
                      e.stopPropagation();
                      onStartConnect(eventType);
                    }}
                    className="niuu:h-[8px] niuu:w-[8px] niuu:shrink-0 niuu:cursor-pointer niuu:rounded-full niuu:border niuu:p-0"
                    style={{
                      borderColor: 'var(--color-brand)',
                      background:
                        connectingFromLabel === eventType
                          ? 'var(--color-brand)'
                          : 'var(--color-bg-primary)',
                    }}
                    aria-label={`Connect output ${eventType}`}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      </foreignObject>
      {isConnectingMode && incomingEvents.length === 0 && (
        <circle
          data-testid={`wait-input-${node.id}`}
          cx={x}
          cy={y + WAIT_HEIGHT / 2}
          r={5}
          fill={C.bg}
          stroke={C.nodeStrokeSelected}
          strokeWidth={1.5}
          role="button"
          tabIndex={0}
          className="niuu:cursor-crosshair"
          aria-label="Connect incoming event"
          onMouseDown={(event) => event.stopPropagation()}
          onClick={(event) => {
            event.stopPropagation();
            completeUnconfiguredConnection();
          }}
          onKeyDown={(event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            event.stopPropagation();
            completeUnconfiguredConnection();
          }}
        />
      )}
      {outgoingEvents.length === 0 && !readOnly && (
        <circle
          data-testid={`wait-output-${node.id}`}
          cx={x + WAIT_WIDTH}
          cy={y + WAIT_HEIGHT / 2}
          r={5}
          fill={C.bg}
          stroke={C.nodeStrokeSelected}
          strokeWidth={1.5}
          role="button"
          tabIndex={0}
          className="niuu:cursor-crosshair"
          aria-label="Connect outgoing event"
          onMouseDown={(event) => event.stopPropagation()}
          onClick={(event) => {
            event.stopPropagation();
            const selectedEvent = requestWaitEventType('continuation');
            if (selectedEvent) onStartConnect(selectedEvent);
          }}
          onKeyDown={(event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            event.stopPropagation();
            const selectedEvent = requestWaitEventType('continuation');
            if (selectedEvent) onStartConnect(selectedEvent);
          }}
        />
      )}
      {selected && !isConnectingMode && !readOnly && (
        <DeleteButton nodeId={node.id} cx={x + WAIT_WIDTH / 2} cy={y - 10} onClick={onDelete} />
      )}
    </g>
  );
}

/** EndNode — same recipe; keeps its existing emerald "complete" semantic
 *  (distinct from the other accent hues, since green-for-done is its own
 *  well-established convention, not a kind-identity color). */
function EndNode({
  node,
  selected,
  issueLevel,
  onSelect,
  onInspect,
  onStartConnect: _onStartConnect,
  onCompleteConnect,
  onDelete,
  onDragPreview,
  onDragEnd,
  isConnectingMode,
  canvasScale,
  readOnly,
}: BaseNodeProps<WorkflowEndNode>) {
  const { x, y } = node.position;
  const { handleMouseDown, handleMouseMove, handleMouseUp } = useDragNode({
    x,
    y,
    isConnectingMode,
    onSelect,
    onCompleteConnect,
    onDragPreview,
    onDragEnd,
    canvasScale,
    readOnly,
  });
  const background = selected
    ? 'var(--color-bg-elevated)'
    : issueLevel === 'error'
      ? C.errorFill
      : issueLevel === 'warning'
        ? C.warnFill
        : 'color-mix(in srgb, var(--status-emerald) 14%, var(--color-bg-secondary))';
  const borderColor = selected
    ? C.nodeStrokeSelected
    : issueLevel === 'error'
      ? C.errorStroke
      : issueLevel === 'warning'
        ? C.warnStroke
        : 'var(--status-emerald)';
  const title = node.label.length > 10 ? `${node.label.slice(0, 8)}…` : node.label;

  return (
    <g
      data-testid={`workflow-node-${node.id}`}
      data-kind="end"
      data-selected={selected ? 'true' : undefined}
      role="button"
      tabIndex={0}
      aria-label={`End step: ${node.label}`}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onInspect();
        if (event.key === ' ') {
          event.preventDefault();
          onSelect();
        }
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={(e) => {
        e.preventDefault();
        onInspect();
      }}
      style={{ cursor: isConnectingMode ? 'crosshair' : 'grab' }}
    >
      <foreignObject x={x} y={y} width={END_RADIUS * 2} height={END_RADIUS * 2}>
        <div
          className="workflow-end-card niuu:flex niuu:h-full niuu:w-full niuu:select-none niuu:flex-col niuu:items-center niuu:justify-center niuu:overflow-hidden niuu:rounded-full niuu:border niuu:text-center niuu:font-sans niuu:shadow-md"
          style={{ background, borderColor, borderWidth: selected || issueLevel ? 2 : 1 }}
        >
          <span className="niuu:leading-none" style={{ color: C.text, fontSize: 16 }}>
            ●
          </span>
          <span
            className="niuu:mt-0.5 niuu:font-mono niuu:text-[9.5px]"
            style={{ color: C.textMuted }}
            title={node.label}
          >
            {title}
          </span>
        </div>
      </foreignObject>
      {selected && !isConnectingMode && !readOnly && (
        <DeleteButton nodeId={node.id} cx={x + END_RADIUS} cy={y - 10} onClick={onDelete} />
      )}
    </g>
  );
}

/** ResourceNode — was a cylinder (ellipse/rect/ellipse); same recipe as
 *  every other kind now: a rounded card, no shape-language special case. */
function ResourceNode({
  node,
  selected,
  issueLevel,
  onSelect,
  onInspect,
  onStartConnect: _onStartConnect,
  onCompleteConnect,
  onDelete,
  onDragPreview,
  onDragEnd,
  isConnectingMode,
  canvasScale,
  readOnly,
}: BaseNodeProps<WorkflowResourceNode>) {
  const { x, y } = node.position;
  const { handleMouseDown, handleMouseMove, handleMouseUp } = useDragNode({
    x,
    y,
    isConnectingMode,
    onSelect,
    onCompleteConnect,
    onDragPreview,
    onDragEnd,
    canvasScale,
    readOnly,
  });
  const background = selected
    ? 'var(--color-bg-elevated)'
    : issueLevel === 'error'
      ? C.errorFill
      : issueLevel === 'warning'
        ? C.warnFill
        : 'color-mix(in srgb, var(--color-brand) 16%, var(--color-bg-secondary))';
  const borderColor = selected
    ? C.nodeStrokeSelected
    : issueLevel === 'error'
      ? C.errorStroke
      : issueLevel === 'warning'
        ? C.warnStroke
        : 'var(--color-brand)';
  const modeLabel = node.bindingMode === 'ephemeral_local' ? 'EPHEMERAL' : 'REGISTRY';
  const modeStroke =
    node.bindingMode === 'ephemeral_local' ? 'var(--status-emerald)' : 'var(--color-brand)';
  const modeFill =
    node.bindingMode === 'ephemeral_local'
      ? 'color-mix(in srgb, var(--status-emerald) 14%, var(--color-bg-primary))'
      : 'color-mix(in srgb, var(--color-brand) 14%, var(--color-bg-primary))';
  const title = node.label.length > 20 ? `${node.label.slice(0, 18)}…` : node.label;

  return (
    <g
      data-testid={`workflow-node-${node.id}`}
      data-kind="resource"
      data-selected={selected ? 'true' : undefined}
      role="button"
      tabIndex={0}
      aria-label={`Resource: ${node.label}`}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onInspect();
        if (event.key === ' ') {
          event.preventDefault();
          onSelect();
        }
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={(e) => {
        e.preventDefault();
        onInspect();
      }}
      style={{ cursor: isConnectingMode ? 'crosshair' : 'grab' }}
    >
      <foreignObject x={x} y={y} width={RESOURCE_WIDTH} height={RESOURCE_HEIGHT}>
        <div
          className="workflow-resource-card niuu:h-full niuu:w-full niuu:select-none niuu:overflow-hidden niuu:rounded-lg niuu:border niuu:border-dashed niuu:font-sans niuu:shadow-md"
          style={{
            background,
            borderColor,
            borderWidth: selected || issueLevel ? 2 : 1,
            borderLeftColor: 'var(--node-accent)',
            borderLeftWidth: 3,
          }}
        >
          <div className="niuu:flex niuu:h-full niuu:flex-col niuu:justify-center niuu:gap-[3px] niuu:overflow-hidden niuu:pl-[13px] niuu:pr-[10px]">
            <div
              className="niuu:truncate niuu:text-[13px] niuu:font-semibold niuu:leading-tight"
              style={{ color: C.text }}
              title={node.label}
            >
              {title}
            </div>
            <div
              className="niuu:truncate niuu:font-mono niuu:text-[10px]"
              style={{ color: C.textMuted }}
            >
              RESOURCE · MIMIR
            </div>
            <span
              className="niuu:w-fit niuu:rounded-full niuu:border niuu:px-[8px] niuu:py-[1px] niuu:font-mono niuu:text-[7.5px] niuu:font-semibold"
              style={{ background: modeFill, borderColor: modeStroke, color: C.text }}
            >
              {modeLabel}
            </span>
          </div>
        </div>
      </foreignObject>
      {selected && !isConnectingMode && !readOnly && (
        <DeleteButton nodeId={node.id} cx={x + RESOURCE_WIDTH / 2} cy={y - 10} onClick={onDelete} />
      )}
    </g>
  );
}

/** IncludeNode — same card recipe as ResourceNode; shows the pinned
 *  workflow alias and how many of its nodes this one inlines. Like
 *  Gate/Cond/Subworkflow, the card carries no inline port markup of its
 *  own — every socket is edge-derived (see `workflowPorts.ts`) and drawn
 *  generically by `NodeSocketLayer`, which grows the card's measured height
 *  with `portRows` the same way a Wait card does. */
function IncludeNode({
  node,
  selected,
  issueLevel,
  onSelect,
  onInspect,
  onStartConnect: _onStartConnect,
  onCompleteConnect,
  onDelete,
  onDragPreview,
  onDragEnd,
  isConnectingMode,
  canvasScale,
  readOnly,
  portRows,
}: BaseNodeProps<WorkflowIncludeNode> & { portRows: number }) {
  const { x, y } = node.position;
  const height = INCLUDE_HEIGHT + (portRows > 0 ? 16 + portRows * 14 : 0);
  const { handleMouseDown, handleMouseMove, handleMouseUp } = useDragNode({
    x,
    y,
    isConnectingMode,
    onSelect,
    onCompleteConnect,
    onDragPreview,
    onDragEnd,
    canvasScale,
    readOnly,
  });
  const background = selected
    ? 'var(--color-bg-elevated)'
    : issueLevel === 'error'
      ? C.errorFill
      : issueLevel === 'warning'
        ? C.warnFill
        : 'color-mix(in srgb, var(--color-accent-violet) 16%, var(--color-bg-secondary))';
  const borderColor = selected
    ? C.nodeStrokeSelected
    : issueLevel === 'error'
      ? C.errorStroke
      : issueLevel === 'warning'
        ? C.warnStroke
        : 'var(--color-accent-violet)';
  const title = node.label.length > 20 ? `${node.label.slice(0, 18)}…` : node.label;
  const nodeCount = Object.keys(node.nodes ?? {}).length;
  const subtitle = `${node.workflow || 'choose a workflow'} · ${nodeCount} node${nodeCount === 1 ? '' : 's'}`;

  return (
    <g
      data-testid={`workflow-node-${node.id}`}
      data-kind="include"
      data-selected={selected ? 'true' : undefined}
      role="button"
      tabIndex={0}
      aria-label={`Include step: ${node.label}`}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onInspect();
        if (event.key === ' ') {
          event.preventDefault();
          onSelect();
        }
      }}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={(e) => {
        e.preventDefault();
        onInspect();
      }}
      style={{ cursor: isConnectingMode ? 'crosshair' : 'grab' }}
    >
      <foreignObject x={x} y={y} width={INCLUDE_WIDTH} height={height}>
        <div
          className="workflow-include-card niuu:h-full niuu:w-full niuu:select-none niuu:overflow-hidden niuu:rounded-lg niuu:border niuu:font-sans niuu:shadow-md"
          style={{
            background,
            borderColor,
            borderWidth: selected || issueLevel ? 2 : 1,
            borderLeftColor: 'var(--node-accent)',
            borderLeftWidth: 3,
          }}
        >
          <div
            className="niuu:flex niuu:flex-col niuu:justify-center niuu:gap-[3px] niuu:overflow-hidden niuu:pl-[13px] niuu:pr-[10px]"
            style={{ height: INCLUDE_HEIGHT }}
          >
            <div
              className="niuu:truncate niuu:text-[13px] niuu:font-semibold niuu:leading-tight"
              style={{ color: C.text }}
              title={node.label}
            >
              {title}
            </div>
            <div
              className="niuu:truncate niuu:font-mono niuu:text-[10px]"
              style={{ color: C.textMuted }}
              title={subtitle}
            >
              {subtitle}
            </div>
          </div>
        </div>
      </foreignObject>
      {selected && !isConnectingMode && !readOnly && (
        <DeleteButton nodeId={node.id} cx={x + INCLUDE_WIDTH / 2} cy={y - 10} onClick={onDelete} />
      )}
    </g>
  );
}

export function stagePortLists(node: WorkflowStageNode, personas: PersonaEntry[]) {
  const stageMembers = normalizedStageMembers(node);
  const personaMap = new Map(personas.map((persona) => [persona.id, persona]));
  return {
    knownInputs: [
      ...new Set(
        stageMembers.flatMap((member) => personaMap.get(member.personaId)?.consumes ?? []),
      ),
    ],
    knownOutputs: [
      ...new Set(
        stageMembers.flatMap((member) => personaMap.get(member.personaId)?.produces ?? []),
      ),
    ],
  };
}

export function renderedStageHeight(node: WorkflowStageNode, personas: PersonaEntry[]) {
  const { knownInputs, knownOutputs } = stagePortLists(node, personas);
  const portRows = Math.max(knownInputs.length, knownOutputs.length, 0);
  return stageNodeHeight(node) + (portRows > 0 ? 22 + portRows * 14 : 0);
}

export function splitEdgePorts(label?: string) {
  if (!label) return { sourcePort: null, targetPort: null };
  const parsed = parseWorkflowEdgeLabel(label);
  if (parsed) {
    return {
      sourcePort: parsed.sourceEventType,
      targetPort: parsed.targetEventType,
    };
  }
  const [rawSourcePort, rawTargetPort] = label.split(' -> ', 2);
  if (rawTargetPort !== undefined) {
    return {
      sourcePort: rawSourcePort ?? null,
      targetPort: rawTargetPort,
    };
  }
  return { sourcePort: label, targetPort: label };
}

export function visibleNodePortCatalog(
  node: WorkflowNode,
  personas: PersonaEntry[],
  edges: WorkflowEdge[],
  expanded: boolean,
): WorkflowNodePortCatalog {
  const catalog = nodePortCatalog(node, { personas, edges });
  if (expanded) return catalog;
  const connectedInputs = new Set<string>();
  const connectedOutputs = new Set<string>();
  const matchIds = nodeMatchIds(node);
  for (const edge of edges) {
    const isSource = matchIds.has(edge.source);
    const isTarget = matchIds.has(edge.target);
    if (!isSource && !isTarget) continue;
    const parsed = parseWorkflowEdgeLabel(edge.label);
    if (isSource) {
      connectedOutputs.add(parsed?.sourceEventType ?? `unresolved-edge:${edge.id}`);
    }
    if (isTarget) {
      connectedInputs.add(parsed?.targetEventType ?? `unresolved-edge:${edge.id}`);
    }
  }
  const inputs = catalog.inputs.filter((port) => connectedInputs.has(port.eventType));
  const outputs = catalog.outputs.filter((port) => connectedOutputs.has(port.eventType));
  return {
    inputs: inputs.map((port, index) => ({ ...port, index, count: inputs.length })),
    outputs: outputs.map((port, index) => ({ ...port, index, count: outputs.length })),
  };
}

export function buildIssueLevelMap(issues?: WorkflowIssue[] | null) {
  const levels = new Map<string, 'error' | 'warning'>();
  for (const issue of Array.isArray(issues) ? issues : []) {
    if (!issue.nodeId) continue;
    const existing = levels.get(issue.nodeId);
    if (issue.severity === 'error' || !existing) {
      levels.set(issue.nodeId, issue.severity);
    }
  }
  return levels;
}

export function isGraphNodeKind(nodeKind: string): nodeKind is WorkflowNode['kind'] {
  return (
    nodeKind === 'trigger' ||
    nodeKind === 'stage' ||
    nodeKind === 'resource' ||
    nodeKind === 'gate' ||
    nodeKind === 'cond' ||
    nodeKind === 'wait' ||
    nodeKind === 'subworkflow' ||
    nodeKind === 'include' ||
    nodeKind === 'end'
  );
}

export function edgeAnchor(
  node: WorkflowNode,
  direction: 'source' | 'target',
  portLabel: string | null,
  personas: PersonaEntry[],
  edges: WorkflowEdge[] = [],
) {
  if (!portLabel) return null;
  const catalog = nodePortCatalog(node, { personas, edges });
  const ports = direction === 'source' ? catalog.outputs : catalog.inputs;
  const port = ports.find((candidate) => candidate.eventType === portLabel);
  if (!port) return null;
  const measurements = new Map<string, WorkflowSize>();
  if (node.kind === 'stage') {
    measurements.set(node.id, {
      width: STAGE_WIDTH,
      height: renderedStageHeight(node, personas),
      portTop: stageNodeHeight(node) + 22,
    });
  }
  if (node.kind === 'wait') {
    const rows = Math.max(catalog.inputs.length, catalog.outputs.length);
    measurements.set(node.id, {
      width: WAIT_WIDTH,
      height: WAIT_HEIGHT + (rows > 0 ? 16 + rows * 14 : 0),
      portTop: rows > 0 ? WAIT_HEIGHT + 16 : 0,
    });
  }
  return portAnchor(node, port, { measurements });
}

function WorkflowEdgePath({
  edge,
  edges,
  nodes,
  personas,
  portCatalogs,
  measurements,
  feedbackLane,
  selected,
  onSelect,
}: {
  edge: WorkflowEdge;
  edges: WorkflowEdge[];
  nodes: Map<string, WorkflowNode>;
  personas: PersonaEntry[];
  portCatalogs: ReadonlyMap<string, WorkflowNodePortCatalog>;
  measurements: ReadonlyMap<string, WorkflowSize>;
  feedbackLane: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const src = nodes.get(edge.source);
  const tgt = nodes.get(edge.target);
  if (!src || !tgt) return null;
  const resolution = resolveWorkflowEdgePorts(edge, nodes, { personas, edges });
  if (!resolution.sourcePort || !resolution.targetPort) return null;
  const sourcePort = portCatalogs
    .get(src.id)
    ?.outputs.find((port) => port.eventType === resolution.sourcePort?.eventType);
  const targetPort = portCatalogs
    .get(tgt.id)
    ?.inputs.find((port) => port.eventType === resolution.targetPort?.eventType);
  if (!sourcePort || !targetPort) return null;
  const srcC = portAnchor(src, sourcePort, { measurements });
  const tgtC = portAnchor(tgt, targetPort, { measurements });
  const feedback = isReentryEdge(edge);
  const maxBottom = Math.max(
    ...[...nodes.values()].map((node) => {
      const bounds = nodeBounds(node, { measurements });
      return bounds.y + bounds.height;
    }),
  );
  const laneY = maxBottom + 44 + feedbackLane * 18;
  const forwardDirection = tgtC.x >= srcC.x ? 1 : -1;
  const forwardBend = Math.max(24, Math.min(80, Math.abs(tgtC.x - srcC.x) * 0.4));
  const d = feedback
    ? `M ${srcC.x} ${srcC.y} L ${srcC.x + 20} ${srcC.y} L ${srcC.x + 20} ${laneY} L ${tgtC.x - 20} ${laneY} L ${tgtC.x - 20} ${tgtC.y} L ${tgtC.x} ${tgtC.y}`
    : `M ${srcC.x} ${srcC.y} C ${srcC.x + forwardDirection * forwardBend} ${srcC.y}, ${tgtC.x - forwardDirection * forwardBend} ${tgtC.y}, ${tgtC.x} ${tgtC.y}`;
  return (
    <g
      data-testid={`workflow-edge-${edge.id}`}
      data-source-node={edge.source}
      data-target-node={edge.target}
      data-source-event={sourcePort.eventType}
      data-target-event={targetPort.eventType}
      data-resolved={resolution.resolved ? 'true' : 'false'}
      data-feedback={feedback ? 'true' : 'false'}
    >
      <path
        d={d}
        fill="none"
        stroke="transparent"
        strokeWidth={10}
        onClick={(e) => {
          e.stopPropagation();
          onSelect();
        }}
        className="niuu:cursor-pointer"
      />
      <path
        d={d}
        fill="none"
        stroke={selected ? C.nodeStrokeSelected : feedback ? C.warnStroke : C.edgeStroke}
        strokeWidth={selected ? 2.25 : 1.5}
        strokeDasharray={feedback ? '5 4' : undefined}
        markerEnd="url(#arrowhead)"
        onClick={(e) => {
          e.stopPropagation();
          onSelect();
        }}
        className="niuu:cursor-pointer"
      />
      {selected &&
        edge.label &&
        (() => {
          return (
            <text
              x={(srcC.x + tgtC.x) / 2}
              y={(srcC.y + tgtC.y) / 2 - 8}
              textAnchor="middle"
              fill={C.textMuted}
              fontSize={10}
              fontFamily="var(--font-sans)"
            >
              {edge.label}
            </text>
          );
        })()}
    </g>
  );
}

function NodeSocketLayer({
  node,
  portCatalog,
  measurements,
  selected,
  hiddenOutcomeCount,
  connecting,
  readOnly,
  connectingFromLabel,
  onStartConnect,
  onCompleteConnect,
  onReveal,
}: {
  node: WorkflowNode;
  portCatalog: WorkflowNodePortCatalog;
  measurements: ReadonlyMap<string, WorkflowSize>;
  selected: boolean;
  hiddenOutcomeCount: number;
  connecting: boolean;
  readOnly: boolean;
  connectingFromLabel?: string | null;
  onStartConnect: (eventType: string) => void;
  onCompleteConnect: (eventType: string) => void;
  onReveal: () => void;
}) {
  const ports = [...portCatalog.inputs, ...portCatalog.outputs];

  function activate(port: WorkflowNodePort) {
    if (readOnly) return;
    if (port.direction === 'output') {
      onStartConnect(port.eventType);
      return;
    }
    if (connecting) onCompleteConnect(port.eventType);
  }

  const bounds = nodeBounds(node, { measurements });
  return (
    <g data-testid={`workflow-sockets-${node.id}`}>
      {ports.map((port) => {
        const anchor = portAnchor(node, port, { measurements });
        const active =
          port.direction === 'output' && connectingFromLabel === port.eventType && connecting;
        const interactive = !readOnly && (port.direction === 'output' || connecting);
        const showsTypedRows =
          node.kind === 'stage' || node.kind === 'wait' || node.kind === 'include';
        return (
          <g key={port.id}>
            <circle
              data-workflow-socket="true"
              data-testid={`workflow-socket-${node.id}-${port.direction}-${port.index}`}
              data-node-id={node.id}
              data-direction={port.direction}
              data-event-type={port.eventType}
              data-resolved={port.resolved ? 'true' : 'false'}
              data-active={active ? 'true' : 'false'}
              cx={anchor.x}
              cy={anchor.y}
              r={5}
              fill={active ? C.nodeStrokeSelected : C.bg}
              stroke={port.resolved ? C.nodeStrokeSelected : C.errorStroke}
              strokeWidth={1.5}
              role={interactive ? 'button' : undefined}
              tabIndex={interactive ? 0 : -1}
              aria-label={`${node.label}: ${port.label} ${port.direction}`}
              className={interactive ? 'niuu:cursor-crosshair' : undefined}
              onClick={(event) => {
                event.stopPropagation();
                activate(port);
              }}
              onKeyDown={(event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return;
                event.preventDefault();
                event.stopPropagation();
                activate(port);
              }}
            >
              <title>{`${port.label} · ${port.eventType}${port.resolved ? '' : ' · unresolved'}`}</title>
            </circle>
            {showsTypedRows && (
              <text
                x={anchor.x + (port.direction === 'input' ? 10 : -10)}
                y={anchor.y - 7}
                textAnchor={port.direction === 'input' ? 'start' : 'end'}
                fill={port.resolved ? C.textMuted : C.errorStroke}
                fontSize={selected ? 10.5 : 10}
                fontFamily="var(--font-mono)"
                className="niuu:pointer-events-none"
              >
                {port.label}
              </text>
            )}
          </g>
        );
      })}
      {hiddenOutcomeCount > 0 && (
        <g
          data-testid={`hidden-outcomes-${node.id}`}
          role="button"
          tabIndex={0}
          aria-label={`Show ${hiddenOutcomeCount} more outcomes for ${node.label}`}
          className="niuu:cursor-pointer"
          onClick={(event) => {
            event.stopPropagation();
            onReveal();
          }}
          onKeyDown={(event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            onReveal();
          }}
        >
          <rect
            x={bounds.x + bounds.width - 76}
            y={bounds.y - 20}
            width={68}
            height={16}
            rx={8}
            fill={C.nodeFillConnecting}
            stroke={C.nodeStroke}
          />
          <text
            x={bounds.x + bounds.width - 42}
            y={bounds.y - 9}
            textAnchor="middle"
            fill={C.textMuted}
            fontSize={9}
            fontFamily="var(--font-mono)"
            className="niuu:pointer-events-none"
          >
            +{hiddenOutcomeCount} outcomes
          </text>
        </g>
      )}
    </g>
  );
}

// ---------------------------------------------------------------------------
// InsertFromPortMenu — "add a step from a port"
// ---------------------------------------------------------------------------

const FLOW_CONTROL_CANDIDATES: {
  kind: 'gate' | 'cond' | 'wait' | 'end';
  label: string;
  hint: string;
}[] = [
  { kind: 'gate', label: 'Human gate', hint: 'hold for approval, then continue' },
  { kind: 'cond', label: 'Condition', hint: 'route on a predicate' },
  { kind: 'wait', label: 'External wait', hint: 'resume on an outside observation' },
  { kind: 'end', label: 'End', hint: 'terminate the workflow here' },
];

/**
 * Candidate list shown while connecting from a typed output port: personas
 * whose `consumes` actually includes `eventType` (the port's type IS the
 * filter, not a search box the user has to use), plus flow-control blocks,
 * which accept any type by construction. Ported from
 * docs/mockups/workflow-builder's "add step from a port" panel.
 *
 * Personas insert via `addStageWithPersona`'s produces/consumes auto-wire;
 * flow-control blocks have no such profile to match, so they wire via
 * `addNodeFromPort`, which takes the exact source + event type instead.
 */
function InsertFromPortMenu({
  personas,
  eventType,
  anchor,
  onInsertPersona,
  onInsertFlowControl,
}: {
  personas: PersonaEntry[];
  eventType: string;
  anchor: { x: number; y: number };
  onInsertPersona: (personaId: string) => void;
  onInsertFlowControl?: (kind: 'gate' | 'cond' | 'wait' | 'end') => void;
}) {
  const compatible = personas.filter((p) => (p.consumes ?? []).includes(eventType));

  return (
    <div
      data-testid="insert-from-port-menu"
      className="workflow-canvas__picker niuu:absolute niuu:z-30 niuu:w-[300px] niuu:overflow-hidden niuu:rounded-xl niuu:backdrop-blur-md"
      style={{ left: anchor.x, top: anchor.y }}
    >
      <div className="niuu:border-b niuu:border-border-subtle niuu:px-3 niuu:py-2">
        <div className="niuu:font-mono niuu:text-[9px] niuu:uppercase niuu:tracking-[0.18em] niuu:text-text-faint">
          Insert a step for
        </div>
        <div
          className="niuu:truncate niuu:font-mono niuu:text-[11px] niuu:text-brand"
          title={eventType}
        >
          {eventType}
        </div>
      </div>
      <div className="niuu:max-h-[280px] niuu:overflow-y-auto niuu:p-1.5">
        {compatible.length === 0 ? (
          <div className="niuu:px-2 niuu:py-2 niuu:text-[10.5px] niuu:text-text-muted">
            Nothing in the persona catalog consumes{' '}
            <span className="niuu:text-text-secondary">{eventType}</span>.
          </div>
        ) : (
          compatible.map((persona) => (
            <button
              key={persona.id}
              type="button"
              data-testid={`insert-from-port-${persona.id}`}
              onClick={() => onInsertPersona(persona.id)}
              className="niuu:flex niuu:w-full niuu:flex-col niuu:items-start niuu:gap-0.5 niuu:rounded-lg niuu:border niuu:border-transparent niuu:px-2.5 niuu:py-2 niuu:text-left niuu:hover:border-border-subtle niuu:hover:bg-bg-tertiary"
            >
              <span className="niuu:text-[11.5px] niuu:font-semibold niuu:text-text-primary">
                {persona.label}
              </span>
              {persona.produces && persona.produces.length > 0 && (
                <span className="niuu:font-mono niuu:text-[9px] niuu:text-text-faint">
                  → {persona.produces.join(', ')}
                </span>
              )}
            </button>
          ))
        )}

        {onInsertFlowControl && (
          <>
            <div className="niuu:mb-1 niuu:mt-2 niuu:px-2.5 niuu:font-mono niuu:text-[8.5px] niuu:uppercase niuu:tracking-[0.18em] niuu:text-text-faint">
              Flow control · accepts any type
            </div>
            {FLOW_CONTROL_CANDIDATES.map((candidate) => (
              <button
                key={candidate.kind}
                type="button"
                data-testid={`insert-from-port-flow-${candidate.kind}`}
                onClick={() => onInsertFlowControl(candidate.kind)}
                className="niuu:flex niuu:w-full niuu:flex-col niuu:items-start niuu:gap-0.5 niuu:rounded-lg niuu:border niuu:border-transparent niuu:px-2.5 niuu:py-2 niuu:text-left niuu:hover:border-border-subtle niuu:hover:bg-bg-tertiary"
              >
                <span className="niuu:text-[11.5px] niuu:font-semibold niuu:text-text-primary">
                  {candidate.label}
                </span>
                <span className="niuu:font-mono niuu:text-[9px] niuu:text-text-faint">
                  {candidate.hint}
                </span>
              </button>
            ))}
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// GraphView
// ---------------------------------------------------------------------------

export interface GraphViewProps {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  personas?: PersonaEntry[];
  registryMounts?: WorkflowRegistryMount[];
  issues?: WorkflowIssue[];
  selectedNodeId: string | null;
  connectingFromId: string | null;
  connectingFromLabel?: string | null;
  onSelectNode: WorkflowBuilderActions['selectNode'];
  onInspectNode: WorkflowBuilderActions['inspectNode'];
  onAddNode: WorkflowBuilderActions['addNode'];
  onAddMimirResource?: WorkflowBuilderActions['addMimirResource'];
  onDeleteNode: WorkflowBuilderActions['deleteNode'];
  onDeleteEdge: WorkflowBuilderActions['deleteEdge'];
  onMoveNode: WorkflowBuilderActions['moveNode'];
  onStartConnect: WorkflowBuilderActions['startConnect'];
  onCancelConnect: WorkflowBuilderActions['cancelConnect'];
  onCompleteConnect: WorkflowBuilderActions['completeConnect'];
  onAddPersonaToStage?: WorkflowBuilderActions['addPersonaToStage'];
  onAddStageWithPersona?: WorkflowBuilderActions['addStageWithPersona'];
  onAddStageFromPort?: WorkflowBuilderActions['addStageFromPort'];
  /** Powers the flow-control candidates in InsertFromPortMenu. Personas
   *  alone (onAddStageWithPersona) are enough to show the menu; this just
   *  adds the gate/cond/wait/end section to it. */
  onAddNodeFromPort?: WorkflowBuilderActions['addNodeFromPort'];
  /** Reposition every node via `layoutWorkflow`. Omit to hide the button. */
  onAutoLayout?: WorkflowBuilderActions['autoLayout'];
  /** System templates and other immutable workflows retain navigation and inspection. */
  readOnly?: boolean;
  /** Optional host action for making a read-only workflow editable. */
  onEdit?: () => void;
  readOnlyMessage?: string;
}

export function GraphView({
  nodes,
  edges,
  personas = [],
  registryMounts = [],
  issues = [],
  selectedNodeId,
  connectingFromId,
  connectingFromLabel,
  onSelectNode,
  onInspectNode,
  onAddNode,
  onAddMimirResource,
  onDeleteNode,
  onDeleteEdge,
  onMoveNode,
  onStartConnect,
  onCancelConnect,
  onCompleteConnect,
  onAddPersonaToStage,
  onAddStageWithPersona,
  onAddStageFromPort,
  onAddNodeFromPort,
  onAutoLayout,
  readOnly = false,
  onEdit,
  readOnlyMessage,
}: GraphViewProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [transform, setTransform] = useState({ x: 0, y: 0, scale: 1 });
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const pointerRef = useRef<{ x: number; y: number } | null>(null);
  const pickerRef = useRef<HTMLDivElement>(null);
  const [canvasPicker, setCanvasPicker] = useState<{
    x: number;
    y: number;
    width: number;
    height: number;
    position: { x: number; y: number };
  } | null>(null);
  const [isPanning, setIsPanning] = useState(false);
  const [dragPreview, setDragPreview] = useState<{
    nodeId: string;
    position: { x: number; y: number };
  } | null>(null);
  const panRef = useRef<{ startX: number; startY: number; tx: number; ty: number } | null>(null);

  const displayNodes = useMemo(
    () =>
      nodes.map((node) =>
        dragPreview?.nodeId === node.id ? { ...node, position: dragPreview.position } : node,
      ),
    [dragPreview, nodes],
  );
  // Provided ids (an include node's inlined local ids) aliased to their
  // owning include node — an edge whose source/target is such an id resolves
  // through this map to the include card itself, so edge rendering and
  // connect-completion both anchor on the one card that represents it.
  const nodeMap = useMemo(() => {
    const map = new Map<string, WorkflowNode>(displayNodes.map((n) => [n.id, n]));
    for (const [localId, includeNode] of providedNodeIds({ nodes: displayNodes })) {
      if (!map.has(localId)) map.set(localId, includeNode);
    }
    return map;
  }, [displayNodes]);
  const fullPortCatalogs = useMemo(
    () =>
      new Map(displayNodes.map((node) => [node.id, nodePortCatalog(node, { personas, edges })])),
    [displayNodes, edges, personas],
  );
  const visiblePortCatalogs = useMemo(
    () =>
      new Map(
        displayNodes.map((node) => [
          node.id,
          visibleNodePortCatalog(node, personas, edges, node.id === selectedNodeId),
        ]),
      ),
    [displayNodes, edges, personas, selectedNodeId],
  );
  const measurements = useMemo(() => {
    const sizes = new Map<string, WorkflowSize>();
    for (const node of displayNodes) {
      switch (node.kind) {
        case 'stage': {
          const stagePorts = visiblePortCatalogs.get(node.id);
          const stageRows = Math.max(
            stagePorts?.inputs.length ?? 0,
            stagePorts?.outputs.length ?? 0,
          );
          sizes.set(node.id, {
            width: STAGE_WIDTH,
            height: stageNodeHeight(node) + (stageRows > 0 ? 22 + stageRows * 14 : 0),
            portTop: stageNodeHeight(node) + 22,
          });
          break;
        }
        case 'gate':
          sizes.set(node.id, { width: GATE_SIZE, height: GATE_SIZE });
          break;
        case 'cond':
          sizes.set(node.id, { width: COND_RADIUS * 2, height: COND_RADIUS * 2 });
          break;
        case 'trigger':
        case 'subworkflow':
          sizes.set(node.id, { width: TRIGGER_WIDTH, height: TRIGGER_HEIGHT });
          break;
        case 'wait': {
          const ports = visiblePortCatalogs.get(node.id) ?? { inputs: [], outputs: [] };
          const rows = Math.max(ports.inputs.length, ports.outputs.length);
          sizes.set(node.id, {
            width: WAIT_WIDTH,
            height: WAIT_HEIGHT + (rows > 0 ? 16 + rows * 14 : 0),
            portTop: rows > 0 ? WAIT_HEIGHT + 16 : 0,
          });
          break;
        }
        case 'include': {
          const ports = visiblePortCatalogs.get(node.id) ?? { inputs: [], outputs: [] };
          const rows = Math.max(ports.inputs.length, ports.outputs.length);
          sizes.set(node.id, {
            width: INCLUDE_WIDTH,
            height: INCLUDE_HEIGHT + (rows > 0 ? 16 + rows * 14 : 0),
            portTop: rows > 0 ? INCLUDE_HEIGHT + 16 : 0,
          });
          break;
        }
        case 'end':
          sizes.set(node.id, { width: END_RADIUS * 2, height: END_RADIUS * 2 });
          break;
        case 'resource':
          sizes.set(node.id, { width: RESOURCE_WIDTH, height: RESOURCE_HEIGHT });
          break;
      }
    }
    return sizes;
  }, [displayNodes, visiblePortCatalogs]);
  const issueMap = useMemo(() => buildIssueLevelMap(issues), [issues]);
  const feedbackLanes = useMemo(() => feedbackLaneAssignments(edges), [edges]);
  const isConnectingMode = !readOnly && connectingFromId !== null;

  const zoomTo = useCallback((scale: number, clientPoint?: { x: number; y: number }) => {
    const svg = svgRef.current;
    setTransform((prev) => {
      const nextScale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, scale));
      if (!svg || !clientPoint) return { ...prev, scale: nextScale };
      const rect = svg.getBoundingClientRect();
      const localX = clientPoint.x - rect.left;
      const localY = clientPoint.y - rect.top;
      const canvasX = (localX - prev.x) / prev.scale;
      const canvasY = (localY - prev.y) / prev.scale;
      return {
        x: localX - canvasX * nextScale,
        y: localY - canvasY * nextScale,
        scale: nextScale,
      };
    });
  }, []);

  const fitToNodes = useCallback(
    (scaleLimit = MAX_ZOOM) => {
      const svg = svgRef.current;
      if (!svg || nodes.length === 0) {
        setTransform({ x: 0, y: 0, scale: 1 });
        return;
      }
      const rect = svg.getBoundingClientRect();
      const bounds = displayNodes.map((node) => nodeBounds(node, { measurements }));
      const minX = Math.min(...bounds.map((box) => box.x)) - 48;
      const maxX = Math.max(...bounds.map((box) => box.x + box.width)) + 48;
      const minY = Math.min(...bounds.map((box) => box.y)) - 48;
      const cardBottom = Math.max(...bounds.map((box) => box.y + box.height));
      const feedbackCount = feedbackLaneAssignments(edges).size;
      const maxY =
        (feedbackCount > 0 ? cardBottom + 44 + (feedbackCount - 1) * 18 : cardBottom) + 48;
      const width = Math.max(1, maxX - minX);
      const height = Math.max(1, maxY - minY);
      const scale = Math.min(
        scaleLimit,
        Math.max(MIN_ZOOM, Math.min(rect.width / width, rect.height / height) * 0.88),
      );
      setTransform({
        x: (rect.width - width * scale) / 2 - minX * scale,
        y: 52 - minY * scale,
        scale,
      });
    },
    [displayNodes, edges, measurements, nodes.length],
  );

  const resetOneToOne = useCallback(() => {
    const svg = svgRef.current;
    if (!svg || nodes.length === 0) {
      setTransform({ x: 0, y: 0, scale: 1 });
      return;
    }
    const rect = svg.getBoundingClientRect();
    const bounds = displayNodes.map((node) => nodeBounds(node, { measurements }));
    const minX = Math.min(...bounds.map((box) => box.x));
    const maxX = Math.max(...bounds.map((box) => box.x + box.width));
    const minY = Math.min(...bounds.map((box) => box.y));
    const maxY = Math.max(...bounds.map((box) => box.y + box.height));
    setTransform({
      x: rect.width / 2 - (minX + maxX) / 2,
      y: rect.height / 2 - (minY + maxY) / 2,
      scale: 1,
    });
  }, [displayNodes, measurements, nodes.length]);

  // Wheel zoom
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const canvas = el;
    function handleWheel(e: WheelEvent) {
      e.preventDefault();
      const currentScale = Number(canvas.dataset.scale ?? '1');
      zoomTo(currentScale * (e.deltaY < 0 ? 1.1 : 0.9), { x: e.clientX, y: e.clientY });
    }
    canvas.addEventListener('wheel', handleWheel, { passive: false });
    return () => canvas.removeEventListener('wheel', handleWheel);
  }, [zoomTo]);

  useEffect(() => {
    if (svgRef.current) svgRef.current.dataset.scale = String(transform.scale);
  }, [transform.scale]);

  const openCanvasPicker = useCallback(
    (point?: { x: number; y: number }) => {
      const bounds = rootRef.current!.getBoundingClientRect();
      const svgBounds = svgRef.current!.getBoundingClientRect();
      const anchor = point ??
        pointerRef.current ?? {
          x: bounds.left + bounds.width / 2,
          y: bounds.top + bounds.height / 2,
        };
      const width = Math.min(320, bounds.width - 24);
      const height = Math.min(readOnly ? 184 : 480, bounds.height - 24);
      setCanvasPicker({
        x: Math.max(12, Math.min(anchor.x - bounds.left, bounds.width - width - 12)),
        y: Math.max(12, Math.min(anchor.y - bounds.top, bounds.height - height - 12)),
        width,
        height,
        position: {
          x: (anchor.x - svgBounds.left - transform.x) / transform.scale,
          y: (anchor.y - svgBounds.top - transform.y) / transform.scale,
        },
      });
      onCancelConnect();
    },
    [onCancelConnect, readOnly, transform],
  );

  // Entering Edit gives the graph its keyboard shortcuts immediately.
  useEffect(() => {
    if (!readOnly) rootRef.current?.focus({ preventScroll: true });
  }, [readOnly]);

  // Key handler for Escape (cancel connect) and Delete (delete selected)
  useEffect(() => {
    if (!canvasPicker) return;
    function dismiss(event: PointerEvent) {
      if ((event.target as Element).closest('[data-testid="open-add-step"]')) return;
      if (!pickerRef.current?.contains(event.target as Node)) setCanvasPicker(null);
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      setCanvasPicker(null);
      rootRef.current?.focus();
    }
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', dismiss);
      document.removeEventListener('keydown', escape);
    };
  }, [canvasPicker]);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const editable =
        target?.isContentEditable ||
        target?.tagName === 'INPUT' ||
        target?.tagName === 'TEXTAREA' ||
        target?.tagName === 'SELECT';
      if (editable || !rootRef.current?.contains(document.activeElement)) return;
      if (e.key === 'Escape') onCancelConnect();
      if (
        !readOnly &&
        e.key === 'Tab' &&
        !e.shiftKey &&
        !e.ctrlKey &&
        !e.metaKey &&
        !e.altKey &&
        !canvasPicker &&
        (target === rootRef.current ||
          (target instanceof SVGElement && svgRef.current?.contains(target)))
      ) {
        e.preventDefault();
        openCanvasPicker();
      }
      if (!readOnly && (e.key === 'Delete' || e.key === 'Backspace') && selectedNodeId) {
        e.preventDefault();
        onDeleteNode(selectedNodeId);
      }
      if (!readOnly && (e.key === 'Delete' || e.key === 'Backspace') && selectedEdgeId) {
        e.preventDefault();
        onDeleteEdge(selectedEdgeId);
        setSelectedEdgeId(null);
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [
    onCancelConnect,
    onDeleteEdge,
    onDeleteNode,
    readOnly,
    selectedEdgeId,
    selectedNodeId,
    canvasPicker,
    openCanvasPicker,
  ]);

  function handleSvgMouseDown(e: React.MouseEvent<SVGSVGElement>) {
    if (e.button === 0 && e.ctrlKey) {
      handleCanvasContextMenu(e);
      return;
    }
    if (e.button === 2) return;
    if ((e.target as Element).closest('[data-testid^="workflow-node"]')) return;
    if ((e.target as Element).closest('[data-testid^="workflow-edge"]')) return;
    if (isConnectingMode) {
      onCancelConnect();
      return;
    }
    setSelectedEdgeId(null);
    onSelectNode(null);
    panRef.current = { startX: e.clientX, startY: e.clientY, tx: transform.x, ty: transform.y };
    setIsPanning(true);
  }

  function handleSvgMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    const pan = panRef.current;
    if (!pan) return;
    const { clientX, clientY } = e;
    setTransform((prev) => ({
      ...prev,
      x: pan.tx + (clientX - pan.startX),
      y: pan.ty + (clientY - pan.startY),
    }));
  }

  function handleSvgMouseUp() {
    panRef.current = null;
    setIsPanning(false);
  }

  function eventToCanvasPosition(clientX: number, clientY: number) {
    const svg = svgRef.current;
    if (!svg) return { x: 120, y: 120 };
    const rect = svg.getBoundingClientRect();
    return {
      x: (clientX - rect.left - transform.x) / transform.scale,
      y: (clientY - rect.top - transform.y) / transform.scale,
    };
  }

  function handleCanvasContextMenu(event: React.MouseEvent<SVGSVGElement>) {
    if (
      (event.target as Element).closest(
        '[data-testid^="workflow-node-"], [data-testid^="workflow-edge-"], [data-workflow-socket]',
      )
    )
      return;
    event.preventDefault();
    openCanvasPicker({ x: event.clientX, y: event.clientY });
  }

  function closeCanvasPicker() {
    setCanvasPicker(null);
    rootRef.current?.focus();
  }

  function findStageAtPoint(x: number, y: number) {
    return nodes.find(
      (node) =>
        node.kind === 'stage' &&
        x >= node.position.x &&
        x <= node.position.x + STAGE_WIDTH &&
        y >= node.position.y &&
        y <= node.position.y + (measurements.get(node.id)?.height ?? stageNodeHeight(node)),
    );
  }

  function handleDrop(e: React.DragEvent<SVGSVGElement>) {
    e.preventDefault();
    if (readOnly) return;
    const position = eventToCanvasPosition(e.clientX, e.clientY);
    const personaId = e.dataTransfer.getData('application/niuu-persona-id');
    const nodeKind = e.dataTransfer.getData('application/niuu-node-kind');
    const mimirMount = parseWorkflowRegistryMount(e.dataTransfer.getData(MIMIR_MOUNT_MIME));

    if (personaId) {
      const targetStage = findStageAtPoint(position.x, position.y);
      if (targetStage?.kind === 'stage') {
        onAddPersonaToStage?.(targetStage.id, personaId);
        onSelectNode(targetStage.id);
        return;
      }

      onAddStageWithPersona?.(personaId, undefined, position);
      return;
    }

    if (mimirMount) {
      onAddMimirResource?.(mimirMount, position);
      return;
    }

    if (isGraphNodeKind(nodeKind)) {
      onAddNode(nodeKind, position);
    }
  }

  const nodeProps = (node: WorkflowNode) => ({
    selected: node.id === selectedNodeId,
    issueLevel: issueMap.get(node.id) ?? null,
    isConnectingMode,
    onSelect: () => onSelectNode(node.id),
    onInspect: () => onInspectNode(node.id),
    onStartConnect: (label?: string) => {
      if (readOnly) return;
      if (label === undefined) {
        // A plain body-click continuation has nothing to disambiguate an
        // include's provided ids with — require an explicit port instead.
        if (node.kind !== 'include') onStartConnect(node.id);
        return;
      }
      const sourceId = resolveIncludeConnectionId(node, label, 'source', edges);
      if (sourceId) onStartConnect(sourceId, label);
    },
    onCompleteConnect: (inputLabel?: string) => {
      if (readOnly) return;
      if (inputLabel === undefined) {
        if (node.kind !== 'include') onCompleteConnect(node.id);
        return;
      }
      const targetId = resolveIncludeConnectionId(node, inputLabel, 'target', edges);
      if (targetId) onCompleteConnect(targetId, inputLabel);
    },
    onDelete: () => {
      if (!readOnly) onDeleteNode(node.id);
    },
    onDragPreview: (position: { x: number; y: number }) => {
      if (!readOnly) setDragPreview({ nodeId: node.id, position });
    },
    onDragEnd: (pos: { x: number; y: number }) => {
      setDragPreview(null);
      if (!readOnly) onMoveNode(node.id, pos);
    },
    canvasScale: transform.scale,
    readOnly,
  });

  const toolbarBtnClass =
    'niuu:bg-bg-elevated niuu:text-text-primary niuu:border niuu:border-border niuu:rounded niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:cursor-pointer niuu:font-sans';

  const connectingPickerAnchor = useMemo(() => {
    if (!connectingFromId || !connectingFromLabel) return { x: 24, y: 80 };
    const source = nodeMap.get(connectingFromId);
    if (!source) return { x: 24, y: 80 };
    const output = visiblePortCatalogs
      .get(source.id)
      ?.outputs.find((port) => port.eventType === connectingFromLabel);
    if (!output) return { x: 24, y: 80 };
    const anchor = portAnchor(source, output, { measurements });
    return {
      x: transform.x + anchor.x * transform.scale + 16,
      y: transform.y + anchor.y * transform.scale - 24,
    };
  }, [
    connectingFromId,
    connectingFromLabel,
    measurements,
    nodeMap,
    transform,
    visiblePortCatalogs,
  ]);

  return (
    <div
      ref={rootRef}
      data-testid="graph-view"
      className="workflow-canvas niuu:flex-1 niuu:relative niuu:overflow-hidden niuu:min-h-[400px]"
      tabIndex={0}
      aria-label="Workflow canvas"
      onMouseDownCapture={(event) => {
        if (event.button === 0 && svgRef.current?.contains(event.target as Node)) {
          rootRef.current?.focus({ preventScroll: true });
        }
      }}
      onMouseMove={(event) => {
        if (svgRef.current?.contains(event.target as Node)) {
          pointerRef.current = { x: event.clientX, y: event.clientY };
        }
      }}
      onMouseLeave={() => {
        pointerRef.current = null;
      }}
    >
      {!readOnly && (
        <div className="niuu:absolute niuu:bottom-4 niuu:left-4 niuu:z-20">
          <button
            type="button"
            data-testid="open-add-step"
            onClick={() => (canvasPicker ? closeCanvasPicker() : openCanvasPicker())}
            className={toolbarBtnClass}
            aria-expanded={canvasPicker !== null}
            aria-haspopup="dialog"
          >
            + Add node <span className="niuu:ml-2 niuu:font-mono niuu:text-text-faint">Tab</span>
          </button>
        </div>
      )}

      {canvasPicker && (
        <div
          ref={pickerRef}
          role="dialog"
          aria-label="Add workflow node"
          data-testid="canvas-node-picker"
          className="niuu:absolute niuu:z-30"
          style={{
            left: canvasPicker.x,
            top: canvasPicker.y,
            width: canvasPicker.width,
            height: canvasPicker.height,
          }}
        >
          {readOnly ? (
            <div className="niuu:rounded-xl niuu:border niuu:border-border niuu:bg-bg-secondary niuu:p-4 niuu:shadow-2xl niuu:text-sm niuu:text-text-primary">
              <div className="niuu:flex niuu:items-center niuu:justify-between niuu:gap-3">
                <strong>Viewing saved workflow</strong>
                <button
                  type="button"
                  onClick={closeCanvasPicker}
                  aria-label="Close node picker"
                  className={toolbarBtnClass}
                >
                  ×
                </button>
              </div>
              <p className="niuu:my-3 niuu:text-xs niuu:text-text-secondary">
                {readOnlyMessage ??
                  (onEdit
                    ? 'Choose Edit to add or change nodes. Saving creates a new version.'
                    : 'You do not have permission to edit this workflow.')}
              </p>
              {onEdit && (
                <button
                  type="button"
                  className={toolbarBtnClass}
                  onClick={() => {
                    closeCanvasPicker();
                    onEdit();
                  }}
                >
                  Edit workflow
                </button>
              )}
            </div>
          ) : (
            <LibraryPanel
              embedded
              personas={onAddStageWithPersona ? personas : []}
              registryMounts={registryMounts}
              onClose={closeCanvasPicker}
              onAddNode={(kind) => onAddNode(kind, canvasPicker.position)}
              onAddPersona={
                onAddStageWithPersona
                  ? (id) => onAddStageWithPersona(id, undefined, canvasPicker.position)
                  : undefined
              }
              onAddMimirResource={
                onAddMimirResource
                  ? (mount) => onAddMimirResource(mount, canvasPicker.position)
                  : undefined
              }
            />
          )}
        </div>
      )}

      {!readOnly && (selectedNodeId || selectedEdgeId || isConnectingMode) && (
        <div className="niuu:absolute niuu:bottom-4 niuu:left-1/2 niuu:z-20 niuu:flex niuu:-translate-x-1/2 niuu:items-center niuu:gap-2 niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-secondary niuu:p-1.5">
          {selectedNodeId && (
            <button
              data-testid="delete-selected"
              onClick={() => onDeleteNode(selectedNodeId)}
              className={cn(toolbarBtnClass, 'niuu:text-critical')}
            >
              Delete step
            </button>
          )}
          {selectedEdgeId && (
            <button
              data-testid="delete-selected-edge"
              onClick={() => {
                onDeleteEdge(selectedEdgeId);
                setSelectedEdgeId(null);
              }}
              className={cn(toolbarBtnClass, 'niuu:text-critical')}
            >
              Delete connection
            </button>
          )}
          {isConnectingMode && (
            <span className="niuu:px-2 niuu:text-xs niuu:text-text-secondary">
              Choose a compatible input…
            </span>
          )}
        </div>
      )}

      <div
        className="niuu:absolute niuu:right-4 niuu:bottom-4 niuu:z-20 niuu:flex niuu:overflow-hidden niuu:rounded-md niuu:shadow-lg"
        aria-label="Canvas zoom controls"
      >
        {!readOnly && onAutoLayout && (
          <button
            type="button"
            data-testid="auto-layout"
            className="workflow-canvas__control"
            onClick={onAutoLayout}
          >
            Auto layout
          </button>
        )}
        <button
          type="button"
          data-testid="zoom-out"
          className="workflow-canvas__control"
          onClick={() => zoomTo(transform.scale / 1.15)}
          aria-label="Zoom out"
        >
          −
        </button>
        <output
          data-testid="zoom-level"
          className="workflow-canvas__control niuu:min-w-[54px] niuu:cursor-default niuu:border-x-0"
          aria-live="polite"
        >
          {Math.round(transform.scale * 100)}%
        </output>
        <button
          type="button"
          data-testid="zoom-in"
          className="workflow-canvas__control"
          onClick={() => zoomTo(transform.scale * 1.15)}
          aria-label="Zoom in"
        >
          +
        </button>
        <button
          type="button"
          data-testid="zoom-one-to-one"
          className="workflow-canvas__control niuu:border-l-0"
          onClick={resetOneToOne}
        >
          1:1
        </button>
        <button
          type="button"
          data-testid="zoom-fit"
          className="workflow-canvas__control niuu:border-l-0"
          onClick={() => fitToNodes()}
        >
          Fit
        </button>
      </div>

      {/* Insert-from-port candidates — while connecting from a typed output,
          offer personas that actually consume it (or a flow-control block,
          which accepts any type) as an alternative to clicking an existing
          node's input. Picking a persona both creates the stage AND wires
          the edge back to the port we started from, via the same auto-wire
          logic LibraryPanel's persona drag already uses (addStageWithPersona
          matches produces/consumes across every node, including the one
          currently mid-connect). Flow-control candidates wire the same edge
          explicitly via addNodeFromPort, since they have no produces/consumes
          profile to auto-match against. */}
      {isConnectingMode && connectingFromLabel && onAddStageWithPersona && (
        <InsertFromPortMenu
          personas={personas}
          eventType={connectingFromLabel}
          anchor={connectingPickerAnchor}
          onInsertPersona={(personaId) => {
            const sourceNode = connectingFromId ? nodeMap.get(connectingFromId) : undefined;
            const position = sourceNode
              ? { x: sourceNode.position.x + STAGE_WIDTH + 96, y: sourceNode.position.y }
              : undefined;
            if (onAddStageFromPort && connectingFromId) {
              onAddStageFromPort(connectingFromId, connectingFromLabel, personaId, position);
            } else {
              onAddStageWithPersona(personaId, undefined, position);
            }
            onCancelConnect();
          }}
          onInsertFlowControl={
            onAddNodeFromPort && connectingFromId
              ? (kind) => {
                  const sourceNode = nodeMap.get(connectingFromId);
                  const position = sourceNode
                    ? { x: sourceNode.position.x + STAGE_WIDTH + 96, y: sourceNode.position.y }
                    : undefined;
                  onAddNodeFromPort(kind, connectingFromId, connectingFromLabel, position);
                  onCancelConnect();
                }
              : undefined
          }
        />
      )}

      {/* SVG Canvas */}
      <svg
        ref={svgRef}
        data-testid="graph-canvas"
        className="niuu:w-full niuu:h-full"
        style={{ cursor: isConnectingMode ? 'crosshair' : isPanning ? 'grabbing' : 'default' }}
        onMouseDown={handleSvgMouseDown}
        onMouseMove={handleSvgMouseMove}
        onMouseUp={handleSvgMouseUp}
        onMouseLeave={handleSvgMouseUp}
        onContextMenu={handleCanvasContextMenu}
        onDragOver={(e) => e.preventDefault()}
        onDrop={handleDrop}
      >
        <defs>
          <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill={C.edgeStroke} />
          </marker>
        </defs>
        <g transform={`translate(${transform.x},${transform.y}) scale(${transform.scale})`}>
          {/* Edges (drawn first, below nodes) */}
          {edges.map((edge) => (
            <WorkflowEdgePath
              key={edge.id}
              edge={edge}
              edges={edges}
              nodes={nodeMap}
              personas={personas}
              portCatalogs={visiblePortCatalogs}
              measurements={measurements}
              feedbackLane={feedbackLanes.get(edge.id) ?? 0}
              selected={edge.id === selectedEdgeId}
              onSelect={() => {
                setSelectedEdgeId(edge.id);
                onSelectNode(null);
              }}
            />
          ))}
          {/* Nodes */}
          {displayNodes.map((node) => {
            const props = nodeProps(node);
            switch (node.kind) {
              case 'trigger':
              case 'subworkflow':
                return <TriggerNode key={node.id} node={node} {...props} />;
              case 'stage':
                return (
                  <StageNode
                    key={node.id}
                    node={node}
                    portCatalog={visiblePortCatalogs.get(node.id) ?? { inputs: [], outputs: [] }}
                    connectingFromLabel={connectingFromLabel}
                    {...props}
                  />
                );
              case 'gate':
                return <GateNode key={node.id} node={node} {...props} />;
              case 'cond':
                return <CondNode key={node.id} node={node} {...props} />;
              case 'wait':
                return (
                  <WaitNode
                    key={node.id}
                    node={node}
                    {...waitPortLists(node.id, edges)}
                    connectingFromLabel={connectingFromLabel}
                    {...props}
                  />
                );
              case 'end':
                return <EndNode key={node.id} node={node} {...props} />;
              case 'resource':
                return <ResourceNode key={node.id} node={node} {...props} />;
              case 'include': {
                const includePorts = visiblePortCatalogs.get(node.id) ?? {
                  inputs: [],
                  outputs: [],
                };
                return (
                  <IncludeNode
                    key={node.id}
                    node={node}
                    portRows={Math.max(includePorts.inputs.length, includePorts.outputs.length)}
                    {...props}
                  />
                );
              }
            }
          })}
          {displayNodes.map((node) => (
            <NodeSocketLayer
              key={`sockets-${node.id}`}
              node={node}
              portCatalog={visiblePortCatalogs.get(node.id) ?? { inputs: [], outputs: [] }}
              measurements={measurements}
              selected={node.id === selectedNodeId}
              hiddenOutcomeCount={Math.max(
                0,
                (fullPortCatalogs.get(node.id)?.outputs.length ?? 0) -
                  (visiblePortCatalogs.get(node.id)?.outputs.length ?? 0),
              )}
              connecting={isConnectingMode}
              readOnly={readOnly}
              connectingFromLabel={connectingFromLabel}
              onStartConnect={(eventType) => {
                const sourceId = resolveIncludeConnectionId(node, eventType, 'source', edges);
                if (sourceId) onStartConnect(sourceId, eventType);
              }}
              onCompleteConnect={(eventType) => {
                const targetId = resolveIncludeConnectionId(node, eventType, 'target', edges);
                if (targetId) onCompleteConnect(targetId, eventType);
              }}
              onReveal={() => onSelectNode(node.id)}
            />
          ))}
        </g>
      </svg>
    </div>
  );
}
