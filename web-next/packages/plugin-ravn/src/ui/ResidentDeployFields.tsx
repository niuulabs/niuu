import { useMemo } from 'react';
import type { ResidentDeploymentProfile } from '../domain/ravn';
import { ResidentModelSelect } from './ResidentModelSelect';

export interface ResidentMemberDraft {
  name: string;
  instanceId: string;
  profileId: string;
  personaName: string;
  model: string;
  role: string;
}

interface PersonaOption {
  name: string;
  role: string;
}

interface ResidentDeployFieldsProps {
  draft: ResidentMemberDraft;
  profiles: ResidentDeploymentProfile[];
  personas: PersonaOption[];
  onChange: (draft: ResidentMemberDraft) => void;
  testIdPrefix: string;
  showRole?: boolean;
}

export function targetLabel(profile: ResidentDeploymentProfile): string {
  return profile.instanceName || profile.instanceSlug || profile.instanceId;
}

export function selectedResidentProfile(
  draft: ResidentMemberDraft,
  profiles: ResidentDeploymentProfile[],
): ResidentDeploymentProfile | undefined {
  const instanceId = profiles.some((profile) => profile.instanceId === draft.instanceId)
    ? draft.instanceId
    : profiles[0]?.instanceId;
  const compatible = profiles.filter((profile) => profile.instanceId === instanceId);
  return compatible.find((profile) => profile.id === draft.profileId) ?? compatible[0];
}

export function ResidentDeployFields({
  draft,
  profiles,
  personas,
  onChange,
  testIdPrefix,
  showRole = false,
}: ResidentDeployFieldsProps) {
  const targets = useMemo(() => {
    const byId = new Map<string, ResidentDeploymentProfile>();
    for (const profile of profiles) byId.set(profile.instanceId, profile);
    return Array.from(byId.values()).sort((left, right) =>
      targetLabel(left).localeCompare(targetLabel(right)),
    );
  }, [profiles]);
  const selectedProfile = selectedResidentProfile(draft, profiles);
  const instanceId = selectedProfile?.instanceId ?? '';
  const compatibleProfiles = profiles.filter((profile) => profile.instanceId === instanceId);
  const selectedModel = selectedProfile?.allowedModels.includes(draft.model)
    ? draft.model
    : (selectedProfile?.defaultModel ?? '');

  function selectTarget(nextInstanceId: string) {
    const profile = profiles.find((candidate) => candidate.instanceId === nextInstanceId);
    onChange({
      ...draft,
      instanceId: nextInstanceId,
      profileId: profile?.id ?? '',
      model: profile?.defaultModel ?? '',
    });
  }

  function selectProfile(nextProfileId: string) {
    const profile = compatibleProfiles.find((candidate) => candidate.id === nextProfileId);
    onChange({
      ...draft,
      profileId: nextProfileId,
      model: profile?.defaultModel ?? '',
    });
  }

  return (
    <>
      <label className="rv-form-field">
        <span>Target</span>
        <select
          value={instanceId}
          onChange={(event) => selectTarget(event.target.value)}
          data-testid={`${testIdPrefix}-target`}
        >
          {targets.map((target) => (
            <option key={target.instanceId} value={target.instanceId}>
              {targetLabel(target)}
            </option>
          ))}
        </select>
      </label>

      {selectedProfile && (
        <div className="rv-profile-summary" data-testid={`${testIdPrefix}-profile-summary`}>
          <div>
            {selectedProfile.displayName}
            <span>
              {selectedProfile.backend} · {selectedProfile.engine}
            </span>
          </div>
          <p>{selectedProfile.description}</p>
        </div>
      )}

      <label className="rv-form-field">
        <span>Profile</span>
        <select
          value={selectedProfile?.id ?? ''}
          onChange={(event) => selectProfile(event.target.value)}
          data-testid={`${testIdPrefix}-profile`}
        >
          {compatibleProfiles.map((profile) => (
            <option key={`${profile.instanceId}:${profile.id}`} value={profile.id}>
              {profile.displayName}
            </option>
          ))}
        </select>
      </label>

      <label className="rv-form-field">
        <span>Name</span>
        <input
          value={draft.name}
          onChange={(event) => onChange({ ...draft, name: event.target.value })}
          required
          maxLength={255}
          data-testid={`${testIdPrefix}-name`}
        />
      </label>

      {showRole && (
        <label className="rv-form-field">
          <span>Mesh role</span>
          <input
            value={draft.role}
            onChange={(event) => onChange({ ...draft, role: event.target.value })}
            required
            maxLength={100}
            data-testid={`${testIdPrefix}-role`}
          />
        </label>
      )}

      <label className="rv-form-field">
        <span>Persona</span>
        <select
          value={draft.personaName}
          onChange={(event) => onChange({ ...draft, personaName: event.target.value })}
          data-testid={`${testIdPrefix}-persona`}
        >
          <option value="">No persona</option>
          {personas.map((persona) => (
            <option key={persona.name} value={persona.name}>
              {persona.name} · {persona.role}
            </option>
          ))}
        </select>
      </label>

      {selectedProfile && selectedProfile.allowedModels.length > 0 && (
        <label className="rv-form-field">
          <span>Model</span>
          <ResidentModelSelect
            allowedModels={selectedProfile.allowedModels}
            modelPrefix={selectedProfile.modelPrefix ?? ''}
            value={selectedModel}
            onChange={(model) => onChange({ ...draft, model })}
            testId={`${testIdPrefix}-model`}
          />
        </label>
      )}
    </>
  );
}
