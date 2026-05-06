import { useState } from 'react';
import type { PersonaDetail, PersonaCreateRequest } from '../../api/types';
import { ToolBadge } from '../ToolBadge';
import styles from './PersonaForm.module.css';

interface PersonaFormProps {
  initial?: PersonaDetail;
  onSubmit: (req: PersonaCreateRequest) => Promise<void>;
  onCancel: () => void;
  submitLabel?: string;
}

const PERMISSION_MODES = ['read-only', 'workspace-write', 'workspace-full', ''];
const FAN_IN_STRATEGIES = ['merge', 'all_must_pass', 'any_pass', 'majority'];

function parseToolList(raw: string): string[] {
  return raw
    .split(',')
    .map(s => s.trim())
    .filter(Boolean);
}

export function PersonaForm({
  initial,
  onSubmit,
  onCancel,
  submitLabel = 'Save',
}: PersonaFormProps) {
  const [name, setName] = useState(initial?.name ?? '');
  const [systemPrompt, setSystemPrompt] = useState(initial?.systemPromptTemplate ?? '');
  const [allowedTools, setAllowedTools] = useState(initial?.allowedTools.join(', ') ?? '');
  const [forbiddenTools, setForbiddenTools] = useState(initial?.forbiddenTools.join(', ') ?? '');
  const [permissionMode, setPermissionMode] = useState(initial?.permissionMode ?? '');
  const [iterationBudget, setIterationBudget] = useState(String(initial?.iterationBudget ?? 0));
  const [llmAlias, setLlmAlias] = useState(initial?.llm.primaryAlias ?? '');
  const [llmThinking, setLlmThinking] = useState(initial?.llm.thinkingEnabled ?? false);
  const [llmMaxTokens, setLlmMaxTokens] = useState(String(initial?.llm.maxTokens ?? 0));
  const [producesEvent, setProducesEvent] = useState(initial?.produces.eventType ?? '');
  const [consumesEvents, setConsumesEvents] = useState(
    initial?.consumes.eventTypes.join(', ') ?? ''
  );
  const [consumesInjects, setConsumesInjects] = useState(
    initial?.consumes.injects.join(', ') ?? ''
  );
  const [fanInStrategy, setFanInStrategy] = useState(initial?.fanIn.strategy ?? 'merge');
  const [fanInContributesTo, setFanInContributesTo] = useState(initial?.fanIn.contributesTo ?? '');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  function validate(): boolean {
    const newErrors: Record<string, string> = {};

    if (!name.trim()) {
      newErrors.name = 'Name is required';
    } else if (!/^[a-zA-Z0-9_-]+$/.test(name.trim())) {
      newErrors.name = 'Name must contain only letters, numbers, hyphens, underscores';
    }

    if (!systemPrompt.trim()) {
      newErrors.systemPrompt = 'System prompt is required';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!validate()) return;

    const req: PersonaCreateRequest = {
      name: name.trim(),
      systemPromptTemplate: systemPrompt,
      allowedTools: parseToolList(allowedTools),
      forbiddenTools: parseToolList(forbiddenTools),
      permissionMode,
      iterationBudget: parseInt(iterationBudget, 10) || 0,
      llmPrimaryAlias: llmAlias,
      llmThinkingEnabled: llmThinking,
      llmMaxTokens: parseInt(llmMaxTokens, 10) || 0,
      producesEventType: producesEvent,
      consumesEventTypes: parseToolList(consumesEvents),
      consumesInjects: parseToolList(consumesInjects),
      fanInStrategy,
      fanInContributesTo,
    };

    setSubmitting(true);
    try {
      await onSubmit(req);
    } finally {
      setSubmitting(false);
    }
  }

  const parsedAllowedTools = parseToolList(allowedTools);

  return (
    <form className={styles.form} onSubmit={handleSubmit} noValidate>
      {/* Identity */}
      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>Identity</h3>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="pf-name">
            Name
          </label>
          <input
            id="pf-name"
            className={errors.name ? styles.inputError : styles.input}
            type="text"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="my-persona"
            disabled={!!initial}
          />
          {errors.name && <span className={styles.error}>{errors.name}</span>}
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="pf-permission">
            Permission Mode
          </label>
          <select
            id="pf-permission"
            className={styles.select}
            value={permissionMode}
            onChange={e => setPermissionMode(e.target.value)}
          >
            {PERMISSION_MODES.map(m => (
              <option key={m} value={m}>
                {m || '(inherit)'}
              </option>
            ))}
          </select>
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="pf-budget">
            Iteration Budget
          </label>
          <input
            id="pf-budget"
            className={styles.input}
            type="number"
            min="0"
            value={iterationBudget}
            onChange={e => setIterationBudget(e.target.value)}
          />
          <span className={styles.hint}>0 = unlimited</span>
        </div>
      </section>

      {/* System Prompt */}
      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>System Prompt</h3>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="pf-prompt">
            Template
          </label>
          <textarea
            id="pf-prompt"
            className={errors.systemPrompt ? styles.textareaError : styles.textarea}
            value={systemPrompt}
            onChange={e => setSystemPrompt(e.target.value)}
            rows={10}
            placeholder="You are a focused agent that…"
          />
          {errors.systemPrompt && <span className={styles.error}>{errors.systemPrompt}</span>}
        </div>
      </section>

      {/* Tools & Permissions */}
      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>Tools & Permissions</h3>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="pf-allowed">
            Allowed Tools
          </label>
          <input
            id="pf-allowed"
            className={styles.input}
            type="text"
            value={allowedTools}
            onChange={e => setAllowedTools(e.target.value)}
            placeholder="file, git, terminal, web"
          />
          <span className={styles.hint}>Comma-separated tool group names</span>
          {parsedAllowedTools.length > 0 && (
            <div className={styles.toolPreview}>
              {parsedAllowedTools.map(t => (
                <ToolBadge key={t} tool={t} />
              ))}
            </div>
          )}
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="pf-forbidden">
            Forbidden Tools
          </label>
          <input
            id="pf-forbidden"
            className={styles.input}
            type="text"
            value={forbiddenTools}
            onChange={e => setForbiddenTools(e.target.value)}
            placeholder="cascade, volundr"
          />
          <span className={styles.hint}>Comma-separated tool group names</span>
        </div>
      </section>

      {/* LLM Settings */}
      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>LLM Settings</h3>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="pf-alias">
            Primary Alias
          </label>
          <input
            id="pf-alias"
            className={styles.input}
            type="text"
            value={llmAlias}
            onChange={e => setLlmAlias(e.target.value)}
            placeholder="balanced"
          />
        </div>

        <div className={styles.fieldRow}>
          <label className={styles.checkboxLabel}>
            <input
              type="checkbox"
              className={styles.checkbox}
              checked={llmThinking}
              onChange={e => setLlmThinking(e.target.checked)}
            />
            Extended Thinking
          </label>
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="pf-maxtokens">
            Max Tokens
          </label>
          <input
            id="pf-maxtokens"
            className={styles.input}
            type="number"
            min="0"
            value={llmMaxTokens}
            onChange={e => setLlmMaxTokens(e.target.value)}
          />
          <span className={styles.hint}>0 = use settings default</span>
        </div>
      </section>

      {/* Pipeline Contract */}
      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>Pipeline Contract</h3>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="pf-produces">
            Produces Event
          </label>
          <input
            id="pf-produces"
            className={styles.input}
            type="text"
            value={producesEvent}
            onChange={e => setProducesEvent(e.target.value)}
            placeholder="review.completed"
          />
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="pf-consumes">
            Consumes Events
          </label>
          <input
            id="pf-consumes"
            className={styles.input}
            type="text"
            value={consumesEvents}
            onChange={e => setConsumesEvents(e.target.value)}
            placeholder="code.changed, review.requested"
          />
          <span className={styles.hint}>Comma-separated</span>
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="pf-injects">
            Context Injects
          </label>
          <input
            id="pf-injects"
            className={styles.input}
            type="text"
            value={consumesInjects}
            onChange={e => setConsumesInjects(e.target.value)}
            placeholder="repo, branch, diff_url"
          />
          <span className={styles.hint}>Comma-separated</span>
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="pf-fanin">
            Fan-in Strategy
          </label>
          <select
            id="pf-fanin"
            className={styles.select}
            value={fanInStrategy}
            onChange={e => setFanInStrategy(e.target.value)}
          >
            {FAN_IN_STRATEGIES.map(s => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>

        <div className={styles.field}>
          <label className={styles.label} htmlFor="pf-contributes">
            Contributes To
          </label>
          <input
            id="pf-contributes"
            className={styles.input}
            type="text"
            value={fanInContributesTo}
            onChange={e => setFanInContributesTo(e.target.value)}
            placeholder="review.verdict"
          />
        </div>
      </section>

      {/* Actions */}
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.cancelButton}
          onClick={onCancel}
          disabled={submitting}
        >
          Cancel
        </button>
        <button type="submit" className={styles.submitButton} disabled={submitting}>
          {submitting ? 'Saving…' : submitLabel}
        </button>
      </div>
    </form>
  );
}
