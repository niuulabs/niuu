/**
 * State hook for the WorkflowBuilder.
 *
 * Manages the editable Workflow, the active view tab, node selection, and
 * the pending "connect from" state used when drawing new edges.
 *
 * All mutations return a new Workflow so callers can persist as needed.
 *
 * Owner: plugin-ting (WorkflowBuilder).
 */

import { useState, useCallback, useRef } from 'react';
import type {
  Workflow,
  WorkflowNode,
  WorkflowNodeKind,
  WorkflowResourceBinding,
  WorkflowStageNode,
} from '../../domain/workflow';
import type { PersonaEntry } from './LibraryPanel';
import { makeNodeId, makeEdgeId, defaultBezierCPs } from './graphUtils';
import { EPHEMERAL_LOCAL_MOUNT_ID, type WorkflowRegistryMount } from './mimirRegistry';
import { normalizeWorkflowGraph } from '../../domain/workflowSemantics';

export type WorkflowView = 'graph' | 'pipeline' | 'yaml';

export interface WorkflowStageModelOption {
  id: string;
  label: string;
  vendor?: string;
}

export interface WorkflowBuilderState {
  workflow: Workflow;
  view: WorkflowView;
  selectedNodeId: string | null;
  /** When non-null, we're in "connect" mode — next node click completes the edge. */
  connectingFromId: string | null;
  connectingFromLabel: string | null;
  /** When non-null, the NodeInspector Dialog is open for this node. */
  inspectorNodeId: string | null;
}

export interface WorkflowBuilderActions {
  setView(v: WorkflowView): void;
  selectNode(id: string | null): void;
  inspectNode(id: string | null): void;
  addNode(kind: WorkflowNodeKind, position?: { x: number; y: number }): void;
  addMimirResource(mount: WorkflowRegistryMount, position?: { x: number; y: number }): void;
  addStageWithPersona(personaId: string, model?: string, position?: { x: number; y: number }): void;
  deleteNode(id: string): void;
  deleteEdge(id: string): void;
  moveNode(id: string, position: { x: number; y: number }): void;
  startConnect(sourceId: string, label?: string): void;
  cancelConnect(): void;
  completeConnect(targetId: string, inputLabel?: string): void;
  addPersonaToStage(nodeId: string, personaId: string, model?: string, budget?: number): void;
  replacePersonaInStage(
    nodeId: string,
    previousPersonaId: string,
    personaId: string,
    model?: string,
  ): void;
  updatePersonaModel(nodeId: string, personaId: string, model: string): void;
  updatePersonaBudget(nodeId: string, personaId: string, budget: number): void;
  removePersonaFromStage(nodeId: string, personaId: string): void;
  updateNodeLabel(id: string, label: string): void;
  updateNode(id: string, patch: Partial<WorkflowNode>): void;
  addResourceBinding(
    resourceNodeId: string,
    patch?: Partial<Omit<WorkflowResourceBinding, 'id' | 'resourceNodeId'>>,
  ): void;
  updateResourceBinding(id: string, patch: Partial<WorkflowResourceBinding>): void;
  removeResourceBinding(id: string): void;
  updateWorkflowMeta(
    patch: Partial<Pick<Workflow, 'name' | 'description' | 'version' | 'tags'>>,
  ): void;
  setWorkflow(workflow: Workflow): void;
}

const DEFAULT_STAGE_POSITION = { x: 120, y: 120 };
const POSITION_OFFSET = 180;

function nextPosition(workflow: Workflow): { x: number; y: number } {
  if (workflow.nodes.length === 0) return { ...DEFAULT_STAGE_POSITION };
  const last = workflow.nodes[workflow.nodes.length - 1]!;
  return { x: last.position.x + POSITION_OFFSET, y: last.position.y };
}

function makeNewNode(kind: WorkflowNodeKind, position: { x: number; y: number }): WorkflowNode {
  const id = makeNodeId();
  switch (kind) {
    case 'stage':
      return {
        id,
        kind: 'stage',
        label: 'New stage',
        runId: null,
        personaIds: [],
        stageMembers: [],
        executionMode: 'parallel',
        maxConcurrent: 3,
        joinMode: 'all',
        position,
      };
    case 'gate':
      return {
        id,
        kind: 'gate',
        label: 'Gate',
        condition: '',
        mode: 'human_approval',
        pendingBehavior: 'help_needed',
        approvalEvent: '',
        changesRequestedEvent: '',
        instructions: '',
        autoForwardAfter: '30m',
        position,
      };
    case 'cond':
      return { id, kind: 'cond', label: 'Condition', predicate: '', position };
    case 'trigger':
      return {
        id,
        kind: 'trigger',
        label: 'Manual trigger',
        source: 'manual dispatch',
        dispatchEvent: 'code.requested',
        position,
      };
    case 'end':
      return { id, kind: 'end', label: 'Complete', position };
    case 'resource':
      return {
        id,
        kind: 'resource',
        label: 'Mimir resource',
        resourceType: 'mimir',
        bindingMode: 'registry',
        registryEntryId: null,
        seedFromRegistryId: null,
        categories: [],
        path: null,
        url: null,
        role: null,
        authRef: null,
        defaultReadPriority: 10,
        position,
      };
  }
}

function makeResourceNodeFromMount(
  mount: WorkflowRegistryMount,
  position: { x: number; y: number },
): WorkflowNode {
  if (mount.lifecycle === 'ephemeral' || mount.id === EPHEMERAL_LOCAL_MOUNT_ID) {
    return {
      id: makeNodeId(),
      kind: 'resource',
      label: mount.name,
      resourceType: 'mimir',
      bindingMode: 'ephemeral_local',
      registryEntryId: null,
      seedFromRegistryId: null,
      categories: [...(mount.categories ?? [])],
      path: null,
      url: null,
      role: 'local',
      authRef: null,
      defaultReadPriority: mount.defaultReadPriority,
      position,
    };
  }

  return {
    id: makeNodeId(),
    kind: 'resource',
    label: mount.name,
    resourceType: 'mimir',
    bindingMode: 'registry',
    registryEntryId: mount.id,
    seedFromRegistryId: null,
    categories: [...(mount.categories ?? [])],
    path: mount.path || null,
    url: mount.url || null,
    role: mount.role,
    authRef: mount.authRef ?? null,
    defaultReadPriority: mount.defaultReadPriority,
    position,
  };
}

function makeDefaultResourceBinding(
  workflow: Workflow,
  resourceNodeId: string,
): WorkflowResourceBinding {
  return {
    id: makeEdgeId(),
    resourceNodeId,
    targetType: 'workflow',
    targetId: workflow.id,
    access: 'read',
    writePrefixes: [],
    readPriority: 10,
  };
}

function syncStagePersonaIds(node: WorkflowStageNode, defaultModelId = ''): WorkflowStageNode {
  const hasExplicitStageMembers = Object.prototype.hasOwnProperty.call(node, 'stageMembers');
  const stageMembers = hasExplicitStageMembers
    ? (node.stageMembers ?? [])
    : (node.personaIds ?? []).map((personaId) => ({
        personaId,
        model: defaultModelId,
        budget: 40,
        consumesEventTypes: [],
        eventFilters: {},
      }));
  const personaIds = stageMembers.map((member) => member.personaId);
  return {
    ...node,
    stageMembers: stageMembers.map((member) => ({
      personaId: member.personaId,
      model: member.model ?? defaultModelId,
      budget: member.budget ?? 40,
      consumesEventTypes: member.consumesEventTypes ?? [],
      eventFilters: member.eventFilters ?? {},
    })),
    executionMode: node.executionMode ?? 'parallel',
    maxConcurrent: node.maxConcurrent ?? 3,
    joinMode: node.joinMode ?? 'all',
    personaIds,
  };
}

function normalizeWorkflowWithStageDefaults(workflow: Workflow, defaultModelId = ''): Workflow {
  return normalizeWorkflowGraph({
    ...workflow,
    nodes: workflow.nodes.map((node) =>
      node.kind === 'stage' ? syncStagePersonaIds(node, defaultModelId) : node,
    ),
  });
}

function defaultStageModelIdForWorkflow(models: WorkflowStageModelOption[]): string {
  return models[0]?.id ?? '';
}

function defaultInputLabelForNode(node: WorkflowNode): string | null {
  switch (node.kind) {
    case 'end':
      return 'complete';
    case 'gate':
      return 'approval.requested';
    case 'cond':
      return 'condition.input';
    case 'trigger':
    case 'stage':
    case 'resource':
      return null;
  }
}

function uniqueStrings(values: string[]): string[] {
  return [...new Set(values.filter(Boolean))];
}

function firstSharedEvent(source: string[], target: string[]): string | null {
  for (const eventType of source) {
    if (target.includes(eventType)) return eventType;
  }
  return null;
}

function splitEdgeLabel(label?: string | null): { source: string; target: string } | null {
  if (!label) return null;
  const parts = label.split('->').map((part) => part.trim());
  if (parts.length !== 2 || !parts[0] || !parts[1]) return null;
  return { source: parts[0], target: parts[1] };
}

function stageEventProfile(
  node: WorkflowStageNode,
  personas: PersonaEntry[],
): { consumes: string[]; produces: string[] } {
  const personaMap = new Map(personas.map((persona) => [persona.id, persona]));
  const members = syncStagePersonaIds(node).stageMembers ?? [];
  return {
    consumes: uniqueStrings(
      members.flatMap((member) => personaMap.get(member.personaId)?.consumes ?? []),
    ),
    produces: uniqueStrings(
      members.flatMap((member) => personaMap.get(member.personaId)?.produces ?? []),
    ),
  };
}

function buildEdge(
  sourceNode: WorkflowNode,
  targetNode: WorkflowNode,
  eventType: string,
): {
  id: string;
  source: string;
  target: string;
  label: string;
  cp1: { x: number; y: number };
  cp2: { x: number; y: number };
} {
  const { cp1, cp2 } = defaultBezierCPs(sourceNode.position, targetNode.position);
  return {
    id: makeEdgeId(),
    source: sourceNode.id,
    target: targetNode.id,
    label: `${eventType} -> ${eventType}`,
    cp1,
    cp2,
  };
}

export function hasMatchingEdge(
  edges: Workflow['edges'],
  sourceId: string,
  targetId: string,
  eventType: string,
): boolean {
  const expected = `${eventType} -> ${eventType}`;
  return edges.some(
    (edge) =>
      edge.source === sourceId && edge.target === targetId && (edge.label ?? '') === expected,
  );
}

function autoWireStageForPersona(
  workflow: Workflow,
  newStage: WorkflowStageNode,
  personas: PersonaEntry[],
): Workflow['edges'] {
  if (personas.length === 0) return [];
  const newProfile = stageEventProfile(newStage, personas);
  const edges: Workflow['edges'] = [];

  for (const node of workflow.nodes) {
    if (node.kind === 'trigger') {
      const triggerEvent = node.dispatchEvent ?? 'code.requested';
      if (
        triggerEvent &&
        newProfile.consumes.includes(triggerEvent) &&
        !hasMatchingEdge(workflow.edges, node.id, newStage.id, triggerEvent)
      ) {
        edges.push(buildEdge(node, newStage, triggerEvent));
      }
      continue;
    }

    if (node.kind !== 'stage') continue;
    const existing = syncStagePersonaIds(node);
    const existingProfile = stageEventProfile(existing, personas);
    const forward = firstSharedEvent(existingProfile.produces, newProfile.consumes);
    const backward = firstSharedEvent(newProfile.produces, existingProfile.consumes);

    if (forward && backward) {
      if (existing.position.x <= newStage.position.x) {
        if (!hasMatchingEdge(workflow.edges, existing.id, newStage.id, forward)) {
          edges.push(buildEdge(existing, newStage, forward));
        }
      } else if (!hasMatchingEdge(workflow.edges, newStage.id, existing.id, backward)) {
        edges.push(buildEdge(newStage, existing, backward));
      }
      continue;
    }

    if (forward && !hasMatchingEdge(workflow.edges, existing.id, newStage.id, forward)) {
      edges.push(buildEdge(existing, newStage, forward));
    }
    if (backward && !hasMatchingEdge(workflow.edges, newStage.id, existing.id, backward)) {
      edges.push(buildEdge(newStage, existing, backward));
    }
  }

  return edges;
}

function rewriteTriggerEdges(
  edges: Workflow['edges'],
  triggerId: string,
  eventType: string,
): Workflow['edges'] {
  return edges.map((edge) => {
    if (edge.source !== triggerId) return edge;
    const parsed = splitEdgeLabel(edge.label);
    if (!parsed) return { ...edge, label: `${eventType} -> ${eventType}` };
    return { ...edge, label: `${eventType} -> ${eventType}` };
  });
}

export function useWorkflowBuilder(
  initial: Workflow,
  personas: PersonaEntry[] = [],
  models: WorkflowStageModelOption[] = [],
): WorkflowBuilderState & WorkflowBuilderActions {
  const defaultStageModelId = defaultStageModelIdForWorkflow(models);
  const [workflow, setWorkflowState] = useState<Workflow>(() =>
    normalizeWorkflowWithStageDefaults(initial, defaultStageModelId),
  );
  const [view, setViewState] = useState<WorkflowView>('graph');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [connectingFromId, setConnectingFromId] = useState<string | null>(null);
  const [connectingFromLabel, setConnectingFromLabel] = useState<string | null>(null);
  const connectingFromRef = useRef<string | null>(null);
  const connectingLabelRef = useRef<string | null>(null);
  const [inspectorNodeId, setInspectorNodeId] = useState<string | null>(null);

  const setView = useCallback((v: WorkflowView) => setViewState(v), []);

  const selectNode = useCallback((id: string | null) => {
    setSelectedNodeId(id);
    connectingFromRef.current = null;
    connectingLabelRef.current = null;
    setConnectingFromId(null);
    setConnectingFromLabel(null);
  }, []);

  const inspectNode = useCallback((id: string | null) => setInspectorNodeId(id), []);

  const setWorkflow = useCallback(
    (w: Workflow) => setWorkflowState(normalizeWorkflowWithStageDefaults(w, defaultStageModelId)),
    [defaultStageModelId],
  );

  const addNode = useCallback(
    (kind: WorkflowNodeKind, position?: { x: number; y: number }) => {
      setWorkflowState((prev) => {
        const pos = position ?? nextPosition(prev);
        const node = makeNewNode(kind, pos);
        return normalizeWorkflowWithStageDefaults(
          { ...prev, nodes: [...prev.nodes, node] },
          defaultStageModelId,
        );
      });
    },
    [defaultStageModelId],
  );

  const addMimirResource = useCallback(
    (mount: WorkflowRegistryMount, position?: { x: number; y: number }) => {
      setWorkflowState((prev) => {
        const pos = position ?? nextPosition(prev);
        const node = makeResourceNodeFromMount(mount, pos);
        return normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            nodes: [...prev.nodes, node],
            resourceBindings: [
              ...(prev.resourceBindings ?? []),
              makeDefaultResourceBinding(prev, node.id),
            ],
          },
          defaultStageModelId,
        );
      });
    },
    [defaultStageModelId],
  );

  const addStageWithPersona = useCallback(
    (personaId: string, model = defaultStageModelId, position?: { x: number; y: number }) => {
      setWorkflowState((prev) => {
        const preferredModelId = defaultStageModelIdForWorkflow(models);
        const pos = position ?? nextPosition(prev);
        const node = makeNewNode('stage', pos);
        const stage =
          node.kind === 'stage'
            ? syncStagePersonaIds(
                {
                  ...node,
                  stageMembers: [
                    {
                      personaId,
                      model: model || preferredModelId,
                      budget: 40,
                      consumesEventTypes: [],
                      eventFilters: {},
                    },
                  ],
                },
                preferredModelId,
              )
            : node;
        const autoEdges =
          stage.kind === 'stage' ? autoWireStageForPersona(prev, stage, personas) : [];
        return normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            nodes: [...prev.nodes, stage],
            edges: [...prev.edges, ...autoEdges],
          },
          preferredModelId,
        );
      });
    },
    [defaultStageModelId, models, personas],
  );

  const deleteNode = useCallback(
    (id: string) => {
      setWorkflowState((prev) =>
        normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            nodes: prev.nodes.filter((n) => n.id !== id),
            edges: prev.edges.filter((e) => e.source !== id && e.target !== id),
            resourceBindings: (prev.resourceBindings ?? []).filter(
              (binding) => binding.resourceNodeId !== id && binding.targetId !== id,
            ),
          },
          defaultStageModelId,
        ),
      );
      setSelectedNodeId((s) => (s === id ? null : s));
      if (connectingFromRef.current === id) {
        connectingFromRef.current = null;
        setConnectingFromId(null);
      }
      setInspectorNodeId((s) => (s === id ? null : s));
    },
    [defaultStageModelId],
  );

  const deleteEdge = useCallback(
    (id: string) => {
      setWorkflowState((prev) =>
        normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            edges: prev.edges.filter((edge) => edge.id !== id),
          },
          defaultStageModelId,
        ),
      );
    },
    [defaultStageModelId],
  );

  const moveNode = useCallback(
    (id: string, position: { x: number; y: number }) => {
      setWorkflowState((prev) =>
        normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            nodes: prev.nodes.map((n) => (n.id === id ? { ...n, position } : n)),
          },
          defaultStageModelId,
        ),
      );
    },
    [defaultStageModelId],
  );

  const startConnect = useCallback((sourceId: string, label?: string) => {
    if (!label) return;
    connectingFromRef.current = sourceId;
    connectingLabelRef.current = label;
    setConnectingFromId(sourceId);
    setConnectingFromLabel(label);
    setSelectedNodeId(sourceId);
  }, []);

  const cancelConnect = useCallback(() => {
    connectingFromRef.current = null;
    connectingLabelRef.current = null;
    setConnectingFromId(null);
    setConnectingFromLabel(null);
  }, []);

  const completeConnect = useCallback(
    (targetId: string, inputLabel?: string) => {
      const fromId = connectingFromRef.current;
      const fromLabel = connectingLabelRef.current;
      connectingFromRef.current = null;
      connectingLabelRef.current = null;
      setConnectingFromId(null);
      setConnectingFromLabel(null);
      if (!fromId || !fromLabel || fromId === targetId) return;
      setWorkflowState((prev) => {
        const srcNode = prev.nodes.find((n) => n.id === fromId);
        const tgtNode = prev.nodes.find((n) => n.id === targetId);
        if (!srcNode || !tgtNode) return prev;
        const resolvedInputLabel = inputLabel ?? defaultInputLabelForNode(tgtNode);
        if (!resolvedInputLabel) return prev;
        const edgeLabel =
          srcNode.kind === 'trigger'
            ? `${resolvedInputLabel} -> ${resolvedInputLabel}`
            : `${fromLabel} -> ${resolvedInputLabel}`;
        const alreadyExists = prev.edges.some(
          (e) => e.source === fromId && e.target === targetId && (e.label ?? '') === edgeLabel,
        );
        if (alreadyExists) return prev;
        const { cp1, cp2 } = defaultBezierCPs(srcNode.position, tgtNode.position);
        const newEdge = {
          id: makeEdgeId(),
          source: fromId,
          target: targetId,
          label: edgeLabel,
          cp1,
          cp2,
        };
        const nodes =
          srcNode.kind === 'trigger'
            ? prev.nodes.map((node) =>
                node.id === srcNode.id ? { ...node, dispatchEvent: resolvedInputLabel } : node,
              )
            : prev.nodes;
        return normalizeWorkflowWithStageDefaults(
          { ...prev, nodes, edges: [...prev.edges, newEdge] },
          defaultStageModelId,
        );
      });
    },
    [defaultStageModelId],
  );

  const addPersonaToStage = useCallback(
    (nodeId: string, personaId: string, model = defaultStageModelId, budget = 40) => {
      setWorkflowState((prev) => {
        const preferredModelId = defaultStageModelIdForWorkflow(models);
        return normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            nodes: prev.nodes.map((n) => {
              if (n.id !== nodeId || n.kind !== 'stage') return n;
              const normalized = syncStagePersonaIds(n, preferredModelId);
              const stageMembers = normalized.stageMembers ?? [];
              if (stageMembers.some((member) => member.personaId === personaId)) return normalized;
              return syncStagePersonaIds(
                {
                  ...normalized,
                  stageMembers: [
                    ...stageMembers,
                    {
                      personaId,
                      model: model || preferredModelId,
                      budget,
                      consumesEventTypes: [],
                      eventFilters: {},
                    },
                  ],
                },
                preferredModelId,
              );
            }),
          },
          preferredModelId,
        );
      });
    },
    [defaultStageModelId, models],
  );

  const replacePersonaInStage = useCallback(
    (nodeId: string, previousPersonaId: string, personaId: string, model?: string) => {
      setWorkflowState((prev) =>
        normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            nodes: prev.nodes.map((n) => {
              if (n.id !== nodeId || n.kind !== 'stage') return n;
              const normalized = syncStagePersonaIds(n, defaultStageModelId);
              const stageMembers = normalized.stageMembers ?? [];
              const existing = stageMembers.find(
                (member) => member.personaId === previousPersonaId,
              );
              return syncStagePersonaIds(
                {
                  ...normalized,
                  stageMembers: stageMembers.map((member) =>
                    member.personaId === previousPersonaId
                      ? {
                          ...member,
                          personaId,
                          model: model ?? existing?.model ?? defaultStageModelId,
                        }
                      : member,
                  ),
                },
                defaultStageModelId,
              );
            }),
          },
          defaultStageModelId,
        ),
      );
    },
    [defaultStageModelId],
  );

  const updatePersonaModel = useCallback(
    (nodeId: string, personaId: string, model: string) => {
      setWorkflowState((prev) =>
        normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            nodes: prev.nodes.map((n) => {
              if (n.id !== nodeId || n.kind !== 'stage') return n;
              const normalized = syncStagePersonaIds(n, defaultStageModelId);
              const stageMembers = normalized.stageMembers ?? [];
              return syncStagePersonaIds(
                {
                  ...normalized,
                  stageMembers: stageMembers.map((member) =>
                    member.personaId === personaId ? { ...member, model } : member,
                  ),
                },
                defaultStageModelId,
              );
            }),
          },
          defaultStageModelId,
        ),
      );
    },
    [defaultStageModelId],
  );

  const updatePersonaBudget = useCallback(
    (nodeId: string, personaId: string, budget: number) => {
      setWorkflowState((prev) =>
        normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            nodes: prev.nodes.map((n) => {
              if (n.id !== nodeId || n.kind !== 'stage') return n;
              const normalized = syncStagePersonaIds(n, defaultStageModelId);
              const stageMembers = normalized.stageMembers ?? [];
              return syncStagePersonaIds(
                {
                  ...normalized,
                  stageMembers: stageMembers.map((member) =>
                    member.personaId === personaId ? { ...member, budget } : member,
                  ),
                },
                defaultStageModelId,
              );
            }),
          },
          defaultStageModelId,
        ),
      );
    },
    [defaultStageModelId],
  );

  const removePersonaFromStage = useCallback(
    (nodeId: string, personaId: string) => {
      setWorkflowState((prev) =>
        normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            nodes: prev.nodes.map((n) => {
              if (n.id !== nodeId || n.kind !== 'stage') return n;
              const normalized = syncStagePersonaIds(n, defaultStageModelId);
              const stageMembers = normalized.stageMembers ?? [];
              return syncStagePersonaIds(
                {
                  ...normalized,
                  stageMembers: stageMembers.filter((member) => member.personaId !== personaId),
                },
                defaultStageModelId,
              );
            }),
          },
          defaultStageModelId,
        ),
      );
    },
    [defaultStageModelId],
  );

  const updateNodeLabel = useCallback(
    (id: string, label: string) => {
      setWorkflowState((prev) =>
        normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            nodes: prev.nodes.map((n) => (n.id === id ? { ...n, label } : n)),
          },
          defaultStageModelId,
        ),
      );
    },
    [defaultStageModelId],
  );

  const updateNode = useCallback(
    (id: string, patch: Partial<WorkflowNode>) => {
      setWorkflowState((prev) => {
        const current = prev.nodes.find((node) => node.id === id);
        const triggerDispatchEvent =
          current?.kind === 'trigger' &&
          'dispatchEvent' in patch &&
          typeof patch.dispatchEvent === 'string' &&
          patch.dispatchEvent
            ? patch.dispatchEvent
            : null;
        const nextNodes = prev.nodes.map((n) => {
          if (n.id !== id) return n;
          const next = { ...n, ...patch } as WorkflowNode;
          return next.kind === 'stage' ? syncStagePersonaIds(next, defaultStageModelId) : next;
        });
        const nextEdges = triggerDispatchEvent
          ? rewriteTriggerEdges(prev.edges, id, triggerDispatchEvent)
          : prev.edges;
        return normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            nodes: nextNodes,
            edges: nextEdges,
          },
          defaultStageModelId,
        );
      });
    },
    [defaultStageModelId],
  );

  const addResourceBinding = useCallback(
    (
      resourceNodeId: string,
      patch: Partial<Omit<WorkflowResourceBinding, 'id' | 'resourceNodeId'>> = {},
    ) => {
      setWorkflowState((prev) =>
        normalizeWorkflowGraph({
          ...prev,
          resourceBindings: [
            ...(prev.resourceBindings ?? []),
            {
              ...makeDefaultResourceBinding(prev, resourceNodeId),
              ...patch,
              id: makeEdgeId(),
              resourceNodeId,
            },
          ],
        }),
      );
    },
    [],
  );

  const updateResourceBinding = useCallback(
    (id: string, patch: Partial<WorkflowResourceBinding>) => {
      setWorkflowState((prev) =>
        normalizeWorkflowGraph({
          ...prev,
          resourceBindings: (prev.resourceBindings ?? []).map((binding) =>
            binding.id === id ? { ...binding, ...patch, id: binding.id } : binding,
          ),
        }),
      );
    },
    [],
  );

  const removeResourceBinding = useCallback((id: string) => {
    setWorkflowState((prev) =>
      normalizeWorkflowGraph({
        ...prev,
        resourceBindings: (prev.resourceBindings ?? []).filter((binding) => binding.id !== id),
      }),
    );
  }, []);

  const updateWorkflowMeta = useCallback(
    (patch: Partial<Pick<Workflow, 'name' | 'description' | 'version' | 'tags'>>) => {
      setWorkflowState((prev) =>
        normalizeWorkflowWithStageDefaults(
          { ...prev, ...patch },
          defaultStageModelIdForWorkflow(models),
        ),
      );
    },
    [models],
  );

  return {
    workflow,
    view,
    selectedNodeId,
    connectingFromId,
    connectingFromLabel,
    inspectorNodeId,
    setView,
    selectNode,
    inspectNode,
    addNode,
    addMimirResource,
    addStageWithPersona,
    deleteNode,
    deleteEdge,
    moveNode,
    startConnect,
    cancelConnect,
    completeConnect,
    addPersonaToStage,
    replacePersonaInStage,
    updatePersonaModel,
    updatePersonaBudget,
    removePersonaFromStage,
    updateNodeLabel,
    updateNode,
    addResourceBinding,
    updateResourceBinding,
    removeResourceBinding,
    updateWorkflowMeta,
    setWorkflow,
  };
}
