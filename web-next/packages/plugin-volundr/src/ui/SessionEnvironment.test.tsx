import { render, screen, fireEvent } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SessionEnvironment } from './SessionEnvironment';
import type { VolundrSession } from '../models/volundr.model';

const session = {
  id: 'session-123',
  source: { type: 'git', repo: 'org/repo', branch: 'review' },
} as VolundrSession;
afterEach(() => vi.unstubAllGlobals());
it('expands the source, copies its branch and session ID, and previews the repository', async () => {
  const copy = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal('navigator', { clipboard: { writeText: copy } });
  render(
    <SessionEnvironment
      session={session}
      repoLabel="org/repo"
      repoUrl="https://github.com/org/repo"
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Session workspace details' }));
  expect(screen.getByText('/workspace')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Copy branch' }));
  expect(await screen.findByText('Branch copied')).toBeInTheDocument();
  expect(copy).toHaveBeenCalledWith('review');
  fireEvent.click(screen.getByRole('button', { name: 'Copy session ID' }));
  expect(await screen.findByText('Session ID copied')).toBeInTheDocument();
  expect(copy).toHaveBeenCalledWith('session-123');
  fireEvent.click(screen.getByRole('button', { name: 'org/repo', exact: true }));
  expect(screen.getByTitle('Preview of repo')).toHaveAttribute(
    'src',
    'https://github.com/org/repo',
  );
});
it('uses the actual local mount path and reports a clipboard failure honestly', async () => {
  vi.stubGlobal('navigator', {
    clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
  });
  render(
    <SessionEnvironment
      session={{
        ...session,
        source: { type: 'local_mount', local_path: '/host/project', path: '/workspace', paths: [] },
      }}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Session workspace details' }));
  expect(screen.getByText('/host/project')).toBeInTheDocument();
  expect(screen.getByText('Not reported for this local mount')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Copy folder path' }));
  expect(await screen.findByText(/Could not copy/)).toBeInTheDocument();
});
it('handles a mount mapping and unavailable source metadata', () => {
  const { rerender } = render(
    <SessionEnvironment
      session={{
        ...session,
        source: {
          type: 'local_mount',
          paths: [{ host_path: '/mapped', mount_path: '/workspace', read_only: false }],
        },
      }}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Session workspace details' }));
  expect(screen.getByText('/mapped')).toBeInTheDocument();
  rerender(
    <SessionEnvironment session={{ ...session, source: { type: 'local_mount', paths: [] } }} />,
  );
  expect(screen.getByText('Not reported by Forge')).toBeInTheDocument();
  rerender(
    <SessionEnvironment
      session={{ ...session, source: { type: 'git', repo: 'offline:repo', branch: '' } }}
      repoLabel="offline:repo"
    />,
  );
  expect(screen.getAllByText('offline:repo')).toHaveLength(2);
  expect(screen.queryByRole('button', { name: 'Copy branch' })).not.toBeInTheDocument();
});
