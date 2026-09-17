import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { ForgeHostsPage } from './ForgeHostsPage';
import { createMockVolundrService } from '../adapters/mock';
import { renderWithVolundr } from '../testing/renderWithVolundr';
const host = {
  id: 'thor',
  slug: 'local',
  name: 'Thor',
  baseUrl: 'http://127.0.0.1:8080',
  enabled: true,
  isDefault: true,
  tags: [],
  config: { transport: 'embedded', defaultFolder: '/home/thor/repos' },
};
function setup(overrides = {}) {
  const saveForgeHost = vi.fn(async (h) => ({ ...host, ...h }));
  const testForgeHost = vi.fn(async () => ({ ok: true, message: 'Thor is reachable' }));
  renderWithVolundr(<ForgeHostsPage />, {
    service: {
      ...createMockVolundrService(),
      getForgeHosts: async () => [host],
      saveForgeHost,
      testForgeHost,
      ...overrides,
    },
  });
  return { saveForgeHost, testForgeHost };
}
describe('Forge host configuration', () => {
  it('shows names, addresses, defaults and tests the actual registered host', async () => {
    const { testForgeHost } = setup();
    await screen.findByText('http://127.0.0.1:8080');
    fireEvent.click(screen.getByRole('button', { name: 'Test Thor' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Thor is reachable');
    expect(testForgeHost).toHaveBeenCalledWith('thor');
  });
  it('saves an edited address/folder while preserving embedded routing configuration', async () => {
    const { saveForgeHost } = setup();
    fireEvent.click(await screen.findByRole('button', { name: 'Edit Thor' }));
    fireEvent.change(screen.getByLabelText('IP address or URL'), {
      target: { value: '100.66.123.128:8080' },
    });
    fireEvent.change(screen.getByLabelText('Default folder'), {
      target: { value: '/home/thor/review' },
    });
    fireEvent.change(screen.getByLabelText('Availability'), { target: { value: 'disabled' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Forge' }));
    await waitFor(() =>
      expect(saveForgeHost).toHaveBeenCalledWith({
        id: 'thor',
        slug: 'local',
        name: 'Thor',
        baseUrl: 'http://100.66.123.128:8080',
        enabled: false,
        config: { transport: 'embedded', defaultFolder: '/home/thor/review' },
      }),
    );
  });
  it('adds a personal host and validates missing names, paths and duplicate addresses', async () => {
    const { saveForgeHost } = setup();
    await screen.findByText('Thor · Default');
    fireEvent.click(screen.getByRole('button', { name: 'Add Forge' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save Forge' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Give this Forge a name');
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Build Bro' } });
    fireEvent.change(screen.getByLabelText('IP address or URL'), {
      target: { value: 'http://127.0.0.1:8080' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Forge' }));
    expect(screen.getByRole('alert')).toHaveTextContent('already registered');
    fireEvent.change(screen.getByLabelText('IP address or URL'), {
      target: { value: '100.115.8.110' },
    });
    fireEvent.change(screen.getByLabelText('Default folder'), { target: { value: '~/bad' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Forge' }));
    expect(screen.getByRole('alert')).toHaveTextContent('absolute default folder');
    fireEvent.change(screen.getByLabelText('Default folder'), { target: { value: '/home/horde' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save Forge' }));
    await waitFor(() =>
      expect(saveForgeHost).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Build Bro',
          slug: 'build-bro',
          baseUrl: 'http://100.115.8.110:8080',
        }),
      ),
    );
  });
  it('shows failed health checks and save errors without claiming success', async () => {
    setup({
      testForgeHost: async () => ({ ok: false, message: 'Connection refused' }),
      saveForgeHost: async () => {
        throw new Error('Permission denied');
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Test Thor' }));
    await screen.findByText('Connection refused');
    fireEvent.click(screen.getByRole('button', { name: 'Edit Thor' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save Forge' }));
    await screen.findByText('Permission denied');
    expect(screen.getByLabelText('Name')).toHaveValue('Thor');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByLabelText('Name')).not.toBeInTheDocument();
  });
  it('surfaces registry errors', async () => {
    setup({
      getForgeHosts: async () => {
        throw new Error('Registry unavailable');
      },
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('Registry unavailable');
  });
});
