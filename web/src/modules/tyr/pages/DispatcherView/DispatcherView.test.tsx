import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DispatcherView } from './DispatcherView';

vi.mock('../../hooks/useDispatchQueue', () => ({
  useDispatchQueue: vi.fn(),
}));

vi.mock('@/modules/shared', () => ({
  LoadingIndicator: ({ messages }: { messages?: string[] }) => (
    <div data-testid="loading-indicator">{messages?.[0]}</div>
  ),
}));

import { useDispatchQueue } from '../../hooks/useDispatchQueue';

const mockDefaults = {
  default_system_prompt: 'test prompt',
  default_model: 'claude-sonnet-4-6',
  models: [
    { id: 'claude-sonnet-4-6', name: 'Sonnet 4.6' },
    { id: 'claude-opus-4-6', name: 'Opus 4.6' },
  ],
};

const mockQueueItem = {
  saga_id: 's1',
  saga_name: 'Test Saga',
  saga_slug: 'test',
  repos: ['https://github.com/org/repo'],
  feature_branch: 'feat/test',
  phase_name: 'Phase 1',
  issue_id: 'i1',
  identifier: 'NIU-100',
  title: 'Test issue',
  description: '',
  status: 'Todo',
  priority: 1,
  priority_label: 'Urgent',
  estimate: 3,
  url: '',
};

describe('DispatcherView', () => {
  beforeEach(() => {
    vi.mocked(useDispatchQueue).mockReturnValue({
      queue: [mockQueueItem],
      defaults: mockDefaults,
      clusters: [],
      loading: false,
      error: null,
      dispatching: false,
      refresh: vi.fn(),
      dispatch: vi.fn(),
    });
  });

  it('renders queue items', () => {
    render(<DispatcherView />);
    expect(screen.getByText('NIU-100')).toBeInTheDocument();
    expect(screen.getByText('Test issue')).toBeInTheDocument();
  });

  it('renders loading indicator', () => {
    vi.mocked(useDispatchQueue).mockReturnValue({
      queue: [],
      defaults: mockDefaults,
      clusters: [],
      loading: true,
      error: null,
      dispatching: false,
      refresh: vi.fn(),
      dispatch: vi.fn(),
    });
    render(<DispatcherView />);
    expect(screen.getByText('Loading dispatch queue...')).toBeInTheDocument();
  });

  it('renders error message', () => {
    vi.mocked(useDispatchQueue).mockReturnValue({
      queue: [],
      defaults: mockDefaults,
      clusters: [],
      loading: false,
      error: 'Something broke',
      dispatching: false,
      refresh: vi.fn(),
      dispatch: vi.fn(),
    });
    render(<DispatcherView />);
    expect(screen.getByText('Something broke')).toBeInTheDocument();
  });

  it('renders empty state', () => {
    vi.mocked(useDispatchQueue).mockReturnValue({
      queue: [],
      defaults: mockDefaults,
      clusters: [],
      loading: false,
      error: null,
      dispatching: false,
      refresh: vi.fn(),
      dispatch: vi.fn(),
    });
    render(<DispatcherView />);
    expect(screen.getByText('No issues ready for dispatch')).toBeInTheDocument();
  });

  it('renders queue count', () => {
    render(<DispatcherView />);
    expect(screen.getByText('1 ready')).toBeInTheDocument();
  });

  it('renders select all and clear buttons', () => {
    render(<DispatcherView />);
    expect(screen.getByText('Select All')).toBeInTheDocument();
    expect(screen.getByText('Clear')).toBeInTheDocument();
  });

  it('renders priority and estimate', () => {
    render(<DispatcherView />);
    expect(screen.getByText('Urgent')).toBeInTheDocument();
    expect(screen.getByText('3pt')).toBeInTheDocument();
  });

  it('renders phase name', () => {
    render(<DispatcherView />);
    expect(screen.getByText('Phase 1')).toBeInTheDocument();
  });

  it('renders system prompt textarea when queue has items', () => {
    render(<DispatcherView />);
    expect(screen.getByText('System Prompt (optional)')).toBeInTheDocument();
  });

  it('renders model dropdown from config', () => {
    render(<DispatcherView />);
    expect(screen.getByText('Sonnet 4.6')).toBeInTheDocument();
    expect(screen.getByText('Opus 4.6')).toBeInTheDocument();
  });

  it('renders saga group header', () => {
    render(<DispatcherView />);
    expect(screen.getByText('Test Saga')).toBeInTheDocument();
  });

  it('selects all and clears selection', () => {
    render(<DispatcherView />);
    fireEvent.click(screen.getByText('Select All'));
    expect(screen.getByText('1 selected')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Clear'));
    expect(screen.getByText('0 selected')).toBeInTheDocument();
  });

  it('toggles individual item selection', () => {
    render(<DispatcherView />);
    const checkbox = screen.getByRole('checkbox');
    fireEvent.click(checkbox);
    expect(screen.getByText('1 selected')).toBeInTheDocument();
    fireEvent.click(checkbox);
    expect(screen.getByText('0 selected')).toBeInTheDocument();
  });

  it('shows dispatch button disabled when nothing selected', () => {
    render(<DispatcherView />);
    const btn = screen.getByText('Dispatch 0');
    expect(btn).toBeDisabled();
  });

  it('dispatches and shows results', async () => {
    const mockDispatch = vi
      .fn()
      .mockResolvedValue([{ issue_id: 'i1', session_name: 'niu-100', status: 'spawned' }]);
    vi.mocked(useDispatchQueue).mockReturnValue({
      queue: [mockQueueItem],
      defaults: mockDefaults,
      clusters: [],
      loading: false,
      error: null,
      dispatching: false,
      refresh: vi.fn(),
      dispatch: mockDispatch,
    });
    render(<DispatcherView />);
    fireEvent.click(screen.getByText('Select All'));
    fireEvent.click(screen.getByText('Dispatch 1'));
    const { waitFor } = await import('@testing-library/react');
    await waitFor(() => {
      expect(screen.getByText(/niu-100/)).toBeInTheDocument();
    });
    // Dismiss results
    fireEvent.click(screen.getByText('Dismiss'));
  });

  it('shows a submitting batch overlay while dispatch is in flight', async () => {
    let resolveDispatch:
      | ((value: { issue_id: string; session_name: string; status: string }[]) => void)
      | null = null;
    const mockDispatch = vi.fn(
      () =>
        new Promise<{ issue_id: string; session_name: string; status: string }[]>(resolve => {
          resolveDispatch = resolve;
        })
    );

    vi.mocked(useDispatchQueue).mockReturnValue({
      queue: [mockQueueItem],
      defaults: mockDefaults,
      clusters: [],
      loading: false,
      error: null,
      dispatching: false,
      refresh: vi.fn(),
      dispatch: mockDispatch,
    });

    render(<DispatcherView />);
    const user = userEvent.setup();

    await user.click(screen.getByText('Select All'));
    const { waitFor } = await import('@testing-library/react');
    await waitFor(() => {
      expect(screen.getByText('1 selected')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Dispatch 1'));

    await waitFor(() => {
      expect(screen.getByText('Dispatching 1 item')).toBeInTheDocument();
    });
    expect(screen.getAllByText('Submitting').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Dispatching...' })).toBeDisabled();

    resolveDispatch?.([{ issue_id: 'i1', session_name: 'niu-100', status: 'spawned' }]);

    await waitFor(() => {
      expect(screen.queryByText('Dispatching 1 item')).not.toBeInTheDocument();
    });
  });

  it('shows dispatching state', () => {
    vi.mocked(useDispatchQueue).mockReturnValue({
      queue: [mockQueueItem],
      defaults: mockDefaults,
      clusters: [],
      loading: false,
      error: null,
      dispatching: true,
      refresh: vi.fn(),
      dispatch: vi.fn(),
    });
    render(<DispatcherView />);
    expect(screen.getByText('Dispatching...')).toBeInTheDocument();
  });

  it('shows cluster selector when multiple clusters available', () => {
    vi.mocked(useDispatchQueue).mockReturnValue({
      queue: [mockQueueItem],
      defaults: mockDefaults,
      clusters: [
        { connection_id: 'c1', name: 'production', url: 'http://prod', enabled: true },
        { connection_id: 'c2', name: 'staging', url: 'http://staging', enabled: true },
      ],
      loading: false,
      error: null,
      dispatching: false,
      refresh: vi.fn(),
      dispatch: vi.fn(),
    });
    render(<DispatcherView />);
    expect(screen.getByText('Auto (default cluster)')).toBeInTheDocument();
    expect(screen.getByText('production')).toBeInTheDocument();
    expect(screen.getByText('staging')).toBeInTheDocument();
  });

  it('hides cluster selector with single cluster', () => {
    vi.mocked(useDispatchQueue).mockReturnValue({
      queue: [mockQueueItem],
      defaults: mockDefaults,
      clusters: [{ connection_id: 'c1', name: 'production', url: 'http://prod', enabled: true }],
      loading: false,
      error: null,
      dispatching: false,
      refresh: vi.fn(),
      dispatch: vi.fn(),
    });
    render(<DispatcherView />);
    expect(screen.queryByText('Auto (default cluster)')).not.toBeInTheDocument();
  });

  it('passes selected cluster to dispatch', async () => {
    const mockDispatch = vi
      .fn()
      .mockResolvedValue([{ issue_id: 'i1', session_name: 'niu-100', status: 'spawned' }]);
    vi.mocked(useDispatchQueue).mockReturnValue({
      queue: [mockQueueItem],
      defaults: mockDefaults,
      clusters: [
        { connection_id: 'c1', name: 'production', url: 'http://prod', enabled: true },
        { connection_id: 'c2', name: 'staging', url: 'http://staging', enabled: true },
      ],
      loading: false,
      error: null,
      dispatching: false,
      refresh: vi.fn(),
      dispatch: mockDispatch,
    });
    render(<DispatcherView />);

    // Select a cluster
    const clusterSelect = screen.getByDisplayValue('Auto (default cluster)');
    fireEvent.change(clusterSelect, { target: { value: 'c2' } });

    // Select item and dispatch
    fireEvent.click(screen.getByText('Select All'));
    fireEvent.click(screen.getByText('Dispatch 1'));

    const { waitFor } = await import('@testing-library/react');
    await waitFor(() => {
      expect(mockDispatch).toHaveBeenCalledWith(
        expect.any(Array),
        'claude-sonnet-4-6',
        'test prompt',
        'c2',
        undefined,
        undefined
      );
    });
  });
});
