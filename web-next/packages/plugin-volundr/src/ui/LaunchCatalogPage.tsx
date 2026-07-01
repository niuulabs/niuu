import { useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import {
  Dialog,
  DialogContent,
  ErrorState,
  Field,
  Input,
  LoadingState,
  Textarea,
} from '@niuulabs/ui';
import { Copy, Edit3, PanelLeftClose, PanelLeftOpen, Plus, Rocket, Search } from 'lucide-react';
import { CliBadge } from './atoms';
import { LaunchWizard, launchSpecRef } from './LaunchWizard';
import { useLaunchSpecs } from './useLaunchSpecs';
import type { IVolundrService } from '../ports/IVolundrService';
import type { PresetRuntimeFields } from '../utils/presetYaml';
import { parsePresetYaml, serializePresetYaml } from '../utils/presetYaml';
import type { VolundrLaunchSpec } from '../models/volundr.model';

type CatalogTab = 'overview' | 'workspace' | 'runtime' | 'mcp' | 'rules';
type EditorMode = 'create' | 'edit' | 'clone';

const CATALOG_TABS: CatalogTab[] = ['overview', 'workspace', 'runtime', 'mcp', 'rules'];

const DEFAULT_RUNTIME_FIELDS: PresetRuntimeFields = {
  cliTool: 'claude',
  workloadType: 'session',
  model: 'sonnet-primary',
  systemPrompt: '',
  resourceConfig: { cpu: '2', memory: '6Gi' },
  mcpServers: [],
  terminalSidecar: { enabled: false, allowedCommands: [] },
  skills: [],
  rules: [],
  envVars: {},
  envSecretRefs: [],
  source: null,
  integrationIds: [],
  setupScripts: [],
  workloadConfig: {},
};

function specRuntimeFields(spec: VolundrLaunchSpec): PresetRuntimeFields {
  return {
    cliTool: spec.cliTool,
    workloadType: spec.workloadType,
    model: spec.model ?? '',
    systemPrompt: spec.systemPrompt ?? '',
    resourceConfig: spec.resourceConfig,
    mcpServers: spec.mcpServers,
    terminalSidecar: spec.terminalSidecar,
    skills: spec.skills,
    rules: spec.rules,
    envVars: spec.envVars,
    envSecretRefs: spec.envSecretRefs,
    source: spec.source,
    integrationIds: spec.integrationIds,
    setupScripts: spec.setupScripts,
    workloadConfig: spec.workloadConfig,
  };
}

function formatResources(spec: VolundrLaunchSpec): string {
  const cpu = spec.resourceConfig.cpu ? `${spec.resourceConfig.cpu} cores` : '';
  const memory = spec.resourceConfig.memory ?? '';
  const gpu =
    spec.resourceConfig.gpu && spec.resourceConfig.gpu !== '0'
      ? `${spec.resourceConfig.gpu} gpu`
      : '';
  return [cpu, memory, gpu].filter(Boolean).join(' · ') || 'default';
}

function formatSource(spec: VolundrLaunchSpec): string {
  if (!spec.source) return 'selected at launch';
  if (spec.source.type === 'local_mount') {
    return spec.source.local_path ?? spec.source.paths[0]?.host_path ?? 'local mount';
  }
  return [spec.source.repo, spec.source.branch].filter(Boolean).join(' @ ');
}

function sourcePath(spec: VolundrLaunchSpec): string {
  if (!spec.source) return 'source selected at launch';
  if (spec.source.type === 'git')
    return `${spec.source.repo}${spec.source.branch ? `#${spec.source.branch}` : ''}`;
  return spec.source.local_path ?? spec.source.paths[0]?.host_path ?? 'local mount';
}

function groupedSpecs(specs: VolundrLaunchSpec[]) {
  return {
    system: specs.filter((spec) => spec.scope === 'system'),
    user: specs.filter((spec) => spec.scope === 'user'),
  };
}

function specMatches(spec: VolundrLaunchSpec, search: string): boolean {
  const haystack = [
    spec.name,
    spec.description,
    spec.cliTool,
    spec.model,
    spec.workloadType,
    spec.sessionDefinition,
    ...spec.mcpServers.map((server) => server.name),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
  return haystack.includes(search.toLowerCase());
}

function DetailPanel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary">
      <h3 className="niuu:border-b niuu:border-border-subtle niuu:px-4 niuu:py-3 niuu:font-mono niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted">
        {title}
      </h3>
      <div className="niuu:p-4">{children}</div>
    </section>
  );
}

function KeyValues({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <dl className="niuu:grid niuu:grid-cols-[110px_1fr] niuu:gap-x-5 niuu:gap-y-3 niuu:text-sm">
      {rows.map(([label, value]) => (
        <div key={label} className="niuu:contents">
          <dt className="niuu:font-mono niuu:text-xs niuu:uppercase niuu:tracking-[0.12em] niuu:text-text-faint">
            {label}
          </dt>
          <dd className="niuu:min-w-0 niuu:font-mono niuu:text-text-secondary">{value || '—'}</dd>
        </div>
      ))}
    </dl>
  );
}

function TemplateListItem({
  spec,
  active,
  onClick,
}: {
  spec: VolundrLaunchSpec;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        'niuu:flex niuu:w-full niuu:items-center niuu:gap-3 niuu:px-4 niuu:py-2.5 niuu:text-left niuu:hover:bg-bg-tertiary',
        active
          ? 'niuu:border-l-2 niuu:border-brand niuu:bg-brand-subtle'
          : 'niuu:border-l-2 niuu:border-transparent',
      ].join(' ')}
      data-testid="catalog-template-item"
    >
      <CliBadge cli={spec.cliTool} compact />
      <span className="niuu:min-w-0 niuu:flex-1 niuu:truncate niuu:font-mono niuu:text-sm niuu:font-semibold niuu:text-text-secondary">
        {spec.name}
      </span>
      {spec.isDefault ? (
        <span className="niuu:rounded niuu:bg-brand-subtle niuu:px-1.5 niuu:py-0.5 niuu:font-mono niuu:text-[10px] niuu:uppercase niuu:text-brand">
          default
        </span>
      ) : null}
    </button>
  );
}

interface SpecEditorState {
  mode: EditorMode;
  base: VolundrLaunchSpec | null;
  name: string;
  description: string;
  yaml: string;
  error: string | null;
  saving: boolean;
}

function buildEditorState(mode: EditorMode, base: VolundrLaunchSpec | null): SpecEditorState {
  const cloneName = base ? `${base.name}-copy` : 'new-template';
  return {
    mode,
    base,
    name:
      mode === 'create'
        ? 'new-template'
        : mode === 'clone'
          ? cloneName
          : (base?.name ?? 'new-template'),
    description: base?.description ?? '',
    yaml: serializePresetYaml(base ? specRuntimeFields(base) : DEFAULT_RUNTIME_FIELDS),
    error: null,
    saving: false,
  };
}

export function LaunchCatalogPage() {
  const volundr = useService<IVolundrService>('volundr');
  const queryClient = useQueryClient();
  const launchSpecs = useLaunchSpecs();
  const specs = useMemo(() => launchSpecs.data ?? [], [launchSpecs.data]);
  const [selectedRef, setSelectedRef] = useState<string>('');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [search, setSearch] = useState('');
  const [activeTab, setActiveTab] = useState<CatalogTab>('overview');
  const [launchOpen, setLaunchOpen] = useState(false);
  const [launchRef, setLaunchRef] = useState<string | null>(null);
  const [editor, setEditor] = useState<SpecEditorState | null>(null);

  const filteredSpecs = useMemo(
    () => specs.filter((spec) => specMatches(spec, search.trim())),
    [search, specs],
  );
  const groups = useMemo(() => groupedSpecs(filteredSpecs), [filteredSpecs]);
  const selected =
    filteredSpecs.find((spec) => launchSpecRef(spec) === selectedRef) ??
    filteredSpecs[0] ??
    specs.find((spec) => launchSpecRef(spec) === selectedRef) ??
    specs[0] ??
    null;

  if (launchSpecs.isLoading) return <LoadingState label="Loading catalog..." />;
  if (launchSpecs.isError) {
    return (
      <ErrorState
        title="Failed to load catalog"
        message={launchSpecs.error instanceof Error ? launchSpecs.error.message : 'Unknown error'}
      />
    );
  }

  async function saveEditor() {
    if (!editor) return;
    const name = editor.name.trim();
    if (!name) {
      setEditor({ ...editor, error: 'Name is required.' });
      return;
    }
    setEditor({ ...editor, error: null, saving: true });

    try {
      const parsed = parsePresetYaml(editor.yaml);
      const base = editor.mode === 'edit' ? editor.base : null;
      const saved = await volundr.saveLaunchSpec({
        id: base?.id ?? undefined,
        name,
        description: editor.description,
        isDefault: base?.isDefault ?? false,
        sessionDefinition: base?.sessionDefinition ?? 'skuldClaude',
        workloadType:
          parsed.workloadType ?? base?.workloadType ?? DEFAULT_RUNTIME_FIELDS.workloadType,
        model: parsed.model ?? base?.model ?? DEFAULT_RUNTIME_FIELDS.model,
        systemPrompt: parsed.systemPrompt ?? base?.systemPrompt ?? '',
        resourceConfig: parsed.resourceConfig ?? base?.resourceConfig ?? {},
        mcpServers: parsed.mcpServers ?? base?.mcpServers ?? [],
        terminalSidecar:
          parsed.terminalSidecar ?? base?.terminalSidecar ?? DEFAULT_RUNTIME_FIELDS.terminalSidecar,
        cliTool: parsed.cliTool ?? base?.cliTool ?? DEFAULT_RUNTIME_FIELDS.cliTool,
        skills: parsed.skills ?? base?.skills ?? [],
        rules: parsed.rules ?? base?.rules ?? [],
        envVars: parsed.envVars ?? base?.envVars ?? {},
        envSecretRefs: parsed.envSecretRefs ?? base?.envSecretRefs ?? [],
        workloadConfig: parsed.workloadConfig ?? base?.workloadConfig ?? {},
        repos: base?.repos ?? [],
        source: parsed.source ?? base?.source ?? null,
        setupScripts: parsed.setupScripts ?? base?.setupScripts ?? [],
        workspaceLayout: base?.workspaceLayout ?? {},
        integrationIds: parsed.integrationIds ?? base?.integrationIds ?? [],
      });
      await queryClient.invalidateQueries({ queryKey: ['volundr', 'launch-specs'] });
      setSelectedRef(launchSpecRef(saved));
      setEditor(null);
    } catch (error) {
      setEditor({
        ...editor,
        error: error instanceof Error ? error.message : 'Could not save catalog spec.',
        saving: false,
      });
    }
  }

  function openLaunch(spec: VolundrLaunchSpec) {
    setLaunchRef(launchSpecRef(spec));
    setLaunchOpen(true);
  }

  function renderSection(title: string, items: VolundrLaunchSpec[]) {
    if (items.length === 0) return null;
    return (
      <div>
        <div className="niuu:flex niuu:items-center niuu:justify-between niuu:px-4 niuu:pb-2 niuu:pt-5 niuu:font-mono niuu:text-[11px] niuu:font-semibold niuu:uppercase niuu:tracking-[0.16em] niuu:text-text-faint">
          <span>{title}</span>
          <span>{items.length}</span>
        </div>
        {items.map((spec) => (
          <TemplateListItem
            key={`${spec.scope}:${launchSpecRef(spec)}`}
            spec={spec}
            active={selected ? launchSpecRef(spec) === launchSpecRef(selected) : false}
            onClick={() => {
              setSelectedRef(launchSpecRef(spec));
              setActiveTab('overview');
            }}
          />
        ))}
      </div>
    );
  }

  return (
    <>
      <div
        className="niuu:flex niuu:min-h-[calc(100vh-80px)] niuu:border-t niuu:border-border-subtle niuu:bg-bg-primary"
        data-testid="launch-catalog-page"
      >
        <aside
          className={[
            'niuu:shrink-0 niuu:border-r niuu:border-border-subtle niuu:bg-bg-primary niuu:transition-[width]',
            sidebarCollapsed ? 'niuu:w-[56px]' : 'niuu:w-[340px]',
          ].join(' ')}
          data-testid="catalog-sidebar"
        >
          {sidebarCollapsed ? (
            <div className="niuu:flex niuu:h-full niuu:flex-col niuu:items-center niuu:gap-3 niuu:pt-5">
              <button
                type="button"
                aria-label="Expand catalog sidebar"
                onClick={() => setSidebarCollapsed(false)}
                className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-2 niuu:text-text-muted niuu:hover:text-text-primary"
              >
                <PanelLeftOpen className="niuu:h-4 niuu:w-4" />
              </button>
              <div className="niuu:[writing-mode:vertical-rl] niuu:font-mono niuu:text-xs niuu:uppercase niuu:tracking-[0.2em] niuu:text-text-muted">
                catalog
              </div>
            </div>
          ) : (
            <>
              <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-3 niuu:p-4">
                <div>
                  <h2 className="niuu:text-base niuu:font-semibold niuu:text-text-primary">
                    Catalog
                  </h2>
                  <p className="niuu:mt-1 niuu:text-xs niuu:text-text-muted">
                    workspace + runtime bundles
                  </p>
                </div>
                <button
                  type="button"
                  aria-label="Collapse catalog sidebar"
                  onClick={() => setSidebarCollapsed(true)}
                  className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-2 niuu:text-text-muted niuu:hover:text-text-primary"
                >
                  <PanelLeftClose className="niuu:h-4 niuu:w-4" />
                </button>
              </div>
              <div className="niuu:px-4">
                <label className="niuu:relative niuu:block">
                  <Search className="niuu:pointer-events-none niuu:absolute niuu:left-3 niuu:top-1/2 niuu:h-4 niuu:w-4 niuu:-translate-y-1/2 niuu:text-text-faint" />
                  <input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="Filter catalog..."
                    className="niuu:w-full niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:py-2 niuu:pl-9 niuu:pr-3 niuu:font-mono niuu:text-sm niuu:text-text-primary niuu:outline-none niuu:focus:border-brand"
                  />
                </label>
              </div>
              {renderSection('built-in', groups.system)}
              {renderSection('saved', groups.user)}
            </>
          )}
        </aside>

        <main className="niuu:min-w-0 niuu:flex-1 niuu:px-8 niuu:py-6">
          {!selected ? (
            <div className="niuu:rounded-md niuu:border niuu:border-dashed niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-6 niuu:text-sm niuu:text-text-muted">
              No catalog specs configured yet.
            </div>
          ) : (
            <div className="niuu:max-w-[1500px]">
              <div className="niuu:flex niuu:flex-wrap niuu:items-start niuu:justify-between niuu:gap-4 niuu:border-b niuu:border-border-subtle niuu:pb-5">
                <div className="niuu:min-w-0">
                  <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-3">
                    <CliBadge cli={selected.cliTool} />
                    <h1 className="niuu:truncate niuu:font-mono niuu:text-2xl niuu:font-semibold niuu:text-text-primary">
                      {selected.name}
                    </h1>
                    <span className="niuu:font-mono niuu:text-xs niuu:text-text-faint">
                      {selected.scope}
                    </span>
                    {selected.isDefault ? (
                      <span className="niuu:rounded niuu:bg-brand-subtle niuu:px-2 niuu:py-1 niuu:font-mono niuu:text-[11px] niuu:uppercase niuu:text-brand">
                        default
                      </span>
                    ) : null}
                  </div>
                  <p className="niuu:mt-2 niuu:text-sm niuu:text-text-muted">
                    {selected.description || 'No description configured.'}
                  </p>
                </div>
                <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2">
                  <button
                    type="button"
                    onClick={() => setEditor(buildEditorState('clone', selected))}
                    className="niuu:inline-flex niuu:items-center niuu:gap-2 niuu:rounded-md niuu:px-3 niuu:py-2 niuu:text-sm niuu:font-semibold niuu:text-text-muted niuu:hover:bg-bg-secondary niuu:hover:text-text-primary"
                  >
                    <Copy className="niuu:h-4 niuu:w-4" />
                    Clone
                  </button>
                  {selected.scope === 'user' && selected.id ? (
                    <button
                      type="button"
                      onClick={() => setEditor(buildEditorState('edit', selected))}
                      className="niuu:inline-flex niuu:items-center niuu:gap-2 niuu:rounded-md niuu:px-3 niuu:py-2 niuu:text-sm niuu:font-semibold niuu:text-text-muted niuu:hover:bg-bg-secondary niuu:hover:text-text-primary"
                    >
                      <Edit3 className="niuu:h-4 niuu:w-4" />
                      Edit
                    </button>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => openLaunch(selected)}
                    className="niuu:inline-flex niuu:items-center niuu:gap-2 niuu:rounded-md niuu:border niuu:border-brand niuu:bg-brand-subtle niuu:px-4 niuu:py-2 niuu:text-sm niuu:font-semibold niuu:text-brand niuu:hover:bg-brand niuu:hover:text-bg-primary"
                  >
                    <Rocket className="niuu:h-4 niuu:w-4" />
                    Launch from this
                  </button>
                  <button
                    type="button"
                    onClick={() => setEditor(buildEditorState('create', null))}
                    className="niuu:inline-flex niuu:items-center niuu:gap-2 niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-2 niuu:text-sm niuu:font-semibold niuu:text-text-primary niuu:hover:border-brand"
                  >
                    <Plus className="niuu:h-4 niuu:w-4" />
                    New
                  </button>
                </div>
              </div>

              <nav
                className="niuu:mt-5 niuu:flex niuu:gap-6 niuu:border-b niuu:border-border-subtle"
                role="tablist"
              >
                {CATALOG_TABS.map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    role="tab"
                    aria-selected={activeTab === tab}
                    onClick={() => setActiveTab(tab)}
                    className={[
                      'niuu:border-b-2 niuu:px-1 niuu:pb-3 niuu:font-mono niuu:text-sm niuu:font-semibold niuu:capitalize',
                      activeTab === tab
                        ? 'niuu:border-brand niuu:text-brand'
                        : 'niuu:border-transparent niuu:text-text-muted niuu:hover:text-text-primary',
                    ].join(' ')}
                  >
                    {tab}
                  </button>
                ))}
              </nav>

              <div className="niuu:mt-6 niuu:grid niuu:grid-cols-1 niuu:gap-4 xl:niuu:grid-cols-2">
                {activeTab === 'overview' ? (
                  <>
                    <DetailPanel title="CLI & Model">
                      <KeyValues
                        rows={[
                          ['CLI', <CliBadge key="cli" cli={selected.cliTool} />],
                          ['Runtime', selected.sessionDefinition ?? selected.workloadType],
                          ['Model', selected.model ?? 'selected at launch'],
                        ]}
                      />
                    </DetailPanel>
                    <DetailPanel title="Resources">
                      <KeyValues
                        rows={[
                          [
                            'CPU',
                            selected.resourceConfig.cpu
                              ? `${selected.resourceConfig.cpu} cores`
                              : 'default',
                          ],
                          ['MEM', selected.resourceConfig.memory ?? 'default'],
                          [
                            'GPU',
                            selected.resourceConfig.gpu && selected.resourceConfig.gpu !== '0'
                              ? selected.resourceConfig.gpu
                              : '—',
                          ],
                        ]}
                      />
                    </DetailPanel>
                    <DetailPanel title="Workspace">
                      <KeyValues
                        rows={[
                          ['Source', sourcePath(selected)],
                          ['Repos', selected.repos.length || '—'],
                        ]}
                      />
                    </DetailPanel>
                    <DetailPanel title="Extensions">
                      <KeyValues
                        rows={[
                          [
                            'MCP',
                            selected.mcpServers.map((server) => server.name).join(' · ') || '—',
                          ],
                          ['Skills', selected.skills.length || '—'],
                          ['Rules', selected.rules.length || '—'],
                        ]}
                      />
                    </DetailPanel>
                  </>
                ) : null}
                {activeTab === 'workspace' ? (
                  <DetailPanel title="Workspace">
                    <KeyValues
                      rows={[
                        ['Source', formatSource(selected)],
                        [
                          'Setup',
                          selected.setupScripts.length
                            ? `${selected.setupScripts.length} scripts`
                            : '—',
                        ],
                        [
                          'Layout',
                          Object.keys(selected.workspaceLayout).length ? 'configured' : '—',
                        ],
                      ]}
                    />
                  </DetailPanel>
                ) : null}
                {activeTab === 'runtime' ? (
                  <DetailPanel title="Runtime">
                    <KeyValues
                      rows={[
                        ['Runtime', selected.sessionDefinition ?? selected.workloadType],
                        ['Model', selected.model ?? 'selected at launch'],
                        ['Resources', formatResources(selected)],
                        ['Prompt', selected.systemPrompt ? 'configured' : '—'],
                      ]}
                    />
                  </DetailPanel>
                ) : null}
                {activeTab === 'mcp' ? (
                  <DetailPanel title="MCP">
                    <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
                      {selected.mcpServers.length > 0
                        ? selected.mcpServers.map((server) => (
                            <span
                              key={server.name}
                              className="niuu:rounded niuu:bg-bg-primary niuu:px-2 niuu:py-1 niuu:font-mono niuu:text-xs niuu:text-text-secondary"
                            >
                              {server.name}
                            </span>
                          ))
                        : 'No MCP servers configured.'}
                    </div>
                  </DetailPanel>
                ) : null}
                {activeTab === 'rules' ? (
                  <DetailPanel title="Skills & Rules">
                    <KeyValues
                      rows={[
                        ['Skills', selected.skills.length || '—'],
                        ['Rules', selected.rules.length || '—'],
                      ]}
                    />
                  </DetailPanel>
                ) : null}
              </div>
            </div>
          )}
        </main>
      </div>

      {launchOpen ? (
        <LaunchWizard
          key={launchRef ?? 'catalog-custom'}
          open={launchOpen}
          onOpenChange={setLaunchOpen}
          initialLaunchSpecRef={launchRef ?? undefined}
        />
      ) : null}

      <Dialog open={Boolean(editor)} onOpenChange={(open) => !open && setEditor(null)}>
        {editor ? (
          <DialogContent
            title={`${editor.mode === 'edit' ? 'Edit' : editor.mode === 'clone' ? 'Clone' : 'Create'} catalog spec`}
            description="Catalog specs are saved as user launch specs."
            className="niuu:max-w-3xl"
          >
            <div className="niuu:flex niuu:flex-col niuu:gap-4">
              <Field label="Name" required>
                <Input
                  value={editor.name}
                  onChange={(event) => setEditor({ ...editor, name: event.target.value })}
                />
              </Field>
              <Field label="Description">
                <Input
                  value={editor.description}
                  onChange={(event) => setEditor({ ...editor, description: event.target.value })}
                />
              </Field>
              <Field label="Runtime YAML" error={editor.error ?? undefined}>
                <Textarea
                  rows={16}
                  value={editor.yaml}
                  onChange={(event) => setEditor({ ...editor, yaml: event.target.value })}
                  className="niuu:font-mono niuu:text-xs"
                  placeholder="Launch spec YAML"
                />
              </Field>
              <div className="niuu:flex niuu:justify-end niuu:gap-2">
                <button
                  type="button"
                  onClick={() => setEditor(null)}
                  className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-primary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary niuu:hover:bg-bg-secondary"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void saveEditor()}
                  disabled={editor.saving}
                  className="niuu:rounded-md niuu:border niuu:border-brand niuu:bg-brand-subtle niuu:px-3 niuu:py-2 niuu:text-sm niuu:font-semibold niuu:text-brand niuu:hover:bg-brand niuu:hover:text-bg-primary niuu:disabled:opacity-60"
                >
                  {editor.saving ? 'Saving...' : 'Save'}
                </button>
              </div>
            </div>
          </DialogContent>
        ) : null}
      </Dialog>
    </>
  );
}
