import { useMemo, useState } from 'react';
import { Dialog, DialogContent } from '@niuulabs/ui';
import { Rocket } from 'lucide-react';
import type { Ravn, ResidentDeploymentProfile } from '../domain/ravn';
import { useDeployResident, useResidentProfiles } from './hooks/useResidentControl';

interface ResidentDeployDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeployed: (ravn: Ravn) => void;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Resident deployment failed';
}

function targetLabel(profile: ResidentDeploymentProfile): string {
  return profile.instanceName || profile.instanceSlug || profile.instanceId;
}

export function ResidentDeployDialog({
  open,
  onOpenChange,
  onDeployed,
}: ResidentDeployDialogProps) {
  const profilesQuery = useResidentProfiles(open);
  const deploy = useDeployResident();
  const [instanceId, setInstanceId] = useState('');
  const [profileId, setProfileId] = useState('');
  const [name, setName] = useState('');
  const [personaName, setPersonaName] = useState('');
  const [model, setModel] = useState('');

  const profiles = useMemo(() => profilesQuery.data ?? [], [profilesQuery.data]);
  const targets = useMemo(() => {
    const byId = new Map<string, ResidentDeploymentProfile>();
    for (const profile of profiles) byId.set(profile.instanceId, profile);
    return Array.from(byId.values()).sort((left, right) =>
      targetLabel(left).localeCompare(targetLabel(right)),
    );
  }, [profiles]);
  const selectedInstanceId = profiles.some((profile) => profile.instanceId === instanceId)
    ? instanceId
    : (profiles[0]?.instanceId ?? '');
  const compatibleProfiles = profiles.filter(
    (profile) => profile.instanceId === selectedInstanceId,
  );
  const selectedProfile =
    compatibleProfiles.find((profile) => profile.id === profileId) ?? compatibleProfiles[0];
  const selectedModel = selectedProfile?.allowedModels.includes(model)
    ? model
    : (selectedProfile?.defaultModel ?? '');

  function selectTarget(nextInstanceId: string) {
    const profile = profiles.find((candidate) => candidate.instanceId === nextInstanceId);
    setInstanceId(nextInstanceId);
    setProfileId(profile?.id ?? '');
    setModel(profile?.defaultModel ?? '');
  }

  function selectProfile(nextProfileId: string) {
    const profile = compatibleProfiles.find((candidate) => candidate.id === nextProfileId);
    setProfileId(nextProfileId);
    setModel(profile?.defaultModel ?? '');
  }

  function setOpen(nextOpen: boolean) {
    if (!nextOpen) {
      deploy.reset();
      setName('');
      setPersonaName('');
    }
    onOpenChange(nextOpen);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!selectedProfile || !name.trim()) return;
    let ravn: Ravn;
    try {
      ravn = await deploy.mutateAsync({
        name: name.trim(),
        profileId: selectedProfile.id,
        instanceId: selectedProfile.instanceId,
        personaName: personaName.trim(),
        model: selectedModel,
      });
    } catch {
      return;
    }
    onDeployed(ravn);
    setOpen(false);
    setName('');
    setPersonaName('');
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent
        title="Deploy resident"
        description="Launch a long-lived Ravn from an enabled target profile."
        className="rv-deploy-dialog"
      >
        <form className="rv-deploy-form" onSubmit={(event) => void submit(event)}>
          {profilesQuery.isLoading && <div className="rv-form-state">Loading profiles…</div>}
          {profilesQuery.isError && (
            <div className="rv-form-error" role="alert">
              {errorMessage(profilesQuery.error)}
            </div>
          )}

          {!profilesQuery.isLoading && profiles.length === 0 && !profilesQuery.isError && (
            <div className="rv-form-state">No resident profiles are enabled.</div>
          )}

          {profiles.length > 0 && (
            <>
              <label className="rv-form-field">
                <span>Target</span>
                <select
                  value={selectedInstanceId}
                  onChange={(event) => selectTarget(event.target.value)}
                  data-testid="resident-target"
                >
                  {targets.map((target) => (
                    <option key={target.instanceId} value={target.instanceId}>
                      {targetLabel(target)}
                    </option>
                  ))}
                </select>
              </label>

              <label className="rv-form-field">
                <span>Profile</span>
                <select
                  value={selectedProfile?.id ?? ''}
                  onChange={(event) => selectProfile(event.target.value)}
                  data-testid="resident-profile"
                >
                  {compatibleProfiles.map((profile) => (
                    <option key={`${profile.instanceId}:${profile.id}`} value={profile.id}>
                      {profile.displayName}
                    </option>
                  ))}
                </select>
              </label>

              {selectedProfile && (
                <div className="rv-profile-summary" data-testid="resident-profile-summary">
                  <div>
                    <strong>{selectedProfile.engine}</strong>
                    <span>{selectedProfile.backend}</span>
                  </div>
                  <p>{selectedProfile.description}</p>
                </div>
              )}

              <label className="rv-form-field">
                <span>Name</span>
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  required
                  maxLength={255}
                  data-testid="resident-name"
                />
              </label>

              <label className="rv-form-field">
                <span>Persona</span>
                <input
                  value={personaName}
                  onChange={(event) => setPersonaName(event.target.value)}
                  maxLength={255}
                  data-testid="resident-persona"
                />
              </label>

              {selectedProfile && selectedProfile.allowedModels.length > 0 && (
                <label className="rv-form-field">
                  <span>Model</span>
                  <select
                    value={selectedModel}
                    onChange={(event) => setModel(event.target.value)}
                    data-testid="resident-model"
                  >
                    {selectedProfile.allowedModels.map((allowedModel) => (
                      <option key={allowedModel} value={allowedModel}>
                        {allowedModel}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </>
          )}

          {deploy.isError && (
            <div className="rv-form-error" role="alert">
              {errorMessage(deploy.error)}
            </div>
          )}

          <div className="rv-form-actions">
            <button type="button" onClick={() => setOpen(false)} className="rv-action-btn">
              Cancel
            </button>
            <button
              type="submit"
              className="rv-action-btn rv-action-btn--primary"
              disabled={!selectedProfile || !name.trim() || deploy.isPending}
              data-testid="resident-deploy-submit"
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
