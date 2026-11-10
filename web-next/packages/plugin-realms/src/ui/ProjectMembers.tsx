import { useState } from 'react';
import {
  PROJECT_ROLES,
  SectionCard,
  type ForgeProject,
  type ProjectMember,
  type ProjectRole,
} from '@niuulabs/plugin-volundr';
import { authorInitials } from '@niuulabs/ui';
import {
  canManageMembers,
  useAddProjectMember,
  useProjectMemberCandidates,
  useProjectMembers,
  useRemoveProjectMember,
  useUpdateProjectMember,
  useViewer,
} from '../application/useProjectMembers';

export const CONTROL =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-2.5 niuu:py-1.5 niuu:text-sm niuu:text-text-primary';
export const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-text-primary niuu:disabled:opacity-40';

const ROLE_LABEL: Record<ProjectRole, string> = {
  viewer: 'Viewer',
  contributor: 'Contributor',
  approver: 'Approver',
  owner: 'Owner',
};

/** What each role adds, said once under the list so the choice is informed. */
const ROLE_HELP =
  'Viewers chat in sessions and steer them. Contributors also start sessions and edit workflows and tools. Approvers also decide gates and answer for the project. Owners also manage members.';

function memberName(member: { display_name: string; email: string; user_id: string }): string {
  return member.display_name || member.email || member.user_id;
}

function Avatar({ member }: { member: ProjectMember }) {
  return (
    <span
      aria-hidden="true"
      className="niuu:flex niuu:h-7 niuu:w-7 niuu:shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:bg-brand/15 niuu:text-[11px] niuu:font-semibold niuu:text-brand-300"
    >
      {authorInitials({ userId: member.user_id, displayName: memberName(member) })}
    </span>
  );
}

function RoleSelect({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: ProjectRole;
  onChange: (role: ProjectRole) => void;
  disabled?: boolean;
}) {
  return (
    <select
      aria-label={label}
      className={CONTROL}
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value as ProjectRole)}
    >
      {PROJECT_ROLES.map((role) => (
        <option key={role} value={role}>
          {ROLE_LABEL[role]}
        </option>
      ))}
    </select>
  );
}

/** Find someone in the org and add them with a role. Owners only. */
function AddMember({ project }: { project: ForgeProject }) {
  const [query, setQuery] = useState('');
  const [chosen, setChosen] = useState<string | null>(null);
  const [role, setRole] = useState<ProjectRole>('contributor');
  const candidates = useProjectMemberCandidates(project, query.trim(), query.trim().length > 0);
  const add = useAddProjectMember(project);

  const submit = () => {
    if (!chosen) return;
    add.mutate(
      { userId: chosen, role },
      {
        onSuccess: () => {
          setQuery('');
          setChosen(null);
        },
      },
    );
  };

  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-2" data-testid="project-member-add">
      <div className="niuu:flex niuu:gap-2">
        <input
          aria-label="Find someone in your org"
          className={`${CONTROL} niuu:min-w-0 niuu:flex-1`}
          placeholder="Name or email"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setChosen(null);
          }}
        />
        <RoleSelect label="Role for the new member" value={role} onChange={setRole} />
        <button
          type="button"
          className={BUTTON}
          disabled={!chosen || add.isPending}
          onClick={submit}
        >
          {add.isPending ? 'Adding…' : 'Add'}
        </button>
      </div>
      {query.trim() && !chosen ? (
        <ul className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:gap-1 niuu:p-0">
          {(candidates.data ?? []).map((person) => (
            <li key={person.user_id}>
              <button
                type="button"
                className="niuu:w-full niuu:rounded-md niuu:px-2 niuu:py-1 niuu:text-left niuu:text-sm niuu:text-text-primary niuu:hover:bg-bg-tertiary"
                onClick={() => {
                  setChosen(person.user_id);
                  setQuery(memberName(person));
                }}
              >
                {memberName(person)}
                {person.email && person.display_name ? (
                  <span className="niuu:ml-2 niuu:text-xs niuu:text-text-muted">
                    {person.email}
                  </span>
                ) : null}
              </button>
            </li>
          ))}
          {candidates.isSuccess && candidates.data.length === 0 ? (
            <li className="niuu:px-2 niuu:text-xs niuu:text-text-muted">
              Nobody else in your org matches.
            </li>
          ) : null}
        </ul>
      ) : null}
      {add.error ? (
        <span className="niuu:text-xs niuu:text-critical-fg">{String(add.error)}</span>
      ) : null}
    </div>
  );
}

function MemberRow({
  project,
  member,
  manage,
  isViewer,
}: {
  project: ForgeProject;
  member: ProjectMember;
  manage: boolean;
  isViewer: boolean;
}) {
  const update = useUpdateProjectMember(project);
  const remove = useRemoveProjectMember(project);
  const [editing, setEditing] = useState(false);
  const [expertise, setExpertise] = useState(member.expertise);
  const name = memberName(member);
  const error = update.error ?? remove.error;

  return (
    <li
      className="niuu:flex niuu:flex-col niuu:gap-1 niuu:border-t niuu:border-border-subtle niuu:py-2"
      data-testid={`project-member-${member.user_id}`}
    >
      <div className="niuu:flex niuu:items-center niuu:gap-3">
        <Avatar member={member} />
        <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
          <span className="niuu:truncate niuu:text-sm niuu:text-text-primary">
            {name}
            {isViewer ? <span className="niuu:ml-1 niuu:text-text-muted">(you)</span> : null}
          </span>
          {member.expertise && !editing ? (
            <span className="niuu:text-xs niuu:text-text-secondary">{member.expertise}</span>
          ) : null}
          {member.skills.length > 0 ? (
            <span className="niuu:mt-1 niuu:flex niuu:flex-wrap niuu:gap-1">
              {member.skills.map((skill) => (
                <span
                  key={skill.id}
                  className="niuu:rounded niuu:bg-bg-tertiary niuu:px-1.5 niuu:py-0.5 niuu:text-[11px] niuu:text-text-secondary"
                >
                  {skill.name}
                </span>
              ))}
            </span>
          ) : null}
        </div>
        {manage ? null : (
          <span className="niuu:text-xs niuu:text-text-muted">{ROLE_LABEL[member.role]}</span>
        )}
      </div>
      {manage || isViewer ? (
        <div className="niuu:flex niuu:flex-wrap niuu:items-center niuu:gap-2 niuu:pl-10">
          {manage ? (
            <RoleSelect
              label={`Role of ${name}`}
              value={member.role}
              disabled={update.isPending}
              onChange={(role) => update.mutate({ userId: member.user_id, change: { role } })}
            />
          ) : null}
          <button
            type="button"
            className={BUTTON}
            aria-label={`Edit expertise of ${name}`}
            onClick={() => setEditing((open) => !open)}
          >
            Expertise
          </button>
          {manage ? (
            <button
              type="button"
              className={BUTTON}
              aria-label={`Remove ${name}`}
              disabled={remove.isPending}
              onClick={() => remove.mutate(member.user_id)}
            >
              Remove
            </button>
          ) : null}
        </div>
      ) : null}
      {editing ? (
        <div className="niuu:flex niuu:gap-2 niuu:pl-10">
          <input
            aria-label={`Expertise of ${name}`}
            className={`${CONTROL} niuu:min-w-0 niuu:flex-1`}
            placeholder="What they know most about, for example push delivery and iOS"
            value={expertise}
            onChange={(event) => setExpertise(event.target.value)}
          />
          <button
            type="button"
            className={BUTTON}
            disabled={update.isPending || expertise === member.expertise}
            onClick={() =>
              update.mutate(
                { userId: member.user_id, change: { expertise } },
                { onSuccess: () => setEditing(false) },
              )
            }
          >
            Save
          </button>
        </div>
      ) : null}
      {error ? <span className="niuu:text-xs niuu:text-critical-fg">{String(error)}</span> : null}
    </li>
  );
}

/** Who is on the project, what they know, and what they may do. */
export function ProjectMembers({ project }: { project: ForgeProject }) {
  const members = useProjectMembers(project);
  const viewer = useViewer();
  const list = members.data ?? [];
  const manage = canManageMembers(list, viewer.data);

  return (
    <SectionCard
      title={`Members · ${list.length}`}
      description="People in your org who share this project. They chat in its sessions on the session owner's credentials."
    >
      <div className="niuu:flex niuu:flex-col niuu:gap-3" data-testid="project-members">
        {manage ? <AddMember project={project} /> : null}
        {members.error ? (
          <span className="niuu:text-xs niuu:text-critical-fg">{String(members.error)}</span>
        ) : null}
        <ul className="niuu:m-0 niuu:list-none niuu:p-0">
          {list.map((member) => (
            <MemberRow
              key={member.user_id}
              project={project}
              member={member}
              manage={manage}
              isViewer={member.user_id === viewer.data?.userId}
            />
          ))}
        </ul>
        <p className="niuu:m-0 niuu:text-xs niuu:text-text-muted">{ROLE_HELP}</p>
      </div>
    </SectionCard>
  );
}
