import type {
  Workflow,
  WorkflowIncludeNode,
  WorkflowPersonaDependency,
  WorkflowSubworkflowNode,
} from './workflow';

/** Raised when pinning a child workflow document to a template fails. */
export class WorkflowDependencySelectionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'WorkflowDependencySelectionError';
  }
}

/** Raised when adding, renaming, or removing a subworkflow node's named
 *  templates would leave the node or its declared children inconsistent. */
export class WorkflowTemplateError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'WorkflowTemplateError';
  }
}

function dependencyMatches(
  dependency: WorkflowPersonaDependency | undefined,
  child: Workflow,
  documentRevision: string,
): boolean {
  return (
    dependency?.id === child.id &&
    dependency.revision === documentRevision &&
    dependency.digest === documentRevision
  );
}

function aliasBase(child: Workflow): string {
  const alias = child.name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
  return alias || `workflow-${child.id.slice(0, 8)}`;
}

function uniqueAlias(base: string, dependencies: Workflow['workflowDependencies']): string {
  if (!dependencies?.[base]) return base;
  let suffix = 2;
  while (dependencies[`${base}-${suffix}`]) suffix += 1;
  return `${base}-${suffix}`;
}

/**
 * Every subworkflow (node, template) pair, and every include node, whose
 * alias points at `alias` — shared by both kinds so an alias's use count is
 * counted consistently before it's dropped from `workflowDependencies`, no
 * matter which kind of node is being repinned or removed. `exceptNodeId` +
 * `exceptTemplateName` excludes one subworkflow (node, template) pair;
 * `exceptNodeId` alone (with `exceptTemplateName` omitted) excludes one
 * include node's own reference.
 */
function referencesAlias(
  workflow: Workflow,
  alias: string,
  exceptNodeId?: string,
  exceptTemplateName?: string,
): Array<{ nodeId: string; templateName: string | null }> {
  const matches: Array<{ nodeId: string; templateName: string | null }> = [];
  for (const node of workflow.nodes) {
    if (node.kind === 'subworkflow') {
      for (const [templateName, templateAlias] of Object.entries(node.templates ?? {})) {
        if (templateAlias !== alias) continue;
        if (node.id === exceptNodeId && templateName === exceptTemplateName) continue;
        matches.push({ nodeId: node.id, templateName });
      }
      continue;
    }
    if (node.kind === 'include') {
      if (node.workflow !== alias) continue;
      if (node.id === exceptNodeId && exceptTemplateName === undefined) continue;
      matches.push({ nodeId: node.id, templateName: null });
    }
  }
  return matches;
}

function findSubworkflowNode(
  workflow: Workflow,
  nodeId: string,
): WorkflowSubworkflowNode | undefined {
  return workflow.nodes.find(
    (candidate): candidate is WorkflowSubworkflowNode =>
      candidate.id === nodeId && candidate.kind === 'subworkflow',
  );
}

function findIncludeNode(workflow: Workflow, nodeId: string): WorkflowIncludeNode | undefined {
  return workflow.nodes.find(
    (candidate): candidate is WorkflowIncludeNode =>
      candidate.id === nodeId && candidate.kind === 'include',
  );
}

function nextTemplateName(existing: string[]): string {
  let index = existing.length + 1;
  let candidate = `template-${index}`;
  while (existing.includes(candidate)) {
    index += 1;
    candidate = `template-${index}`;
  }
  return candidate;
}

/**
 * Pin a subworkflow node's named template to an exact catalog document.
 *
 * The node and top-level dependency map are returned in one immutable value so
 * callers can commit them as one undo step. A private alias remains stable when
 * it is repinned; an alias shared with another template (on this node or
 * another) is split before changing its target.
 */
export function bindSubworkflowTemplate(
  workflow: Workflow,
  nodeId: string,
  templateName: string,
  child: Workflow,
): Workflow {
  const node = findSubworkflowNode(workflow, nodeId);
  if (!node) {
    throw new WorkflowDependencySelectionError(`Subworkflow node ${nodeId} was not found`);
  }
  const trimmedTemplateName = templateName.trim();
  if (!trimmedTemplateName || !(trimmedTemplateName in (node.templates ?? {}))) {
    throw new WorkflowDependencySelectionError(
      `Subworkflow node ${nodeId} does not offer template ${templateName}`,
    );
  }
  if (workflow.id === child.id) {
    throw new WorkflowDependencySelectionError('A workflow cannot select itself as its child');
  }

  const documentRevision = child.documentRevision?.trim();
  if (!documentRevision) {
    throw new WorkflowDependencySelectionError(
      `Workflow ${child.name} does not expose an immutable document revision`,
    );
  }

  const dependencies = { ...(workflow.workflowDependencies ?? {}) };
  const previousAlias = (node.templates?.[trimmedTemplateName] ?? '').trim();
  const previousDependency = previousAlias ? dependencies[previousAlias] : undefined;
  const previousAliasIsShared =
    previousAlias.length > 0 &&
    referencesAlias(workflow, previousAlias, nodeId, trimmedTemplateName).length > 0;

  let alias = previousAlias;
  if (
    !alias ||
    (previousAliasIsShared && !dependencyMatches(previousDependency, child, documentRevision))
  ) {
    alias =
      Object.entries(dependencies).find(([, dependency]) =>
        dependencyMatches(dependency, child, documentRevision),
      )?.[0] ?? uniqueAlias(aliasBase(child), dependencies);
  }

  const existing = dependencies[alias];
  if (!dependencyMatches(existing, child, documentRevision)) {
    dependencies[alias] = {
      id: child.id,
      revision: documentRevision,
      digest: documentRevision,
    };
  }

  const nodeChanged = node.templates?.[trimmedTemplateName] !== alias;
  const dependenciesChanged =
    JSON.stringify(dependencies) !== JSON.stringify(workflow.workflowDependencies ?? {});
  if (!nodeChanged && !dependenciesChanged && workflow.schemaVersion === 2) return workflow;

  return {
    ...workflow,
    schemaVersion: 2,
    workflowDependencies: dependencies,
    nodes: workflow.nodes.map((candidate) =>
      candidate.id === nodeId && candidate.kind === 'subworkflow'
        ? { ...candidate, templates: { ...candidate.templates, [trimmedTemplateName]: alias } }
        : candidate,
    ),
  };
}

/**
 * Offer another child workflow on a subworkflow node: a new template name
 * mapped to no alias yet (repoint it with `bindSubworkflowTemplate`). `name`
 * must be unique on the node when given; omit it for an auto-generated one.
 */
export function addSubworkflowTemplate(
  workflow: Workflow,
  nodeId: string,
  name?: string,
): Workflow {
  const node = findSubworkflowNode(workflow, nodeId);
  if (!node) {
    throw new WorkflowTemplateError(`Subworkflow node ${nodeId} was not found`);
  }
  const templates = node.templates ?? {};
  const trimmedName = name?.trim();
  if (trimmedName && trimmedName in templates) {
    throw new WorkflowTemplateError(
      `Subworkflow node ${nodeId} already declares template ${trimmedName}`,
    );
  }
  const templateName = trimmedName || nextTemplateName(Object.keys(templates));

  return {
    ...workflow,
    nodes: workflow.nodes.map((candidate) =>
      candidate.id === nodeId && candidate.kind === 'subworkflow'
        ? { ...candidate, templates: { ...templates, [templateName]: '' } }
        : candidate,
    ),
  };
}

/**
 * Rename one of a subworkflow node's templates, keeping any static
 * `children[].template` reference to it consistent.
 */
export function renameSubworkflowTemplate(
  workflow: Workflow,
  nodeId: string,
  previousName: string,
  nextName: string,
): Workflow {
  const node = findSubworkflowNode(workflow, nodeId);
  if (!node) {
    throw new WorkflowTemplateError(`Subworkflow node ${nodeId} was not found`);
  }
  const templates = node.templates ?? {};
  const trimmedPrevious = previousName.trim();
  const trimmedNext = nextName.trim();
  if (!(trimmedPrevious in templates)) {
    throw new WorkflowTemplateError(
      `Subworkflow node ${nodeId} does not offer template ${previousName}`,
    );
  }
  if (!trimmedNext) {
    throw new WorkflowTemplateError('Template name must not be blank');
  }
  if (trimmedNext === trimmedPrevious) return workflow;
  if (trimmedNext in templates) {
    throw new WorkflowTemplateError(
      `Subworkflow node ${nodeId} already declares template ${trimmedNext}`,
    );
  }

  const nextTemplates: Record<string, string> = {};
  for (const [name, alias] of Object.entries(templates)) {
    nextTemplates[name === trimmedPrevious ? trimmedNext : name] = alias;
  }

  return {
    ...workflow,
    nodes: workflow.nodes.map((candidate) => {
      if (candidate.id !== nodeId || candidate.kind !== 'subworkflow') return candidate;
      const children = candidate.children?.map((declaredChild) =>
        declaredChild.template === trimmedPrevious
          ? { ...declaredChild, template: trimmedNext }
          : declaredChild,
      );
      return {
        ...candidate,
        templates: nextTemplates,
        ...(children ? { children } : {}),
      };
    }),
  };
}

/**
 * Remove one of a subworkflow node's templates. Refused when it is the
 * node's last template, or when a declared static child still names it — the
 * caller repoints or removes those children first. An alias the removed
 * template held is dropped from the document's `workflowDependencies` only
 * when nothing else on the workflow still references it.
 */
export function removeSubworkflowTemplate(
  workflow: Workflow,
  nodeId: string,
  name: string,
): Workflow {
  const node = findSubworkflowNode(workflow, nodeId);
  if (!node) {
    throw new WorkflowTemplateError(`Subworkflow node ${nodeId} was not found`);
  }
  const templates = node.templates ?? {};
  const trimmedName = name.trim();
  if (!(trimmedName in templates)) {
    throw new WorkflowTemplateError(`Subworkflow node ${nodeId} does not offer template ${name}`);
  }
  if (Object.keys(templates).length <= 1) {
    throw new WorkflowTemplateError(
      `Subworkflow node ${nodeId} must keep at least one template; add another before removing ${trimmedName}`,
    );
  }
  const usedBy = (node.children ?? []).filter(
    (declaredChild) => declaredChild.template === trimmedName,
  );
  if (usedBy.length > 0) {
    throw new WorkflowTemplateError(
      `Template ${trimmedName} is still used by declared child(ren) ` +
        `${usedBy.map((declaredChild) => declaredChild.key).join(', ')}; repoint or remove them first`,
    );
  }

  const removedAlias = templates[trimmedName];
  const remainingTemplates = Object.fromEntries(
    Object.entries(templates).filter(([templateName]) => templateName !== trimmedName),
  );
  const nodes = workflow.nodes.map((candidate) =>
    candidate.id === nodeId && candidate.kind === 'subworkflow'
      ? { ...candidate, templates: remainingTemplates }
      : candidate,
  );

  const dependencies = { ...(workflow.workflowDependencies ?? {}) };
  if (removedAlias && referencesAlias({ ...workflow, nodes }, removedAlias).length === 0) {
    delete dependencies[removedAlias];
  }

  return { ...workflow, nodes, workflowDependencies: dependencies };
}

/**
 * Pin an include node's `workflow` alias to an exact catalog document, using
 * the same alias-reuse/split rules as `bindSubworkflowTemplate` — a private
 * alias stays stable when repinned; one shared with another node (a
 * subworkflow template or another include) is split before its target
 * changes.
 */
export function bindIncludeWorkflow(workflow: Workflow, nodeId: string, child: Workflow): Workflow {
  const node = findIncludeNode(workflow, nodeId);
  if (!node) {
    throw new WorkflowDependencySelectionError(`Include node ${nodeId} was not found`);
  }
  if (workflow.id === child.id) {
    throw new WorkflowDependencySelectionError('A workflow cannot include itself');
  }

  const documentRevision = child.documentRevision?.trim();
  if (!documentRevision) {
    throw new WorkflowDependencySelectionError(
      `Workflow ${child.name} does not expose an immutable document revision`,
    );
  }

  const dependencies = { ...(workflow.workflowDependencies ?? {}) };
  const previousAlias = (node.workflow ?? '').trim();
  const previousDependency = previousAlias ? dependencies[previousAlias] : undefined;
  const previousAliasIsShared =
    previousAlias.length > 0 && referencesAlias(workflow, previousAlias, nodeId).length > 0;

  let alias = previousAlias;
  if (
    !alias ||
    (previousAliasIsShared && !dependencyMatches(previousDependency, child, documentRevision))
  ) {
    alias =
      Object.entries(dependencies).find(([, dependency]) =>
        dependencyMatches(dependency, child, documentRevision),
      )?.[0] ?? uniqueAlias(aliasBase(child), dependencies);
  }

  const existing = dependencies[alias];
  if (!dependencyMatches(existing, child, documentRevision)) {
    dependencies[alias] = {
      id: child.id,
      revision: documentRevision,
      digest: documentRevision,
    };
  }

  const nodeChanged = node.workflow !== alias;
  const dependenciesChanged =
    JSON.stringify(dependencies) !== JSON.stringify(workflow.workflowDependencies ?? {});
  if (!nodeChanged && !dependenciesChanged && workflow.schemaVersion === 2) return workflow;

  return {
    ...workflow,
    schemaVersion: 2,
    workflowDependencies: dependencies,
    nodes: workflow.nodes.map((candidate) =>
      candidate.id === nodeId && candidate.kind === 'include'
        ? { ...candidate, workflow: alias }
        : candidate,
    ),
  };
}
