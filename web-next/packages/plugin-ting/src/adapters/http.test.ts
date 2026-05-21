/**
 * HTTP adapter tests — adapted from web/src/modules/ting/adapters/api/ test files.
 */
import { describe, it, expect, vi } from 'vitest';
import {
  buildTingHttpAdapter,
  buildDispatcherHttpAdapter,
  buildTingSessionHttpAdapter,
  buildTrackerHttpAdapter,
  buildTingIntegrationHttpAdapter,
  buildDispatchBusHttpAdapter,
  buildWorkflowHttpAdapter,
} from './http';
import type {
  ITingService,
  IDispatcherService,
  ITingSessionService,
  ITrackerBrowserService,
  ITingIntegrationService,
  IDispatchBus,
  CommitSagaRequest,
  CreateIntegrationParams,
} from '../ports';
import type { Workflow } from '../domain/workflow';

// ---------------------------------------------------------------------------
// Shared mock client factory
// ---------------------------------------------------------------------------

function makeClient() {
  return {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  };
}

// ---------------------------------------------------------------------------
// Shared raw fixtures
// ---------------------------------------------------------------------------

const rawSaga = {
  id: '00000000-0000-0000-0000-000000000001',
  tracker_id: 'LIN-001',
  tracker_type: 'linear',
  slug: 'auth-rewrite',
  name: 'Auth Rewrite',
  repos: ['niuulabs/volundr'],
  feature_branch: 'feat/auth-rewrite',
  status: 'active',
  confidence: 72,
  created_at: '2026-01-01T00:00:00Z',
  phase_summary: { total: 3, completed: 1 },
};

const rawRun = {
  id: '00000000-0000-0000-0000-000000000002',
  phase_id: '00000000-0000-0000-0000-000000000010',
  tracker_id: 'LIN-R1',
  name: 'Implement JWT refresh',
  description: 'Add silent token refresh.',
  acceptance_criteria: ['Refreshes before expiry'],
  declared_files: ['src/auth/refresh.ts'],
  estimate_hours: 4,
  status: 'queued',
  confidence: 80,
  session_id: null,
  reviewer_session_id: null,
  review_round: 0,
  branch: null,
  chronicle_summary: null,
  retry_count: 0,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
};

const rawPhase = {
  id: '00000000-0000-0000-0000-000000000010',
  saga_id: '00000000-0000-0000-0000-000000000001',
  tracker_id: 'LIN-M1',
  number: 1,
  name: 'Foundation',
  status: 'active',
  confidence: 75,
  runs: [rawRun],
};

const rawDispatcherState = {
  id: '00000000-0000-0000-0000-000000000099',
  running: true,
  threshold: 70,
  max_concurrent_runs: 3,
  auto_continue: false,
  updated_at: '2026-01-01T00:00:00Z',
};

const rawSessionInfo = {
  session_id: 'sess-abc',
  status: 'running',
  chronicle_lines: ['line 1', 'line 2'],
  branch: 'feat/jwt-refresh',
  confidence: 80,
  run_name: 'Implement JWT refresh',
  saga_name: 'Auth Rewrite',
  cluster_name: 'Mac mini',
};

const rawHelpMessage = {
  id: 'msg-1',
  session_id: 'sess-abc',
  content: '{"summary":"Need your call","reason":"needs_feedback"}',
  sender: 'help_needed',
  created_at: '2026-05-11T12:00:00Z',
  kind: 'help_request',
  help_request: {
    summary: 'Need your call on the final recommendation.',
    reason: 'needs_feedback',
    attempted: ['Compared the top two options'],
    recommendation: 'Pick the rollout order.',
    context: { slug: 'research/council-human-v1' },
    target_peer_id: 'flock-council-chair',
    persona: 'council-chair',
  },
};

const rawProject = {
  id: 'proj-1',
  name: 'My Project',
  description: 'A project',
  status: 'active',
  url: 'https://linear.app/niuu/proj/1',
  milestone_count: 3,
  issue_count: 12,
  slug: 'my-project',
};

const rawDispatchQueueItem = {
  saga_id: '00000000-0000-0000-0000-000000000001',
  saga_name: 'Auth Rewrite',
  saga_slug: 'auth-rewrite',
  repos: ['niuulabs/volundr'],
  feature_branch: 'feat/auth-rewrite',
  phase_name: 'Foundation',
  issue_id: 'issue-1',
  identifier: 'NIU-010',
  title: 'Implement JWT refresh',
  description: 'Add silent token refresh.',
  status: 'todo',
  priority: 1,
  priority_label: 'urgent',
  estimate: 4,
  url: 'https://linear.app/issue/NIU-010',
};

const rawDispatchApprovalResult = {
  issue_id: 'issue-1',
  session_id: 'sess-1',
  session_name: 'NIU-010',
  status: 'spawned',
  cluster_name: 'Default',
};

const rawDispatchCluster = {
  connection_id: 'cluster-mini',
  name: 'Mac mini',
  url: 'http://mac-mini.local:8000',
  enabled: true,
};

const rawMilestone = {
  id: 'ms-1',
  project_id: 'proj-1',
  name: 'M1',
  description: 'First milestone',
  sort_order: 1,
  progress: 50,
};

const rawIssue = {
  id: 'iss-1',
  identifier: 'NIU-100',
  title: 'Fix login bug',
  description: 'Login broken on Safari',
  status: 'in_progress',
  assignee: 'alice',
  labels: ['bug'],
  priority: 2,
  url: 'https://linear.app/niuu/iss/1',
  milestone_id: 'ms-1',
};

const rawIntegration = {
  id: 'int-1',
  integration_type: 'telegram',
  adapter: 'TelegramAdapter',
  credential_name: 'tg-token',
  enabled: true,
  status: 'connected',
  created_at: '2026-01-01T00:00:00Z',
};

const rawWorkflow = {
  id: '00000000-0000-0000-0000-0000000000aa',
  name: 'Knowledge Flow',
  description: 'Workflow with resource attachments',
  version: '1.0.0',
  scope: 'user' as const,
  owner_id: 'user-1',
  nodes: [
    { id: 'stage-1', kind: 'stage', label: 'Review', position: { x: 0, y: 0 } },
    {
      id: 'mimir-1',
      kind: 'resource',
      label: 'Shared Mimir',
      resourceType: 'mimir',
      bindingMode: 'registry',
      registryEntryId: 'shared-team-mimir',
      categories: ['entity'],
      position: { x: 200, y: 0 },
    },
  ],
  edges: [],
  resourceBindings: [
    {
      id: 'binding-1',
      resourceNodeId: 'mimir-1',
      targetType: 'stage',
      targetId: 'stage-1',
      access: 'read_write',
      writePrefixes: ['project/'],
      readPriority: 3,
    },
  ],
};

// ---------------------------------------------------------------------------
// buildTingHttpAdapter
// ---------------------------------------------------------------------------

describe('buildTingHttpAdapter', () => {
  describe('getSagas', () => {
    it('calls GET /sagas', async () => {
      const client = makeClient();
      client.get.mockResolvedValue([rawSaga]);
      await buildTingHttpAdapter(client).getSagas();
      expect(client.get).toHaveBeenCalledWith('/sagas');
    });

    it('transforms snake_case to camelCase', async () => {
      const client = makeClient();
      client.get.mockResolvedValue([rawSaga]);
      const result = await buildTingHttpAdapter(client).getSagas();
      expect(result[0]).toMatchObject({
        id: rawSaga.id,
        trackerId: 'LIN-001',
        trackerType: 'linear',
        featureBranch: 'feat/auth-rewrite',
        phaseSummary: { total: 3, completed: 1 },
      });
    });

    it('returns empty array when server returns none', async () => {
      const client = makeClient();
      client.get.mockResolvedValue([]);
      const result = await buildTingHttpAdapter(client).getSagas();
      expect(result).toHaveLength(0);
    });

    it('propagates errors', async () => {
      const client = makeClient();
      client.get.mockRejectedValue(new Error('network error'));
      await expect(buildTingHttpAdapter(client).getSagas()).rejects.toThrow('network error');
    });
  });

  describe('getSaga', () => {
    it('calls GET /sagas/:id', async () => {
      const client = makeClient();
      client.get.mockResolvedValue(rawSaga);
      await buildTingHttpAdapter(client).getSaga('00000000-0000-0000-0000-000000000001');
      expect(client.get).toHaveBeenCalledWith('/sagas/00000000-0000-0000-0000-000000000001');
    });

    it('URL-encodes id', async () => {
      const client = makeClient();
      client.get.mockResolvedValue(rawSaga);
      await buildTingHttpAdapter(client).getSaga('id with spaces');
      expect(client.get).toHaveBeenCalledWith('/sagas/id%20with%20spaces');
    });

    it('returns null when the HTTP client throws', async () => {
      const client = makeClient();
      client.get.mockRejectedValue(new Error('not found'));
      const result = await buildTingHttpAdapter(client).getSaga('missing');
      expect(result).toBeNull();
    });
  });

  describe('getPhases', () => {
    it('calls GET /sagas/:id/phases', async () => {
      const client = makeClient();
      client.get.mockResolvedValue([rawPhase]);
      await buildTingHttpAdapter(client).getPhases('saga-1');
      expect(client.get).toHaveBeenCalledWith('/sagas/saga-1/phases');
    });

    it('transforms phases and nested runs', async () => {
      const client = makeClient();
      client.get.mockResolvedValue([rawPhase]);
      const [phase] = await buildTingHttpAdapter(client).getPhases('saga-1');
      expect(phase?.sagaId).toBe('00000000-0000-0000-0000-000000000001');
      expect(phase?.runs[0]?.phaseId).toBe('00000000-0000-0000-0000-000000000010');
      expect(phase?.runs[0]?.acceptanceCriteria).toEqual(['Refreshes before expiry']);
    });
  });

  describe('run messages', () => {
    it('lists and transforms help requests', async () => {
      const client = makeClient();
      client.get.mockResolvedValue([rawHelpMessage]);

      const [message] = await buildTingHttpAdapter(client).listRunMessages(
        '00000000-0000-0000-0000-000000000002',
      );

      expect(client.get).toHaveBeenCalledWith(
        '/runs/00000000-0000-0000-0000-000000000002/messages',
      );
      expect(message).toMatchObject({
        id: 'msg-1',
        sessionId: 'sess-abc',
        kind: 'help_request',
        helpRequest: {
          targetPeerId: 'flock-council-chair',
          persona: 'council-chair',
        },
      });
    });

    it('sends a directed reply payload and normalizes the receipt', async () => {
      const client = makeClient();
      client.post.mockResolvedValue({
        message_id: 'msg-user-1',
        run_id: 'run-1',
        session_id: 'sess-abc',
        content: 'Please prefer the staged rollout option.',
        sender: 'user',
        created_at: '2026-05-11T12:05:00Z',
      });

      const message = await buildTingHttpAdapter(client).sendRunMessage(
        'run-1',
        'Please prefer the staged rollout option.',
        'flock-council-chair',
      );

      expect(client.post).toHaveBeenCalledWith('/runs/run-1/message', {
        content: 'Please prefer the staged rollout option.',
        target_peer_id: 'flock-council-chair',
      });
      expect(message).toMatchObject({
        id: 'msg-user-1',
        sessionId: 'sess-abc',
        sender: 'user',
        kind: 'message',
      });
    });
  });

  describe('commitSaga', () => {
    const req: CommitSagaRequest = {
      name: 'Auth Rewrite',
      slug: 'auth-rewrite',
      description: 'Rewrite auth layer',
      repos: ['niuulabs/volundr'],
      baseBranch: 'main',
      phases: [
        {
          name: 'Phase 1',
          runs: [
            {
              name: 'JWT refresh',
              description: 'Add silent refresh',
              acceptanceCriteria: ['Refreshes before expiry'],
              declaredFiles: ['src/auth/refresh.ts'],
              estimateHours: 4,
            },
          ],
        },
      ],
    };

    it('calls POST /sagas/commit', async () => {
      const client = makeClient();
      client.post.mockResolvedValue(rawSaga);
      await buildTingHttpAdapter(client).commitSaga(req);
      expect(client.post).toHaveBeenCalledWith('/sagas/commit', expect.any(Object));
    });

    it('converts camelCase to snake_case in request body', async () => {
      const client = makeClient();
      client.post.mockResolvedValue(rawSaga);
      await buildTingHttpAdapter(client).commitSaga(req);
      const body = client.post.mock.calls[0][1] as Record<string, unknown>;
      expect(body.base_branch).toBe('main');
      const phases = body.phases as { runs: { acceptance_criteria: string[] }[] }[];
      expect(phases[0]?.runs[0]?.acceptance_criteria).toEqual(['Refreshes before expiry']);
    });
  });

  describe('spawnPlanSession', () => {
    it('calls POST /sagas/plan', async () => {
      const client = makeClient();
      client.post.mockResolvedValue({ session_id: 'sess-1', chat_endpoint: null });
      const result = await buildTingHttpAdapter(client).spawnPlanSession('spec text', 'my/repo');
      expect(client.post).toHaveBeenCalledWith('/sagas/plan', {
        spec: 'spec text',
        repo: 'my/repo',
      });
      expect(result.sessionId).toBe('sess-1');
      expect(result.chatEndpoint).toBeNull();
    });
  });

  describe('extractStructure', () => {
    it('calls POST /sagas/extract-structure', async () => {
      const client = makeClient();
      client.post.mockResolvedValue({ found: true, structure: null });
      await buildTingHttpAdapter(client).extractStructure('some text');
      expect(client.post).toHaveBeenCalledWith('/sagas/extract-structure', { text: 'some text' });
    });
  });

  describe('interface compliance', () => {
    it('satisfies ITingService', () => {
      const client = makeClient();
      const svc: ITingService = buildTingHttpAdapter(client);
      expect(typeof svc.getSagas).toBe('function');
      expect(typeof svc.getSaga).toBe('function');
      expect(typeof svc.getPhases).toBe('function');
      expect(typeof svc.createSaga).toBe('function');
      expect(typeof svc.commitSaga).toBe('function');
      expect(typeof svc.decompose).toBe('function');
      expect(typeof svc.spawnPlanSession).toBe('function');
      expect(typeof svc.extractStructure).toBe('function');
    });
  });
});

describe('buildWorkflowHttpAdapter', () => {
  it('maps resource bindings from the API payload', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawWorkflow]);

    const [workflow] = await buildWorkflowHttpAdapter(client).listWorkflows();

    expect(workflow.resourceBindings).toEqual(rawWorkflow.resourceBindings);
  });

  it('sends resource bindings when saving a workflow', async () => {
    const client = makeClient();
    client.get.mockRejectedValue(new Error('not found'));
    client.post.mockResolvedValue(rawWorkflow);

    const workflow = {
      id: rawWorkflow.id,
      name: rawWorkflow.name,
      description: rawWorkflow.description,
      version: rawWorkflow.version,
      scope: rawWorkflow.scope,
      ownerId: rawWorkflow.owner_id,
      nodes: rawWorkflow.nodes,
      edges: rawWorkflow.edges,
      resourceBindings: rawWorkflow.resourceBindings,
    } satisfies Workflow;

    await buildWorkflowHttpAdapter(client).saveWorkflow(workflow);

    expect(client.post).toHaveBeenCalledWith(
      '/workflows',
      expect.objectContaining({
        resourceBindings: rawWorkflow.resourceBindings,
      }),
    );
  });

  it('fills default geometry for workflows that omit editor layout fields', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([
      {
        ...rawWorkflow,
        nodes: [
          { id: 'trigger-1', kind: 'trigger', label: 'Dispatch run', source: 'ting dispatch' },
          {
            id: 'stage-1',
            kind: 'stage',
            label: 'Run flock',
            joinMode: 'all',
            stageMembers: [{ personaId: 'coordinator', budget: 40 }],
            executionMode: 'parallel',
          },
          { id: 'end-1', kind: 'end', label: 'Done' },
        ],
        edges: [
          { id: 'edge-1', source: 'trigger-1', target: 'stage-1' },
          { id: 'edge-2', source: 'stage-1', target: 'end-1' },
        ],
      },
    ]);

    const [workflow] = await buildWorkflowHttpAdapter(client).listWorkflows();

    expect(workflow.nodes.map((node) => node.position)).toEqual([
      { x: 96, y: 144 },
      { x: 336, y: 144 },
      { x: 576, y: 144 },
    ]);
    expect(workflow.edges).toEqual([
      {
        id: 'edge-1',
        source: 'trigger-1',
        target: 'stage-1',
        cp1: { x: 92, y: 0 },
        cp2: { x: -92, y: 0 },
      },
      {
        id: 'edge-2',
        source: 'stage-1',
        target: 'end-1',
        cp1: { x: 92, y: 0 },
        cp2: { x: -92, y: 0 },
      },
    ]);
  });

  it('launches a workflow through the direct launch endpoint', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({
      workflowId: rawWorkflow.id,
      workflowName: rawWorkflow.name,
      slug: 'knowledge-flow',
      sessionId: 'sess-123',
      sessionName: 'knowledge-flow',
      status: 'starting',
      clusterName: 'valhalla',
    });

    const result = await buildWorkflowHttpAdapter(client).launchWorkflow(rawWorkflow.id, {
      prompt: 'Run the workflow against this topic.',
      sessionName: 'knowledge-flow',
      repo: 'https://github.com/niuulabs/volundr.git',
      branch: 'feat/workflow-launch',
    });

    expect(client.post).toHaveBeenCalledWith(
      `/workflows/${encodeURIComponent(rawWorkflow.id)}/launch`,
      {
        prompt: 'Run the workflow against this topic.',
        sessionName: 'knowledge-flow',
        repo: 'https://github.com/niuulabs/volundr.git',
        branch: 'feat/workflow-launch',
      },
    );
    expect(result.sessionId).toBe('sess-123');
  });
});

// ---------------------------------------------------------------------------
// buildDispatcherHttpAdapter
// ---------------------------------------------------------------------------

describe('buildDispatcherHttpAdapter', () => {
  it('calls GET /dispatcher for state', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawDispatcherState);
    await buildDispatcherHttpAdapter(client).getState();
    expect(client.get).toHaveBeenCalledWith('/dispatcher');
  });

  it('transforms snake_case dispatcher state', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawDispatcherState);
    const state = await buildDispatcherHttpAdapter(client).getState();
    expect(state).toMatchObject({
      running: true,
      threshold: 70,
      maxConcurrentRuns: 3,
      autoContinue: false,
    });
  });

  it('returns null when dispatcher throws', async () => {
    const client = makeClient();
    client.get.mockRejectedValue(new Error('not configured'));
    const result = await buildDispatcherHttpAdapter(client).getState();
    expect(result).toBeNull();
  });

  it('PATCHes running state', async () => {
    const client = makeClient();
    client.patch.mockResolvedValue(undefined);
    await buildDispatcherHttpAdapter(client).setRunning(false);
    expect(client.patch).toHaveBeenCalledWith('/dispatcher', { running: false });
  });

  it('PATCHes threshold', async () => {
    const client = makeClient();
    client.patch.mockResolvedValue(undefined);
    await buildDispatcherHttpAdapter(client).setThreshold(80);
    expect(client.patch).toHaveBeenCalledWith('/dispatcher', { threshold: 80 });
  });

  it('PATCHes auto_continue', async () => {
    const client = makeClient();
    client.patch.mockResolvedValue(undefined);
    await buildDispatcherHttpAdapter(client).setAutoContinue(true);
    expect(client.patch).toHaveBeenCalledWith('/dispatcher', { auto_continue: true });
  });

  it('calls GET /dispatcher/log', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(['log line 1', 'log line 2']);
    const log = await buildDispatcherHttpAdapter(client).getLog();
    expect(client.get).toHaveBeenCalledWith('/dispatcher/log');
    expect(log).toEqual(['log line 1', 'log line 2']);
  });

  it('satisfies IDispatcherService', () => {
    const client = makeClient();
    const svc: IDispatcherService = buildDispatcherHttpAdapter(client);
    expect(typeof svc.getState).toBe('function');
    expect(typeof svc.setRunning).toBe('function');
    expect(typeof svc.setThreshold).toBe('function');
    expect(typeof svc.setAutoContinue).toBe('function');
    expect(typeof svc.getLog).toBe('function');
  });
});

// ---------------------------------------------------------------------------
// buildTingSessionHttpAdapter
// ---------------------------------------------------------------------------

describe('buildTingSessionHttpAdapter', () => {
  it('calls GET /sessions', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawSessionInfo]);
    await buildTingSessionHttpAdapter(client).getSessions();
    expect(client.get).toHaveBeenCalledWith('/sessions');
  });

  it('transforms snake_case session info', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawSessionInfo]);
    const [session] = await buildTingSessionHttpAdapter(client).getSessions();
    expect(session?.sessionId).toBe('sess-abc');
    expect(session?.chronicleLines).toEqual(['line 1', 'line 2']);
    expect(session?.runName).toBe('Implement JWT refresh');
    expect(session?.clusterName).toBe('Mac mini');
  });

  it('calls GET /sessions/:id', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawSessionInfo);
    await buildTingSessionHttpAdapter(client).getSession('sess-abc');
    expect(client.get).toHaveBeenCalledWith('/sessions/sess-abc');
  });

  it('returns null when session not found', async () => {
    const client = makeClient();
    client.get.mockRejectedValue(new Error('404'));
    const result = await buildTingSessionHttpAdapter(client).getSession('missing');
    expect(result).toBeNull();
  });

  it('calls POST /sessions/:id/approve', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(undefined);
    await buildTingSessionHttpAdapter(client).approve('sess-abc');
    expect(client.post).toHaveBeenCalledWith('/sessions/sess-abc/approve', {});
  });

  it('satisfies ITingSessionService', () => {
    const client = makeClient();
    const svc: ITingSessionService = buildTingSessionHttpAdapter(client);
    expect(typeof svc.getSessions).toBe('function');
    expect(typeof svc.getSession).toBe('function');
    expect(typeof svc.approve).toBe('function');
  });
});

// ---------------------------------------------------------------------------
// buildTrackerHttpAdapter
// ---------------------------------------------------------------------------

describe('buildTrackerHttpAdapter', () => {
  it('calls GET /tracker/projects', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawProject]);
    await buildTrackerHttpAdapter(client).listProjects();
    expect(client.get).toHaveBeenCalledWith('/tracker/projects');
  });

  it('transforms tracker project', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawProject]);
    const [project] = await buildTrackerHttpAdapter(client).listProjects();
    expect(project?.milestoneCount).toBe(3);
    expect(project?.issueCount).toBe(12);
    expect(project?.slug).toBe('my-project');
  });

  it('calls GET /tracker/projects/:id', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawProject);
    await buildTrackerHttpAdapter(client).getProject('proj-1');
    expect(client.get).toHaveBeenCalledWith('/tracker/projects/proj-1');
  });

  it('calls GET /tracker/projects/:id/milestones', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawMilestone]);
    const [ms] = await buildTrackerHttpAdapter(client).listMilestones('proj-1');
    expect(client.get).toHaveBeenCalledWith('/tracker/projects/proj-1/milestones');
    expect(ms?.sortOrder).toBe(1);
    expect(ms?.projectId).toBe('proj-1');
  });

  it('calls GET /tracker/projects/:id/issues without milestone filter', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawIssue]);
    await buildTrackerHttpAdapter(client).listIssues('proj-1');
    expect(client.get).toHaveBeenCalledWith('/tracker/projects/proj-1/issues');
  });

  it('appends milestone_id query param when provided', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawIssue]);
    await buildTrackerHttpAdapter(client).listIssues('proj-1', 'ms-1');
    expect(client.get).toHaveBeenCalledWith('/tracker/projects/proj-1/issues?milestone_id=ms-1');
  });

  it('transforms tracker issue camelCase', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawIssue]);
    const [issue] = await buildTrackerHttpAdapter(client).listIssues('proj-1');
    expect(issue?.milestoneId).toBe('ms-1');
  });

  it('calls POST /tracker/import for importProject', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawSaga);
    await buildTrackerHttpAdapter(client).importProject('proj-1', ['niuulabs/volundr'], 'main');
    expect(client.post).toHaveBeenCalledWith('/tracker/import', {
      project_id: 'proj-1',
      repos: ['niuulabs/volundr'],
      base_branch: 'main',
      instance_id: null,
    });
  });

  it('normalizes lightweight tracker import responses without phase_summary', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({
      id: 'saga-1',
      tracker_id: 'proj-1',
      name: 'Research Persona Sandbox',
      repos: ['niuulabs/volundr'],
      feature_branch: 'feat/research-persona-sandbox',
      status: 'active',
      phase_count: 0,
      run_count: 2,
      workflow_id: null,
      workflow: null,
      workflow_version: null,
      instance_id: 'instance-1',
      instance_name: 'Guild Beta',
      warnings: [],
    });

    const saga = await buildTrackerHttpAdapter(client).importProject(
      'proj-1',
      ['niuulabs/volundr'],
      'main',
      'instance-1',
    );

    expect(saga.instanceName).toBe('Guild Beta');
    expect(saga.phaseSummary.total).toBe(2);
    expect(saga.phaseSummary.completed).toBe(0);
    expect(saga.slug).toBe('research-persona-sandbox');
  });

  it('satisfies ITrackerBrowserService', () => {
    const client = makeClient();
    const svc: ITrackerBrowserService = buildTrackerHttpAdapter(client);
    expect(typeof svc.listProjects).toBe('function');
    expect(typeof svc.getProject).toBe('function');
    expect(typeof svc.listMilestones).toBe('function');
    expect(typeof svc.listIssues).toBe('function');
    expect(typeof svc.importProject).toBe('function');
  });
});

// ---------------------------------------------------------------------------
// buildTingIntegrationHttpAdapter
// ---------------------------------------------------------------------------

describe('buildTingIntegrationHttpAdapter', () => {
  it('calls GET /integrations', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawIntegration]);
    await buildTingIntegrationHttpAdapter(client).listIntegrations();
    expect(client.get).toHaveBeenCalledWith('/integrations');
  });

  it('transforms integration snake_case', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawIntegration]);
    const [integration] = await buildTingIntegrationHttpAdapter(client).listIntegrations();
    expect(integration?.integrationType).toBe('telegram');
    expect(integration?.credentialName).toBe('tg-token');
  });

  it('creates integration with snake_case body', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawIntegration);
    const params: CreateIntegrationParams = {
      integrationType: 'telegram',
      adapter: 'TelegramAdapter',
      credentialName: 'tg-token',
      credentialValue: 'secret',
      config: { chat_id: '123' },
    };
    await buildTingIntegrationHttpAdapter(client).createIntegration(params);
    const body = client.post.mock.calls[0][1] as Record<string, unknown>;
    expect(body.integration_type).toBe('telegram');
    expect(body.credential_name).toBe('tg-token');
    expect(body.credential_value).toBe('secret');
  });

  it('calls DELETE /integrations/:id', async () => {
    const client = makeClient();
    client.delete.mockResolvedValue(undefined);
    await buildTingIntegrationHttpAdapter(client).deleteIntegration('int-1');
    expect(client.delete).toHaveBeenCalledWith('/integrations/int-1');
  });

  it('calls PATCH for toggle', async () => {
    const client = makeClient();
    client.patch.mockResolvedValue(rawIntegration);
    await buildTingIntegrationHttpAdapter(client).toggleIntegration('int-1', false);
    expect(client.patch).toHaveBeenCalledWith('/integrations/int-1', { enabled: false });
  });

  it('calls POST /integrations/:id/test', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({ success: true, message: 'ok' });
    const result = await buildTingIntegrationHttpAdapter(client).testConnection('int-1');
    expect(result.success).toBe(true);
  });

  it('calls GET /integrations/telegram/setup', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({ deeplink: 'https://t.me/bot', token: 'tok' });
    const result = await buildTingIntegrationHttpAdapter(client).getTelegramSetup();
    expect(result.deeplink).toBe('https://t.me/bot');
  });

  it('satisfies ITingIntegrationService', () => {
    const client = makeClient();
    const svc: ITingIntegrationService = buildTingIntegrationHttpAdapter(client);
    expect(typeof svc.listIntegrations).toBe('function');
    expect(typeof svc.createIntegration).toBe('function');
    expect(typeof svc.deleteIntegration).toBe('function');
    expect(typeof svc.toggleIntegration).toBe('function');
    expect(typeof svc.testConnection).toBe('function');
    expect(typeof svc.getTelegramSetup).toBe('function');
  });
});

describe('buildDispatchBusHttpAdapter', () => {
  it('calls GET /dispatch/queue and camelizes queue items', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawDispatchQueueItem]);

    const [item] = await buildDispatchBusHttpAdapter(client).getQueue();

    expect(client.get).toHaveBeenCalledWith('/dispatch/queue');
    expect(item?.sagaId).toBe(rawDispatchQueueItem.saga_id);
    expect(item?.phaseName).toBe(rawDispatchQueueItem.phase_name);
    expect(item?.priorityLabel).toBe(rawDispatchQueueItem.priority_label);
  });

  it('calls GET /dispatch/targets and camelizes cluster items', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawDispatchCluster]);

    const [cluster] = await buildDispatchBusHttpAdapter(client).getClusters();

    expect(client.get).toHaveBeenCalledWith('/dispatch/targets');
    expect(cluster).toEqual({
      connectionId: 'cluster-mini',
      name: 'Mac mini',
      url: 'http://mac-mini.local:8000',
      enabled: true,
    });
  });

  it('calls POST /dispatch/approve with snake_case request fields', async () => {
    const client = makeClient();
    client.post.mockResolvedValue([rawDispatchApprovalResult]);

    const [result] = await buildDispatchBusHttpAdapter(client).approve(
      [
        {
          sagaId: '00000000-0000-0000-0000-000000000001',
          issueId: 'issue-1',
          repo: 'niuulabs/volundr',
          connectionId: 'cluster-1',
          sessionDefinition: 'skuldCodex',
        },
      ],
      {
        model: 'gpt-test',
        systemPrompt: 'Ship it',
        connectionId: 'cluster-default',
        sessionDefinition: 'skuldCodex',
        workloadType: 'ravn_flock',
        workloadConfig: { personas: ['coder'] },
      },
    );

    expect(client.post).toHaveBeenCalledWith('/dispatch/approve', {
      items: [
        {
          saga_id: '00000000-0000-0000-0000-000000000001',
          issue_id: 'issue-1',
          repo: 'niuulabs/volundr',
          connection_id: 'cluster-1',
          session_definition: 'skuldCodex',
        },
      ],
      model: 'gpt-test',
      system_prompt: 'Ship it',
      connection_id: 'cluster-default',
      session_definition: 'skuldCodex',
      workload_type: 'ravn_flock',
      workload_config: { personas: ['coder'] },
    });
    expect(result?.issueId).toBe(rawDispatchApprovalResult.issue_id);
    expect(result?.clusterName).toBe(rawDispatchApprovalResult.cluster_name);
  });

  it('satisfies IDispatchBus', () => {
    const client = makeClient();
    const svc: IDispatchBus = buildDispatchBusHttpAdapter(client);
    expect(typeof svc.getQueue).toBe('function');
    expect(typeof svc.getClusters).toBe('function');
    expect(typeof svc.approve).toBe('function');
    expect(typeof svc.dispatch).toBe('function');
    expect(typeof svc.dispatchBatch).toBe('function');
  });
});
