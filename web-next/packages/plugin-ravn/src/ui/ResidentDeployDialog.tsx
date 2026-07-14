import { useMemo, useState } from 'react';
import { Dialog, DialogContent } from '@niuulabs/ui';
import { Rocket } from 'lucide-react';
import type { Ravn } from '../domain/ravn';
import { useDeployResident, useResidentProfiles } from './hooks/useResidentControl';
import { useOptionalPersonas } from './usePersonas';
import {
  ResidentDeployFields,
  selectedResidentProfile,
  type ResidentMemberDraft,
} from './ResidentDeployFields';

interface ResidentDeployDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeployed: (ravn: Ravn) => void;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Resident deployment failed';
}

export function ResidentDeployDialog({
  open,
  onOpenChange,
  onDeployed,
}: ResidentDeployDialogProps) {
  const profilesQuery = useResidentProfiles(open);
  const personasQuery = useOptionalPersonas(open);
  const deploy = useDeployResident();
  const [draft, setDraft] = useState<ResidentMemberDraft>({
    name: '',
    instanceId: '',
    profileId: '',
    personaName: '',
    model: '',
    role: '',
  });

  const profiles = useMemo(() => profilesQuery.data ?? [], [profilesQuery.data]);
  const personas = useMemo(
    () =>
      [...(personasQuery.data ?? [])].sort((left, right) => left.name.localeCompare(right.name)),
    [personasQuery.data],
  );
  const selectedProfile = selectedResidentProfile(draft, profiles);

  function setOpen(nextOpen: boolean) {
    if (!nextOpen) {
      deploy.reset();
      setDraft((current) => ({ ...current, name: '', personaName: '' }));
    }
    onOpenChange(nextOpen);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!selectedProfile || !draft.name.trim()) return;
    let ravn: Ravn;
    try {
      ravn = await deploy.mutateAsync({
        name: draft.name.trim(),
        profileId: selectedProfile.id,
        instanceId: selectedProfile.instanceId,
        personaName: draft.personaName.trim(),
        model: draft.model || selectedProfile.defaultModel,
      });
    } catch {
      return;
    }
    onDeployed(ravn);
    setOpen(false);
    setDraft((current) => ({ ...current, name: '', personaName: '' }));
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
              <ResidentDeployFields
                draft={draft}
                profiles={profiles}
                personas={personas}
                onChange={setDraft}
                testIdPrefix="resident"
              />
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
              disabled={!selectedProfile || !draft.name.trim() || deploy.isPending}
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
