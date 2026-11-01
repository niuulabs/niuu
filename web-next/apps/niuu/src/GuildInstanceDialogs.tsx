import { useState } from 'react';
import type { AppIdentity } from '@niuulabs/plugin-sdk';
import { Dialog, DialogContent } from '@niuulabs/ui';
import {
  isValidTlsFingerprint,
  parseTags,
  registryError,
  type InstanceRecord,
  type InstanceUpdate,
  type VisibilityScope,
} from './guildInstances';

export const guildActionClass =
  'niuu:inline-flex niuu:items-center niuu:justify-center niuu:gap-2 niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary niuu:hover:bg-bg-elevated niuu:focus-visible:outline-brand niuu:disabled:opacity-50';
const fieldClass =
  'niuu:w-full niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary niuu:focus:outline-brand';
const labelClass = 'niuu:grid niuu:gap-2 niuu:text-sm niuu:text-text-secondary';

type DialogState = {
  instance: InstanceRecord;
  pending: boolean;
  error: unknown;
  onClose: () => void;
};

export function EditInstanceDialog({
  instance,
  identity,
  pending,
  error,
  onClose,
  onSave,
}: DialogState & {
  identity: AppIdentity;
  onSave: (update: InstanceUpdate) => void;
}) {
  const [name, setName] = useState(instance.name);
  const [slug, setSlug] = useState(instance.slug);
  const [baseUrl, setBaseUrl] = useState(instance.baseUrl);
  const [folder, setFolder] = useState(String(instance.config.defaultFolder ?? ''));
  const [tags, setTags] = useState(instance.tags.join(', '));
  const [enabled, setEnabled] = useState(instance.enabled);
  const [isDefault, setIsDefault] = useState(instance.isDefault);
  const [visibility, setVisibility] = useState(instance.visibility);
  const [allowPlaintext, setAllowPlaintext] = useState(instance.config.allow_plaintext === true);
  const [tlsFingerprint, setTlsFingerprint] = useState(
    String(instance.config.tls_fingerprint ?? ''),
  );
  const [config, setConfig] = useState(() => {
    const extra = { ...instance.config };
    delete extra.defaultFolder;
    delete extra.allow_plaintext;
    delete extra.tls_fingerprint;
    return JSON.stringify(extra, null, 2);
  });
  const [validation, setValidation] = useState('');
  const close = () => {
    if (!pending) onClose();
  };

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (pending) return;
    try {
      if (!name.trim() || !slug.trim()) throw new Error('Name and routing slug are required.');
      const endpoint = new URL(baseUrl.trim());
      if (
        !['http:', 'https:'].includes(endpoint.protocol) ||
        endpoint.username ||
        endpoint.password ||
        endpoint.search ||
        endpoint.hash
      ) {
        throw new Error(
          'Use an HTTP or HTTPS server URL without credentials, a query, or a fragment.',
        );
      }
      const extra: unknown = JSON.parse(config);
      if (!extra || typeof extra !== 'object' || Array.isArray(extra))
        throw new Error('Advanced configuration must be a JSON object.');
      if ('defaultFolder' in extra || 'allow_plaintext' in extra || 'tls_fingerprint' in extra)
        throw new Error(
          'Set the default folder, plaintext, and TLS fingerprint in the fields above, ' +
            'not in advanced configuration.',
        );
      const trimmedFingerprint = tlsFingerprint.trim();
      if (!isValidTlsFingerprint(trimmedFingerprint))
        throw new Error(
          'TLS fingerprint must be a sha256 hex digest of the leaf certificate ' +
            '(64 hex characters, optionally colon-separated).',
        );
      const updatedConfig: Record<string, unknown> = { ...extra };
      if (folder.trim()) updatedConfig.defaultFolder = folder.trim();
      if (allowPlaintext) updatedConfig.allow_plaintext = true;
      if (trimmedFingerprint) updatedConfig.tls_fingerprint = trimmedFingerprint;
      setValidation('');
      onSave({
        name: name.trim(),
        slug: slug.trim(),
        baseUrl: baseUrl.trim().replace(/\/+$/, ''),
        enabled,
        isDefault,
        tags: parseTags(tags),
        config: updatedConfig,
        ...(visibility !== instance.visibility
          ? { visibility: visibility as VisibilityScope }
          : {}),
      });
    } catch (failure) {
      setValidation(
        failure instanceof TypeError
          ? 'Enter a valid HTTP or HTTPS server URL.'
          : registryError(failure),
      );
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogContent
        title="Edit node settings"
        description={`Update ${instance.name} in the Guild registry.`}
      >
        <form onSubmit={submit} className="niuu:space-y-4 niuu:text-text-primary">
          <fieldset disabled={pending} className="niuu:space-y-4">
            <label className={labelClass}>
              Name
              <input
                autoFocus
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                className={fieldClass}
              />
            </label>
            <label className={labelClass}>
              Server URL
              <input
                required
                type="url"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                className={fieldClass}
              />
              <span>Use the server address and port, without /volundr or ?config=…</span>
            </label>
            <label className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm">
              <input
                type="checkbox"
                className="niuu:appearance-auto niuu:accent-brand"
                checked={allowPlaintext}
                onChange={(e) => setAllowPlaintext(e.target.checked)}
              />
              Allow plaintext (trusted network only)
            </label>
            <p className="niuu:text-xs niuu:text-text-secondary">
              A remote node must use https:// unless you opt in here. Only opt in when the network
              path is already encrypted or otherwise trusted — for example a Tailscale tailnet,
              where WireGuard encrypts the link end to end. Never opt in over the open internet or
              an untrusted LAN.
            </p>
            <label className={labelClass}>
              TLS fingerprint (optional)
              <input
                value={tlsFingerprint}
                onChange={(e) => setTlsFingerprint(e.target.value)}
                placeholder="sha256 leaf certificate fingerprint, e.g. AB:CD:…"
                className={`${fieldClass} niuu:font-mono`}
              />
              <span>
                Pin a self-signed certificate by its sha256 fingerprint. Every call to this node
                then verifies the live certificate against this exact pin and refuses to connect on
                any mismatch — leave blank to use the platform&rsquo;s normal certificate trust.
              </span>
            </label>
            <label className={labelClass}>
              Default local folder
              <input
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
                className={fieldClass}
              />
            </label>
            <label className={labelClass}>
              Tags
              <input
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                className={fieldClass}
              />
            </label>
            <div className="niuu:flex niuu:flex-wrap niuu:gap-4 niuu:text-sm">
              <label className="niuu:flex niuu:items-center niuu:gap-2">
                <input
                  type="checkbox"
                  className="niuu:appearance-auto niuu:accent-brand"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                />
                Enabled
              </label>
              <label className="niuu:flex niuu:items-center niuu:gap-2">
                <input
                  type="checkbox"
                  className="niuu:appearance-auto niuu:accent-brand"
                  checked={isDefault}
                  onChange={(e) => setIsDefault(e.target.checked)}
                />
                Default node
              </label>
            </div>
            <details className="niuu:space-y-4">
              <summary className="niuu:cursor-pointer niuu:text-sm niuu:text-text-secondary">
                Advanced settings
              </summary>
              <label className={labelClass}>
                Routing slug
                <input
                  required
                  value={slug}
                  onChange={(e) => setSlug(e.target.value)}
                  className={fieldClass}
                />
                <span>Changing this changes the node’s proxy URLs.</span>
              </label>
              <label className={labelClass}>
                Visibility
                <select
                  value={visibility}
                  onChange={(e) => setVisibility(e.target.value)}
                  className={fieldClass}
                >
                  <option value="user">Only me</option>
                  <option value="tenant" disabled={!identity.tenantId}>
                    Tenant
                  </option>
                  <option value="system" disabled={!identity.roles.includes('volundr:admin')}>
                    System
                  </option>
                </select>
              </label>
              <label className={labelClass}>
                Configuration (JSON)
                <textarea
                  rows={8}
                  spellCheck={false}
                  value={config}
                  onChange={(e) => setConfig(e.target.value)}
                  className={`${fieldClass} niuu:font-mono`}
                />
                <span>
                  Transport, capabilities and credential references. Use stored credentials for
                  secrets.
                </span>
              </label>
            </details>
          </fieldset>
          {validation || error ? (
            <p role="alert" className="niuu:text-sm niuu:text-danger">
              {validation || registryError(error)}
            </p>
          ) : null}
          <div className="niuu:flex niuu:justify-end niuu:gap-3">
            <button type="button" onClick={close} disabled={pending} className={guildActionClass}>
              Cancel
            </button>
            <button type="submit" disabled={pending} className={guildActionClass}>
              {pending ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function DeleteInstanceDialog({
  instance,
  pending,
  error,
  onClose,
  onDelete,
}: DialogState & { onDelete: () => void }) {
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose();
      }}
    >
      <DialogContent
        title={`Delete ${instance.name}?`}
        description="This removes the node from Guild and the environment selectors. Its server and sessions keep running."
      >
        <p className="niuu:mb-4 niuu:break-all niuu:font-mono niuu:text-sm niuu:text-text-secondary">
          {instance.baseUrl}
        </p>
        {error ? (
          <p role="alert" className="niuu:mb-4 niuu:text-sm niuu:text-danger">
            {registryError(error)}
          </p>
        ) : null}
        <div className="niuu:flex niuu:justify-end niuu:gap-3">
          <button
            type="button"
            autoFocus
            onClick={onClose}
            disabled={pending}
            className={guildActionClass}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onDelete}
            disabled={pending}
            className={`${guildActionClass} niuu:text-danger`}
          >
            {pending ? 'Deleting…' : 'Delete node'}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
