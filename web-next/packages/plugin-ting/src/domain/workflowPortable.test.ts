import { describe, expect, it } from 'vitest';
import { load } from 'js-yaml';
import type { Workflow } from './workflow';
import { serializePortableWorkflow, toPortableWorkflowDocument } from './workflowPortable';

const workflow: Workflow = {
  id: '00000000-0000-4000-8000-000000000001',
  name: 'Portable review',
  description: 'Keep every field',
  version: '2.1.0',
  tags: ['review'],
  nodes: [],
  edges: [],
  resourceBindings: [],
  personaDependencies: {
    reviewer: {
      id: 'persona-reviewer',
      revision: '12',
      digest: 'sha256:abc',
      path: 'personas/reviewer.yaml',
    },
  },
  graph: {
    artifactPaths: ['reports/review.md'],
    prompt: 'First line\nSecond line',
    editorExtension: { collapsed: ['stage-1'] },
    nodes: [{ stale: true }],
  },
};

describe('portable workflow documents', () => {
  it('preserves passive wait nodes and their external event edges', () => {
    const waiting: Workflow = {
      ...workflow,
      schemaVersion: 2,
      nodes: [
        { id: 'producer', kind: 'end', label: 'Queued', position: { x: 0, y: 0 } },
        { id: 'ci-wait', kind: 'wait', label: 'Wait for CI', position: { x: 200, y: 0 } },
        { id: 'consumer', kind: 'end', label: 'Continue', position: { x: 400, y: 0 } },
      ],
      edges: [
        {
          id: 'wait-in',
          source: 'producer',
          target: 'ci-wait',
          label: 'custom.build.waiting -> custom.build.waiting',
          cp1: { x: 92, y: 0 },
          cp2: { x: -92, y: 0 },
        },
        {
          id: 'wait-out',
          source: 'ci-wait',
          target: 'consumer',
          label: 'custom.build.finished -> custom.build.finished',
          cp1: { x: 92, y: 0 },
          cp2: { x: -92, y: 0 },
        },
      ],
    };

    const document = toPortableWorkflowDocument(waiting);

    expect(document.graph.nodes).toEqual(waiting.nodes);
    expect(document.graph.edges).toEqual(waiting.edges);
    expect(load(serializePortableWorkflow(waiting))).toEqual(document);
  });

  it('preserves pinned child workflows and dynamic expansion contracts in schema two', () => {
    const nested: Workflow = {
      ...workflow,
      schemaVersion: 2,
      workflowDependencies: {
        worker: { id: workflow.id, revision: 'sha256:child', digest: 'sha256:child' },
      },
      nodes: [
        {
          id: 'children',
          kind: 'subworkflow',
          label: 'Workstreams',
          position: { x: 1, y: 2 },
          templates: { worker: 'worker' },
          allowedCoordinator: 'coordinator',
          inputSchema: { type: 'object' },
          resultSchema: { type: 'object' },
          maxChildren: 100,
          maxAttempts: 3,
          maxActiveChildren: 4,
          joinMode: 'all',
        },
      ],
    };
    const document = toPortableWorkflowDocument(nested);
    expect(document.schema_version).toBe(2);
    expect(document.workflow_dependencies).toEqual(nested.workflowDependencies);
    expect(load(serializePortableWorkflow(nested))).toEqual(document);
    expect(document.graph.nodes).toEqual(nested.nodes);
  });
  it('preserves an include node and its overrides in schema two', () => {
    const included: Workflow = {
      ...workflow,
      schemaVersion: 2,
      workflowDependencies: {
        planning: { id: workflow.id, revision: 'sha256:planning', digest: 'sha256:planning' },
      },
      nodes: [
        {
          id: 'delivery-planning',
          kind: 'include',
          label: 'Plan the delivery',
          workflow: 'planning',
          nodes: {
            'planning-analysis': 'delivery-plan-author',
            'planning-reviews': 'delivery-plan-reviews',
          },
          overrides: {
            'delivery-plan-author': { label: 'Author the plan', position: { x: 0, y: 0 } },
          },
          position: { x: 3, y: 4 },
        },
      ],
    };
    const document = toPortableWorkflowDocument(included);
    expect(document.schema_version).toBe(2);
    expect(document.workflow_dependencies).toEqual(included.workflowDependencies);
    expect(load(serializePortableWorkflow(included))).toEqual(document);
    expect(document.graph.nodes).toEqual(included.nodes);
  });

  it('overlays editor fields while retaining unknown graph semantics', () => {
    expect(toPortableWorkflowDocument(workflow)).toEqual({
      schema_version: 1,
      id: workflow.id,
      name: workflow.name,
      description: workflow.description,
      version: workflow.version,
      persona_dependencies: workflow.personaDependencies,
      graph: {
        artifactPaths: ['reports/review.md'],
        prompt: 'First line\nSecond line',
        editorExtension: { collapsed: ['stage-1'] },
        tags: ['review'],
        nodes: [],
        edges: [],
        resourceBindings: [],
      },
    });
  });

  it('round-trips the versioned canonical shape through a YAML parser', () => {
    const yaml = serializePortableWorkflow(workflow);

    expect(load(yaml)).toEqual(toPortableWorkflowDocument(workflow));
  });

  it('preserves YAML-reserved keys and exact whitespace in strings', () => {
    const edgeCases: Workflow = {
      ...workflow,
      graph: {
        yes: 'yes',
        no: 'no',
        on: 'on',
        off: 'off',
        null: 'null',
        true: 'true',
        spaced: '  first line\n    second line\n',
        onlyNewlines: '\n\n',
      },
    };

    const parsed = load(serializePortableWorkflow(edgeCases));

    expect(parsed).toEqual(toPortableWorkflowDocument(edgeCases));
  });
});
