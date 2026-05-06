import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ApiTyrService } from './tyr';
import { mockResponse } from '@/test/mockFetch';

// Mock fetch globally
const mockFetch = vi.fn();
global.fetch = mockFetch;

describe('ApiTyrService', () => {
  let service: ApiTyrService;

  beforeEach(() => {
    service = new ApiTyrService();
    mockFetch.mockReset();
  });

  describe('getSagas', () => {
    it('returns transformed sagas from API', async () => {
      const apiResponse = [
        {
          id: 'saga-1',
          tracker_id: 'tracker-1',
          tracker_type: 'linear',
          slug: 'my-saga',
          name: 'My Saga',
          repos: ['https://github.com/org/repo'],
          feature_branch: 'feat/my-saga',
          status: 'Active',
          milestone_count: 3,
          issue_count: 10,
          url: 'https://linear.app/org/project/my-saga',
        },
      ];
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const sagas = await service.getSagas();

      expect(sagas).toHaveLength(1);
      expect(sagas[0]).toMatchObject({
        id: 'saga-1',
        tracker_id: 'tracker-1',
        tracker_type: 'linear',
        slug: 'my-saga',
        name: 'My Saga',
        repos: ['https://github.com/org/repo'],
        feature_branch: 'feat/my-saga',
        status: 'active',
        confidence: 0,
        created_at: '',
      });
    });

    it('lowercases status from API', async () => {
      const apiResponse = [
        {
          id: 'saga-1',
          tracker_id: 't-1',
          tracker_type: 'linear',
          slug: 'test',
          name: 'Test',
          repos: [],
          feature_branch: 'main',
          status: 'PLANNING',
          milestone_count: 0,
          issue_count: 0,
          url: '',
        },
      ];
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const sagas = await service.getSagas();

      expect(sagas[0].status).toBe('planning');
    });

    it('maps milestone_count to phase_summary.total', async () => {
      const apiResponse = [
        {
          id: 'saga-1',
          tracker_id: 't-1',
          tracker_type: 'linear',
          slug: 'test',
          name: 'Test',
          repos: [],
          feature_branch: 'main',
          status: 'active',
          milestone_count: 5,
          issue_count: 20,
          url: '',
        },
      ];
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const sagas = await service.getSagas();

      expect(sagas[0].phase_summary).toEqual({ total: 5, completed: 0 });
    });

    it('handles empty saga list', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([]));

      const sagas = await service.getSagas();

      expect(sagas).toEqual([]);
    });

    it('maps multiple sagas correctly', async () => {
      const apiResponse = [
        {
          id: 'saga-1',
          tracker_id: 't-1',
          tracker_type: 'linear',
          slug: 'first',
          name: 'First',
          repos: ['repo1'],
          feature_branch: 'feat/first',
          status: 'active',
          milestone_count: 2,
          issue_count: 5,
          url: '',
        },
        {
          id: 'saga-2',
          tracker_id: 't-2',
          tracker_type: 'github',
          slug: 'second',
          name: 'Second',
          repos: ['repo2', 'repo3'],
          feature_branch: 'feat/second',
          status: 'Completed',
          milestone_count: 4,
          issue_count: 12,
          url: '',
        },
      ];
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const sagas = await service.getSagas();

      expect(sagas).toHaveLength(2);
      expect(sagas[0].id).toBe('saga-1');
      expect(sagas[1].id).toBe('saga-2');
      expect(sagas[1].status).toBe('completed');
      expect(sagas[1].repos).toEqual(['repo2', 'repo3']);
    });
  });

  describe('getSaga', () => {
    const mockSagaDetail = {
      id: 'saga-1',
      tracker_id: 'tracker-1',
      tracker_type: 'linear',
      slug: 'my-saga',
      name: 'My Saga',
      description: 'A test saga',
      repos: ['https://github.com/org/repo'],
      feature_branch: 'feat/my-saga',
      status: 'Active',
      url: 'https://linear.app/org/project/my-saga',
      phases: [
        {
          id: 'phase-1',
          name: 'Phase 1',
          description: 'First phase',
          sort_order: 1,
          progress: 0.5,
          raids: [],
        },
        {
          id: 'phase-2',
          name: 'Phase 2',
          description: 'Second phase',
          sort_order: 2,
          progress: 0,
          raids: [],
        },
      ],
    };

    it('returns transformed saga by ID', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(mockSagaDetail));

      const saga = await service.getSaga('saga-1');

      expect(saga).not.toBeNull();
      expect(saga).toMatchObject({
        id: 'saga-1',
        tracker_id: 'tracker-1',
        tracker_type: 'linear',
        slug: 'my-saga',
        name: 'My Saga',
        repos: ['https://github.com/org/repo'],
        feature_branch: 'feat/my-saga',
        status: 'active',
        confidence: 0,
        created_at: '',
      });
    });

    it('maps phases.length to phase_summary.total', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(mockSagaDetail));

      const saga = await service.getSaga('saga-1');

      expect(saga?.phase_summary).toEqual({ total: 2, completed: 0 });
    });

    it('returns null when API throws an error', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Not found' }, 404));

      const saga = await service.getSaga('nonexistent');

      expect(saga).toBeNull();
    });

    it('returns null when fetch throws a network error', async () => {
      mockFetch.mockRejectedValueOnce(new Error('Network error'));

      const saga = await service.getSaga('nonexistent');

      expect(saga).toBeNull();
    });

    it('returns null for 500 error', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Internal error' }, 500));

      const saga = await service.getSaga('server-error');

      expect(saga).toBeNull();
    });

    it('lowercases status from API', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ ...mockSagaDetail, status: 'PLANNING' }));

      const saga = await service.getSaga('saga-1');

      expect(saga?.status).toBe('planning');
    });
  });

  describe('getPhases', () => {
    const mockSagaWithPhases = {
      id: 'saga-1',
      tracker_id: 'tracker-1',
      tracker_type: 'linear',
      slug: 'my-saga',
      name: 'My Saga',
      description: 'A test saga',
      repos: [],
      feature_branch: 'feat/my-saga',
      status: 'active',
      url: '',
      phases: [
        {
          id: 'phase-1',
          name: 'Setup',
          description: 'Setup phase',
          sort_order: 1,
          progress: 0,
          raids: [
            {
              id: 'raid-1',
              identifier: 'NIU-101',
              title: 'Create database schema',
              status: 'pending',
              assignee: null,
              priority: 1,
              url: 'https://linear.app/issue/NIU-101',
              milestone_id: 'phase-1',
            },
            {
              id: 'raid-2',
              identifier: 'NIU-102',
              title: 'Add API endpoints',
              status: 'in_progress',
              assignee: 'user-1',
              priority: 2,
              url: 'https://linear.app/issue/NIU-102',
              milestone_id: 'phase-1',
            },
          ],
        },
        {
          id: 'phase-2',
          name: 'Implementation',
          description: 'Impl phase',
          sort_order: 2,
          progress: 0,
          raids: [],
        },
      ],
    };

    it('returns transformed phases with raids', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(mockSagaWithPhases));

      const phases = await service.getPhases('saga-1');

      expect(phases).toHaveLength(2);
      expect(phases[0]).toMatchObject({
        id: 'phase-1',
        saga_id: 'saga-1',
        tracker_id: 'phase-1',
        number: 1,
        name: 'Setup',
        status: 'pending',
        confidence: 0,
      });
    });

    it('maps raids correctly', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(mockSagaWithPhases));

      const phases = await service.getPhases('saga-1');

      expect(phases[0].raids).toHaveLength(2);
      expect(phases[0].raids[0]).toMatchObject({
        id: 'raid-1',
        phase_id: 'phase-1',
        tracker_id: 'raid-1',
        name: 'Create database schema',
        description: '',
        acceptance_criteria: [],
        declared_files: [],
        estimate_hours: null,
        status: 'pending',
        confidence: 0,
        session_id: null,
        reviewer_session_id: null,
        review_round: 0,
        branch: null,
        chronicle_summary: null,
        retry_count: 0,
        created_at: '',
        updated_at: '',
      });
    });

    it('handles phase with no raids', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(mockSagaWithPhases));

      const phases = await service.getPhases('saga-1');

      expect(phases[1].raids).toEqual([]);
    });

    it('sets saga_id on all phases', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(mockSagaWithPhases));

      const phases = await service.getPhases('my-saga-id');

      for (const phase of phases) {
        expect(phase.saga_id).toBe('my-saga-id');
      }
    });
  });

  describe('createSaga', () => {
    it('throws error directing to import flow', async () => {
      await expect(service.createSaga('spec', 'repo')).rejects.toThrow(
        'Use the import flow instead'
      );
    });
  });

  describe('decompose', () => {
    it('throws not yet implemented error', async () => {
      await expect(service.decompose('spec', 'repo')).rejects.toThrow('Not yet implemented');
    });
  });

  describe('commitSaga', () => {
    it('commits saga and returns transformed result', async () => {
      const commitResponse = {
        id: 'saga-committed-1',
        tracker_id: 'tracker-99',
        tracker_type: 'linear',
        slug: 'committed-saga',
        name: 'Committed Saga',
        repos: ['https://github.com/org/repo'],
        feature_branch: 'feat/committed',
        base_branch: 'main',
        status: 'Active',
      };
      mockFetch.mockReturnValueOnce(mockResponse(commitResponse));

      const request = {
        name: 'Committed Saga',
        slug: 'committed-saga',
        description: 'A committed saga',
        repos: ['https://github.com/org/repo'],
        base_branch: 'main',
        phases: [
          {
            name: 'Phase 1',
            raids: [
              {
                name: 'Raid 1',
                description: 'First raid',
                acceptance_criteria: ['AC1'],
                declared_files: ['file.ts'],
                estimate_hours: 2,
              },
            ],
          },
          {
            name: 'Phase 2',
            raids: [],
          },
        ],
      };

      const saga = await service.commitSaga(request);

      expect(saga).toMatchObject({
        id: 'saga-committed-1',
        tracker_id: 'tracker-99',
        tracker_type: 'linear',
        slug: 'committed-saga',
        name: 'Committed Saga',
        repos: ['https://github.com/org/repo'],
        feature_branch: 'feat/committed',
        status: 'active',
        confidence: 0,
        created_at: '',
      });
    });

    it('maps phase count to phase_summary', async () => {
      const commitResponse = {
        id: 'saga-2',
        tracker_id: 't-2',
        tracker_type: 'linear',
        slug: 'test',
        name: 'Test',
        repos: [],
        feature_branch: 'feat/test',
        base_branch: 'main',
        status: 'planning',
      };
      mockFetch.mockReturnValueOnce(mockResponse(commitResponse));

      const request = {
        name: 'Test',
        slug: 'test',
        description: '',
        repos: [],
        base_branch: 'main',
        phases: [
          { name: 'P1', raids: [] },
          { name: 'P2', raids: [] },
          { name: 'P3', raids: [] },
        ],
      };

      const saga = await service.commitSaga(request);

      expect(saga.phase_summary).toEqual({ total: 3, completed: 0 });
    });

    it('lowercases status from response', async () => {
      const commitResponse = {
        id: 'saga-3',
        tracker_id: 't-3',
        tracker_type: 'github',
        slug: 'upper',
        name: 'Upper',
        repos: [],
        feature_branch: 'feat/upper',
        base_branch: 'main',
        status: 'ACTIVE',
      };
      mockFetch.mockReturnValueOnce(mockResponse(commitResponse));

      const request = {
        name: 'Upper',
        slug: 'upper',
        description: '',
        repos: [],
        base_branch: 'main',
        phases: [],
      };

      const saga = await service.commitSaga(request);

      expect(saga.status).toBe('active');
    });

    it('sends request body to API', async () => {
      const commitResponse = {
        id: 'saga-4',
        tracker_id: 't-4',
        tracker_type: 'linear',
        slug: 'body-test',
        name: 'Body Test',
        repos: ['repo1'],
        feature_branch: 'feat/body',
        base_branch: 'main',
        status: 'active',
      };
      mockFetch.mockReturnValueOnce(mockResponse(commitResponse));

      const request = {
        name: 'Body Test',
        slug: 'body-test',
        description: 'Testing body',
        repos: ['repo1'],
        base_branch: 'main',
        phases: [],
        transcript: 'Some transcript',
      };

      await service.commitSaga(request);

      const fetchCall = mockFetch.mock.calls[0];
      const body = JSON.parse(fetchCall[1].body);
      expect(body.name).toBe('Body Test');
      expect(body.transcript).toBe('Some transcript');
    });
  });

  describe('spawnPlanSession', () => {
    it('posts spec and repo and returns plan session', async () => {
      const planSessionResponse = {
        session_id: 'plan-sess-001',
        chat_endpoint: 'wss://sessions.test/s/plan-sess-001/session',
      };
      mockFetch.mockReturnValueOnce(mockResponse(planSessionResponse));

      const result = await service.spawnPlanSession(
        'Build feature X',
        'https://github.com/org/repo'
      );

      expect(result).toEqual(planSessionResponse);

      const fetchCall = mockFetch.mock.calls[0];
      const body = JSON.parse(fetchCall[1].body);
      expect(body.spec).toBe('Build feature X');
      expect(body.repo).toBe('https://github.com/org/repo');
    });

    it('handles empty spec and repo', async () => {
      const planSessionResponse = {
        session_id: 'plan-sess-002',
        chat_endpoint: null,
      };
      mockFetch.mockReturnValueOnce(mockResponse(planSessionResponse));

      const result = await service.spawnPlanSession('', '');

      expect(result.session_id).toBe('plan-sess-002');
      expect(result.chat_endpoint).toBeNull();
    });
  });

  describe('extractStructure', () => {
    it('posts text and returns extracted structure', async () => {
      const extractResponse = {
        found: true,
        structure: {
          name: 'My Feature',
          phases: [
            {
              name: 'Phase 1',
              raids: [
                {
                  name: 'Raid 1',
                  description: 'Do thing',
                  acceptance_criteria: ['AC1'],
                  declared_files: ['file.ts'],
                  estimate_hours: 4,
                  confidence: 0.8,
                },
              ],
            },
          ],
        },
      };
      mockFetch.mockReturnValueOnce(mockResponse(extractResponse));

      const result = await service.extractStructure('Build a new auth system');

      expect(result.found).toBe(true);
      expect(result.structure?.name).toBe('My Feature');
      expect(result.structure?.phases).toHaveLength(1);

      const fetchCall = mockFetch.mock.calls[0];
      const body = JSON.parse(fetchCall[1].body);
      expect(body.text).toBe('Build a new auth system');
    });

    it('returns not found when no structure detected', async () => {
      const extractResponse = {
        found: false,
        structure: null,
      };
      mockFetch.mockReturnValueOnce(mockResponse(extractResponse));

      const result = await service.extractStructure('Random text');

      expect(result.found).toBe(false);
      expect(result.structure).toBeNull();
    });
  });
});
