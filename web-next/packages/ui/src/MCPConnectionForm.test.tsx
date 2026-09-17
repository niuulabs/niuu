import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it, vi } from 'vitest';
import { MCPConnectionForm } from './MCPConnectionForm';

const metadata = {
  issuer: 'https://auth.example',
  resource: 'https://tools.example/mcp',
  scope: 'read',
  registration_available: true,
};

describe('MCPConnectionForm', () => {
  it('discovers OAuth before opening the authorization URL', async () => {
    const done = vi.fn();
    const discover = vi.fn().mockResolvedValue(metadata);
    const connect = vi
      .fn()
      .mockResolvedValue({ url: 'https://auth.example/authorize', connection_id: 'linear' });
    render(
      <MCPConnectionForm
        discover={discover}
        connect={connect}
        onConnected={done}
        connections={[]}
      />,
    );
    fireEvent.change(screen.getByLabelText('Server URL'), { target: { value: metadata.resource } });
    fireEvent.click(screen.getByRole('button', { name: 'Discover authentication' }));
    expect(await screen.findByText(/Permissions: read/)).toBeInTheDocument();
    expect(connect).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Connect MCP server' }));
    expect(await screen.findByRole('link', { name: 'Continue to sign in' })).toHaveAttribute(
      'href',
      'https://auth.example/authorize',
    );
    fireEvent(
      window,
      new StorageEvent('storage', { key: 'niuu:mcp-connected', newValue: 'unrelated' }),
    );
    expect(done).not.toHaveBeenCalled();
    fireEvent(
      window,
      new StorageEvent('storage', { key: 'niuu:mcp-connected', newValue: 'linear' }),
    );
    expect(await screen.findByText('MCP server connected')).toBeInTheDocument();
    expect(done).toHaveBeenCalledOnce();
    expect(connect).toHaveBeenCalledWith(
      expect.objectContaining({ server_url: metadata.resource }),
    );
  });

  it('stores an API token and clears the field', async () => {
    const connect = vi.fn().mockResolvedValue({ connection_id: 'connection' });
    const done = vi.fn();
    render(
      <MCPConnectionForm
        discover={vi.fn()}
        connect={connect}
        onConnected={done}
        connections={[]}
      />,
    );
    fireEvent.change(screen.getByLabelText('Authentication'), { target: { value: 'token' } });
    fireEvent.change(screen.getByLabelText('Server URL'), { target: { value: metadata.resource } });
    fireEvent.change(screen.getByLabelText('API token'), { target: { value: 'test-token' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect MCP server' }));
    expect(await screen.findByRole('status')).toHaveTextContent('MCP server connected');
    expect(screen.getByLabelText('API token')).toHaveValue('');
    expect(done).toHaveBeenCalledOnce();
  });

  it('shows a safe error and permits retry', async () => {
    const discover = vi.fn().mockRejectedValue(new Error('secret provider response'));
    render(
      <MCPConnectionForm
        discover={discover}
        connect={vi.fn()}
        onConnected={vi.fn()}
        connections={[]}
      />,
    );
    fireEvent.change(screen.getByLabelText('Server URL'), { target: { value: metadata.resource } });
    fireEvent.click(screen.getByRole('button', { name: 'Discover authentication' }));
    expect(await screen.findByRole('alert')).not.toHaveTextContent('secret provider response');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Discover authentication' })).toBeEnabled(),
    );
  });
});

it('preserves custom authentication headers when reconnecting an MCP', async () => {
  const connect = vi.fn().mockResolvedValue({ connection_id: 'custom' });
  render(
    <MCPConnectionForm
      connections={[
        {
          id: 'custom',
          config: {
            name: 'Custom',
            mcp_url: metadata.resource,
            auth_header: 'X-API-Key',
            auth_prefix: '',
          },
        },
      ]}
      initialConnectionId="custom"
      discover={vi.fn()}
      connect={connect}
      onConnected={vi.fn()}
    />,
  );
  fireEvent.change(screen.getByLabelText('Authentication'), { target: { value: 'token' } });
  fireEvent.change(screen.getByLabelText('API token'), { target: { value: 'replacement' } });
  fireEvent.click(screen.getByRole('button', { name: 'Connect MCP server' }));
  await waitFor(() =>
    expect(connect).toHaveBeenCalledWith(
      expect.objectContaining({
        connection_id: 'custom',
        auth_header: 'X-API-Key',
        auth_prefix: '',
        api_token: 'replacement',
      }),
    ),
  );
  fireEvent.change(screen.getByLabelText('API token'), { target: { value: 'unsaved-secret' } });
  fireEvent.change(screen.getByLabelText('Connection'), { target: { value: '' } });
  expect(screen.getByLabelText('API token')).toHaveValue('');
});
