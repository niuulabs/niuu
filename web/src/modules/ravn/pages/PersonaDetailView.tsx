import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  getPersona,
  getPersonaYaml,
  createPersona,
  updatePersona,
  deletePersona,
  forkPersona,
} from '../api/client';
import type { PersonaDetail, PersonaCreateRequest } from '../api/types';
import { PersonaForm } from '../components/PersonaForm';
import { ToolBadge } from '../components/ToolBadge';
import { cn } from '@/modules/shared/utils/classnames';
import styles from './PersonaDetailView.module.css';
import badgeStyles from '../styles/badges.module.css';

type ViewMode = 'view' | 'edit' | 'fork' | 'create';

const NEW_SENTINEL = '~new';

export function PersonaDetailView() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();

  const isCreate = name === NEW_SENTINEL;

  const [persona, setPersona] = useState<PersonaDetail | null>(null);
  const [yaml, setYaml] = useState<string | null>(null);
  const [loading, setLoading] = useState(!isCreate);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<ViewMode>(isCreate ? 'create' : 'view');
  const [showYaml, setShowYaml] = useState(false);
  const [forkName, setForkName] = useState('');
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    if (isCreate || !name) return;

    Promise.all([getPersona(name), getPersonaYaml(name)])
      .then(([detail, rawYaml]) => {
        setPersona(detail);
        setYaml(rawYaml);
        setLoading(false);
      })
      .catch(() => {
        setError('Failed to load persona');
        setLoading(false);
      });
  }, [name, isCreate]);

  async function handleCreate(req: PersonaCreateRequest) {
    setActionError(null);
    const created = await createPersona(req).catch(err => {
      setActionError(err.detail ?? 'Create failed');
      return null;
    });
    if (!created) return;
    navigate(`/ravn/personas/${encodeURIComponent(created.name)}`);
  }

  async function handleUpdate(req: PersonaCreateRequest) {
    if (!persona) return;
    setActionError(null);
    const updated = await updatePersona(persona.name, req).catch(err => {
      setActionError(err.detail ?? 'Update failed');
      return null;
    });
    if (!updated) return;
    setPersona(updated);
    setMode('view');
  }

  async function handleDelete() {
    if (!persona) return;
    if (!window.confirm(`Delete persona "${persona.name}"? This cannot be undone.`)) return;
    setActionError(null);
    const ok = await deletePersona(persona.name)
      .then(() => true)
      .catch(err => {
        setActionError(err.detail ?? 'Delete failed');
        return false;
      });
    if (!ok) return;
    navigate('/ravn/personas');
  }

  async function handleFork() {
    if (!persona || !forkName.trim()) return;
    setActionError(null);
    const forked = await forkPersona(persona.name, { newName: forkName.trim() }).catch(err => {
      setActionError(err.detail ?? 'Fork failed');
      return null;
    });
    if (!forked) return;
    navigate(`/ravn/personas/${encodeURIComponent(forked.name)}`);
  }

  if (loading) {
    return <div className={styles.status}>Loading…</div>;
  }

  if (error) {
    return (
      <div className={styles.status}>
        <span className={styles.errorText}>{error}</span>
        <button className={styles.backButton} onClick={() => navigate('/ravn/personas')}>
          ← Back
        </button>
      </div>
    );
  }

  if (isCreate || mode === 'edit') {
    return (
      <div className={styles.container}>
        <div className={styles.pageHeader}>
          <button
            className={styles.backButton}
            onClick={() => {
              if (isCreate) {
                navigate('/ravn/personas');
              } else {
                setMode('view');
              }
            }}
          >
            ← Back
          </button>
          <h2 className={styles.pageTitle}>
            {isCreate ? 'New Persona' : `Edit: ${persona?.name}`}
          </h2>
        </div>
        {actionError && <div className={styles.actionError}>{actionError}</div>}
        <PersonaForm
          initial={mode === 'edit' ? (persona ?? undefined) : undefined}
          onSubmit={isCreate ? handleCreate : handleUpdate}
          onCancel={() => {
            if (isCreate) {
              navigate('/ravn/personas');
            } else {
              setMode('view');
            }
          }}
          submitLabel={isCreate ? 'Create' : 'Save Changes'}
        />
      </div>
    );
  }

  if (!persona) return null;

  return (
    <div className={styles.container}>
      {/* Page header */}
      <div className={styles.pageHeader}>
        <button className={styles.backButton} onClick={() => navigate('/ravn/personas')}>
          ← Back
        </button>
        <div className={styles.titleRow}>
          <h2 className={styles.pageTitle}>{persona.name}</h2>
          <div className={styles.titleBadges}>
            {persona.isBuiltin && (
              <span className={cn(badgeStyles.badge, badgeStyles.builtinBadge)}>built-in</span>
            )}
            {persona.hasOverride && (
              <span className={cn(badgeStyles.badge, badgeStyles.overrideBadge)}>override</span>
            )}
          </div>
        </div>
        <div className={styles.actions}>
          <button className={styles.actionButton} onClick={() => setMode('fork')}>
            Fork
          </button>
          <button className={styles.actionButton} onClick={() => setMode('edit')}>
            Edit
          </button>
          {!persona.isBuiltin && (
            <button className={cn(styles.actionButton, styles.deleteButton)} onClick={handleDelete}>
              Delete
            </button>
          )}
        </div>
      </div>

      {actionError && <div className={styles.actionError}>{actionError}</div>}

      {/* Fork panel */}
      {mode === 'fork' && (
        <div className={styles.forkPanel}>
          <span className={styles.forkLabel}>Fork as:</span>
          <input
            className={styles.forkInput}
            type="text"
            value={forkName}
            onChange={e => setForkName(e.target.value)}
            placeholder="new-persona-name"
            autoFocus
          />
          <button className={styles.actionButton} onClick={handleFork} disabled={!forkName.trim()}>
            Create Fork
          </button>
          <button className={styles.cancelForkButton} onClick={() => setMode('view')}>
            Cancel
          </button>
        </div>
      )}

      {/* Detail sections */}
      <div className={styles.sections}>
        {/* Identity */}
        <section className={styles.section}>
          <h3 className={styles.sectionTitle}>Identity</h3>
          <dl className={styles.dl}>
            <dt className={styles.dt}>Permission Mode</dt>
            <dd className={styles.dd}>{persona.permissionMode || '—'}</dd>
            <dt className={styles.dt}>Iteration Budget</dt>
            <dd className={styles.dd}>
              {persona.iterationBudget === 0 ? 'unlimited' : persona.iterationBudget}
            </dd>
            <dt className={styles.dt}>Source</dt>
            <dd className={styles.dd}>{persona.yamlSource}</dd>
          </dl>
        </section>

        {/* Tools & Permissions */}
        <section className={styles.section}>
          <h3 className={styles.sectionTitle}>Tools & Permissions</h3>
          <dl className={styles.dl}>
            <dt className={styles.dt}>Allowed Tools</dt>
            <dd className={styles.dd}>
              {persona.allowedTools.length > 0 ? (
                <div className={styles.tagRow}>
                  {persona.allowedTools.map(t => (
                    <ToolBadge key={t} tool={t} />
                  ))}
                </div>
              ) : (
                '—'
              )}
            </dd>
            <dt className={styles.dt}>Forbidden Tools</dt>
            <dd className={styles.dd}>
              {persona.forbiddenTools.length > 0 ? (
                <div className={styles.tagRow}>
                  {persona.forbiddenTools.map(t => (
                    <ToolBadge key={t} tool={t} />
                  ))}
                </div>
              ) : (
                '—'
              )}
            </dd>
          </dl>
        </section>

        {/* LLM Settings */}
        <section className={styles.section}>
          <h3 className={styles.sectionTitle}>LLM Settings</h3>
          <dl className={styles.dl}>
            <dt className={styles.dt}>Primary Alias</dt>
            <dd className={styles.dd}>{persona.llm.primaryAlias || '—'}</dd>
            <dt className={styles.dt}>Extended Thinking</dt>
            <dd className={styles.dd}>{persona.llm.thinkingEnabled ? 'enabled' : 'disabled'}</dd>
            <dt className={styles.dt}>Max Tokens</dt>
            <dd className={styles.dd}>
              {persona.llm.maxTokens === 0 ? 'default' : persona.llm.maxTokens}
            </dd>
          </dl>
        </section>

        {/* Pipeline Contract */}
        <section className={styles.section}>
          <h3 className={styles.sectionTitle}>Pipeline Contract</h3>
          <dl className={styles.dl}>
            <dt className={styles.dt}>Produces</dt>
            <dd className={styles.dd}>{persona.produces.eventType || '—'}</dd>
            <dt className={styles.dt}>Consumes</dt>
            <dd className={styles.dd}>
              {persona.consumes.eventTypes.length > 0
                ? persona.consumes.eventTypes.join(', ')
                : '—'}
            </dd>
            <dt className={styles.dt}>Context Injects</dt>
            <dd className={styles.dd}>
              {persona.consumes.injects.length > 0 ? persona.consumes.injects.join(', ') : '—'}
            </dd>
            <dt className={styles.dt}>Fan-in Strategy</dt>
            <dd className={styles.dd}>{persona.fanIn.strategy || '—'}</dd>
            <dt className={styles.dt}>Contributes To</dt>
            <dd className={styles.dd}>{persona.fanIn.contributesTo || '—'}</dd>
          </dl>
        </section>

        {/* System Prompt */}
        <section className={styles.section}>
          <h3 className={styles.sectionTitle}>System Prompt</h3>
          <pre className={styles.prompt}>{persona.systemPromptTemplate || '—'}</pre>
        </section>

        {/* YAML Preview */}
        <section className={styles.section}>
          <button className={styles.toggleYaml} onClick={() => setShowYaml(v => !v)}>
            {showYaml ? '▾' : '▸'} Raw YAML
          </button>
          {showYaml && yaml && <pre className={styles.yaml}>{yaml}</pre>}
        </section>
      </div>
    </div>
  );
}
