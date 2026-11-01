import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import type { ValkyrieResident } from '@niuulabs/plugin-valkyrie';
import { SectionCard } from '@niuulabs/plugin-volundr';
import { Chip, Field, Modal } from '@niuulabs/ui';
import { useAdoptResident } from '../application/useProjects';
import type { ProjectChoice } from '../domain/projects';
import { ProjectPicker } from './ProjectPicker';

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-2.5 niuu:py-1 niuu:text-xs niuu:font-medium niuu:text-text-primary';
const CONTROL =
  'niuu:w-full niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-2.5 niuu:py-1.5 niuu:text-sm niuu:text-text-primary';

function AdoptDialog({ resident, onClose }: { resident: ValkyrieResident; onClose: () => void }) {
  const navigate = useNavigate();
  const adopt = useAdoptResident();
  const [name, setName] = useState(resident.name);
  const [choice, setChoice] = useState<ProjectChoice>({ projectId: '', newProjectName: '' });
  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={`A workspace for ${resident.name}`}
      description="The resident keeps running as it is. This only creates the workspace around it."
      actions={[
        { label: 'Cancel', variant: 'secondary' },
        {
          label: adopt.isPending ? 'Creating…' : 'Create workspace',
          variant: 'primary',
          disabled: adopt.isPending || !name.trim(),
          closes: false,
          onClick: () =>
            adopt.mutate(
              { resident, name, choice },
              {
                onSuccess: (realm) =>
                  void navigate({ to: '/realms/$slug', params: { slug: realm.slug } }),
              },
            ),
        },
      ]}
    >
      <div className="niuu:flex niuu:flex-col niuu:gap-3" data-testid="adopt-dialog">
        <Field label="Workspace name">
          <input
            aria-label="Workspace name"
            className={CONTROL}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field label="Project">
          <ProjectPicker value={choice} onChange={setChoice} testId="adopt-project" />
        </Field>
        {adopt.error ? (
          <span className="niuu:text-xs niuu:text-critical-fg">{String(adopt.error)}</span>
        ) : null}
      </div>
    </Modal>
  );
}

/** Residents that run but have no workspace yet, each one click from getting one. */
export function AdoptResidents({ residents }: { residents: ValkyrieResident[] }) {
  const [adopting, setAdopting] = useState<ValkyrieResident | null>(null);
  if (residents.length === 0) return null;
  return (
    <SectionCard
      title="Residents without a workspace"
      description="Already running. Give one a workspace to see it here with its work."
    >
      <div className="niuu:flex niuu:flex-col" data-testid="unclaimed-residents">
        {residents.map((resident) => (
          <div
            key={resident.id}
            className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2 niuu:last:border-b-0"
            data-testid={`unclaimed-${resident.id}`}
          >
            <span className="niuu:flex-1 niuu:text-sm niuu:text-text-primary">{resident.name}</span>
            <Chip tone="muted">{resident.status}</Chip>
            <button type="button" className={BUTTON} onClick={() => setAdopting(resident)}>
              Create workspace
            </button>
          </div>
        ))}
      </div>
      {adopting ? <AdoptDialog resident={adopting} onClose={() => setAdopting(null)} /> : null}
    </SectionCard>
  );
}
