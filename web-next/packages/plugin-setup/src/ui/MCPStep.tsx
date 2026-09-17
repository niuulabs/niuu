import { useState } from 'react';
import { MCPConnectionForm } from '@niuulabs/ui';
import { useIntegrations, useSetupService, useTestIntegration } from './hooks';
import { TestOutcome } from './TestOutcome';
import { connectionNeedsSignIn, type IntegrationTestResult } from '../domain/setup';

export function MCPStep() {
  const service = useSetupService();
  const connections = useIntegrations();
  const test = useTestIntegration();
  const [results, setResults] = useState<Record<string, IntegrationTestResult>>({});
  const mcps = (connections.data ?? []).filter((item) => item.slug === 'mcp');
  return (
    <div className="setup-col" data-testid="setup-step-mcp">
      {connections.isLoading ? <p role="status">Loading MCP connections…</p> : null}
      {connections.error ? <p role="alert">Could not load MCP connections.</p> : null}
      {mcps.map((item) => (
        <div className="setup-card" key={item.id}>
          <h2>{String(item.config.name || item.config.mcp_url)}</h2>
          <p>{String(item.config.mcp_url)}</p>
          <p>{connectionNeedsSignIn(item) ? 'Sign-in needed' : 'Connected'}</p>
          <button
            type="button"
            className="setup-btn"
            disabled={test.isPending}
            onClick={() =>
              test.mutate(item.id, {
                onSuccess: (result) => setResults((current) => ({ ...current, [item.id]: result })),
              })
            }
          >
            Test connection
          </button>
          {results[item.id] ? <TestOutcome slug="mcp" result={results[item.id]!} /> : null}
        </div>
      ))}
      {test.error ? <p role="alert">Could not test the MCP connection.</p> : null}
      <div className="setup-card">
        <MCPConnectionForm
          connections={mcps}
          discover={(url) => service.discoverMCP(url)}
          connect={(input) => service.connectMCP(input)}
          onConnected={() => {
            void connections.refetch();
          }}
        />
      </div>
      <p className="setup-note">
        Optional. You can add or reconnect MCP servers later in Settings → Integrations.
      </p>
    </div>
  );
}
