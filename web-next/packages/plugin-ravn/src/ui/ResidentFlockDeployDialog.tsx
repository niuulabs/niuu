import { useMemo, useState } from 'react';
import { Dialog, DialogContent } from '@niuulabs/ui';
import type { PersonaSummary } from '@niuulabs/domain';
import { ChevronDown, ChevronUp, Plus, Trash2, Users } from 'lucide-react';
import type { Ravn, ResidentDeploymentProfile } from '../domain/ravn';
import { useDeployResidentFlock, useResidentProfiles } from './hooks/useResidentControl';
import { useOptionalPersonas } from './usePersonas';
import {
  ResidentDeployFields,
  selectedResidentProfile,
  type ResidentMemberDraft,
} from './ResidentDeployFields';

interface ResidentFlockDeployDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeployed: (ravns: Ravn[]) => void;
}

interface FlockMemberDraft extends ResidentMemberDraft {
  key: string;
}

function initialDraft(role = 'specialist'): FlockMemberDraft {
  return {
    key: crypto.randomUUID(),
    name: '',
    instanceId: '',
    profileId: '',
    personaName: '',
    model: '',
    role,
  };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Flock deployment failed';
}

function defaultMemberName(
  flockName: string,
  profile: ResidentDeploymentProfile | undefined,
  index: number,
): string {
  return `${flockName.trim() || 'flock'}-${profile?.engine ?? 'member'}-${index + 1}`;
}

function coordinatorPersonas(personas: PersonaSummary[]): PersonaSummary[] {
  return personas.filter((persona) => persona.allowedTools.includes('cascade'));
}

export function ResidentFlockDeployDialog({
  open,
  onOpenChange,
  onDeployed,
}: ResidentFlockDeployDialogProps) {
  const profilesQuery = useResidentProfiles(open);
  const personasQuery = useOptionalPersonas(open);
  const deploy = useDeployResidentFlock();
  const [flockName, setFlockName] = useState('');
  const [drafts, setDrafts] = useState<FlockMemberDraft[]>(() => [initialDraft('coordinator')]);
  const profiles = useMemo(
    () =>
      (profilesQuery.data ?? [])
        .filter((profile) => profile.capabilities.includes('flock'))
        .sort((left, right) => {
          if (left.engine === 'ravn' && right.engine !== 'ravn') return -1;
          if (right.engine === 'ravn' && left.engine !== 'ravn') return 1;
          return left.displayName.localeCompare(right.displayName);
        }),
    [profilesQuery.data],
  );
  const personas = useMemo(
    () => [...(personasQuery.data ?? [])].sort((a, b) => a.name.localeCompare(b.name)),
    [personasQuery.data],
  );
  const coordinators = useMemo(() => coordinatorPersonas(personas), [personas]);
  const preferredCoordinator = coordinators.find((persona) => persona.name === 'flock-coordinator');
  const selectedProfiles = drafts.map((draft) => selectedResidentProfile(draft, profiles));
  const normalizedNames = drafts.map((draft) => draft.name.trim().toLowerCase());
  const duplicateNames = new Set(
    normalizedNames.filter((name, index) => name && normalizedNames.indexOf(name) !== index),
  );
  const coordinatorCount = drafts.filter((draft) => draft.role.trim() === 'coordinator').length;
  const coordinatorReady = drafts
    .filter((draft) => draft.role.trim() === 'coordinator')
    .every((draft) =>
      coordinators.some(
        (persona) => persona.name === (draft.personaName || preferredCoordinator?.name),
      ),
    );
  const valid =
    Boolean(flockName.trim()) &&
    drafts.length >= 2 &&
    coordinatorCount === 1 &&
    coordinatorReady &&
    duplicateNames.size === 0 &&
    drafts.every((draft, index) => Boolean(draft.name.trim() && selectedProfiles[index]));

  function setOpen(nextOpen: boolean) {
    if (!nextOpen) deploy.reset();
    onOpenChange(nextOpen);
  }

  function updateDraft(index: number, draft: ResidentMemberDraft) {
    setDrafts((current) =>
      current.map((item, itemIndex) => (itemIndex === index ? { ...draft, key: item.key } : item)),
    );
  }

  function updateFlockName(name: string) {
    const previous = flockName.trim() || 'flock';
    setFlockName(name);
    setDrafts((current) =>
      current.map((draft, index) => ({
        ...draft,
        name:
          !draft.name || draft.name.startsWith(`${previous}-`)
            ? defaultMemberName(name, selectedResidentProfile(draft, profiles), index)
            : draft.name,
      })),
    );
  }

  function addMember() {
    const profile = profiles[drafts.length % Math.max(profiles.length, 1)];
    const draft = initialDraft();
    draft.instanceId = profile?.instanceId ?? '';
    draft.profileId = profile?.id ?? '';
    draft.model = profile?.defaultModel ?? '';
    draft.name = defaultMemberName(flockName, profile, drafts.length);
    setDrafts((current) => [...current, draft]);
  }

  function removeMember(index: number) {
    setDrafts((current) => current.filter((_, itemIndex) => itemIndex !== index));
  }

  function moveMember(index: number, offset: -1 | 1) {
    const destination = index + offset;
    if (destination < 0 || destination >= drafts.length) return;
    setDrafts((current) => {
      const next = [...current];
      [next[index], next[destination]] = [next[destination]!, next[index]!];
      return next;
    });
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!valid) return;
    const flockId = crypto.randomUUID();
    try {
      const requests = drafts.map((draft, index) => {
        const profile = selectedProfiles[index]!;
        const memberId = crypto.randomUUID();
        return {
          name: draft.name.trim(),
          profileId: profile.id,
          instanceId: profile.instanceId,
          personaName:
            draft.role === 'coordinator'
              ? (draft.personaName || preferredCoordinator?.name || '').trim()
              : draft.personaName.trim(),
          model: draft.model || profile.defaultModel,
          flockId,
          flockMemberId: memberId,
          flockRole: draft.role.trim(),
          flockPeerId: `${profile.engine}-${memberId}`,
        };
      });
      const ravns = await deploy.mutateAsync(requests);
      onDeployed(ravns);
      setOpen(false);
    } catch {
      return;
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent
        title="Deploy flock"
        description="Compose a resident flock from the deployment profiles available on your targets."
        className="rv-flock-deploy-dialog"
      >
        <form className="rv-deploy-form" onSubmit={(event) => void submit(event)}>
          <label className="rv-form-field">
            <span>Flock name</span>
            <input
              value={flockName}
              onChange={(event) => updateFlockName(event.target.value)}
              required
              maxLength={100}
              data-testid="flock-name"
            />
          </label>

          {profilesQuery.isLoading && <div className="rv-form-state">Loading profiles…</div>}
          {profilesQuery.isError && (
            <div className="rv-form-error" role="alert">
              {errorMessage(profilesQuery.error)}
            </div>
          )}
          {!profilesQuery.isLoading && profiles.length === 0 && (
            <div className="rv-form-error" role="alert">
              No flock-capable resident profiles are enabled.
            </div>
          )}
          {!personasQuery.isLoading && coordinators.length === 0 && (
            <div className="rv-form-error" role="alert">
              A persona with cascade tools is required for the coordinator.
            </div>
          )}
          {drafts.length < 2 && profiles.length > 0 && (
            <div className="rv-form-state">Add at least one member to the coordinator.</div>
          )}
          {coordinatorCount !== 1 && (
            <div className="rv-form-error" role="alert">
              A flock requires exactly one coordinator.
            </div>
          )}
          {duplicateNames.size > 0 && (
            <div className="rv-form-error" role="alert">
              Member names must be unique.
            </div>
          )}

          {profiles.length > 0 && (
            <div className="rv-flock-members">
              {drafts.map((draft, index) => {
                const coordinator = draft.role === 'coordinator';
                return (
                  <fieldset key={draft.key} className="rv-flock-member">
                    <legend>{coordinator ? 'Coordinator' : `Member ${index + 1}`}</legend>
                    <div className="rv-flock-member__tools">
                      <button
                        type="button"
                        onClick={() => moveMember(index, -1)}
                        disabled={index === 0}
                        aria-label={`Move member ${index + 1} up`}
                        title="Move up"
                      >
                        <ChevronUp size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        onClick={() => moveMember(index, 1)}
                        disabled={index === drafts.length - 1}
                        aria-label={`Move member ${index + 1} down`}
                        title="Move down"
                      >
                        <ChevronDown size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        onClick={() => removeMember(index)}
                        disabled={drafts.length === 1}
                        aria-label={`Remove member ${index + 1}`}
                        title="Remove member"
                      >
                        <Trash2 size={14} aria-hidden="true" />
                      </button>
                    </div>
                    <ResidentDeployFields
                      draft={{
                        ...draft,
                        personaName:
                          coordinator && !draft.personaName && preferredCoordinator
                            ? preferredCoordinator.name
                            : draft.personaName,
                      }}
                      profiles={profiles}
                      personas={coordinator ? coordinators : personas}
                      onChange={(nextDraft) => updateDraft(index, nextDraft)}
                      testIdPrefix={`flock-member-${index}`}
                      showRole
                    />
                  </fieldset>
                );
              })}
              <button
                type="button"
                className="rv-flock-member-add"
                onClick={addMember}
                data-testid="flock-add-member"
              >
                <Plus size={16} aria-hidden="true" />
                Add member
              </button>
            </div>
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
              disabled={!valid || deploy.isPending}
              data-testid="flock-deploy-submit"
            >
              <Users size={14} aria-hidden="true" />
              {deploy.isPending ? 'Deploying…' : 'Deploy flock'}
            </button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
