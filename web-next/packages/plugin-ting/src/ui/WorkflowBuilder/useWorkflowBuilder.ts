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
import { layoutWorkflow } from '../../domain/workflowLayout';
import { estimateWorkflowNodeSize } from '../../domain/workflowGeometry';
import {
  addSubworkflowTemplate as addSubworkflowTemplateToWorkflow,
  bindSubworkflowTemplate,
  removeSubworkflowTemplate as removeSubworkflowTemplateFromWorkflow,
  renameSubworkflowTemplate as renameSubworkflowTemplateOnWorkflow,
} from '../../domain/workflowDependencies';

export type WorkflowView = 'graph' | 'pipeline' | 'yaml';

export interface WorkflowStageModelOption {
  id: string;
  label: string;
  vendor?: string;
}

export interface WorkflowBuilderState {
  workflow: Workflow;
  /** Persisted baseline used for dirty comparison and draft restoration. */
  savedWorkflow: Workflow;
  isDirty: boolean;
  view: WorkflowView;
  selectedNodeId: string | null;
  /** When non-null, we're in "connect" mode — next node click completes the edge. */
  connectingFromId: string | null;
  connectingFromLabel: string | null;
  /** When non-null, the NodeInspector Dialog is open for this node. */
  inspectorNodeId: string | null;
  canUndo: boolean;
  canRedo: boolean;
}

export interface WorkflowBuilderActions {
  setView(v: WorkflowView): void;
  selectNode(id: string | null): void;
  inspectNode(id: string | null): void;
  addNode(kind: WorkflowNodeKind, position?: { x: number; y: number }): void;
  /** Insert a non-persona node wired from an existing node's typed output. */
  addNodeFromPort(
    kind: WorkflowNodeKind,
    sourceId: string,
    eventType: string,
    position?: { x: number; y: number },
  ): void;
  addMimirResource(mount: WorkflowRegistryMount, position?: { x: number; y: number }): void;
  addStageWithPersona(personaId: string, model?: string, position?: { x: number; y: number }): void;
  /** Add one persona stage and only the exact connection requested from a source port. */
  addStageFromPort(
    sourceId: string,
    eventType: string,
    personaId: string,
    position?: { x: number; y: number },
    model?: string,
  ): void;
  deleteNode(id: string): void;
  deleteEdge(id: string): void;
  moveNode(id: string, position: { x: number; y: number }): void;
  /** Reposition every node via `layoutWorkflow`, as one undo step. */
  autoLayout(): void;
  /** Step back to the previous committed workflow state. No-op at the start of history. */
  undo(): void;
  /** Step forward again after an undo. No-op with nothing to redo. */
  redo(): void;
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
  /** Select and exactly pin one of a subworkflow node's templates to a child
   *  workflow, as one undoable mutation. */
  selectSubworkflowTemplate(nodeId: string, templateName: string, child: Workflow): void;
  /** Offer another child workflow on a subworkflow node under a new template
   *  name (auto-generated when omitted); repoint it with
   *  `selectSubworkflowTemplate`. */
  addSubworkflowTemplate(nodeId: string, name?: string): void;
  /** Rename one of a subworkflow node's templates, keeping any static
   *  `children[].template` reference to it consistent. */
  renameSubworkflowTemplate(nodeId: string, previousName: string, nextName: string): void;
  /** Remove one of a subworkflow node's templates. Refused when it is the
   *  node's last template or a declared static child still names it. */
  removeSubworkflowTemplate(nodeId: string, name: string): void;
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
  /** Replace the active document and clear edit history/dirty state. */
  resetWorkflow(workflow: Workflow): void;
  /** Discard the draft and restore the latest successfully persisted document. */
  discardChanges(): void;
  /**
   * Rebase the editor on a successfully persisted immutable version.
   * Returns true when edits made while the save was pending remain dirty.
   */
  markSaved(savedWorkflow?: Workflow, submittedWorkflow?: Workflow): boolean;
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
    case 'subworkflow':
      return {
        id,
        kind,
        label: 'Child workflows',
        position,
        templates: { default: '' },
        allowedCoordinator: '',
        inputSchema: { type: 'object', properties: {} },
        resultSchema: { type: 'object', properties: {} },
        maxChildren: 10,
        maxAttempts: 3,
        maxActiveChildren: 4,
        joinMode: 'all',
        blockedEvent: 'children.blocked',
      };
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
    case 'wait':
      return { id, kind: 'wait', label: 'Wait for observation', position };
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
    adapter: mount.adapter ?? '',
    kwargs: mount.kwargs ?? {},
    secretKwargsEnv: mount.secretKwargsEnv ?? {},
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
    case 'subworkflow':
      return 'children.requested';
    case 'end':
      return 'complete';
    case 'gate':
      return 'approval.requested';
    case 'cond':
      return 'condition.input';
    case 'trigger':
    case 'stage':
    case 'resource':
    case 'wait':
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
  initialSavedWorkflow: Workflow = initial,
): WorkflowBuilderState & WorkflowBuilderActions {
  const defaultStageModelId = defaultStageModelIdForWorkflow(models);
  const initialWorkflow = useState<Workflow>(() =>
    normalizeWorkflowWithStageDefaults(initial, defaultStageModelId),
  )[0];
  const savedInitialWorkflow = useState<Workflow>(() =>
    normalizeWorkflowWithStageDefaults(initialSavedWorkflow, defaultStageModelId),
  )[0];
  const initiallyDirty = JSON.stringify(initialWorkflow) !== JSON.stringify(savedInitialWorkflow);
  const [workflow, setWorkflow_] = useState<Workflow>(initialWorkflow);
  const [savedWorkflow, setSavedWorkflow] = useState<Workflow>(savedInitialWorkflow);

  // Undo/redo history. `workflowRef` is the synchronous source of truth for
  // `commit()` — reading `workflow` from render scope would be one render
  // stale for the second of two commits in the same tick (e.g. double-click).
  // `historyRef` is a plain mutable stack, not state: it's written by
  // `commit`, `undo`, and `redo`, all ordinary functions called from event
  // handlers, never from inside a state updater (which React/StrictMode may
  // invoke more than once — mutating a ref there would double-push) or an
  // effect (which would make this derived-state-via-effect, exactly the
  // anti-pattern removed from the detail-panel logic above).
  const workflowRef = useRef<Workflow>(initialWorkflow);
  const savedWorkflowRef = useRef<Workflow>(savedInitialWorkflow);
  const historyRef = useRef<{ stack: Workflow[]; index: number }>({
    stack: initiallyDirty ? [savedInitialWorkflow, initialWorkflow] : [initialWorkflow],
    index: initiallyDirty ? 1 : 0,
  });
  const [historyFlags, setHistoryFlags] = useState({ canUndo: initiallyDirty, canRedo: false });
  const [isDirty, setIsDirty] = useState(initiallyDirty);

  /** Apply `updater` to the latest workflow, as one undo step. Replaces the
   *  raw `setState` setter everywhere in this file so every mutation is
   *  captured in history uniformly. */
  const commit = useCallback((updater: (prev: Workflow) => Workflow) => {
    const current = workflowRef.current;
    const next = updater(current);
    if (next === current || JSON.stringify(next) === JSON.stringify(current)) return;
    workflowRef.current = next;
    const h = historyRef.current;
    h.stack = [...h.stack.slice(0, h.index + 1), next];
    h.index = h.stack.length - 1;
    setHistoryFlags({ canUndo: h.index > 0, canRedo: false });
    setIsDirty(JSON.stringify(next) !== JSON.stringify(savedWorkflowRef.current));
    setWorkflow_(next);
  }, []);

  const undo = useCallback(() => {
    const h = historyRef.current;
    if (h.index <= 0) return;
    h.index -= 1;
    const next = h.stack[h.index]!;
    workflowRef.current = next;
    setHistoryFlags({ canUndo: h.index > 0, canRedo: true });
    setIsDirty(JSON.stringify(next) !== JSON.stringify(savedWorkflowRef.current));
    setWorkflow_(next);
  }, []);

  const redo = useCallback(() => {
    const h = historyRef.current;
    if (h.index >= h.stack.length - 1) return;
    h.index += 1;
    const next = h.stack[h.index]!;
    workflowRef.current = next;
    setHistoryFlags({ canUndo: true, canRedo: h.index < h.stack.length - 1 });
    setIsDirty(JSON.stringify(next) !== JSON.stringify(savedWorkflowRef.current));
    setWorkflow_(next);
  }, []);

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
    (w: Workflow) => commit(() => normalizeWorkflowWithStageDefaults(w, defaultStageModelId)),
    [commit, defaultStageModelId],
  );

  const resetWorkflow = useCallback(
    (nextWorkflow: Workflow) => {
      const next = normalizeWorkflowWithStageDefaults(nextWorkflow, defaultStageModelId);
      workflowRef.current = next;
      savedWorkflowRef.current = next;
      historyRef.current = { stack: [next], index: 0 };
      setWorkflow_(next);
      setSavedWorkflow(next);
      setHistoryFlags({ canUndo: false, canRedo: false });
      setIsDirty(false);
      setSelectedNodeId(null);
      setInspectorNodeId(null);
      connectingFromRef.current = null;
      connectingLabelRef.current = null;
      setConnectingFromId(null);
      setConnectingFromLabel(null);
    },
    [defaultStageModelId],
  );

  const markSaved = useCallback(
    (
      savedWorkflow: Workflow = workflowRef.current,
      submittedWorkflow: Workflow = savedWorkflow,
    ) => {
      const saved = normalizeWorkflowWithStageDefaults(savedWorkflow, defaultStageModelId);
      const submitted = normalizeWorkflowWithStageDefaults(submittedWorkflow, defaultStageModelId);
      const changedWhileSaving = JSON.stringify(workflowRef.current) !== JSON.stringify(submitted);
      savedWorkflowRef.current = saved;
      setSavedWorkflow(saved);
      const persistedMeta: Partial<Workflow> = {
        version: saved.version,
        revision: saved.revision,
        documentRevision: saved.documentRevision,
        isHead: saved.isHead,
        origin: saved.origin,
        canEdit: saved.canEdit,
        readOnly: saved.readOnly,
        canonicalYaml: saved.canonicalYaml,
      };
      if (!changedWhileSaving) {
        workflowRef.current = saved;
        const history = historyRef.current;
        const stack = history.stack.map((entry) => ({ ...entry, ...persistedMeta }));
        stack[history.index] = saved;
        historyRef.current = { ...history, stack };
        setWorkflow_(saved);
        setHistoryFlags({
          canUndo: history.index > 0,
          canRedo: history.index < stack.length - 1,
        });
        setIsDirty(false);
        return false;
      }

      const rebased = normalizeWorkflowWithStageDefaults(
        { ...workflowRef.current, ...persistedMeta },
        defaultStageModelId,
      );
      const history = historyRef.current;
      historyRef.current = {
        ...history,
        stack: history.stack.map((entry) => ({ ...entry, ...persistedMeta })),
      };
      workflowRef.current = rebased;
      setWorkflow_(rebased);
      setIsDirty(true);
      return true;
    },
    [defaultStageModelId],
  );

  const discardChanges = useCallback(() => {
    resetWorkflow(savedWorkflowRef.current);
  }, [resetWorkflow]);

  const addNode = useCallback(
    (kind: WorkflowNodeKind, position?: { x: number; y: number }) => {
      commit((prev) => {
        const pos = position ?? nextPosition(prev);
        const node = makeNewNode(kind, pos);
        return normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            ...(kind === 'wait' ? { schemaVersion: 2 as const } : {}),
            nodes: [...prev.nodes, node],
          },
          defaultStageModelId,
        );
      });
    },
    [commit, defaultStageModelId],
  );

  /**
   * Insert a flow-control node (gate/cond/wait/end — anything that isn't
   * persona-shaped) wired from an existing node's typed output in one step.
   * The persona equivalent is `addStageWithPersona`, whose auto-wire this
   * mirrors directly; unlike that one, there's no produces/consumes profile
   * to match against, so the caller supplies the exact source and event
   * type (this is what backs InsertFromPortMenu's flow-control candidates).
   */
  const addNodeFromPort = useCallback(
    (
      kind: WorkflowNodeKind,
      sourceId: string,
      eventType: string,
      position?: { x: number; y: number },
    ) => {
      commit((prev) => {
        const sourceNode = prev.nodes.find((n) => n.id === sourceId);
        if (!sourceNode || !eventType.trim()) return prev;
        const pos = position ?? nextPosition(prev);
        const node = makeNewNode(kind, pos);
        const edges = [...prev.edges, buildEdge(sourceNode, node, eventType)];
        return normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            ...(kind === 'wait' ? { schemaVersion: 2 as const } : {}),
            nodes: [...prev.nodes, node],
            edges,
          },
          defaultStageModelId,
        );
      });
    },
    [commit, defaultStageModelId],
  );

  const addMimirResource = useCallback(
    (mount: WorkflowRegistryMount, position?: { x: number; y: number }) => {
      commit((prev) => {
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
    [commit, defaultStageModelId],
  );

  const addStageWithPersona = useCallback(
    (personaId: string, model = defaultStageModelId, position?: { x: number; y: number }) => {
      commit((prev) => {
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
    [commit, defaultStageModelId, models, personas],
  );

  const addStageFromPort = useCallback(
    (
      sourceId: string,
      eventType: string,
      personaId: string,
      position?: { x: number; y: number },
      model = defaultStageModelId,
    ) => {
      commit((prev) => {
        const sourceNode = prev.nodes.find((node) => node.id === sourceId);
        const persona = personas.find((entry) => entry.id === personaId);
        const normalizedEvent = eventType.trim();
        if (!sourceNode || !persona || !normalizedEvent) return prev;
        const preferredModelId = defaultStageModelIdForWorkflow(models);
        const candidate = makeNewNode('stage', position ?? nextPosition(prev));
        if (candidate.kind !== 'stage') return prev;
        const stage = syncStagePersonaIds(
          {
            ...candidate,
            stageMembers: [
              {
                personaId,
                model: model || preferredModelId,
                budget: 40,
                consumesEventTypes: [normalizedEvent],
                eventFilters: {},
              },
            ],
          },
          preferredModelId,
        );
        return normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            nodes: [...prev.nodes, stage],
            edges: [...prev.edges, buildEdge(sourceNode, stage, normalizedEvent)],
          },
          preferredModelId,
        );
      });
    },
    [commit, defaultStageModelId, models, personas],
  );

  const deleteNode = useCallback(
    (id: string) => {
      commit((prev) => {
        const deletedNode = prev.nodes.find((node) => node.id === id);
        if (!deletedNode) return prev;
        const nodes = prev.nodes.filter((node) => node.id !== id);
        const workflowDependencies = { ...(prev.workflowDependencies ?? {}) };
        if (deletedNode.kind === 'subworkflow') {
          const remainingAliases = new Set(
            nodes.flatMap((node) =>
              node.kind === 'subworkflow' ? Object.values(node.templates ?? {}) : [],
            ),
          );
          for (const alias of Object.values(deletedNode.templates ?? {})) {
            if (alias && !remainingAliases.has(alias)) {
              delete workflowDependencies[alias];
            }
          }
        }
        return normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            nodes,
            edges: prev.edges.filter((e) => e.source !== id && e.target !== id),
            resourceBindings: (prev.resourceBindings ?? []).filter(
              (binding) => binding.resourceNodeId !== id && binding.targetId !== id,
            ),
            workflowDependencies,
          },
          defaultStageModelId,
        );
      });
      setSelectedNodeId((s) => (s === id ? null : s));
      if (connectingFromRef.current === id) {
        connectingFromRef.current = null;
        setConnectingFromId(null);
      }
      setInspectorNodeId((s) => (s === id ? null : s));
    },
    [commit, defaultStageModelId],
  );

  const deleteEdge = useCallback(
    (id: string) => {
      commit((prev) => {
        if (!prev.edges.some((edge) => edge.id === id)) return prev;
        return normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            edges: prev.edges.filter((edge) => edge.id !== id),
          },
          defaultStageModelId,
        );
      });
    },
    [commit, defaultStageModelId],
  );

  const moveNode = useCallback(
    (id: string, position: { x: number; y: number }) => {
      commit((prev) => {
        const current = prev.nodes.find((node) => node.id === id);
        if (!current) return prev;
        if (current.position.x === position.x && current.position.y === position.y) return prev;
        return normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            nodes: prev.nodes.map((n) => (n.id === id ? { ...n, position } : n)),
          },
          defaultStageModelId,
        );
      });
    },
    [commit, defaultStageModelId],
  );

  /**
   * Reposition every node with `layoutWorkflow` — a single state update
   * (not N `moveNode` calls) so it's one undo step. Positions currently
   * outside the canvas viewport (a node dragged off-screen, or one restored
   * from a YAML document that never carried a `position:` at all) are the
   * main case this fixes.
   */
  const autoLayout = useCallback(() => {
    commit((prev) => {
      const { positions } = layoutWorkflow(
        prev.nodes.map((n) => n.id),
        prev.edges,
        {
          nodeSizes: new Map(
            prev.nodes.map((node) => [
              node.id,
              estimateWorkflowNodeSize(node, { personas, edges: prev.edges }),
            ]),
          ),
          feedbackLaneSpacing: 0,
        },
      );
      return normalizeWorkflowWithStageDefaults(
        {
          ...prev,
          nodes: prev.nodes.map((n) => ({ ...n, position: positions.get(n.id) ?? n.position })),
        },
        defaultStageModelId,
      );
    });
  }, [commit, defaultStageModelId, personas]);

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
      commit((prev) => {
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
    [commit, defaultStageModelId],
  );

  const addPersonaToStage = useCallback(
    (nodeId: string, personaId: string, model = defaultStageModelId, budget = 40) => {
      commit((prev) => {
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
    [commit, defaultStageModelId, models],
  );

  const replacePersonaInStage = useCallback(
    (nodeId: string, previousPersonaId: string, personaId: string, model?: string) => {
      commit((prev) =>
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
    [commit, defaultStageModelId],
  );

  const updatePersonaModel = useCallback(
    (nodeId: string, personaId: string, model: string) => {
      commit((prev) =>
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
    [commit, defaultStageModelId],
  );

  const updatePersonaBudget = useCallback(
    (nodeId: string, personaId: string, budget: number) => {
      commit((prev) =>
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
    [commit, defaultStageModelId],
  );

  const removePersonaFromStage = useCallback(
    (nodeId: string, personaId: string) => {
      commit((prev) =>
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
    [commit, defaultStageModelId],
  );

  const updateNodeLabel = useCallback(
    (id: string, label: string) => {
      commit((prev) =>
        normalizeWorkflowWithStageDefaults(
          {
            ...prev,
            nodes: prev.nodes.map((n) => (n.id === id ? { ...n, label } : n)),
          },
          defaultStageModelId,
        ),
      );
    },
    [commit, defaultStageModelId],
  );

  const updateNode = useCallback(
    (id: string, patch: Partial<WorkflowNode>) => {
      commit((prev) => {
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
    [commit, defaultStageModelId],
  );

  const selectSubworkflowTemplate = useCallback(
    (nodeId: string, templateName: string, child: Workflow) => {
      commit((prev) => bindSubworkflowTemplate(prev, nodeId, templateName, child));
    },
    [commit],
  );

  const addSubworkflowTemplate = useCallback(
    (nodeId: string, name?: string) => {
      commit((prev) => addSubworkflowTemplateToWorkflow(prev, nodeId, name));
    },
    [commit],
  );

  const renameSubworkflowTemplate = useCallback(
    (nodeId: string, previousName: string, nextName: string) => {
      commit((prev) => renameSubworkflowTemplateOnWorkflow(prev, nodeId, previousName, nextName));
    },
    [commit],
  );

  const removeSubworkflowTemplate = useCallback(
    (nodeId: string, name: string) => {
      commit((prev) => removeSubworkflowTemplateFromWorkflow(prev, nodeId, name));
    },
    [commit],
  );

  const addResourceBinding = useCallback(
    (
      resourceNodeId: string,
      patch: Partial<Omit<WorkflowResourceBinding, 'id' | 'resourceNodeId'>> = {},
    ) => {
      commit((prev) =>
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
    [commit],
  );

  const updateResourceBinding = useCallback(
    (id: string, patch: Partial<WorkflowResourceBinding>) => {
      commit((prev) =>
        normalizeWorkflowGraph({
          ...prev,
          resourceBindings: (prev.resourceBindings ?? []).map((binding) =>
            binding.id === id ? { ...binding, ...patch, id: binding.id } : binding,
          ),
        }),
      );
    },
    [commit],
  );

  const removeResourceBinding = useCallback(
    (id: string) => {
      commit((prev) =>
        normalizeWorkflowGraph({
          ...prev,
          resourceBindings: (prev.resourceBindings ?? []).filter((binding) => binding.id !== id),
        }),
      );
    },
    [commit],
  );

  const updateWorkflowMeta = useCallback(
    (patch: Partial<Pick<Workflow, 'name' | 'description' | 'version' | 'tags'>>) => {
      commit((prev) =>
        normalizeWorkflowWithStageDefaults(
          { ...prev, ...patch },
          defaultStageModelIdForWorkflow(models),
        ),
      );
    },
    [commit, models],
  );

  return {
    workflow,
    savedWorkflow,
    isDirty,
    view,
    selectedNodeId,
    connectingFromId,
    connectingFromLabel,
    inspectorNodeId,
    canUndo: historyFlags.canUndo,
    canRedo: historyFlags.canRedo,
    setView,
    selectNode,
    inspectNode,
    addNode,
    addNodeFromPort,
    addMimirResource,
    addStageWithPersona,
    addStageFromPort,
    deleteNode,
    deleteEdge,
    moveNode,
    autoLayout,
    undo,
    redo,
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
    selectSubworkflowTemplate,
    addSubworkflowTemplate,
    renameSubworkflowTemplate,
    removeSubworkflowTemplate,
    addResourceBinding,
    updateResourceBinding,
    removeResourceBinding,
    updateWorkflowMeta,
    setWorkflow,
    resetWorkflow,
    discardChanges,
    markSaved,
  };
}
