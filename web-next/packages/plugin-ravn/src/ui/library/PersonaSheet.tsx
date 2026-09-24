import { useState, type ReactNode } from 'react';
import { ArrowRight } from 'lucide-react';
import type { PersonaDetail } from '../../ports';
import type { Ravn } from '../../domain/ravn';
import { nameForRavn } from '../../domain/residentActions';
import { ravnLifeState, ravnTarget } from '../../application/ravnWorkbench';
import { useTriggers } from '../hooks/useTriggers';
import { RavnStateBadge } from '../workbench/RavnMark';
import { errorText } from '../workbench/errorText';

function Section({
  title,
  action,
  children,
  testId,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <section data-testid={testId}>
      <h3 className="rw-sec__title">
        {title}
        {action}
      </h3>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="rw-kv__k">{label}</div>
      <div className="rw-kv__v">{children}</div>
    </div>
  );
}

export function describeFanIn(fanIn: PersonaDetail['fanIn']): string | null {
  if (!fanIn) return null;
  const strategy = fanIn.strategy.replace(/_/g, ' ');
  const target = fanIn.params.contributes_to;
  return typeof target === 'string' ? `${strategy} → ${target}` : strategy;
}

function Triggers({ persona }: { persona: PersonaDetail }) {
  const triggers = useTriggers();
  if (triggers.isLoading) return <p className="rw-muted">Loading triggers…</p>;
  if (triggers.isError) {
    return (
      <p className="rw-muted" data-testid="persona-triggers-unavailable">
        Triggers can’t be listed: {errorText(triggers.error, 'the trigger service did not answer')}.
      </p>
    );
  }
  const mine = (triggers.data ?? []).filter((trigger) => trigger.personaName === persona.name);
  if (mine.length === 0) {
    return <p className="rw-muted">Nothing starts this persona on a schedule or hook.</p>;
  }
  return (
    <div className="rw-kv">
      {mine.map((trigger) => (
        <Field key={trigger.id} label={`${trigger.kind}${trigger.enabled ? '' : ' · paused'}`}>
          <span className="rw-kv__v--mono">{trigger.spec}</span>
        </Field>
      ))}
    </div>
  );
}

/** A persona read as a character sheet: what it does, how it fits the flow, what it may use. */
export function PersonaSheet({
  persona,
  usedBy,
  onOpenRavn,
  onDeploy,
}: {
  persona: PersonaDetail;
  usedBy: Ravn[];
  onOpenRavn: (ravn: Ravn) => void;
  onDeploy: () => void;
}) {
  const [promptOpen, setPromptOpen] = useState(false);
  const consumes = persona.consumes.events;
  const schema = Object.entries(persona.produces.schemaDef ?? {});
  const fanIn = describeFanIn(persona.fanIn);
  const outcomes = Object.entries(persona.outcomeEvents ?? {});

  return (
    <div className="rw-sheet" data-testid="persona-sheet">
      <Section title="What it does">
        <p className="rw-sec__lead">{persona.description || persona.summary}</p>
      </Section>

      <Section title="How it fits the flow" testId="persona-flow">
        <div className="rw-flow">
          <div className="rw-flow__col">
            <span className="rw-flow__label">Wakes on</span>
            {consumes.length === 0 ? (
              <span className="rw-flow__none">direct requests only</span>
            ) : (
              consumes.map((event) => (
                <span key={event.name} className="rw-flow__ev" title={event.name}>
                  {event.name}
                </span>
              ))
            )}
          </div>
          <ArrowRight className="rw-flow__arrow" size={18} aria-hidden="true" />
          <div className="rw-flow__me">
            <strong>{persona.name}</strong>
            {persona.iterationBudget ? `${persona.iterationBudget} iterations max` : 'no cap'}
          </div>
          <ArrowRight className="rw-flow__arrow" size={18} aria-hidden="true" />
          <div className="rw-flow__col rw-flow__col--out">
            <span className="rw-flow__label">Emits</span>
            {persona.produces.eventType ? (
              <span className="rw-flow__ev rw-flow__ev--out" title={persona.produces.eventType}>
                {persona.produces.eventType}
              </span>
            ) : (
              <span className="rw-flow__none">nothing</span>
            )}
            {outcomes.map(([outcome, event]) => (
              <span key={outcome} className="rw-flow__ev rw-flow__ev--out" title={`on ${outcome}`}>
                {event}
              </span>
            ))}
            {fanIn && <span className="rw-flow__note">fan-in: {fanIn}</span>}
          </div>
        </div>
        {schema.length > 0 && (
          <div className="rw-schema" aria-label="Emitted fields">
            {schema.map(([field, type]) => (
              <span key={field}>
                <b>{field}</b>: {String(type)}
              </span>
            ))}
          </div>
        )}
      </Section>

      <Section title="Tools">
        {persona.allowedTools.length === 0 && persona.forbiddenTools.length === 0 ? (
          <p className="rw-muted">No tools granted.</p>
        ) : (
          <div className="rw-tools-list">
            {persona.allowedTools.map((tool) => (
              <span key={tool} className="rw-tool">
                {tool}
              </span>
            ))}
            {persona.forbiddenTools.map((tool) => (
              <span key={tool} className="rw-tool rw-tool--forbidden" title="Forbidden">
                {tool}
              </span>
            ))}
          </div>
        )}
      </Section>

      <Section title="Behaviour">
        <div className="rw-kv">
          <Field label="Permission mode">{persona.permissionMode}</Field>
          <Field label="Iteration budget">{persona.iterationBudget || 'uncapped'}</Field>
          <Field label="Extended thinking">{persona.llm.thinkingEnabled ? 'on' : 'off'}</Field>
          <Field label="Max tokens">{persona.llm.maxTokens || 'model default'}</Field>
          <Field label="Temperature">{persona.llm.temperature ?? 'model default'}</Field>
          <Field label="Mímir writes go to">{persona.mimirWriteRouting ?? 'not set'}</Field>
        </div>
      </Section>

      <Section
        title="System prompt"
        action={
          <button type="button" onClick={() => setPromptOpen((open) => !open)}>
            {promptOpen ? 'Collapse' : 'Expand'}
          </button>
        }
      >
        <pre className="rw-prompt" data-open={promptOpen} data-testid="persona-prompt">
          {persona.systemPromptTemplate || 'No system prompt.'}
        </pre>
      </Section>

      <Section title="Triggers">
        <Triggers persona={persona} />
      </Section>

      <Section title="Ravens using it" testId="persona-used-by">
        {usedBy.length === 0 ? (
          <p className="rw-muted">
            None yet.{' '}
            <button type="button" className="rw-btn rw-btn--small" onClick={onDeploy}>
              Deploy one
            </button>
          </p>
        ) : (
          <div className="rw-kv">
            {usedBy.map((ravn) => (
              <div key={`${ravn.instanceId ?? ''}:${ravn.id}`}>
                <div className="rw-kv__k">{ravnTarget(ravn)}</div>
                <button
                  type="button"
                  className="rw-btn rw-btn--small"
                  onClick={() => onOpenRavn(ravn)}
                >
                  <RavnStateBadge state={ravnLifeState(ravn)} />
                  {nameForRavn(ravn)}
                </button>
              </div>
            ))}
          </div>
        )}
      </Section>

      <p className="rw-muted">
        Loaded from <span className="rw-kv__v--mono">{persona.yamlSource}</span>
        {persona.overrideSource && (
          <>
            , overridden by <span className="rw-kv__v--mono">{persona.overrideSource}</span>
          </>
        )}
        .
      </p>
    </div>
  );
}
