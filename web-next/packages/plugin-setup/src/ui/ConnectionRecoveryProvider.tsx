import { useState, type ReactNode } from 'react';
import { ChatConnectionsContext, Dialog, DialogContent, MCPConnectionForm } from '@niuulabs/ui';
import {
  useCatalog,
  useConnectIntegration,
  useIntegrations,
  useOptionalSetupService,
  useTestIntegration,
} from './hooks';
import { SignInCard } from './SignInCard';
import { IntegrationCard } from './IntegrationCard';
import { connectionNeedsSignIn, signInOffered, isConnectableFromWizard } from '../domain/setup';

export function ConnectionRecoveryProvider({ children }: { children: ReactNode }) {
  const service = useOptionalSetupService();
  const [open, setOpen] = useState(false);
  if (!service) return children;
  return (
    <ChatConnectionsContext.Provider value={() => setOpen(true)}>
      {children}
      {open && <ConnectionRecoveryDialog onClose={() => setOpen(false)} />}
    </ChatConnectionsContext.Provider>
  );
}

function ConnectionRecoveryDialog({ onClose }: { onClose: () => void }) {
  const service = useOptionalSetupService()!;
  const catalog = useCatalog();
  const connections = useIntegrations();
  const connect = useConnectIntegration();
  const test = useTestIntegration();
  const [useKey, setUseKey] = useState(false);
  const [selectedId, setSelectedId] = useState('');
  const [saved, setSaved] = useState(false);
  const selected = connections.data?.find((item) => item.id === selectedId);
  const entry = catalog.data?.find((item) => item.slug === selected?.slug);
  const label = (item: NonNullable<typeof connections.data>[number]) =>
    String(
      item.config.name ||
        catalog.data?.find((provider) => provider.slug === item.slug)?.name ||
        item.slug,
    );
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent title="Reconnect an account" className="setup-dialog">
        <div className="setup-col">
          <p className="setup-note">
            Your connected accounts across Niuu. Choose the account that needs attention; your
            conversation stays open.
          </p>
          {catalog.isLoading || connections.isLoading ? (
            <p role="status">Loading accounts…</p>
          ) : null}
          {catalog.error || connections.error ? (
            <div role="alert">
              Could not load accounts.
              <button
                type="button"
                className="setup-btn"
                onClick={() => {
                  void catalog.refetch();
                  void connections.refetch();
                }}
              >
                Try again
              </button>
            </div>
          ) : null}
          {connections.data?.length === 0 ? (
            <p>No connected accounts. Add a provider in Settings → Integrations.</p>
          ) : null}
          <label className="setup-col">
            Account
            <select
              className="niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-primary niuu:p-2"
              value={selectedId}
              onChange={(event) => {
                setSelectedId(event.target.value);
                setSaved(false);
                setUseKey(false);
                test.reset();
                connect.reset();
              }}
            >
              <option value="">Choose an account</option>
              {connections.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {label(item)} ·{' '}
                  {item.slug === 'mcp' ? String(item.config.mcp_url) : item.credentialName}
                  {connectionNeedsSignIn(item) || item.credentialStatus === 'missing'
                    ? ' · Sign-in needed'
                    : ''}
                </option>
              ))}
            </select>
          </label>
          {selected && entry && signInOffered(entry) && isConnectableFromWizard(entry) ? (
            <div className="setup-form__actions">
              <button
                type="button"
                className="setup-btn"
                aria-pressed={!useKey}
                onClick={() => setUseKey(false)}
              >
                Sign in
              </button>
              <button
                type="button"
                className="setup-btn"
                aria-pressed={useKey}
                onClick={() => setUseKey(true)}
              >
                Replace API key
              </button>
            </div>
          ) : null}
          {selected?.slug === 'mcp' ? (
            <MCPConnectionForm
              key={selected.id}
              initialConnectionId={selected.id}
              connections={[selected]}
              discover={(url) => service.discoverMCP(url)}
              connect={(input) => service.connectMCP(input)}
              onConnected={() => {
                void connections.refetch();
              }}
            />
          ) : selected && entry ? (
            signInOffered(entry) && !useKey ? (
              <SignInCard
                key={selected.id}
                entry={entry}
                connection={selected}
                reconnect
                credentialName={selected.credentialName}
                oauthApp={String(selected.config.oauth_app ?? 'default')}
              />
            ) : (
              <IntegrationCard
                key={selected.id}
                entry={entry}
                connection={saved ? selected : undefined}
                credentialName={selected.credentialName}
                initialConfig={selected.config}
                connecting={connect.isPending}
                connectError={connect.error}
                testing={test.isPending}
                testResult={test.data}
                onTest={(id) => test.mutate(id)}
                onConnect={(input) =>
                  connect.mutate(
                    { ...input, connectionId: selected.id },
                    { onSuccess: () => setSaved(true) },
                  )
                }
              />
            )
          ) : selected ? (
            <p role="alert">This provider is unavailable in the current catalog.</p>
          ) : null}
          {saved ? <p role="status">Account updated.</p> : null}
          {test.error ? <p role="alert">Could not test the connection.</p> : null}
          <p className="setup-note">
            After sign-in, return to chat and retry. If the runtime still reports an authentication
            error, stop and start this session to load the updated credentials.
          </p>
          <button type="button" className="setup-btn" onClick={onClose}>
            Return to chat
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
