import { describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { UserStorageSettings } from './UserStorageSettings';
import { renderWithVolundr } from '../testing/renderWithVolundr';
import { createMockVolundrService } from '../adapters/mock';
import type { UserHomeListing } from '../ports/IVolundrService';

const targets = ['valhalla', 'ymir'].map((id) => ({
  id,
  slug: id,
  name: id,
  baseUrl: '',
  enabled: true,
  isDefault: false,
  tags: [],
}));
const ready: UserHomeListing = {
  status: 'ready',
  capacity_bytes: 64 * 1024 ** 3,
  available_bytes: 63 * 1024 ** 3,
  entries: [{ name: 'tmp', path: 'tmp', kind: 'directory', size: 0 }],
};
function setup(listUserHome = vi.fn().mockResolvedValue(ready)) {
  const service = {
    ...createMockVolundrService(),
    getTargets: vi.fn().mockResolvedValue(targets),
    listUserHome,
    deleteUserHomePath: vi.fn().mockResolvedValue(undefined),
  };
  renderWithVolundr(<UserStorageSettings />, { service });
  return service;
}

describe('UserStorageSettings', () => {
  it('browses home, temp and caches on the selected cluster and confirms deletion', async () => {
    const service = setup();
    expect(await screen.findByText('63.0 GiB available of 64.0 GiB')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Temporary files' }));
    await waitFor(() =>
      expect(service.listUserHome).toHaveBeenCalledWith('valhalla', 'tmp/sessions'),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Caches' }));
    await waitFor(() => expect(service.listUserHome).toHaveBeenCalledWith('valhalla', 'tmp/cache'));
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'ymir' } });
    await waitFor(() => expect(service.listUserHome).toHaveBeenCalledWith('ymir', ''));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete tmp' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(service.deleteUserHomePath).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Delete tmp' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete permanently' }));
    await waitFor(() => expect(service.deleteUserHomePath).toHaveBeenCalledWith('ymir', 'tmp'));
  });
  it('shows mounting and refreshes to a ready listing', async () => {
    const service = setup(
      vi
        .fn()
        .mockResolvedValueOnce({ status: 'starting', detail: 'Mounting your home storage' })
        .mockResolvedValue(ready),
    );
    expect(await screen.findByRole('status')).toHaveTextContent(/Loading|Mounting|Opening/);
    expect(await screen.findByText('Mounting your home storage')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText('63.0 GiB available of 64.0 GiB')).toBeInTheDocument();
    expect(service.listUserHome).toHaveBeenCalledTimes(2);
  });
  it('shows unsupported storage errors', async () => {
    setup(
      vi.fn().mockRejectedValue(new Error('Home file management is not enabled on this cluster')),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent('not enabled');
  });
  it('keeps the confirmation open when an active session blocks deletion', async () => {
    const service = setup();
    service.deleteUserHomePath.mockRejectedValue(new Error('Stop sessions before deleting'));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete tmp' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete permanently' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Stop sessions');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});
