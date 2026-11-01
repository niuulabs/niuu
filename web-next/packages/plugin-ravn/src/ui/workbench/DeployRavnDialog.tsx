import { useMemo, useState, type FormEvent } from 'react';
import { Rocket, Search, Users } from 'lucide-react';
import { Dialog, DialogContent, LoadingState, PersonaAvatar } from '@niuulabs/ui';
import type { Ravn, ResidentCapability, ResidentDeploymentProfile } from '../../domain/ravn';
import { useDeployResident, useResidentProfiles } from '../hooks/useResidentControl';
import { useRavnSessionLaunch } from '../hooks/useRavnSessionLaunch';
import { useOptionalPersonas } from '../usePersonas';
import { ResidentModelSelect } from '../ResidentModelSelect';
import { targetLabel } from '../ResidentDeployFields';
import { matchesPersonaQuery, personaTagline } from '../../application/personaFamilies';
import { sessionNameFor } from '../../application/sessionLaunch';
import { EngineLabel } from './RavnMark';
import { errorText } from './errorText';

/** The capabilities worth naming on a profile card, in the words people use. */
const HEADLINE_CAPABILITIES: Array<[ResidentCapability, string]> = [
  ['chat', 'Chat'],
  ['session.list', 'Conversations'],
  ['approvals', 'Approvals'],
  ['steer', 'Steer'],
  ['flock', 'Flock'],
  ['logs', 'Logs'],
];

/** The runtime key of a ravn run as a Forge session rather than a resident profile. */
const SESSION_RUNTIME = 'forge-session';

function profileKey(profile: ResidentDeploymentProfile): string {
  return `${profile.instanceId}:${profile.id}`;
}

function groupByTarget(profiles: ResidentDeploymentProfile[]) {
  const groups = new Map<string, ResidentDeploymentProfile[]>();
  for (const profile of profiles) {
    const key = targetLabel(profile);
    groups.set(key, [...(groups.get(key) ?? []), profile]);
  }
  return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right));
}

export interface DeployRavnDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeployed: (ravn: Pick<Ravn, 'id' | 'instanceId'>) => void;
  onDeployFlock: () => void;
  initialPersona?: string;
}

export function DeployRavnDialog({
  open,
  onOpenChange,
  onDeployed,
  onDeployFlock,
  initialPersona = '',
}: DeployRavnDialogProps) {
  const profilesQuery = useResidentProfiles(open);
  const personasQuery = useOptionalPersonas(open);
  const deploy = useDeployResident();
  const launch = useRavnSessionLaunch();
  const [runtimeKey, setRuntimeKey] = useState('');
  const [name, setName] = useState('');
  const [model, setModel] = useState('');
  const [persona, setPersona] = useState(initialPersona);
  const [personaQuery, setPersonaQuery] = useState('');

  const profiles = useMemo(() => profilesQuery.data ?? [], [profilesQuery.data]);
  // A Forge session needs nothing but the Forge itself, so it leads when offered.
  const resolvedKey =
    runtimeKey || (launch ? SESSION_RUNTIME : profiles[0] ? profileKey(profiles[0]) : '');
  const asSession = resolvedKey === SESSION_RUNTIME && Boolean(launch);
  const profile = asSession
    ? undefined
    : profiles.find((candidate) => profileKey(candidate) === resolvedKey);
  // A session ravn's model comes from the Forge's ravn configuration, not the request.
  const allowedModels = profile?.allowedModels ?? [];
  const selectedModel = allowedModels.includes(model) ? model : (profile?.defaultModel ?? '');
  const sessionName = sessionNameFor(name);
  const personas = useMemo(
    () =>
      [...(personasQuery.data ?? [])]
        .filter((candidate) => matchesPersonaQuery(candidate, personaQuery))
        .sort((left, right) => left.name.localeCompare(right.name)),
    [personasQuery.data, personaQuery],
  );
  const pending = deploy.isPending || Boolean(launch?.isPending);
  const failure = deploy.error ?? launch?.error ?? null;
  const ready = asSession ? Boolean(sessionName && persona) : Boolean(profile && name.trim());

  function close(next: boolean) {
    if (!next) {
      deploy.reset();
      launch?.reset();
      setName('');
      setPersonaQuery('');
    }
    onOpenChange(next);
  }

  function pickRuntime(key: string) {
    setRuntimeKey(key);
    setModel('');
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready) return;
    let target: Pick<Ravn, 'id' | 'instanceId'>;
    try {
      if (asSession && launch) {
        const session = await launch.mutateAsync({ name, persona });
        target = { id: session.id };
      } else if (profile) {
        target = await deploy.mutateAsync({
          name: name.trim(),
          profileId: profile.id,
          instanceId: profile.instanceId,
          personaName: persona,
          model: selectedModel,
        });
      } else {
        return;
      }
    } catch {
      return;
    }
    close(false);
    onDeployed(target);
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title="Deploy a ravn"
        description="Pick where it runs, then who it is."
        className="rw-dialog"
      >
        <form className="rw-form" onSubmit={(event) => void submit(event)}>
          <div>
            <div className="rw-step">Runtime</div>
            {launch && (
              <div>
                <div className="rw-target-label">This Forge · no container</div>
                <div className="rw-profiles" role="radiogroup" aria-label="Forge session">
                  <button
                    type="button"
                    role="radio"
                    className="rw-profile"
                    aria-checked={asSession}
                    aria-pressed={asSession}
                    onClick={() => pickRuntime(SESSION_RUNTIME)}
                    data-testid="ravn-deploy-runtime-session"
                  >
                    <span className="rw-profile__title">
                      Ravn session
                      <EngineLabel engine="ravn" />
                    </span>
                    <span className="rw-profile__desc">
                      Runs the persona&rsquo;s ravn and its chat room as processes on this Forge.
                      Stopping it ends the session.
                    </span>
                    <span className="rw-profile__caps">processes · Chat · Logs</span>
                  </button>
                </div>
              </div>
            )}
            {profilesQuery.isLoading && <LoadingState label="Loading deployment profiles…" />}
            {profilesQuery.isError && (
              <div className="rw-form-error" role="alert">
                {errorText(profilesQuery.error, 'Deployment profiles could not be loaded')}
              </div>
            )}
            {!profilesQuery.isLoading &&
              !profilesQuery.isError &&
              profiles.length === 0 &&
              !launch && (
                <p className="rw-muted">No deployment profiles are enabled on any target.</p>
              )}
            {groupByTarget(profiles).map(([target, targetProfiles]) => (
              <div key={target}>
                <div className="rw-target-label">{target} · resident</div>
                <div className="rw-profiles" role="radiogroup" aria-label={`Profiles on ${target}`}>
                  {targetProfiles.map((candidate) => (
                    <button
                      key={profileKey(candidate)}
                      type="button"
                      role="radio"
                      className="rw-profile"
                      aria-checked={profile === candidate}
                      aria-pressed={profile === candidate}
                      onClick={() => pickRuntime(profileKey(candidate))}
                      data-testid={`ravn-deploy-profile-${candidate.id}`}
                    >
                      <span className="rw-profile__title">
                        {candidate.displayName}
                        <EngineLabel engine={candidate.engine} />
                      </span>
                      {candidate.description && (
                        <span className="rw-profile__desc">{candidate.description}</span>
                      )}
                      <span className="rw-profile__caps">
                        {candidate.backend} ·{' '}
                        {HEADLINE_CAPABILITIES.filter(([capability]) =>
                          candidate.capabilities.includes(capability),
                        )
                          .map(([, label]) => label)
                          .join(' · ')}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>

          {(asSession || profile) && (
            <div className="rw-stack">
              <div className="rw-step">Identity</div>
              <div className="rw-grid-2">
                <label className="rw-field">
                  <span className="rw-field__label">Name</span>
                  <input
                    className="rw-input"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder="e.g. Muninn"
                    maxLength={255}
                    autoFocus
                    data-testid="ravn-deploy-name"
                  />
                </label>
                {asSession && (
                  <div className="rw-field">
                    <span className="rw-field__label">Model</span>
                    <span className="rw-field__note" data-testid="ravn-deploy-session-model">
                      Set by this Forge&rsquo;s ravn configuration
                    </span>
                  </div>
                )}
                {allowedModels.length > 0 && (
                  <label className="rw-field">
                    <span className="rw-field__label">Model</span>
                    <ResidentModelSelect
                      allowedModels={allowedModels}
                      modelPrefix={profile?.modelPrefix ?? ''}
                      value={selectedModel}
                      onChange={setModel}
                      testId="ravn-deploy-model"
                    />
                  </label>
                )}
              </div>

              <div className="rw-field" role="group" aria-labelledby="ravn-deploy-persona-label">
                <span className="rw-field__label" id="ravn-deploy-persona-label">
                  Persona{' '}
                  <span className="rw-field__hint">
                    {asSession ? '— the character the session runs' : '— optional'}
                  </span>
                </span>
                <label className="rw-search">
                  <Search size={14} aria-hidden="true" />
                  <input
                    type="search"
                    value={personaQuery}
                    onChange={(event) => setPersonaQuery(event.target.value)}
                    placeholder="Search personas"
                    aria-label="Search personas"
                  />
                </label>
                <div className="rw-picker" data-testid="ravn-deploy-personas">
                  {profile && (
                    <button
                      type="button"
                      aria-pressed={persona === ''}
                      onClick={() => setPersona('')}
                    >
                      <span />
                      <span>
                        <span className="rw-picker__name">Engine default</span>
                        <span className="rw-picker__sub">
                          {profile.engine} runs with its own built-in character
                        </span>
                      </span>
                    </button>
                  )}
                  {personas.map((candidate) => (
                    <button
                      key={candidate.name}
                      type="button"
                      aria-pressed={persona === candidate.name}
                      onClick={() => setPersona(candidate.name)}
                    >
                      <PersonaAvatar role={candidate.role} letter={candidate.letter} size={22} />
                      <span>
                        <span className="rw-picker__name">{candidate.name}</span>
                        <span className="rw-picker__sub">{personaTagline(candidate)}</span>
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {failure && (
            <div className="rw-form-error" role="alert">
              {errorText(failure, 'Deployment failed')}
            </div>
          )}

          <div className="rw-dialog-foot">
            <span className="rw-dialog-foot__sum" data-testid="ravn-deploy-summary">
              {asSession ? (
                <SessionSummary name={sessionName} typed={name} persona={persona} />
              ) : profile && name.trim() ? (
                <>
                  Deploys <strong>{name.trim()}</strong> as {profile.displayName} on{' '}
                  <strong>{targetLabel(profile)}</strong>
                  {persona && (
                    <>
                      {' '}
                      with persona <strong>{persona}</strong>
                    </>
                  )}
                  .
                </>
              ) : (
                'Name it to deploy.'
              )}
            </span>
            <button
              type="button"
              className="rw-btn"
              onClick={() => {
                close(false);
                onDeployFlock();
              }}
              data-testid="ravn-deploy-flock"
            >
              <Users size={14} aria-hidden="true" />A flock instead…
            </button>
            <button
              type="submit"
              className="rw-btn rw-btn--primary"
              disabled={!ready || pending}
              data-testid="ravn-deploy-submit"
            >
              <Rocket size={14} aria-hidden="true" />
              {pending ? 'Deploying…' : 'Deploy'}
            </button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function SessionSummary({
  name,
  typed,
  persona,
}: {
  name: string;
  typed: string;
  persona: string;
}) {
  if (typed.trim() && !name) return <>Give it a name with at least one letter or digit.</>;
  if (!name && !persona) return <>Name it and pick a persona.</>;
  if (!name) return <>Name it.</>;
  if (!persona) return <>Pick the persona it runs.</>;
  return (
    <>
      Starts <strong>{name}</strong>
      {name !== typed.trim() && ' (Forge names sessions in lowercase)'} as a Ravn session on this
      Forge with persona <strong>{persona}</strong>.
    </>
  );
}
