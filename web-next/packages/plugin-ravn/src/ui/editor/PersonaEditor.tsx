import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Check, X } from 'lucide-react';
import { ErrorState, LoadingState } from '@niuulabs/ui';
import { useService } from '@niuulabs/plugin-sdk';
import type { PersonaSummary } from '@niuulabs/domain';
import type { IPersonaStore, PersonaDocument } from '../../ports';
import {
  PERMISSIONS,
  blankPersonaDocument,
  documentDescription,
  documentInstructions,
  documentName,
  documentPermission,
  documentProblems,
  documentYaml,
  eventName,
  knownEvents,
  newOutcome,
  outcomeName,
  outcomesOf,
  parseDocumentYaml,
  renameOutcome,
  startFrom,
  startsOn,
  withField,
  withName,
  withOutcomes,
  withStartsOn,
  type DocumentProblem,
} from '../../application/personaDocument';
import { usePersonaDocument, usePersonaYamlCheck, useSavePersonaYaml } from '../usePersona';
import { YamlCode } from '../PersonaYaml';
import { errorText } from '../library/errorText';
import { PersonaCardPreview } from './PersonaCardPreview';
import './editor.css';

type StepId = 'start' | 'name' | 'permission' | DocumentProblem['step'] | 'review';

const STEP_LABELS: Record<StepId, string> = {
  start: 'Start from',
  name: 'Name',
  permission: 'What it may change',
  starts: 'When it starts',
  finishes: 'How it finishes',
  instructions: 'Instructions',
  review: 'Review',
};

const CREATE_STEPS: StepId[] = [
  'start',
  'name',
  'permission',
  'starts',
  'finishes',
  'instructions',
  'review',
];
const EDIT_STEPS: StepId[] = CREATE_STEPS.filter((step) => step !== 'start');

/** How long typed YAML rests before the server is asked whether it can be saved. */
const CHECK_DELAY_MS = 400;

type EditorMode = 'wizard' | 'yaml';

export interface PersonaEditorProps {
  /** The persona being edited; absent, a new one is created. */
  name?: string;
  /** Whether that persona is a built-in, so saving writes an override. */
  isBuiltin?: boolean;
  personas: readonly PersonaSummary[];
  onSaved: (name: string) => void;
  onCancel: () => void;
}

/** Create or edit a persona, step by step or as its YAML. */
export function PersonaEditor(props: PersonaEditorProps) {
  const existing = usePersonaDocument(props.name ?? '');
  if (!props.name) return <EditorBody {...props} initial={blankPersonaDocument()} />;
  if (existing.isLoading) return <LoadingState label="Loading persona…" />;
  if (existing.isError || !existing.data) {
    return (
      <ErrorState
        title={`${props.name} could not be loaded`}
        message={errorText(existing.error, 'The persona service did not answer')}
      />
    );
  }
  return <EditorBody {...props} initial={existing.data} />;
}

function EditorBody({
  name: editing,
  isBuiltin,
  personas,
  onSaved,
  onCancel,
  initial,
}: PersonaEditorProps & { initial: PersonaDocument }) {
  const steps = editing ? EDIT_STEPS : CREATE_STEPS;
  const [doc, setDoc] = useState<PersonaDocument>(initial);
  const [mode, setMode] = useState<EditorMode>('wizard');
  const [source, setSource] = useState('');
  const [parseError, setParseError] = useState<string | null>(null);
  const [step, setStep] = useState<StepId>(steps[0]!);
  const [startedFrom, setStartedFrom] = useState('blank');
  const save = useSavePersonaYaml(editing ?? null);

  const yaml = mode === 'yaml' ? source : documentYaml(doc);
  const taken = useMemo(() => personas.map((persona) => persona.name), [personas]);
  const problems = useMemo(() => {
    const found = documentProblems(doc, { taken: editing ? undefined : taken });
    const docName = documentName(doc).trim();
    if (editing && docName !== editing) {
      found.unshift({
        step: 'name',
        message: `This is ${editing}; fork it to save it under another name.`,
      });
    }
    return found;
  }, [doc, editing, taken]);

  const [checked, setChecked] = useState(yaml);
  useEffect(() => {
    const timer = setTimeout(() => setChecked(yaml), CHECK_DELAY_MS);
    return () => clearTimeout(timer);
  }, [yaml]);
  const serverCheck = usePersonaYamlCheck(checked, !parseError && problems.length === 0);
  const serverErrors =
    serverCheck.data && !serverCheck.data.valid && checked === yaml ? serverCheck.data.errors : [];

  const blocked = Boolean(parseError) || problems.length > 0 || serverErrors.length > 0;
  const title = documentName(doc).trim() || (editing ?? 'New persona');

  function update(next: PersonaDocument) {
    setDoc(next);
    save.reset();
  }

  function switchMode(next: EditorMode) {
    if (next === mode) return;
    if (next === 'yaml') {
      setSource(documentYaml(doc));
      setParseError(null);
    }
    setMode(next);
  }

  function editSource(text: string) {
    setSource(text);
    save.reset();
    const parsed = parseDocumentYaml(text);
    if (!parsed.ok) {
      setParseError(parsed.error);
      return;
    }
    setParseError(null);
    setDoc(parsed.doc);
  }

  function submit() {
    if (blocked) return;
    save.mutate(yaml, { onSuccess: (saved) => onSaved(saved.name) });
  }

  return (
    <div className="rw-ed" data-testid="persona-editor" data-mode={mode}>
      <header className="rw-ed__head">
        <div className="rw-ed__who">
          <div className="rw-ed__crumb">{editing ? 'Edit persona' : 'New persona'}</div>
          <h1 className="rw-ed__title" data-testid="persona-editor-title">
            {title}
          </h1>
          <p className="rw-ed__lede">
            {mode === 'wizard'
              ? 'Answer a few questions; the YAML beside it is exactly what gets saved. Once you know the shape, switch to YAML and write it directly.'
              : 'The same persona as text. What it does is read back beside it as you type.'}
          </p>
        </div>
        <div className="rw-actions">
          <div className="rw-seg" role="group" aria-label="Edit with">
            <button
              type="button"
              aria-pressed={mode === 'wizard'}
              onClick={() => switchMode('wizard')}
              disabled={Boolean(parseError)}
              title={parseError ? 'Fix the YAML first' : undefined}
              data-testid="persona-editor-wizard"
            >
              Wizard
            </button>
            <button
              type="button"
              aria-pressed={mode === 'yaml'}
              onClick={() => switchMode('yaml')}
              data-testid="persona-editor-yaml"
            >
              YAML
            </button>
          </div>
          <button type="button" className="rw-btn" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="rw-btn rw-btn--primary"
            disabled={blocked || save.isPending}
            onClick={submit}
            data-testid="persona-editor-save"
          >
            {save.isPending ? 'Saving…' : 'Save persona'}
          </button>
        </div>
      </header>

      {editing && isBuiltin && (
        <p className="rw-ed__note" role="status">
          {editing} is built in. Saving writes your own version; the built-in stays one reset away.
        </p>
      )}
      {save.isError && (
        <p className="rw-inline-error" role="alert">
          {errorText(save.error, 'Saving failed')}
        </p>
      )}

      {mode === 'wizard' ? (
        <div className="rw-ed__grid">
          <Wizard
            steps={steps}
            step={step}
            onStep={setStep}
            doc={doc}
            onChange={update}
            personas={personas}
            problems={problems}
            serverErrors={serverErrors}
            onSave={submit}
            canSave={!blocked && !save.isPending}
            editing={editing}
            startedFrom={startedFrom}
            onStartedFrom={setStartedFrom}
          />
          <aside className="rw-ed__side">
            <section className="rw-ed__panel" aria-label="Its card in a workflow">
              <PanelHead title="In a workflow" hint="How its card looks in the builder" />
              <PersonaCardPreview doc={doc} />
            </section>
            <section className="rw-ed__panel" aria-label="YAML preview">
              <PanelHead
                title={`${documentName(doc).trim() || 'new-persona'}.yaml`}
                hint="Exactly what gets saved"
              />
              <div className="rw-ed__yaml">
                <YamlCode text={yaml} testId="persona-editor-yaml-preview" />
              </div>
            </section>
          </aside>
        </div>
      ) : (
        <div className="rw-ed__grid">
          <section className="rw-ed__panel rw-ed__panel--source" aria-label="Persona YAML">
            <PanelHead
              title={`${documentName(doc).trim() || 'new-persona'}.yaml`}
              hint="Edit freely. The wizard reads it back."
            />
            <textarea
              className="rw-ed__source"
              value={source}
              onChange={(event) => editSource(event.target.value)}
              spellCheck={false}
              wrap="off"
              aria-label="Persona YAML"
              data-testid="persona-editor-source"
            />
            <YamlStatus
              parseError={parseError}
              problems={problems}
              serverErrors={serverErrors}
              checking={serverCheck.isFetching}
            />
          </section>
          <aside className="rw-ed__side">
            <section className="rw-ed__panel" aria-label="What this persona does">
              <PanelHead title="In a workflow" hint="How its card looks in the builder" />
              <PersonaCardPreview doc={doc} />
              <Summary doc={doc} />
            </section>
          </aside>
        </div>
      )}
    </div>
  );
}

function PanelHead({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="rw-ed__panel-head">
      <b>{title}</b>
      <span>{hint}</span>
    </div>
  );
}

function YamlStatus({
  parseError,
  problems,
  serverErrors,
  checking,
}: {
  parseError: string | null;
  problems: DocumentProblem[];
  serverErrors: string[];
  checking: boolean;
}) {
  const messages = parseError
    ? [parseError]
    : [...problems.map((problem) => problem.message), ...serverErrors];
  if (messages.length === 0) {
    return (
      <div className="rw-ed__status" data-ok="true" data-testid="persona-editor-status">
        {checking ? 'Checking…' : 'Valid persona'}
      </div>
    );
  }
  return (
    <ul className="rw-ed__status" data-ok="false" data-testid="persona-editor-status">
      {messages.map((message) => (
        <li key={message}>{message}</li>
      ))}
    </ul>
  );
}

// ── Wizard ──────────────────────────────────────────────────────────────────────

interface WizardProps {
  steps: StepId[];
  step: StepId;
  onStep: (step: StepId) => void;
  doc: PersonaDocument;
  onChange: (doc: PersonaDocument) => void;
  personas: readonly PersonaSummary[];
  problems: DocumentProblem[];
  serverErrors: string[];
  onSave: () => void;
  canSave: boolean;
  editing?: string;
  startedFrom: string;
  onStartedFrom: (name: string) => void;
}

function Wizard({
  steps,
  step,
  onStep,
  doc,
  onChange,
  personas,
  problems,
  serverErrors,
  onSave,
  canSave,
  editing,
  startedFrom,
  onStartedFrom,
}: WizardProps) {
  const index = steps.indexOf(step);
  const last = index === steps.length - 1;
  const stepHasProblem = (id: StepId) => problems.some((problem) => problem.step === id);

  return (
    <section className="rw-ed__panel rw-ed__wizard" aria-label="Wizard">
      <nav className="rw-ed__steps" aria-label="Steps">
        {steps.map((id, i) => (
          <button
            key={id}
            type="button"
            aria-current={id === step ? 'step' : undefined}
            data-done={i < index ? 'true' : undefined}
            data-problem={i < index && stepHasProblem(id) ? 'true' : undefined}
            onClick={() => onStep(id)}
            data-testid={`persona-step-${id}`}
          >
            <span className="rw-ed__step-dot">
              {i < index && !stepHasProblem(id) ? <Check size={11} aria-hidden="true" /> : i + 1}
            </span>
            {STEP_LABELS[id]}
          </button>
        ))}
      </nav>
      <div className="rw-ed__body" data-testid={`persona-step-body-${step}`}>
        {step === 'start' && (
          <StartStep
            doc={doc}
            onChange={onChange}
            personas={personas}
            chosen={startedFrom}
            onChosen={onStartedFrom}
          />
        )}
        {step === 'name' && <NameStep doc={doc} onChange={onChange} editing={editing} />}
        {step === 'permission' && <PermissionStep doc={doc} onChange={onChange} />}
        {step === 'starts' && <StartsStep doc={doc} onChange={onChange} personas={personas} />}
        {step === 'finishes' && <FinishesStep doc={doc} onChange={onChange} />}
        {step === 'instructions' && <InstructionsStep doc={doc} onChange={onChange} />}
        {step === 'review' && (
          <ReviewStep doc={doc} problems={problems} serverErrors={serverErrors} onStep={onStep} />
        )}
      </div>
      <div className="rw-ed__foot">
        <span className="rw-ed__pos">
          Step {index + 1} of {steps.length}
        </span>
        <div className="rw-ed__foot-actions">
          <button
            type="button"
            className="rw-btn"
            disabled={index === 0}
            onClick={() => onStep(steps[index - 1]!)}
          >
            Back
          </button>
          {last ? (
            <button
              type="button"
              className="rw-btn rw-btn--primary"
              disabled={!canSave}
              onClick={onSave}
              data-testid="persona-wizard-save"
            >
              Save persona
            </button>
          ) : (
            <button
              type="button"
              className="rw-btn rw-btn--primary"
              onClick={() => onStep(steps[index + 1]!)}
              data-testid="persona-wizard-next"
            >
              Next
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

function Question({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <>
      <h2 className="rw-ed__q">{title}</h2>
      {hint && <p className="rw-ed__hint">{hint}</p>}
      {children}
    </>
  );
}

function StartStep({
  doc,
  onChange,
  personas,
  chosen,
  onChosen: setChosen,
}: {
  doc: PersonaDocument;
  onChange: (doc: PersonaDocument) => void;
  personas: readonly PersonaSummary[];
  chosen: string;
  onChosen: (name: string) => void;
}) {
  const service = useService<IPersonaStore>('ravn.personas');
  const queryClient = useQueryClient();
  const [loading, setLoading] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  async function choose(name: string) {
    setFailed(null);
    if (name === 'blank') {
      setChosen('blank');
      onChange(withName(blankPersonaDocument(), documentName(doc)));
      return;
    }
    setLoading(name);
    try {
      const source = await queryClient.fetchQuery({
        queryKey: ['ravn', 'personas', name, 'document'],
        queryFn: () => service.getPersonaDocument(name),
      });
      setChosen(name);
      onChange(startFrom(source, doc));
    } catch (error) {
      setFailed(errorText(error, `${name} could not be loaded`));
    } finally {
      setLoading(null);
    }
  }

  return (
    <Question
      title="Start from"
      hint="Blank, or a copy of a persona you already have. You can change every part."
    >
      <div className="rw-ed__opts">
        <Option
          pressed={chosen === 'blank'}
          onClick={() => void choose('blank')}
          label="Blank"
          hint="Start empty and fill it in"
          testId="persona-start-blank"
        />
        {personas.map((persona) => (
          <Option
            key={persona.name}
            pressed={chosen === persona.name}
            onClick={() => void choose(persona.name)}
            label={loading === persona.name ? `${persona.name}…` : persona.name}
            hint={persona.summary}
            testId={`persona-start-${persona.name}`}
          />
        ))}
      </div>
      {failed && (
        <p className="rw-inline-error" role="alert">
          {failed}
        </p>
      )}
    </Question>
  );
}

function Option({
  pressed,
  onClick,
  label,
  hint,
  testId,
}: {
  pressed: boolean;
  onClick: () => void;
  label: string;
  hint: string;
  testId: string;
}) {
  return (
    <button
      type="button"
      className="rw-ed__opt"
      aria-pressed={pressed}
      onClick={onClick}
      data-testid={testId}
    >
      <b>{label}</b>
      <small>{hint}</small>
    </button>
  );
}

function NameStep({
  doc,
  onChange,
  editing,
}: {
  doc: PersonaDocument;
  onChange: (doc: PersonaDocument) => void;
  editing?: string;
}) {
  const name = documentName(doc);
  return (
    <Question title="What is it?">
      <label className="rw-field">
        <span className="rw-field__label">Name</span>
        <input
          className="rw-input"
          value={name}
          onChange={(event) => onChange(withName(doc, event.target.value))}
          placeholder="migration-reviewer"
          spellCheck={false}
          readOnly={Boolean(editing)}
          data-testid="persona-editor-name"
        />
      </label>
      <label className="rw-field">
        <span className="rw-field__label">What it is for</span>
        <textarea
          className="rw-input rw-ed__short"
          value={documentDescription(doc)}
          onChange={(event) => onChange(withField(doc, 'description', event.target.value))}
          placeholder="Checks a database migration before it runs and returns approve or request changes."
          rows={2}
          data-testid="persona-editor-description"
        />
      </label>
      <p className="rw-ed__note">
        {editing
          ? `To save it under another name, fork ${editing}.`
          : 'Lowercase words joined by dashes. '}
        The description is what a coordinator reads when it decides who to ask, so say when to use
        it.
      </p>
    </Question>
  );
}

function PermissionStep({
  doc,
  onChange,
}: {
  doc: PersonaDocument;
  onChange: (doc: PersonaDocument) => void;
}) {
  const current = documentPermission(doc).replace(/-/g, '_');
  return (
    <Question title="What may it change?">
      <div className="rw-ed__opts">
        {PERMISSIONS.map((permission) => (
          <Option
            key={permission.value}
            pressed={current === permission.value}
            onClick={() => onChange(withField(doc, 'permission_mode', permission.value))}
            label={permission.label}
            hint={permission.hint}
            testId={`persona-permission-${permission.value}`}
          />
        ))}
      </div>
      <p className="rw-ed__note">
        Tools and MCP servers come later. For now the harness on each workflow step (Claude Code,
        Codex) decides what it can run.
      </p>
    </Question>
  );
}

function AddRow({
  placeholder,
  label,
  button,
  onAdd,
  normalize,
  testId,
}: {
  placeholder: string;
  label: string;
  button: string;
  onAdd: (value: string) => void;
  normalize: (raw: string) => string;
  testId: string;
}) {
  const [value, setValue] = useState('');
  function add() {
    const normalized = normalize(value);
    if (!normalized) return;
    onAdd(normalized);
    setValue('');
  }
  return (
    <div className="rw-ed__add">
      <input
        className="rw-input"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event: KeyboardEvent<HTMLInputElement>) => {
          if (event.key !== 'Enter') return;
          event.preventDefault();
          add();
        }}
        placeholder={placeholder}
        aria-label={label}
        spellCheck={false}
        data-testid={`${testId}-input`}
      />
      <button type="button" className="rw-btn" onClick={add} data-testid={`${testId}-button`}>
        {button}
      </button>
    </div>
  );
}

function StartsStep({
  doc,
  onChange,
  personas,
}: {
  doc: PersonaDocument;
  onChange: (doc: PersonaDocument) => void;
  personas: readonly PersonaSummary[];
}) {
  const picked = startsOn(doc);
  const own = new Set(outcomesOf(doc).map((outcome) => outcome.event));
  const known = useMemo(
    () => knownEvents(personas.filter((persona) => persona.name !== documentName(doc))),
    [personas, doc],
  );
  const sentBy = new Map(known.map((entry) => [entry.event, entry.senders]));
  const suggestions = known.filter(
    (entry) => !picked.includes(entry.event) && !own.has(entry.event),
  );

  function add(event: string) {
    if (!picked.includes(event)) onChange(withStartsOn(doc, [...picked, event]));
  }

  return (
    <Question
      title="When does it start?"
      hint="Steps in a workflow hand work on by sending events, like code.changed when a coder pushes a change. This persona starts when one of the events below is sent."
    >
      <div className="rw-ed__picked" data-testid="persona-starts-picked">
        {picked.length === 0 && (
          <div className="rw-ed__empty">Nothing yet. Pick an event below, or name a new one.</div>
        )}
        {picked.map((event) => (
          <div className="rw-ed__pick" key={event}>
            <code>{event}</code>
            <span>
              {sentBy.has(event) ? (
                `Sent by ${sentBy.get(event)!.join(', ')}`
              ) : (
                <>
                  <span className="rw-ed__tag">new</span> Nothing sends this yet. A workflow step or
                  trigger has to send it before this persona can start.
                </>
              )}
            </span>
            <button
              type="button"
              className="rw-icon-btn"
              aria-label={`Remove ${event}`}
              onClick={() =>
                onChange(
                  withStartsOn(
                    doc,
                    picked.filter((e) => e !== event),
                  ),
                )
              }
            >
              <X size={14} aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
      {suggestions.length > 0 && (
        <>
          <div className="rw-ed__sublabel">Sent by something you already have</div>
          <div className="rw-ed__sugs">
            {suggestions.map((entry) => (
              <button
                key={entry.event}
                type="button"
                className="rw-ed__sug"
                onClick={() => add(entry.event)}
                data-testid={`persona-starts-suggest-${entry.event}`}
              >
                <code>{entry.event}</code>
                <small>{entry.senders.join(', ')}</small>
              </button>
            ))}
          </div>
        </>
      )}
      <div className="rw-ed__sublabel">Or name a new event</div>
      <AddRow
        placeholder="migration.requested"
        label="New event name"
        button="Add"
        normalize={eventName}
        onAdd={add}
        testId="persona-starts-add"
      />
      <p className="rw-ed__note">
        Add as many as you like. Names are lowercase words joined by dots: what it is about, then
        what happened.
      </p>
    </Question>
  );
}

function OutcomeNameInput({
  verdict,
  taken,
  onRename,
}: {
  verdict: string;
  taken: string[];
  onRename: (verdict: string) => void;
}) {
  // The outcome card is keyed by its name, so a rename starts this input afresh.
  const [value, setValue] = useState(verdict);
  function commit() {
    const next = outcomeName(value);
    if (!next || (next !== verdict && taken.includes(next))) {
      setValue(verdict);
      return;
    }
    if (next !== verdict) onRename(next);
  }
  return (
    <input
      className="rw-input rw-ed__verdict"
      value={value}
      onChange={(event) => setValue(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') commit();
      }}
      aria-label="Outcome name"
      spellCheck={false}
      data-testid={`persona-outcome-name-${verdict}`}
    />
  );
}

function FinishesStep({
  doc,
  onChange,
}: {
  doc: PersonaDocument;
  onChange: (doc: PersonaDocument) => void;
}) {
  const outcomes = outcomesOf(doc);
  const verdicts = outcomes.map((outcome) => outcome.verdict);

  function change(index: number, patch: Partial<{ event: string; when: string }>) {
    onChange(
      withOutcomes(
        doc,
        outcomes.map((outcome, i) => (i === index ? { ...outcome, ...patch } : outcome)),
      ),
    );
  }

  return (
    <Question
      title="How can it finish?"
      hint="When it is done, it reports one of these outcomes. Each is a connector on the right of its card that you wire to what should happen next."
    >
      <div className="rw-ed__outs">
        {outcomes.map((outcome, index) => (
          <div className="rw-ed__out" key={outcome.verdict} data-testid="persona-outcome">
            <div className="rw-ed__out-row">
              <OutcomeNameInput
                verdict={outcome.verdict}
                taken={verdicts}
                onRename={(verdict) => onChange(renameOutcome(doc, index, verdict))}
              />
              <label className="rw-ed__sends">
                sends
                <input
                  className="rw-input rw-ed__mono"
                  value={outcome.event}
                  onChange={(event) => change(index, { event: eventName(event.target.value) })}
                  aria-label={`Event ${outcome.verdict} sends`}
                  spellCheck={false}
                  data-testid={`persona-outcome-event-${outcome.verdict}`}
                />
              </label>
              <button
                type="button"
                className="rw-icon-btn"
                aria-label={`Remove ${outcome.verdict}`}
                onClick={() =>
                  onChange(
                    withOutcomes(
                      doc,
                      outcomes.filter((_, i) => i !== index),
                    ),
                  )
                }
              >
                <X size={14} aria-hidden="true" />
              </button>
            </div>
            <label className="rw-field">
              <span className="rw-field__label">Report it when</span>
              <input
                className="rw-input"
                data-missing={outcome.when.trim() ? undefined : 'true'}
                value={outcome.when}
                onChange={(event) => change(index, { when: event.target.value })}
                placeholder="When is this the right outcome? For example: every check passed."
                data-testid={`persona-outcome-when-${outcome.verdict}`}
              />
            </label>
          </div>
        ))}
      </div>
      <p className="rw-ed__note">
        This is how it knows which outcome to pick: the line is put next to the outcome in every
        step it runs.
      </p>
      <AddRow
        placeholder="Name an outcome, like approved or blocked"
        label="New outcome name"
        button="Add outcome"
        normalize={outcomeName}
        onAdd={(verdict) => {
          if (verdicts.includes(verdict)) return;
          onChange(withOutcomes(doc, [...outcomes, newOutcome(doc, verdict)]));
        }}
        testId="persona-outcome-add"
      />
    </Question>
  );
}

function InstructionsStep({
  doc,
  onChange,
}: {
  doc: PersonaDocument;
  onChange: (doc: PersonaDocument) => void;
}) {
  return (
    <Question title="Instructions" hint="What it is told every time it runs.">
      <textarea
        className="rw-input rw-ed__prompt"
        value={documentInstructions(doc)}
        onChange={(event) => onChange(withField(doc, 'system_prompt_template', event.target.value))}
        placeholder="What it does, how it works, and what it must never do."
        aria-label="Instructions"
        data-testid="persona-editor-instructions"
      />
    </Question>
  );
}

function ReviewStep({
  doc,
  problems,
  serverErrors,
  onStep,
}: {
  doc: PersonaDocument;
  problems: DocumentProblem[];
  serverErrors: string[];
  onStep: (step: StepId) => void;
}) {
  return (
    <Question title="Review">
      <Summary doc={doc} />
      {problems.length + serverErrors.length > 0 ? (
        <ul className="rw-ed__problems" data-testid="persona-review-problems">
          {problems.map((problem) => (
            <li key={problem.message}>
              {problem.message}{' '}
              <button type="button" onClick={() => onStep(problem.step)}>
                Fix in {STEP_LABELS[problem.step]}
              </button>
            </li>
          ))}
          {serverErrors.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      ) : (
        <p className="rw-ed__note">
          Saved as a new revision; workflows pinned to an older one keep it until they are updated.
        </p>
      )}
    </Question>
  );
}

function Summary({ doc }: { doc: PersonaDocument }) {
  const permission = documentPermission(doc).replace(/-/g, '_');
  const label = PERMISSIONS.find((p) => p.value === permission)?.label ?? (permission || '—');
  const starts = startsOn(doc);
  const outcomes = outcomesOf(doc);
  return (
    <dl className="rw-ed__sum" data-testid="persona-editor-summary">
      <div>
        <dt>Name</dt>
        <dd>{documentName(doc) || '—'}</dd>
      </div>
      <div>
        <dt>For</dt>
        <dd>{documentDescription(doc) || '—'}</dd>
      </div>
      <div>
        <dt>May change</dt>
        <dd>{label}</dd>
      </div>
      <div>
        <dt>Starts on</dt>
        <dd className="rw-ed__chips">
          {starts.length ? starts.map((event) => <code key={event}>{event}</code>) : '—'}
        </dd>
      </div>
      <div>
        <dt>Finishes</dt>
        <dd>
          {outcomes.length === 0 && '—'}
          {outcomes.map((outcome) => (
            <div className="rw-ed__fin" key={outcome.verdict}>
              <div>
                <b>{outcome.verdict}</b> → <code>{outcome.event}</code>
              </div>
              <small data-missing={outcome.when.trim() ? undefined : 'true'}>
                {outcome.when.trim() || 'No rule yet for when to report it'}
              </small>
            </div>
          ))}
        </dd>
      </div>
    </dl>
  );
}
