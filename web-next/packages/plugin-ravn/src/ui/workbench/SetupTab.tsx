import type { ReactNode } from 'react';
import { Check, Minus } from 'lucide-react';
import { PersonaAvatar, relTime } from '@niuulabs/ui';
import { residentCapabilitySchema, type Ravn, type ResidentCapability } from '../../domain/ravn';
import { isSessionRavn, ravnLifeState, ravnTarget } from '../../application/ravnWorkbench';
import { usePersona } from '../usePersona';
import { EngineLabel, RavnStateBadge } from './RavnMark';
import { errorText } from './errorText';

const CAPABILITY_LABEL: Record<ResidentCapability, string> = {
  chat: 'Chat',
  'session.list': 'Conversations',
  'session.create': 'New conversations',
  'session.delete': 'Close conversations',
  steer: 'Steer mid-turn',
  interrupt: 'Interrupt',
  approvals: 'Approvals',
  'runtime.restart': 'Restart',
  'runtime.suspend': 'Suspend / resume',
  logs: 'Logs',
  metrics: 'Metrics',
  usage: 'Usage',
  flock: 'Flock mesh',
};

function Section({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section>
      <h3 className="rw-sec__title">
        {title}
        {action}
      </h3>
      {children}
    </section>
  );
}

function Field({ label, children, mono }: { label: string; children: ReactNode; mono?: boolean }) {
  return (
    <div>
      <div className="rw-kv__k">{label}</div>
      <div className={mono ? 'rw-kv__v rw-kv__v--mono' : 'rw-kv__v'}>{children}</div>
    </div>
  );
}

function PersonaSection({
  ravn,
  onOpenPersona,
}: {
  ravn: Ravn;
  onOpenPersona: (name: string) => void;
}) {
  const persona = usePersona(ravn.personaName);
  if (!ravn.personaName) {
    return (
      <Section title="Persona">
        <div className="rw-card">
          <div>
            <div className="rw-card__title">Engine default</div>
            <div className="rw-card__sub">
              {ravn.engine ?? 'This runtime'} runs with its own built-in character; no persona is
              bound.
            </div>
          </div>
        </div>
      </Section>
    );
  }
  return (
    <Section
      title="Persona"
      action={
        <button type="button" onClick={() => onOpenPersona(ravn.personaName)}>
          Open persona →
        </button>
      }
    >
      <div className="rw-card" data-testid="ravn-setup-persona">
        {persona.data && (
          <PersonaAvatar role={persona.data.role} letter={persona.data.letter} size={32} />
        )}
        <div>
          <div className="rw-card__title">{ravn.personaName}</div>
          <div className="rw-card__sub">
            {persona.isLoading && 'Loading persona…'}
            {persona.isError && errorText(persona.error, 'Persona could not be loaded')}
            {persona.data &&
              `${persona.data.summary} · ${persona.data.allowedTools.length} tools · ${
                persona.data.iterationBudget
                  ? `${persona.data.iterationBudget} iterations max`
                  : 'no iteration cap'
              }`}
          </div>
        </div>
      </div>
    </Section>
  );
}

function ForgeSessionSection({
  ravn,
  onOpenForgeSession,
}: {
  ravn: Ravn;
  onOpenForgeSession: (sessionId: string) => void;
}) {
  const sessionId = ravn.sessionId ?? ravn.id;
  return (
    <Section
      title="Runs as"
      action={
        <button type="button" onClick={() => onOpenForgeSession(sessionId)}>
          Open in Forge →
        </button>
      }
    >
      <div className="rw-card" data-testid="ravn-setup-session">
        <div>
          <div className="rw-card__title">A Forge session on {ravnTarget(ravn)}</div>
          <div className="rw-card__sub">
            Its ravn and chat room run as processes on the Forge host — no container. The personas
            it runs were chosen at launch; Forge shows the session&rsquo;s full configuration.
          </div>
        </div>
      </div>
    </Section>
  );
}

export function SetupTab({
  ravn,
  onOpenPersona,
  onOpenForgeSession,
}: {
  ravn: Ravn;
  onOpenPersona: (name: string) => void;
  onOpenForgeSession: (sessionId: string) => void;
}) {
  const capabilities = new Set(ravn.capabilities ?? []);
  const conditions = ravn.conditions ?? [];
  const endpoints = ravn.endpoints ?? [];
  const connections = [
    ...(ravn.mcpServers?.length ? [['MCP servers', ravn.mcpServers.join(', ')]] : []),
    ...(ravn.gatewayChannels?.length ? [['Channels', ravn.gatewayChannels.join(', ')]] : []),
    ...(ravn.eventSubscriptions?.length
      ? [['Listens for', ravn.eventSubscriptions.join('\n')]]
      : []),
    ...(ravn.mounts?.length
      ? [['Mímir mounts', ravn.mounts.map((mount) => `${mount.name} (${mount.role})`).join(', ')]]
      : []),
  ];

  return (
    <div className="rw-sheet" data-testid="ravn-setup-tab">
      {isSessionRavn(ravn) ? (
        <ForgeSessionSection ravn={ravn} onOpenForgeSession={onOpenForgeSession} />
      ) : (
        <PersonaSection ravn={ravn} onOpenPersona={onOpenPersona} />
      )}

      <Section title="Runtime">
        <div className="rw-kv">
          <Field label="Engine">
            <EngineLabel engine={ravn.engine} />
          </Field>
          {ravn.backend && <Field label="Backend">{ravn.backend}</Field>}
          {ravn.profileId && (
            <Field label="Profile" mono>
              {ravn.profileId}
            </Field>
          )}
          <Field label="Target">{ravnTarget(ravn)}</Field>
          <Field label="Model" mono={Boolean(ravn.model)}>
            {ravn.model || 'set by the Forge’s ravn configuration'}
          </Field>
          <Field label="State">
            <RavnStateBadge state={ravnLifeState(ravn)} />
            {ravn.desiredState && ` wants ${ravn.desiredState}`}
          </Field>
          <Field label="Deployed">{relTime(ravn.createdAt)}</Field>
          {ravn.updatedAt && <Field label="Last update">{relTime(ravn.updatedAt)}</Field>}
        </div>
      </Section>

      {ravn.managed && (
        <Section title="What it can do">
          <div className="rw-caps" data-testid="ravn-capabilities">
            {residentCapabilitySchema.options.map((capability) => {
              const on = capabilities.has(capability);
              return (
                <span
                  key={capability}
                  className="rw-cap"
                  data-on={on}
                  title={
                    on
                      ? 'Offered by this runtime'
                      : `Not offered by ${ravn.profileId ?? 'this profile'}`
                  }
                >
                  {on ? (
                    <Check size={13} aria-hidden="true" />
                  ) : (
                    <Minus size={13} aria-hidden="true" />
                  )}
                  {CAPABILITY_LABEL[capability]}
                </span>
              );
            })}
          </div>
        </Section>
      )}

      {conditions.length > 0 && (
        <Section title="Health">
          <div className="rw-table-wrap">
            <table className="rw-table" data-testid="ravn-conditions">
              <thead>
                <tr>
                  <th>Check</th>
                  <th>Status</th>
                  <th>Reason</th>
                  <th>Detail</th>
                  <th>Since</th>
                </tr>
              </thead>
              <tbody>
                {conditions.map((condition) => (
                  <tr key={condition.type}>
                    <td>{condition.type}</td>
                    <td>{condition.status}</td>
                    <td>{condition.reason || '—'}</td>
                    <td className="rw-table__mono">{condition.message || '—'}</td>
                    <td>
                      {condition.lastTransitionAt ? relTime(condition.lastTransitionAt) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {(endpoints.length > 0 || connections.length > 0) && (
        <Section title="Connections">
          <div className="rw-kv">
            {endpoints.map((endpoint) => (
              <Field
                key={`${endpoint.kind}:${endpoint.url}`}
                label={`${endpoint.kind} · ${endpoint.protocol}`}
                mono
              >
                {endpoint.url}
              </Field>
            ))}
            {connections.map(([label, value]) => (
              <Field key={label} label={label!} mono>
                {value}
              </Field>
            ))}
          </div>
        </Section>
      )}

      {ravn.flockId && (
        <Section title="Flock">
          <div className="rw-kv">
            <Field label="Flock" mono>
              {ravn.flockId}
            </Field>
            {ravn.flockRole && <Field label="Role">{ravn.flockRole}</Field>}
            {ravn.flockPeerId && (
              <Field label="Mesh peer" mono>
                {ravn.flockPeerId}
              </Field>
            )}
          </div>
        </Section>
      )}

      <Section title="Identifiers">
        <div className="rw-kv">
          <Field label="Ravn" mono>
            {ravn.id}
          </Field>
          {ravn.sessionId && ravn.sessionId !== ravn.id && (
            <Field label="Session" mono>
              {ravn.sessionId}
            </Field>
          )}
          {ravn.instanceId && (
            <Field label="Target id" mono>
              {ravn.instanceId}
            </Field>
          )}
          {ravn.backendRef && (
            <Field label="Backend object" mono>
              {[ravn.backendRef.kind, ravn.backendRef.name].filter(Boolean).join(' · ') ||
                JSON.stringify(ravn.backendRef)}
            </Field>
          )}
        </div>
      </Section>
    </div>
  );
}
