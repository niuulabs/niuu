import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ApiRealmService } from './yggdrasil.adapter';
import type {
  ApiRealmSummary,
  ApiRealmDetail,
  ApiNodeSnapshot,
  ApiWorkloadSummary,
  ApiStorageSummary,
  ApiInfraEvent,
} from './yggdrasil.types';

const mockFetch = vi.fn();
global.fetch = mockFetch;

import { mockResponse } from '@/test/mockFetch';

const mockHealth = {
  status: 'healthy',
  inputs: {
    nodes_ready: 3,
    nodes_total: 3,
    pod_running_ratio: 0.95,
    volumes_degraded: 0,
    volumes_faulted: 0,
    recent_error_count: 0,
  },
  reason: 'All systems operational',
};

const mockResources = {
  cpu: { capacity: 16, allocatable: 14, unit: 'cores' },
  memory: { capacity: 64000, allocatable: 60000, unit: 'Mi' },
  gpu_count: 2,
  pod_counts: { running: 20, pending: 0, failed: 1, succeeded: 5, unknown: 0 },
};

const mockRealmSummary: ApiRealmSummary = {
  realm_id: 'realm-1',
  display_name: 'Production',
  description: 'Production cluster',
  location: 'us-east-1',
  status: 'healthy',
  health: mockHealth,
  resources: mockResources,
};

const mockRealmDetail: ApiRealmDetail = {
  ...mockRealmSummary,
  nodes: [
    {
      name: 'node-1',
      status: 'Ready',
      roles: ['master', 'worker'],
      cpu: { capacity: 8, allocatable: 7, unit: 'cores' },
      memory: { capacity: 32000, allocatable: 30000, unit: 'Mi' },
      gpu_count: 1,
      conditions: [{ condition_type: 'Ready', status: 'True', message: 'kubelet is healthy' }],
    },
  ],
  workloads: {
    namespace_count: 5,
    deployment_total: 10,
    deployment_healthy: 9,
    statefulset_count: 3,
    daemonset_count: 2,
    pods: { running: 20, pending: 0, failed: 1, succeeded: 5, unknown: 0 },
  },
  storage: {
    total_capacity_bytes: 1000000000,
    used_bytes: 500000000,
    volumes: { healthy: 10, degraded: 1, faulted: 0 },
  },
  events: [
    {
      timestamp: '2024-01-15T10:00:00Z',
      severity: 'info',
      source: 'kubelet',
      message: 'Node started',
      involved_object: 'node/node-1',
    },
  ],
};

describe('ApiRealmService', () => {
  let service: ApiRealmService;

  beforeEach(() => {
    service = new ApiRealmService();
    mockFetch.mockReset();
  });

  describe('getRealms', () => {
    it('returns transformed realm summaries', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([mockRealmSummary]));

      const realms = await service.getRealms();

      expect(realms).toHaveLength(1);
      expect(realms[0]).toMatchObject({
        id: 'realm-1',
        name: 'Production',
        description: 'Production cluster',
        location: 'us-east-1',
        status: 'healthy',
      });
    });

    it('transforms health data', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([mockRealmSummary]));

      const realms = await service.getRealms();

      expect(realms[0].health).toMatchObject({
        status: 'healthy',
        reason: 'All systems operational',
        inputs: {
          nodesReady: 3,
          nodesTotal: 3,
          podRunningRatio: 0.95,
          volumesDegraded: 0,
          volumesFaulted: 0,
          recentErrorCount: 0,
        },
      });
    });

    it('transforms resource data', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([mockRealmSummary]));

      const realms = await service.getRealms();

      expect(realms[0].resources).toMatchObject({
        cpu: { capacity: 16, allocatable: 14, unit: 'cores' },
        memory: { capacity: 64000, allocatable: 60000, unit: 'Mi' },
        gpuCount: 2,
        pods: { running: 20, pending: 0, failed: 1, succeeded: 5, unknown: 0 },
      });
    });

    it('handles empty realm list', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([]));

      const realms = await service.getRealms();

      expect(realms).toEqual([]);
    });
  });

  describe('getRealm', () => {
    it('returns matching realm by id', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([mockRealmSummary]));

      const realm = await service.getRealm('realm-1');

      expect(realm).not.toBeNull();
      expect(realm?.id).toBe('realm-1');
    });

    it('returns null when no realm matches', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([mockRealmSummary]));

      const realm = await service.getRealm('nonexistent');

      expect(realm).toBeNull();
    });

    it('returns null on 404 error', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Not found' }, 404));

      const realm = await service.getRealm('bad-id');

      expect(realm).toBeNull();
    });

    it('throws on non-404 errors', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Server error' }, 500));

      await expect(service.getRealm('bad')).rejects.toThrow();
    });
  });

  describe('getRealmDetail', () => {
    it('returns transformed realm detail', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(mockRealmDetail));

      const detail = await service.getRealmDetail('realm-1');

      expect(detail).not.toBeNull();
      expect(detail?.id).toBe('realm-1');
      expect(detail?.nodes).toHaveLength(1);
      expect(detail?.events).toHaveLength(1);
    });

    it('transforms nodes correctly', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(mockRealmDetail));

      const detail = await service.getRealmDetail('realm-1');

      expect(detail?.nodes[0]).toMatchObject({
        name: 'node-1',
        status: 'Ready',
        roles: ['master', 'worker'],
        gpuCount: 1,
      });
      expect(detail?.nodes[0].conditions).toEqual([
        { conditionType: 'Ready', status: 'True', message: 'kubelet is healthy' },
      ]);
    });

    it('transforms workloads correctly', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(mockRealmDetail));

      const detail = await service.getRealmDetail('realm-1');

      expect(detail?.workloads).toMatchObject({
        namespaceCount: 5,
        deploymentTotal: 10,
        deploymentHealthy: 9,
        statefulsetCount: 3,
        daemonsetCount: 2,
      });
    });

    it('transforms storage correctly', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(mockRealmDetail));

      const detail = await service.getRealmDetail('realm-1');

      expect(detail?.storage).toMatchObject({
        totalCapacityBytes: 1000000000,
        usedBytes: 500000000,
        volumes: { healthy: 10, degraded: 1, faulted: 0 },
      });
    });

    it('transforms events correctly', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(mockRealmDetail));

      const detail = await service.getRealmDetail('realm-1');

      expect(detail?.events[0]).toMatchObject({
        timestamp: '2024-01-15T10:00:00Z',
        severity: 'info',
        source: 'kubelet',
        message: 'Node started',
        involvedObject: 'node/node-1',
      });
    });

    it('returns null on 404', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Not found' }, 404));

      const detail = await service.getRealmDetail('nonexistent');

      expect(detail).toBeNull();
    });

    it('throws on non-404 errors', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Server error' }, 500));

      await expect(service.getRealmDetail('bad')).rejects.toThrow();
    });

    it('handles null nodes and events', async () => {
      const detailNoNodes = {
        ...mockRealmDetail,
        nodes: null,
        events: null,
      };
      mockFetch.mockReturnValueOnce(mockResponse(detailNoNodes));

      const detail = await service.getRealmDetail('realm-1');

      expect(detail?.nodes).toEqual([]);
      expect(detail?.events).toEqual([]);
    });

    it('handles node with null roles and conditions', async () => {
      const detailNullFields = {
        ...mockRealmDetail,
        nodes: [
          {
            name: 'node-2',
            status: 'Ready',
            roles: null,
            cpu: { capacity: 4, allocatable: 3, unit: 'cores' },
            memory: { capacity: 16000, allocatable: 14000, unit: 'Mi' },
            gpu_count: 0,
            conditions: null,
          },
        ],
      };
      mockFetch.mockReturnValueOnce(mockResponse(detailNullFields));

      const detail = await service.getRealmDetail('realm-1');

      expect(detail?.nodes[0].roles).toEqual([]);
      expect(detail?.nodes[0].conditions).toEqual([]);
    });
  });

  describe('getRealmNodes', () => {
    it('returns transformed nodes', async () => {
      const nodes: ApiNodeSnapshot[] = [
        {
          name: 'node-1',
          status: 'Ready',
          roles: ['worker'],
          cpu: { capacity: 8, allocatable: 7, unit: 'cores' },
          memory: { capacity: 32000, allocatable: 30000, unit: 'Mi' },
          gpu_count: 0,
          conditions: [],
        },
      ];
      mockFetch.mockReturnValueOnce(mockResponse(nodes));

      const result = await service.getRealmNodes('realm-1');

      expect(result).toHaveLength(1);
      expect(result[0].name).toBe('node-1');
    });
  });

  describe('getRealmWorkloads', () => {
    it('returns transformed workloads', async () => {
      const workloads: ApiWorkloadSummary = {
        namespace_count: 3,
        deployment_total: 5,
        deployment_healthy: 4,
        statefulset_count: 1,
        daemonset_count: 1,
        pods: { running: 10, pending: 1, failed: 0, succeeded: 2, unknown: 0 },
      };
      mockFetch.mockReturnValueOnce(mockResponse(workloads));

      const result = await service.getRealmWorkloads('realm-1');

      expect(result).toMatchObject({
        namespaceCount: 3,
        deploymentTotal: 5,
        deploymentHealthy: 4,
      });
    });

    it('returns null on 404', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Not found' }, 404));

      const result = await service.getRealmWorkloads('bad');

      expect(result).toBeNull();
    });

    it('throws on non-404 errors', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Server error' }, 500));

      await expect(service.getRealmWorkloads('bad')).rejects.toThrow();
    });
  });

  describe('getRealmStorage', () => {
    it('returns transformed storage', async () => {
      const storage: ApiStorageSummary = {
        total_capacity_bytes: 2000000000,
        used_bytes: 1000000000,
        volumes: { healthy: 5, degraded: 0, faulted: 0 },
      };
      mockFetch.mockReturnValueOnce(mockResponse(storage));

      const result = await service.getRealmStorage('realm-1');

      expect(result).toMatchObject({
        totalCapacityBytes: 2000000000,
        usedBytes: 1000000000,
        volumes: { healthy: 5, degraded: 0, faulted: 0 },
      });
    });

    it('returns null on 404', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Not found' }, 404));

      const result = await service.getRealmStorage('bad');

      expect(result).toBeNull();
    });

    it('throws on non-404 errors', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Server error' }, 500));

      await expect(service.getRealmStorage('bad')).rejects.toThrow();
    });
  });

  describe('getRealmEvents', () => {
    it('returns transformed events', async () => {
      const events: ApiInfraEvent[] = [
        {
          timestamp: '2024-01-15T10:00:00Z',
          severity: 'warning',
          source: 'scheduler',
          message: 'Pod scheduling delayed',
          involved_object: 'pod/app-1',
        },
      ];
      mockFetch.mockReturnValueOnce(mockResponse(events));

      const result = await service.getRealmEvents('realm-1');

      expect(result).toHaveLength(1);
      expect(result[0]).toMatchObject({
        severity: 'warning',
        source: 'scheduler',
        involvedObject: 'pod/app-1',
      });
    });

    it('passes since and severity params', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([]));

      await service.getRealmEvents('realm-1', '2024-01-01', 'error');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('since=2024-01-01'),
        expect.any(Object)
      );
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('severity=error'),
        expect.any(Object)
      );
    });

    it('omits query string when no params', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([]));

      await service.getRealmEvents('realm-1');

      const calledUrl = mockFetch.mock.calls[0][0];
      expect(calledUrl).not.toContain('?');
    });

    it('handles only since param', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([]));

      await service.getRealmEvents('realm-1', '2024-06-01');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('since=2024-06-01'),
        expect.any(Object)
      );
    });

    it('handles only severity param', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([]));

      await service.getRealmEvents('realm-1', undefined, 'warning');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('severity=warning'),
        expect.any(Object)
      );
    });
  });

  describe('subscribe', () => {
    it('returns unsubscribe function', () => {
      const callback = vi.fn();
      const unsubscribe = service.subscribe(callback);

      expect(typeof unsubscribe).toBe('function');
      unsubscribe();
    });

    it('immediately notifies with cached data after getRealms', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([mockRealmSummary]));
      await service.getRealms();

      const callback = vi.fn();
      service.subscribe(callback);

      expect(callback).toHaveBeenCalledWith(
        expect.arrayContaining([expect.objectContaining({ id: 'realm-1' })])
      );
    });

    it('does not notify when no cached data', () => {
      const callback = vi.fn();
      service.subscribe(callback);

      expect(callback).not.toHaveBeenCalled();
    });
  });
});
