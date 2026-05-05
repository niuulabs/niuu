import { useMemo, useState } from 'react';
import { EmptyState, ErrorState, LoadingState, cn } from '@niuulabs/ui';
import { useService } from '@niuulabs/plugin-sdk';
import { useQuery } from '@tanstack/react-query';
import type { IVolundrService } from '../ports/IVolundrService';
import type { SecretType, StoredCredential } from '../models/volundr.model';
import './CredentialsPage.css';

const SECRET_TYPE_ORDER: SecretType[] = [
  'api_key',
  'git_credential',
  'oauth_token',
  'ssh_key',
  'tls_cert',
  'generic',
];

const TYPE_LABEL: Record<SecretType, string> = {
  api_key: 'api key',
  git_credential: 'git credential',
  oauth_token: 'oauth token',
  ssh_key: 'ssh key',
  tls_cert: 'tls cert',
  generic: 'generic',
};

function keyCountLabel(keys: string[]) {
  return `${keys.length}k`;
}

function RotateIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" className="vol-creds__icon">
      <path
        d="M13 5.5V2.5M13 2.5H10M13 2.5L9.8 5.7M12.7 8a4.7 4.7 0 1 1-1.4-3.3"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.4"
      />
    </svg>
  );
}

function CopyIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" className="vol-creds__icon">
      <rect
        x="5.4"
        y="3.2"
        width="7.2"
        height="9"
        rx="1.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
      />
      <path
        d="M3.8 10.8H3.2A1.2 1.2 0 0 1 2 9.6V3.2A1.2 1.2 0 0 1 3.2 2h6.4a1.2 1.2 0 0 1 1.2 1.2v.6"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="1.4"
      />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" className="vol-creds__icon">
      <path
        d="M2.8 4.2h10.4M6.2 2.6h3.6M5 4.2v8.2m3-8.2v8.2m3-8.2v8.2M4.4 4.2l.4 8.5c.04.7.62 1.3 1.32 1.3h3.8c.7 0 1.28-.55 1.32-1.3l.4-8.5"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.4"
      />
    </svg>
  );
}

function sortCredentials(rows: StoredCredential[]) {
  return [...rows].sort((left, right) => {
    const typeDelta =
      SECRET_TYPE_ORDER.indexOf(left.secretType) - SECRET_TYPE_ORDER.indexOf(right.secretType);
    if (typeDelta !== 0) return typeDelta;
    return left.name.localeCompare(right.name);
  });
}

function groupCredentials(rows: StoredCredential[]) {
  const groups = new Map<SecretType, StoredCredential[]>();

  for (const row of rows) {
    const group = groups.get(row.secretType) ?? [];
    group.push(row);
    groups.set(row.secretType, group);
  }

  return SECRET_TYPE_ORDER.map((type) => ({
    type,
    label: TYPE_LABEL[type],
    items: groups.get(type) ?? [],
  })).filter((group) => group.items.length > 0);
}

export function CredentialsPage() {
  const service = useService<IVolundrService>('volundr');
  const credentials = useQuery({
    queryKey: ['volundr', 'credentials'],
    queryFn: () => service.getCredentials(),
  });
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [selectedCredential, setSelectedCredential] = useState<string | null>(null);

  const rows = useMemo(() => sortCredentials(credentials.data ?? []), [credentials.data]);
  const grouped = useMemo(() => groupCredentials(rows), [rows]);

  return (
    <div className="vol-creds" data-testid="credentials-page">
      <aside
        className={cn('vol-creds__sidebar', sidebarCollapsed && 'vol-creds__sidebar--collapsed')}
        aria-label="Credentials by type"
        data-testid="credentials-sidebar"
      >
        {sidebarCollapsed ? (
          <div className="vol-creds__collapsed">
            <div className="vol-creds__collapsed-head">
              <button
                type="button"
                onClick={() => setSidebarCollapsed(false)}
                className="vol-creds__toggle"
                aria-label="Expand credentials sidebar"
              >
                ›
              </button>
            </div>
            <div className="vol-creds__collapsed-body">
              {grouped.map((group) => (
                <div key={group.type} className="vol-creds__collapsed-group">
                  {group.items.map((credential) => (
                    <button
                      key={credential.id}
                      type="button"
                      className={cn(
                        'vol-creds__collapsed-item',
                        selectedCredential === credential.id &&
                          'vol-creds__collapsed-item--selected',
                      )}
                      onClick={() => setSelectedCredential(credential.id)}
                      aria-label={credential.name}
                    >
                      <span className="vol-creds__dot" />
                    </button>
                  ))}
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="vol-creds__expanded">
            <div className="vol-creds__sidebar-head">
              <div className="vol-creds__sidebar-copy">
                <div className="vol-creds__sidebar-title-row">
                  <div>
                    <h1 className="vol-creds__sidebar-title">Credentials</h1>
                    <p className="vol-creds__sidebar-subtitle">mounted into pods on boot</p>
                  </div>
                  <span className="vol-creds__sidebar-count">{rows.length}</span>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setSidebarCollapsed(true)}
                className="vol-creds__toggle"
                aria-label="Collapse credentials sidebar"
              >
                ‹
              </button>
            </div>

            <div className="vol-creds__sidebar-body">
              {grouped.map((group) => (
                <section key={group.type} className="vol-creds__group">
                  <header className="vol-creds__group-head">
                    <span>{group.label}</span>
                    <span className="vol-creds__group-count">{group.items.length}</span>
                  </header>
                  <div className="vol-creds__group-body">
                    {group.items.map((credential) => (
                      <button
                        key={credential.id}
                        type="button"
                        className={cn(
                          'vol-creds__sidebar-item',
                          selectedCredential === credential.id &&
                            'vol-creds__sidebar-item--selected',
                        )}
                        onClick={() => setSelectedCredential(credential.id)}
                      >
                        <span className="vol-creds__dot" />
                        <span className="vol-creds__sidebar-item-name">{credential.name}</span>
                        <span className="vol-creds__sidebar-item-count">
                          {keyCountLabel(credential.keys)}
                        </span>
                      </button>
                    ))}
                  </div>
                </section>
              ))}
            </div>
          </div>
        )}
      </aside>

      <section className="vol-creds__detail">
        <header className="vol-creds__head">
          <div>
            <h2 className="vol-creds__title">Credentials</h2>
            <p className="vol-creds__subtitle">
              Secrets injected into pods as env vars or mounted files. Rotated centrally.
            </p>
          </div>
          <button
            className="vol-creds__primary-btn"
            aria-label="New credential"
            data-testid="new-credential-btn"
            type="button"
          >
            <span className="vol-creds__primary-plus">+</span>
            new credential
          </button>
        </header>

        {credentials.isLoading && (
          <div className="vol-creds__state" data-testid="credentials-loading">
            <LoadingState label="Loading credentials…" />
          </div>
        )}

        {credentials.isError && (
          <div className="vol-creds__state" data-testid="credentials-error">
            <ErrorState title="Failed to load credentials" message="Please retry in a moment." />
          </div>
        )}

        {!credentials.isLoading && !credentials.isError && rows.length === 0 && (
          <div className="vol-creds__state" data-testid="no-credentials">
            <EmptyState
              title="No credentials stored yet"
              description="Add a credential to inject secrets into pods at boot."
            />
          </div>
        )}

        {!credentials.isLoading && !credentials.isError && rows.length > 0 && (
          <div className="vol-creds__table-wrap" data-testid="credentials-table">
            <table className="vol-creds__table" aria-label="Credentials">
              <thead>
                <tr>
                  <th>name</th>
                  <th>type</th>
                  <th>keys</th>
                  <th>scope</th>
                  <th className="vol-creds__num">used</th>
                  <th>updated</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((credential) => (
                  <tr
                    key={credential.id}
                    className={cn(
                      selectedCredential === credential.id && 'vol-creds__row--selected',
                    )}
                  >
                    <td>
                      <div className="vol-creds__namecell">
                        <span className="vol-creds__dot" />
                        <span className="vol-creds__name">{credential.name}</span>
                      </div>
                    </td>
                    <td>
                      <span className="vol-creds__type-pill">
                        {TYPE_LABEL[credential.secretType]}
                      </span>
                    </td>
                    <td>
                      <div className="vol-creds__keys">
                        {credential.keys.map((key) => (
                          <code key={key} className="vol-creds__key-chip">
                            {key}
                          </code>
                        ))}
                      </div>
                    </td>
                    <td>
                      <span className="vol-creds__scope">{credential.scope ?? 'global'}</span>
                    </td>
                    <td className="vol-creds__num">{credential.used ?? 0}</td>
                    <td>
                      <span className="vol-creds__updated">{credential.updatedAt}</span>
                    </td>
                    <td>
                      <div className="vol-creds__actions">
                        <button type="button" title="rotate" aria-label="rotate credential">
                          <RotateIcon />
                        </button>
                        <button type="button" title="copy" aria-label="copy credential">
                          <CopyIcon />
                        </button>
                        <button
                          type="button"
                          title="delete"
                          aria-label="delete credential"
                          className="vol-creds__action-btn--danger"
                        >
                          <TrashIcon />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
