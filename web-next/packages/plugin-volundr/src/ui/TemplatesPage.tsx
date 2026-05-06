import { useState } from 'react';
import { ErrorState, LoadingState } from '@niuulabs/ui';
import type { Mount, McpServer } from '../domain/pod';
import type { Template } from '../domain/template';
import { useTemplates } from './useTemplates';
import { CliBadge } from './atoms';

const TABS = ['overview', 'workspace', 'runtime', 'mcp', 'skills', 'rules'] as const;
type TabId = (typeof TABS)[number];
const SHOWCASE_TEMPLATE_ORDER = [
  'niuu-platform',
  'volundr-web',
  'bifrost-gateway',
  'mimir-embeddings',
  'scratch',
  'local-laptop',
] as const;

function deriveCli(template: Template): string {
  if (template.name.includes('bifrost')) return 'codex';
  if (template.name.includes('gpu')) return 'codex';
  if (template.name.includes('web')) return 'aider';
  return 'claude';
}

function isDefaultTemplate(template: Template): boolean {
  return template.name === 'niuu-platform' || template.name === 'default';
}

function getDisplayTemplates(templates: Template[]): Template[] {
  const showcase = SHOWCASE_TEMPLATE_ORDER.map((name) =>
    templates.find((template) => template.name === name),
  ).filter((template): template is Template => Boolean(template));

  return showcase.length >= 4 ? showcase : templates;
}

function formatMountSummary(mount: Mount): string {
  switch (mount.source.kind) {
    case 'git':
      return `niuu/${mount.source.repo.split('/').at(-1)} @${mount.source.branch}`;
    case 'pvc':
      return `pvc:${mount.source.name}`;
    case 'secret':
      return `secret:${mount.source.name}`;
    case 'configmap':
      return `configmap:${mount.source.name}`;
  }
}

function formatMountMeta(mount: Mount): string {
  switch (mount.source.kind) {
    case 'git':
      return 'shallow clone · depth 50';
    case 'pvc':
      return 'persistent · local mount';
    case 'secret':
      return 'secret mount';
    case 'configmap':
      return 'config map mount';
  }
}

function RailRow({
  template,
  selected,
  onSelect,
}: {
  template: Template;
  selected: boolean;
  onSelect: (template: Template) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(template)}
      aria-pressed={selected}
      data-testid="template-card"
      className={`niuu-flex niuu-w-full niuu-items-center niuu-gap-3 niuu-rounded-sm niuu-border niuu-px-3 niuu-py-2 niuu-text-left niuu-transition-colors ${
        selected
          ? 'niuu-border-brand niuu-bg-bg-tertiary'
          : 'niuu-border-transparent niuu-bg-transparent hover:niuu-border-border-subtle hover:niuu-bg-bg-secondary'
      }`}
    >
      <span
        className="niuu-flex niuu-h-6 niuu-w-6 niuu-items-center niuu-justify-center niuu-rounded-sm niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-font-mono niuu-text-[11px] niuu-text-text-secondary"
        aria-hidden
      >
        {template.name[0]?.toUpperCase()}
      </span>
      <span className="niuu-min-w-0 niuu-flex-1">
        <span className="niuu-flex niuu-items-center niuu-gap-2">
          <span className="niuu-truncate niuu-font-mono niuu-text-xs niuu-font-medium niuu-text-text-primary">
            {template.name}
          </span>
          {isDefaultTemplate(template) && (
            <span className="niuu-rounded-sm niuu-border niuu-border-border niuu-bg-bg-secondary niuu-px-1 niuu-py-0.5 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-text-text-muted">
              default
            </span>
          )}
          {template.spec.resources.gpuCount > 0 && (
            <span className="niuu-rounded-sm niuu-border niuu-border-brand/50 niuu-bg-bg-secondary niuu-px-1 niuu-py-0.5 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-text-brand">
              GPU
            </span>
          )}
        </span>
      </span>
    </button>
  );
}

function DetailCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section
      className="niuu-rounded-sm niuu-border niuu-border-border-subtle niuu-bg-bg-secondary"
      data-testid="detail-card"
    >
      <div className="niuu-border-b niuu-border-border-subtle niuu-px-4 niuu-py-2">
        <h4 className="niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-muted">
          {title}
        </h4>
      </div>
      <div className="niuu-flex niuu-flex-col niuu-gap-1 niuu-px-4 niuu-py-3">{children}</div>
    </section>
  );
}

function KV({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="niuu-flex niuu-items-start niuu-gap-3 niuu-py-1">
      <span className="niuu-w-24 niuu-shrink-0 niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-text-text-faint">
        {label}
      </span>
      <span className="niuu-min-w-0 niuu-flex-1 niuu-font-mono niuu-text-xs niuu-text-text-secondary">
        {children}
      </span>
    </div>
  );
}

function TplOverview({ template }: { template: Template }) {
  const cli = deriveCli(template);
  return (
    <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3" data-testid="tab-overview">
      <DetailCard title="CLI & model">
        <KV label="cli">
          <span>{cli}</span>
        </KV>
        <KV label="model">
          <span>{template.spec.tag}</span>
        </KV>
      </DetailCard>

      <DetailCard title="Resources">
        <KV label="cpu">
          <span>
            {template.spec.resources.cpuRequest}-{template.spec.resources.cpuLimit} cores
          </span>
        </KV>
        <KV label="mem">
          <span>
            {template.spec.resources.memRequestMi}-{template.spec.resources.memLimitMi} Mi
          </span>
        </KV>
        <KV label="gpu">
          <span>
            {template.spec.resources.gpuCount > 0 ? `${template.spec.resources.gpuCount} gpu` : '—'}
          </span>
        </KV>
      </DetailCard>

      <DetailCard title="Workspace">
        {template.spec.mounts.length === 0 ? (
          <span className="niuu-font-mono niuu-text-xs niuu-text-text-faint">
            blank · no sources
          </span>
        ) : (
          template.spec.mounts.map((mount) => (
            <div key={mount.name} className="niuu-flex niuu-items-center niuu-gap-2 niuu-py-1">
              <span className="niuu-font-mono niuu-text-text-muted" aria-hidden>
                {mount.source.kind === 'git' ? '❯' : '⌂'}
              </span>
              <span className="niuu-font-mono niuu-text-xs niuu-text-text-secondary">
                {mount.name}
              </span>
            </div>
          ))
        )}
      </DetailCard>

      <DetailCard title="Extensions">
        <KV label="mcp">
          <span>
            {(template.spec.mcpServers ?? []).map((server) => server.name).join(' · ') || '—'}
          </span>
        </KV>
        <KV label="skills">
          <span>{template.spec.tools.length}</span>
        </KV>
        <KV label="rules">
          <span>
            {(template.spec.clusterAffinity?.length ?? 0) +
              (template.spec.tolerations?.length ?? 0)}
          </span>
        </KV>
      </DetailCard>
    </div>
  );
}

function TplWorkspace({ template }: { template: Template }) {
  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-3" data-testid="tab-workspace">
      <div className="niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-muted">
        Workspace sources
      </div>
      <div className="niuu-flex niuu-flex-col niuu-gap-2">
        {template.spec.mounts.length === 0 ? (
          <div className="niuu-rounded-sm niuu-border niuu-border-dashed niuu-border-border-subtle niuu-p-4 niuu-font-mono niuu-text-xs niuu-text-text-faint">
            blank · no sources
          </div>
        ) : (
          template.spec.mounts.map((mount) => (
            <div
              key={mount.name}
              className="niuu-flex niuu-items-center niuu-gap-3 niuu-rounded-sm niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-4 niuu-py-3"
            >
              <span className="niuu-font-mono niuu-text-text-muted" aria-hidden>
                {mount.source.kind === 'git' ? '❯' : '⌂'}
              </span>
              <span className="niuu-font-mono niuu-text-xs niuu-text-text-primary">
                {mount.name}
              </span>
              <span className="niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
                {formatMountSummary(mount)}
              </span>
              <span className="niuu-font-mono niuu-text-[11px] niuu-text-text-faint">
                · {formatMountMeta(mount)}
              </span>
              <span className="niuu-ml-auto niuu-font-mono niuu-text-[11px] niuu-text-text-faint">
                {mount.readOnly ? 'read-only' : 'read-write'}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function TplRuntime({ template }: { template: Template }) {
  return (
    <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3" data-testid="tab-runtime">
      <DetailCard title="Image">
        <KV label="base">
          <span>{template.spec.image}</span>
        </KV>
        <KV label="tag">
          <span>{template.spec.tag}</span>
        </KV>
        <KV label="tools">
          <span>{template.spec.tools.join(' · ') || '—'}</span>
        </KV>
        <KV label="shell">
          <span>bash · zsh</span>
        </KV>
      </DetailCard>

      <DetailCard title="Lifecycle">
        <KV label="TTL">
          <span>{Math.round(template.spec.ttlSec / 60)}m</span>
        </KV>
        <KV label="idle timeout">
          <span>{Math.round(template.spec.idleTimeoutSec / 60)}m</span>
        </KV>
        <KV label="auto-archive">
          <span>7d</span>
        </KV>
        <KV label="post-boot">
          <span className="niuu-text-text-faint">—</span>
        </KV>
      </DetailCard>
    </div>
  );
}

function McpRow({ server }: { server: McpServer }) {
  return (
    <div
      className="niuu-grid niuu-grid-cols-[auto_120px_1fr_72px] niuu-items-center niuu-gap-3 niuu-rounded-sm niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-4 niuu-py-3"
      data-testid="mcp-server-card"
    >
      <span className="niuu-h-2 niuu-w-2 niuu-rounded-full niuu-bg-brand" aria-hidden />
      <span className="niuu-font-mono niuu-text-xs niuu-text-text-primary">{server.name}</span>
      <span className="niuu-text-xs niuu-text-text-secondary">{server.connectionString}</span>
      <span className="niuu-justify-self-end niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-text-text-faint">
        {server.transport}
      </span>
    </div>
  );
}

function TplMcp({ template }: { template: Template }) {
  const servers = template.spec.mcpServers ?? [];
  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-3" data-testid="tab-mcp">
      <div className="niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-muted">
        MCP servers
      </div>
      {servers.length === 0 ? (
        <div className="niuu-rounded-sm niuu-border niuu-border-dashed niuu-border-border-subtle niuu-p-4 niuu-font-mono niuu-text-xs niuu-text-text-faint">
          no MCP servers enabled
        </div>
      ) : (
        <div className="niuu-flex niuu-flex-col niuu-gap-2">
          {servers.map((server) => (
            <McpRow key={server.name} server={server} />
          ))}
        </div>
      )}
    </div>
  );
}

function TplSkills({ template }: { template: Template }) {
  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-3" data-testid="tab-skills">
      <div className="niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-muted">
        Skills ({template.spec.tools.length})
      </div>
      {template.spec.tools.length === 0 ? (
        <div className="niuu-rounded-sm niuu-border niuu-border-dashed niuu-border-border-subtle niuu-p-4 niuu-font-mono niuu-text-xs niuu-text-text-faint">
          no skills defined
        </div>
      ) : (
        <div className="niuu-rounded-sm niuu-border niuu-border-border-subtle niuu-bg-bg-secondary niuu-px-4 niuu-py-3 niuu-font-mono niuu-text-xs niuu-text-text-secondary">
          {template.spec.tools.join(' · ')}
        </div>
      )}
    </div>
  );
}

function TplRules({ template }: { template: Template }) {
  const affinity = template.spec.clusterAffinity ?? [];
  const tolerations = template.spec.tolerations ?? [];
  const hasRules = affinity.length > 0 || tolerations.length > 0;

  return (
    <div className="niuu-flex niuu-flex-col niuu-gap-3" data-testid="tab-rules">
      <div className="niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-muted">
        Rules & constraints
      </div>
      {!hasRules ? (
        <div className="niuu-rounded-sm niuu-border niuu-border-dashed niuu-border-border-subtle niuu-p-4 niuu-font-mono niuu-text-xs niuu-text-text-faint">
          no rules or constraints defined
        </div>
      ) : (
        <div className="niuu-grid niuu-grid-cols-2 niuu-gap-3">
          {affinity.length > 0 && (
            <DetailCard title="Cluster affinity">
              {affinity.map((cluster) => (
                <div
                  key={cluster}
                  className="niuu-py-1 niuu-font-mono niuu-text-xs niuu-text-text-secondary"
                >
                  {cluster}
                </div>
              ))}
            </DetailCard>
          )}
          {tolerations.length > 0 && (
            <DetailCard title="Tolerations">
              {tolerations.map((rule) => (
                <div
                  key={rule}
                  className="niuu-py-1 niuu-font-mono niuu-text-xs niuu-text-text-secondary"
                >
                  {rule}
                </div>
              ))}
            </DetailCard>
          )}
        </div>
      )}
    </div>
  );
}

export function TemplatesPage() {
  const templates = useTemplates();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>('overview');
  const displayTemplates = templates.data ? getDisplayTemplates(templates.data) : [];

  const selectedTemplate =
    displayTemplates.find((template) => template.id === selectedId) ??
    displayTemplates.find((template) => template.name === 'bifrost-gateway') ??
    displayTemplates[0] ??
    null;

  function handleSelect(template: Template) {
    setSelectedId(template.id);
    setTab('overview');
  }

  return (
    <div
      className="niuu-flex niuu-min-h-0 niuu-flex-1 niuu-flex-col niuu-p-5"
      data-testid="templates-page"
    >
      {templates.isLoading && <LoadingState label="loading templates…" />}

      {templates.isError && (
        <ErrorState
          title="Failed to load templates"
          message={
            templates.error instanceof Error ? templates.error.message : 'failed to load templates'
          }
        />
      )}

      {templates.data && displayTemplates.length === 0 && (
        <p className="niuu-font-mono niuu-text-xs niuu-text-text-muted" data-testid="empty-state">
          No templates yet — create one to get started.
        </p>
      )}

      {templates.data && displayTemplates.length > 0 && selectedTemplate && (
        <div className="niuu-flex niuu-min-h-0 niuu-flex-1 niuu-gap-6">
          <aside className="niuu-flex niuu-min-h-0 niuu-w-[272px] niuu-shrink-0 niuu-flex-col niuu-border-r niuu-border-border-subtle niuu-pr-4">
            <div className="niuu-flex niuu-items-baseline niuu-justify-between niuu-gap-2">
              <h2 className="niuu-text-lg niuu-font-semibold niuu-text-text-primary">Templates</h2>
              <span className="niuu-font-mono niuu-text-xs niuu-text-text-faint">
                {displayTemplates.length}
              </span>
            </div>
            <p className="niuu-mt-1 niuu-font-mono niuu-text-[11px] niuu-text-text-muted">
              workspace + runtime bundles
            </p>
            <div className="niuu-mt-5 niuu-flex niuu-items-center niuu-justify-between">
              <div className="niuu-font-mono niuu-text-[11px] niuu-uppercase niuu-tracking-[0.18em] niuu-text-text-faint">
                Built-in
              </div>
              <span className="niuu-font-mono niuu-text-[11px] niuu-text-text-faint">
                {displayTemplates.length}
              </span>
            </div>
            <div
              className="niuu-mt-2 niuu-flex niuu-flex-col niuu-gap-1"
              role="list"
              aria-label="Pod templates"
            >
              {displayTemplates.map((template) => (
                <div key={template.id} role="listitem">
                  <RailRow
                    template={template}
                    selected={selectedTemplate.id === template.id}
                    onSelect={handleSelect}
                  />
                </div>
              ))}
            </div>
          </aside>

          <section className="niuu-flex niuu-min-h-0 niuu-min-w-0 niuu-flex-1 niuu-flex-col">
            <header className="niuu-flex niuu-items-start niuu-justify-between niuu-gap-4 niuu-border-b niuu-border-border-subtle niuu-pb-4">
              <div className="niuu-min-w-0">
                <div className="niuu-flex niuu-items-center niuu-gap-2">
                  <CliBadge cli={deriveCli(selectedTemplate)} compact />
                  <h3 className="niuu-font-mono niuu-text-lg niuu-font-medium niuu-text-text-primary">
                    {selectedTemplate.name}
                  </h3>
                  {isDefaultTemplate(selectedTemplate) && (
                    <span className="niuu-rounded-sm niuu-border niuu-border-border niuu-bg-bg-secondary niuu-px-1.5 niuu-py-0.5 niuu-font-mono niuu-text-[10px] niuu-uppercase niuu-text-text-muted">
                      default
                    </span>
                  )}
                  {selectedTemplate.usageCount !== undefined && (
                    <span className="niuu-font-mono niuu-text-[11px] niuu-text-text-faint">
                      · {selectedTemplate.usageCount} sessions launched
                    </span>
                  )}
                </div>
                <p className="niuu-mt-2 niuu-text-sm niuu-text-text-secondary">
                  {selectedTemplate.description || 'workspace + runtime bundles'}
                </p>
              </div>
              <div className="niuu-flex niuu-items-center niuu-gap-2">
                <button
                  type="button"
                  aria-label={`Clone template ${selectedTemplate.name}`}
                  className="niuu-rounded-sm niuu-border niuu-border-border niuu-bg-transparent niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-xs niuu-text-text-secondary hover:niuu-bg-bg-secondary"
                >
                  clone
                </button>
                <button
                  type="button"
                  aria-label={`Edit template ${selectedTemplate.name}`}
                  className="niuu-rounded-sm niuu-border niuu-border-border niuu-bg-transparent niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-xs niuu-text-text-secondary hover:niuu-bg-bg-secondary"
                >
                  edit
                </button>
                <button
                  type="button"
                  className="niuu-rounded-sm niuu-border niuu-border-brand niuu-bg-brand niuu-px-3 niuu-py-1.5 niuu-font-mono niuu-text-xs niuu-text-bg-primary"
                >
                  + launch from this
                </button>
              </div>
            </header>

            <nav
              className="niuu-flex niuu-gap-1 niuu-border-b niuu-border-border-subtle"
              aria-label="Template detail tabs"
            >
              {TABS.map((value) => (
                <button
                  key={value}
                  type="button"
                  role="tab"
                  aria-selected={tab === value}
                  className={`niuu-border-b niuu-px-3 niuu-py-2 niuu-font-mono niuu-text-xs niuu-transition-colors ${
                    tab === value
                      ? 'niuu-border-brand niuu-text-text-primary'
                      : 'niuu-border-transparent niuu-text-text-muted hover:niuu-text-text-secondary'
                  }`}
                  onClick={() => setTab(value)}
                >
                  {value}
                </button>
              ))}
            </nav>

            <div className="niuu-mt-4 niuu-min-h-0 niuu-flex-1">
              {tab === 'overview' && <TplOverview template={selectedTemplate} />}
              {tab === 'workspace' && <TplWorkspace template={selectedTemplate} />}
              {tab === 'runtime' && <TplRuntime template={selectedTemplate} />}
              {tab === 'mcp' && <TplMcp template={selectedTemplate} />}
              {tab === 'skills' && <TplSkills template={selectedTemplate} />}
              {tab === 'rules' && <TplRules template={selectedTemplate} />}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
