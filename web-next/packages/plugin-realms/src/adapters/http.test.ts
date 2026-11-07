import { afterEach, describe, expect, it, vi } from 'vitest';
import { buildRealmGovernanceHttpAdapter } from './http';

function makeClient() {
  return {
    basePath: '/api/v1',
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  };
}

describe('buildRealmGovernanceHttpAdapter', () => {
  it('lists realms and trust grants from the shared realms API', async () => {
    const client = makeClient();
    client.get.mockResolvedValue([]);
    const adapter = buildRealmGovernanceHttpAdapter(client);

    await adapter.listRealms();
    expect(client.get).toHaveBeenCalledWith('/realms');

    await adapter.listTrustGrants('asgard');
    expect(client.get).toHaveBeenCalledWith('/realms/asgard/trust-grants');
  });

  it('reads one realm by its encoded slug', async () => {
    const client = makeClient();
    client.get.mockResolvedValue({ slug: 'realm/with slash' });
    const adapter = buildRealmGovernanceHttpAdapter(client);

    await adapter.getRealm('realm/with slash');
    expect(client.get).toHaveBeenCalledWith('/realms/realm%2Fwith%20slash');
  });

  it('creates a realm with the body untouched', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({ slug: 'asgard' });
    const adapter = buildRealmGovernanceHttpAdapter(client);

    await adapter.createRealm({ slug: 'asgard', name: 'Asgard', project_id: 'p-1' });
    expect(client.post).toHaveBeenCalledWith('/realms', {
      slug: 'asgard',
      name: 'Asgard',
      project_id: 'p-1',
    });
  });

  it('moves a realm into a project by its encoded slug', async () => {
    const client = makeClient();
    client.patch.mockResolvedValue({ slug: 'realm/with slash' });
    const adapter = buildRealmGovernanceHttpAdapter(client);

    await adapter.setRealmProject('realm/with slash', 'project-1');
    expect(client.patch).toHaveBeenCalledWith('/realms/realm%2Fwith%20slash/project', {
      project_id: 'project-1',
    });
  });

  it('deletes a realm by its encoded slug', async () => {
    const client = makeClient();
    client.delete.mockResolvedValue(undefined);
    const adapter = buildRealmGovernanceHttpAdapter(client);

    await adapter.deleteRealm('realm/with slash');
    expect(client.delete).toHaveBeenCalledWith('/realms/realm%2Fwith%20slash');
  });

  it('posts the trust grant body untouched', async () => {
    const client = makeClient();
    client.post.mockResolvedValue({ id: 'grant-1' });
    const adapter = buildRealmGovernanceHttpAdapter(client);

    await adapter.createTrustGrant('realm/with slash', {
      action_class: 'build',
      target: '*',
      level: 3,
      limits: {},
      granted_by: 'human:operator',
    });

    expect(client.post).toHaveBeenCalledWith('/realms/realm%2Fwith%20slash/trust-grants', {
      action_class: 'build',
      target: '*',
      level: 3,
      limits: {},
      granted_by: 'human:operator',
    });
  });

  it('refuses to open the detail stream without a stream base', () => {
    const adapter = buildRealmGovernanceHttpAdapter(makeClient());
    expect(() => adapter.subscribeRealmDetail('asgard', () => {})).toThrow(
      'Realm detail stream URL is not configured',
    );
  });
});

describe('realm-detail SSE subscription', () => {
  const originalFetch = global.fetch;

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('connects, forwards realm.detail events, and disconnects on unsubscribe', async () => {
    let signal: AbortSignal | undefined;
    global.fetch = vi.fn((_input, init) => {
      signal = (init as RequestInit).signal ?? undefined;
      return Promise.resolve(
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(
                new TextEncoder().encode(
                  [
                    'event: realm.detail',
                    'data: {"realm":{"slug":"asgard"},"trust_grants":[],"capabilities":[]}',
                    '',
                    '',
                  ].join('\n'),
                ),
              );
            },
          }),
          { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
        ),
      );
    }) as typeof fetch;
    const adapter = buildRealmGovernanceHttpAdapter(makeClient(), '/api/v1');
    const received: unknown[] = [];
    const unsubscribe = adapter.subscribeRealmDetail('asgard', (detail) => received.push(detail));

    await vi.waitFor(() =>
      expect(received).toEqual([{ realm: { slug: 'asgard' }, trust_grants: [], capabilities: [] }]),
    );
    unsubscribe();

    expect(global.fetch).toHaveBeenCalledWith(
      '/api/v1/realms/asgard/stream',
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(signal?.aborted).toBe(true);
  });
});
