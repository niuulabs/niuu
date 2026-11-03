import { useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { Field, Modal } from '@niuulabs/ui';
import { useCreateProject } from '../application/useProjects';

const CONTROL =
  'niuu:w-full niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-2.5 niuu:py-1.5 niuu:text-sm niuu:text-text-primary';

/** A project before any workspace: a name now, its brief and workspaces on its own page. */
export function NewProjectDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const create = useCreateProject();
  const [name, setName] = useState('');
  const ready = name.trim().length > 0 && !create.isPending;

  const submit = () => {
    if (!ready) return;
    create.mutate(name, {
      onSuccess: (project) =>
        void navigate({
          to: '/realms/projects/$projectId' as never,
          params: { projectId: project.id } as never,
        }),
    });
  };

  return (
    <Modal
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="New project"
      description="A project holds workspaces and the brief they share. You add the workspaces next."
      actions={[
        { label: 'Cancel', variant: 'secondary' },
        {
          label: create.isPending ? 'Creating…' : 'Create project',
          variant: 'primary',
          disabled: !ready,
          closes: false,
          onClick: submit,
        },
      ]}
    >
      <form
        className="niuu:flex niuu:flex-col niuu:gap-3"
        data-testid="new-project-dialog"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Field label="Project name">
          <input
            aria-label="Project name"
            className={CONTROL}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        {create.error ? (
          <span className="niuu:text-xs niuu:text-critical-fg">{String(create.error)}</span>
        ) : null}
      </form>
    </Modal>
  );
}
