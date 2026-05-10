import { useCallback, useEffect, useMemo, useState } from 'react';
import { PersonaAvatar, ErrorState, LoadingState, cn } from '@niuulabs/ui';
import type { PersonaRole } from '@niuulabs/domain';
import type { PersonaCreateRequest, PersonaSummary } from '../ports';
import { PERSONA_ROLE_ORDER } from '../catalog';
import { PersonaList } from './PersonaList';
import { PersonaForm } from './PersonaForm';
import { PersonaYaml } from './PersonaYaml';
import { PersonaSubs } from './PersonaSubs';
import { useCreatePersona, useForkPersona, usePersona, useUpdatePersona } from './usePersona';
import { usePersonas } from './usePersonas';
import { loadStorage, saveStorage } from './storage';
import './ravn-views.css';
import './PersonasPage.css';

const PERSONA_STORAGE_KEY = 'ravn.persona';

type TabId = 'form' | 'yaml' | 'subs';

function pickDefaultPersona(
  personas: PersonaSummary[],
  preferredName: string | null,
): string | null {
  if (personas.length === 0) return null;
  if (preferredName && personas.some((persona) => persona.name === preferredName))
    return preferredName;
  return personas.find((persona) => persona.name === 'reviewer')?.name ?? personas[0]!.name;
}

function groupPersonas(personas: PersonaSummary[]) {
  const grouped = new Map<PersonaRole, PersonaSummary[]>();
  for (const role of PERSONA_ROLE_ORDER) grouped.set(role, []);

  for (const persona of personas) {
    const bucket = grouped.get(persona.role);
    if (bucket) bucket.push(persona);
    else grouped.get('plan')!.push(persona);
  }

  return PERSONA_ROLE_ORDER.map((role) => [role, grouped.get(role) ?? []] as const).filter(
    ([, items]) => items.length > 0,
  );
}

interface PersonaDetailPaneProps {
  name: string;
  activeTab: TabId;
  onTabChange: (tab: TabId) => void;
  onSelectPersona: (name: string) => void;
}

function buildDraftPersona(name: string): PersonaCreateRequest {
  const label = name.replace(/[-_]+/g, ' ').trim() || 'New persona';
  return {
    name,
    role: 'build',
    letter: '',
    color: '',
    summary: label,
    description: `${label} persona.`,
    systemPromptTemplate: `# ${name}\nYou are the ${name} persona.`,
    allowedTools: [],
    forbiddenTools: [],
    permissionMode: 'default',
    executor: undefined,
    iterationBudget: 20,
    llmPrimaryAlias: 'claude-sonnet-4-6',
    llmThinkingEnabled: false,
    llmMaxTokens: 8192,
    producesEventType: '',
    producesSchema: {},
    consumesEvents: [],
    consumesSchema: {},
  };
}

function PersonaDetailPane({
  name,
  activeTab,
  onTabChange,
  onSelectPersona,
}: PersonaDetailPaneProps) {
  const { data: persona } = usePersona(name);
  const { mutateAsync: updatePersona, isPending: isSaving } = useUpdatePersona(name);
  const { mutateAsync: createPersona, isPending: isCreating } = useCreatePersona();
  const { mutateAsync: forkPersona, isPending: isForking } = useForkPersona(name);

  const handleSave = useCallback(
    async (req: PersonaCreateRequest) => {
      await updatePersona(req);
    },
    [updatePersona],
  );

  const handleCreate = useCallback(async () => {
    const requestedName = window.prompt('New persona name');
    const nextName = requestedName?.trim();
    if (!nextName) return;
    const created = await createPersona(buildDraftPersona(nextName));
    onSelectPersona(created.name);
  }, [createPersona, onSelectPersona]);

  const handleFork = useCallback(async () => {
    if (!persona) return;
    const requestedName = window.prompt('Clone persona as…', `${persona.name}-copy`);
    const nextName = requestedName?.trim();
    if (!nextName) return;
    const forked = await forkPersona(nextName);
    onSelectPersona(forked.name);
  }, [forkPersona, onSelectPersona, persona]);

  const tabs: { id: TabId; label: string }[] = [
    { id: 'form', label: 'form' },
    { id: 'yaml', label: 'yaml' },
    { id: 'subs', label: 'subscriptions' },
  ];

  return (
    <div className="rv-personas__detail-pane" data-testid="persona-detail">
      {persona && (
        <div className="rv-pr-head rv-personas__head">
          <div className="rv-pr-head__left">
            {persona.role && persona.letter && (
              <PersonaAvatar role={persona.role} letter={persona.letter} size={54} />
            )}
            <div className="rv-pr-head__info">
              <div className="rv-pr-head__name">{persona.name}</div>
              <div className="rv-pr-head__sub">
                role: <strong>{persona.role}</strong>
                <span className="rv-pr-head__sep">·</span>
                {persona.isBuiltin ? 'builtin' : 'user-defined'}
                {persona.isBuiltin && persona.hasOverride && (
                  <>
                    <span className="rv-pr-head__sep">·</span>
                    <span className="rv-pr-head__override">override applied</span>
                  </>
                )}
              </div>
              <div className="rv-pr-head__origin">
                <span className="rv-pr-head__origin-label">loaded from</span>
                <code className="rv-pr-head__origin-path">
                  {persona.yamlSource === '[mock]'
                    ? `volundr/src/ravn/personas/${persona.name}.yaml`
                    : persona.yamlSource}
                </code>
                {persona.overrideSource && (
                  <>
                    <span className="rv-pr-head__origin-label">then overridden by</span>
                    <code className="rv-pr-head__origin-path rv-pr-head__origin-path--override">
                      {persona.overrideSource}
                    </code>
                  </>
                )}
              </div>
            </div>
          </div>
          <div className="rv-pr-head__right rv-personas__head-actions">
            <div className="rv-pr-seg">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  aria-selected={activeTab === tab.id}
                  onClick={() => onTabChange(tab.id)}
                  className={`rv-pr-seg__btn${activeTab === tab.id ? ' rv-pr-seg__btn--active' : ''}`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <button
              type="button"
              className="rv-pr-action-btn"
              onClick={() => void handleCreate()}
              disabled={isCreating}
            >
              {isCreating ? 'creating…' : '+ new persona'}
            </button>
            <button
              type="button"
              className="rv-pr-action-btn"
              onClick={() => void handleFork()}
              disabled={isForking}
            >
              {isForking ? 'cloning…' : 'clone as…'}
            </button>
          </div>
        </div>
      )}

      <div className="rv-personas__detail-body">
        {activeTab === 'form' && persona && (
          <PersonaForm persona={persona} onSave={handleSave} isSaving={isSaving} />
        )}
        {activeTab === 'yaml' && <PersonaYaml name={name} />}
        {activeTab === 'subs' && <PersonaSubs name={name} />}
      </div>
    </div>
  );
}

export function PersonasPage() {
  const [selectedName, setSelectedName] = useState<string | null>(() =>
    loadStorage<string | null>(PERSONA_STORAGE_KEY, null),
  );
  const [activeTab, setActiveTab] = useState<TabId>('form');
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const { data: personas, isLoading, isError, error } = usePersonas();

  const personaList = useMemo(() => personas ?? [], [personas]);

  useEffect(() => {
    const handleSelect = (event: Event) => {
      const name = (event as CustomEvent<string>).detail;
      saveStorage(PERSONA_STORAGE_KEY, name);
      setSelectedName(name);
      setActiveTab('form');
    };

    window.addEventListener('ravn:persona-selected', handleSelect);
    return () => window.removeEventListener('ravn:persona-selected', handleSelect);
  }, []);

  useEffect(() => {
    if (personaList.length === 0) {
      setSelectedName(null);
      return;
    }

    const preferredName = selectedName ?? loadStorage<string | null>(PERSONA_STORAGE_KEY, null);
    const nextName = pickDefaultPersona(personaList, preferredName);
    if (nextName && nextName !== selectedName) {
      saveStorage(PERSONA_STORAGE_KEY, nextName);
      setSelectedName(nextName);
    }
  }, [personaList, selectedName]);

  const groupedPersonas = useMemo(() => groupPersonas(personaList), [personaList]);

  const handleSelectPersona = useCallback((name: string) => {
    saveStorage(PERSONA_STORAGE_KEY, name);
    setSelectedName(name);
    setActiveTab('form');
  }, []);

  const selectedPersona = personaList.find((persona) => persona.name === selectedName) ?? null;

  if (isLoading) {
    return (
      <div className="rv-personas" data-testid="personas-page">
        <div className="rv-personas__state" data-testid="personas-loading">
          <LoadingState label="Loading personas…" />
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="rv-personas" data-testid="personas-page">
        <div className="rv-personas__state" data-testid="personas-error">
          <ErrorState
            message={error instanceof Error ? error.message : 'Failed to load personas'}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="rv-personas" data-testid="personas-page">
      <div className="rv-personas__content">
        <aside
          className={cn(
            'rv-personas__sidebar',
            sidebarCollapsed && 'rv-personas__sidebar--collapsed',
          )}
          aria-label="Personas directory"
          data-testid="personas-sidebar"
        >
          {sidebarCollapsed ? (
            <div className="rv-personas__collapsed">
              <div className="rv-personas__collapsed-head">
                <button
                  type="button"
                  onClick={() => setSidebarCollapsed(false)}
                  className="rv-personas__toggle"
                  data-testid="personas-sidebar-toggle"
                  aria-label="Expand personas sidebar"
                >
                  ›
                </button>
              </div>
              <div className="rv-personas__collapsed-body">
                {groupedPersonas.map(([role, rolePersonas]) => (
                  <div key={role} className="rv-personas__collapsed-group">
                    {rolePersonas.map((persona) => (
                      <button
                        key={persona.name}
                        type="button"
                        onClick={() => handleSelectPersona(persona.name)}
                        className={cn(
                          'rv-personas__collapsed-item',
                          selectedPersona?.name === persona.name &&
                            'rv-personas__collapsed-item--selected',
                        )}
                        aria-label={persona.name}
                      >
                        <PersonaAvatar role={persona.role} letter={persona.letter} size={24} />
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="rv-personas__expanded">
              <div className="rv-personas__headbar">
                <div className="rv-personas__title-block">
                  <div>
                    <h2 className="rv-personas__title">Personas</h2>
                    <div className="rv-personas__subtitle">cognitive templates</div>
                  </div>
                </div>
                <div className="rv-personas__title-row">
                  <div className="rv-personas__count">{personaList.length}</div>
                  <button
                    type="button"
                    onClick={() => setSidebarCollapsed(true)}
                    className="rv-personas__toggle"
                    data-testid="personas-sidebar-toggle"
                    aria-label="Collapse personas sidebar"
                  >
                    ‹
                  </button>
                </div>
              </div>
              <div className="rv-personas__body">
                <PersonaList
                  selectedName={selectedName}
                  onSelect={handleSelectPersona}
                  personas={personaList}
                  isLoadingOverride={false}
                  showFooterAction={false}
                  showRoleHint={false}
                  showMeta={false}
                  className="rv-personas__list"
                  dataTestId="personas-directory"
                />
              </div>
            </div>
          )}
        </aside>

        <main className="rv-personas__detail" data-testid="personas-detail-pane">
          {selectedName ? (
            <PersonaDetailPane
              name={selectedName}
              activeTab={activeTab}
              onTabChange={setActiveTab}
              onSelectPersona={handleSelectPersona}
            />
          ) : (
            <div className="rv-personas__empty">No personas available.</div>
          )}
        </main>
      </div>
    </div>
  );
}
