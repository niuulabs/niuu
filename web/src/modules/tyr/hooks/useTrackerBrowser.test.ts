import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useTrackerBrowser } from './useTrackerBrowser';

const mockProjects = [{ id: 'p-1', name: 'Project 1', url: 'https://example.com/p-1' }];

const mockMilestones = [{ id: 'ms-1', title: 'Sprint 1', project_id: 'p-1' }];

const mockIssues = [
  { id: 'i-1', identifier: 'NIU-1', title: 'Fix bug', status: 'Todo', project_id: 'p-1' },
];

vi.mock('../adapters', () => ({
  trackerService: {
    listProjects: vi.fn(),
    getProject: vi.fn(),
    listMilestones: vi.fn(),
    listIssues: vi.fn(),
    importProject: vi.fn(),
  },
}));

import { trackerService } from '../adapters';

describe('useTrackerBrowser', () => {
  beforeEach(() => {
    vi.mocked(trackerService.listProjects).mockResolvedValue(mockProjects);
    vi.mocked(trackerService.getProject).mockResolvedValue(mockProjects[0]);
    vi.mocked(trackerService.listMilestones).mockResolvedValue(mockMilestones);
    vi.mocked(trackerService.listIssues).mockResolvedValue(mockIssues);
    vi.mocked(trackerService.importProject).mockResolvedValue(undefined);

    vi.spyOn(global, 'fetch').mockImplementation(async input => {
      const url = typeof input === 'string' ? input : (input as Request).url;
      if (url.includes('/repos')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            github: [{ url: 'https://github.com/org/repo', name: 'repo', default_branch: 'main' }],
          }),
        } as Response;
      }
      if (url.includes('/sagas')) {
        return {
          ok: true,
          status: 200,
          json: async () => [{ id: 'saga-1', tracker_id: 'p-1' }],
        } as Response;
      }
      return { ok: true, status: 200, json: async () => ({}) } as Response;
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('should load projects, repos, and imported tracker ids on mount', async () => {
    const { result } = renderHook(() => useTrackerBrowser());
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.projects).toEqual(mockProjects);
    expect(result.current.repos).toHaveLength(1);
    expect(result.current.importedTrackerIds.has('p-1')).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it('should handle load error', async () => {
    vi.mocked(trackerService.listProjects).mockRejectedValue(new Error('fail'));
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe('fail');
  });

  it('should handle non-Error load rejection', async () => {
    vi.mocked(trackerService.listProjects).mockRejectedValue('string err');
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe('string err');
  });

  it('should handle selectProject error', async () => {
    vi.mocked(trackerService.getProject).mockRejectedValue(new Error('not found'));
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      result.current.selectProject('bad-id');
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe('not found');
  });

  it('should handle selectProject non-Error rejection', async () => {
    vi.mocked(trackerService.getProject).mockRejectedValue('boom');
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      result.current.selectProject('bad-id');
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe('boom');
  });

  it('should toggle unknown repo using main as default branch', async () => {
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      result.current.toggleRepo('https://github.com/org/unknown');
    });
    expect(result.current.selectedRepos).toHaveLength(1);
    expect(result.current.selectedRepos[0].branch).toBe('main');
  });

  it('should select a project and load milestones/issues', async () => {
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      result.current.selectProject('p-1');
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.selectedProject).toEqual(mockProjects[0]);
    expect(result.current.milestones).toEqual(mockMilestones);
    expect(result.current.issues).toEqual(mockIssues);
  });

  it('should clear project', async () => {
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      result.current.selectProject('p-1');
    });
    await waitFor(() => expect(result.current.selectedProject).not.toBeNull());

    act(() => {
      result.current.clearProject();
    });
    expect(result.current.selectedProject).toBeNull();
    expect(result.current.milestones).toHaveLength(0);
    expect(result.current.issues).toHaveLength(0);
  });

  it('should filter by milestone', async () => {
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      result.current.filterByMilestone('ms-1');
    });
    expect(result.current.selectedMilestone).toBe('ms-1');

    act(() => {
      result.current.filterByMilestone(null);
    });
    expect(result.current.selectedMilestone).toBeNull();
  });

  it('should toggle repos', async () => {
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      result.current.toggleRepo('https://github.com/org/repo');
    });
    expect(result.current.selectedRepos).toHaveLength(1);
    expect(result.current.selectedRepos[0].branch).toBe('main');

    act(() => {
      result.current.toggleRepo('https://github.com/org/repo');
    });
    expect(result.current.selectedRepos).toHaveLength(0);
  });

  it('should set branch for repo', async () => {
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      result.current.toggleRepo('https://github.com/org/repo');
    });

    act(() => {
      result.current.setBranch('https://github.com/org/repo', 'develop');
    });
    expect(result.current.selectedRepos[0].branch).toBe('develop');
  });

  it('should import project', async () => {
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      result.current.selectProject('p-1');
    });
    await waitFor(() => expect(result.current.selectedProject).not.toBeNull());

    act(() => {
      result.current.toggleRepo('https://github.com/org/repo');
    });

    await act(async () => {
      await result.current.importProject();
    });
    expect(trackerService.importProject).toHaveBeenCalledWith(
      'p-1',
      ['https://github.com/org/repo'],
      'main'
    );
  });

  it('should noop import when no project selected', async () => {
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));

    const callsBefore = vi.mocked(trackerService.importProject).mock.calls.length;
    await act(async () => {
      await result.current.importProject();
    });
    expect(vi.mocked(trackerService.importProject).mock.calls.length).toBe(callsBefore);
  });

  it('should handle importProject error', async () => {
    vi.mocked(trackerService.importProject).mockRejectedValue(new Error('import fail'));
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      result.current.selectProject('p-1');
    });
    await waitFor(() => expect(result.current.selectedProject).not.toBeNull());

    act(() => {
      result.current.toggleRepo('https://github.com/org/repo');
    });

    await act(async () => {
      try {
        await result.current.importProject();
      } catch {
        // expected
      }
    });
    expect(result.current.error).toBe('import fail');
  });

  it('should handle importProject non-Error rejection', async () => {
    vi.mocked(trackerService.importProject).mockRejectedValue('string import error');
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      result.current.selectProject('p-1');
    });
    await waitFor(() => expect(result.current.selectedProject).not.toBeNull());

    act(() => {
      result.current.toggleRepo('https://github.com/org/repo');
    });

    await act(async () => {
      try {
        await result.current.importProject();
      } catch {
        // expected
      }
    });
    expect(result.current.error).toBe('string import error');
  });

  it('should set branch for non-matching repo id (no-op)', async () => {
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => {
      result.current.toggleRepo('https://github.com/org/repo');
    });

    act(() => {
      result.current.setBranch('nonexistent-repo', 'develop');
    });
    // Original repo unchanged
    expect(result.current.selectedRepos[0].branch).toBe('main');
  });

  it('should noop import when no repos selected', async () => {
    const { result } = renderHook(() => useTrackerBrowser());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      result.current.selectProject('p-1');
    });
    await waitFor(() => expect(result.current.selectedProject).not.toBeNull());

    const callsBefore = vi.mocked(trackerService.importProject).mock.calls.length;
    await act(async () => {
      await result.current.importProject();
    });
    expect(vi.mocked(trackerService.importProject).mock.calls.length).toBe(callsBefore);
  });
});
