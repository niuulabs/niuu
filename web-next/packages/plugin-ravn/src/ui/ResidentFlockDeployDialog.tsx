import { useMemo, useState } from 'react';
import { Dialog, DialogContent } from '@niuulabs/ui';
import { Users } from 'lucide-react';
import type { Ravn, ResidentEngine } from '../domain/ravn';
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

const MEMBER_SPECS: Array<{ engine: ResidentEngine; label: string; role: string }> = [
  { engine: 'ravn', label: 'Ravn coordinator', role: 'coordinator' },
  { engine: 'openclaw', label: 'NemoClaw specialist', role: 'specialist' },
  { engine: 'hermes', label: 'NemoHermes specialist', role: 'specialist' },
];

function initialDraft(spec: (typeof MEMBER_SPECS)[number]): ResidentMemberDraft {
  return {
    name: '',
    instanceId: '',
    profileId: '',
    personaName: '',
    model: '',
    role: spec.role,
  };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Flock deployment failed';
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
  const [drafts, setDrafts] = useState(() => MEMBER_SPECS.map(initialDraft));
  const profiles = useMemo(
    () => (profilesQuery.data ?? []).filter((profile) => profile.capabilities.includes('flock')),
    [profilesQuery.data],
  );
  const personas = useMemo(
    () => [...(personasQuery.data ?? [])].sort((a, b) => a.name.localeCompare(b.name)),
    [personasQuery.data],
  );

  const selectedProfiles = drafts.map((draft) => selectedResidentProfile(draft, profiles));
  const valid =
    Boolean(flockName.trim()) &&
    drafts.every((draft, index) => Boolean(draft.name.trim() && selectedProfiles[index]));

  function setOpen(nextOpen: boolean) {
    if (!nextOpen) deploy.reset();
    onOpenChange(nextOpen);
  }

  function updateDraft(index: number, draft: ResidentMemberDraft) {
    setDrafts((current) => current.map((item, itemIndex) => (itemIndex === index ? draft : item)));
  }

  function updateFlockName(name: string) {
    const previous = flockName.trim() || 'flock';
    const next = name.trim() || 'flock';
    setFlockName(name);
    setDrafts((current) =>
      current.map((draft, index) => ({
        ...draft,
        name:
          !draft.name || draft.name === `${previous}-${MEMBER_SPECS[index]!.engine}`
            ? `${next}-${MEMBER_SPECS[index]!.engine}`
            : draft.name,
      })),
    );
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
          personaName: draft.personaName.trim(),
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
        description="Launch a Ravn coordinator with NemoClaw and NemoHermes members."
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
          {!profilesQuery.isLoading && profiles.length > 0 && selectedProfiles.some((p) => !p) && (
            <div className="rv-form-error" role="alert">
              Ravn, NemoClaw, and NemoHermes resident profiles must be enabled.
            </div>
          )}

          {profiles.length > 0 && selectedProfiles.every(Boolean) && (
            <div className="rv-flock-members">
              {MEMBER_SPECS.map((member, index) => (
                <fieldset key={member.engine} className="rv-flock-member">
                  <legend>{member.label}</legend>
                  <ResidentDeployFields
                    draft={drafts[index]!}
                    profiles={profiles.filter((profile) => profile.engine === member.engine)}
                    personas={personas}
                    onChange={(draft) => updateDraft(index, draft)}
                    testIdPrefix={`flock-${member.engine}`}
                    showRole
                  />
                </fieldset>
              ))}
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
