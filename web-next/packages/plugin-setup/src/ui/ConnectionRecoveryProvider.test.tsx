import { fireEvent, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it, vi } from 'vitest';
import { ChatConnectionsButton } from '@niuulabs/ui';
import { createMockSetupService } from '../adapters/mock';
import { renderWithSetup } from '../testing/renderWithSetup';
import { ConnectionRecoveryDialog, ConnectionRecoveryProvider } from './ConnectionRecoveryProvider';

const account = {
  id: 'account-1',
  slug: 'codex',
  integrationType: 'ai_provider',
  credentialName: 'codex-work',
  enabled: true,
  config: {},
  credentialStatus: 'auth_required',
};
function renderRecovery(service = createMockSetupService({ latencyMs: 0 })) {
  renderWithSetup(
    <ConnectionRecoveryProvider>
      <p>Current conversation</p>
      <ChatConnectionsButton />
    </ConnectionRecoveryProvider>,
    { service },
  );
  fireEvent.click(screen.getByRole('button', { name: 'Reconnect account' }));
  return service;
}

describe('chat account recovery', () => {
  it('reconnects the existing Codex account without leaving the conversation', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    vi.spyOn(service, 'listIntegrations').mockResolvedValue([account]);
    const start = vi.spyOn(service, 'startEnrollment');
    renderRecovery(service);
    expect(screen.getByRole('status')).toHaveTextContent('Loading accounts');
    await screen.findByRole('option', { name: /codex-work/ });
    fireEvent.change(screen.getByLabelText('Account'), { target: { value: account.id } });
    fireEvent.click(screen.getByTestId('setup-signin-start-codex'));
    // The sign-in names the existing connection, so the backend refreshes it in place.
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith('codex', 'codex-work', 'default', account.id),
    );
    expect(screen.getByText('Current conversation')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Return to chat' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('preselects the existing custom MCP and keeps its connection id', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    const mcp = {
      ...account,
      slug: 'mcp',
      config: { name: 'Linear', mcp_url: 'https://mcp.linear.app/mcp' },
    };
    vi.spyOn(service, 'listIntegrations').mockResolvedValue([mcp]);
    const connect = vi
      .spyOn(service, 'connectMCP')
      .mockResolvedValue({ connection_id: account.id });
    renderRecovery(service);
    await screen.findByRole('option', { name: /Linear/ });
    fireEvent.change(screen.getByLabelText('Account'), { target: { value: account.id } });
    expect(screen.getByLabelText('Server URL')).toHaveValue(mcp.config.mcp_url);
    fireEvent.change(screen.getByLabelText('Authentication'), { target: { value: 'token' } });
    fireEvent.change(screen.getByLabelText('API token'), { target: { value: 'test-only' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect MCP server' }));
    await waitFor(() =>
      expect(connect).toHaveBeenCalledWith(
        expect.objectContaining({ connection_id: account.id, api_token: 'test-only' }),
      ),
    );
  });

  it('replaces an API key on the same connection and displays failures', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    vi.spyOn(service, 'listIntegrations').mockResolvedValue([
      { ...account, slug: 'openai', credentialName: 'openai-work' },
    ]);
    const connect = vi
      .spyOn(service, 'connectIntegration')
      .mockRejectedValueOnce(new Error('Storage unavailable'))
      .mockResolvedValue({ ...account, slug: 'openai' });
    renderRecovery(service);
    await screen.findByRole('option', { name: /openai-work/ });
    fireEvent.change(screen.getByLabelText('Account'), { target: { value: account.id } });
    fireEvent.change(screen.getByTestId('setup-input-openai-api_key'), {
      target: { value: 'test-only' },
    });
    fireEvent.click(screen.getByTestId('setup-connect-openai'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Storage unavailable');
    fireEvent.click(screen.getByTestId('setup-connect-openai'));
    expect(await screen.findByRole('status')).toHaveTextContent('Account updated');
    expect(connect).toHaveBeenLastCalledWith(
      expect.objectContaining({ connectionId: account.id, credentialName: 'openai-work' }),
    );
  });

  it('opens on a given account with Settings copy and explains why it needs a sign-in', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    vi.spyOn(service, 'listIntegrations').mockResolvedValue([
      { ...account, credentialErrorCode: 'oauth_reconnect_required' },
    ]);
    const start = vi.spyOn(service, 'startEnrollment');
    const onClose = vi.fn();
    renderWithSetup(
      <ConnectionRecoveryDialog
        context="settings"
        initialConnectionId={account.id}
        onClose={onClose}
      />,
      { service },
    );
    await screen.findByRole('option', { name: /codex-work/ });
    expect(screen.getByLabelText('Account')).toHaveValue(account.id);
    expect(screen.getByTestId('setup-recovery-problem')).toHaveTextContent(
      'The provider no longer accepts the stored sign-in',
    );
    expect(screen.queryByRole('button', { name: 'Return to chat' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('setup-signin-start-codex'));
    await waitFor(() =>
      expect(start).toHaveBeenCalledWith('codex', 'codex-work', 'default', account.id),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('reports account list failures and allows retry', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    vi.spyOn(service, 'listIntegrations')
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue([]);
    renderRecovery(service);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load accounts');
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText(/No connected accounts/)).toBeInTheDocument();
  });
});
