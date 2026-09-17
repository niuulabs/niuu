import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { LoadingState } from '@niuulabs/ui';
import type { IVolundrService } from '../ports/IVolundrService';
import type { ForgeHostInput, ForgeHostTest, VolundrTarget } from '../models/volundr.model';
import { hostDefaultFolder, normalizeForgeOrigin, forgeErrorMessage } from './quickLaunchModel';
import './QuickLaunch.css';

export function ForgeHostsPage() {
  const service = useService<IVolundrService>('volundr');
  const client = useQueryClient();
  const hosts = useQuery({
    queryKey: ['volundr', 'forge-hosts'],
    queryFn: () => service.getForgeHosts(),
  });
  const [editor, setEditor] = useState<ForgeHostInput | null>(null);
  const [error, setError] = useState('');
  const [checks, setChecks] = useState<Record<string, ForgeHostTest>>({});
  const save = useMutation({
    mutationFn: (host: ForgeHostInput) => service.saveForgeHost(host),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['volundr', 'forge-hosts'] }),
        client.invalidateQueries({ queryKey: ['volundr', 'targets'] }),
      ]);
      setEditor(null);
    },
  });
  const probe = useMutation({
    mutationFn: (id: string) => service.testForgeHost(id),
    onSuccess: (result, id) => setChecks((current) => ({ ...current, [id]: result })),
  });
  function edit(host?: VolundrTarget) {
    save.reset();
    setError('');
    setEditor(
      host
        ? {
            id: host.id,
            slug: host.slug,
            name: host.name,
            baseUrl: host.baseUrl,
            enabled: host.enabled,
            config: { ...host.config },
          }
        : { name: '', slug: '', baseUrl: '', enabled: true, config: {} },
    );
  }
  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!editor || save.isPending) return;
    try {
      const name = editor.name.trim();
      if (!name) throw new Error('Give this Forge a name.');
      const baseUrl = normalizeForgeOrigin(editor.baseUrl);
      if (hosts.data?.some((h) => h.id !== editor.id && h.baseUrl === baseUrl))
        throw new Error('This address is already registered. Edit the existing Forge.');
      const folder = String(editor.config.defaultFolder ?? '').trim();
      if (folder && !folder.startsWith('/'))
        throw new Error('Use an absolute default folder on this Forge.');
      const slug =
        editor.slug ||
        name
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, '-')
          .replace(/^-|-$/g, '');
      if (!slug) throw new Error('Use letters or numbers in the Forge name.');
      setError('');
      save.mutate({
        ...editor,
        name,
        slug,
        baseUrl,
        config: { ...editor.config, defaultFolder: folder },
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save Forge.');
    }
  }
  return (
    <div className="vol-quick vol-quick-page" data-testid="forge-hosts-page">
      <header>
        <h1>Forge Hosts</h1>
        <p>
          Choose the computer where your sessions run. Names are labels; the address identifies the
          Forge.
        </p>
      </header>
      <p>
        These connections are saved in the shared Niuu registry, also visible in Guild. Local
        addresses refer to the Niuu server, not this browser.
      </p>
      <div className="vol-quick__actions">
        <a className="vol-quick__link" href="/volundr/catalog">
          Quick launch
        </a>
        <button className="vol-quick__button" onClick={() => edit()}>
          Add Forge
        </button>
      </div>
      {hosts.isLoading && <LoadingState label="Loading Forge hosts…" />}
      {hosts.error && <p role="alert">{forgeErrorMessage(hosts.error)}</p>}
      {hosts.data?.length === 0 && <p>No Forge hosts configured. Add a host to start a session.</p>}
      {hosts.data?.map((host) => (
        <section className="vol-quick__section" key={host.id}>
          <div className="vol-quick__host">
            <div>
              <h2>
                {host.name}
                {host.isDefault ? ' · Default' : ''}
                {!host.enabled ? ' · Disabled' : ''}
              </h2>
              <code>{host.baseUrl}</code>
              <p>Default folder: {hostDefaultFolder(host) || 'Choose at launch'}</p>
            </div>
            <div className="vol-quick__actions">
              <button className="vol-quick__button" onClick={() => edit(host)}>
                Edit {host.name}
              </button>
              <button
                className="vol-quick__button"
                disabled={probe.isPending}
                onClick={() => probe.mutate(host.id)}
              >
                Test {host.name}
              </button>
            </div>
          </div>
          {checks[host.id] && (
            <p role={checks[host.id]!.ok ? 'status' : 'alert'}>{checks[host.id]!.message}</p>
          )}
        </section>
      ))}
      {probe.error && <p role="alert">{forgeErrorMessage(probe.error)}</p>}
      {editor && (
        <form className="vol-quick__section" onSubmit={submit} aria-label="Forge connection">
          <h2>{editor.id ? 'Edit Forge' : 'Add Forge'}</h2>
          <div className="vol-quick__row">
            <label>
              Name
              <input
                autoFocus
                value={editor.name}
                onChange={(e) => setEditor({ ...editor, name: e.target.value })}
              />
            </label>
            <label>
              IP address or URL
              <input
                value={editor.baseUrl}
                placeholder="100.x.x.x:8080"
                onChange={(e) => setEditor({ ...editor, baseUrl: e.target.value })}
              />
            </label>
          </div>
          <label>
            Default folder
            <input
              value={String(editor.config.defaultFolder ?? '')}
              placeholder="Absolute folder path on this host"
              onChange={(e) =>
                setEditor({
                  ...editor,
                  config: { ...editor.config, defaultFolder: e.target.value },
                })
              }
            />
          </label>
          <label>
            Availability
            <select
              value={editor.enabled ? 'enabled' : 'disabled'}
              onChange={(e) => setEditor({ ...editor, enabled: e.target.value === 'enabled' })}
            >
              <option value="enabled">Enabled</option>
              <option value="disabled">Disabled</option>
            </select>
          </label>
          <small>
            HTTP hosts are reached by Niuu. Their session sockets also need an HTTPS proxy on the
            web server; the configured Tailscale hosts already have one. For other hosts, use an
            HTTPS Forge URL or configure its proxy.
          </small>
          {(error || save.error) && <p role="alert">{error || forgeErrorMessage(save.error)}</p>}
          <div className="vol-quick__actions">
            <button
              type="button"
              className="vol-quick__button"
              disabled={save.isPending}
              onClick={() => setEditor(null)}
            >
              Cancel
            </button>
            <button className="vol-quick__button vol-quick__primary" disabled={save.isPending}>
              {save.isPending ? 'Saving…' : 'Save Forge'}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
