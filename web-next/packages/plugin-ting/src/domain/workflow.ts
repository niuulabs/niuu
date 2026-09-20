import { z } from 'zod';

/**
 * Workflow — a DAG value object representing a structured execution plan.
 *
 * Node kinds include:
 *   stage  — a unit of work (maps to a Run)
 *   gate   — a checkpoint requiring human or automated approval before proceeding
 *   cond   — a conditional branch that routes execution based on a predicate
 *   wait   — a passive boundary resumed by an external observation
 *
 * Edges use cubic bezier curves for UI rendering (control-point pairs cp1/cp2).
 *
 * Owner: plugin-ting.
 */

// ---------------------------------------------------------------------------
// Nodes
// ---------------------------------------------------------------------------

export const workflowNodeKindSchema = z.enum([
  'stage',
  'gate',
  'cond',
  'trigger',
  'end',
  'resource',
  'subworkflow',
  'wait',
]);
export type WorkflowNodeKind = z.input<typeof workflowNodeKindSchema>;

/** Spatial position for UI rendering (pixels from top-left). */
const positionSchema = z.object({ x: z.number(), y: z.number() });
const stageExecutionModeSchema = z.enum(['parallel', 'sequential']);
const stageJoinModeSchema = z.enum(['all', 'any', 'merge']);
const gateModeSchema = z.enum(['human_approval', 'human_review', 'automated_approval', 'evidence']);
const gatePendingBehaviorSchema = z.enum(['silent', 'notify_only', 'help_needed']);
const stageEventFiltersSchema = z.record(z.string(), z.string()).default({});
const stageMemberSchema = z.object({
  personaId: z.string().min(1),
  model: z.string().default(''),
  budget: z.number().int().nonnegative().default(40),
  consumesEventTypes: z.array(z.string()).default([]),
  eventFilters: stageEventFiltersSchema,
});
export type StageExecutionMode = z.input<typeof stageExecutionModeSchema>;
export type StageJoinMode = z.input<typeof stageJoinModeSchema>;
export type WorkflowGateMode = z.input<typeof gateModeSchema>;
export type WorkflowGatePendingBehavior = z.input<typeof gatePendingBehaviorSchema>;
export type WorkflowStageMember = z.input<typeof stageMemberSchema>;

export const workflowStageNodeSchema = z.object({
  id: z.string().min(1),
  kind: z.literal('stage'),
  label: z.string().min(1),
  /** Optional reference to the Run this stage maps to. */
  runId: z.string().nullable(),
  /** Persona IDs assigned to this stage (drives missing_persona validation). */
  personaIds: z.array(z.string()).default([]),
  /** Rich stage membership used by the workflow editor. */
  stageMembers: z.array(stageMemberSchema).default([]),
  /** Parallel or sequential execution for the stage flock. */
  executionMode: stageExecutionModeSchema.default('parallel'),
  /** Maximum number of concurrent workers allowed in the stage. */
  maxConcurrent: z.number().int().positive().default(3),
  /** How inbound branches join when this stage has fan-in. */
  joinMode: stageJoinModeSchema.default('all'),
  position: positionSchema,
});
export type WorkflowStageNode = z.input<typeof workflowStageNodeSchema>;

export const workflowGateNodeSchema = z.object({
  id: z.string().min(1),
  kind: z.literal('gate'),
  label: z.string().min(1),
  /** Human-readable approval condition description. */
  condition: z.string(),
  /** Gate execution mode for runtime and UI semantics. */
  mode: gateModeSchema.default('human_approval'),
  /** Declarative receipt requirements; verifier trust is deployment configuration. */
  evidencePolicy: z.record(z.string(), z.unknown()).optional(),
  /** Exact artifact whose identity binds every receipt evaluated by an evidence gate. */
  artifact: z.object({ kind: z.string().min(1), id: z.string().min(1) }).optional(),
  /** How the runtime surfaces the gate while it is pending. */
  pendingBehavior: gatePendingBehaviorSchema.default('help_needed'),
  /** Optional explicit approval event type override. */
  approvalEvent: z.string().default(''),
  /** Optional explicit changes-requested event type override. */
  changesRequestedEvent: z.string().default(''),
  /** Optional operator guidance shown with the gate request. */
  instructions: z.string().default(''),
  autoForwardAfter: z.string().default('30m'),
  position: positionSchema,
});
export type WorkflowGateNode = z.input<typeof workflowGateNodeSchema>;

export const workflowCondNodeSchema = z.object({
  id: z.string().min(1),
  kind: z.literal('cond'),
  label: z.string().min(1),
  /** Predicate expression evaluated at runtime. */
  predicate: z.string(),
  position: positionSchema,
});
export type WorkflowCondNode = z.input<typeof workflowCondNodeSchema>;

export const workflowTriggerNodeSchema = z.object({
  id: z.string().min(1),
  kind: z.literal('trigger'),
  label: z.string().min(1),
  source: z.string().default('manual dispatch'),
  dispatchEvent: z.string().default('code.requested'),
  position: positionSchema,
});
export type WorkflowTriggerNode = z.input<typeof workflowTriggerNodeSchema>;

export const workflowEndNodeSchema = z.object({
  id: z.string().min(1),
  kind: z.literal('end'),
  label: z.string().min(1),
  position: positionSchema,
});
export type WorkflowEndNode = z.input<typeof workflowEndNodeSchema>;

const workflowResourceBindingModeSchema = z.enum(['registry', 'ephemeral_local']);
const workflowResourceAccessSchema = z.enum(['read', 'write', 'read_write']);
const workflowResourceTargetTypeSchema = z.enum(['workflow', 'stage', 'persona']);

export const workflowResourceNodeSchema = z.object({
  id: z.string().min(1),
  kind: z.literal('resource'),
  label: z.string().min(1),
  resourceType: z.literal('mimir').default('mimir'),
  bindingMode: workflowResourceBindingModeSchema.default('registry'),
  registryEntryId: z.string().nullable().default(null),
  seedFromRegistryId: z.string().nullable().default(null),
  categories: z.array(z.string()).default([]),
  path: z.string().nullable().optional(),
  url: z.string().nullable().optional(),
  role: z.string().nullable().optional(),
  adapter: z.string().optional(),
  kwargs: z.record(z.string(), z.unknown()).optional(),
  secretKwargsEnv: z.record(z.string(), z.string()).optional(),
  authRef: z.string().nullable().optional(),
  defaultReadPriority: z.number().int().optional(),
  position: positionSchema,
});
export type WorkflowResourceNode = z.input<typeof workflowResourceNodeSchema>;

export const workflowSubworkflowNodeSchema = z.object({
  id: z.string().min(1),
  kind: z.literal('subworkflow'),
  label: z.string().min(1),
  position: positionSchema,
  workflowDependency: z.string().min(1),
  allowedCoordinator: z.string().min(1),
  inputSchema: z.record(z.string(), z.unknown()),
  resultSchema: z.record(z.string(), z.unknown()),
  maxChildren: z.number().int().positive(),
  maxAttempts: z.number().int().positive(),
  maxActiveChildren: z.number().int().positive().optional(),
  joinMode: z.literal('all'),
});
export type WorkflowSubworkflowNode = z.input<typeof workflowSubworkflowNodeSchema>;

export const workflowWaitNodeSchema = z.object({
  id: z.string().min(1),
  kind: z.literal('wait'),
  label: z.string().min(1),
  /** Passive waits never execute personas. */
  personaIds: z.never().optional(),
  stageMembers: z.never().optional(),
  position: positionSchema,
});
export type WorkflowWaitNode = z.input<typeof workflowWaitNodeSchema>;

export const workflowNodeSchema = z.discriminatedUnion('kind', [
  workflowSubworkflowNodeSchema,
  workflowWaitNodeSchema,
  workflowStageNodeSchema,
  workflowGateNodeSchema,
  workflowCondNodeSchema,
  workflowTriggerNodeSchema,
  workflowEndNodeSchema,
  workflowResourceNodeSchema,
]);
export type WorkflowNode = z.input<typeof workflowNodeSchema>;

// ---------------------------------------------------------------------------
// Edges (bezier curves)
// ---------------------------------------------------------------------------

export const workflowEdgeSchema = z.object({
  id: z.string().min(1),
  /** Source node id. */
  source: z.string().min(1),
  /** Target node id. */
  target: z.string().min(1),
  /** Optional edge label (e.g. "yes" / "no" on cond nodes). */
  label: z.string().optional(),
  /** First bezier control point (relative to source). */
  cp1: positionSchema,
  /** Second bezier control point (relative to target). */
  cp2: positionSchema,
});
export type WorkflowEdge = z.input<typeof workflowEdgeSchema>;

export const workflowResourceBindingSchema = z.object({
  id: z.string().min(1),
  resourceNodeId: z.string().min(1),
  targetType: workflowResourceTargetTypeSchema,
  targetId: z.string().min(1),
  access: workflowResourceAccessSchema.default('read'),
  writePrefixes: z.array(z.string()).default([]),
  readPriority: z.number().int().default(10),
});
export type WorkflowResourceBinding = z.input<typeof workflowResourceBindingSchema>;

export const workflowPersonaDependencySchema = z.object({
  id: z.string().min(1),
  revision: z.string().min(1),
  digest: z.string().min(1),
  path: z.string().min(1).optional(),
  resolved: z.boolean().optional(),
  message: z.string().optional(),
});
export type WorkflowPersonaDependency = z.input<typeof workflowPersonaDependencySchema>;

export const workflowRequirementSchema = z.object({
  id: z.string().min(1),
  kind: z.string().min(1),
  message: z.string(),
  resolved: z.boolean().optional(),
  binding: z.string().nullable().optional(),
});
export type WorkflowRequirement = z.input<typeof workflowRequirementSchema>;

// ---------------------------------------------------------------------------
// Workflow DAG invariants
// ---------------------------------------------------------------------------

export class WorkflowValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'WorkflowValidationError';
  }
}

export const workflowSchema = z
  .object({
    /** Portable workflow document schema version. */
    schemaVersion: z.union([z.literal(1), z.literal(2)]).optional(),
    workflowDependencies: z.record(z.string(), workflowPersonaDependencySchema).default({}),
    /** Unique identifier (UUID). */
    id: z.string().uuid(),
    /** Display name. */
    name: z.string().min(1),
    /** Semantic version string (e.g. "1.4.2"). */
    version: z.string().optional(),
    /** Human-readable description. */
    description: z.string().optional(),
    /** Visibility scope in the persisted workflow catalog. */
    scope: z.enum(['system', 'user']).optional(),
    /** Owning user for user-scoped workflows. */
    ownerId: z.string().nullable().optional(),
    /** Freeform workflow tags used for launch filtering and discovery. */
    tags: z.array(z.string()).default([]),
    /** Nodes in the DAG. IDs must be unique within a workflow. */
    nodes: z.array(workflowNodeSchema),
    /** Directed edges. Source and target must reference valid node IDs. */
    edges: z.array(workflowEdgeSchema),
    /** Non-execution resource attachments used by runtime composition. */
    resourceBindings: z.array(workflowResourceBindingSchema).default([]),
    /** Complete portable graph, including fields not currently edited by this UI. */
    graph: z.record(z.string(), z.unknown()).optional(),
    /** Workflow-local persona alias to exact portable persona revision. */
    personaDependencies: z.record(z.string(), workflowPersonaDependencySchema).default({}),
    /** Storage revision used for conditional writes. */
    revision: z.string().nullable().optional(),
    /** Bundled definitions cannot be overwritten; edit-as-copy creates a local workflow. */
    readOnly: z.boolean().optional(),
    /** Backend canonical source for the persisted document. */
    canonicalYaml: z.string().optional(),
    /** Server-side source used only while explicitly creating an editable copy. */
    copyFrom: z.string().uuid().optional(),
    /** Persona aliases explicitly requested for repinning during this save only. */
    refreshPersonas: z.array(z.string()).optional(),
    /** Local environment requirements that must resolve before launch. */
    requirements: z.array(workflowRequirementSchema).default([]),
  })
  .superRefine((workflow, context) => {
    if (workflow.nodes.some((node) => node.kind === 'wait') && workflow.schemaVersion !== 2) {
      context.addIssue({
        code: 'custom',
        path: ['schemaVersion'],
        message: 'Passive wait nodes require workflow schema version 2',
      });
    }
  });
export type Workflow = z.input<typeof workflowSchema>;

/**
 * Validate DAG structural invariants beyond Zod schema:
 *  1. Node IDs are unique.
 *  2. Every edge source and target references an existing node.
 *  3. No self-loops.
 *  4. No duplicate edges (same source + target + label tuple).
 *
 * Throws WorkflowValidationError on the first violated invariant.
 */
export function validateWorkflow(workflow: Workflow): void {
  const nodeIds = new Set<string>();
  const resourceNodeIds = new Set<string>();

  for (const node of workflow.nodes) {
    if (nodeIds.has(node.id)) {
      throw new WorkflowValidationError(`Duplicate node id: ${node.id}`);
    }
    nodeIds.add(node.id);
    if (node.kind === 'resource') {
      resourceNodeIds.add(node.id);
    }
  }

  const edgeKeys = new Set<string>();

  for (const edge of workflow.edges) {
    if (!nodeIds.has(edge.source)) {
      throw new WorkflowValidationError(
        `Edge ${edge.id} references unknown source node: ${edge.source}`,
      );
    }
    if (!nodeIds.has(edge.target)) {
      throw new WorkflowValidationError(
        `Edge ${edge.id} references unknown target node: ${edge.target}`,
      );
    }
    if (edge.source === edge.target) {
      throw new WorkflowValidationError(`Edge ${edge.id} is a self-loop on node: ${edge.source}`);
    }
    const key = `${edge.source}->${edge.target}->${edge.label ?? ''}`;
    if (edgeKeys.has(key)) {
      throw new WorkflowValidationError(
        `Duplicate edge from ${edge.source} to ${edge.target} (${edge.label ?? 'unlabelled'})`,
      );
    }
    edgeKeys.add(key);
  }

  const bindingKeys = new Set<string>();

  for (const binding of workflow.resourceBindings ?? []) {
    if (!resourceNodeIds.has(binding.resourceNodeId)) {
      throw new WorkflowValidationError(
        `Resource binding ${binding.id} references unknown resource node: ${binding.resourceNodeId}`,
      );
    }

    if (binding.targetType === 'workflow' && binding.targetId !== workflow.id) {
      throw new WorkflowValidationError(
        `Resource binding ${binding.id} references unknown workflow target: ${binding.targetId}`,
      );
    }

    if (binding.targetType === 'stage' && !nodeIds.has(binding.targetId)) {
      throw new WorkflowValidationError(
        `Resource binding ${binding.id} references unknown stage target: ${binding.targetId}`,
      );
    }

    const key = [
      binding.resourceNodeId,
      binding.targetType,
      binding.targetId,
      binding.access,
      (binding.writePrefixes ?? []).join(','),
    ].join('::');
    if (bindingKeys.has(key)) {
      throw new WorkflowValidationError(
        `Duplicate resource binding for ${binding.targetType} target ${binding.targetId}`,
      );
    }
    bindingKeys.add(key);
  }
}
