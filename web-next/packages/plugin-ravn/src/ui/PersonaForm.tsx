import { useState, useCallback, useEffect } from 'react';
import {
  cn,
  EventPicker,
  SchemaEditor,
  ToolPicker,
  ValidationSummary,
  MountChip,
} from '@niuulabs/ui';
import type { FieldType, PersonaRole } from '@niuulabs/domain';
import type {
  PersonaDetail,
  PersonaCreateRequest,
  PersonaConsumesEvent,
  PersonaExecutor,
} from '../ports';
import { validatePersona } from './validatePersona';
import { SEED_EVENT_CATALOG, SEED_TOOL_REGISTRY } from '../catalog';
import './PersonaForm.css';

const PERMISSION_MODES = ['default', 'safe', 'loose'] as const;
const MIMIR_ROUTINGS = ['local', 'shared', 'domain'] as const;
const CLI_EXECUTOR_ADAPTER = 'ravn.adapters.executors.cli.CliTransportExecutor';
const CODEX_WS_TRANSPORT_ADAPTER = 'skuld.transports.codex_ws.CodexWebSocketTransport';

type ExecutorMode = 'ravn' | 'codex_ws' | 'custom';

// ── Fan-in strategy definitions ────────────────────────────────────────────

const FAN_IN_STRATEGIES = [
  'all_must_pass',
  'any_passes',
  'quorum',
  'merge',
  'first_wins',
  'weighted_score',
] as const;

type FanInStrategy = (typeof FAN_IN_STRATEGIES)[number];

const FAN_IN_DESCRIPTIONS: Record<FanInStrategy, string> = {
  all_must_pass: 'All upstream events must arrive before processing',
  any_passes: 'First arriving event triggers processing',
  quorum: 'N of M events must arrive',
  merge: 'All events merged into a single context',
  first_wins: 'First event wins, others discarded',
  weighted_score: 'Events scored and ranked by weight',
};

// ── Fan-in SVG diagrams ─────────────────────────────────────────────────────

function AllMustPassDiagram() {
  return (
    <svg width={32} height={20} viewBox="0 0 32 20" fill="none" aria-hidden="true">
      <line x1="2" y1="5" x2="14" y2="10" stroke="currentColor" strokeWidth={1.5} />
      <line x1="2" y1="10" x2="14" y2="10" stroke="currentColor" strokeWidth={1.5} />
      <line x1="2" y1="15" x2="14" y2="10" stroke="currentColor" strokeWidth={1.5} />
      <circle cx="17" cy="10" r="3" fill="currentColor" />
      <line x1="20" y1="10" x2="30" y2="10" stroke="currentColor" strokeWidth={1.5} />
    </svg>
  );
}

function AnyPassesDiagram() {
  return (
    <svg width={32} height={20} viewBox="0 0 32 20" fill="none" aria-hidden="true">
      <line x1="2" y1="5" x2="14" y2="10" stroke="currentColor" strokeWidth={1.5} />
      <line x1="2" y1="10" x2="14" y2="10" stroke="currentColor" strokeWidth={0.8} opacity={0.4} />
      <line x1="2" y1="15" x2="14" y2="10" stroke="currentColor" strokeWidth={0.8} opacity={0.4} />
      <polygon points="14,7 20,10 14,13" fill="currentColor" />
      <line x1="20" y1="10" x2="30" y2="10" stroke="currentColor" strokeWidth={1.5} />
    </svg>
  );
}

function QuorumDiagram() {
  return (
    <svg width={32} height={20} viewBox="0 0 32 20" fill="none" aria-hidden="true">
      <line x1="2" y1="5" x2="12" y2="10" stroke="currentColor" strokeWidth={1.5} />
      <line x1="2" y1="10" x2="12" y2="10" stroke="currentColor" strokeWidth={1.5} />
      <line x1="2" y1="15" x2="12" y2="10" stroke="currentColor" strokeWidth={0.8} opacity={0.4} />
      <rect x="12" y="7" width="8" height="6" rx="1" fill="currentColor" opacity={0.6} />
      <line x1="20" y1="10" x2="30" y2="10" stroke="currentColor" strokeWidth={1.5} />
    </svg>
  );
}

function MergeDiagram() {
  return (
    <svg width={32} height={20} viewBox="0 0 32 20" fill="none" aria-hidden="true">
      <line x1="2" y1="4" x2="14" y2="10" stroke="currentColor" strokeWidth={1.5} />
      <line x1="2" y1="10" x2="14" y2="10" stroke="currentColor" strokeWidth={1.5} />
      <line x1="2" y1="16" x2="14" y2="10" stroke="currentColor" strokeWidth={1.5} />
      <line x1="14" y1="10" x2="30" y2="10" stroke="currentColor" strokeWidth={3} />
    </svg>
  );
}

function FirstWinsDiagram() {
  return (
    <svg width={32} height={20} viewBox="0 0 32 20" fill="none" aria-hidden="true">
      <line x1="2" y1="5" x2="30" y2="5" stroke="currentColor" strokeWidth={1.5} />
      <line x1="2" y1="10" x2="18" y2="10" stroke="currentColor" strokeWidth={0.8} opacity={0.3} />
      <line x1="16" y1="8" x2="20" y2="12" stroke="currentColor" strokeWidth={1} opacity={0.3} />
      <line x1="16" y1="12" x2="20" y2="8" stroke="currentColor" strokeWidth={1} opacity={0.3} />
      <line x1="2" y1="15" x2="18" y2="15" stroke="currentColor" strokeWidth={0.8} opacity={0.3} />
      <line x1="16" y1="13" x2="20" y2="17" stroke="currentColor" strokeWidth={1} opacity={0.3} />
      <line x1="16" y1="17" x2="20" y2="13" stroke="currentColor" strokeWidth={1} opacity={0.3} />
    </svg>
  );
}

function WeightedScoreDiagram() {
  return (
    <svg width={32} height={20} viewBox="0 0 32 20" fill="none" aria-hidden="true">
      <rect x="2" y="12" width="6" height="6" fill="currentColor" opacity={0.5} />
      <rect x="11" y="6" width="6" height="12" fill="currentColor" opacity={0.8} />
      <rect x="20" y="9" width="6" height="9" fill="currentColor" opacity={0.65} />
      <line x1="1" y1="19" x2="31" y2="19" stroke="currentColor" strokeWidth={0.8} />
    </svg>
  );
}

const FAN_IN_DIAGRAMS: Record<FanInStrategy, React.ReactElement> = {
  all_must_pass: <AllMustPassDiagram />,
  any_passes: <AnyPassesDiagram />,
  quorum: <QuorumDiagram />,
  merge: <MergeDiagram />,
  first_wins: <FirstWinsDiagram />,
  weighted_score: <WeightedScoreDiagram />,
};

// ── Form state ─────────────────────────────────────────────────────────────

function detailToRequest(d: PersonaDetail): PersonaCreateRequest {
  return {
    name: d.name,
    role: d.role,
    letter: d.letter,
    color: d.color,
    summary: d.summary,
    description: d.description,
    systemPromptTemplate: d.systemPromptTemplate,
    allowedTools: d.allowedTools,
    forbiddenTools: d.forbiddenTools,
    permissionMode: d.permissionMode,
    executor: d.executor,
    iterationBudget: d.iterationBudget,
    llmPrimaryAlias: d.llm.primaryAlias,
    llmThinkingEnabled: d.llm.thinkingEnabled,
    llmMaxTokens: d.llm.maxTokens,
    llmTemperature: d.llm.temperature,
    producesEventType: d.produces.eventType,
    producesSchema: d.produces.schemaDef,
    consumesEvents: d.consumes.events,
    consumesSchema: d.consumes.schemaDef,
    fanInStrategy: d.fanIn?.strategy,
    fanInParams: d.fanIn?.params,
    mimirWriteRouting: d.mimirWriteRouting,
  };
}

function defaultCodexExecutor(): PersonaExecutor {
  return {
    adapter: CLI_EXECUTOR_ADAPTER,
    kwargs: {
      transport_adapter: CODEX_WS_TRANSPORT_ADAPTER,
      transport_kwargs: { model: '' },
    },
  };
}

function inferExecutorMode(executor: PersonaCreateRequest['executor']): ExecutorMode {
  if (!executor?.adapter) return 'ravn';
  if (
    executor.adapter === CLI_EXECUTOR_ADAPTER &&
    executor.kwargs?.transport_adapter === CODEX_WS_TRANSPORT_ADAPTER
  ) {
    return 'codex_ws';
  }
  return 'custom';
}

function normalizeExecutor(
  executor: PersonaCreateRequest['executor'],
): PersonaCreateRequest['executor'] {
  if (!executor) return undefined;
  const adapter = executor.adapter.trim();
  const kwargs = { ...(executor.kwargs ?? {}) };
  if (!adapter && Object.keys(kwargs).length === 0) return undefined;
  return { adapter, kwargs };
}

function getExecutorTransportAdapter(executor: PersonaCreateRequest['executor']): string {
  const value = executor?.kwargs?.transport_adapter;
  return typeof value === 'string' ? value : '';
}

function getExecutorTransportModel(executor: PersonaCreateRequest['executor']): string {
  const transportKwargs = executor?.kwargs?.transport_kwargs;
  if (!transportKwargs || typeof transportKwargs !== 'object' || Array.isArray(transportKwargs)) {
    return '';
  }
  const value = (transportKwargs as Record<string, unknown>).model;
  return typeof value === 'string' ? value : '';
}

// ── Section components ─────────────────────────────────────────────────────

interface SectionProps {
  title: string;
  children: React.ReactNode;
}

function Section({ title, subtitle, children }: SectionProps & { subtitle?: string }) {
  return (
    <section className="niuu-border niuu-border-border-subtle niuu-rounded-lg niuu-overflow-hidden">
      <div className="niuu-px-4 niuu-py-2 niuu-bg-bg-secondary niuu-border-b niuu-border-border-subtle niuu-flex niuu-items-baseline niuu-gap-3">
        <h3 className="niuu-m-0 niuu-text-xs niuu-font-mono niuu-font-medium niuu-text-text-muted niuu-uppercase niuu-tracking-widest">
          {title}
        </h3>
        {subtitle && (
          <span className="niuu-text-xs niuu-font-mono niuu-text-text-muted">{subtitle}</span>
        )}
      </div>
      <div className="niuu-p-4 niuu-flex niuu-flex-col niuu-gap-3 niuu-bg-bg-primary">
        {children}
      </div>
    </section>
  );
}

interface FieldRowProps {
  label: string;
  htmlFor?: string;
  children: React.ReactNode;
}

function FieldRow({ label, htmlFor, children }: FieldRowProps) {
  return (
    <div className="niuu-grid niuu-grid-cols-[140px_1fr] niuu-gap-3 niuu-items-start">
      <label
        htmlFor={htmlFor}
        className="niuu-pt-2 niuu-text-sm niuu-text-text-muted niuu-font-sans"
      >
        {label}
      </label>
      <div>{children}</div>
    </div>
  );
}

// ── Main form ──────────────────────────────────────────────────────────────

export interface PersonaFormProps {
  persona: PersonaDetail;
  onSave: (req: PersonaCreateRequest) => Promise<void>;
  isSaving?: boolean;
}

export function PersonaForm({ persona, onSave, isSaving = false }: PersonaFormProps) {
  const [form, setForm] = useState<PersonaCreateRequest>(() => detailToRequest(persona));
  const [dirty, setDirty] = useState(false);
  const [showAllowPicker, setShowAllowPicker] = useState(false);
  const [showDenyPicker, setShowDenyPicker] = useState(false);

  // Sync form when persona prop changes (navigating to another persona).
  // Intentionally depend only on persona.name — not the full persona object —
  // so saving (which triggers a re-fetch) doesn't reset the form mid-edit.
  useEffect(() => {
    setForm(detailToRequest(persona));
    setDirty(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona.name]);

  const validationErrors = validatePersona(form, SEED_EVENT_CATALOG);
  const executorMode = inferExecutorMode(form.executor);

  const update = useCallback(
    <K extends keyof PersonaCreateRequest>(key: K, value: PersonaCreateRequest[K]) => {
      setForm((prev) => ({ ...prev, [key]: value }));
      setDirty(true);
    },
    [],
  );

  const updateExecutor = useCallback(
    (patch: Partial<PersonaExecutor> | undefined) => {
      const current = form.executor ?? { adapter: '', kwargs: {} };
      const next = patch
        ? normalizeExecutor({
            adapter: patch.adapter ?? current.adapter,
            kwargs: patch.kwargs ?? current.kwargs,
          })
        : undefined;
      update('executor', next);
    },
    [form.executor, update],
  );

  const updateExecutorMode = useCallback(
    (mode: ExecutorMode) => {
      if (mode === 'ravn') {
        update('executor', undefined);
        return;
      }
      if (mode === 'codex_ws') {
        update('executor', defaultCodexExecutor());
        return;
      }
      update(
        'executor',
        normalizeExecutor(form.executor ?? { adapter: CLI_EXECUTOR_ADAPTER, kwargs: {} }),
      );
    },
    [form.executor, update],
  );

  const updateTransportAdapter = useCallback(
    (transportAdapter: string) => {
      const current = form.executor ?? { adapter: CLI_EXECUTOR_ADAPTER, kwargs: {} };
      updateExecutor({
        adapter: current.adapter || CLI_EXECUTOR_ADAPTER,
        kwargs: {
          ...current.kwargs,
          transport_adapter: transportAdapter,
        },
      });
    },
    [form.executor, updateExecutor],
  );

  const updateTransportModel = useCallback(
    (model: string) => {
      const current = form.executor ?? { adapter: CLI_EXECUTOR_ADAPTER, kwargs: {} };
      const transportKwargs =
        current.kwargs.transport_kwargs &&
        typeof current.kwargs.transport_kwargs === 'object' &&
        !Array.isArray(current.kwargs.transport_kwargs)
          ? { ...(current.kwargs.transport_kwargs as Record<string, unknown>) }
          : {};
      updateExecutor({
        adapter: current.adapter || CLI_EXECUTOR_ADAPTER,
        kwargs: {
          ...current.kwargs,
          transport_kwargs: {
            ...transportKwargs,
            model,
          },
        },
      });
    },
    [form.executor, updateExecutor],
  );

  const handleReset = useCallback(() => {
    setForm(detailToRequest(persona));
    setDirty(false);
  }, [persona]);

  const handleSave = useCallback(async () => {
    if (validationErrors.length > 0) return;
    await onSave(form);
    setDirty(false);
  }, [form, onSave, validationErrors]);

  // Consumes events helpers
  const addConsumedEvent = useCallback(() => {
    update('consumesEvents', [...form.consumesEvents, { name: '' }]);
  }, [form.consumesEvents, update]);

  const updateConsumedEvent = useCallback(
    (i: number, patch: Partial<PersonaConsumesEvent>) => {
      const next = form.consumesEvents.map((e, idx) => (idx === i ? { ...e, ...patch } : e));
      update('consumesEvents', next);
    },
    [form.consumesEvents, update],
  );

  const removeConsumedEvent = useCallback(
    (i: number) => {
      update(
        'consumesEvents',
        form.consumesEvents.filter((_, idx) => idx !== i),
      );
    },
    [form.consumesEvents, update],
  );

  return (
    <div className="rv-persona-form" data-testid="persona-form">
      {/* Save bar */}
      {dirty && (
        <div className="niuu-flex niuu-items-center niuu-justify-between niuu-px-4 niuu-py-2 niuu-bg-bg-secondary niuu-border-b niuu-border-border">
          <span className="niuu-text-sm niuu-text-text-muted">Unsaved changes</span>
          <div className="niuu-flex niuu-gap-2">
            <button
              type="button"
              onClick={handleReset}
              className="niuu-px-3 niuu-py-1 niuu-text-sm niuu-text-text-secondary niuu-bg-transparent niuu-border niuu-border-border niuu-rounded-md niuu-cursor-pointer hover:niuu-bg-bg-tertiary"
            >
              Reset
            </button>
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={isSaving || validationErrors.length > 0}
              aria-label="Save persona"
              className="niuu-px-3 niuu-py-1 niuu-text-sm niuu-text-text-primary niuu-bg-brand niuu-border niuu-border-transparent niuu-rounded-md niuu-cursor-pointer disabled:niuu-opacity-50"
            >
              {isSaving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      )}

      <div className="rv-persona-form__content">
        {validationErrors.length > 0 && (
          <ValidationSummary
            errors={validationErrors.map((e) => ({
              id: e.field,
              label: e.field,
              message: e.message,
            }))}
          />
        )}

        {/* Identity — full-width fields with mono labels, matching web2 */}
        <Section title="Identity" subtitle="What this persona is for.">
          <label className="rv-pf-field">
            <span className="rv-pf-field__label">name</span>
            <input className="niuu-form-control niuu-font-mono" value={form.name} readOnly />
          </label>
          <label className="rv-pf-field">
            <span className="rv-pf-field__label">role</span>
            <input
              className="niuu-form-control niuu-font-mono"
              value={form.role}
              onChange={(e) => update('role', e.target.value as PersonaRole)}
            />
          </label>
          <label className="rv-pf-field">
            <span className="rv-pf-field__label">summary</span>
            <input
              className="niuu-form-control"
              value={form.summary}
              onChange={(e) => update('summary', e.target.value)}
            />
          </label>
          <label className="rv-pf-field">
            <span className="rv-pf-field__label">description</span>
            <input
              className="niuu-form-control"
              value={form.description}
              onChange={(e) => update('description', e.target.value)}
            />
          </label>
          <label className="rv-pf-field">
            <span className="rv-pf-field__label">system_prompt_template</span>
            <textarea
              className="niuu-form-control niuu-font-mono"
              value={form.systemPromptTemplate}
              onChange={(e) => update('systemPromptTemplate', e.target.value)}
              rows={10}
              spellCheck={false}
            />
          </label>
        </Section>

        {/* Runtime — combined section matching web2 (iteration_budget + permission_mode + LLM) */}
        <Section title="Runtime" subtitle="Iteration budget, permissions and LLM config.">
          <div className="rv-pf-grid-3">
            <label className="rv-pf-field">
              <span className="rv-pf-field__label">iteration_budget</span>
              <input
                type="number"
                className="niuu-form-control niuu-font-mono"
                value={form.iterationBudget}
                min={1}
                max={500}
                onChange={(e) => update('iterationBudget', parseInt(e.target.value, 10) || 25)}
              />
            </label>
            <label className="rv-pf-field">
              <span className="rv-pf-field__label">permission_mode</span>
              <select
                className="niuu-form-control niuu-font-mono"
                value={form.permissionMode}
                onChange={(e) => update('permissionMode', e.target.value)}
              >
                {PERMISSION_MODES.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>
            <label className="rv-pf-field">
              <span className="rv-pf-field__label">llm.alias</span>
              <select
                className="niuu-form-control niuu-font-mono"
                value={form.llmPrimaryAlias}
                onChange={(e) => update('llmPrimaryAlias', e.target.value)}
              >
                <option value="claude-sonnet-4-6">sonnet-primary</option>
                <option value="claude-opus-4-6">opus-primary</option>
                <option value="claude-haiku-4-5">haiku-primary</option>
              </select>
            </label>
          </div>
          <div className="rv-pf-grid-2">
            <label className="rv-pf-field">
              <span className="rv-pf-field__label">llm.thinking</span>
              <button
                type="button"
                className={`rv-pf-toggle ${form.llmThinkingEnabled ? 'rv-pf-toggle--on' : ''}`}
                onClick={() => update('llmThinkingEnabled', !form.llmThinkingEnabled)}
              >
                <span className="rv-pf-toggle__knob" />
                <span className="rv-pf-toggle__label">
                  {form.llmThinkingEnabled ? 'true' : 'false'}
                </span>
              </button>
            </label>
            <label className="rv-pf-field">
              <span className="rv-pf-field__label">llm.max_tokens</span>
              <input
                type="number"
                className="niuu-form-control niuu-font-mono"
                value={form.llmMaxTokens}
                min={1}
                step={1024}
                onChange={(e) => update('llmMaxTokens', parseInt(e.target.value, 10) || 8192)}
              />
            </label>
          </div>
        </Section>

        <Section title="Execution" subtitle="How this persona actually runs turns.">
          <div className="rv-pf-grid-2">
            <label className="rv-pf-field">
              <span className="rv-pf-field__label">execution_mode</span>
              <select
                className="niuu-form-control niuu-font-mono"
                value={executorMode}
                onChange={(e) => updateExecutorMode(e.target.value as ExecutorMode)}
              >
                <option value="ravn">embedded ravn agent</option>
                <option value="codex_ws">codex streaming</option>
                <option value="custom">custom executor</option>
              </select>
            </label>
            {executorMode !== 'ravn' && (
              <label className="rv-pf-field">
                <span className="rv-pf-field__label">executor.adapter</span>
                <input
                  className="niuu-form-control niuu-font-mono"
                  value={form.executor?.adapter ?? ''}
                  onChange={(e) => updateExecutor({ adapter: e.target.value })}
                />
              </label>
            )}
          </div>
          {executorMode !== 'ravn' && (
            <div className="rv-pf-grid-2">
              <label className="rv-pf-field">
                <span className="rv-pf-field__label">transport_adapter</span>
                <input
                  className="niuu-form-control niuu-font-mono"
                  value={getExecutorTransportAdapter(form.executor)}
                  onChange={(e) => updateTransportAdapter(e.target.value)}
                />
              </label>
              <label className="rv-pf-field">
                <span className="rv-pf-field__label">transport.model</span>
                <input
                  className="niuu-form-control niuu-font-mono"
                  value={getExecutorTransportModel(form.executor)}
                  onChange={(e) => updateTransportModel(e.target.value)}
                  placeholder="inherit transport default"
                />
              </label>
            </div>
          )}
        </Section>

        {/* Tool access */}
        <Section
          title="Tool access"
          subtitle={`Enforced at dispatch. Destructive tools (${form.allowedTools.filter((t) => SEED_TOOL_REGISTRY.find((r) => r.id === t)?.destructive).length} granted) require permission_mode ≥ normal.`}
        >
          <FieldRow label={`allowed (${form.allowedTools.length})`}>
            <div className="niuu-flex niuu-flex-wrap niuu-gap-1 niuu-mb-2">
              {form.allowedTools.map((toolId) => {
                const tool = SEED_TOOL_REGISTRY.find((t) => t.id === toolId);
                return (
                  <span
                    key={toolId}
                    className={cn(
                      'niuu-inline-flex niuu-items-center niuu-gap-1 niuu-px-2 niuu-py-0 niuu-rounded niuu-text-xs niuu-font-mono',
                      tool?.destructive
                        ? 'niuu-bg-critical/10 niuu-text-critical niuu-border niuu-border-critical/30'
                        : 'niuu-bg-bg-tertiary niuu-text-text-secondary niuu-border niuu-border-border',
                    )}
                  >
                    {tool?.destructive && (
                      <span className="niuu-inline-block niuu-w-1.5 niuu-h-1.5 niuu-rounded-full niuu-bg-critical" />
                    )}
                    {toolId}
                    <button
                      type="button"
                      aria-label={`Remove ${toolId} from allow list`}
                      onClick={() =>
                        update(
                          'allowedTools',
                          form.allowedTools.filter((t) => t !== toolId),
                        )
                      }
                      className="niuu-ml-0.5 niuu-text-text-muted hover:niuu-text-text-primary niuu-border-0 niuu-bg-transparent niuu-cursor-pointer"
                    >
                      ×
                    </button>
                  </span>
                );
              })}
            </div>
            <button
              type="button"
              onClick={() => setShowAllowPicker(true)}
              className="niuu-text-sm niuu-text-text-secondary niuu-border niuu-border-dashed niuu-border-border niuu-px-2 niuu-py-0.5 niuu-rounded niuu-cursor-pointer hover:niuu-border-brand hover:niuu-text-text-primary niuu-bg-transparent"
            >
              + grant tool
            </button>
          </FieldRow>
          <FieldRow label={`forbidden (${form.forbiddenTools.length})`}>
            <div className="niuu-flex niuu-flex-wrap niuu-gap-1 niuu-mb-2">
              {form.forbiddenTools.map((toolId) => (
                <span
                  key={toolId}
                  className="niuu-inline-flex niuu-items-center niuu-gap-1 niuu-px-2 niuu-py-0 niuu-rounded niuu-text-xs niuu-font-mono niuu-bg-bg-tertiary niuu-text-text-secondary niuu-border niuu-border-border"
                >
                  {toolId}
                  <button
                    type="button"
                    aria-label={`Remove ${toolId} from deny list`}
                    onClick={() =>
                      update(
                        'forbiddenTools',
                        form.forbiddenTools.filter((t) => t !== toolId),
                      )
                    }
                    className="niuu-ml-0.5 niuu-text-text-muted hover:niuu-text-text-primary niuu-border-0 niuu-bg-transparent niuu-cursor-pointer"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <button
              type="button"
              onClick={() => setShowDenyPicker(true)}
              className="niuu-text-sm niuu-text-text-secondary niuu-border niuu-border-dashed niuu-border-border niuu-px-2 niuu-py-0.5 niuu-rounded niuu-cursor-pointer hover:niuu-border-brand hover:niuu-text-text-primary niuu-bg-transparent"
            >
              + deny tool
            </button>
          </FieldRow>
        </Section>

        {/* Produces */}
        <Section
          title="Produces"
          subtitle="The event this persona emits on a successful iteration."
        >
          <FieldRow label="Event">
            <EventPicker
              value={form.producesEventType}
              onChange={(v) => update('producesEventType', v)}
              catalog={SEED_EVENT_CATALOG}
              allowNew
              allowEmpty
            />
          </FieldRow>
          {form.producesEventType && (
            <FieldRow label="Schema">
              <SchemaEditor
                value={form.producesSchema}
                onChange={(v) => update('producesSchema', v as Record<string, FieldType>)}
              />
            </FieldRow>
          )}
        </Section>

        {/* Consumes */}
        <Section
          title="Consumes"
          subtitle="Events this persona listens for, and the structured inputs it expects."
        >
          <div className="niuu-flex niuu-flex-col niuu-gap-3">
            {form.consumesEvents.map((ev, i) => (
              <div
                key={i}
                className="niuu-flex niuu-items-start niuu-gap-2 niuu-p-3 niuu-bg-bg-secondary niuu-rounded-md niuu-border niuu-border-border-subtle"
              >
                <div className="niuu-flex-1 niuu-flex niuu-flex-col niuu-gap-2">
                  <EventPicker
                    value={ev.name}
                    onChange={(v) => updateConsumedEvent(i, { name: v })}
                    catalog={SEED_EVENT_CATALOG}
                  />
                  <input
                    className="niuu-form-control niuu-font-mono niuu-text-xs"
                    value={ev.injects?.join(', ') ?? ''}
                    onChange={(e) => {
                      const injects = e.target.value
                        ? e.target.value
                            .split(',')
                            .map((s) => s.trim())
                            .filter(Boolean)
                        : [];
                      updateConsumedEvent(i, { injects });
                    }}
                    placeholder="inject keys (comma-separated)"
                    aria-label="Inject keys"
                  />
                  <input
                    type="number"
                    className="niuu-form-control niuu-w-28"
                    value={ev.trust ?? ''}
                    min={0}
                    max={1}
                    step={0.1}
                    onChange={(e) => {
                      const t = parseFloat(e.target.value);
                      updateConsumedEvent(i, { trust: isNaN(t) ? undefined : t });
                    }}
                    placeholder="trust (0–1)"
                    aria-label="Producer trust threshold"
                  />
                </div>
                <button
                  type="button"
                  onClick={() => removeConsumedEvent(i)}
                  aria-label={`Remove consumed event ${ev.name}`}
                  className="niuu-border-0 niuu-bg-transparent niuu-cursor-pointer niuu-text-text-muted hover:niuu-text-critical niuu-mt-1 niuu-p-1"
                >
                  ✕
                </button>
              </div>
            ))}
            <button
              type="button"
              onClick={addConsumedEvent}
              className="niuu-text-sm niuu-text-text-secondary niuu-border niuu-border-dashed niuu-border-border niuu-px-3 niuu-py-1.5 niuu-rounded niuu-cursor-pointer hover:niuu-border-brand hover:niuu-text-text-primary niuu-bg-transparent niuu-self-start"
            >
              + Add consumed event
            </button>
          </div>
          <FieldRow label="Input schema">
            <SchemaEditor
              value={form.consumesSchema}
              onChange={(v) => update('consumesSchema', v as Record<string, FieldType>)}
            />
          </FieldRow>
        </Section>

        {/* Fan-in */}
        <Section
          title="Fan-in"
          subtitle="How Týr combines this persona's output with others emitting the same event."
        >
          <div className="rv-fanin-grid" data-testid="fanin-cards">
            {FAN_IN_STRATEGIES.map((s) => {
              const isActive = form.fanInStrategy === s;
              return (
                <button
                  key={s}
                  type="button"
                  data-testid={`fanin-card-${s}`}
                  aria-pressed={isActive}
                  onClick={() => {
                    setForm((prev) => ({ ...prev, fanInStrategy: s, fanInParams: undefined }));
                    setDirty(true);
                  }}
                  className={`rv-fanin-card${isActive ? ' rv-fanin-card--active' : ''}`}
                >
                  <span className="rv-fanin-card__diagram">{FAN_IN_DIAGRAMS[s]}</span>
                  <span className="rv-fanin-card__name">{s}</span>
                  <span className="rv-fanin-card__desc">{FAN_IN_DESCRIPTIONS[s]}</span>
                </button>
              );
            })}
          </div>

          {/* "None" option */}
          <button
            type="button"
            onClick={() => update('fanInStrategy', undefined)}
            className={cn(
              'niuu-mt-2 niuu-text-xs niuu-text-text-muted niuu-bg-transparent niuu-border-0',
              'niuu-cursor-pointer niuu-px-0 hover:niuu-text-text-secondary niuu-self-start',
              !form.fanInStrategy && 'niuu-underline',
            )}
          >
            {form.fanInStrategy ? 'Clear strategy (none)' : '— no strategy selected —'}
          </button>

          {/* Quorum params */}
          {form.fanInStrategy === 'quorum' && (
            <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-mt-2">
              <label
                htmlFor="pf-fanin-quorum"
                className="niuu-text-sm niuu-text-text-muted niuu-font-sans"
              >
                Quorum count
              </label>
              <input
                id="pf-fanin-quorum"
                type="number"
                className="niuu-form-control niuu-w-24"
                min={1}
                value={(form.fanInParams as Record<string, number> | undefined)?.quorum ?? 2}
                onChange={(e) =>
                  update('fanInParams', {
                    ...form.fanInParams,
                    quorum: parseInt(e.target.value, 10) || 2,
                  })
                }
              />
            </div>
          )}

          {/* Weighted score params */}
          {form.fanInStrategy === 'weighted_score' && (
            <div className="niuu-flex niuu-items-center niuu-gap-3 niuu-mt-2">
              <label
                htmlFor="pf-fanin-threshold"
                className="niuu-text-sm niuu-text-text-muted niuu-font-sans"
              >
                Min score threshold
              </label>
              <input
                id="pf-fanin-threshold"
                type="number"
                className="niuu-form-control niuu-w-24"
                min={0}
                max={1}
                step={0.1}
                value={(form.fanInParams as Record<string, number> | undefined)?.threshold ?? 0.5}
                onChange={(e) =>
                  update('fanInParams', {
                    ...form.fanInParams,
                    threshold: parseFloat(e.target.value) || 0.5,
                  })
                }
              />
            </div>
          )}
        </Section>

        {/* Mímir write routing */}
        <Section title="Mímir write routing">
          <FieldRow label="Route writes to">
            <div className="niuu-flex niuu-items-center niuu-gap-2">
              {MIMIR_ROUTINGS.map((r) => (
                <label
                  key={r}
                  className="niuu-flex niuu-items-center niuu-gap-1.5 niuu-cursor-pointer niuu-select-none"
                >
                  <input
                    type="radio"
                    name="mimir-routing"
                    value={r}
                    checked={form.mimirWriteRouting === r}
                    onChange={() => update('mimirWriteRouting', r)}
                    className="niuu-w-3.5 niuu-h-3.5"
                  />
                  <MountChip name={r} role={r} />
                </label>
              ))}
              <label className="niuu-flex niuu-items-center niuu-gap-1.5 niuu-cursor-pointer niuu-select-none">
                <input
                  type="radio"
                  name="mimir-routing"
                  value=""
                  checked={form.mimirWriteRouting === undefined}
                  onChange={() => update('mimirWriteRouting', undefined)}
                  className="niuu-w-3.5 niuu-h-3.5"
                />
                <span className="niuu-text-sm niuu-text-text-muted">inherit global default</span>
              </label>
            </div>
          </FieldRow>
        </Section>

        {/* Iteration budget is now inside Runtime section */}
      </div>

      {/* ToolPicker modals */}
      <ToolPicker
        open={showAllowPicker}
        onOpenChange={setShowAllowPicker}
        registry={SEED_TOOL_REGISTRY}
        selected={form.allowedTools}
        excluded={form.forbiddenTools}
        onToggle={(id) => {
          update(
            'allowedTools',
            form.allowedTools.includes(id)
              ? form.allowedTools.filter((t) => t !== id)
              : [...form.allowedTools, id],
          );
        }}
        label="Allow list — select tools"
      />
      <ToolPicker
        open={showDenyPicker}
        onOpenChange={setShowDenyPicker}
        registry={SEED_TOOL_REGISTRY}
        selected={form.forbiddenTools}
        excluded={form.allowedTools}
        onToggle={(id) => {
          update(
            'forbiddenTools',
            form.forbiddenTools.includes(id)
              ? form.forbiddenTools.filter((t) => t !== id)
              : [...form.forbiddenTools, id],
          );
        }}
        label="Deny list — select tools"
      />
    </div>
  );
}
