import { describe, it, expect, vi } from 'vitest';
import {
  createMockVolundrService,
  createMockClusterAdapter,
  createMockSessionStore,
  createMockPtyStream,
  createMockMetricsStream,
  createMockFileSystemPort,
} from './mock';

// ---------------------------------------------------------------------------
// IVolundrService
// ---------------------------------------------------------------------------

describe('createMockVolundrService', () => {
  it('returns an object implementing IVolundrService', () => {
    const svc = createMockVolundrService();
    expect(typeof svc.getSessions).toBe('function');
    expect(typeof svc.getSession).toBe('function');
    expect(typeof svc.subscribe).toBe('function');
  });

  it('getSessions returns seeded sessions', async () => {
    const svc = createMockVolundrService();
    const sessions = await svc.getSessions();
    expect(sessions.length).toBeGreaterThan(0);
    expect(sessions[0]).toHaveProperty('id');
    expect(sessions[0]).toHaveProperty('status');
  });

  it('getSession returns a session by id', async () => {
    const svc = createMockVolundrService();
    const session = await svc.getSession('sess-1');
    expect(session).not.toBeNull();
    expect(session?.id).toBe('sess-1');
  });

  it('getSession returns null for unknown id', async () => {
    const svc = createMockVolundrService();
    const session = await svc.getSession('does-not-exist');
    expect(session).toBeNull();
  });

  it('listExternalSessions returns seeded external sessions', async () => {
    const svc = createMockVolundrService();
    const sessions = await svc.listExternalSessions();
    expect(sessions.length).toBeGreaterThan(0);
    expect(sessions[0]).toHaveProperty('provider');
    expect(sessions[0]).toHaveProperty('externalId');
    expect(sessions[0]).toHaveProperty('workspaceExists');
  });

  it('importExternalSession imports a known session and marks it imported', async () => {
    const svc = createMockVolundrService();
    const [importable] = (await svc.listExternalSessions()).filter(
      (session) => session.workspaceExists && !session.importedSessionId,
    );
    expect(importable).toBeDefined();

    const imported = await svc.importExternalSession(
      importable!.provider,
      importable!.externalId,
      'renamed',
    );
    expect(imported.name).toBe('renamed');
    expect(imported.origin).toBe(importable!.harness);
    expect(imported.externalSessionId).toBe(importable!.externalId);

    const refreshed = await svc.listExternalSessions();
    const row = refreshed.find((session) => session.externalId === importable!.externalId);
    expect(row?.importedSessionId).toBe(imported.id);

    const sessions = await svc.getSessions();
    expect(sessions.some((session) => session.id === imported.id)).toBe(true);

    await expect(
      svc.importExternalSession(importable!.provider, importable!.externalId),
    ).rejects.toThrow(/already imported/);
  });

  it('importExternalSession rejects unknown sessions and missing workspaces', async () => {
    const svc = createMockVolundrService();
    await expect(svc.importExternalSession('claude-code', 'nope')).rejects.toThrow(
      /Unknown external session/,
    );

    const missingWorkspace = (await svc.listExternalSessions()).find(
      (session) => !session.workspaceExists,
    );
    expect(missingWorkspace).toBeDefined();
    await expect(
      svc.importExternalSession(missingWorkspace!.provider, missingWorkspace!.externalId),
    ).rejects.toThrow(/Workspace directory missing/);
  });

  it('getActiveSessions returns only active statuses', async () => {
    const svc = createMockVolundrService();
    const active = await svc.getActiveSessions();
    for (const s of active) {
      expect(['starting', 'provisioning', 'running']).toContain(s.status);
    }
  });

  it('getStats returns numeric fields', async () => {
    const svc = createMockVolundrService();
    const stats = await svc.getStats();
    expect(typeof stats.activeSessions).toBe('number');
    expect(typeof stats.costToday).toBe('number');
    expect(typeof stats.tokensToday).toBe('number');
  });

  it('subscribe calls back immediately with session list', () => {
    const svc = createMockVolundrService();
    const cb = vi.fn();
    const unsubscribe = svc.subscribe(cb);
    expect(cb).toHaveBeenCalledOnce();
    expect(cb.mock.calls[0]?.[0]).toBeInstanceOf(Array);
    unsubscribe();
  });

  it('subscribeStats returns an unsubscribe function', () => {
    const svc = createMockVolundrService();
    const unsub = svc.subscribeStats(vi.fn());
    expect(typeof unsub).toBe('function');
    unsub();
  });

  it('startSession returns a session with the given name', async () => {
    const svc = createMockVolundrService();
    const session = await svc.startSession({
      name: 'my-session',
      source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
      model: 'claude-sonnet',
    });
    expect(session.name).toBe('my-session');
    expect(session.status).toBe('starting');
  });

  it('connectSession returns a manual session', async () => {
    const svc = createMockVolundrService();
    const session = await svc.connectSession({ name: 'ext', hostname: 'host.example.com' });
    expect(session.origin).toBe('manual');
    expect(session.hostname).toBe('host.example.com');
  });

  it('sendMessage echoes back the content', async () => {
    const svc = createMockVolundrService();
    const msg = await svc.sendMessage('sess-1', 'hello');
    expect(msg.role).toBe('assistant');
    expect(msg.content).toContain('hello');
    expect(msg.sessionId).toBe('sess-1');
  });

  it('getFeatures returns a feature flags object', async () => {
    const svc = createMockVolundrService();
    const features = await svc.getFeatures();
    expect(typeof features.localMountsEnabled).toBe('boolean');
    expect(typeof features.fileManagerEnabled).toBe('boolean');
    expect(typeof features.miniMode).toBe('boolean');
  });

  it('getIdentity returns a mock identity', async () => {
    const svc = createMockVolundrService();
    const identity = await svc.getIdentity();
    expect(identity.email).toBe('dev@niuu.world');
    expect(identity.roles).toContain('user');
  });

  it('createCredential returns a credential with keys from the data', async () => {
    const svc = createMockVolundrService();
    const cred = await svc.createCredential({
      name: 'my-key',
      secretType: 'api_key',
      data: { token: 'abc123' },
    });
    expect(cred.name).toBe('my-key');
    expect(cred.secretType).toBe('api_key');
    expect(cred.keys).toContain('token');
  });

  it('createToken returns a token with the given name', async () => {
    const svc = createMockVolundrService();
    const result = await svc.createToken('ci-bot');
    expect(result.name).toBe('ci-bot');
    expect(result.token).toBeTruthy();
  });

  it('toggleFeature returns a FeatureModule with updated enabled flag', async () => {
    const svc = createMockVolundrService();
    const module = await svc.toggleFeature('my-feature', true);
    expect(module.key).toBe('my-feature');
    expect(module.enabled).toBe(true);
  });

  it('bulkDeleteWorkspaces returns a result object', async () => {
    const svc = createMockVolundrService();
    const result = await svc.bulkDeleteWorkspaces(['sess-1']);
    expect(result).toHaveProperty('deleted');
    expect(result).toHaveProperty('failed');
  });

  it('reprovisionUser returns success result', async () => {
    const svc = createMockVolundrService();
    const result = await svc.reprovisionUser('u1');
    expect(result.success).toBe(true);
    expect(result.userId).toBe('u1');
  });

  it('createTenant returns a tenant with the given name', async () => {
    const svc = createMockVolundrService();
    const tenant = await svc.createTenant({
      name: 'acme',
      tier: 'pro',
      maxSessions: 10,
      maxStorageGb: 50,
    });
    expect(tenant.name).toBe('acme');
  });

  it('createPullRequest returns a PR object', async () => {
    const svc = createMockVolundrService();
    const pr = await svc.createPullRequest('sess-1', 'My PR');
    expect(pr.title).toBe('My PR');
    expect(pr.status).toBe('open');
  });

  it('saveLaunchSpec generates an id when not provided', async () => {
    const svc = createMockVolundrService();
    const preset = await svc.saveLaunchSpec({
      name: 'fast',
      description: 'fast preset',
      isDefault: false,
      cliTool: 'claude',
      workloadType: 'default',
      model: null,
      systemPrompt: null,
      resourceConfig: {},
      mcpServers: [],
      terminalSidecar: { enabled: false, allowedCommands: [] },
      skills: [],
      rules: [],
      envVars: {},
      envSecretRefs: [],
      source: null,
      integrationIds: [],
      setupScripts: [],
      workloadConfig: {},
    });
    expect(preset.id).toBeTruthy();
    expect(preset.name).toBe('fast');
  });

  it('saveLaunchSpec replaces an existing spec with the same id', async () => {
    const svc = createMockVolundrService();
    const base = {
      name: 'original',
      description: '',
      isDefault: false,
      cliTool: 'claude',
      workloadType: 'default',
      model: null,
      systemPrompt: null,
      resourceConfig: {},
      mcpServers: [],
      terminalSidecar: { enabled: false, allowedCommands: [] },
      skills: [],
      rules: [],
      envVars: {},
      envSecretRefs: [],
      source: null,
      integrationIds: [],
      setupScripts: [],
      workloadConfig: {},
    } as const;

    const first = await svc.saveLaunchSpec({ ...base, id: 'spec-dup' });
    expect(first.name).toBe('original');

    const second = await svc.saveLaunchSpec({ ...base, id: 'spec-dup', name: 'replaced' });
    expect(second.name).toBe('replaced');

    const userSpecs = await svc.getLaunchSpecs('user');
    const matching = userSpecs.filter((spec) => spec.id === 'spec-dup');
    expect(matching).toHaveLength(1);
    expect(matching[0]?.name).toBe('replaced');
  });

  it('startSession honours an explicit instance id', async () => {
    const svc = createMockVolundrService();
    const session = await svc.startSession({
      name: 'pinned',
      source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
      model: 'claude-sonnet',
      instanceId: 'inst-custom',
    });
    expect(session.instanceId).toBe('inst-custom');
    expect(session.instanceName).toBe('Selected Mock Volundr');
  });

  it('startSession routes gpu-tagged sessions to the gpu instance', async () => {
    const svc = createMockVolundrService();
    const session = await svc.startSession({
      name: 'gpu-job',
      source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
      model: 'claude-sonnet',
      targetTags: ['gpu'],
    });
    expect(session.instanceId).toBe('mock-volundr-gpu');
    expect(session.instanceName).toBe('Mock Volundr GPU');
  });

  it('startSession defaults to the default instance for non-gpu tags', async () => {
    const svc = createMockVolundrService();
    const session = await svc.startSession({
      name: 'cpu-job',
      source: { type: 'git', repo: 'github.com/niuulabs/volundr', branch: 'main' },
      model: 'claude-sonnet',
      targetTags: ['batch'],
    });
    expect(session.instanceId).toBe('mock-volundr-default');
    expect(session.instanceName).toBe('Mock Volundr');
  });

  it('evaluatePermissionAutoApproval approves allowlisted commands', async () => {
    const svc = createMockVolundrService();
    const decision = await svc.evaluatePermissionAutoApproval('sess-1', {
      requestId: 'perm-1',
      toolName: 'Bash',
      description: 'start dev server',
      command: './start-dev --port 3000',
    });
    expect(decision.canAutoApprove).toBe(true);
    expect(decision.reason).toBe('allowed');
    expect(decision.command).toBe('./start-dev --port 3000');
    expect(decision.matchedPattern).toBe('^\\s*\\./start-dev');
  });

  it('evaluatePermissionAutoApproval rejects other commands', async () => {
    const svc = createMockVolundrService();
    const decision = await svc.evaluatePermissionAutoApproval('sess-1', {
      requestId: 'perm-2',
      toolName: 'Bash',
      description: 'delete everything',
      command: 'rm -rf /',
    });
    expect(decision.canAutoApprove).toBe(false);
    expect(decision.reason).toBe('no_allowlist_match');
    expect(decision.matchedPattern).toBeNull();
  });

  it('evaluatePermissionAutoApproval handles a missing command', async () => {
    const svc = createMockVolundrService();
    const decision = await svc.evaluatePermissionAutoApproval('sess-1', {
      requestId: 'perm-3',
      toolName: 'WebFetch',
      description: 'fetch a page',
    });
    expect(decision.canAutoApprove).toBe(false);
    expect(decision.command).toBeNull();
  });

  it('importExternalSession rejects sessions outside the allowed mounts', async () => {
    const svc = createMockVolundrService();
    const denied = (await svc.listExternalSessions()).find(
      (session) => session.workspaceExists && !session.workspaceAllowed,
    );
    expect(denied).toBeDefined();
    await expect(svc.importExternalSession(denied!.provider, denied!.externalId)).rejects.toThrow(
      /outside the allowed mount prefixes/,
    );
  });

  it('importExternalSession falls back to the external title when no name is given', async () => {
    const svc = createMockVolundrService();
    const importable = (await svc.listExternalSessions()).find(
      (session) =>
        session.workspaceExists && session.workspaceAllowed && !session.importedSessionId,
    );
    expect(importable).toBeDefined();
    const imported = await svc.importExternalSession(importable!.provider, importable!.externalId);
    expect(imported.name).toBe(importable!.title.trim());
  });

  it('getWorkflowGates returns an empty list for sessions without gates', async () => {
    const svc = createMockVolundrService();
    expect(await svc.getWorkflowGates('sess-1')).toEqual([]);
  });

  it('resolveWorkflowGate fabricates and stores an approved gate', async () => {
    const svc = createMockVolundrService();
    const resolved = await svc.resolveWorkflowGate('sess-1', 'gate-1', { decision: 'APPROVE' });
    expect(resolved.id).toBe('gate-1');
    expect(resolved.status).toBe('approved');
    expect(resolved.decision).toBe('APPROVE');
    expect(resolved.notes).toBe('');
    expect(resolved.source).toBe('human');

    const gates = await svc.getWorkflowGates('sess-1');
    expect(gates).toHaveLength(1);
    expect(gates[0]?.id).toBe('gate-1');
  });

  it('resolveWorkflowGate updates an existing gate with changes requested', async () => {
    const svc = createMockVolundrService();
    await svc.resolveWorkflowGate('sess-1', 'gate-1', { decision: 'APPROVE' });
    const resolved = await svc.resolveWorkflowGate('sess-1', 'gate-1', {
      decision: 'CHANGES_REQUESTED',
      notes: 'needs tests',
      source: 'reviewer',
    });
    expect(resolved.status).toBe('changes_requested');
    expect(resolved.notes).toBe('needs tests');
    expect(resolved.source).toBe('reviewer');

    const gates = await svc.getWorkflowGates('sess-1');
    expect(gates).toHaveLength(1);
    expect(gates[0]?.status).toBe('changes_requested');
  });

  it('updateTenant falls back to default quota fields', async () => {
    const svc = createMockVolundrService();
    const tenant = await svc.updateTenant('t1', {});
    expect(tenant.tier).toBe('free');
    expect(tenant.maxSessions).toBe(5);
    expect(tenant.maxStorageGb).toBe(10);
  });

  it('updateTenant keeps explicitly provided quota fields', async () => {
    const svc = createMockVolundrService();
    const tenant = await svc.updateTenant('t1', {
      tier: 'pro',
      maxSessions: 20,
      maxStorageGb: 100,
    });
    expect(tenant.tier).toBe('pro');
    expect(tenant.maxSessions).toBe(20);
    expect(tenant.maxStorageGb).toBe(100);
  });

  it('getSessionDefinitions returns seeded session definitions', async () => {
    const svc = createMockVolundrService();
    const definitions = await svc.getSessionDefinitions();
    expect(definitions.length).toBeGreaterThan(0);
    expect(definitions[0]).toHaveProperty('key');
    expect(definitions[0]).toHaveProperty('displayName');
    expect(definitions[0]).toHaveProperty('description');
    expect(definitions[0]).toHaveProperty('labels');
    expect(definitions[0]).toHaveProperty('defaultModel');
    expect(definitions[0]).toHaveProperty('compatibleProviders');
    const keys = definitions.map((d) => d.key);
    expect(keys).toContain('skuldClaude');
    expect(keys).toContain('skuldCodex');
  });
});

// ---------------------------------------------------------------------------
// IClusterAdapter
// ---------------------------------------------------------------------------

describe('createMockClusterAdapter', () => {
  it('getClusters returns seeded clusters', async () => {
    const adapter = createMockClusterAdapter();
    const clusters = await adapter.getClusters();
    expect(clusters.length).toBeGreaterThan(0);
    expect(clusters[0]).toHaveProperty('capacity');
    expect(clusters[0]).toHaveProperty('used');
  });

  it('getCluster returns a cluster by id', async () => {
    const adapter = createMockClusterAdapter();
    const cluster = await adapter.getCluster('cl-eitri');
    expect(cluster?.name).toBe('Eitri');
  });

  it('getCluster returns null for unknown id', async () => {
    const adapter = createMockClusterAdapter();
    const cluster = await adapter.getCluster('does-not-exist');
    expect(cluster).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// ISessionStore
// ---------------------------------------------------------------------------

describe('createMockSessionStore', () => {
  it('listSessions returns seeded domain sessions', async () => {
    const store = createMockSessionStore();
    const sessions = await store.listSessions();
    expect(sessions.length).toBeGreaterThan(0);
  });

  it('getSession returns a session by id', async () => {
    const store = createMockSessionStore();
    const session = await store.getSession('ds-1');
    expect(session).not.toBeNull();
    expect(session?.state).toBe('running');
  });

  it('getSession returns null for unknown id', async () => {
    const store = createMockSessionStore();
    expect(await store.getSession('nope')).toBeNull();
  });

  it('listSessions filters by state', async () => {
    const store = createMockSessionStore();
    const running = await store.listSessions({ state: 'running' });
    expect(running.every((s) => s.state === 'running')).toBe(true);
  });

  it('createSession persists and notifies subscribers', async () => {
    const store = createMockSessionStore();
    const cb = vi.fn();
    store.subscribe(cb);
    cb.mockClear();

    await store.createSession({
      ravnId: 'r99',
      personaName: 'skald',
      templateId: 'tpl-default',
      clusterId: 'cl-eitri',
      state: 'requested',
      startedAt: new Date().toISOString(),
      resources: {
        cpuRequest: 1,
        cpuLimit: 2,
        cpuUsed: 0,
        memRequestMi: 512,
        memLimitMi: 1024,
        memUsedMi: 0,
        gpuCount: 0,
      },
      env: {},
    });

    expect(cb).toHaveBeenCalledOnce();
    const all = await store.listSessions();
    expect(all.some((s) => s.ravnId === 'r99')).toBe(true);
  });

  it('updateSession changes the state and notifies', async () => {
    const store = createMockSessionStore();
    const cb = vi.fn();
    store.subscribe(cb);
    cb.mockClear();

    const updated = await store.updateSession('ds-1', { state: 'idle' });
    expect(updated.state).toBe('idle');
    expect(cb).toHaveBeenCalledOnce();
  });

  it('updateSession throws for unknown id', async () => {
    const store = createMockSessionStore();
    await expect(store.updateSession('nope', { state: 'idle' })).rejects.toThrow(
      'Session not found',
    );
  });

  it('deleteSession removes the session and notifies', async () => {
    const store = createMockSessionStore();
    const cb = vi.fn();
    store.subscribe(cb);
    cb.mockClear();

    await store.deleteSession('ds-1');
    expect(cb).toHaveBeenCalledOnce();
    expect(await store.getSession('ds-1')).toBeNull();
  });

  it('subscribe returns an unsubscribe function that stops notifications', async () => {
    const store = createMockSessionStore();
    const cb = vi.fn();
    const unsub = store.subscribe(cb);
    cb.mockClear();
    unsub();
    await store.deleteSession('ds-1');
    expect(cb).not.toHaveBeenCalled();
  });

  it('listSessions filters by clusterId', async () => {
    const store = createMockSessionStore();
    const sessions = await store.listSessions({ clusterId: 'Eitri' });
    expect(sessions.length).toBeGreaterThan(0);
    expect(sessions.every((s) => s.clusterId === 'Eitri')).toBe(true);
  });

  it('listSessions filters by ravnId', async () => {
    const store = createMockSessionStore();
    const sessions = await store.listSessions({ ravnId: 'r-local' });
    expect(sessions.length).toBeGreaterThan(0);
    expect(sessions.every((s) => s.ravnId === 'r-local')).toBe(true);
  });

  it('listSessions combines filters and returns [] when nothing matches', async () => {
    const store = createMockSessionStore();
    const sessions = await store.listSessions({ clusterId: 'Eitri', ravnId: 'r-does-not-exist' });
    expect(sessions).toEqual([]);
  });

  it('unsubscribing twice is a no-op', async () => {
    const store = createMockSessionStore();
    const stable = vi.fn();
    const cb = vi.fn();
    store.subscribe(stable);
    const unsub = store.subscribe(cb);
    unsub();
    unsub();
    stable.mockClear();
    cb.mockClear();
    await store.deleteSession('ds-1');
    expect(stable).toHaveBeenCalledOnce();
    expect(cb).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// IPtyStream
// ---------------------------------------------------------------------------

describe('createMockPtyStream', () => {
  it('subscribe returns an unsubscribe function', () => {
    vi.useFakeTimers();
    const pty = createMockPtyStream();
    const cb = vi.fn();
    const unsub = pty.subscribe('sess-1', cb);
    expect(typeof unsub).toBe('function');
    unsub();
    vi.useRealTimers();
  });

  it('subscribe calls onData with a prompt after a short delay', () => {
    vi.useFakeTimers();
    const pty = createMockPtyStream();
    const cb = vi.fn();
    const unsub = pty.subscribe('sess-1', cb);
    expect(cb).not.toHaveBeenCalled(); // not yet
    vi.advanceTimersByTime(100);
    expect(cb).toHaveBeenCalled();
    unsub();
    vi.useRealTimers();
  });

  it('unsubscribe prevents further notifications', () => {
    vi.useFakeTimers();
    const pty = createMockPtyStream();
    const cb = vi.fn();
    const unsub = pty.subscribe('sess-1', cb);
    unsub();
    vi.advanceTimersByTime(200);
    // cb may or may not have been called once already; key is no further calls after unsub
    const callsAfterUnsub = cb.mock.calls.length;
    pty.send('sess-1', 'data');
    expect(cb.mock.calls.length).toBe(callsAfterUnsub);
    vi.useRealTimers();
  });

  it('send echoes data back to subscribers', () => {
    vi.useFakeTimers();
    const pty = createMockPtyStream();
    const cb = vi.fn();
    const unsub = pty.subscribe('sess-1', cb);
    cb.mockClear();
    pty.send('sess-1', 'a');
    expect(cb).toHaveBeenCalledWith('a');
    unsub();
    vi.useRealTimers();
  });

  it('send emits newline prompt on carriage return', () => {
    vi.useFakeTimers();
    const pty = createMockPtyStream();
    const cb = vi.fn();
    const unsub = pty.subscribe('sess-1', cb);
    cb.mockClear();
    pty.send('sess-1', '\r');
    expect(cb).toHaveBeenCalledWith('\r\nmock-output\r\n$ ');
    unsub();
    vi.useRealTimers();
  });

  it('send does not throw when no subscriber is present', () => {
    const pty = createMockPtyStream();
    expect(() => pty.send('sess-nobody', 'ls\n')).not.toThrow();
  });

  it('seeds a git transcript for the local session main terminal', () => {
    vi.useFakeTimers();
    const pty = createMockPtyStream();
    const cb = vi.fn();
    const unsub = pty.subscribe('laptop-volundr-local', cb);
    vi.advanceTimersByTime(100);
    expect(cb).toHaveBeenCalledOnce();
    expect(cb.mock.calls[0]?.[0]).toContain('git log --oneline -5');
    unsub();
    vi.useRealTimers();
  });

  it('seeds a test-run transcript for the tests terminal', () => {
    vi.useFakeTimers();
    const pty = createMockPtyStream();
    const cb = vi.fn();
    const unsub = pty.subscribe('laptop-volundr-local::tests', cb);
    vi.advanceTimersByTime(100);
    expect(cb.mock.calls[0]?.[0]).toContain('pnpm test observatory.perf.test.ts');
    unsub();
    vi.useRealTimers();
  });

  it('seeds a metrics transcript for the view-io terminal', () => {
    vi.useFakeTimers();
    const pty = createMockPtyStream();
    const cb = vi.fn();
    const unsub = pty.subscribe('laptop-volundr-local::view-io', cb);
    vi.advanceTimersByTime(100);
    expect(cb.mock.calls[0]?.[0]).toContain('jq . metrics/render.json');
    unsub();
    vi.useRealTimers();
  });

  it('seeds a bare prompt for unknown local terminals', () => {
    vi.useFakeTimers();
    const pty = createMockPtyStream();
    const cb = vi.fn();
    const unsub = pty.subscribe('laptop-volundr-local::scratch', cb);
    vi.advanceTimersByTime(100);
    expect(cb.mock.calls[0]?.[0]).toBe('scratch $ ');
    unsub();
    vi.useRealTimers();
  });
});

// ---------------------------------------------------------------------------
// IFileSystemPort
// ---------------------------------------------------------------------------

describe('createMockFileSystemPort', () => {
  it('listTree returns a non-empty tree', async () => {
    const fs = createMockFileSystemPort();
    const tree = await fs.listTree('sess-1');
    expect(tree.length).toBeGreaterThan(0);
  });

  it('listTree includes workspace directories', async () => {
    const fs = createMockFileSystemPort();
    const tree = await fs.listTree('sess-1');
    expect(tree.some((n) => n.kind === 'directory')).toBe(true);
  });

  it('listTree includes a secret mount node', async () => {
    const fs = createMockFileSystemPort();
    const tree = await fs.listTree('sess-1');
    expect(tree.some((n) => n.isSecret === true)).toBe(true);
  });

  it('expandDirectory returns children for a known path', async () => {
    const fs = createMockFileSystemPort();
    const children = await fs.expandDirectory('sess-1', '/workspace/src');
    expect(children.length).toBeGreaterThan(0);
  });

  it('expandDirectory returns [] for unknown path', async () => {
    const fs = createMockFileSystemPort();
    const children = await fs.expandDirectory('sess-1', '/does-not-exist');
    expect(children).toEqual([]);
  });

  it('readFile returns content for a known file', async () => {
    const fs = createMockFileSystemPort();
    const content = await fs.readFile('sess-1', '/workspace/package.json');
    expect(content).toContain('"name"');
  });

  it('readFile throws for an unknown file', async () => {
    const fs = createMockFileSystemPort();
    await expect(fs.readFile('sess-1', '/not/a/real/file')).rejects.toThrow('File not found');
  });

  it('writeFile adds a new file to the tree', async () => {
    const fs = createMockFileSystemPort();
    await fs.writeFile('sess-1', '/workspace/new.txt', 'hello');
    const tree = await fs.listTree('sess-1');
    expect(tree.some((node) => node.path === '/workspace/new.txt')).toBe(true);
    await expect(fs.readFile('sess-1', '/workspace/new.txt')).resolves.toBe('hello');
  });

  it('deletePaths removes files from the tree', async () => {
    const fs = createMockFileSystemPort();
    await fs.writeFile('sess-1', '/workspace/temp.txt', 'bye');
    await fs.deletePaths('sess-1', ['/workspace/temp.txt']);
    await expect(fs.readFile('sess-1', '/workspace/temp.txt')).rejects.toThrow('File not found');
  });

  it('writeFile updates the size of an existing file in place', async () => {
    const fs = createMockFileSystemPort();
    await fs.writeFile('sess-1', '/workspace/README.md', 'short');
    await expect(fs.readFile('sess-1', '/workspace/README.md')).resolves.toBe('short');
    const tree = await fs.listTree('sess-1');
    const readme = tree.find((node) => node.path === '/workspace/README.md');
    expect(readme?.size).toBe(5);
  });

  it('writeFile inserts a new file under its parent directory sorted by name', async () => {
    const fs = createMockFileSystemPort();
    await fs.writeFile('sess-1', '/workspace/src/aaa.ts', 'export {};');
    const children = await fs.expandDirectory('sess-1', '/workspace/src');
    expect(children.map((child) => child.name)).toEqual(['aaa.ts', 'app.tsx', 'index.ts']);
    await expect(fs.readFile('sess-1', '/workspace/src/aaa.ts')).resolves.toBe('export {};');
  });

  it('deletePaths removes a nested file from its parent directory', async () => {
    const fs = createMockFileSystemPort();
    await fs.deletePaths('sess-1', ['/workspace/src/index.ts']);
    const children = await fs.expandDirectory('sess-1', '/workspace/src');
    expect(children.some((child) => child.path === '/workspace/src/index.ts')).toBe(false);
    expect(children.some((child) => child.path === '/workspace/src/app.tsx')).toBe(true);
  });

  it('deletePaths removes a top-level node from the tree root', async () => {
    const fs = createMockFileSystemPort();
    await fs.deletePaths('sess-1', ['/workspace/package.json']);
    const tree = await fs.listTree('sess-1');
    expect(tree.some((node) => node.path === '/workspace/package.json')).toBe(false);
  });

  it('deletePaths removes a file inside the secret mount', async () => {
    const fs = createMockFileSystemPort();
    await fs.deletePaths('sess-1', ['/mnt/secrets/API_KEY']);
    const children = await fs.expandDirectory('sess-1', '/mnt/secrets');
    expect(children).toEqual([]);
  });

  it('deletePaths ignores unknown paths', async () => {
    const fs = createMockFileSystemPort();
    const before = await fs.listTree('sess-1');
    await fs.deletePaths('sess-1', ['/does/not/exist']);
    const after = await fs.listTree('sess-1');
    expect(after.length).toBe(before.length);
  });
});

// ---------------------------------------------------------------------------
// IMetricsStream
// ---------------------------------------------------------------------------

describe('createMockMetricsStream', () => {
  it('subscribe calls onMetrics immediately with an initial point', () => {
    const metrics = createMockMetricsStream();
    const cb = vi.fn();
    const unsub = metrics.subscribe('sess-1', cb);
    expect(cb).toHaveBeenCalledOnce();
    const point = cb.mock.calls[0]?.[0];
    expect(typeof point?.cpu).toBe('number');
    expect(typeof point?.memMi).toBe('number');
    unsub();
  });

  it('subscribe interval fires and unsub clears it', () => {
    vi.useFakeTimers();
    const metrics = createMockMetricsStream();
    const cb = vi.fn();
    const unsub = metrics.subscribe('sess-1', cb);
    cb.mockClear();
    vi.advanceTimersByTime(2_001);
    expect(cb).toHaveBeenCalled();
    unsub();
    vi.useRealTimers();
  });
});

// ---------------------------------------------------------------------------
// Complete IVolundrService method sweep — ensures every arrow function is called
// ---------------------------------------------------------------------------

describe('createMockVolundrService — full method sweep', () => {
  it('covers every IVolundrService method', async () => {
    const svc = createMockVolundrService();
    const templateArg = {
      name: 'sweep',
      description: '',
      isDefault: false,
      repos: [],
      setupScripts: [],
      workspaceLayout: {},
      cliTool: 'claude',
      workloadType: 'default',
      model: null,
      systemPrompt: null,
      resourceConfig: {},
      mcpServers: [],
      envVars: {},
      envSecretRefs: [],
      workloadConfig: {},
      terminalSidecar: { enabled: false, allowedCommands: [] },
      skills: [],
      rules: [],
    } as const;
    const presetBase = {
      name: 'sweep',
      description: '',
      isDefault: false,
      cliTool: 'claude',
      workloadType: 'default',
      model: null,
      systemPrompt: null,
      resourceConfig: {},
      mcpServers: [],
      terminalSidecar: { enabled: false, allowedCommands: [] },
      skills: [],
      rules: [],
      envVars: {},
      envSecretRefs: [],
      source: null,
      integrationIds: [],
      setupScripts: [],
      workloadConfig: {},
    } as const;

    await svc.getRepos();
    await svc.getLaunchSpecs();
    await svc.getLaunchSpec('t1');
    await svc.saveLaunchSpec(templateArg);
    await svc.getLaunchSpecs('user');
    await svc.getLaunchSpec('p1');
    // saveLaunchSpec with id (different code path from existing test)
    await svc.saveLaunchSpec({ ...presetBase, id: 'existing-id' });
    await svc.deleteLaunchSpec('p1');
    await svc.getAvailableMcpServers();
    await svc.getAvailableSecrets();
    await svc.createSecret('sweep-secret', { key: 'val' });
    await svc.getClusterResources();
    await svc.evaluatePermissionAutoApproval('sess-1', {
      requestId: 'perm-1',
      toolName: 'Bash',
      description: './start-dev',
      command: './start-dev',
      input: { command: './start-dev' },
    });
    await svc.updateSession('sess-1', { name: 'renamed' });
    await svc.stopSession('sess-1');
    await svc.resumeSession('sess-1');
    await svc.deleteSession('sess-1');
    await svc.archiveSession('sess-1');
    await svc.archiveStoppedSessions();
    await svc.restoreSession('sess-1');
    await svc.listArchivedSessions();
    await svc.getMessages('sess-1');
    const unsub3 = svc.subscribeMessages('sess-1', () => {});
    unsub3();
    await svc.getLogs('sess-1');
    await svc.getLogs('sess-1', 20);
    const unsub4 = svc.subscribeLogs('sess-1', () => {});
    unsub4();
    await svc.getAggregatedLogs('sess-1', { limit: 20, level: 'INFO', participants: ['coder'] });
    const unsub4b = svc.subscribeAggregatedLogs(
      'sess-1',
      { limit: 20, level: 'INFO', participants: ['coder'] },
      () => {},
    );
    unsub4b();
    await svc.getCodeServerUrl('sess-1');
    await svc.getChronicle('sess-1');
    const unsub5 = svc.subscribeChronicle('sess-1', () => {});
    unsub5();
    await svc.getPullRequests('github.com/niuulabs/repo');
    await svc.mergePullRequest(1, 'github.com/niuulabs/repo');
    await svc.getCIStatus(1, 'github.com/niuulabs/repo', 'main');
    await svc.getSessionMcpServers('sess-1');
    await svc.searchTrackerIssues('bug', 'proj-1');
    await svc.getProjectRepoMappings();
    await svc.updateTrackerIssueStatus('issue-1', 'done');
    await svc.listUsers();
    await svc.getTenants();
    await svc.getTenant('t1');
    await svc.deleteTenant('t1');
    await svc.updateTenant('t1', { tier: 'pro' });
    await svc.getTenantMembers('t1');
    await svc.reprovisionTenant('t1');
    await svc.getUserCredentials();
    await svc.storeUserCredential('cred', { key: 'val' });
    await svc.deleteUserCredential('cred');
    await svc.getTenantCredentials();
    await svc.storeTenantCredential('cred', { key: 'val' });
    await svc.deleteTenantCredential('cred');
    await svc.getIntegrationCatalog();
    await svc.getIntegrations();
    await svc.createIntegration({});
    await svc.deleteIntegration('int-1');
    await svc.testIntegration('int-1');
    await svc.getCredentials();
    await svc.getCredentials('api_key');
    await svc.getCredential('cred-1');
    await svc.deleteCredential('cred-1');
    await svc.getCredentialTypes();
    await svc.listWorkspaces();
    await svc.listWorkspaces('active');
    await svc.listAllWorkspaces();
    await svc.listAllWorkspaces('archived');
    await svc.restoreWorkspace('ws-1');
    await svc.deleteWorkspace('ws-1');
    await svc.getAdminSettings();
    await svc.updateAdminSettings({ storage: { homeEnabled: true, fileManagerEnabled: false } });
    await svc.getFeatureModules('admin');
    await svc.getUserFeaturePreferences();
    await svc.updateUserFeaturePreferences([{ featureKey: 'f1', visible: true, sortOrder: 0 }]);
    await svc.listTokens();
    await svc.revokeToken('tok-1');
  });
});
