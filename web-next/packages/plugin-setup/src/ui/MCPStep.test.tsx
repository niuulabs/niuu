import { fireEvent, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { describe, expect, it, vi } from 'vitest';
import { createMockSetupService } from '../adapters/mock';
import { renderWithSetup } from '../testing/renderWithSetup';
import { MCPStep } from './MCPStep';

describe('MCP onboarding', () => {
  it('connects a token, refreshes the list and tests the connection', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    const list = vi.spyOn(service, 'listIntegrations').mockResolvedValue([]);
    vi.spyOn(service, 'connectMCP').mockImplementation(async () => {
      list.mockResolvedValue([
        {
          id: 'linear',
          slug: 'mcp',
          integrationType: 'mcp',
          credentialName: 'internal',
          enabled: true,
          config: { name: 'Linear', mcp_url: 'https://mcp.linear.app/mcp' },
          credentialStatus: 'active',
          credentialExpiresAt: null,
          credentialErrorCode: null,
        },
      ]);
      return { connection_id: 'linear' };
    });
    const test = vi
      .spyOn(service, 'testIntegration')
      .mockResolvedValue({
        success: true,
        provider: 'mcp',
        workspace: null,
        user: null,
        error: null,
        detail: 'Connected',
        repositories: [],
      });
    renderWithSetup(<MCPStep />, { service });
    expect(screen.getByRole('status')).toHaveTextContent('Loading MCP');
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Server URL'), {
      target: { value: 'https://mcp.linear.app/mcp' },
    });
    fireEvent.change(screen.getByLabelText('Authentication'), { target: { value: 'token' } });
    fireEvent.change(screen.getByLabelText('API token'), { target: { value: 'test-token' } });
    fireEvent.click(screen.getByRole('button', { name: 'Connect MCP server' }));
    expect(await screen.findByRole('heading', { name: 'Linear' })).toBeInTheDocument();
    expect(screen.queryByText('internal')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }));
    await waitFor(() => expect(test).toHaveBeenCalledWith('linear'));
    expect(await screen.findByTestId('setup-test-ok-mcp')).toBeInTheDocument();
  });

  it('reports loading failures without claiming an empty successful result', async () => {
    const service = createMockSetupService({ latencyMs: 0 });
    vi.spyOn(service, 'listIntegrations').mockRejectedValue(new Error('offline'));
    renderWithSetup(<MCPStep />, { service });
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load MCP connections');
  });
});
