import { useMemo, useState, type FormEvent } from 'react';
import { Rocket, Search, Users } from 'lucide-react';
import { Dialog, DialogContent, LoadingState, PersonaAvatar } from '@niuulabs/ui';
import type { Ravn, ResidentCapability, ResidentDeploymentProfile } from '../../domain/ravn';
import { useDeployResident, useResidentProfiles } from '../hooks/useResidentControl';
import { useOptionalPersonas } from '../usePersonas';
import { ResidentModelSelect } from '../ResidentModelSelect';
import { targetLabel } from '../ResidentDeployFields';
import { matchesPersonaQuery, personaTagline } from '../../application/personaFamilies';
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
  onDeployed: (ravn: Ravn) => void;
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
  const [profileId, setProfileId] = useState('');
  const [name, setName] = useState('');
  const [model, setModel] = useState('');
  const [persona, setPersona] = useState(initialPersona);
  const [personaQuery, setPersonaQuery] = useState('');

  const profiles = useMemo(() => profilesQuery.data ?? [], [profilesQuery.data]);
  const profile = profiles.find((candidate) => profileKey(candidate) === profileId) ?? profiles[0];
  const selectedModel =
    profile && profile.allowedModels.includes(model) ? model : (profile?.defaultModel ?? '');
  const personas = useMemo(
    () =>
      [...(personasQuery.data ?? [])]
        .filter((candidate) => matchesPersonaQuery(candidate, personaQuery))
        .sort((left, right) => left.name.localeCompare(right.name)),
    [personasQuery.data, personaQuery],
  );
  const canDeploy = Boolean(profile && name.trim()) && !deploy.isPending;

  function close(next: boolean) {
    if (!next) {
      deploy.reset();
      setName('');
      setPersonaQuery('');
    }
    onOpenChange(next);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!profile || !name.trim()) return;
    let ravn: Ravn;
    try {
      ravn = await deploy.mutateAsync({
        name: name.trim(),
        profileId: profile.id,
        instanceId: profile.instanceId,
        personaName: persona,
        model: selectedModel,
      });
    } catch {
      return;
    }
    close(false);
    onDeployed(ravn);
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
            {profilesQuery.isLoading && <LoadingState label="Loading deployment profiles…" />}
            {profilesQuery.isError && (
              <div className="rw-form-error" role="alert">
                {errorText(profilesQuery.error, 'Deployment profiles could not be loaded')}
              </div>
            )}
            {!profilesQuery.isLoading && !profilesQuery.isError && profiles.length === 0 && (
              <p className="rw-muted">No deployment profiles are enabled on any target.</p>
            )}
            {groupByTarget(profiles).map(([target, targetProfiles]) => (
              <div key={target}>
                <div className="rw-target-label">{target}</div>
                <div className="rw-profiles" role="radiogroup" aria-label={`Profiles on ${target}`}>
                  {targetProfiles.map((candidate) => (
                    <button
                      key={profileKey(candidate)}
                      type="button"
                      role="radio"
                      className="rw-profile"
                      aria-checked={profile === candidate}
                      aria-pressed={profile === candidate}
                      onClick={() => {
                        setProfileId(profileKey(candidate));
                        setModel('');
                      }}
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

          {profile && (
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
                {profile.allowedModels.length > 0 && (
                  <label className="rw-field">
                    <span className="rw-field__label">Model</span>
                    <ResidentModelSelect
                      allowedModels={profile.allowedModels}
                      modelPrefix={profile.modelPrefix ?? ''}
                      value={selectedModel}
                      onChange={setModel}
                      testId="ravn-deploy-model"
                    />
                  </label>
                )}
              </div>

              <div className="rw-field" role="group" aria-labelledby="ravn-deploy-persona-label">
                <span className="rw-field__label" id="ravn-deploy-persona-label">
                  Persona <span className="rw-field__hint">— optional</span>
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

          {deploy.isError && (
            <div className="rw-form-error" role="alert">
              {errorText(deploy.error, 'Deployment failed')}
            </div>
          )}

          <div className="rw-dialog-foot">
            <span className="rw-dialog-foot__sum">
              {profile && name.trim() ? (
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
              disabled={!canDeploy}
              data-testid="ravn-deploy-submit"
            >
              <Rocket size={14} aria-hidden="true" />
              {deploy.isPending ? 'Deploying…' : 'Deploy'}
            </button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
