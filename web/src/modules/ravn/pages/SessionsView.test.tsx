import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { SessionsView } from './SessionsView';

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

beforeEach(() => {
  mockFetch.mockReset();
});

describe('SessionsView', () => {
  it('shows loading state initially', () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    render(<SessionsView />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it('falls back to mock data when API returns empty', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    render(<SessionsView />);
    await waitFor(() => {
      expect(screen.getByText(/demo data/i)).toBeInTheDocument();
    });
  });

  it('renders sessions from API', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [
        { id: 'abc12345', status: 'running', model: 'claude-opus', created_at: '2026-04-06' },
      ],
    });
    render(<SessionsView />);
    await waitFor(() => {
      expect(screen.getByText('abc12345')).toBeInTheDocument();
    });
  });

  it('renders session status', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [
        { id: 'xyz00000', status: 'idle', model: 'claude-sonnet', created_at: '2026-04-06' },
      ],
    });
    render(<SessionsView />);
    await waitFor(() => {
      expect(screen.getByText('idle')).toBeInTheDocument();
    });
  });

  it('falls back to mock data when API returns non-ok response', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false });
    render(<SessionsView />);
    await waitFor(() => {
      expect(screen.getByText(/demo data/i)).toBeInTheDocument();
    });
  });

  it('renders session model', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [
        { id: 'aaa00000', status: 'running', model: 'claude-haiku', created_at: '2026-04-06' },
      ],
    });
    render(<SessionsView />);
    await waitFor(() => {
      expect(screen.getByText(/claude-haiku/)).toBeInTheDocument();
    });
  });

  it('renders multiple sessions', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [
        { id: 'aaa00001', status: 'running', model: 'claude-opus', created_at: '2026-04-06' },
        { id: 'bbb00002', status: 'stopped', model: 'claude-sonnet', created_at: '2026-04-06' },
      ],
    });
    render(<SessionsView />);
    await waitFor(() => {
      expect(screen.getByText('aaa00001')).toBeInTheDocument();
      expect(screen.getByText('bbb00002')).toBeInTheDocument();
    });
  });

  it('renders the heading', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => [],
    });
    render(<SessionsView />);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /active agent sessions/i })).toBeInTheDocument();
    });
  });
});
