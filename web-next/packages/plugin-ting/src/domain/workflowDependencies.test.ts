import { describe, expect, it } from 'vitest';
import type { Workflow, WorkflowDeclaredChild, WorkflowSubworkflowNode } from './workflow';
import {
  addSubworkflowTemplate,
  bindSubworkflowTemplate,
  removeSubworkflowTemplate,
  renameSubworkflowTemplate,
  WorkflowDependencySelectionError,
  WorkflowTemplateError,
} from './workflowDependencies';

const parentId = '00000000-0000-4000-8000-000000000001';
const childId = '00000000-0000-4000-8000-000000000002';

function subworkflow(
  id: string,
  templates: Record<string, string>,
  children?: WorkflowDeclaredChild[],
): WorkflowSubworkflowNode {
  return {
    id,
    kind: 'subworkflow',
    label: 'Child work',
    position: { x: 0, y: 0 },
    templates,
    allowedCoordinator: 'coordinator',
    inputSchema: { type: 'object', properties: {} },
    resultSchema: { type: 'object', properties: {} },
    maxChildren: 10,
    maxAttempts: 3,
    maxActiveChildren: 4,
    joinMode: 'all',
    blockedEvent: 'children.blocked',
    ...(children ? { children } : {}),
  };
}

function parent(nodes = [subworkflow('child-node', { default: '' })]): Workflow {
  return {
    id: parentId,
    name: 'Parent',
    nodes,
    edges: [],
    workflowDependencies: {},
  };
}

function child(overrides: Partial<Workflow> = {}): Workflow {
  return {
    id: childId,
    name: 'Research Thread',
    documentRevision: 'sha256:child-document',
    nodes: [],
    edges: [],
    ...overrides,
  };
}

describe('bindSubworkflowTemplate', () => {
  it('pins the exact immutable document and upgrades the parent to schema v2', () => {
    const result = bindSubworkflowTemplate(parent(), 'child-node', 'default', child());

    expect(result.schemaVersion).toBe(2);
    expect(result.workflowDependencies).toEqual({
      'research-thread': {
        id: childId,
        revision: 'sha256:child-document',
        digest: 'sha256:child-document',
      },
    });
    expect(result.nodes[0]).toMatchObject({ templates: { default: 'research-thread' } });
  });

  it('preserves a private alias and returns the same value for the same exact pin', () => {
    const workflow: Workflow = {
      ...parent([subworkflow('child-node', { default: 'worker' })]),
      schemaVersion: 2,
      workflowDependencies: {
        worker: {
          id: childId,
          revision: 'sha256:child-document',
          digest: 'sha256:child-document',
          path: 'workflows/research-thread.yaml',
        },
      },
    };

    expect(bindSubworkflowTemplate(workflow, 'child-node', 'default', child())).toBe(workflow);
  });

  it('splits a shared alias before selecting a different child for one template', () => {
    const firstChild = subworkflow('child-one', { default: 'worker' });
    const secondChild = subworkflow('child-two', { default: 'worker' });
    const workflow: Workflow = {
      ...parent([firstChild, secondChild]),
      schemaVersion: 2,
      workflowDependencies: {
        worker: { id: childId, revision: 'sha256:old', digest: 'sha256:old' },
        'new-child': {
          id: '00000000-0000-4000-8000-000000000003',
          revision: 'sha256:new',
          digest: 'sha256:new',
        },
      },
    };
    const selected = child({
      id: '00000000-0000-4000-8000-000000000003',
      name: 'New Child',
      documentRevision: 'sha256:new',
    });

    const result = bindSubworkflowTemplate(workflow, 'child-one', 'default', selected);

    expect(result.nodes).toEqual([
      expect.objectContaining({ id: 'child-one', templates: { default: 'new-child' } }),
      expect.objectContaining({ id: 'child-two', templates: { default: 'worker' } }),
    ]);
    expect(result.workflowDependencies?.worker?.digest).toBe('sha256:old');
  });

  it('splits a shared alias between two templates on the same node', () => {
    const node = subworkflow('child-node', { breadth: 'worker', depth: 'worker' });
    const workflow: Workflow = {
      ...parent([node]),
      schemaVersion: 2,
      workflowDependencies: {
        worker: { id: childId, revision: 'sha256:old', digest: 'sha256:old' },
      },
    };
    const selected = child({
      id: '00000000-0000-4000-8000-000000000003',
      name: 'New Child',
      documentRevision: 'sha256:new',
    });

    const result = bindSubworkflowTemplate(workflow, 'child-node', 'breadth', selected);

    expect(result.nodes[0]).toMatchObject({
      templates: { breadth: 'new-child', depth: 'worker' },
    });
    expect(result.workflowDependencies?.worker?.digest).toBe('sha256:old');
  });

  it('keeps a shared alias when every template already references the exact child', () => {
    const workflow: Workflow = {
      ...parent([
        subworkflow('child-one', { default: 'worker' }),
        subworkflow('child-two', { default: 'worker' }),
      ]),
      schemaVersion: 2,
      workflowDependencies: {
        worker: {
          id: childId,
          revision: 'sha256:child-document',
          digest: 'sha256:child-document',
        },
      },
    };

    expect(bindSubworkflowTemplate(workflow, 'child-one', 'default', child())).toBe(workflow);
  });

  it('reuses an existing exact alias for an unconfigured template', () => {
    const workflow: Workflow = {
      ...parent(),
      workflowDependencies: {
        thread: {
          id: childId,
          revision: 'sha256:child-document',
          digest: 'sha256:child-document',
        },
      },
    };

    const result = bindSubworkflowTemplate(workflow, 'child-node', 'default', child());

    expect(result.nodes[0]).toMatchObject({ templates: { default: 'thread' } });
    expect(Object.keys(result.workflowDependencies ?? {})).toEqual(['thread']);
  });

  it('uses a collision-safe alias for a new child', () => {
    const workflow: Workflow = {
      ...parent(),
      workflowDependencies: {
        'research-thread': {
          id: '00000000-0000-4000-8000-000000000099',
          revision: 'sha256:other',
          digest: 'sha256:other',
        },
        'research-thread-2': {
          id: '00000000-0000-4000-8000-000000000098',
          revision: 'sha256:other-2',
          digest: 'sha256:other-2',
        },
      },
    };

    const result = bindSubworkflowTemplate(workflow, 'child-node', 'default', child());

    expect(result.nodes[0]).toMatchObject({ templates: { default: 'research-thread-3' } });
  });

  it('derives a stable id alias when the child name has no letters or digits', () => {
    const result = bindSubworkflowTemplate(
      parent(),
      'child-node',
      'default',
      child({ name: '---', id: '12345678-0000-4000-8000-000000000002' }),
    );

    expect(result.nodes[0]).toMatchObject({ templates: { default: 'workflow-12345678' } });
  });

  it('rejects self references, missing revisions, unknown nodes, and unknown templates', () => {
    expect(() =>
      bindSubworkflowTemplate(parent(), 'child-node', 'default', child({ id: parentId })),
    ).toThrow(WorkflowDependencySelectionError);
    expect(() =>
      bindSubworkflowTemplate(parent(), 'child-node', 'default', child({ documentRevision: null })),
    ).toThrow(/immutable document revision/);
    expect(() =>
      bindSubworkflowTemplate(parent(), 'missing', 'default', child()),
    ).toThrow(/was not found/);
    expect(() =>
      bindSubworkflowTemplate(parent(), 'child-node', 'unknown', child()),
    ).toThrow(/does not offer template/);
  });
});

describe('addSubworkflowTemplate', () => {
  it('adds a new template with an auto-generated name and no alias yet', () => {
    const result = addSubworkflowTemplate(parent(), 'child-node');

    expect(result.nodes[0]).toMatchObject({
      templates: { default: '', 'template-2': '' },
    });
  });

  it('adds a new template with a given unique name', () => {
    const result = addSubworkflowTemplate(parent(), 'child-node', 'breadth');

    expect(result.nodes[0]).toMatchObject({
      templates: { default: '', breadth: '' },
    });
  });

  it('skips already-taken auto-generated names', () => {
    const node = subworkflow('child-node', { default: '', 'template-2': 'worker' });
    const result = addSubworkflowTemplate(parent([node]), 'child-node');

    expect(result.nodes[0]).toMatchObject({
      templates: { default: '', 'template-2': 'worker', 'template-3': '' },
    });
  });

  it('rejects a duplicate template name and an unknown node', () => {
    expect(() => addSubworkflowTemplate(parent(), 'child-node', 'default')).toThrow(
      WorkflowTemplateError,
    );
    expect(() => addSubworkflowTemplate(parent(), 'missing')).toThrow(/was not found/);
  });
});

describe('renameSubworkflowTemplate', () => {
  it('renames a template while keeping its alias', () => {
    const node = subworkflow('child-node', { default: 'worker' });
    const result = renameSubworkflowTemplate(parent([node]), 'child-node', 'default', 'general');

    expect(result.nodes[0]).toMatchObject({ templates: { general: 'worker' } });
  });

  it('propagates the rename to static children that name the old template', () => {
    const node = subworkflow('child-node', { breadth: 'worker', depth: 'thread' }, [
      { key: 'first', objective: 'Map broadly', template: 'breadth' },
      { key: 'second', objective: 'Go deep', template: 'depth' },
    ]);
    const result = renameSubworkflowTemplate(parent([node]), 'child-node', 'breadth', 'wide');

    expect(result.nodes[0]).toMatchObject({
      templates: { wide: 'worker', depth: 'thread' },
      children: [
        { key: 'first', objective: 'Map broadly', template: 'wide' },
        { key: 'second', objective: 'Go deep', template: 'depth' },
      ],
    });
  });

  it('is a no-op when the name is unchanged', () => {
    const workflow = parent();
    expect(renameSubworkflowTemplate(workflow, 'child-node', 'default', 'default')).toBe(workflow);
  });

  it('rejects a blank name, a duplicate name, an unknown template, and an unknown node', () => {
    const node = subworkflow('child-node', { default: 'worker', breadth: 'thread' });
    const workflow = parent([node]);

    expect(() => renameSubworkflowTemplate(workflow, 'child-node', 'default', '  ')).toThrow(
      /must not be blank/,
    );
    expect(() =>
      renameSubworkflowTemplate(workflow, 'child-node', 'default', 'breadth'),
    ).toThrow(WorkflowTemplateError);
    expect(() =>
      renameSubworkflowTemplate(workflow, 'child-node', 'unknown', 'general'),
    ).toThrow(/does not offer template/);
    expect(() => renameSubworkflowTemplate(workflow, 'missing', 'default', 'general')).toThrow(
      /was not found/,
    );
  });
});

describe('removeSubworkflowTemplate', () => {
  it('removes a template and prunes its unreferenced alias', () => {
    const node = subworkflow('child-node', { default: 'worker', breadth: 'thread' });
    const workflow: Workflow = {
      ...parent([node]),
      workflowDependencies: {
        worker: { id: childId, revision: 'sha256:a', digest: 'sha256:a' },
        thread: { id: childId, revision: 'sha256:b', digest: 'sha256:b' },
      },
    };

    const result = removeSubworkflowTemplate(workflow, 'child-node', 'breadth');

    expect(result.nodes[0]).toMatchObject({ templates: { default: 'worker' } });
    expect(result.workflowDependencies).toEqual({
      worker: { id: childId, revision: 'sha256:a', digest: 'sha256:a' },
    });
  });

  it('keeps an alias still referenced by another template after removal', () => {
    const node = subworkflow('child-node', { default: 'worker', breadth: 'worker' });
    const workflow: Workflow = {
      ...parent([node]),
      workflowDependencies: {
        worker: { id: childId, revision: 'sha256:a', digest: 'sha256:a' },
      },
    };

    const result = removeSubworkflowTemplate(workflow, 'child-node', 'breadth');

    expect(result.workflowDependencies).toEqual({
      worker: { id: childId, revision: 'sha256:a', digest: 'sha256:a' },
    });
  });

  it('refuses to remove the last template', () => {
    const workflow = parent([subworkflow('child-node', { default: 'worker' })]);

    expect(() => removeSubworkflowTemplate(workflow, 'child-node', 'default')).toThrow(
      /must keep at least one template/,
    );
  });

  it('refuses to remove a template still used by a declared static child', () => {
    const node = subworkflow('child-node', { breadth: 'worker', depth: 'thread' }, [
      { key: 'first', objective: 'Map broadly', template: 'breadth' },
    ]);
    const workflow = parent([node]);

    expect(() => removeSubworkflowTemplate(workflow, 'child-node', 'breadth')).toThrow(
      /still used by declared child/,
    );
  });

  it('rejects an unknown template and an unknown node', () => {
    const workflow = parent([subworkflow('child-node', { default: 'worker', breadth: 'thread' })]);

    expect(() => removeSubworkflowTemplate(workflow, 'child-node', 'unknown')).toThrow(
      /does not offer template/,
    );
    expect(() => removeSubworkflowTemplate(workflow, 'missing', 'default')).toThrow(
      /was not found/,
    );
  });
});
