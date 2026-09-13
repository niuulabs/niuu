import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearch } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '@niuulabs/plugin-mimir';
import {
  ResidentDeployFields,
  selectedResidentProfile,
  useResidentProfiles,
  type IPersonaStore,
  type ResidentMemberDraft,
} from '@niuulabs/plugin-ravn';
import type { ITrackerBrowserService } from '@niuulabs/plugin-ting';
import { useRealmTrustGrants } from '@niuulabs/plugin-valkyrie';
import { ConfirmRow, SectionCard, StepIndicator, type IVolundrService } from '@niuulabs/plugin-volundr';
import {
  BranchSelect,
  Chip,
  ErrorState,
  Field,
  Input,
  LoadingState,
  RepoSelect,
  SegmentedFilter,
  Select,
  Textarea,
} from '@niuulabs/ui';
import { useCreateRealm } from '../application/useCreateRealm';
import { FIRST_REALM_WALKTHROUGH, useWalkthrough } from '../application/useWalkthrough';
import { bindingFromGrants, latestGrants } from '../domain/join';
import {
  ACTION_CLASSES,
  ACTION_CLASS_COPY,
  EMPTY_DRAFT,
  personaNameFor,
  slugify,
  trustSettingForLevel,
  type RealmDraft,
  type TrustPreset,
  type TrustSetting,
} from '../domain/realm';
import { parseSentence } from '../domain/sentence';
import { REALM_TEMPLATES, templateById } from '../domain/templates';
import { RecipeChecklist } from './RecipeChecklist';
import { WalkthroughRail } from './WalkthroughRail';

const STEPS = ['template', 'connect', 'charter', 'launch'] as const;
type Step = (typeof STEPS)[number];
const STEP_LABELS: Record<Step, string> = {
  template: 'Template',
  connect: 'Connect',
  charter: 'Charter & limits',
  launch: 'Launch',
};

const BUTTON =
  'niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-3 niuu:py-1.5 niuu:text-xs niuu:font-medium niuu:text-text-primary niuu:disabled:opacity-40';
const CTA =
  'niuu:rounded-full niuu:bg-brand niuu:px-5 niuu:py-2 niuu:text-sm niuu:font-semibold niuu:text-bg-primary niuu:disabled:opacity-40';

const TRUST_OPTIONS: Array<{ value: TrustSetting; label: string }> = [
  { value: 'auto', label: 'on its own' },
  { value: 'ask', label: 'asks first' },
  { value: 'never', label: 'never' },
];

interface NewRealmSearch {
  sentence?: string;
  from?: string;
  template?: string;
}

function TemplateStep({ draft, onChange }: { draft: RealmDraft; onChange: (next: RealmDraft) => void }) {
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-3" data-testid="step-template">
      {REALM_TEMPLATES.map((template) => {
        const checked = template.id === draft.templateId;
        return (
          <button
            key={template.id}
            type="button"
            onClick={() => onChange({ ...draft, templateId: template.id, trust: template.trust })}
            className={`niuu:flex niuu:gap-3 niuu:rounded-lg niuu:border niuu:p-4 niuu:text-left ${
              checked
                ? 'niuu:border-brand/60 niuu:bg-brand/10'
                : 'niuu:border-border-subtle niuu:bg-bg-primary'
            }`}
            data-testid={`template-${template.id}`}
            aria-pressed={checked}
          >
            <span
              className={`niuu:mt-0.5 niuu:h-[18px] niuu:w-[18px] niuu:shrink-0 niuu:rounded-full niuu:border ${
                checked ? 'niuu:border-[5px] niuu:border-brand' : 'niuu:border-brand/40'
              }`}
            />
            <span className="niuu:flex niuu:flex-1 niuu:flex-col niuu:gap-2">
              <span className="niuu:flex niuu:items-center niuu:justify-between">
                <span className="niuu:text-[15px] niuu:font-medium niuu:text-text-primary">{template.name}</span>
                {template.recommended ? <Chip tone="brand">Recommended</Chip> : null}
              </span>
              <span className="niuu:text-xs niuu:text-text-secondary">{template.blurb}</span>
              <span className="niuu:grid niuu:grid-cols-2 niuu:gap-3 niuu:pt-1">
                <span className="niuu:flex niuu:flex-col niuu:gap-1">
                  <span className="niuu:text-[10px] niuu:uppercase niuu:tracking-wider niuu:text-text-muted">
                    It keeps doing
                  </span>
                  {template.keepsDoing.map((line) => (
                    <span key={line} className="niuu:text-xs niuu:text-text-secondary">
                      · {line}
                    </span>
                  ))}
                </span>
                <span className="niuu:flex niuu:flex-col niuu:gap-1">
                  <span className="niuu:text-[10px] niuu:uppercase niuu:tracking-wider niuu:text-text-muted">
                    It needs
                  </span>
                  <span className="niuu:flex niuu:flex-wrap niuu:gap-1.5">
                    {template.needs.map((need) => (
                      <Chip key={need} tone="muted">
                        {need}
                      </Chip>
                    ))}
                  </span>
                </span>
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

function ConnectStep({ draft, onChange }: { draft: RealmDraft; onChange: (next: RealmDraft) => void }) {
  const volundr = useService<IVolundrService>('volundr');
  const tracker = useService<ITrackerBrowserService>('ting.tracker');
  const mimir = useService<IMimirService>('mimir');
  const template = templateById(draft.templateId);

  const repos = useQuery({ queryKey: ['volundr', 'repos'], queryFn: () => volundr.getRepos() });
  const boards = useQuery({ queryKey: ['ting', 'tracker', 'boards'], queryFn: () => tracker.listProjects() });
  const integrations = useQuery({ queryKey: ['volundr', 'integrations'], queryFn: () => volundr.getIntegrations() });
  const mcpServers = useQuery({ queryKey: ['volundr', 'mcp-servers'], queryFn: () => volundr.getAvailableMcpServers() });
  const deployments = useQuery({
    queryKey: ['mimir', 'deployments'],
    queryFn: () => {
      if (!mimir.mounts.getDeployments) {
        throw new Error('This Mímir has no deployment target; realm memory cannot be created.');
      }
      return mimir.mounts.getDeployments();
    },
  });

  const targets = useMemo(() => {
    const status = deployments.data;
    if (!status) return [];
    const ids = (status.targets ?? []).map((target) => target.id);
    if (ids.length > 0) return ids;
    return status.target ? [status.target] : [];
  }, [deployments.data]);

  useEffect(() => {
    if (!draft.mountTarget && targets[0]) onChange({ ...draft, mountTarget: targets[0] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targets]);

  const boardOptions = (boards.data ?? []).map((board) => ({
    value: board.id,
    label: board.slug ? `${board.name} · ${board.slug}` : board.name,
  }));

  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-4" data-testid="step-connect">
      <SectionCard title="Repository" description="Where the resident works. Cloned into a sandbox for every session.">
        {repos.error ? (
          <ErrorState message={String(repos.error)} />
        ) : repos.isLoading ? (
          <LoadingState label="Loading repositories…" />
        ) : (
          <div className="niuu:grid niuu:grid-cols-2 niuu:gap-3">
            <Field label="Repository">
              <RepoSelect
                repos={repos.data ?? []}
                value={draft.repo}
                onChange={(repo) => onChange({ ...draft, repo, branch: '' })}
                valueMode="slug"
                testId="realm-repo"
              />
            </Field>
            <Field label="Branch" hint="Optional — uses the repository default">
              <BranchSelect
                repos={repos.data ?? []}
                selectedRepos={draft.repo}
                value={draft.branch}
                onChange={(branch) => onChange({ ...draft, branch })}
                testId="realm-branch"
              />
            </Field>
          </div>
        )}
      </SectionCard>

      <SectionCard
        title="Tracker board"
        description={
          template.needsBoard
            ? 'Where work comes from. The resident reads the board and moves tickets as it works.'
            : 'Optional for this template.'
        }
      >
        {boards.error ? (
          <ErrorState message={String(boards.error)} />
        ) : (
          <div className="niuu:grid niuu:grid-cols-2 niuu:gap-3">
            <Field label="Board">
              <Select
                options={boardOptions}
                placeholder={boards.isLoading ? 'Loading boards…' : 'Pick a board'}
                value={draft.trackerBoard}
                onValueChange={(trackerBoard) => onChange({ ...draft, trackerBoard })}
              />
            </Field>
            <Field label="Bug board" hint="Optional — where your QA team files findings">
              <Select
                options={[{ value: '', label: 'Use the tracker board' }, ...boardOptions]}
                value={draft.bugBoard}
                onValueChange={(bugBoard) => onChange({ ...draft, bugBoard })}
              />
            </Field>
          </div>
        )}
      </SectionCard>

      <SectionCard title="Connections" description="Tested before launch. A failing one stops the launch; nothing is skipped.">
        <div className="niuu:flex niuu:flex-col niuu:gap-2">
          {(integrations.data ?? []).length === 0 ? (
            <span className="niuu:text-xs niuu:text-text-muted">
              No integrations connected yet. Add them under Settings › Integrations.
            </span>
          ) : (
            (integrations.data ?? []).map((integration) => {
              const checked = draft.integrationIds.includes(integration.id);
              return (
                <label key={integration.id} className="niuu:flex niuu:items-center niuu:gap-2 niuu:text-sm niuu:text-text-primary">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() =>
                      onChange({
                        ...draft,
                        integrationIds: checked
                          ? draft.integrationIds.filter((id) => id !== integration.id)
                          : [...draft.integrationIds, integration.id],
                      })
                    }
                  />
                  {integration.slug ?? integration.integrationType ?? integration.id}
                  {integration.enabled === false ? <Chip tone="muted">disabled</Chip> : null}
                </label>
              );
            })
          )}
          {(mcpServers.data ?? []).length > 0 ? (
            <div className="niuu:flex niuu:flex-wrap niuu:gap-1.5 niuu:pt-1">
              <span className="niuu:text-[10px] niuu:uppercase niuu:tracking-wider niuu:text-text-muted">
                MCP servers available
              </span>
              {(mcpServers.data ?? []).map((server) => (
                <Chip key={server.name} tone="muted">
                  {server.name}
                </Chip>
              ))}
            </div>
          ) : null}
        </div>
      </SectionCard>

      <SectionCard title="Realm memory" description="A Mímir mount is created for this realm; the resident writes what it learns there.">
        {deployments.error ? (
          <ErrorState message={String(deployments.error)} />
        ) : (
          <Field label="Created on">
            <Select
              options={targets.map((target) => ({ value: target, label: target }))}
              placeholder={deployments.isLoading ? 'Loading targets…' : 'No deployment target configured'}
              value={draft.mountTarget}
              onValueChange={(mountTarget) => onChange({ ...draft, mountTarget })}
            />
          </Field>
        )}
      </SectionCard>
    </div>
  );
}

function CharterStep({ draft, onChange }: { draft: RealmDraft; onChange: (next: RealmDraft) => void }) {
  const profiles = useResidentProfiles(true);
  const memberDraft: ResidentMemberDraft = {
    name: draft.slug,
    instanceId: draft.instanceId,
    profileId: draft.profileId,
    personaName: '',
    model: draft.model,
    role: '',
  };

  useEffect(() => {
    if (draft.profileId || !profiles.data?.[0]) return;
    const first = profiles.data[0];
    onChange({ ...draft, profileId: first.id, instanceId: first.instanceId, model: first.defaultModel });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profiles.data]);

  return (
    <div className="niuu:grid niuu:grid-cols-2 niuu:gap-4" data-testid="step-charter">
      <div className="niuu:flex niuu:flex-col niuu:gap-4">
        <SectionCard title="Charter" description="A seed, not a rulebook. The resident builds its own understanding from the code, the tickets and what happens.">
          <div className="niuu:flex niuu:flex-col niuu:gap-3">
            <Field label="What matters">
              <Textarea
                rows={6}
                value={draft.charter}
                onChange={(event) => onChange({ ...draft, charter: event.target.value })}
                placeholder="Keep the platform shippable every day. Work the board in priority order, but a red main branch beats any ticket. Never lower coverage gates."
                data-testid="realm-charter"
              />
            </Field>
            <div className="niuu:grid niuu:grid-cols-2 niuu:gap-3">
              <Field label="Name">
                <Input
                  value={draft.name}
                  onChange={(event) =>
                    onChange({ ...draft, name: event.target.value, slug: slugify(event.target.value) })
                  }
                  data-testid="realm-name"
                />
              </Field>
              <Field label="Slug" hint="Names the resident and its memory">
                <Input
                  value={draft.slug}
                  onChange={(event) => onChange({ ...draft, slug: slugify(event.target.value) })}
                  data-testid="realm-slug"
                />
              </Field>
            </div>
          </div>
        </SectionCard>
        <SectionCard title="Runs on" description="The deployment profile the resident starts from.">
          {profiles.error ? (
            <ErrorState message={String(profiles.error)} />
          ) : profiles.isLoading ? (
            <LoadingState label="Loading profiles…" />
          ) : (
            <ResidentDeployFields
              draft={memberDraft}
              profiles={profiles.data ?? []}
              personas={[]}
              testIdPrefix="realm"
              onChange={(next) => {
                const profile = selectedResidentProfile(next, profiles.data ?? []);
                onChange({
                  ...draft,
                  profileId: next.profileId,
                  instanceId: next.instanceId || profile?.instanceId || '',
                  model: next.model,
                });
              }}
            />
          )}
        </SectionCard>
      </div>
      <SectionCard title="What it may do on its own" description="Starts cautious. Trust ratchets up as it proves itself; loosen any rung later from the realm page.">
        <div className="niuu:flex niuu:flex-col" data-testid="trust-ladder">
          {ACTION_CLASSES.map((actionClass) => (
            <div
              key={actionClass}
              className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2"
            >
              <span className="niuu:w-16 niuu:font-mono niuu:text-xs niuu:text-text-primary">{actionClass}</span>
              <span className="niuu:flex-1 niuu:text-xs niuu:text-text-muted">{ACTION_CLASS_COPY[actionClass]}</span>
              <SegmentedFilter<TrustSetting>
                options={TRUST_OPTIONS}
                value={draft.trust[actionClass]}
                onChange={(value) => onChange({ ...draft, trust: { ...draft.trust, [actionClass]: value } })}
                aria-label={`${actionClass} trust`}
              />
            </div>
          ))}
        </div>
      </SectionCard>
    </div>
  );
}

function LaunchStep({
  draft,
  sentence,
  inferred,
}: {
  draft: RealmDraft;
  sentence: string | null;
  inferred: Set<keyof RealmDraft>;
}) {
  const template = templateById(draft.templateId);
  const source = (key: keyof RealmDraft) =>
    inferred.has(key) ? (
      <Chip tone="brand">from your sentence</Chip>
    ) : (
      <Chip tone="muted">{draft[key] ? 'default' : 'needs you'}</Chip>
    );
  return (
    <div className="niuu:flex niuu:flex-col niuu:gap-4" data-testid="step-launch">
      {sentence ? (
        <SectionCard title="How I read that">
          <p className="niuu:m-0 niuu:text-sm niuu:text-text-secondary">
            I will be a <strong className="niuu:text-text-primary">{template.name}</strong> for{' '}
            <strong className="niuu:text-text-primary">{draft.repo || 'a repository you still need to pick'}</strong>
            {draft.trackerBoard ? (
              <>
                , working board <strong className="niuu:text-text-primary">{draft.trackerBoard}</strong> in priority order
              </>
            ) : null}
            . {draft.trust.deploy === 'ask' ? 'Deploying will ask you first.' : ''}{' '}
            Nothing starts until you say go.
          </p>
        </SectionCard>
      ) : null}
      <SectionCard title="Realm draft" description="Every row is the same field the step-by-step setup shows.">
        <div className="niuu:flex niuu:flex-col">
          {(
            [
              ['template', template.name],
              ['repo', draft.repo],
              ['branch', draft.branch || 'repository default'],
              ['trackerBoard', draft.trackerBoard],
              ['bugBoard', draft.bugBoard || 'tracker board'],
              ['mountTarget', draft.mountTarget],
              ['profileId', draft.profileId],
              ['model', draft.model || 'profile default'],
            ] as Array<[keyof RealmDraft, string]>
          ).map(([key, value]) => (
            <div key={key} className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2">
              <div className="niuu:flex-1">
                <ConfirmRow label={key === 'trackerBoard' ? 'board' : key === 'bugBoard' ? 'bug board' : key === 'mountTarget' ? 'memory' : key === 'profileId' ? 'runs on' : key} value={value || 'not set'} />
              </div>
              {source(key)}
            </div>
          ))}
          {ACTION_CLASSES.filter((actionClass) => draft.trust[actionClass] !== 'auto').map((actionClass) => (
            <div key={actionClass} className="niuu:flex niuu:items-center niuu:gap-3 niuu:border-b niuu:border-border-subtle niuu:py-2">
              <div className="niuu:flex-1">
                <ConfirmRow label={actionClass} value={draft.trust[actionClass] === 'ask' ? 'asks first' : 'never'} />
              </div>
              {inferred.has('trust') ? <Chip tone="brand">from your sentence</Chip> : <Chip tone="muted">default</Chip>}
            </div>
          ))}
        </div>
      </SectionCard>
      <SectionCard title="Charter">
        <p className="niuu:m-0 niuu:whitespace-pre-wrap niuu:text-sm niuu:text-text-secondary">{draft.charter || 'not written yet'}</p>
      </SectionCard>
    </div>
  );
}

export function NewRealmPage() {
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as NewRealmSearch;
  const personas = useService<IPersonaStore>('ravn.personas');
  const walkthrough = useWalkthrough(FIRST_REALM_WALKTHROUGH);
  const { run, progress } = useCreateRealm();

  const sentence = search.sentence?.trim() || null;
  const parsed = useMemo(() => (sentence ? parseSentence(sentence) : null), [sentence]);
  const fromSlug = search.from?.trim() || null;
  const fromGrants = useRealmTrustGrants(fromSlug ?? '', fromSlug !== null);
  const fromPersona = useQuery({
    queryKey: ['ravn', 'personas', fromSlug ? personaNameFor(fromSlug) : ''],
    queryFn: () => personas.getPersona(personaNameFor(fromSlug as string)),
    enabled: fromSlug !== null,
  });

  const [draft, setDraft] = useState<RealmDraft>(() => {
    const templateId = search.template && REALM_TEMPLATES.some((t) => t.id === search.template) ? search.template : EMPTY_DRAFT.templateId;
    const base: RealmDraft = { ...EMPTY_DRAFT, templateId, trust: templateById(templateId).trust };
    if (!parsed) return base;
    const trust: TrustPreset = { ...base.trust };
    for (const actionClass of parsed.askBefore) trust[actionClass] = actionClass === 'mutate' ? 'never' : 'ask';
    return {
      ...base,
      charter: parsed.charter,
      repo: parsed.repo ?? '',
      name: parsed.name ?? '',
      slug: parsed.name ? slugify(parsed.name) : '',
      trust,
    };
  });
  const [step, setStep] = useState<Step>(parsed ? 'launch' : 'template');
  const [launching, setLaunching] = useState(false);

  // Clone: fill from the source realm's grants and persona, leave the blanks empty.
  useEffect(() => {
    if (!fromSlug || !fromGrants.data || !fromPersona.data) return;
    const binding = bindingFromGrants(fromGrants.data);
    const trust: TrustPreset = { ...EMPTY_DRAFT.trust };
    for (const grant of latestGrants(fromGrants.data)) {
      if ((ACTION_CLASSES as readonly string[]).includes(grant.actionClass)) {
        trust[grant.actionClass as keyof TrustPreset] = trustSettingForLevel(grant.level);
      }
    }
    for (const actionClass of ACTION_CLASSES) {
      if (!fromGrants.data.some((grant) => grant.action_class === actionClass)) trust[actionClass] = 'never';
    }
    trust.observe = 'auto';
    setDraft((current) => ({
      ...current,
      templateId: binding?.template && REALM_TEMPLATES.some((t) => t.id === binding.template) ? binding.template : current.templateId,
      charter: fromPersona.data.description,
      mountTarget: binding?.mountTarget ?? current.mountTarget,
      trust,
    }));
    setStep('connect');
  }, [fromGrants.data, fromPersona.data, fromSlug]);

  // The tracker board key from a sentence is matched against real boards once they load.
  const tracker = useService<ITrackerBrowserService>('ting.tracker');
  const boards = useQuery({
    queryKey: ['ting', 'tracker', 'boards'],
    queryFn: () => tracker.listProjects(),
    enabled: parsed?.boardKey !== null && parsed?.boardKey !== undefined,
  });
  useEffect(() => {
    if (!parsed?.boardKey || !boards.data || draft.trackerBoard) return;
    const key = parsed.boardKey.toLowerCase();
    const match = boards.data.find(
      (board) => board.slug?.toLowerCase() === key || board.name.toLowerCase() === key || board.id.toLowerCase() === key,
    );
    if (match) setDraft((current) => ({ ...current, trackerBoard: match.id }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [boards.data, parsed?.boardKey]);

  const inferred = useMemo(() => {
    const keys = new Set<keyof RealmDraft>();
    if (!parsed) return keys;
    keys.add('charter');
    if (parsed.repo) keys.add('repo');
    if (parsed.boardKey && draft.trackerBoard) keys.add('trackerBoard');
    if (parsed.askBefore.length > 0) keys.add('trust');
    return keys;
  }, [draft.trackerBoard, parsed]);

  const stepIndex = STEPS.indexOf(step);

  async function launch() {
    setLaunching(true);
    try {
      const realm = await run(draft);
      walkthrough.markDone('template');
      walkthrough.markDone('connect');
      walkthrough.markDone('charter');
      walkthrough.markDone('trust');
      void navigate({ to: '/realms/$slug', params: { slug: realm.slug } });
    } catch {
      // progress carries the failing step and its message
    } finally {
      setLaunching(false);
    }
  }

  const content = (() => {
    switch (step) {
      case 'template':
        return <TemplateStep draft={draft} onChange={setDraft} />;
      case 'connect':
        return <ConnectStep draft={draft} onChange={setDraft} />;
      case 'charter':
        return <CharterStep draft={draft} onChange={setDraft} />;
      case 'launch':
        return <LaunchStep draft={draft} sentence={sentence} inferred={inferred} />;
    }
  })();

  const titles: Record<Step, [string, string]> = {
    template: ['What kind of realm is this?', 'A template is a starting point, not a script. The resident learns the realm and adjusts.'],
    connect: ['Connect the realm to its world.', 'Everything here is an integration you already have or add once. The resident reaches these through MCP; it never gets your raw credentials.'],
    charter: ['Tell it what matters. Then say how far it may go.', 'The charter is a seed. A few sentences. The resident builds its own understanding from the code, the tickets and what happens.'],
    launch: [sentence ? 'Here is how I read that.' : 'Ready to start.', 'Say go and the resident starts. Its first triage lands in a few minutes, and it touches nothing before you have seen it.'],
  };

  return (
    <div className="niuu:flex niuu:h-full" data-testid="new-realm">
      <div className="niuu:flex niuu:min-w-0 niuu:flex-1 niuu:flex-col">
        <div className="niuu:flex niuu:items-center niuu:justify-between niuu:border-b niuu:border-border-subtle niuu:px-8 niuu:py-3">
          <span className="niuu:text-[15px] niuu:font-medium niuu:text-text-primary">New realm</span>
          <StepIndicator current={step} steps={STEPS} labels={STEP_LABELS} />
          <span className="niuu:font-mono niuu:text-[11px] niuu:text-text-faint">{draft.slug || 'unnamed'}</span>
        </div>
        <div className="niuu:flex niuu:flex-1 niuu:flex-col niuu:gap-5 niuu:overflow-auto niuu:px-8 niuu:py-6">
          <div className="niuu:flex niuu:max-w-3xl niuu:flex-col niuu:gap-1.5">
            <h1 className="niuu:m-0 niuu:text-2xl niuu:font-bold niuu:tracking-tight niuu:text-text-primary">{titles[step][0]}</h1>
            <p className="niuu:m-0 niuu:text-[15px] niuu:text-text-secondary">{titles[step][1]}</p>
          </div>
          {content}
          {step === 'launch' && (launching || progress.error) ? (
            <SectionCard title="Starting the realm">
              <RecipeChecklist progress={progress} />
            </SectionCard>
          ) : null}
        </div>
        <div className="niuu:flex niuu:items-center niuu:justify-between niuu:border-t niuu:border-border-subtle niuu:px-8 niuu:py-4">
          <button
            type="button"
            className={BUTTON}
            disabled={stepIndex === 0 || launching}
            onClick={() => setStep(STEPS[stepIndex - 1] ?? 'template')}
          >
            Back
          </button>
          <div className="niuu:flex niuu:items-center niuu:gap-3">
            {step === 'launch' && sentence ? (
              <button type="button" className={BUTTON} disabled={launching} onClick={() => setStep('connect')}>
                Review step by step
              </button>
            ) : null}
            {step === 'launch' ? (
              <button type="button" className={CTA} disabled={launching} onClick={() => void launch()} data-testid="realm-go">
                {launching ? 'Starting…' : 'Go'}
              </button>
            ) : (
              <button
                type="button"
                className={CTA}
                onClick={() => {
                  walkthrough.markDone(step === 'charter' ? 'charter' : step);
                  setStep(STEPS[stepIndex + 1] ?? 'launch');
                }}
                data-testid="realm-continue"
              >
                {step === 'charter' ? 'Review & launch' : 'Continue'}
              </button>
            )}
          </div>
        </div>
      </div>
      <WalkthroughRail walkthrough={FIRST_REALM_WALKTHROUGH} />
    </div>
  );
}
