import { useState } from 'react';

interface Discovery {
  issuer: string;
  resource: string;
  scope: string;
  registration_available: boolean;
}

export interface MCPConnectionInput {
  server_url: string;
  name: string;
  connection_id?: string;
  client_id?: string;
  client_secret?: string;
  token_endpoint_auth_method?: string;
  api_token?: string;
  auth_header?: string;
  auth_prefix?: string;
}

export function MCPConnectionForm({
  discover,
  connect,
  onConnected,
  connections,
}: {
  discover: (serverUrl: string) => Promise<Discovery>;
  connect: (input: MCPConnectionInput) => Promise<{ url?: string; connection_id?: string }>;
  onConnected: () => void;
  connections: { id: string; config: Record<string, unknown> }[];
}) {
  const [connectionId, setConnectionId] = useState('');
  const [serverUrl, setServerUrl] = useState('');
  const [name, setName] = useState('');
  const [mode, setMode] = useState('oauth');
  const [clientId, setClientId] = useState('');
  const [clientSecret, setClientSecret] = useState('');
  const [method, setMethod] = useState('none');
  const [token, setToken] = useState('');
  const [header, setHeader] = useState('Authorization');
  const [prefix, setPrefix] = useState('Bearer ');
  const [metadata, setMetadata] = useState<Discovery | null>(null);
  const [authorizationUrl, setAuthorizationUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [connected, setConnected] = useState(false);

  async function submit() {
    setError('');
    setBusy(true);
    try {
      if (mode === 'oauth' && !metadata) {
        setMetadata(await discover(serverUrl));
        return;
      }
      const result = await connect({
        server_url: serverUrl,
        name: name || serverUrl,
        connection_id: connectionId,
        ...(mode === 'oauth'
          ? { client_id: clientId, client_secret: clientSecret, token_endpoint_auth_method: method }
          : { api_token: token, auth_header: header, auth_prefix: prefix }),
      });
      setToken('');
      setClientSecret('');
      if (result.url) {
        setAuthorizationUrl(result.url);
        return;
      }
      setConnected(true);
      onConnected();
    } catch {
      setError('Could not connect. Check the server URL and authentication settings.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="settings-resource__composer settings-resource__composer--stacked"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <h3 className="settings-resource__title">Connect an MCP server</h3>
      <label className="settings-field">
        Connection
        <select
          className="settings-field__control"
          value={connectionId}
          onChange={(event) => {
            const selected = connections.find((item) => item.id === event.target.value);
            setConnectionId(event.target.value);
            setServerUrl(String(selected?.config.mcp_url ?? ''));
            setName(String(selected?.config.name ?? ''));
            setMetadata(null);
            setAuthorizationUrl('');
            setConnected(false);
          }}
        >
          <option value="">New MCP connection</option>
          {connections.map((item) => (
            <option value={item.id} key={item.id}>
              Reconnect {String(item.config.name || item.config.mcp_url)}
            </option>
          ))}
        </select>
      </label>
      <label className="settings-field">
        Server URL
        <input
          className="settings-field__control"
          type="url"
          required
          value={serverUrl}
          disabled={!!connectionId}
          list="mcp-server-suggestions"
          onChange={(event) => {
            setServerUrl(event.target.value);
            setMetadata(null);
            setAuthorizationUrl('');
            setConnected(false);
          }}
        />
      </label>
      <datalist id="mcp-server-suggestions">
        <option value="https://api.githubcopilot.com/mcp/">GitHub MCP</option>
        <option value="https://gitlab.com/api/v4/mcp">GitLab MCP</option>
        <option value="https://mcp.linear.app/mcp">Linear MCP</option>
      </datalist>
      <label className="settings-field">
        Name
        <input
          className="settings-field__control"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label className="settings-field">
        Authentication
        <select
          className="settings-field__control"
          value={mode}
          onChange={(event) => {
            setMode(event.target.value);
            setAuthorizationUrl('');
          }}
        >
          <option value="oauth">Sign in with OAuth</option>
          <option value="token">API token</option>
        </select>
      </label>
      {mode === 'oauth' && metadata ? (
        <>
          <p>
            Sign in through {metadata.issuer}
            {metadata.scope ? ` · Permissions: ${metadata.scope}` : ''}
          </p>
          <details open={!metadata.registration_available}>
            <summary>Registered OAuth application</summary>
            <label className="settings-field">
              Client ID
              <input
                className="settings-field__control"
                value={clientId}
                required={!metadata.registration_available}
                onChange={(event) => setClientId(event.target.value)}
              />
            </label>
            <label className="settings-field">
              Client authentication
              <select
                className="settings-field__control"
                value={method}
                onChange={(event) => setMethod(event.target.value)}
              >
                <option value="none">Public client (PKCE)</option>
                <option value="client_secret_post">Client secret in request body</option>
                <option value="client_secret_basic">HTTP Basic client secret</option>
              </select>
            </label>
            {method !== 'none' ? (
              <label className="settings-field">
                Client secret
                <input
                  className="settings-field__control"
                  type="password"
                  autoComplete="off"
                  value={clientSecret}
                  onChange={(event) => setClientSecret(event.target.value)}
                />
              </label>
            ) : null}
          </details>
        </>
      ) : null}
      {mode === 'token' ? (
        <>
          <label className="settings-field">
            API token
            <input
              className="settings-field__control"
              type="password"
              autoComplete="off"
              required
              value={token}
              onChange={(event) => setToken(event.target.value)}
            />
          </label>
          <details>
            <summary>Authentication header</summary>
            <label className="settings-field">
              Header name
              <input
                className="settings-field__control"
                value={header}
                onChange={(event) => setHeader(event.target.value)}
              />
            </label>
            <label className="settings-field">
              Token prefix
              <input
                className="settings-field__control"
                value={prefix}
                onChange={(event) => setPrefix(event.target.value)}
              />
            </label>
          </details>
        </>
      ) : null}
      {authorizationUrl ? (
        <div>
          <a href={authorizationUrl} target="_blank" rel="noopener noreferrer">
            Continue to sign in
          </a>
          <button className="settings-resource__row-action" type="button" onClick={onConnected}>
            Refresh connections after signing in
          </button>
        </div>
      ) : (
        <button className="settings-resource__row-action" disabled={busy} type="submit">
          {busy
            ? 'Connecting…'
            : mode === 'oauth' && !metadata
              ? 'Discover authentication'
              : 'Connect MCP server'}
        </button>
      )}
      {error ? <p role="alert">{error}</p> : null}
      {connected ? <p role="status">MCP server connected</p> : null}
    </form>
  );
}
