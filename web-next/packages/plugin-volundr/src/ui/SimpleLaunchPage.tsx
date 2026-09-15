import { useMemo, useState } from 'react';
import { Link, useSearch } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { useOptionalService, useService } from '@niuulabs/plugin-sdk';
import type { IPersonaCatalog } from '@niuulabs/domain';
import {
  BranchSelect,
  Field,
  Input,
  LoadingState,
  RepoSelect,
  StateDot,
  Textarea,
  cn,
  relTime,
  type RepoRecord,
} from '@niuulabs/ui';
import { ArrowRight } from 'lucide-react';
import type { IVolundrService } from '../ports/IVolundrService';
import type { SessionDefinition } from '../models/volundr.model';
import { deriveCliTool, getDefinitionRune, validateSessionName } from './launchWizardModel';
import {
  defaultTargetId,
  quickLaunchName,
  quickLaunchSource,
  useQuickLaunch,
} from './hooks/useQuickLaunch';
import { useSessionList } from './hooks/useSessionStore';
import { SESSION_DOT, compareSessionsByActivity } from './sessions/sessionLabels';
import { LaunchWizard } from './LaunchWizard';

type RepoCatalogService = {
  getRepos(): Promise<RepoRecord[]>;
  getBranches(repoUrl: string): Promise<string[]>;
};

interface SimpleLaunchSearch {
  repo?: string;
  branch?: string;
  persona?: string;
  prompt?: string;
  back?: string;
}

/** Trimmed string search params only; anything else is ignored. */
function readParam(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

const RECENT_LIMIT = 3;

const WHAT_HAPPENS_NEXT = [
  'A sandbox boots with your repository checked out.',
  'The agent starts on the task and shows its work in the session.',
  'It asks you whenever it needs a decision, and you answer in the chat.',
];

/** The one line a card can show about an engine: its own words, else its providers. */
function definitionLine(definition: SessionDefinition): string {
  if (definition.description) return definition.description;
  if (definition.compatibleProviders.length === 0) return '';
  return `runs on ${definition.compatibleProviders.join(', ')}`;
}

const PRIMARY_BTN =
  'niuu:inline-flex niuu:items-center niuu:gap-2 niuu:rounded-full niuu:border niuu:border-brand niuu:bg-brand niuu:px-5 niuu:py-2.5 niuu:text-sm niuu:font-medium niuu:text-bg-primary niuu:transition-opacity niuu:hover:opacity-90 niuu:disabled:cursor-not-allowed niuu:disabled:opacity-50';

export function SimpleLaunchPage() {
  const search = useSearch({ strict: false }) as SimpleLaunchSearch;
  const volundr = useService<IVolundrService>('volundr');
  const repoCatalog = useService<RepoCatalogService>('niuu.repos');
  const personaCatalog = useOptionalService<IPersonaCatalog>('ravn.personas');
  const { launch, creating, error } = useQuickLaunch();

  const backTo = readParam(search.back);

  const [task, setTask] = useState(() => readParam(search.prompt));
  const [repo, setRepo] = useState(() => readParam(search.repo));
  const [branch, setBranch] = useState(() => readParam(search.branch));
  const [personaName, setPersonaName] = useState(() => readParam(search.persona));
  const [definitionKey, setDefinitionKey] = useState('');
  const [wizardOpen, setWizardOpen] = useState(false);

  const definitionsQuery = useQuery({
    queryKey: ['volundr', 'session-definitions'],
    queryFn: () => volundr.getSessionDefinitions(),
  });
  const targetsQuery = useQuery({
    queryKey: ['volundr', 'targets'],
    queryFn: () => volundr.getTargets(),
  });
  const reposQuery = useQuery({
    queryKey: ['niuu', 'repos'],
    queryFn: () => repoCatalog.getRepos(),
  });
  const personasQuery = useQuery({
    queryKey: ['ravn', 'personas'],
    queryFn: () => (personaCatalog as IPersonaCatalog).listPersonas(),
    enabled: Boolean(personaCatalog),
  });
  const sessionsQuery = useSessionList();

  const definitions = useMemo(() => definitionsQuery.data ?? [], [definitionsQuery.data]);
  const repos = useMemo(() => reposQuery.data ?? [], [reposQuery.data]);
  const personas = personasQuery.data ?? [];
  const targets = useMemo(
    () => (targetsQuery.data ?? []).filter((target) => target.enabled),
    [targetsQuery.data],
  );
  const recent = useMemo(
    () => [...(sessionsQuery.data ?? [])].sort(compareSessionsByActivity).slice(0, RECENT_LIMIT),
    [sessionsQuery.data],
  );

  const selectedDefinition: SessionDefinition | undefined =
    definitions.find((definition) => definition.key === definitionKey) ?? definitions[0];
  const sessionName = quickLaunchName('', repo);
  const nameError = validateSessionName(sessionName);
  const loadError = definitionsQuery.error ?? targetsQuery.error ?? reposQuery.error;
  const canStart =
    task.trim().length > 0 &&
    repo.trim().length > 0 &&
    Boolean(selectedDefinition) &&
    !nameError &&
    !creating &&
    !loadError;

  async function handleStart() {
    if (!canStart) return;
    await launch(
      {
        name: sessionName,
        source: quickLaunchSource(false, repo, branch),
        definition: selectedDefinition,
        instanceId: defaultTargetId(targets),
        initialPrompt: task,
        personaName,
      },
      backTo ? { returnTo: backTo } : {},
    );
  }

  if (wizardOpen) {
    return (
      <LaunchWizard
        open
        onOpenChange={setWizardOpen}
        initialForm={{
          sourcetype: 'git',
          repo: repo.trim(),
          branch: branch.trim(),
          sessionName,
          personaName,
          initialPrompt: task,
          definition: selectedDefinition?.key ?? '',
          model: selectedDefinition?.defaultModel ?? '',
        }}
      />
    );
  }

  return (
    <div
      className="niuu:mx-auto niuu:flex niuu:w-full niuu:max-w-5xl niuu:gap-10 niuu:px-8 niuu:py-10"
      data-testid="simple-launch-page"
    >
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col niuu:gap-6">
        <div className="niuu:flex niuu:flex-col niuu:gap-2">
          <span className="niuu:font-mono niuu:text-[10px] niuu:uppercase niuu:tracking-[0.2em] niuu:text-text-muted">
            new session
          </span>
          <h1 className="niuu:m-0 niuu:text-2xl niuu:font-semibold niuu:text-text-primary">
            What should it work on?
          </h1>
          <p className="niuu:m-0 niuu:text-sm niuu:text-text-secondary">
            Describe the task in your own words. A coding agent picks it up in its own sandbox.
          </p>
        </div>

        <Field label="The task">
          <Textarea
            value={task}
            onChange={(event) => setTask(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== 'Enter' || !(event.metaKey || event.ctrlKey)) return;
              event.preventDefault();
              void handleStart();
            }}
            rows={6}
            placeholder="e.g. Fix the failing auth tests and explain what was wrong."
            data-testid="simple-launch-task"
          />
        </Field>

        <div className="niuu:grid niuu:grid-cols-1 niuu:gap-4 niuu:sm:grid-cols-2">
          <Field label="Repository">
            {reposQuery.isFetching ? <LoadingState label="Loading repositories…" /> : null}
            {repos.length > 0 ? (
              <RepoSelect
                repos={repos}
                value={repo}
                onChange={(value) => {
                  const selected = repos.find((item) => item.cloneUrl === value);
                  setRepo(value);
                  setBranch(selected?.defaultBranch ?? '');
                }}
                valueMode="cloneUrl"
                testId="simple-launch-repo"
              />
            ) : (
              <Input
                aria-label="Repository"
                value={repo}
                onChange={(event) => setRepo(event.target.value)}
                placeholder="https://git.example.com/group/repository.git"
                data-testid="simple-launch-repo-input"
              />
            )}
          </Field>
          <Field label="Branch" hint="Optional — uses the repository default">
            {repo && repos.length > 0 ? (
              <BranchSelect
                loadBranches={repoCatalog.getBranches}
                repos={repos}
                selectedRepos={repo}
                value={branch}
                onChange={setBranch}
                testId="simple-launch-branch"
              />
            ) : (
              <Input
                aria-label="Branch"
                value={branch}
                onChange={(event) => setBranch(event.target.value)}
                data-testid="simple-launch-branch-input"
              />
            )}
          </Field>
        </div>

        <div className="niuu:flex niuu:flex-col niuu:gap-3">
          <span className="niuu:text-xs niuu:font-semibold niuu:text-text-primary">
            Who works on it
          </span>
          {definitionsQuery.isPending ? <LoadingState label="Loading engines…" /> : null}
          <div
            role="radiogroup"
            aria-label="Who works on it"
            className="niuu:grid niuu:grid-cols-1 niuu:gap-3 niuu:sm:grid-cols-3"
          >
            {definitions.map((definition) => {
              const active = definition.key === selectedDefinition?.key;
              return (
                <button
                  key={definition.key}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => setDefinitionKey(definition.key)}
                  data-testid={`simple-launch-engine-${deriveCliTool(definition.key)}`}
                  className={cn(
                    'niuu:flex niuu:flex-col niuu:gap-1.5 niuu:rounded-xl niuu:border niuu:bg-bg-primary niuu:p-4 niuu:text-left niuu:transition-colors',
                    active
                      ? 'niuu:border-brand niuu:bg-bg-tertiary'
                      : 'niuu:border-border-subtle niuu:hover:border-brand/40',
                  )}
                >
                  <span
                    aria-hidden
                    className="niuu:font-mono niuu:text-lg niuu:leading-none niuu:text-brand"
                  >
                    {getDefinitionRune(definition.key)}
                  </span>
                  <span className="niuu:text-sm niuu:font-medium niuu:text-text-primary">
                    {definition.displayName}
                  </span>
                  {definitionLine(definition) ? (
                    <span className="niuu:text-xs niuu:text-text-secondary">
                      {definitionLine(definition)}
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
          {!definitionsQuery.isPending && definitions.length === 0 ? (
            <p role="status" className="niuu:text-xs niuu:text-text-muted">
              No session engines are configured.
            </p>
          ) : null}
        </div>

        {personaCatalog && personas.length > 0 ? (
          <Field label="Work as" hint="Optional — a persona shapes how the agent works">
            <select
              aria-label="Work as"
              value={personaName}
              onChange={(event) => setPersonaName(event.target.value)}
              data-testid="simple-launch-persona"
              className="niuu:w-full niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary"
            >
              <option value="">No persona</option>
              {personas.map((persona) => (
                <option key={persona.name} value={persona.name}>
                  {persona.name} · {persona.role}
                </option>
              ))}
            </select>
          </Field>
        ) : null}

        <p className="niuu:m-0 niuu:text-xs niuu:text-text-muted">
          It asks before anything risky; you answer in the chat.
        </p>

        {personasQuery.error ? (
          <p role="alert" className="niuu:text-xs niuu:text-critical">
            Personas could not be loaded: {personasQuery.error.message}
          </p>
        ) : null}
        {loadError ? (
          <p role="alert" className="niuu:text-xs niuu:text-critical">
            {loadError.message}
          </p>
        ) : null}
        {error ? (
          <p
            role="alert"
            className="niuu:text-xs niuu:text-critical"
            data-testid="simple-launch-error"
          >
            {error}
          </p>
        ) : null}

        <div className="niuu:flex niuu:items-center niuu:gap-4">
          <button
            type="button"
            onClick={() => void handleStart()}
            disabled={!canStart}
            className={PRIMARY_BTN}
            data-testid="simple-launch-start"
          >
            {creating ? 'Starting…' : 'Start session'}
            <ArrowRight className="niuu:h-4 niuu:w-4" aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={() => setWizardOpen(true)}
            className="niuu:text-xs niuu:text-text-muted niuu:underline niuu:underline-offset-4 niuu:hover:text-text-primary"
            data-testid="simple-launch-more-options"
          >
            More options
          </button>
        </div>
      </div>

      <aside className="niuu:hidden niuu:w-64 niuu:flex-shrink-0 niuu:flex-col niuu:gap-8 niuu:lg:flex">
        <section className="niuu:flex niuu:flex-col niuu:gap-3">
          <h2 className="niuu:m-0 niuu:text-xs niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted">
            What happens next
          </h2>
          <ol className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:gap-3 niuu:p-0">
            {WHAT_HAPPENS_NEXT.map((step, index) => (
              <li key={step} className="niuu:flex niuu:gap-3">
                <span className="niuu:flex niuu:h-5 niuu:w-5 niuu:flex-shrink-0 niuu:items-center niuu:justify-center niuu:rounded-full niuu:border niuu:border-border-subtle niuu:font-mono niuu:text-[10px] niuu:text-text-muted">
                  {index + 1}
                </span>
                <span className="niuu:text-xs niuu:leading-relaxed niuu:text-text-secondary">
                  {step}
                </span>
              </li>
            ))}
          </ol>
        </section>

        <section className="niuu:flex niuu:flex-col niuu:gap-3" data-testid="simple-launch-recent">
          <h2 className="niuu:m-0 niuu:text-xs niuu:font-semibold niuu:uppercase niuu:tracking-[0.14em] niuu:text-text-muted">
            Recent
          </h2>
          {recent.length === 0 ? (
            <p className="niuu:m-0 niuu:text-xs niuu:text-text-muted">No sessions yet.</p>
          ) : (
            <ul className="niuu:m-0 niuu:flex niuu:list-none niuu:flex-col niuu:gap-2 niuu:p-0">
              {recent.map((session) => (
                <li key={session.id}>
                  <Link
                    to="/volundr/sessions/$sessionId"
                    params={{ sessionId: session.id }}
                    className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-xs niuu:text-text-secondary niuu:hover:text-text-primary"
                  >
                    <StateDot state={SESSION_DOT[session.state]} />
                    <span className="niuu:min-w-0 niuu:flex-1 niuu:truncate niuu:font-mono">
                      {session.name || session.personaName || session.id}
                    </span>
                    <span className="niuu:flex-shrink-0 niuu:font-mono niuu:text-[10px] niuu:text-text-muted">
                      {relTime(new Date(session.lastActivityAt ?? session.startedAt).getTime())}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </aside>
    </div>
  );
}
