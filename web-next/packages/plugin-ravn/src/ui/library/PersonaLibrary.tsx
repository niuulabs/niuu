import { useMemo, useState, type FormEvent } from 'react';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { ChevronLeft, GitFork, Pencil, Plus, Rocket, Search, Trash2 } from 'lucide-react';
import { Dialog, DialogContent, ErrorState, LoadingState, PersonaAvatar, cn } from '@niuulabs/ui';
import type { PersonaSummary } from '@niuulabs/domain';
import type { PersonaCreateRequest } from '../../ports';
import type { Ravn } from '../../domain/ravn';
import {
  groupPersonaFamilies,
  isCustomPersona,
  matchesPersonaQuery,
  matchesPersonaSource,
  personaTagline,
  type PersonaSource,
} from '../../application/personaFamilies';
import { buildDraftPersona, personaNameProblem } from '../../application/personaDraft';
import { usePersonas } from '../usePersonas';
import {
  useCreatePersona,
  useDeletePersona,
  useForkPersona,
  usePersona,
  useUpdatePersona,
} from '../usePersona';
import { useRavens } from '../hooks/useRavens';
import { PersonaForm } from '../PersonaForm';
import { PersonaYaml } from '../PersonaYaml';
import { PersonaSheet } from './PersonaSheet';
import { errorText } from '../workbench/errorText';
import '../workbench/workbench.css';

type PersonaView = 'sheet' | 'yaml' | 'edit';

const SOURCES: Array<{ id: PersonaSource; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'in-use', label: 'In use' },
  { id: 'custom', label: 'Custom' },
];

function sourceLabel(persona: Pick<PersonaSummary, 'isBuiltin' | 'hasOverride'>): string {
  if (!persona.isBuiltin) return 'Custom';
  return persona.hasOverride ? 'Built-in · overridden' : 'Built-in';
}

function NameDialog({
  title,
  description,
  submitLabel,
  initialName,
  taken,
  pending,
  error,
  open,
  onOpenChange,
  onSubmit,
}: {
  title: string;
  description: string;
  submitLabel: string;
  initialName: string;
  taken: string[];
  pending: boolean;
  error: unknown;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (name: string) => void;
}) {
  const [name, setName] = useState(initialName);
  const [touched, setTouched] = useState(false);
  const problem = personaNameProblem(name, taken);

  function submit(event: FormEvent) {
    event.preventDefault();
    setTouched(true);
    if (problem) return;
    onSubmit(name.trim());
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={title} description={description}>
        <form className="rw-form" onSubmit={submit}>
          <label className="rw-field">
            <span className="rw-field__label">Name</span>
            <input
              className="rw-input"
              value={name}
              onChange={(event) => setName(event.target.value)}
              onBlur={() => setTouched(true)}
              autoFocus
              spellCheck={false}
              data-testid="persona-name-input"
            />
          </label>
          {touched && problem && (
            <div className="rw-form-error" role="alert">
              {problem}
            </div>
          )}
          {Boolean(error) && (
            <div className="rw-form-error" role="alert">
              {errorText(error, 'The persona could not be saved')}
            </div>
          )}
          <div className="rw-dialog-foot">
            <span className="rw-dialog-foot__sum" />
            <button type="button" className="rw-btn" onClick={() => onOpenChange(false)}>
              Cancel
            </button>
            <button
              type="submit"
              className="rw-btn rw-btn--primary"
              disabled={pending}
              data-testid="persona-name-submit"
            >
              {pending ? 'Saving…' : submitLabel}
            </button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function PersonaDetailPane({
  name,
  taken,
  usedBy,
  onSelect,
  onOpenRavn,
  onDeploy,
  onBack,
}: {
  name: string;
  taken: string[];
  usedBy: Ravn[];
  onSelect: (name: string | null) => void;
  onOpenRavn: (ravn: Ravn) => void;
  onDeploy: (name: string) => void;
  onBack: () => void;
}) {
  const persona = usePersona(name);
  const update = useUpdatePersona(name);
  const fork = useForkPersona(name);
  const remove = useDeletePersona(name);
  const [view, setView] = useState<PersonaView>('sheet');
  const [forking, setForking] = useState(false);
  const [removing, setRemoving] = useState(false);

  if (persona.isLoading) return <LoadingState label="Loading persona…" />;
  if (persona.isError || !persona.data) {
    return (
      <ErrorState
        title={`${name} could not be loaded`}
        message={errorText(persona.error, 'The persona service did not answer')}
      />
    );
  }
  const detail = persona.data;
  const custom = isCustomPersona(detail);

  async function save(request: PersonaCreateRequest) {
    await update.mutateAsync(request);
  }

  return (
    <>
      <header className="rw-head">
        <button type="button" className="rw-btn rw-back" onClick={onBack}>
          <ChevronLeft size={14} aria-hidden="true" />
          Personas
        </button>
        <PersonaAvatar role={detail.role} letter={detail.letter} size={44} />
        <div className="rw-head__who">
          <h1 className="rw-head__title">{detail.name}</h1>
          <div className="rw-chips">
            <span className="rw-chip">
              <span className="rw-chip__k">role</span>
              {detail.role}
            </span>
            <span className="rw-chip">{sourceLabel(detail)}</span>
            <span className="rw-chip">
              <span className="rw-chip__k">permissions</span>
              {detail.permissionMode}
            </span>
            <span className="rw-chip">
              {usedBy.length > 0 ? `used by ${usedBy.length}` : 'not used by any ravn'}
            </span>
          </div>
        </div>
        <div className="rw-actions">
          <div className="rw-seg" role="group" aria-label="View">
            <button
              type="button"
              aria-pressed={view !== 'yaml'}
              onClick={() => setView('sheet')}
              data-testid="persona-view-sheet"
            >
              Sheet
            </button>
            <button
              type="button"
              aria-pressed={view === 'yaml'}
              onClick={() => setView('yaml')}
              data-testid="persona-view-yaml"
            >
              YAML
            </button>
          </div>
          <button type="button" className="rw-btn" onClick={() => onDeploy(detail.name)}>
            <Rocket size={14} aria-hidden="true" />
            Deploy
          </button>
          <button
            type="button"
            className="rw-btn"
            onClick={() => {
              fork.reset();
              setForking(true);
            }}
            data-testid="persona-fork"
          >
            <GitFork size={14} aria-hidden="true" />
            Fork
          </button>
          {custom && (
            <button
              type="button"
              className="rw-icon-btn rw-icon-btn--danger"
              onClick={() => {
                remove.reset();
                setRemoving(true);
              }}
              aria-label={detail.isBuiltin ? 'Reset to built-in' : 'Delete persona'}
              title={detail.isBuiltin ? 'Reset to built-in' : 'Delete persona'}
              data-testid="persona-delete"
            >
              <Trash2 size={15} aria-hidden="true" />
            </button>
          )}
          <button
            type="button"
            className="rw-btn rw-btn--primary"
            onClick={() => setView('edit')}
            disabled={view === 'edit'}
            data-testid="persona-edit"
          >
            <Pencil size={14} aria-hidden="true" />
            Edit
          </button>
        </div>
      </header>

      <div className="rw-body" data-testid={`persona-body-${view}`}>
        {view === 'sheet' && (
          <PersonaSheet
            persona={detail}
            usedBy={usedBy}
            onOpenRavn={onOpenRavn}
            onDeploy={() => onDeploy(detail.name)}
          />
        )}
        {view === 'yaml' && (
          <div className="rw-yaml">
            <PersonaYaml name={detail.name} />
          </div>
        )}
        {view === 'edit' && (
          <>
            <div className="rw-editbar" role="status">
              <span className="rw-editbar__text">
                <strong>Editing {detail.name}</strong>
                {detail.isBuiltin
                  ? ' — saving writes an override; the built-in stays one reset away.'
                  : ' — changes apply to ravens the next time they load it.'}
              </span>
              <button type="button" className="rw-btn" onClick={() => setView('sheet')}>
                Done
              </button>
            </div>
            {update.isError && (
              <p className="rw-inline-error" role="alert">
                {errorText(update.error, 'Saving failed')}
              </p>
            )}
            <PersonaForm persona={detail} onSave={save} isSaving={update.isPending} />
          </>
        )}
      </div>

      <NameDialog
        key={`fork:${forking}`}
        title={`Fork ${detail.name}`}
        description="Makes an editable copy. The original stays as it is."
        submitLabel="Fork"
        initialName={`${detail.name}-copy`}
        taken={taken}
        pending={fork.isPending}
        error={fork.error}
        open={forking}
        onOpenChange={setForking}
        onSubmit={(newName) =>
          fork.mutate(newName, {
            onSuccess: (created) => {
              setForking(false);
              onSelect(created.name);
            },
          })
        }
      />

      <Dialog open={removing} onOpenChange={setRemoving}>
        <DialogContent
          title={detail.isBuiltin ? `Reset ${detail.name}` : `Delete ${detail.name}`}
          description={
            detail.isBuiltin
              ? 'Removes your override; the built-in definition applies again.'
              : `Deletes this persona.${usedBy.length > 0 ? ` ${usedBy.length} ravn(s) still name it and will fail to load it.` : ''}`
          }
        >
          {remove.isError && (
            <div className="rw-form-error" role="alert">
              {errorText(remove.error, 'The persona could not be removed')}
            </div>
          )}
          <div className="rw-dialog-foot">
            <span className="rw-dialog-foot__sum" />
            <button type="button" className="rw-btn" onClick={() => setRemoving(false)}>
              Cancel
            </button>
            <button
              type="button"
              className="rw-btn rw-btn--danger"
              disabled={remove.isPending}
              onClick={() =>
                remove.mutate(undefined, {
                  onSuccess: () => {
                    setRemoving(false);
                    onSelect(detail.isBuiltin ? detail.name : null);
                  },
                })
              }
              data-testid="persona-delete-confirm"
            >
              {detail.isBuiltin ? 'Reset' : 'Delete'}
            </button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function PersonaLibrary() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as { persona?: unknown };
  const requested = typeof search.persona === 'string' ? search.persona : null;
  const personas = usePersonas();
  const ravens = useRavens();
  const create = useCreatePersona();
  const [query, setQuery] = useState('');
  const [source, setSource] = useState<PersonaSource>('all');
  const [closed, setClosed] = useState<Record<string, boolean>>({});
  const [creating, setCreating] = useState(false);

  const list = useMemo(() => personas.data ?? [], [personas.data]);
  const fleet = useMemo(() => ravens.data ?? [], [ravens.data]);
  const usage = useMemo(() => {
    const counts = new Map<string, number>();
    for (const ravn of fleet) {
      if (ravn.personaName) counts.set(ravn.personaName, (counts.get(ravn.personaName) ?? 0) + 1);
    }
    return (name: string) => counts.get(name) ?? 0;
  }, [fleet]);
  const counts = useMemo(
    () => ({
      all: list.length,
      'in-use': list.filter((persona) => usage(persona.name) > 0).length,
      custom: list.filter(isCustomPersona).length,
    }),
    [list, usage],
  );
  const families = useMemo(
    () =>
      groupPersonaFamilies(
        list.filter(
          (persona) =>
            matchesPersonaQuery(persona, query) && matchesPersonaSource(persona, source, usage),
        ),
      ),
    [list, query, source, usage],
  );
  const selectedName =
    (requested && list.some((persona) => persona.name === requested) ? requested : null) ??
    list.find((persona) => persona.name === 'reviewer')?.name ??
    list[0]?.name ??
    null;

  function select(name: string | null) {
    void navigate({
      to: '/ravn/personas' as never,
      search: (name ? { persona: name } : {}) as never,
    });
  }

  function openRavn(ravn: Ravn) {
    void navigate({
      to: '/ravn' as never,
      search: { ravn: ravn.id, instance_id: ravn.instanceId, tab: 'chat' } as never,
    });
  }

  function deploy(name: string) {
    void navigate({ to: '/ravn' as never, search: { deploy: name } as never });
  }

  if (personas.isLoading) {
    return (
      <div className="rw-state-screen" data-testid="persona-library-loading">
        <LoadingState label="Loading personas…" />
      </div>
    );
  }
  if (personas.isError) {
    return (
      <div className="rw-state-screen" data-testid="persona-library-error">
        <ErrorState
          title="Personas could not be loaded"
          message={errorText(personas.error, 'The persona service did not answer')}
        />
      </div>
    );
  }

  return (
    <div className="rw" data-detail-open={Boolean(requested)} data-testid="persona-library">
      <aside className="rw-rail" aria-label="Personas">
        <div className="rw-rail__head">
          <div>
            <h2 className="rw-rail__title">Personas</h2>
            <div className="rw-rail__sub">the character a ravn runs with</div>
          </div>
          <button
            type="button"
            className="rw-btn rw-btn--primary"
            onClick={() => {
              create.reset();
              setCreating(true);
            }}
            data-testid="persona-new"
          >
            <Plus size={14} aria-hidden="true" />
            New
          </button>
        </div>
        <div className="rw-pills" role="group" aria-label="Filter personas">
          {SOURCES.map((option) => (
            <button
              key={option.id}
              type="button"
              className="rw-pill"
              aria-pressed={source === option.id}
              onClick={() => setSource(option.id)}
              data-testid={`persona-filter-${option.id}`}
            >
              {option.label}
              <span className="rw-pill__n">{counts[option.id]}</span>
            </button>
          ))}
        </div>
        <div className="rw-tools">
          <label className="rw-search">
            <Search size={14} aria-hidden="true" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search name, event, tool…"
              aria-label="Search personas"
              data-testid="persona-search"
            />
          </label>
        </div>
        <div className="rw-rail__scroll">
          {families.length === 0 && (
            <p className="rw-rail__empty">
              {source === 'custom' && !query
                ? 'No custom personas yet. Fork a built-in to make one.'
                : 'No personas match.'}
            </p>
          )}
          {families.map((family) => {
            const isClosed = Boolean(closed[family.key]) && !query;
            return (
              <section key={family.key} aria-label={family.label}>
                <button
                  type="button"
                  className="rw-fam"
                  aria-expanded={!isClosed}
                  onClick={() => setClosed((current) => ({ ...current, [family.key]: !isClosed }))}
                  data-testid={`persona-family-${family.key}`}
                >
                  <span>
                    {isClosed ? '▸' : '▾'} {family.label}
                  </span>
                  <span className="rw-fam__n">{family.personas.length}</span>
                </button>
                {!isClosed &&
                  family.personas.map((persona) => {
                    const used = usage(persona.name);
                    return (
                      <button
                        key={persona.name}
                        type="button"
                        className="rw-prow"
                        aria-current={persona.name === selectedName}
                        onClick={() => select(persona.name)}
                        data-testid={`persona-row-${persona.name}`}
                      >
                        <PersonaAvatar role={persona.role} letter={persona.letter} size={24} />
                        <span className="rw-row__body">
                          <span className="rw-prow__name">{persona.name}</span>
                          <span className="rw-prow__sub">{personaTagline(persona)}</span>
                        </span>
                        <span className={cn(used > 0 ? 'rw-prow__used' : 'rw-prow__badge')}>
                          {used > 0 ? `● ${used}` : isCustomPersona(persona) ? 'custom' : ''}
                        </span>
                      </button>
                    );
                  })}
              </section>
            );
          })}
        </div>
      </aside>

      <section className="rw-detail" data-testid="persona-detail">
        {selectedName ? (
          <PersonaDetailPane
            key={selectedName}
            name={selectedName}
            taken={list.map((persona) => persona.name)}
            usedBy={fleet.filter((ravn) => ravn.personaName === selectedName)}
            onSelect={select}
            onOpenRavn={openRavn}
            onDeploy={deploy}
            onBack={() => select(null)}
          />
        ) : (
          <div className="rw-empty">
            <div className="rw-empty__inner">
              <h3 className="rw-empty__title">No personas</h3>
              <p className="rw-empty__text">Create one to give a ravn its character.</p>
            </div>
          </div>
        )}
      </section>

      <NameDialog
        key={`new:${creating}`}
        title="New persona"
        description="Starts from an empty sheet you fill in next."
        submitLabel="Create"
        initialName=""
        taken={list.map((persona) => persona.name)}
        pending={create.isPending}
        error={create.error}
        open={creating}
        onOpenChange={setCreating}
        onSubmit={(name) =>
          create.mutate(buildDraftPersona(name), {
            onSuccess: (created) => {
              setCreating(false);
              select(created.name);
            },
          })
        }
      />
    </div>
  );
}
