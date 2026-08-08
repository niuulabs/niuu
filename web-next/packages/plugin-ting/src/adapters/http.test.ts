/**
 * HTTP adapter tests — adapted from web/src/modules/ting/adapters/api/ test files.
 */
import { describe, it, expect, vi } from 'vitest';
import {
  buildTingHttpAdapter,
  buildDispatcherHttpAdapter,
  buildTingSessionHttpAdapter,
  buildTrackerHttpAdapter,
  buildDispatchBusHttpAdapter,
  buildWorkflowHttpAdapter,
  buildResearchHttpAdapter,
  buildTingSettingsHttpAdapter,
  buildTingAuditLogHttpAdapter,
} from './http';
import type {
  ITingService,
  IDispatcherService,
  ITingSessionService,
  ITrackerBrowserService,
  IDispatchBus,
  IResearchService,
  ITingSettingsService,
  IAuditLogService,
  CommitSagaRequest,
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

const rawResearchCampaign = {
  id: 'campaign-1',
  slug: 'research/council-human-v1',
  name: 'Council Human Research',
  owner_id: 'user-1',
  workflow_id: rawWorkflow.id,
  workflow_version: '1.0.0',
  workflow_name: 'Knowledge Flow',
  session_id: 'sess-200',
  session_name: 'council-human',
  status: 'running',
  active_stage_id: 'stage-review',
  stage_state: [
    {
      stage_id: 'stage-review',
      label: 'Review',
      status: 'active',
      started_at: '2026-05-10T10:00:00Z',
      completed_at: null,
      reason: null,
    },
  ],
  metadata: { topic: 'council' },
  created_at: '2026-05-10T09:00:00Z',
  updated_at: '2026-05-10T12:00:00Z',
  last_activity_at: '2026-05-10T12:05:00Z',
  completed_at: null,
};

const rawArtifact = {
  path: 'reports/final.md',
  title: 'Final Report',
  updated_at: '2026-05-10T12:00:00Z',
  kind: 'report',
  publish_state: 'published',
  source_ids: ['src-1'],
  summary: 'A concise recommendation.',
};

const rawFlockConfig = {
  flock_name: 'Valhalla',
  default_base_branch: 'main',
  default_tracker_type: 'linear',
  default_repos: ['niuulabs/volundr'],
  max_active_sagas: 12,
  auto_create_milestones: true,
  updated_at: '2026-05-10T12:00:00Z',
};

const rawDispatchDefaults = {
  confidence_threshold: 72,
  max_concurrent_runs: 4,
  auto_continue: true,
  batch_size: 6,
  retry_policy: {
    max_retries: 3,
    retry_delay_seconds: 45,
    escalate_on_exhaustion: true,
  },
  updated_at: '2026-05-10T12:00:00Z',
};

const rawNotificationSettings = {
  channel: 'slack',
  on_run_pending_approval: true,
  on_run_merged: true,
  on_run_failed: false,
  on_saga_complete: true,
  on_dispatcher_error: true,
  webhook_url: 'https://hooks.example/slack',
  updated_at: '2026-05-10T12:00:00Z',
};

const rawAuditEntry = {
  id: 'audit-1',
  kind: 'dispatch.approved',
  summary: 'Approved queue items',
  actor: 'alice',
  payload: { issueCount: 2 },
  created_at: '2026-05-10T12:00:00Z',
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

  describe('deleteSaga', () => {
    it('calls DELETE /sagas/:id', async () => {
      const client = makeClient();
      await buildTingHttpAdapter(client).deleteSaga?.('saga-1');
      expect(client.delete).toHaveBeenCalledWith('/sagas/saga-1');
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
      client.post.mockResolvedValue({
        session_id: 'sess-1',
        chat_endpoint: null,
        campaign_slug: 'plan-auth',
        workflow_name: 'Saga Planning',
        status: 'pending',
        active_stage_id: 'plan-clarify',
        stage_state: [{ stage_id: 'plan-clarify', label: 'Clarify brief', status: 'active' }],
        questions: [
          {
            id: 'planning-feedback',
            question: 'What constraints should this workflow account for?',
            hint: 'Keep this focused.',
            kind: 'text',
          },
        ],
      });
      const result = await buildTingHttpAdapter(client).spawnPlanSession('spec text', 'my/repo');
      expect(client.post).toHaveBeenCalledWith('/sagas/plan', {
        spec: 'spec text',
        repo: 'my/repo',
      });
      expect(result.sessionId).toBe('sess-1');
      expect(result.chatEndpoint).toBeNull();
      expect(result.campaignSlug).toBe('plan-auth');
      expect(result.workflowName).toBe('Saga Planning');
      expect(result.activeStageId).toBe('plan-clarify');
      expect(result.stageState?.[0]?.label).toBe('Clarify brief');
      expect(result.questions[0]?.id).toBe('planning-feedback');
    });

    it('lists active plan sessions', async () => {
      const client = makeClient();
      client.get.mockResolvedValue([
        {
          session_id: 'sess-1',
          chat_endpoint: null,
          name: 'Plan SDCP operator',
          prompt: 'Plan SDCP operator',
          repo: '',
          campaign_slug: 'plan-sdcp-operator',
          workflow_name: 'Saga Planning',
          status: 'running',
          active_stage_id: 'plan-clarify',
          updated_at: '2026-07-01T12:00:00Z',
          stage_state: [{ stage_id: 'plan-clarify', label: 'Clarify brief', status: 'active' }],
        },
      ]);

      const result = await buildTingHttpAdapter(client).listPlanSessions?.();

      expect(client.get).toHaveBeenCalledWith('/sagas/plan');
      expect(result?.[0]?.sessionId).toBe('sess-1');
      expect(result?.[0]?.name).toBe('Plan SDCP operator');
      expect(result?.[0]?.repo).toBe('');
      expect(result?.[0]?.updatedAt).toBe('2026-07-01T12:00:00Z');
    });

    it('fetches persisted plan session status', async () => {
      const client = makeClient();
      client.get.mockResolvedValue({
        session_id: 'sess-1',
        chat_endpoint: null,
        campaign_slug: 'plan-auth',
        workflow_name: 'Saga Planning',
        status: 'running',
        active_stage_id: 'plan-breakdown',
        stage_state: [
          { stage_id: 'plan-breakdown', label: 'Draft saga breakdown', status: 'active' },
        ],
      });

      const result = await buildTingHttpAdapter(client).getPlanSession?.('plan-auth');

      expect(client.get).toHaveBeenCalledWith('/sagas/plan/plan-auth');
      expect(result?.status).toBe('running');
      expect(result?.activeStageId).toBe('plan-breakdown');
      expect(result?.stageState?.[0]?.label).toBe('Draft saga breakdown');
    });

    it('fetches a workflow-backed plan draft', async () => {
      const client = makeClient();
      client.get.mockResolvedValue({
        found: true,
        structure: {
          name: 'Auth Saga',
          risks: [{ kind: 'blast', message: 'Touches auth dispatch.' }],
          phases: [
            {
              name: 'Build',
              runs: [
                {
                  name: 'JWT refresh',
                  description: 'Add silent refresh',
                  acceptance_criteria: ['Refreshes before expiry'],
                  declared_files: ['src/auth/refresh.ts'],
                  estimate_hours: 4,
                  confidence: 80,
                },
              ],
            },
          ],
        },
      });

      const result = await buildTingHttpAdapter(client).getPlanDraft?.('plan-auth');

      expect(client.get).toHaveBeenCalledWith('/sagas/plan/plan-auth/draft');
      expect(result?.found).toBe(true);
      expect(result?.structure?.name).toBe('Auth Saga');
      expect(result?.structure?.risks?.[0]?.kind).toBe('blast');
      expect(result?.structure?.phases[0]?.runs[0]?.acceptanceCriteria).toEqual([
        'Refreshes before expiry',
      ]);
    });

    it('sends plan feedback to the workflow session', async () => {
      const client = makeClient();
      client.post.mockResolvedValue({ status: 'sent' });

      await buildTingHttpAdapter(client).sendPlanFeedback?.(
        'plan-auth',
        'make it smaller',
        'changes_requested',
      );

      expect(client.post).toHaveBeenCalledWith('/sagas/plan/plan-auth/feedback', {
        content: 'make it smaller',
        decision: 'changes_requested',
      });
    });

    it('cancels a plan session', async () => {
      const client = makeClient();

      await buildTingHttpAdapter(client).cancelPlanSession?.('plan-auth');

      expect(client.delete).toHaveBeenCalledWith('/sagas/plan/plan-auth');
    });
  });

  describe('extractStructure', () => {
    it('calls POST /sagas/extract-structure', async () => {
      const client = makeClient();
      client.post.mockResolvedValue({
        found: true,
        structure: {
          name: 'Auth Saga',
          risks: [{ kind: 'blast', message: 'Touches auth dispatch.' }],
          phases: [
            {
              name: 'Build',
              runs: [
                {
                  name: 'JWT refresh',
                  description: 'Add silent refresh',
                  acceptance_criteria: ['Refreshes before expiry'],
                  declared_files: ['src/auth/refresh.ts'],
                  estimate_hours: 4,
                  confidence: 80,
                },
              ],
            },
          ],
        },
      });
      const result = await buildTingHttpAdapter(client).extractStructure('some text');
      expect(client.post).toHaveBeenCalledWith('/sagas/extract-structure', { text: 'some text' });
      expect(result.structure?.phases[0]?.runs[0]?.declaredFiles).toEqual(['src/auth/refresh.ts']);
      expect(result.structure?.risks?.[0]?.message).toBe('Touches auth dispatch.');
    });
  });

  describe('other saga actions', () => {
    it('defaults plain run messages to kind=message and helpRequest=null', async () => {
      const client = makeClient();
      client.get.mockResolvedValue([
        {
          id: 'msg-plain',
          session_id: 'sess-plain',
          content: 'A regular note',
          sender: 'user',
          created_at: '2026-05-11T12:10:00Z',
        },
      ]);

      const [message] = await buildTingHttpAdapter(client).listRunMessages('run-plain');

      expect(message).toMatchObject({
        id: 'msg-plain',
        kind: 'message',
        helpRequest: null,
      });
    });

    it('sends null target_peer_id when a reply is not directed', async () => {
      const client = makeClient();
      client.post.mockResolvedValue({
        message_id: 'msg-user-2',
        run_id: 'run-2',
        session_id: 'sess-plain',
        content: 'Undirected note',
        sender: 'user',
        created_at: '2026-05-11T12:15:00Z',
      });

      await buildTingHttpAdapter(client).sendRunMessage('run-2', 'Undirected note');

      expect(client.post).toHaveBeenCalledWith('/runs/run-2/message', {
        content: 'Undirected note',
        target_peer_id: null,
      });
    });

    it('creates sagas and fills server fallbacks when optional fields are missing', async () => {
      const client = makeClient();
      client.post.mockResolvedValue({
        id: 'saga-min',
        tracker_id: 'TRK-1',
        name: 'My New Saga',
        repos: ['niuulabs/volundr'],
        feature_branch: 'feat/my-new-saga',
        status: 'draft',
        created_at: '2026-05-10T09:00:00Z',
        run_count: 2,
      });

      const saga = await buildTingHttpAdapter(client).createSaga('spec', 'niuulabs/volundr');

      expect(client.post).toHaveBeenCalledWith('/sagas', {
        spec: 'spec',
        repo: 'niuulabs/volundr',
      });
      expect(saga).toMatchObject({
        trackerType: 'linear',
        slug: 'my-new-saga',
        baseBranch: 'main',
        confidence: 0,
        phaseSummary: { total: 2, completed: 0 },
      });
    });

    it('decomposes specs into phases', async () => {
      const client = makeClient();
      client.post.mockResolvedValue([rawPhase]);

      const phases = await buildTingHttpAdapter(client).decompose('spec', 'repo');

      expect(client.post).toHaveBeenCalledWith('/sagas/decompose', { spec: 'spec', repo: 'repo' });
      expect(phases[0]?.runs[0]?.name).toBe(rawRun.name);
    });

    it('assigns workflow ids including null to clear an assignment', async () => {
      const client = makeClient();
      client.put.mockResolvedValue(rawSaga);

      await buildTingHttpAdapter(client).assignWorkflow('saga 1', null);

      expect(client.put).toHaveBeenCalledWith('/sagas/saga%201/workflow', {
        workflow_id: null,
      });
    });

    it('assigns saga targets and maps the resulting saga', async () => {
      const client = makeClient();
      client.put.mockResolvedValue({
        ...rawSaga,
        instance_id: 'cluster-2',
        instance_name: 'Cluster Two',
      });

      const saga = await buildTingHttpAdapter(client).assignTarget('saga-1', {
        mode: 'instance',
        instanceId: 'cluster-2',
      });

      expect(client.put).toHaveBeenCalledWith('/sagas/saga-1/target', {
        instance_id: 'cluster-2',
        target_tags: [],
        target_match: 'all',
      });
      expect(saga.instanceName).toBe('Cluster Two');
    });

    it('assigns saga tag targets', async () => {
      const client = makeClient();
      client.put.mockResolvedValue({
        ...rawSaga,
        target_tags: ['gpu', 'valhalla'],
        target_match: 'all',
      });

      const saga = await buildTingHttpAdapter(client).assignTarget('saga-1', {
        mode: 'tags',
        tags: ['gpu', 'valhalla'],
        match: 'all',
      });

      expect(client.put).toHaveBeenCalledWith('/sagas/saga-1/target', {
        instance_id: null,
        target_tags: ['gpu', 'valhalla'],
        target_match: 'all',
      });
      expect(saga.targetTags).toEqual(['gpu', 'valhalla']);
    });

    it('assigns saga repositories with per-repo branches', async () => {
      const client = makeClient();
      client.put.mockResolvedValue({
        ...rawSaga,
        repos: ['niuulabs/volundr', 'niuulabs/infrastructure'],
        repo_refs: [
          { repo: 'niuulabs/volundr', branch: 'dev' },
          { repo: 'niuulabs/infrastructure', branch: 'main' },
        ],
      });

      const repoRefs = [
        { repo: 'niuulabs/volundr', branch: 'dev' },
        { repo: 'niuulabs/infrastructure', branch: 'main' },
      ];
      const saga = await buildTingHttpAdapter(client).assignRepos('saga-1', repoRefs);

      expect(client.put).toHaveBeenCalledWith('/sagas/saga-1/repos', {
        repos: ['niuulabs/volundr', 'niuulabs/infrastructure'],
        repo_refs: repoRefs,
      });
      expect(saga.repoRefs).toEqual(repoRefs);
    });
  });

  describe('interface compliance', () => {
    it('satisfies ITingService', () => {
      const client = makeClient();
      const svc: ITingService = buildTingHttpAdapter(client);
      expect(typeof svc.getSagas).toBe('function');
      expect(typeof svc.getSaga).toBe('function');
      expect(typeof svc.deleteSaga).toBe('function');
      expect(typeof svc.getPhases).toBe('function');
      expect(typeof svc.createSaga).toBe('function');
      expect(typeof svc.commitSaga).toBe('function');
      expect(typeof svc.decompose).toBe('function');
      expect(typeof svc.spawnPlanSession).toBe('function');
      expect(typeof svc.cancelPlanSession).toBe('function');
      expect(typeof svc.extractStructure).toBe('function');
      expect(typeof svc.assignRepos).toBe('function');
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

  it('returns null when a workflow lookup fails', async () => {
    const client = makeClient();
    client.get.mockRejectedValue(new Error('404'));

    const workflow = await buildWorkflowHttpAdapter(client).getWorkflow('missing workflow');

    expect(client.get).toHaveBeenCalledWith('/workflows/missing%20workflow');
    expect(workflow).toBeNull();
  });

  it('gets a workflow and preserves explicit edge control points', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      ...rawWorkflow,
      edges: [
        {
          id: 'edge-explicit',
          source: 'stage-1',
          target: 'mimir-1',
          cp1: { x: 10, y: 20 },
          cp2: { x: -10, y: -20 },
        },
      ],
    });

    const workflow = await buildWorkflowHttpAdapter(client).getWorkflow(rawWorkflow.id);

    expect(client.get).toHaveBeenCalledWith(`/workflows/${encodeURIComponent(rawWorkflow.id)}`);
    expect(workflow?.edges[0]).toEqual({
      id: 'edge-explicit',
      source: 'stage-1',
      target: 'mimir-1',
      cp1: { x: 10, y: 20 },
      cp2: { x: -10, y: -20 },
    });
  });

  it('updates existing workflows with normalized defaults in the request body', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawWorkflow);
    client.put.mockResolvedValue(rawWorkflow);

    await buildWorkflowHttpAdapter(client).saveWorkflow({
      id: rawWorkflow.id,
      name: rawWorkflow.name,
      description: undefined,
      version: undefined,
      scope: undefined,
      ownerId: rawWorkflow.owner_id,
      nodes: rawWorkflow.nodes,
      edges: rawWorkflow.edges,
      tags: undefined,
      resourceBindings: undefined,
    } as Workflow);

    expect(client.put).toHaveBeenCalledWith(`/workflows/${encodeURIComponent(rawWorkflow.id)}`, {
      name: rawWorkflow.name,
      description: '',
      version: 'draft',
      scope: 'user',
      tags: [],
      nodes: rawWorkflow.nodes,
      edges: rawWorkflow.edges,
      resourceBindings: [],
    });
  });

  it('creates a user copy when saving a system workflow is forbidden', async () => {
    const client = makeClient();
    const systemWorkflow = { ...rawWorkflow, scope: 'system' as const, owner_id: null };
    client.get.mockResolvedValue(systemWorkflow);
    client.put.mockRejectedValue(new Error('403'));
    client.post.mockResolvedValue({ ...rawWorkflow, scope: 'user' as const });

    await buildWorkflowHttpAdapter(client).saveWorkflow({
      id: systemWorkflow.id,
      name: systemWorkflow.name,
      description: systemWorkflow.description,
      version: systemWorkflow.version,
      scope: systemWorkflow.scope,
      ownerId: systemWorkflow.owner_id,
      nodes: systemWorkflow.nodes,
      edges: systemWorkflow.edges,
      tags: [],
      resourceBindings: systemWorkflow.resourceBindings,
    } as Workflow);

    expect(client.put).toHaveBeenCalledWith(
      `/workflows/${encodeURIComponent(systemWorkflow.id)}`,
      expect.objectContaining({ scope: 'system' }),
    );
    expect(client.post).toHaveBeenCalledWith(
      '/workflows',
      expect.objectContaining({ name: systemWorkflow.name, scope: 'user' }),
    );
  });

  it('surfaces update failures for owned user workflows', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawWorkflow);
    client.put.mockRejectedValue(new Error('500'));

    await expect(
      buildWorkflowHttpAdapter(client).saveWorkflow({
        id: rawWorkflow.id,
        name: rawWorkflow.name,
        description: rawWorkflow.description,
        version: rawWorkflow.version,
        scope: rawWorkflow.scope,
        ownerId: rawWorkflow.owner_id,
        nodes: rawWorkflow.nodes,
        edges: rawWorkflow.edges,
        resourceBindings: rawWorkflow.resourceBindings,
      } as Workflow),
    ).rejects.toThrow('500');

    expect(client.post).not.toHaveBeenCalled();
  });

  it('creates workflows when the existence probe returns null instead of throwing', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(null);
    client.post.mockResolvedValue(rawWorkflow);

    await buildWorkflowHttpAdapter(client).saveWorkflow({
      id: rawWorkflow.id,
      name: rawWorkflow.name,
      description: rawWorkflow.description,
      version: rawWorkflow.version,
      scope: rawWorkflow.scope,
      ownerId: rawWorkflow.owner_id,
      nodes: rawWorkflow.nodes,
      edges: rawWorkflow.edges,
      resourceBindings: rawWorkflow.resourceBindings,
    } as Workflow);

    expect(client.post).toHaveBeenCalledWith(
      '/workflows',
      expect.objectContaining({
        name: rawWorkflow.name,
      }),
    );
  });

  it('maps snake_case resource bindings and vertical default bezier controls', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([
      {
        ...rawWorkflow,
        description: '',
        version: '',
        tags: undefined,
        nodes: [
          {
            id: 'trigger-1',
            kind: 'trigger',
            label: 'Dispatch run',
            source: 'ting dispatch',
            position: { x: 100, y: 100 },
          },
          { id: 'stage-1', kind: 'stage', label: 'Run flock', position: { x: 100, y: 360 } },
        ],
        edges: [{ id: 'edge-1', source: 'trigger-1', target: 'stage-1' }],
        resourceBindings: undefined,
        resource_bindings: [
          {
            id: 'binding-snake',
            resourceNodeId: 'mimir-1',
            targetType: 'stage',
            targetId: 'stage-1',
            access: 'read',
            readPriority: 2,
          },
        ],
      },
    ]);

    const [workflow] = await buildWorkflowHttpAdapter(client).listWorkflows();

    expect(workflow.description).toBeUndefined();
    expect(workflow.version).toBeUndefined();
    expect(workflow.tags).toEqual([]);
    expect(workflow.resourceBindings).toEqual([
      {
        id: 'binding-snake',
        resourceNodeId: 'mimir-1',
        targetType: 'stage',
        targetId: 'stage-1',
        access: 'read',
        readPriority: 2,
      },
    ]);
    expect(workflow.edges[0]).toMatchObject({
      cp1: { x: 0, y: 92 },
      cp2: { x: 0, y: -92 },
    });
  });

  it('deletes workflows by encoded id', async () => {
    const client = makeClient();
    client.delete.mockResolvedValue(undefined);

    await buildWorkflowHttpAdapter(client).deleteWorkflow('workflow 1');

    expect(client.delete).toHaveBeenCalledWith('/workflows/workflow%201');
  });

  it('maps snake_case workflow launch responses', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({
      workflow_id: rawWorkflow.id,
      workflow_name: rawWorkflow.name,
      slug: 'knowledge-flow',
      session_id: 'sess-999',
      session_name: 'knowledge-flow-2',
      status: 'queued',
      cluster_name: 'bifrost',
    });

    const result = await buildWorkflowHttpAdapter(client).launchWorkflow(rawWorkflow.id, {
      prompt: 'Launch',
      sessionName: 'knowledge-flow-2',
      repo: 'https://github.com/niuulabs/volundr.git',
      branch: 'feat/launch',
    });

    expect(result).toMatchObject({
      workflowId: rawWorkflow.id,
      workflowName: rawWorkflow.name,
      sessionId: 'sess-999',
      sessionName: 'knowledge-flow-2',
      clusterName: 'bifrost',
    });
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

  it('lists dispatcher activity logs with the requested limit', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      events: [
        {
          id: 'evt-1',
          event: 'dispatch.started',
          data: { issueId: 'issue-1' },
          owner_id: 'alice',
          timestamp: '2026-05-10T12:00:00Z',
        },
      ],
      total: 1,
    });

    const [event] = await buildDispatcherHttpAdapter(client).getActivityLog(25);

    expect(client.get).toHaveBeenCalledWith('/dispatcher/log?limit=25');
    expect(event).toEqual({
      id: 'evt-1',
      event: 'dispatch.started',
      data: { issueId: 'issue-1' },
      ownerId: 'alice',
      timestamp: '2026-05-10T12:00:00Z',
    });
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

  it('defaults project slug to an empty string when the API omits it', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([{ ...rawProject, slug: undefined }]);

    const [project] = await buildTrackerHttpAdapter(client).listProjects();

    expect(project?.slug).toBe('');
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
      repo_refs: undefined,
      instance_id: null,
      target_tags: [],
      target_match: 'all',
    });
  });

  it('sends repo refs and tag target selectors when importing a project', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawSaga);

    await buildTrackerHttpAdapter(client).importProject(
      'proj-1',
      ['niuulabs/volundr'],
      'main',
      null,
      {
        repoRefs: [
          { repo: 'niuulabs/volundr', branch: 'main' },
          { repo: 'niuulabs/infrastructure', branch: 'prod' },
        ],
        target: { mode: 'tags', tags: ['gpu', 'valhalla'], match: 'all' },
      },
    );

    expect(client.post).toHaveBeenCalledWith('/tracker/import', {
      project_id: 'proj-1',
      repos: ['niuulabs/volundr', 'niuulabs/infrastructure'],
      base_branch: 'main',
      repo_refs: [
        { repo: 'niuulabs/volundr', branch: 'main' },
        { repo: 'niuulabs/infrastructure', branch: 'prod' },
      ],
      instance_id: null,
      target_tags: ['gpu', 'valhalla'],
      target_match: 'all',
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
      tags: [],
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

  it('uses instanceId fallbacks and omits optional approve fields when absent', async () => {
    const client = makeClient();
    client.post.mockResolvedValue([
      {
        ...rawDispatchApprovalResult,
        cluster_name: 'Guild Beta',
      },
    ]);

    await buildDispatchBusHttpAdapter(client).approve(
      [
        {
          sagaId: 'saga-1',
          issueId: 'issue-1',
          repo: 'niuulabs/volundr',
          instanceId: 'instance-9',
          workflowId: 'workflow-1',
        },
      ],
      { instanceId: 'instance-default' },
    );

    expect(client.post).toHaveBeenCalledWith('/dispatch/approve', {
      items: [
        {
          saga_id: 'saga-1',
          issue_id: 'issue-1',
          repo: 'niuulabs/volundr',
          connection_id: 'instance-9',
          workflow_id: 'workflow-1',
        },
      ],
      connection_id: 'instance-default',
    });
  });

  it('omits optional approval fields when neither items nor options provide them', async () => {
    const client = makeClient();
    client.post.mockResolvedValue([rawDispatchApprovalResult]);

    await buildDispatchBusHttpAdapter(client).approve([
      {
        sagaId: 'saga-1',
        issueId: 'issue-1',
        repo: 'niuulabs/volundr',
      },
    ]);

    expect(client.post).toHaveBeenCalledWith('/dispatch/approve', {
      items: [
        {
          saga_id: 'saga-1',
          issue_id: 'issue-1',
          repo: 'niuulabs/volundr',
        },
      ],
    });
  });

  it('dispatches individual runs and dispatch batches', async () => {
    const client = makeClient();
    client.post
      .mockResolvedValueOnce(undefined)
      .mockResolvedValueOnce({ queued: 2, skipped: 1, failed: 0 });

    await buildDispatchBusHttpAdapter(client).dispatch('run 1');
    const result = await buildDispatchBusHttpAdapter(client).dispatchBatch(['run-1', 'run-2']);

    expect(client.post).toHaveBeenNthCalledWith(1, '/dispatch/run%201', {});
    expect(client.post).toHaveBeenNthCalledWith(2, '/dispatch/batch', {
      run_ids: ['run-1', 'run-2'],
    });
    expect(result).toEqual({ queued: 2, skipped: 1, failed: 0 });
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

describe('buildResearchHttpAdapter', () => {
  it('lists campaigns and maps snake_case campaign fields', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawResearchCampaign]);

    const [campaign] = await buildResearchHttpAdapter(client).listCampaigns();

    expect(client.get).toHaveBeenCalledWith('/research/campaigns');
    expect(campaign).toMatchObject({
      ownerId: 'user-1',
      workflowId: rawWorkflow.id,
      workflowVersion: '1.0.0',
      workflowName: 'Knowledge Flow',
      sessionId: 'sess-200',
      sessionName: 'council-human',
      activeStageId: 'stage-review',
      createdAt: '2026-05-10T09:00:00Z',
      updatedAt: '2026-05-10T12:00:00Z',
    });
    expect(campaign.stageState[0]).toEqual({
      stageId: 'stage-review',
      label: 'Review',
      status: 'active',
      startedAt: '2026-05-10T10:00:00Z',
      completedAt: null,
      reason: null,
    });
  });

  it('gets campaign details and maps artifacts plus canonical_artifacts', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({
      ...rawResearchCampaign,
      artifacts: [rawArtifact],
      canonical_artifacts: { brief: 'reports/final.md' },
    });

    const detail = await buildResearchHttpAdapter(client).getCampaign('research/council-human-v1');

    expect(client.get).toHaveBeenCalledWith('/research/campaigns/research%2Fcouncil-human-v1');
    expect(detail?.artifacts[0]).toEqual({
      path: 'reports/final.md',
      title: 'Final Report',
      updatedAt: '2026-05-10T12:00:00Z',
      kind: 'report',
      publishState: 'published',
      sourceIds: ['src-1'],
      summary: 'A concise recommendation.',
    });
    expect(detail?.canonicalArtifacts).toEqual({ brief: 'reports/final.md' });
  });

  it('returns null when a campaign lookup fails', async () => {
    const client = makeClient();
    client.get.mockRejectedValue(new Error('404'));

    const detail = await buildResearchHttpAdapter(client).getCampaign('missing');

    expect(detail).toBeNull();
  });

  it('creates, updates, and deletes campaigns through the expected endpoints', async () => {
    const client = makeClient();
    client.post.mockResolvedValue(rawResearchCampaign);
    client.patch.mockResolvedValue({ ...rawResearchCampaign, status: 'complete' });
    client.delete.mockResolvedValue(undefined);

    const service = buildResearchHttpAdapter(client);
    await service.createCampaign({
      question: 'Which rollout should we pick?',
      name: 'Council Human Research',
      workflowId: rawWorkflow.id,
      repo: 'https://github.com/niuulabs/volundr.git',
      branch: 'feat/research',
      mode: 'direct',
      audience: 'Leads',
      deliverable: 'Recommendation memo',
      success: 'Clear decision',
      constraints: 'One day',
      monitoringCadence: 'daily',
      connectionId: 'cluster-mini',
    });
    const updated = await service.updateCampaign('research/council-human-v1', {
      name: 'Updated Name',
      status: 'complete',
      metadata: { approved: true },
    });
    await service.deleteCampaign('research/council-human-v1');

    expect(client.post).toHaveBeenCalledWith('/research/campaigns', {
      question: 'Which rollout should we pick?',
      name: 'Council Human Research',
      workflowId: rawWorkflow.id,
      repo: 'https://github.com/niuulabs/volundr.git',
      branch: 'feat/research',
      mode: 'direct',
      audience: 'Leads',
      deliverable: 'Recommendation memo',
      success: 'Clear decision',
      constraints: 'One day',
      monitoringCadence: 'daily',
      connectionId: 'cluster-mini',
    });
    expect(client.patch).toHaveBeenCalledWith('/research/campaigns/research%2Fcouncil-human-v1', {
      name: 'Updated Name',
      status: 'complete',
      metadata: { approved: true },
    });
    expect(client.delete).toHaveBeenCalledWith('/research/campaigns/research%2Fcouncil-human-v1');
    expect(updated.status).toBe('complete');
  });

  it('gets artifact details and returns null when an artifact is missing', async () => {
    const client = makeClient();
    client.get
      .mockResolvedValueOnce({
        ...rawArtifact,
        content: '# Final Report',
      })
      .mockRejectedValueOnce(new Error('404'));

    const service = buildResearchHttpAdapter(client);
    const found = await service.getArtifact('research/council-human-v1', 'reports/final.md');
    const missing = await service.getArtifact('research/council-human-v1', 'missing.md');

    expect(client.get).toHaveBeenNthCalledWith(
      1,
      '/research/campaigns/research%2Fcouncil-human-v1/artifact?path=reports%2Ffinal.md',
    );
    expect(found?.content).toBe('# Final Report');
    expect(missing).toBeNull();
  });

  it('satisfies IResearchService', () => {
    const client = makeClient();
    const svc: IResearchService = buildResearchHttpAdapter(client);
    expect(typeof svc.listCampaigns).toBe('function');
    expect(typeof svc.getCampaign).toBe('function');
    expect(typeof svc.createCampaign).toBe('function');
    expect(typeof svc.updateCampaign).toBe('function');
    expect(typeof svc.deleteCampaign).toBe('function');
    expect(typeof svc.getArtifact).toBe('function');
  });
});

describe('buildTingSettingsHttpAdapter', () => {
  it('gets and updates flock config with snake_case fields', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawFlockConfig);
    client.patch.mockResolvedValue(rawFlockConfig);

    const service = buildTingSettingsHttpAdapter(client);
    const config = await service.getFlockConfig();
    await service.updateFlockConfig({
      flockName: 'Valhalla',
      defaultBaseBranch: 'develop',
      defaultTrackerType: 'jira',
      defaultRepos: ['niuulabs/volundr', 'niuulabs/mimir'],
      maxActiveSagas: 20,
      autoCreateMilestones: false,
    });

    expect(client.get).toHaveBeenCalledWith('/settings/flock');
    expect(config).toEqual({
      flockName: 'Valhalla',
      defaultBaseBranch: 'main',
      defaultTrackerType: 'linear',
      defaultRepos: ['niuulabs/volundr'],
      maxActiveSagas: 12,
      autoCreateMilestones: true,
      updatedAt: '2026-05-10T12:00:00Z',
    });
    expect(client.patch).toHaveBeenCalledWith('/settings/flock', {
      flock_name: 'Valhalla',
      default_base_branch: 'develop',
      default_tracker_type: 'jira',
      default_repos: ['niuulabs/volundr', 'niuulabs/mimir'],
      max_active_sagas: 20,
      auto_create_milestones: false,
    });
  });

  it('omits flock patch fields that are left undefined', async () => {
    const client = makeClient();
    client.patch.mockResolvedValue(rawFlockConfig);

    await buildTingSettingsHttpAdapter(client).updateFlockConfig({});

    expect(client.patch).toHaveBeenCalledWith('/settings/flock', {});
  });

  it('gets and updates dispatch defaults including retry policy fallbacks', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawDispatchDefaults);
    client.patch.mockResolvedValue({
      ...rawDispatchDefaults,
      quiet_hours: '23:00-06:00 UTC',
      escalate_after: '45m',
    });

    const service = buildTingSettingsHttpAdapter(client);
    const defaults = await service.getDispatchDefaults();
    const updated = await service.updateDispatchDefaults({
      confidenceThreshold: 80,
      maxConcurrentRuns: 6,
      autoContinue: false,
      batchSize: 10,
      retryPolicy: {
        maxRetries: 5,
        retryDelaySeconds: 60,
        escalateOnExhaustion: false,
      },
      quietHours: '23:00-06:00 UTC',
      escalateAfter: '45m',
    });

    expect(defaults.quietHours).toBe('22:00–07:00 UTC');
    expect(defaults.escalateAfter).toBe('30m');
    expect(client.patch).toHaveBeenCalledWith('/settings/dispatch', {
      confidence_threshold: 80,
      max_concurrent_runs: 6,
      auto_continue: false,
      batch_size: 10,
      retry_policy: {
        max_retries: 5,
        retry_delay_seconds: 60,
        escalate_on_exhaustion: false,
      },
      quiet_hours: '23:00-06:00 UTC',
      escalate_after: '45m',
    });
    expect(updated.quietHours).toBe('23:00-06:00 UTC');
    expect(updated.escalateAfter).toBe('45m');
  });

  it('omits dispatch default patch fields that are left undefined', async () => {
    const client = makeClient();
    client.patch.mockResolvedValue(rawDispatchDefaults);

    await buildTingSettingsHttpAdapter(client).updateDispatchDefaults({});

    expect(client.patch).toHaveBeenCalledWith('/settings/dispatch', {});
  });

  it('gets and updates notification settings', async () => {
    const client = makeClient();
    client.get.mockResolvedValue(rawNotificationSettings);
    client.patch.mockResolvedValue({ ...rawNotificationSettings, webhook_url: null });

    const service = buildTingSettingsHttpAdapter(client);
    const settings = await service.getNotificationSettings();
    const updated = await service.updateNotificationSettings({
      channel: 'email',
      onRunPendingApproval: false,
      onRunMerged: false,
      onRunFailed: true,
      onSagaComplete: false,
      onDispatcherError: false,
      webhookUrl: null,
    });

    expect(settings.webhookUrl).toBe('https://hooks.example/slack');
    expect(client.patch).toHaveBeenCalledWith('/settings/notifications', {
      channel: 'email',
      on_run_pending_approval: false,
      on_run_merged: false,
      on_run_failed: true,
      on_saga_complete: false,
      on_dispatcher_error: false,
      webhook_url: null,
    });
    expect(updated.webhookUrl).toBeNull();
  });

  it('omits notification patch fields that are left undefined', async () => {
    const client = makeClient();
    client.patch.mockResolvedValue(rawNotificationSettings);

    await buildTingSettingsHttpAdapter(client).updateNotificationSettings({});

    expect(client.patch).toHaveBeenCalledWith('/settings/notifications', {});
  });

  it('satisfies ITingSettingsService', () => {
    const client = makeClient();
    const svc: ITingSettingsService = buildTingSettingsHttpAdapter(client);
    expect(typeof svc.getFlockConfig).toBe('function');
    expect(typeof svc.updateFlockConfig).toBe('function');
    expect(typeof svc.getDispatchDefaults).toBe('function');
    expect(typeof svc.updateDispatchDefaults).toBe('function');
    expect(typeof svc.getNotificationSettings).toBe('function');
    expect(typeof svc.updateNotificationSettings).toBe('function');
  });
});

describe('buildTingAuditLogHttpAdapter', () => {
  it('lists audit entries with encoded filters', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([rawAuditEntry]);

    const [entry] = await buildTingAuditLogHttpAdapter(client).listAuditEntries({
      kinds: ['dispatch.approved', 'run.failed'],
      actor: 'alice',
      since: '2026-05-01T00:00:00Z',
      until: '2026-05-31T00:00:00Z',
      limit: 20,
    });

    expect(client.get).toHaveBeenCalledWith(
      '/audit?kinds=dispatch.approved%2Crun.failed&actor=alice&since=2026-05-01T00%3A00%3A00Z&until=2026-05-31T00%3A00%3A00Z&limit=20',
    );
    expect(entry).toEqual({
      id: 'audit-1',
      kind: 'dispatch.approved',
      summary: 'Approved queue items',
      actor: 'alice',
      payload: { issueCount: 2 },
      createdAt: '2026-05-10T12:00:00Z',
    });
  });

  it('lists audit entries without filters when none are provided', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([]);

    const entries = await buildTingAuditLogHttpAdapter(client).listAuditEntries();

    expect(client.get).toHaveBeenCalledWith('/audit');
    expect(entries).toEqual([]);
  });

  it('satisfies IAuditLogService', () => {
    const client = makeClient();
    const svc: IAuditLogService = buildTingAuditLogHttpAdapter(client);
    expect(typeof svc.listAuditEntries).toBe('function');
  });
});

describe('research campaign artifact summary', () => {
  it('maps the summary the list now carries', async () => {
    const client = {
      get: vi.fn().mockResolvedValue([
        {
          id: '11111111-1111-4111-8111-111111111111',
          slug: 'a-campaign',
          name: 'A campaign',
          ownerId: 'dev',
          workflowId: '22222222-2222-4222-8222-222222222222',
          workflowVersion: '1.0.0',
          workflowName: 'Research Campaign',
          sessionId: 's',
          sessionName: 's',
          status: 'running',
          stageState: [],
          metadata: {},
          createdAt: '2026-08-08T00:00:00Z',
          updatedAt: '2026-08-08T00:00:00Z',
          artifact_summary: {
            artifact_count: 7,
            source_count: 4,
            critique_count: 1,
            learning_count: 1,
            follow_up_count: 2,
            published: true,
            known: true,
          },
        },
      ]),
    };
    const service = buildResearchHttpAdapter(client as never);

    const [campaign] = await service.listCampaigns();

    expect(campaign?.artifactSummary).toEqual({
      artifactCount: 7,
      sourceCount: 4,
      critiqueCount: 1,
      learningCount: 1,
      followUpCount: 2,
      published: true,
      known: true,
    });
  });

  it('leaves the summary null when the service does not send one', async () => {
    const client = {
      get: vi.fn().mockResolvedValue([
        {
          id: '11111111-1111-4111-8111-111111111111',
          slug: 'a-campaign',
          name: 'A campaign',
          ownerId: 'dev',
          workflowId: '22222222-2222-4222-8222-222222222222',
          workflowVersion: '1.0.0',
          workflowName: 'Research Campaign',
          sessionId: 's',
          sessionName: 's',
          status: 'running',
          stageState: [],
          metadata: {},
          createdAt: '2026-08-08T00:00:00Z',
          updatedAt: '2026-08-08T00:00:00Z',
        },
      ]),
    };
    const service = buildResearchHttpAdapter(client as never);

    const [campaign] = await service.listCampaigns();

    // Null reads as "not summarised", which the cards show as unknown rather
    // than as a campaign with no artifacts.
    expect(campaign?.artifactSummary).toBeNull();
  });
});
