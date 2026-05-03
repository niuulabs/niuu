/**
 * GraphView — interactive pan/zoom SVG canvas.
 *
 * Renders WorkflowNodes as SVG shapes and WorkflowEdges as bezier curves.
 * Supports:
 *  • Mouse-drag pan on the canvas background
 *  • Scroll-wheel zoom (clamped 0.3×–3×)
 *  • Node drag-to-reposition
 *  • Click to select
 *  • Right-click to open Inspector
 *  • "Connect" button on selected node to draw edges
 *  • Add-node toolbar
 *  • Delete-node button on selected node
 *
 * Owner: plugin-tyr (WorkflowBuilder).
 */

import { useRef, useState, useEffect, useMemo } from 'react';
import { cn } from '@niuulabs/ui';
import type {
  WorkflowNode,
  WorkflowEdge,
  WorkflowStageNode,
  WorkflowGateNode,
  WorkflowCondNode,
  WorkflowTriggerNode,
  WorkflowEndNode,
  WorkflowResourceNode,
} from '../../domain/workflow';
import type { WorkflowIssue } from '../../domain/workflowValidation';
import type { WorkflowBuilderActions } from './useWorkflowBuilder';
import type { PersonaEntry } from './LibraryPanel';
import { MIMIR_MOUNT_MIME, parseWorkflowRegistryMount } from './mimirRegistry';
import {
  STAGE_WIDTH,
  GATE_SIZE,
  COND_RADIUS,
  TRIGGER_WIDTH,
  TRIGGER_HEIGHT,
  END_RADIUS,
  RESOURCE_WIDTH,
  RESOURCE_HEIGHT,
  nodeCentre,
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
  gate: 'color-mix(in srgb, var(--status-amber) 20%, var(--color-bg-secondary))',
  gateStroke: 'var(--status-amber)',
  cond: 'color-mix(in srgb, var(--status-cyan) 20%, var(--color-bg-secondary))',
  condStroke: 'var(--status-cyan)',
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
  onDragEnd,
}: {
  x: number;
  y: number;
  isConnectingMode: boolean;
  onSelect: () => void;
  onCompleteConnect: () => void;
  onDragEnd: (pos: { x: number; y: number }) => void;
}) {
  const dragRef = useRef<{ startX: number; startY: number; nx: number; ny: number } | null>(null);

  function handleMouseDown(e: React.MouseEvent) {
    e.stopPropagation();
    if (isConnectingMode) {
      onCompleteConnect();
      return;
    }
    dragRef.current = { startX: e.clientX, startY: e.clientY, nx: x, ny: y };
    onSelect();
  }

  function handleMouseMove(e: React.MouseEvent) {
    if (!dragRef.current) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;
    if (Math.abs(dx) > 4 || Math.abs(dy) > 4) {
      onDragEnd({ x: dragRef.current.nx + dx, y: dragRef.current.ny + dy });
    }
  }

  function handleMouseUp() {
    dragRef.current = null;
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
      className="niuu-cursor-pointer"
    >
      <circle cx={cx} cy={cy} r={7} fill="var(--color-critical)" />
      <text
        x={cx}
        y={cy}
        textAnchor="middle"
        dominantBaseline="middle"
        fill="var(--color-bg-primary)"
        fontSize={10}
        className="niuu-pointer-events-none"
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
  onDragEnd: (position: { x: number; y: number }) => void;
  isConnectingMode: boolean;
}

function StageNode({
  node,
  personas,
  connectingFromLabel,
  selected,
  issueLevel,
  onSelect,
  onInspect,
  onStartConnect,
  onCompleteConnect,
  onDelete,
  onDragEnd,
  isConnectingMode,
}: BaseNodeProps<WorkflowStageNode> & {
  personas: PersonaEntry[];
  connectingFromLabel?: string | null;
}) {
  const { x, y } = node.position;
  const stageMembers = normalizedStageMembers(node);
  const personaMap = new Map(personas.map((persona) => [persona.id, persona]));
  const knownInputs = [
    ...new Set(stageMembers.flatMap((member) => personaMap.get(member.personaId)?.consumes ?? [])),
  ];
  const knownOutputs = [
    ...new Set(stageMembers.flatMap((member) => personaMap.get(member.personaId)?.produces ?? [])),
  ];
  const portRows = Math.max(knownInputs.length, knownOutputs.length, 0);
  const stageHeight = stageNodeHeight(node) + (portRows > 0 ? 22 + portRows * 14 : 0);
  const { handleMouseDown, handleMouseMove, handleMouseUp } = useDragNode({
    x,
    y,
    isConnectingMode,
    onSelect,
    onCompleteConnect: () => {},
    onDragEnd,
  });
  const fill = selected
    ? 'var(--color-bg-elevated)'
    : issueLevel === 'error'
      ? C.errorFill
      : issueLevel === 'warning'
        ? C.warnFill
        : C.nodeFill;
  const stroke = selected
    ? C.nodeStrokeSelected
    : issueLevel === 'error'
      ? C.errorStroke
      : issueLevel === 'warning'
        ? C.warnStroke
        : C.nodeStroke;
  const sw = selected || issueLevel ? 2 : 1;

  return (
    <g
      data-testid={`workflow-node-${node.id}`}
      data-kind="stage"
      data-selected={selected ? 'true' : undefined}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={(e) => {
        e.preventDefault();
        onInspect();
      }}
      style={{ cursor: isConnectingMode ? 'crosshair' : 'grab' }}
    >
      <rect
        x={x}
        y={y}
        width={STAGE_WIDTH}
        height={stageHeight}
        rx={6}
        fill={fill}
        stroke={stroke}
        strokeWidth={sw}
      />
      <text
        x={x + STAGE_WIDTH / 2}
        y={y + 18}
        textAnchor="middle"
        fill={C.text}
        fontSize={11}
        fontWeight="600"
        fontFamily="var(--font-sans)"
        className="niuu-pointer-events-none niuu-select-none"
      >
        {node.kind === 'stage' && node.label.length > 18
          ? node.label.slice(0, 16) + '…'
          : node.label}
      </text>
      <text
        x={x + 12}
        y={y + 34}
        fill={C.textMuted}
        fontSize={8}
        fontFamily="var(--font-mono)"
        className="niuu-pointer-events-none niuu-select-none"
      >
        ACTOR STAGE · {(node.executionMode ?? 'parallel').toUpperCase()} · {stageMembers.length} RAVN
        {stageMembers.length !== 1 ? 'S' : ''}
      </text>
      {stageMembers.map((member, index) => (
        <g key={`${member.personaId}-${index}`}>
          <rect
            x={x + 10}
            y={y + 42 + index * 20}
            width={STAGE_WIDTH - 20}
            height={16}
            rx={4}
            fill="var(--color-bg-primary)"
            opacity={0.65}
          />
          <text
            x={x + 16}
            y={y + 53 + index * 20}
            fill={C.text}
            fontSize={8.5}
            fontFamily="var(--font-sans)"
            className="niuu-pointer-events-none niuu-select-none"
          >
            {member.personaId}
          </text>
          <text
            x={x + STAGE_WIDTH - 16}
            y={y + 53 + index * 20}
            textAnchor="end"
            fill={C.textMuted}
            fontSize={8}
            fontFamily="var(--font-mono)"
            className="niuu-pointer-events-none niuu-select-none"
          >
            budget {member.budget}
          </text>
        </g>
      ))}
      {knownInputs.length > 0 && (
        <>
          <text
            x={x + 12}
            y={y + stageHeight - (portRows * 14 + 12)}
            fill={C.textMuted}
            fontSize={7}
            fontFamily="var(--font-mono)"
            className="niuu-pointer-events-none niuu-select-none"
          >
            known inputs
          </text>
          {knownInputs.map((input, index) => (
            <g key={`in-${input}-${index}`}>
              {isConnectingMode && (
                <circle
                  cx={x + 10}
                  cy={y + stageHeight - portRows * 14 + index * 14 - 4}
                  r={4}
                  fill="var(--color-bg-primary)"
                  stroke="var(--color-brand)"
                  strokeWidth={1}
                  onClick={(e) => {
                    e.stopPropagation();
                    onCompleteConnect(input);
                  }}
                  className="niuu-cursor-pointer"
                />
              )}
              <rect
                x={x + 18}
                y={y + stageHeight - portRows * 14 + index * 14 - 9}
                width={64}
                height={10}
                rx={3}
                fill="var(--color-bg-primary)"
                stroke="var(--color-border-subtle)"
              />
              <text
                x={x + 23}
                y={y + stageHeight - portRows * 14 + index * 14 - 2}
                textAnchor="start"
                fill={C.textMuted}
                fontSize={6.5}
                fontFamily="var(--font-mono)"
                className="niuu-pointer-events-none niuu-select-none"
              >
                {input.length > 14 ? `${input.slice(0, 13)}…` : input}
              </text>
            </g>
          ))}
        </>
      )}
      {knownOutputs.length > 0 && (
        <>
          <text
            x={x + STAGE_WIDTH - 12}
            y={y + stageHeight - (portRows * 14 + 12)}
            textAnchor="end"
            fill={C.textMuted}
            fontSize={7}
            fontFamily="var(--font-mono)"
            className="niuu-pointer-events-none niuu-select-none"
          >
            known outputs
          </text>
          {knownOutputs.map((output, index) => (
            <g key={`out-${output}-${index}`}>
              <rect
                x={x + STAGE_WIDTH - 82}
                y={y + stageHeight - portRows * 14 + index * 14 - 9}
                width={64}
                height={10}
                rx={3}
                fill="var(--color-bg-primary)"
                stroke="var(--color-border-subtle)"
              />
              <circle
                cx={x + STAGE_WIDTH - 10}
                cy={y + stageHeight - portRows * 14 + index * 14 - 4}
                r={4}
                fill={
                  connectingFromLabel === output ? 'var(--color-brand)' : 'var(--color-bg-primary)'
                }
                stroke="var(--color-brand)"
                strokeWidth={1}
                onClick={(e) => {
                  e.stopPropagation();
                  onStartConnect(output);
                }}
                className="niuu-cursor-pointer"
              />
              <text
                x={x + STAGE_WIDTH - 77}
                y={y + stageHeight - portRows * 14 + index * 14 - 2}
                textAnchor="start"
                fill={C.text}
                fontSize={6.5}
                fontFamily="var(--font-mono)"
                className="niuu-pointer-events-none niuu-select-none"
              >
                {output.length > 14 ? `${output.slice(0, 13)}…` : output}
              </text>
            </g>
          ))}
        </>
      )}
      {issueLevel && (
        <text
          x={x + STAGE_WIDTH - 12}
          y={y + 18}
          textAnchor="end"
          fill={issueLevel === 'error' ? C.errorStroke : C.warnStroke}
          fontSize={8}
          fontFamily="var(--font-mono)"
          className="niuu-pointer-events-none niuu-select-none"
        >
          {issueLevel === 'error' ? 'ERR' : 'WARN'}
        </text>
      )}
      {selected && !isConnectingMode && (
        <DeleteButton nodeId={node.id} cx={x + STAGE_WIDTH / 2} cy={y - 10} onClick={onDelete} />
      )}
    </g>
  );
}

function GateNode({
  node,
  selected,
  issueLevel,
  onSelect,
  onInspect,
  onStartConnect: _onStartConnect,
  onCompleteConnect,
  onDelete,
  onDragEnd,
  isConnectingMode,
}: BaseNodeProps<WorkflowGateNode>) {
  const { x, y } = node.position;
  const { handleMouseDown, handleMouseMove, handleMouseUp } = useDragNode({
    x,
    y,
    isConnectingMode,
    onSelect,
    onCompleteConnect,
    onDragEnd,
  });
  const H = GATE_SIZE / 2;
  const fill =
    issueLevel === 'error' ? C.errorFill : issueLevel === 'warning' ? C.warnFill : C.gate;
  const stroke = selected
    ? C.nodeStrokeSelected
    : issueLevel === 'error'
      ? C.errorStroke
      : issueLevel === 'warning'
        ? C.warnStroke
        : C.gateStroke;
  const sw = selected || issueLevel ? 2 : 1;
  const cx = x + H;
  const cy = y + H;

  return (
    <g
      data-testid={`workflow-node-${node.id}`}
      data-kind="gate"
      data-selected={selected ? 'true' : undefined}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={(e) => {
        e.preventDefault();
        onInspect();
      }}
      style={{ cursor: isConnectingMode ? 'crosshair' : 'grab' }}
    >
      <path
        d={`M ${cx} ${y} L ${x + GATE_SIZE} ${cy} L ${cx} ${y + GATE_SIZE} L ${x} ${cy} Z`}
        fill={fill}
        stroke={stroke}
        strokeWidth={sw}
      />
      <text
        x={cx}
        y={cy}
        textAnchor="middle"
        dominantBaseline="middle"
        fill={C.text}
        fontSize={11}
        fontFamily="var(--font-sans)"
        className="niuu-pointer-events-none niuu-select-none"
      >
        {node.label.length > 8 ? node.label.slice(0, 7) + '…' : node.label}
      </text>
      {selected && !isConnectingMode && (
        <DeleteButton nodeId={node.id} cx={cx} cy={y - 10} onClick={onDelete} />
      )}
    </g>
  );
}

function CondNode({
  node,
  selected,
  issueLevel,
  onSelect,
  onInspect,
  onStartConnect: _onStartConnect,
  onCompleteConnect,
  onDelete,
  onDragEnd,
  isConnectingMode,
}: BaseNodeProps<WorkflowCondNode>) {
  const { x, y } = node.position;
  const { handleMouseDown, handleMouseMove, handleMouseUp } = useDragNode({
    x,
    y,
    isConnectingMode,
    onSelect,
    onCompleteConnect,
    onDragEnd,
  });
  const cx = x + COND_RADIUS;
  const cy = y + COND_RADIUS;
  const fill =
    issueLevel === 'error' ? C.errorFill : issueLevel === 'warning' ? C.warnFill : C.cond;
  const stroke = selected
    ? C.nodeStrokeSelected
    : issueLevel === 'error'
      ? C.errorStroke
      : issueLevel === 'warning'
        ? C.warnStroke
        : C.condStroke;
  const sw = selected || issueLevel ? 2 : 1;

  return (
    <g
      data-testid={`workflow-node-${node.id}`}
      data-kind="cond"
      data-selected={selected ? 'true' : undefined}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={(e) => {
        e.preventDefault();
        onInspect();
      }}
      style={{ cursor: isConnectingMode ? 'crosshair' : 'grab' }}
    >
      <circle cx={cx} cy={cy} r={COND_RADIUS} fill={fill} stroke={stroke} strokeWidth={sw} />
      <text
        x={cx}
        y={cy}
        textAnchor="middle"
        dominantBaseline="middle"
        fill={C.text}
        fontSize={10}
        fontFamily="var(--font-sans)"
        className="niuu-pointer-events-none niuu-select-none"
      >
        {node.label.length > 6 ? node.label.slice(0, 5) + '…' : node.label}
      </text>
      {selected && !isConnectingMode && (
        <DeleteButton nodeId={node.id} cx={cx} cy={cy - COND_RADIUS - 10} onClick={onDelete} />
      )}
    </g>
  );
}

function TriggerNode({
  node,
  selected,
  issueLevel,
  onSelect,
  onInspect,
  onStartConnect,
  onCompleteConnect,
  onDelete,
  onDragEnd,
  isConnectingMode,
}: BaseNodeProps<WorkflowTriggerNode>) {
  const { x, y } = node.position;
  const { handleMouseDown, handleMouseMove, handleMouseUp } = useDragNode({
    x,
    y,
    isConnectingMode,
    onSelect,
    onCompleteConnect,
    onDragEnd,
  });
  const fill = selected
    ? 'var(--color-bg-elevated)'
    : issueLevel === 'error'
      ? C.errorFill
      : issueLevel === 'warning'
        ? C.warnFill
        : 'color-mix(in srgb, var(--status-cyan) 14%, var(--color-bg-secondary))';
  const stroke = selected
    ? C.nodeStrokeSelected
    : issueLevel === 'error'
      ? C.errorStroke
      : issueLevel === 'warning'
        ? C.warnStroke
        : 'var(--status-cyan)';
  const sw = selected || issueLevel ? 2 : 1;

  return (
    <g
      data-testid={`workflow-node-${node.id}`}
      data-kind="trigger"
      data-selected={selected ? 'true' : undefined}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={(e) => {
        e.preventDefault();
        onInspect();
      }}
      style={{ cursor: isConnectingMode ? 'crosshair' : 'grab' }}
    >
      <rect
        x={x}
        y={y}
        width={TRIGGER_WIDTH}
        height={TRIGGER_HEIGHT}
        rx={8}
        fill={fill}
        stroke={stroke}
        strokeWidth={sw}
      />
      <text
        x={x + 14}
        y={y + 22}
        fill={C.text}
        fontSize={11}
        fontWeight="600"
        fontFamily="var(--font-sans)"
      >
        {node.label.length > 20 ? node.label.slice(0, 18) + '…' : node.label}
      </text>
      <text x={x + 14} y={y + 38} fill={C.textMuted} fontSize={8.5} fontFamily="var(--font-mono)">
        {node.dispatchEvent ?? 'code.requested'}
      </text>
      <circle
        data-testid={`trigger-output-${node.id}`}
        cx={x + TRIGGER_WIDTH - 10}
        cy={y + TRIGGER_HEIGHT / 2}
        r={4}
        fill="var(--color-bg-primary)"
        stroke="var(--color-brand)"
        strokeWidth={1}
        onClick={(e) => {
          e.stopPropagation();
          onStartConnect(node.dispatchEvent ?? 'code.requested');
        }}
        className="niuu-cursor-pointer"
      />
      {selected && !isConnectingMode && (
        <DeleteButton nodeId={node.id} cx={x + TRIGGER_WIDTH / 2} cy={y - 10} onClick={onDelete} />
      )}
    </g>
  );
}

function EndNode({
  node,
  selected,
  issueLevel,
  onSelect,
  onInspect,
  onStartConnect: _onStartConnect,
  onCompleteConnect,
  onDelete,
  onDragEnd,
  isConnectingMode,
}: BaseNodeProps<WorkflowEndNode>) {
  const { x, y } = node.position;
  const { handleMouseDown, handleMouseMove, handleMouseUp } = useDragNode({
    x,
    y,
    isConnectingMode,
    onSelect,
    onCompleteConnect,
    onDragEnd,
  });
  const fill = selected
    ? 'var(--color-bg-elevated)'
    : issueLevel === 'error'
      ? C.errorFill
      : issueLevel === 'warning'
        ? C.warnFill
        : 'color-mix(in srgb, var(--status-emerald) 14%, var(--color-bg-secondary))';
  const stroke = selected
    ? C.nodeStrokeSelected
    : issueLevel === 'error'
      ? C.errorStroke
      : issueLevel === 'warning'
        ? C.warnStroke
        : 'var(--status-emerald)';
  const sw = selected || issueLevel ? 2 : 1;
  const cx = x + END_RADIUS;
  const cy = y + END_RADIUS;

  return (
    <g
      data-testid={`workflow-node-${node.id}`}
      data-kind="end"
      data-selected={selected ? 'true' : undefined}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={(e) => {
        e.preventDefault();
        onInspect();
      }}
      style={{ cursor: isConnectingMode ? 'crosshair' : 'grab' }}
    >
      <circle cx={cx} cy={cy} r={END_RADIUS} fill={fill} stroke={stroke} strokeWidth={sw} />
      <text
        x={cx}
        y={cy - 4}
        textAnchor="middle"
        fill={C.text}
        fontSize={16}
        fontFamily="var(--font-sans)"
      >
        ●
      </text>
      <text
        x={cx}
        y={cy + 12}
        textAnchor="middle"
        fill={C.textMuted}
        fontSize={8}
        fontFamily="var(--font-mono)"
      >
        {node.label.length > 10 ? node.label.slice(0, 8) + '…' : node.label}
      </text>
      {isConnectingMode && (
        <circle
          data-testid={`end-input-${node.id}`}
          cx={x + 6}
          cy={cy}
          r={4}
          fill="var(--color-bg-primary)"
          stroke="var(--color-brand)"
          strokeWidth={1}
          onClick={(e) => {
            e.stopPropagation();
            onCompleteConnect('complete');
          }}
          className="niuu-cursor-pointer"
        />
      )}
      {selected && !isConnectingMode && (
        <DeleteButton nodeId={node.id} cx={cx} cy={y - 10} onClick={onDelete} />
      )}
    </g>
  );
}

function ResourceNode({
  node,
  selected,
  issueLevel,
  onSelect,
  onInspect,
  onStartConnect: _onStartConnect,
  onCompleteConnect,
  onDelete,
  onDragEnd,
  isConnectingMode,
}: BaseNodeProps<WorkflowResourceNode>) {
  const { x, y } = node.position;
  const { handleMouseDown, handleMouseMove, handleMouseUp } = useDragNode({
    x,
    y,
    isConnectingMode,
    onSelect,
    onCompleteConnect,
    onDragEnd,
  });
  const fill = selected
    ? 'var(--color-bg-elevated)'
    : issueLevel === 'error'
      ? C.errorFill
      : issueLevel === 'warning'
        ? C.warnFill
        : 'color-mix(in srgb, var(--color-brand) 16%, var(--color-bg-secondary))';
  const stroke = selected
    ? C.nodeStrokeSelected
    : issueLevel === 'error'
      ? C.errorStroke
      : issueLevel === 'warning'
        ? C.warnStroke
        : 'var(--color-brand)';
  const sw = selected || issueLevel ? 2 : 1;
  const modeLabel = node.bindingMode === 'ephemeral_local' ? 'EPHEMERAL' : 'REGISTRY';
  const modeStroke =
    node.bindingMode === 'ephemeral_local' ? 'var(--status-emerald)' : 'var(--color-brand)';
  const modeFill =
    node.bindingMode === 'ephemeral_local'
      ? 'color-mix(in srgb, var(--status-emerald) 14%, var(--color-bg-primary))'
      : 'color-mix(in srgb, var(--color-brand) 14%, var(--color-bg-primary))';

  return (
    <g
      data-testid={`workflow-node-${node.id}`}
      data-kind="resource"
      data-selected={selected ? 'true' : undefined}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onContextMenu={(e) => {
        e.preventDefault();
        onInspect();
      }}
      style={{ cursor: isConnectingMode ? 'crosshair' : 'grab' }}
    >
      <ellipse
        cx={x + RESOURCE_WIDTH / 2}
        cy={y + 10}
        rx={RESOURCE_WIDTH / 2}
        ry={10}
        fill={fill}
        stroke={stroke}
        strokeWidth={sw}
      />
      <rect
        x={x}
        y={y + 10}
        width={RESOURCE_WIDTH}
        height={RESOURCE_HEIGHT - 20}
        fill={fill}
        stroke={stroke}
        strokeWidth={sw}
      />
      <ellipse
        cx={x + RESOURCE_WIDTH / 2}
        cy={y + RESOURCE_HEIGHT - 10}
        rx={RESOURCE_WIDTH / 2}
        ry={10}
        fill={fill}
        stroke={stroke}
        strokeWidth={sw}
      />
      <text
        x={x + 14}
        y={y + 22}
        fill={C.text}
        fontSize={11}
        fontWeight="600"
        fontFamily="var(--font-sans)"
      >
        {node.label.length > 20 ? node.label.slice(0, 18) + '…' : node.label}
      </text>
      <text x={x + 14} y={y + 38} fill={C.textMuted} fontSize={8.5} fontFamily="var(--font-mono)">
        RESOURCE · MIMIR
      </text>
      <rect
        x={x + 14}
        y={y + 46}
        width={72}
        height={14}
        rx={7}
        fill={modeFill}
        stroke={modeStroke}
      />
      <text
        x={x + 50}
        y={y + 55.5}
        textAnchor="middle"
        fill={C.text}
        fontSize={7.5}
        fontWeight="600"
        fontFamily="var(--font-mono)"
      >
        {modeLabel}
      </text>
      {selected && !isConnectingMode && (
        <DeleteButton nodeId={node.id} cx={x + RESOURCE_WIDTH / 2} cy={y - 10} onClick={onDelete} />
      )}
    </g>
  );
}

function stagePortLists(node: WorkflowStageNode, personas: PersonaEntry[]) {
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

function renderedStageHeight(node: WorkflowStageNode, personas: PersonaEntry[]) {
  const { knownInputs, knownOutputs } = stagePortLists(node, personas);
  const portRows = Math.max(knownInputs.length, knownOutputs.length, 0);
  return stageNodeHeight(node) + (portRows > 0 ? 22 + portRows * 14 : 0);
}

function splitEdgePorts(label?: string) {
  if (!label) return { sourcePort: null, targetPort: null };
  const [rawSourcePort, rawTargetPort] = label.split(' -> ', 2);
  const sourcePort = rawSourcePort ?? null;
  const targetPort = rawTargetPort ?? null;
  if (rawTargetPort !== undefined) {
    return { sourcePort, targetPort };
  }
  return { sourcePort: label, targetPort: label };
}

function edgeAnchor(
  node: WorkflowNode,
  direction: 'source' | 'target',
  portLabel: string | null,
  personas: PersonaEntry[],
) {
  if (node.kind !== 'stage' || !portLabel) {
    return nodeCentre(node);
  }
  const { knownInputs, knownOutputs } = stagePortLists(node, personas);
  const portList = direction === 'source' ? knownOutputs : knownInputs;
  const index = portList.indexOf(portLabel);
  if (index === -1) {
    return nodeCentre(node);
  }
  const portRows = Math.max(knownInputs.length, knownOutputs.length, 0);
  const totalHeight = renderedStageHeight(node, personas);
  return {
    x: direction === 'source' ? node.position.x + STAGE_WIDTH - 10 : node.position.x + 10,
    y: node.position.y + totalHeight - portRows * 14 + index * 14 - 4,
  };
}

function WorkflowEdgePath({
  edge,
  nodes,
  personas,
  selected,
  onSelect,
}: {
  edge: WorkflowEdge;
  nodes: Map<string, WorkflowNode>;
  personas: PersonaEntry[];
  selected: boolean;
  onSelect: () => void;
}) {
  const src = nodes.get(edge.source);
  const tgt = nodes.get(edge.target);
  if (!src || !tgt) return null;
  const { sourcePort, targetPort } = splitEdgePorts(edge.label);
  const srcC = edgeAnchor(src, 'source', sourcePort, personas);
  const tgtC = edgeAnchor(tgt, 'target', targetPort, personas);
  const c1x = srcC.x + edge.cp1.x;
  const c1y = srcC.y + edge.cp1.y;
  const c2x = tgtC.x + edge.cp2.x;
  const c2y = tgtC.y + edge.cp2.y;
  const d = `M ${srcC.x} ${srcC.y} C ${c1x} ${c1y}, ${c2x} ${c2y}, ${tgtC.x} ${tgtC.y}`;
  if (!d) return null;
  return (
    <g data-testid={`workflow-edge-${edge.id}`}>
      <path
        d={d}
        fill="none"
        stroke="transparent"
        strokeWidth={10}
        onClick={(e) => {
          e.stopPropagation();
          onSelect();
        }}
        className="niuu-cursor-pointer"
      />
      <path
        d={d}
        fill="none"
        stroke={selected ? C.nodeStrokeSelected : C.edgeStroke}
        strokeWidth={selected ? 2.25 : 1.5}
        markerEnd="url(#arrowhead)"
        onClick={(e) => {
          e.stopPropagation();
          onSelect();
        }}
        className="niuu-cursor-pointer"
      />
      {edge.label &&
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

// ---------------------------------------------------------------------------
// GraphView
// ---------------------------------------------------------------------------

export interface GraphViewProps {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  personas?: PersonaEntry[];
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
}

export function GraphView({
  nodes,
  edges,
  personas = [],
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
}: GraphViewProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [transform, setTransform] = useState({ x: 0, y: 0, scale: 1 });
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const panRef = useRef<{ startX: number; startY: number; tx: number; ty: number } | null>(null);

  const nodeMap = useMemo(
    () => new Map<string, WorkflowNode>(nodes.map((n) => [n.id, n])),
    [nodes],
  );
  const safeIssues = useMemo(() => (Array.isArray(issues) ? issues : []), [issues]);
  const issueMap = useMemo(() => {
    const levels = new Map<string, 'error' | 'warning'>();
    for (const issue of safeIssues) {
      if (!issue.nodeId) continue;
      const existing = levels.get(issue.nodeId);
      if (issue.severity === 'error' || !existing) {
        levels.set(issue.nodeId, issue.severity);
      }
    }
    return levels;
  }, [safeIssues]);
  const isConnectingMode = connectingFromId !== null;

  // Wheel zoom
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    function handleWheel(e: WheelEvent) {
      e.preventDefault();
      setTransform((prev) => {
        const delta = e.deltaY < 0 ? 1.1 : 0.9;
        const newScale = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, prev.scale * delta));
        return { ...prev, scale: newScale };
      });
    }
    el.addEventListener('wheel', handleWheel, { passive: false });
    return () => el.removeEventListener('wheel', handleWheel);
  }, []);

  // Key handler for Escape (cancel connect) and Delete (delete selected)
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onCancelConnect();
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedNodeId) {
        onDeleteNode(selectedNodeId);
      }
      if ((e.key === 'Delete' || e.key === 'Backspace') && selectedEdgeId) {
        onDeleteEdge(selectedEdgeId);
        setSelectedEdgeId(null);
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onCancelConnect, onDeleteEdge, onDeleteNode, selectedEdgeId, selectedNodeId]);

  function handleSvgMouseDown(e: React.MouseEvent<SVGSVGElement>) {
    if ((e.target as Element).closest('[data-testid^="workflow-node"]')) return;
    if ((e.target as Element).closest('[data-testid^="workflow-edge"]')) return;
    if (isConnectingMode) {
      onCancelConnect();
      return;
    }
    setSelectedEdgeId(null);
    onSelectNode(null);
    panRef.current = { startX: e.clientX, startY: e.clientY, tx: transform.x, ty: transform.y };
  }

  function handleSvgMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    if (!panRef.current) return;
    setTransform((prev) => ({
      ...prev,
      x: panRef.current!.tx + (e.clientX - panRef.current!.startX),
      y: panRef.current!.ty + (e.clientY - panRef.current!.startY),
    }));
  }

  function handleSvgMouseUp() {
    panRef.current = null;
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

  function findStageAtPoint(x: number, y: number) {
    return nodes.find(
      (node) =>
        node.kind === 'stage' &&
        x >= node.position.x &&
        x <= node.position.x + STAGE_WIDTH &&
        y >= node.position.y &&
        y <= node.position.y + renderedStageHeight(node, personas),
    );
  }

  function handleDrop(e: React.DragEvent<SVGSVGElement>) {
    e.preventDefault();
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

      onAddStageWithPersona?.(personaId, position);
      return;
    }

    if (mimirMount) {
      onAddMimirResource?.(mimirMount, position);
      return;
    }

    if (
      nodeKind === 'trigger' ||
      nodeKind === 'stage' ||
      nodeKind === 'resource' ||
      nodeKind === 'gate' ||
      nodeKind === 'cond' ||
      nodeKind === 'end'
    ) {
      onAddNode(nodeKind, position);
    }
  }

  const nodeProps = (node: WorkflowNode) => ({
    selected: node.id === selectedNodeId,
    issueLevel: issueMap.get(node.id) ?? null,
    isConnectingMode,
    onSelect: () => onSelectNode(node.id),
    onInspect: () => onInspectNode(node.id),
    onStartConnect: (label?: string) =>
      label === undefined ? onStartConnect(node.id) : onStartConnect(node.id, label),
    onCompleteConnect: (inputLabel?: string) =>
      inputLabel === undefined
        ? onCompleteConnect(node.id)
        : onCompleteConnect(node.id, inputLabel),
    onDelete: () => onDeleteNode(node.id),
    onDragEnd: (pos: { x: number; y: number }) => onMoveNode(node.id, pos),
  });

  const toolbarBtnClass =
    'niuu-bg-bg-elevated niuu-text-text-primary niuu-border niuu-border-border niuu-rounded niuu-px-2.5 niuu-py-1 niuu-text-xs niuu-cursor-pointer niuu-font-sans';

  return (
    <div
      data-testid="graph-view"
      className="niuu-flex-1 niuu-relative niuu-overflow-hidden niuu-bg-bg-primary niuu-min-h-[400px]"
    >
      {/* Toolbar */}
      <div className="niuu-absolute niuu-bottom-12 niuu-left-1/2 niuu--translate-x-1/2 niuu-flex niuu-gap-2 niuu-z-10 niuu-bg-bg-secondary niuu-border niuu-border-border niuu-rounded-md niuu-py-1.5 niuu-px-2.5">
        <button
          data-testid="add-trigger"
          onClick={() => onAddNode('trigger')}
          className={toolbarBtnClass}
          title="Add trigger node"
        >
          Trigger
        </button>
        <button
          data-testid="add-stage"
          onClick={() => onAddNode('stage')}
          className={toolbarBtnClass}
          title="Add stage node"
        >
          + Stage
        </button>
        <button
          data-testid="add-resource"
          onClick={() => onAddNode('resource')}
          className={toolbarBtnClass}
          title="Add resource node"
        >
          Resource
        </button>
        <button
          data-testid="add-gate"
          onClick={() => onAddNode('gate')}
          className={toolbarBtnClass}
          title="Add gate node"
        >
          Gate
        </button>
        <button
          data-testid="add-cond"
          onClick={() => onAddNode('cond')}
          className={toolbarBtnClass}
          title="Add condition node"
        >
          Condition
        </button>
        <button
          data-testid="add-end"
          onClick={() => onAddNode('end')}
          className={toolbarBtnClass}
          title="Add end node"
        >
          End
        </button>
        {selectedNodeId && (
          <button
            data-testid="delete-selected"
            onClick={() => onDeleteNode(selectedNodeId)}
            className={cn(toolbarBtnClass, 'niuu-text-critical')}
            title="Delete selected node"
          >
            Delete
          </button>
        )}
        {selectedEdgeId && (
          <button
            data-testid="delete-selected-edge"
            onClick={() => {
              onDeleteEdge(selectedEdgeId);
              setSelectedEdgeId(null);
            }}
            className={cn(toolbarBtnClass, 'niuu-text-critical')}
            title="Delete selected connection"
          >
            Delete connection
          </button>
        )}
        {isConnectingMode && (
          <span className="niuu-text-text-secondary niuu-text-xs niuu-self-center">
            Click target input…
          </span>
        )}
      </div>

      {/* SVG Canvas */}
      <svg
        ref={svgRef}
        data-testid="graph-canvas"
        className="niuu-w-full niuu-h-full"
        style={{ cursor: isConnectingMode ? 'crosshair' : panRef.current ? 'grabbing' : 'default' }}
        onMouseDown={handleSvgMouseDown}
        onMouseMove={handleSvgMouseMove}
        onMouseUp={handleSvgMouseUp}
        onMouseLeave={handleSvgMouseUp}
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
              nodes={nodeMap}
              personas={personas}
              selected={edge.id === selectedEdgeId}
              onSelect={() => {
                setSelectedEdgeId(edge.id);
                onSelectNode(null);
              }}
            />
          ))}
          {/* Nodes */}
          {nodes.map((node) => {
            const props = nodeProps(node);
            switch (node.kind) {
              case 'trigger':
                return <TriggerNode key={node.id} node={node} {...props} />;
              case 'stage':
                return (
                  <StageNode
                    key={node.id}
                    node={node}
                    personas={personas}
                    connectingFromLabel={connectingFromLabel}
                    {...props}
                  />
                );
              case 'gate':
                return <GateNode key={node.id} node={node} {...props} />;
              case 'cond':
                return <CondNode key={node.id} node={node} {...props} />;
              case 'end':
                return <EndNode key={node.id} node={node} {...props} />;
              case 'resource':
                return <ResourceNode key={node.id} node={node} {...props} />;
            }
          })}
        </g>
      </svg>
    </div>
  );
}
