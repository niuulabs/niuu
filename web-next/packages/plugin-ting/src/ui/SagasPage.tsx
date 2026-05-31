import { useMemo, useState } from 'react';
import { useNavigate, useParams } from '@tanstack/react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import {
  BranchSelect,
  findRepoByRef,
  getCommonBranches,
  LoadingState,
  ErrorState,
  EmptyState,
  RepoSelect,
  Rune,
  ToastProvider,
  useToast,
  Modal,
  type RepoRecord,
} from '@niuulabs/ui';
import type { Saga } from '../domain/saga';
import type { SagaStatus } from '../domain/saga';
import type {
  DispatchCluster,
  IDispatchBus,
  ITrackerBrowserService,
  TrackerProject,
} from '../ports';
import { useSagas } from './useSagas';
import { SagaDetailPage } from './SagaDetailPage';

type SagaBucket = 'active' | 'review' | 'complete' | 'failed';

function sagaBucket(saga: Saga): SagaBucket {
  if (saga.status === 'complete') return 'complete';
  if (saga.status === 'failed') return 'failed';
  if (saga.phaseSummary.completed > 0) return 'review';
  return 'active';
}

function statusLabel(status: SagaStatus): string {
  switch (status) {
    case 'active':
      return 'RUNNING';
    case 'complete':
      return 'COMPLETE';
    case 'failed':
      return 'FAILED';
  }
}

function isTerminalTrackerStatus(status: string): boolean {
  const normalized = status.trim().toLowerCase();
  return [
    'complete',
    'completed',
    'done',
    'closed',
    'cancelled',
    'canceled',
    'archived',
    'merged',
  ].includes(normalized);
}

function trackerProjectSlug(project: TrackerProject): string {
  const source = (project.slug ?? '').trim() || project.name;
  return source
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function downloadJson(filename: string, data: string): void {
  const blob = new Blob([data], { type: 'application/json' });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

type RepoCatalogService = {
  getRepos(): Promise<RepoRecord[]>;
  getBranches(repoUrl: string): Promise<string[]>;
};

function SagaRailItem({
  saga,
  selected,
  onClick,
}: {
  saga: Saga;
  selected: boolean;
  onClick: () => void;
}) {
  const bucketColor =
    saga.status === 'failed'
      ? 'niuu:bg-critical'
      : saga.status === 'complete'
        ? 'niuu:bg-emerald-400'
        : 'niuu:bg-brand';

  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={selected}
      className={[
        'niuu:grid niuu:w-full niuu:grid-cols-[10px_minmax(0,1fr)] niuu:items-start niuu:gap-3 niuu:rounded-md niuu:border niuu:border-transparent niuu:px-3 niuu:py-3 niuu:text-left niuu:transition-colors',
        selected
          ? 'niuu:border-border niuu:bg-[#202733] niuu:text-text-primary'
          : 'niuu:hover:bg-bg-secondary/70 niuu:text-text-secondary',
      ].join(' ')}
    >
      <span
        className={['niuu:mt-1 niuu:w-2.5 niuu:h-2.5 niuu:rounded-full', bucketColor].join(' ')}
      />
      <span className="niuu:min-w-0 niuu:flex niuu:flex-col">
        <span className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-2">
          <span className="niuu:min-w-0 niuu:truncate niuu:text-[13px] niuu:font-medium niuu:text-text-primary">
            {saga.name}
          </span>
          <span className="niuu:shrink-0 niuu:text-[10px] niuu:font-mono niuu:uppercase niuu:tracking-[0.08em] niuu:text-text-muted">
            {statusLabel(saga.status)}
          </span>
        </span>
        <span className="niuu:mt-1 niuu:truncate niuu:text-[11px] niuu:font-mono niuu:text-text-muted">
          {saga.trackerId}
        </span>
        <span className="niuu:mt-2 niuu:grid niuu:grid-cols-[minmax(0,1fr)_auto] niuu:gap-x-3 niuu:gap-y-1 niuu:text-[10px] niuu:font-mono niuu:uppercase niuu:tracking-[0.06em] niuu:text-text-muted">
          <span className="niuu:truncate">{saga.repos[0] ?? 'niuulabs/volundr'}</span>
          <span>{`${saga.phaseSummary.completed}/${saga.phaseSummary.total} runs`}</span>
          <span className="niuu:col-span-2 niuu:truncate">{saga.featureBranch}</span>
        </span>
      </span>
    </button>
  );
}

function SagaBucketSection({
  title,
  items,
  selectedSagaId,
  onSelect,
}: {
  title: string;
  items: Saga[];
  selectedSagaId: string | null;
  onSelect: (saga: Saga) => void;
}) {
  if (items.length === 0) return null;
  return (
    <section className="niuu:space-y-2">
      <div className="niuu:flex niuu:items-center niuu:justify-between niuu:px-4">
        <span className="niuu:text-[12px] niuu:font-mono niuu:tracking-[0.08em] niuu:text-text-muted niuu:uppercase">
          {title}
        </span>
        <span className="niuu:text-[12px] niuu:font-mono niuu:text-text-muted">{items.length}</span>
      </div>
      <div className="niuu:space-y-1">
        {items.map((saga) => (
          <SagaRailItem
            key={saga.id}
            saga={saga}
            selected={selectedSagaId === saga.id}
            onClick={() => onSelect(saga)}
          />
        ))}
      </div>
    </section>
  );
}

export function SagasPage() {
  return (
    <ToastProvider>
      <SagasPageContent />
    </ToastProvider>
  );
}

function SagasPageContent() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const params = useParams({ strict: false }) as { sagaId?: string };
  const tracker = useService<ITrackerBrowserService>('ting.tracker');
  const dispatchBus = useService<IDispatchBus>('ting.dispatch');
  const repoCatalog = useService<RepoCatalogService>('niuu.repos');
  const { data: sagas, isLoading, isError, error } = useSagas();
  const [showNewSagaModal, setShowNewSagaModal] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);
  const [search, setSearch] = useState('');
  const [selectedSagaIdState, setSelectedSagaIdState] = useState<string | null>(
    params.sagaId ?? null,
  );
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [selectedRepos, setSelectedRepos] = useState<string[]>([]);
  const [repoCandidate, setRepoCandidate] = useState('');
  const [baseBranch, setBaseBranch] = useState('main');
  const [selectedInstanceId, setSelectedInstanceId] = useState('');
  const [isImporting, setIsImporting] = useState(false);

  const allSagas = useMemo(() => sagas ?? [], [sagas]);
  const selectedSagaId = selectedSagaIdState ?? allSagas[0]?.id ?? null;
  const importedTrackerIds = useMemo(
    () =>
      new Set(allSagas.filter((saga) => saga.status !== 'complete').map((saga) => saga.trackerId)),
    [allSagas],
  );
  const existingSagaSlugs = useMemo(() => new Set(allSagas.map((saga) => saga.slug)), [allSagas]);
  const filtered = allSagas.filter((saga) => {
    if (!search) return true;
    const haystack = `${saga.name} ${saga.trackerId} ${saga.featureBranch}`.toLowerCase();
    return haystack.includes(search.toLowerCase());
  });

  const trackerProjectsQuery = useQuery({
    queryKey: ['ting', 'tracker', 'projects'],
    queryFn: () => tracker.listProjects(),
    enabled: showImportModal,
  });
  const repoCatalogQuery = useQuery({
    queryKey: ['niuu', 'repos'],
    queryFn: () => repoCatalog.getRepos(),
    enabled: showImportModal,
  });
  const dispatchTargetsQuery = useQuery({
    queryKey: ['ting', 'dispatch', 'targets'],
    queryFn: () => dispatchBus.getClusters(),
    enabled: showImportModal,
  });
  const repoBranchesQuery = useQuery({
    queryKey: ['niuu', 'repos', 'branches', selectedRepos],
    queryFn: async () => {
      const branchSets = await Promise.all(
        selectedRepos.map((repo) => repoCatalog.getBranches(repo).catch(() => [] as string[])),
      );
      return branchSets.reduce<string[]>((acc, branches, index) => {
        if (index === 0) return [...branches];
        return acc.filter((branch) => branches.includes(branch));
      }, []);
    },
    enabled: showImportModal && selectedRepos.length > 0 && repoCatalogQuery.data?.length === 0,
  });

  const trackerProjects = (trackerProjectsQuery.data ?? []).filter(
    (project) => !isTerminalTrackerStatus(project.status),
  );
  const availableRepos = useMemo(() => repoCatalogQuery.data ?? [], [repoCatalogQuery.data]);
  const commonBranches = useMemo(
    () =>
      availableRepos.length > 0
        ? getCommonBranches(availableRepos, selectedRepos)
        : (repoBranchesQuery.data ?? []),
    [availableRepos, repoBranchesQuery.data, selectedRepos],
  );
  const effectiveBaseBranch =
    showImportModal && commonBranches.length > 0 && !commonBranches.includes(baseBranch)
      ? (commonBranches[0] ?? 'main')
      : baseBranch;
  const effectiveSelectedProjectId =
    showImportModal && !selectedProjectId && trackerProjects.length > 0
      ? (
          trackerProjects.find((project) => !importedTrackerIds.has(project.id)) ??
          trackerProjects[0]!
        ).id
      : selectedProjectId;
  const effectiveSelectedProject =
    trackerProjects.find((project) => project.id === effectiveSelectedProjectId) ?? null;
  const selectedProjectHasSlugConflict =
    effectiveSelectedProject !== null &&
    existingSagaSlugs.has(trackerProjectSlug(effectiveSelectedProject));
  const selectedProjectSlug = effectiveSelectedProject
    ? trackerProjectSlug(effectiveSelectedProject)
    : '';
  const canImportSelectedProject =
    effectiveSelectedProject !== null &&
    selectedRepos.length > 0 &&
    Boolean(effectiveBaseBranch.trim()) &&
    !importedTrackerIds.has(effectiveSelectedProject.id) &&
    !selectedProjectHasSlugConflict &&
    !isImporting;

  function addSelectedRepo(repoRef: string) {
    const value = repoRef.trim();
    if (!value || selectedRepos.includes(value)) return;
    setSelectedRepos((current) => [...current, value]);
    if (selectedRepos.length === 0) {
      const repo = findRepoByRef(availableRepos, value);
      setBaseBranch(repo?.defaultBranch ?? 'main');
    }
    setRepoCandidate('');
  }

  const groups = useMemo(() => {
    return {
      active: filtered.filter((s) => sagaBucket(s) === 'active'),
      review: filtered.filter((s) => sagaBucket(s) === 'review'),
      complete: filtered.filter((s) => sagaBucket(s) === 'complete'),
      failed: filtered.filter((s) => sagaBucket(s) === 'failed'),
    };
  }, [filtered]);

  const selectedSaga = filtered.find((s) => s.id === selectedSagaId) ?? filtered[0] ?? null;

  if (isLoading) return <LoadingState label="Loading sagas…" />;
  if (isError)
    return <ErrorState message={error instanceof Error ? error.message : 'Failed to load sagas'} />;

  function handleSelectSaga(saga: Saga) {
    setSelectedSagaIdState(saga.id);
    void navigate({ to: '/ting/sagas/$sagaId', params: { sagaId: saga.id } });
  }

  function openImportModal() {
    setShowImportModal(true);
    setSelectedProjectId(null);
    setSelectedRepos([]);
    setRepoCandidate('');
    setBaseBranch('');
    setSelectedInstanceId('');
  }

  function closeImportModal() {
    setShowImportModal(false);
    setSelectedProjectId(null);
    setIsImporting(false);
    setSelectedRepos([]);
    setRepoCandidate('');
    setBaseBranch('');
    setSelectedInstanceId('');
  }

  function handleImportModalToggle(open: boolean) {
    if (open) {
      openImportModal();
      return;
    }
    closeImportModal();
  }

  async function handleImportProject() {
    if (!effectiveSelectedProject) return;
    if (!canImportSelectedProject) return;

    setIsImporting(true);
    try {
      const importedSaga = await tracker.importProject(
        effectiveSelectedProject.id,
        selectedRepos,
        effectiveBaseBranch,
        selectedInstanceId || undefined,
      );
      await queryClient.invalidateQueries({ queryKey: ['ting', 'sagas'] });
      setSelectedSagaIdState(importedSaga.id);
      closeImportModal();
      toast({ title: `Imported ${effectiveSelectedProject.name}`, tone: 'success' });
      void navigate({ to: '/ting/sagas/$sagaId', params: { sagaId: importedSaga.id } });
    } catch (importError) {
      toast({
        title:
          importError instanceof Error ? importError.message : 'Failed to import tracker project',
        tone: 'critical',
      });
    } finally {
      setIsImporting(false);
    }
  }

  return (
    <div className="niuu:flex niuu:h-full niuu:overflow-hidden niuu:bg-bg-primary">
      <aside className="niuu:flex niuu:w-[294px] niuu:shrink-0 niuu:flex-col niuu:border-r niuu:border-border-subtle niuu:bg-[#151a20]">
        <div className="niuu:p-4 niuu:border-b niuu:border-border-subtle">
          <input
            type="search"
            placeholder="Filter sagas..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search sagas"
            className="niuu:w-full niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:py-3 niuu:text-[14px] niuu:text-text-primary niuu:placeholder-text-muted niuu:outline-none"
          />
        </div>
        <div className="niuu:flex-1 niuu:overflow-y-auto niuu:py-4 niuu:space-y-4">
          <SagaBucketSection
            title="ACTIVE"
            items={groups.active}
            selectedSagaId={selectedSagaId}
            onSelect={handleSelectSaga}
          />
          <SagaBucketSection
            title="IN REVIEW"
            items={groups.review}
            selectedSagaId={selectedSagaId}
            onSelect={handleSelectSaga}
          />
          <SagaBucketSection
            title="COMPLETE"
            items={groups.complete}
            selectedSagaId={selectedSagaId}
            onSelect={handleSelectSaga}
          />
          <SagaBucketSection
            title="FAILED"
            items={groups.failed}
            selectedSagaId={selectedSagaId}
            onSelect={handleSelectSaga}
          />
          {filtered.length === 0 && (
            <div className="niuu:px-4 niuu:text-sm niuu:text-text-muted">
              No sagas match &quot;{search}&quot;.
            </div>
          )}
        </div>
      </aside>

      <main className="niuu:flex-1 niuu:overflow-y-auto niuu:p-5 niuu:space-y-5">
        <div className="niuu:flex niuu:items-start niuu:justify-between niuu:gap-5">
          <div className="niuu:max-w-[680px]">
            <div className="niuu:flex niuu:items-start niuu:gap-3">
              <Rune glyph="ᚦ" size={26} />
              <div>
                <h2 className="niuu:m-0 niuu:text-[22px] niuu:font-semibold niuu:text-text-primary">
                  Sagas
                </h2>
                <p className="niuu:m-0 niuu:mt-2 niuu:text-[14px] niuu:leading-6 niuu:text-text-secondary">
                  Every saga is a decomposed tracker issue driven by a workflow. Select one to
                  inspect phases, runs, and confidence movement.
                </p>
              </div>
            </div>
          </div>
          <div className="niuu:flex niuu:items-center niuu:gap-3">
            <input
              type="search"
              placeholder="Filter sagas..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Filter sagas"
              className="niuu:w-[310px] niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:py-2.5 niuu:text-[14px] niuu:text-text-primary niuu:placeholder-text-muted niuu:outline-none"
            />
            <button
              type="button"
              className="niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:py-2.5 niuu:text-[14px] niuu:font-medium niuu:text-text-primary"
              onClick={openImportModal}
              aria-label="Import saga from tracker"
            >
              Import
            </button>
            <button
              type="button"
              className="niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:px-4 niuu:py-2.5 niuu:text-[14px] niuu:font-medium niuu:text-text-primary"
              onClick={() => {
                const data = JSON.stringify(allSagas, null, 2);
                downloadJson('sagas.json', data);
                toast({ title: `Exported ${allSagas.length} sagas`, tone: 'success' });
              }}
              aria-label="Export sagas as JSON"
            >
              Export
            </button>
            <button
              type="button"
              className="niuu:rounded-lg niuu:border niuu:border-brand/50 niuu:bg-brand niuu:px-4 niuu:py-2.5 niuu:text-[14px] niuu:font-medium niuu:text-bg-primary"
              onClick={() => setShowNewSagaModal(true)}
              aria-label="Create new saga"
            >
              + New saga
            </button>
          </div>
        </div>

        {selectedSaga ? (
          <SagaDetailPage sagaId={selectedSaga.id} hideBackButton />
        ) : filtered.length === 0 ? (
          <div className="niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-6">
            <EmptyState
              title="No sagas found"
              description={search ? `No sagas match "${search}"` : 'No sagas yet.'}
            />
          </div>
        ) : (
          <div className="niuu:rounded-xl niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-6">
            <EmptyState
              title="Select a saga"
              description="Choose a saga to inspect phases, runs, and confidence movement."
            />
          </div>
        )}

        <Modal
          open={showNewSagaModal}
          onOpenChange={setShowNewSagaModal}
          title="New Saga"
          description="New sagas start from a prompt in the Plan view."
          actions={[
            { label: 'Cancel', variant: 'secondary', closes: true },
            {
              label: 'Go to Plan →',
              variant: 'primary',
              closes: true,
              onClick: () => void navigate({ to: '/ting/plan' as never }),
            },
          ]}
        >
          <p className="niuu:m-0 niuu:text-sm niuu:text-text-secondary">Want to go there now?</p>
        </Modal>

        <Modal
          open={showImportModal}
          onOpenChange={handleImportModalToggle}
          title="Import From Tracker"
          description="Browse tracker projects, choose a target repo, and register the project as a Ting saga."
          className="niuu:max-w-[920px]"
          actions={[
            { label: 'Cancel', variant: 'secondary', closes: true },
            {
              label: isImporting ? 'Importing…' : 'Import saga',
              variant: 'primary',
              disabled: !canImportSelectedProject,
              closes: false,
              onClick: () => void handleImportProject(),
            },
          ]}
        >
          {trackerProjectsQuery.isLoading ? (
            <div className="niuu:py-6">
              <LoadingState label="Loading tracker projects…" />
            </div>
          ) : trackerProjectsQuery.isError ? (
            <ErrorState
              message={
                trackerProjectsQuery.error instanceof Error
                  ? trackerProjectsQuery.error.message
                  : 'Failed to load tracker projects'
              }
            />
          ) : (
            <div className="niuu:grid niuu:grid-cols-[minmax(0,1.3fr)_minmax(280px,0.9fr)] niuu:gap-4">
              <div className="niuu:space-y-2 niuu:max-h-[420px] niuu:overflow-y-auto">
                {trackerProjects.length === 0 ? (
                  <EmptyState
                    title="No tracker projects found"
                    description="Connect a tracker in Ting settings, then try again."
                  />
                ) : (
                  trackerProjects.map((project: TrackerProject) => {
                    const imported = importedTrackerIds.has(project.id);
                    const slugConflict = existingSagaSlugs.has(trackerProjectSlug(project));
                    const selected = effectiveSelectedProjectId === project.id;
                    return (
                      <button
                        key={project.id}
                        type="button"
                        onClick={() => setSelectedProjectId(project.id)}
                        className={[
                          'niuu:w-full niuu:rounded-lg niuu:border niuu:p-3 niuu:text-left niuu:transition-colors',
                          selected
                            ? 'niuu:border-brand/40 niuu:bg-[#1d232b]'
                            : 'niuu:border-border-subtle niuu:bg-bg-secondary niuu:hover:bg-bg-tertiary',
                        ].join(' ')}
                      >
                        <div className="niuu:flex niuu:items-start niuu:gap-3">
                          <div className="niuu:min-w-0 niuu:flex-1">
                            <div className="niuu:flex niuu:items-center niuu:gap-2 niuu:flex-wrap">
                              <span className="niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                                {project.name}
                              </span>
                              <span className="niuu:rounded niuu:bg-bg-elevated niuu:px-2 niuu:py-0.5 niuu:text-[11px] niuu:font-mono niuu:text-text-muted">
                                {project.status}
                              </span>
                              {imported && (
                                <span className="niuu:rounded niuu:bg-brand/15 niuu:px-2 niuu:py-0.5 niuu:text-[11px] niuu:font-mono niuu:text-brand">
                                  imported
                                </span>
                              )}
                              {!imported && slugConflict && (
                                <span className="niuu:rounded niuu:bg-amber-500/15 niuu:px-2 niuu:py-0.5 niuu:text-[11px] niuu:font-mono niuu:text-amber-300">
                                  slug conflict
                                </span>
                              )}
                            </div>
                            {project.description && (
                              <p className="niuu:m-0 niuu:mt-2 niuu:text-sm niuu:leading-5 niuu:text-text-secondary">
                                {project.description}
                              </p>
                            )}
                            <div className="niuu:mt-2 niuu:flex niuu:items-center niuu:gap-3 niuu:text-[11px] niuu:font-mono niuu:text-text-muted">
                              <span>{project.issueCount} issues</span>
                              <span>{project.milestoneCount} milestones</span>
                            </div>
                          </div>
                        </div>
                      </button>
                    );
                  })
                )}
              </div>

              <div className="niuu:rounded-lg niuu:border niuu:border-border-subtle niuu:bg-bg-secondary niuu:p-4 niuu:space-y-4">
                {effectiveSelectedProject ? (
                  <>
                    <div>
                      <h3 className="niuu:m-0 niuu:text-sm niuu:font-semibold niuu:text-text-primary">
                        {effectiveSelectedProject.name}
                      </h3>
                      <p className="niuu:m-0 niuu:mt-2 niuu:text-sm niuu:leading-5 niuu:text-text-secondary">
                        Import registers this tracker project as a saga. Ting will keep the saga
                        linked to the tracker instead of copying the project into local-only state.
                      </p>
                    </div>

                    <label className="niuu:block">
                      <span className="niuu:block niuu:mb-1.5 niuu:text-xs niuu:font-mono niuu:text-text-muted">
                        Repositories
                      </span>
                      {availableRepos.length > 0 ? (
                        <div className="niuu:flex niuu:flex-col niuu:gap-3">
                          {selectedRepos.length > 0 ? (
                            <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
                              {selectedRepos.map((repoUrl) => {
                                const repo = findRepoByRef(availableRepos, repoUrl);
                                const label = repo ? `${repo.org}/${repo.name}` : repoUrl;
                                return (
                                  <button
                                    key={repoUrl}
                                    type="button"
                                    onClick={() =>
                                      setSelectedRepos((current) =>
                                        current.filter((item) => item !== repoUrl),
                                      )
                                    }
                                    className="niuu:inline-flex niuu:items-center niuu:gap-2 niuu:rounded-full niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-3 niuu:py-1 niuu:text-xs niuu:font-mono niuu:text-text-secondary"
                                  >
                                    <span>{label}</span>
                                    <span aria-hidden="true">×</span>
                                  </button>
                                );
                              })}
                            </div>
                          ) : null}
                          <RepoSelect
                            repos={availableRepos}
                            value={repoCandidate}
                            excludedRepos={selectedRepos}
                            valueMode="slug"
                            onChange={addSelectedRepo}
                            placeholder={
                              selectedRepos.length > 0 ? 'Add repository' : 'Select repository'
                            }
                            testId="repo-select"
                          />
                        </div>
                      ) : (
                        <div className="niuu:flex niuu:flex-col niuu:gap-3">
                          {selectedRepos.length > 0 ? (
                            <div className="niuu:flex niuu:flex-wrap niuu:gap-2">
                              {selectedRepos.map((repoUrl) => (
                                <button
                                  key={repoUrl}
                                  type="button"
                                  onClick={() =>
                                    setSelectedRepos((current) =>
                                      current.filter((item) => item !== repoUrl),
                                    )
                                  }
                                  className="niuu:inline-flex niuu:items-center niuu:gap-2 niuu:rounded-full niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-3 niuu:py-1 niuu:text-xs niuu:font-mono niuu:text-text-secondary"
                                >
                                  <span>{repoUrl}</span>
                                  <span aria-hidden="true">×</span>
                                </button>
                              ))}
                            </div>
                          ) : null}
                          <div className="niuu:flex niuu:gap-2">
                            <input
                              type="text"
                              value={repoCandidate}
                              onChange={(e) => setRepoCandidate(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') {
                                  e.preventDefault();
                                  addSelectedRepo(repoCandidate);
                                }
                              }}
                              placeholder="org/repo or https://host/org/repo.git"
                              data-testid="repo-select"
                              className="niuu:w-full niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-tertiary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary niuu:outline-none niuu:focus:border-brand"
                            />
                            <button
                              type="button"
                              onClick={() => addSelectedRepo(repoCandidate)}
                              className="niuu:rounded-md niuu:border niuu:border-border-subtle niuu:bg-bg-tertiary niuu:px-3 niuu:py-2 niuu:text-xs niuu:font-mono niuu:text-text-primary"
                            >
                              add
                            </button>
                          </div>
                        </div>
                      )}
                    </label>

                    <label className="niuu:block">
                      <span className="niuu:block niuu:mb-1.5 niuu:text-xs niuu:font-mono niuu:text-text-muted">
                        Base branch
                      </span>
                      {commonBranches.length > 0 ? (
                        <BranchSelect
                          repos={availableRepos}
                          selectedRepos={selectedRepos}
                          value={effectiveBaseBranch}
                          onChange={setBaseBranch}
                          placeholder="Select branch"
                          testId="branch-select"
                          className="niuu:bg-bg-tertiary"
                        />
                      ) : (
                        <input
                          type="text"
                          value={effectiveBaseBranch}
                          onChange={(e) => setBaseBranch(e.target.value)}
                          placeholder="main"
                          className="niuu:w-full niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-tertiary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary niuu:outline-none niuu:focus:border-brand"
                        />
                      )}
                    </label>

                    <label className="niuu:block">
                      <span className="niuu:block niuu:mb-1.5 niuu:text-xs niuu:font-mono niuu:text-text-muted">
                        Volundr target (optional)
                      </span>
                      <select
                        value={selectedInstanceId}
                        onChange={(event) => setSelectedInstanceId(event.target.value)}
                        className="niuu:w-full niuu:rounded-md niuu:border niuu:border-border niuu:bg-bg-tertiary niuu:px-3 niuu:py-2 niuu:text-sm niuu:text-text-primary niuu:outline-none niuu:focus:border-brand"
                      >
                        <option value="">Use default routing</option>
                        {(dispatchTargetsQuery.data ?? []).map((target: DispatchCluster) => (
                          <option
                            key={target.instanceId ?? target.connectionId}
                            value={target.instanceId ?? target.connectionId}
                          >
                            {target.name}
                          </option>
                        ))}
                      </select>
                      <div className="niuu:mt-1 niuu:text-[11px] niuu:text-text-faint">
                        If selected, the imported saga will default to this forge for dispatches.
                      </div>
                    </label>

                    <div className="niuu:rounded-md niuu:bg-bg-tertiary niuu:p-3 niuu:text-xs niuu:leading-5 niuu:text-text-secondary">
                      {importedTrackerIds.has(effectiveSelectedProject.id)
                        ? 'This tracker project is already imported into Ting.'
                        : selectedProjectHasSlugConflict
                          ? `A saga with slug "${selectedProjectSlug}" already exists in Ting.`
                          : 'Select one or more repositories to bind the imported saga to.'}
                    </div>
                  </>
                ) : (
                  <EmptyState
                    title="Select a project"
                    description="Pick a tracker project to configure the import."
                  />
                )}
              </div>
            </div>
          )}
        </Modal>
      </main>
    </div>
  );
}
