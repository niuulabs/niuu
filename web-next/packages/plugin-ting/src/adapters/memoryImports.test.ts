import { describe, expect, it, vi } from 'vitest';
import type { ApiClient } from '@niuulabs/query';
import { buildMemoryImportsHttpAdapter, createMockMemoryImportsService } from './memoryImports';

const RAW = {
  id: 'i-1',
  project_id: 'p-1',
  connector: 'linear',
  connection_id: 'c-1',
  tracker_project_id: 'lin-1',
  compile: true,
  last_run_at: null,
  last_error: '',
};

describe('buildMemoryImportsHttpAdapter', () => {
  it('lists, creates and removes a project owner imports', async () => {
    const client = {
      get: vi.fn().mockResolvedValue([RAW]),
      post: vi.fn().mockResolvedValue(RAW),
      delete: vi.fn().mockResolvedValue(undefined),
    } as unknown as ApiClient;
    const service = buildMemoryImportsHttpAdapter(client);
    const [found] = await service.list('p-1');
    expect(found).toMatchObject({ connectionId: 'c-1', trackerProjectId: 'lin-1' });
    expect(client.get).toHaveBeenCalledWith('/memory/imports?project_id=p-1');
    await service.create({
      projectId: 'p-1',
      connectionId: 'c-1',
      trackerProjectId: 'lin-1',
      compile: true,
    });
    expect(client.post).toHaveBeenCalledWith('/memory/imports', {
      project_id: 'p-1',
      connector: 'linear',
      connection_id: 'c-1',
      tracker_project_id: 'lin-1',
      compile: true,
    });
    await service.remove('i-1');
    expect(client.delete).toHaveBeenCalledWith('/memory/imports/i-1');
  });
});

describe('createMockMemoryImportsService', () => {
  it('keeps imports per project', async () => {
    const service = createMockMemoryImportsService();
    await expect(
      service.create({ projectId: 'p', connectionId: 'c', trackerProjectId: ' ', compile: true }),
    ).rejects.toThrow('tracker_project_id');
    const created = await service.create({
      projectId: 'p',
      connectionId: 'c',
      trackerProjectId: 'lin',
      compile: true,
    });
    expect(await service.list('other')).toEqual([]);
    expect(await service.list('p')).toHaveLength(1);
    await service.remove(created.id);
    expect(await service.list('p')).toEqual([]);
  });
});
