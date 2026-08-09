import { describe, it, expect } from 'vitest';
import {
  createMockTingService,
  createMockDispatcherService,
  createMockTingSessionService,
  createMockTrackerService,
  createMockTingSettingsService,
  createMockAuditLogService,
  createMockDispatchBus,
  createMockWorkflowService,
  createMockResearchService,
} from './mock';

// ---------------------------------------------------------------------------
// createMockTingService
// ---------------------------------------------------------------------------

describe('createMockTingService', () => {
  it('returns 3 seed sagas', async () => {
    const svc = createMockTingService();
    const sagas = await svc.getSagas();
    expect(sagas).toHaveLength(7);
  });

  it('getSaga returns correct saga', async () => {
    const svc = createMockTingService();
    const saga = await svc.getSaga('00000000-0000-0000-0000-000000000001');
    expect(saga?.name).toBe('Auth Rewrite');
    expect(saga?.status).toBe('complete');
  });

  it('getSaga returns null for unknown id', async () => {
    const svc = createMockTingService();
    const result = await svc.getSaga('does-not-exist');
    expect(result).toBeNull();
  });

  it('getPhases returns phases for first saga', async () => {
    const svc = createMockTingService();
    const phases = await svc.getPhases('00000000-0000-0000-0000-000000000001');
    expect(phases).toHaveLength(3);
    expect(phases[0]?.name).toBe('Phase 1: Foundation');
  });

  it('getPhases returns empty array for unknown saga', async () => {
    const svc = createMockTingService();
    const phases = await svc.getPhases('saga-does-not-exist');
    expect(phases).toHaveLength(0);
  });

  it('createSaga adds a new saga', async () => {
    const svc = createMockTingService();
    const newSaga = await svc.createSaga('My new feature', 'niuulabs/volundr');
    expect(newSaga.name).toBe('My new feature');
    expect(newSaga.status).toBe('active');
    const all = await svc.getSagas();
    expect(all).toHaveLength(8);
  });

  it('commitSaga creates a saga from request', async () => {
    const svc = createMockTingService();
    const saga = await svc.commitSaga({
      name: 'Committed Saga',
      slug: 'committed-saga',
      description: 'A committed saga',
      repos: ['niuulabs/volundr'],
      baseBranch: 'main',
      phases: [{ name: 'Phase 1', runs: [] }],
    });
    expect(saga.name).toBe('Committed Saga');
    expect(saga.phaseSummary.total).toBe(1);
  });

  it('spawnPlanSession returns a session id', async () => {
    const svc = createMockTingService();
    const session = await svc.spawnPlanSession('spec', 'repo');
    expect(session.sessionId).toBeTruthy();
    expect(session.chatEndpoint).toBeNull();
  });

  it('extractStructure returns found: true with sample structure', async () => {
    const svc = createMockTingService();
    const result = await svc.extractStructure('some text');
    expect(result.found).toBe(true);
    expect(result.structure).not.toBeNull();
    expect(result.structure?.phases.length).toBeGreaterThan(0);
  });

  it('decompose returns two phases with one run each', async () => {
    const svc = createMockTingService();
    const phases = await svc.decompose('spec', 'repo');
    expect(phases).toHaveLength(2);
    expect(phases[0]?.runs).toHaveLength(1);
    expect(phases[1]?.name).toBe('Phase 2: API layer');
  });

  it('listRunMessages returns empty for a run without messages', async () => {
    const svc = createMockTingService();
    const messages = await svc.listRunMessages('run-without-messages');
    expect(messages).toEqual([]);
  });

  it('sendRunMessage attaches the run session id for known runs', async () => {
    const svc = createMockTingService();
    const message = await svc.sendRunMessage('00000000-0000-0000-0000-000000000010', 'status?');
    expect(message.sessionId).toBe('sess-001');
    expect(message.content).toBe('status?');
    expect(message.sender).toBe('user');

    const messages = await svc.listRunMessages('00000000-0000-0000-0000-000000000010');
    expect(messages).toHaveLength(1);
    expect(messages[0]?.id).toBe(message.id);
  });

  it('sendRunMessage falls back to a mock session for unknown runs', async () => {
    const svc = createMockTingService();
    const message = await svc.sendRunMessage('run-unknown', 'hello');
    expect(message.sessionId).toBe('mock-session');
  });

  it('sendRunMessage appends to an existing message list', async () => {
    const svc = createMockTingService();
    await svc.sendRunMessage('run-unknown', 'first');
    await svc.sendRunMessage('run-unknown', 'second');
    const messages = await svc.listRunMessages('run-unknown');
    expect(messages.map((m) => m.content)).toEqual(['first', 'second']);
  });

  it('assignWorkflow throws for an unknown saga', async () => {
    const svc = createMockTingService();
    await expect(svc.assignWorkflow('nope', 'wf-1')).rejects.toThrow('Saga not found: nope');
  });

  it('assignWorkflow sets workflow fields when an id is given', async () => {
    const svc = createMockTingService();
    const saga = await svc.assignWorkflow('00000000-0000-0000-0000-000000000001', 'wf-1');
    expect(saga.workflowId).toBe('wf-1');
    expect(saga.workflow).toBe('custom-workflow');
    expect(saga.workflowVersion).toBe('1.0.0');
  });

  it('assignWorkflow clears workflow fields when id is null', async () => {
    const svc = createMockTingService();
    await svc.assignWorkflow('00000000-0000-0000-0000-000000000001', 'wf-1');
    const cleared = await svc.assignWorkflow('00000000-0000-0000-0000-000000000001', null);
    expect(cleared.workflowId).toBeUndefined();
    expect(cleared.workflow).toBeUndefined();
    expect(cleared.workflowVersion).toBeUndefined();
  });

  it('assignTarget throws for an unknown saga', async () => {
    const svc = createMockTingService();
    await expect(svc.assignTarget('nope', { mode: 'default' })).rejects.toThrow(
      'Saga not found: nope',
    );
  });

  it('assignTarget pins the saga to an instance', async () => {
    const svc = createMockTingService();
    const saga = await svc.assignTarget('00000000-0000-0000-0000-000000000001', {
      mode: 'instance',
      instanceId: 'inst-9',
    });
    expect(saga.instanceId).toBe('inst-9');
    expect(saga.instanceName).toBe('Assigned target');
    expect(saga.targetTags).toBeUndefined();
    expect(saga.targetMatch).toBeUndefined();
  });

  it('assignTarget stores tags with an explicit match mode', async () => {
    const svc = createMockTingService();
    const saga = await svc.assignTarget('00000000-0000-0000-0000-000000000001', {
      mode: 'tags',
      tags: ['gpu'],
      match: 'any',
    });
    expect(saga.targetTags).toEqual(['gpu']);
    expect(saga.targetMatch).toBe('any');
    expect(saga.instanceId).toBeUndefined();
  });

  it('assignTarget defaults the tag match mode to all', async () => {
    const svc = createMockTingService();
    const saga = await svc.assignTarget('00000000-0000-0000-0000-000000000001', {
      mode: 'tags',
      tags: ['gpu', 'batch'],
    });
    expect(saga.targetMatch).toBe('all');
  });

  it('assignRepos stores multiple repositories with branches', async () => {
    const svc = createMockTingService();
    const repoRefs = [
      { repo: 'niuulabs/volundr', branch: 'dev' },
      { repo: 'niuulabs/infrastructure', branch: 'main' },
    ];
    const saga = await svc.assignRepos('00000000-0000-0000-0000-000000000001', repoRefs);
    expect(saga.repos).toEqual(['niuulabs/volundr', 'niuulabs/infrastructure']);
    expect(saga.repoRefs).toEqual(repoRefs);
    expect(saga.baseBranch).toBe('dev');
  });
});

// ---------------------------------------------------------------------------
// createMockDispatcherService
// ---------------------------------------------------------------------------

describe('createMockDispatcherService', () => {
  it('returns initial running state', async () => {
    const svc = createMockDispatcherService();
    const state = await svc.getState();
    expect(state?.running).toBe(true);
    expect(state?.threshold).toBe(70);
    expect(state?.maxConcurrentRuns).toBe(5);
  });

  it('setRunning toggles running state', async () => {
    const svc = createMockDispatcherService();
    await svc.setRunning(false);
    const state = await svc.getState();
    expect(state?.running).toBe(false);
  });

  it('setThreshold updates threshold', async () => {
    const svc = createMockDispatcherService();
    await svc.setThreshold(85);
    const state = await svc.getState();
    expect(state?.threshold).toBe(85);
  });

  it('setAutoContinue updates autoContinue', async () => {
    const svc = createMockDispatcherService();
    await svc.setAutoContinue(true);
    const state = await svc.getState();
    expect(state?.autoContinue).toBe(true);
  });

  it('getLog returns non-empty log', async () => {
    const svc = createMockDispatcherService();
    const log = await svc.getLog();
    expect(log.length).toBeGreaterThan(0);
  });

  it('setRunning appends to log', async () => {
    const svc = createMockDispatcherService();
    await svc.setRunning(false);
    const log = await svc.getLog();
    expect(log.some((l) => l.includes('running'))).toBe(true);
  });

  it('getActivityLog returns all seed events by default', async () => {
    const svc = createMockDispatcherService();
    const events = await svc.getActivityLog();
    expect(events).toHaveLength(4);
    expect(events[0]?.event).toBe('run.state_changed');
  });

  it('getActivityLog applies an explicit limit', async () => {
    const svc = createMockDispatcherService();
    const events = await svc.getActivityLog(2);
    expect(events).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------
// createMockTingSessionService
// ---------------------------------------------------------------------------

describe('createMockTingSessionService', () => {
  it('returns 2 seed sessions', async () => {
    const svc = createMockTingSessionService();
    const sessions = await svc.getSessions();
    expect(sessions).toHaveLength(2);
  });

  it('getSession returns correct session', async () => {
    const svc = createMockTingSessionService();
    const session = await svc.getSession('sess-001');
    expect(session?.runName).toBe('Implement OIDC flow');
    expect(session?.status).toBe('complete');
  });

  it('getSession returns null for unknown id', async () => {
    const svc = createMockTingSessionService();
    const result = await svc.getSession('does-not-exist');
    expect(result).toBeNull();
  });

  it('approve changes session status to approved', async () => {
    const svc = createMockTingSessionService();
    await svc.approve('sess-002');
    const session = await svc.getSession('sess-002');
    expect(session?.status).toBe('approved');
  });

  it('approve on unknown session does not throw', async () => {
    const svc = createMockTingSessionService();
    await expect(svc.approve('no-such-session')).resolves.not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// createMockTrackerService
// ---------------------------------------------------------------------------

describe('createMockTrackerService', () => {
  it('returns 2 seed projects', async () => {
    const svc = createMockTrackerService();
    const projects = await svc.listProjects();
    expect(projects).toHaveLength(2);
  });

  it('getProject returns known project', async () => {
    const svc = createMockTrackerService();
    const project = await svc.getProject('proj-niuu-core');
    expect(project.name).toBe('Niuu Core');
    expect(project.milestoneCount).toBe(8);
  });

  it('getProject throws for unknown project', async () => {
    const svc = createMockTrackerService();
    await expect(svc.getProject('unknown')).rejects.toThrow();
  });

  it('listMilestones filters by projectId', async () => {
    const svc = createMockTrackerService();
    const milestones = await svc.listMilestones('proj-niuu-core');
    expect(milestones.every((m) => m.projectId === 'proj-niuu-core')).toBe(true);
  });

  it('listIssues returns issues for project', async () => {
    const svc = createMockTrackerService();
    const issues = await svc.listIssues('proj-niuu-core');
    expect(issues.length).toBeGreaterThan(0);
  });

  it('importProject creates a saga', async () => {
    const svc = createMockTrackerService();
    const saga = await svc.importProject('proj-niuu-core', ['niuulabs/volundr']);
    expect(saga.name).toBe('Niuu Core');
    expect(saga.repos).toEqual(['niuulabs/volundr']);
    expect(saga.status).toBe('active');
  });

  it('listIssues narrows by milestone id', async () => {
    const svc = createMockTrackerService();
    const filtered = await svc.listIssues('proj-niuu-core', 'ms-auth');
    expect(filtered.length).toBeGreaterThan(0);
    expect(filtered.every((issue) => issue.milestoneId === 'ms-auth')).toBe(true);
    expect(await svc.listIssues('proj-niuu-core', 'ms-observatory')).toEqual([]);
  });

  it('importProject falls back to the project id when the project is unknown', async () => {
    const svc = createMockTrackerService();
    const saga = await svc.importProject('proj-unknown', ['niuulabs/volundr']);
    expect(saga.name).toBe('proj-unknown');
    expect(saga.slug).toBe('proj-unknown');
  });

  it('importProject uses the base branch for derived repo refs', async () => {
    const svc = createMockTrackerService();
    const saga = await svc.importProject('proj-niuu-core', ['niuulabs/volundr'], 'dev');
    expect(saga.baseBranch).toBe('dev');
    expect(saga.repoRefs).toEqual([{ repo: 'niuulabs/volundr', branch: 'dev' }]);
  });

  it('importProject prefers explicit repo refs from options', async () => {
    const svc = createMockTrackerService();
    const saga = await svc.importProject('proj-niuu-core', [], undefined, null, {
      repoRefs: [
        { repo: 'niuulabs/ting', branch: 'feat/x' },
        { repo: 'niuulabs/volundr', branch: 'main' },
      ],
    });
    expect(saga.repos).toEqual(['niuulabs/ting', 'niuulabs/volundr']);
    expect(saga.baseBranch).toBe('feat/x');
  });

  it('importProject pins to an instance target', async () => {
    const svc = createMockTrackerService();
    const saga = await svc.importProject('proj-niuu-core', ['niuulabs/volundr'], 'main', null, {
      target: { mode: 'instance', instanceId: 'inst-7' },
    });
    expect(saga.instanceId).toBe('inst-7');
    expect(saga.targetTags).toBeUndefined();
  });

  it('importProject stores tag targets and defaults match to all', async () => {
    const svc = createMockTrackerService();
    const saga = await svc.importProject('proj-niuu-core', ['niuulabs/volundr'], 'main', null, {
      target: { mode: 'tags', tags: ['gpu'] },
    });
    expect(saga.targetTags).toEqual(['gpu']);
    expect(saga.targetMatch).toBe('all');
    expect(saga.instanceId).toBeUndefined();
  });

  it('importProject keeps an explicit tag match mode', async () => {
    const svc = createMockTrackerService();
    const saga = await svc.importProject('proj-niuu-core', ['niuulabs/volundr'], 'main', null, {
      target: { mode: 'tags', tags: ['gpu', 'batch'], match: 'any' },
    });
    expect(saga.targetMatch).toBe('any');
  });

  it('importProject falls back to the legacy instanceId argument', async () => {
    const svc = createMockTrackerService();
    const saga = await svc.importProject('proj-niuu-core', ['niuulabs/volundr'], 'main', 'inst-3');
    expect(saga.instanceId).toBe('inst-3');
  });
});

// ---------------------------------------------------------------------------
// createMockDispatchBus
// ---------------------------------------------------------------------------

describe('createMockDispatchBus', () => {
  it('getQueue returns an empty queue', async () => {
    const bus = createMockDispatchBus();
    expect(await bus.getQueue()).toEqual([]);
  });

  it('getClusters returns copies of the seed clusters', async () => {
    const bus = createMockDispatchBus();
    const clusters = await bus.getClusters();
    expect(clusters.length).toBeGreaterThan(0);
    expect(clusters[0]).toHaveProperty('connectionId');
    expect(clusters[0]).toHaveProperty('name');
  });

  it('approve uses the item connection id when present', async () => {
    const bus = createMockDispatchBus();
    const [result] = await bus.approve(
      [{ sagaId: 's1', issueId: 'NIU-1', repo: 'niuulabs/volundr', connectionId: 'conn-item' }],
      { connectionId: 'conn-options' },
    );
    expect(result?.clusterName).toBe('conn-item');
    expect(result?.sessionId).toBe('sess-NIU-1');
    expect(result?.status).toBe('spawned');
  });

  it('approve falls back to the options connection id', async () => {
    const bus = createMockDispatchBus();
    const [result] = await bus.approve(
      [{ sagaId: 's1', issueId: 'NIU-2', repo: 'niuulabs/volundr' }],
      { connectionId: 'conn-options' },
    );
    expect(result?.clusterName).toBe('conn-options');
  });

  it('approve defaults the cluster to local', async () => {
    const bus = createMockDispatchBus();
    const [result] = await bus.approve([
      { sagaId: 's1', issueId: 'NIU-3', repo: 'niuulabs/volundr' },
    ]);
    expect(result?.clusterName).toBe('local');
  });

  it('dispatch resolves without error', async () => {
    const bus = createMockDispatchBus();
    await expect(bus.dispatch('run-1')).resolves.toBeUndefined();
  });

  it('dispatchBatch reports all runs as dispatched', async () => {
    const bus = createMockDispatchBus();
    const result = await bus.dispatchBatch(['run-1', 'run-2']);
    expect(result.dispatched).toEqual(['run-1', 'run-2']);
    expect(result.failed).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// createMockWorkflowService
// ---------------------------------------------------------------------------

describe('createMockWorkflowService', () => {
  it('listWorkflows returns the seed workflows', async () => {
    const svc = createMockWorkflowService();
    const workflows = await svc.listWorkflows();
    expect(workflows.length).toBeGreaterThan(0);
    expect(workflows[0]?.nodes.length).toBeGreaterThan(0);
  });

  it('getWorkflow returns a workflow by id', async () => {
    const svc = createMockWorkflowService();
    const workflow = await svc.getWorkflow('00000000-0000-0000-0000-000000000a01');
    expect(workflow?.name).toContain('ship');
  });

  it('getWorkflow returns null for an unknown id', async () => {
    const svc = createMockWorkflowService();
    expect(await svc.getWorkflow('nope')).toBeNull();
  });

  it('saveWorkflow upserts and deleteWorkflow removes', async () => {
    const svc = createMockWorkflowService();
    const existing = await svc.getWorkflow('00000000-0000-0000-0000-000000000a01');
    const saved = await svc.saveWorkflow({ ...existing!, id: 'wf-new', name: 'fresh workflow' });
    expect(saved.id).toBe('wf-new');
    expect((await svc.getWorkflow('wf-new'))?.name).toBe('fresh workflow');

    await svc.deleteWorkflow('wf-new');
    expect(await svc.getWorkflow('wf-new')).toBeNull();
  });

  it('launchWorkflow throws for an unknown workflow', async () => {
    const svc = createMockWorkflowService();
    await expect(svc.launchWorkflow('nope', { prompt: 'go' })).rejects.toThrow(
      'Workflow nope not found',
    );
  });

  it('launchWorkflow slugifies the prompt', async () => {
    const svc = createMockWorkflowService();
    const result = await svc.launchWorkflow('00000000-0000-0000-0000-000000000a01', {
      prompt: 'Ship The Release!',
    });
    expect(result.slug).toBe('ship-the-release');
    expect(result.status).toBe('starting');
    expect(result.clusterName).toBe('mock');
    expect(result.sessionName).toContain('ship-the-release');
  });

  it('launchWorkflow falls back to the workflow name when prompt is empty', async () => {
    const svc = createMockWorkflowService();
    const result = await svc.launchWorkflow('00000000-0000-0000-0000-000000000a01', {
      prompt: '',
    });
    expect(result.slug).toContain('ship');
  });

  it('launchWorkflow defaults the slug when the prompt has no characters to keep', async () => {
    const svc = createMockWorkflowService();
    const result = await svc.launchWorkflow('00000000-0000-0000-0000-000000000a01', {
      prompt: '!!!',
    });
    expect(result.slug).toBe('workflow');
    expect(result.sessionName).toContain('workflow');
  });

  it('launchWorkflow honours an explicit session name', async () => {
    const svc = createMockWorkflowService();
    const result = await svc.launchWorkflow('00000000-0000-0000-0000-000000000a01', {
      prompt: 'go',
      sessionName: 'my-session',
    });
    expect(result.sessionName).toBe('my-session');
  });
});

// ---------------------------------------------------------------------------
// createMockResearchService
// ---------------------------------------------------------------------------

describe('createMockResearchService', () => {
  it('listCampaigns returns the seed campaign', async () => {
    const svc = createMockResearchService();
    const campaigns = await svc.listCampaigns();
    expect(campaigns).toHaveLength(1);
    expect(campaigns[0]?.slug).toBe('rag-landscape');
  });

  it('getCampaign returns the seed campaign detail', async () => {
    const svc = createMockResearchService();
    const campaign = await svc.getCampaign('rag-landscape');
    expect(campaign?.status).toBe('running');
    expect(campaign?.artifacts).toHaveLength(2);
  });

  it('getCampaign returns null for an unknown slug', async () => {
    const svc = createMockResearchService();
    expect(await svc.getCampaign('nope')).toBeNull();
  });

  it('createCampaign derives the name and slug from the question', async () => {
    const svc = createMockResearchService();
    const campaign = await svc.createCampaign({ question: 'How do agents fail?' });
    expect(campaign.name).toBe('How do agents fail?');
    expect(campaign.slug).toBe('how-do-agents-fail');
    expect(campaign.status).toBe('running');
    expect(campaign.activeStageId).toBe('frame');
  });

  it('createCampaign uses an explicit workflow id when known', async () => {
    const svc = createMockResearchService();
    const campaign = await svc.createCampaign({
      question: 'q',
      name: 'Pinned workflow',
      workflowId: '00000000-0000-0000-0000-000000000a02',
    });
    expect(campaign.workflowId).toBe('00000000-0000-0000-0000-000000000a02');
    expect(campaign.workflowName).toContain('deep-review');
  });

  it('createCampaign falls back to a seed workflow for unknown workflow ids', async () => {
    const svc = createMockResearchService();
    const campaign = await svc.createCampaign({
      question: 'q',
      name: 'Fallback workflow',
      workflowId: 'wf-missing',
    });
    expect(campaign.workflowId).toBe('00000000-0000-0000-0000-000000000a01');
  });

  it('createCampaign generates a placeholder slug for symbol-only names', async () => {
    const svc = createMockResearchService();
    const campaign = await svc.createCampaign({ question: '???', name: '###' });
    expect(campaign.slug).toBe('campaign-2');
  });

  it('createCampaign records metadata with defaults for omitted fields', async () => {
    const svc = createMockResearchService();
    const campaign = await svc.createCampaign({ question: 'metadata defaults' });
    const detail = await svc.getCampaign(campaign.slug);
    expect(detail?.metadata).toMatchObject({
      question: 'metadata defaults',
      mode: 'exploratory',
      audience: '',
      deliverable: '',
      success: '',
      constraints: [],
      repo: '',
      branch: '',
    });
  });

  it('createCampaign keeps explicit metadata fields', async () => {
    const svc = createMockResearchService();
    const campaign = await svc.createCampaign({
      question: 'explicit metadata',
      mode: 'focused',
      audience: 'execs',
      deliverable: 'memo',
      success: 'decision made',
      constraints: ['cited sources'],
      repo: 'niuulabs/volundr',
      branch: 'main',
    });
    const detail = await svc.getCampaign(campaign.slug);
    expect(detail?.metadata).toMatchObject({
      mode: 'focused',
      audience: 'execs',
      deliverable: 'memo',
      success: 'decision made',
      constraints: ['cited sources'],
      repo: 'niuulabs/volundr',
      branch: 'main',
    });
  });

  it('updateCampaign throws for an unknown slug', async () => {
    const svc = createMockResearchService();
    await expect(svc.updateCampaign('nope', { name: 'x' })).rejects.toThrow(
      'Campaign nope not found',
    );
  });

  it('updateCampaign patches name, status and metadata', async () => {
    const svc = createMockResearchService();
    const updated = await svc.updateCampaign('rag-landscape', {
      name: 'RAG landscape v2',
      status: 'paused',
      metadata: { audience: 'engineering' },
    });
    expect(updated.name).toBe('RAG landscape v2');
    expect(updated.status).toBe('paused');
    const detail = await svc.getCampaign('rag-landscape');
    expect(detail?.metadata.audience).toBe('engineering');
    expect(detail?.metadata.question).toBe('What does the RAG tooling landscape look like?');
  });

  it('updateCampaign keeps current values when the patch is empty', async () => {
    const svc = createMockResearchService();
    const updated = await svc.updateCampaign('rag-landscape', {});
    expect(updated.name).toBe('RAG landscape');
    expect(updated.status).toBe('running');
  });

  it('deleteCampaign removes the campaign', async () => {
    const svc = createMockResearchService();
    await svc.deleteCampaign('rag-landscape');
    expect(await svc.getCampaign('rag-landscape')).toBeNull();
    expect(await svc.listCampaigns()).toHaveLength(0);
  });

  it('getArtifact returns content for a known artifact', async () => {
    const svc = createMockResearchService();
    const artifact = await svc.getArtifact(
      'rag-landscape',
      'research/campaigns/rag-landscape/brief.md',
    );
    expect(artifact?.title).toBe('Brief');
    expect(artifact?.content).toContain('Mock content');
  });

  it('getArtifact returns null for unknown paths', async () => {
    const svc = createMockResearchService();
    expect(await svc.getArtifact('rag-landscape', 'missing.md')).toBeNull();
    expect(await svc.getArtifact('nope', 'missing.md')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// createMockTingSettingsService
// ---------------------------------------------------------------------------

describe('createMockTingSettingsService', () => {
  it('returns flock config', async () => {
    const svc = createMockTingSettingsService();
    const config = await svc.getFlockConfig();
    expect(config.flockName).toBe('Niuu Core');
    expect(config.defaultBaseBranch).toBe('main');
    expect(config.defaultTrackerType).toBe('linear');
    expect(config.maxActiveSagas).toBe(5);
    expect(config.autoCreateMilestones).toBe(true);
  });

  it('updateFlockConfig patches the config', async () => {
    const svc = createMockTingSettingsService();
    const updated = await svc.updateFlockConfig({ flockName: 'Updated Flock', maxActiveSagas: 10 });
    expect(updated.flockName).toBe('Updated Flock');
    expect(updated.maxActiveSagas).toBe(10);
    expect(updated.defaultBaseBranch).toBe('main');
  });

  it('updateFlockConfig persists changes', async () => {
    const svc = createMockTingSettingsService();
    await svc.updateFlockConfig({ flockName: 'Persisted' });
    const config = await svc.getFlockConfig();
    expect(config.flockName).toBe('Persisted');
  });

  it('returns dispatch defaults', async () => {
    const svc = createMockTingSettingsService();
    const defaults = await svc.getDispatchDefaults();
    expect(defaults.confidenceThreshold).toBe(70);
    expect(defaults.maxConcurrentRuns).toBe(3);
    expect(defaults.batchSize).toBe(10);
    expect(defaults.autoContinue).toBe(false);
    expect(defaults.retryPolicy.maxRetries).toBe(2);
    expect(defaults.retryPolicy.retryDelaySeconds).toBe(30);
    expect(defaults.retryPolicy.escalateOnExhaustion).toBe(true);
  });

  it('updateDispatchDefaults patches threshold', async () => {
    const svc = createMockTingSettingsService();
    const updated = await svc.updateDispatchDefaults({ confidenceThreshold: 85 });
    expect(updated.confidenceThreshold).toBe(85);
    expect(updated.batchSize).toBe(10);
  });

  it('updateDispatchDefaults patches retryPolicy', async () => {
    const svc = createMockTingSettingsService();
    const updated = await svc.updateDispatchDefaults({
      retryPolicy: { maxRetries: 5, retryDelaySeconds: 60, escalateOnExhaustion: false },
    });
    expect(updated.retryPolicy.maxRetries).toBe(5);
    expect(updated.retryPolicy.retryDelaySeconds).toBe(60);
    expect(updated.retryPolicy.escalateOnExhaustion).toBe(false);
  });

  it('updateDispatchDefaults persists changes', async () => {
    const svc = createMockTingSettingsService();
    await svc.updateDispatchDefaults({ confidenceThreshold: 90 });
    const defaults = await svc.getDispatchDefaults();
    expect(defaults.confidenceThreshold).toBe(90);
  });

  it('returns notification settings', async () => {
    const svc = createMockTingSettingsService();
    const settings = await svc.getNotificationSettings();
    expect(settings.channel).toBe('telegram');
    expect(settings.onRunPendingApproval).toBe(true);
    expect(settings.onRunFailed).toBe(true);
    expect(settings.onRunMerged).toBe(false);
    expect(settings.webhookUrl).toBeNull();
  });

  it('updateNotificationSettings patches channel', async () => {
    const svc = createMockTingSettingsService();
    const updated = await svc.updateNotificationSettings({
      channel: 'webhook',
      webhookUrl: 'https://example.com/hook',
    });
    expect(updated.channel).toBe('webhook');
    expect(updated.webhookUrl).toBe('https://example.com/hook');
  });

  it('updateNotificationSettings persists changes', async () => {
    const svc = createMockTingSettingsService();
    await svc.updateNotificationSettings({ channel: 'none' });
    const settings = await svc.getNotificationSettings();
    expect(settings.channel).toBe('none');
  });
});

// ---------------------------------------------------------------------------
// createMockAuditLogService
// ---------------------------------------------------------------------------

describe('createMockAuditLogService', () => {
  it('returns all seed entries when no filter', async () => {
    const svc = createMockAuditLogService();
    const entries = await svc.listAuditEntries();
    expect(entries.length).toBe(6);
  });

  it('filters by kinds', async () => {
    const svc = createMockAuditLogService();
    const entries = await svc.listAuditEntries({
      kinds: ['run.dispatched', 'run.merged'],
    });
    expect(entries.every((e) => ['run.dispatched', 'run.merged'].includes(e.kind))).toBe(true);
    expect(entries).toHaveLength(2);
  });

  it('filters by actor', async () => {
    const svc = createMockAuditLogService();
    const entries = await svc.listAuditEntries({ actor: 'system' });
    expect(entries.every((e) => e.actor === 'system')).toBe(true);
    expect(entries).toHaveLength(1);
  });

  it('filters by since', async () => {
    const svc = createMockAuditLogService();
    const entries = await svc.listAuditEntries({ since: '2026-01-13T00:00:00Z' });
    expect(entries.every((e) => e.createdAt >= '2026-01-13T00:00:00Z')).toBe(true);
  });

  it('filters by until', async () => {
    const svc = createMockAuditLogService();
    const entries = await svc.listAuditEntries({ until: '2026-01-10T09:00:00Z' });
    expect(entries.every((e) => e.createdAt <= '2026-01-10T09:00:00Z')).toBe(true);
  });

  it('applies limit', async () => {
    const svc = createMockAuditLogService();
    const entries = await svc.listAuditEntries({ limit: 2 });
    expect(entries).toHaveLength(2);
  });

  it('returns empty array when kind filter has no matches', async () => {
    const svc = createMockAuditLogService();
    const entries = await svc.listAuditEntries({ kinds: ['saga.completed'] });
    expect(entries).toHaveLength(0);
  });

  it('handles combined filters', async () => {
    const svc = createMockAuditLogService();
    const entries = await svc.listAuditEntries({
      kinds: [
        'dispatcher.started',
        'dispatcher.stopped',
        'dispatcher.threshold_changed',
        'dispatcher.batch_size_changed',
      ],
      actor: 'system',
    });
    expect(entries.every((e) => e.actor === 'system')).toBe(true);
    expect(entries.every((e) => e.kind.startsWith('dispatcher.'))).toBe(true);
  });
});
