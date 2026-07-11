import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from '@tanstack/react-router';
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { usePluginCtx, useService } from '@niuulabs/plugin-sdk';
import { openEventStream } from '@niuulabs/query';
import type { IDispatcherService, IResearchService } from '../ports';
import type { CampaignArtifactDetail } from '../domain/research';
import { useDeleteResearchCampaign, useResearchCampaign } from './useResearch';
import {
  artifactDisplayTitle,
  confidenceFromArtifacts,
  deriveCampaignState,
  deriveWorkingThesis,
  findActiveStage,
  inferDomain,
  inferSourceKind,
  parseCritiques,
  parseFrontmatter,
  parseListCards,
  qualityForSource,
  readDrawerState,
  takeExcerpt,
  writeDrawerState,
  type CitationPopoverState,
  type CritiqueVm,
  type DrawerState,
  type MimirServiceLike,
  type ParsedArtifact,
  type SourceVm,
  type ViewMode,
} from './researchCampaignModel';

export function useResearchCampaignPage() {
  const { slug } = useParams({ from: '/ting/research/$slug' });
  const navigate = useNavigate();
  const ctx = usePluginCtx();
  const queryClient = useQueryClient();
  const research = useService<IResearchService>('ting.research');
  const dispatcher = useService<IDispatcherService>('ting.dispatcher');
  const mimir = useService<MimirServiceLike>('mimir');
  const { data: campaign, isLoading, isError, error } = useResearchCampaign(slug);
  const deleteCampaign = useDeleteResearchCampaign();
  const [drawer, setDrawer] = useState<DrawerState>(() => readDrawerState());
  const [viewMode, setViewMode] = useState<ViewMode>('clean');
  const [popoverCitation, setPopoverCitation] = useState<CitationPopoverState | null>(null);
  const [evidenceOpenOverride, setEvidenceOpenOverride] = useState<boolean | null>(null);
  const [skepticOpenOverride, setSkepticOpenOverride] = useState<boolean | null>(null);
  const [memoryOpenOverride, setMemoryOpenOverride] = useState<boolean | null>(null);
  const [learningsOpenOverride, setLearningsOpenOverride] = useState<boolean | null>(null);

  const activityLog = useQuery({
    queryKey: ['ting', 'research', 'campaign', slug, 'activity'],
    queryFn: () => dispatcher.getActivityLog(150),
    refetchInterval: 15000,
  });

  useEffect(() => {
    const sync = () => setDrawer(readDrawerState());
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, []);

  useEffect(() => {
    writeDrawerState(drawer);
  }, [drawer]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const stream = openEventStream('/api/v1/ting/events', {
      onMessage: () => {},
      onEvent: ({ event, data }) => {
        if (
          event !== 'workflow.campaign.updated' &&
          event !== 'workflow.campaign.completed' &&
          event !== 'workflow.campaign.failed' &&
          event !== 'workflow.campaign.deleted'
        ) {
          return;
        }

        try {
          const parsed = JSON.parse(data) as { slug?: string };
          if (parsed.slug && parsed.slug !== slug) return;
        } catch {
          return;
        }
        void queryClient.invalidateQueries({ queryKey: ['ting', 'research', 'campaign', slug] });
        void queryClient.invalidateQueries({ queryKey: ['ting', 'research', 'campaigns'] });
        void queryClient.invalidateQueries({
          queryKey: ['ting', 'research', 'campaign', slug, 'activity'],
        });
      },
    });
    return () => stream.close();
  }, [queryClient, slug]);

  const artifactQueries = useQueries({
    queries: (campaign?.artifacts ?? []).map((artifact) => ({
      queryKey: ['ting', 'research', 'campaign', slug, 'artifact', artifact.path, 'detail'],
      queryFn: () => research.getArtifact(slug, artifact.path),
      enabled: !!campaign,
      staleTime: 15_000,
    })),
  });

  const artifactDetails = useMemo(
    () => artifactQueries.map((query) => query.data).filter(Boolean) as CampaignArtifactDetail[],
    [artifactQueries],
  );

  const parsedArtifacts = useMemo<ParsedArtifact[]>(() => {
    return artifactDetails.map((artifact) => {
      const { frontmatter, body } = parseFrontmatter(artifact.content);
      return {
        ...artifact,
        frontmatter,
        body,
        displayTitle: artifactDisplayTitle(artifact),
      };
    });
  }, [artifactDetails]);

  const openMimirPage = (path: string | null | undefined) => {
    if (!path) return;
    ctx.setTweak('mimir.selectedPagePath', path);
    void navigate({ to: '/mimir/pages' });
  };

  const artifactByPath = useMemo(() => {
    const map = new Map<string, ParsedArtifact>();
    for (const artifact of parsedArtifacts) map.set(artifact.path, artifact);
    return map;
  }, [parsedArtifacts]);

  const artifactByKind = useMemo(() => {
    const map = new Map<string, ParsedArtifact>();
    for (const artifact of parsedArtifacts) {
      if (artifact.kind && !map.has(artifact.kind)) {
        map.set(artifact.kind, artifact);
      }
    }
    return map;
  }, [parsedArtifacts]);

  const sourceIds = useMemo(() => {
    const ids = new Set<string>();
    for (const artifact of parsedArtifacts) {
      for (const id of artifact.sourceIds ?? []) ids.add(id);
      const extraIds = Array.isArray(artifact.frontmatter.source_ids)
        ? (artifact.frontmatter.source_ids as string[])
        : [];
      for (const id of extraIds) ids.add(id);
      const bodyMatches = artifact.content.match(/src_[a-z0-9]+/gi) ?? [];
      for (const id of bodyMatches) ids.add(id);
    }
    return Array.from(ids);
  }, [parsedArtifacts]);

  const sourcesQuery = useQuery({
    queryKey: ['ting', 'research', 'campaign', slug, 'sources', sourceIds.join(',')],
    queryFn: async () => {
      const all = await mimir.pages.listSources();
      return all.filter((source) => sourceIds.includes(source.id));
    },
    enabled: sourceIds.length > 0,
    staleTime: 15_000,
  });

  const sourceList = useMemo<SourceVm[]>(() => {
    const orderedSources = [...(sourcesQuery.data ?? [])].sort((left, right) => {
      const leftCount = left.compiledInto.filter(
        (path) => path.includes(`/research/campaigns/${slug}/`) || path.endsWith(`${slug}.md`),
      ).length;
      const rightCount = right.compiledInto.filter(
        (path) => path.includes(`/research/campaigns/${slug}/`) || path.endsWith(`${slug}.md`),
      ).length;
      return rightCount - leftCount;
    });
    return orderedSources.map((source, index) => ({
      id: source.id,
      citation: `s${index + 1}`,
      title: source.title,
      domain: inferDomain(source),
      quality: qualityForSource(source),
      citedCount:
        parsedArtifacts.filter((artifact) => artifact.sourceIds.includes(source.id)).length ||
        source.compiledInto.filter((path) => artifactByPath.has(path)).length ||
        1,
      compiledInto:
        source.compiledInto.filter((path) => artifactByPath.has(path)).length > 0
          ? source.compiledInto.filter((path) => artifactByPath.has(path))
          : parsedArtifacts
              .filter((artifact) => artifact.sourceIds.includes(source.id))
              .map((artifact) => artifact.path),
      excerpt: takeExcerpt(source.content),
      kind: inferSourceKind(source),
      content: source.content,
      originUrl: source.originUrl,
    }));
  }, [artifactByPath, parsedArtifacts, slug, sourcesQuery.data]);

  const sourceByCitation = useMemo(() => {
    const map = new Map<string, SourceVm>();
    for (const source of sourceList) map.set(source.citation, source);
    return map;
  }, [sourceList]);

  const critiques = useMemo(() => {
    const critiqueArtifact = artifactByKind.get('critique');
    if (!critiqueArtifact) return [] as CritiqueVm[];
    const linkedArtifacts = ['research/campaigns/' + slug + '/critique.md'];
    const finalArtifact = artifactByKind.get('final');
    if (finalArtifact) linkedArtifacts.push(finalArtifact.path);
    return parseCritiques(critiqueArtifact.body, linkedArtifacts);
  }, [artifactByKind, slug]);

  const critiqueByCitation = useMemo(() => {
    const map = new Map<string, CritiqueVm>();
    for (const critique of critiques) map.set(critique.citation, critique);
    return map;
  }, [critiques]);

  const finalArtifact = artifactByKind.get('final') ?? null;
  const manifestArtifact = artifactByKind.get('manifest') ?? null;
  const learningsArtifact = artifactByKind.get('learnings') ?? null;
  const followupsArtifact = artifactByKind.get('followups') ?? null;
  const workingThesis = useMemo(() => deriveWorkingThesis(parsedArtifacts), [parsedArtifacts]);
  const derivedState = useMemo(
    () => (campaign ? deriveCampaignState(campaign, parsedArtifacts) : 'draft'),
    [campaign, parsedArtifacts],
  );
  const activeStage = useMemo(
    () => (campaign ? findActiveStage(campaign.stageState) : undefined),
    [campaign],
  );
  const confidence = useMemo(
    () => confidenceFromArtifacts(derivedState, finalArtifact, sourceList.length, critiques.length),
    [critiques.length, derivedState, finalArtifact, sourceList.length],
  );
  const selectedFilePath =
    drawer.file ??
    finalArtifact?.path ??
    artifactByKind.get('analysis')?.path ??
    artifactByKind.get('brief')?.path ??
    parsedArtifacts[0]?.path;
  const selectedFileArtifact = selectedFilePath
    ? (artifactByPath.get(selectedFilePath) ?? null)
    : null;
  const selectedSource = useMemo(() => {
    const sourceFromDrawer = drawer.sourceId
      ? (sourceList.find((source) => source.id === drawer.sourceId) ?? null)
      : null;
    if (sourceFromDrawer) return sourceFromDrawer;

    const sourceFromCitation =
      popoverCitation?.kind === 'source'
        ? (sourceByCitation.get(popoverCitation.key) ?? null)
        : null;
    return sourceFromCitation ?? sourceList[0] ?? null;
  }, [drawer.sourceId, popoverCitation, sourceByCitation, sourceList]);
  const selectedCritique = useMemo(() => {
    const critiqueFromDrawer = drawer.critiqueId
      ? (critiques.find((critique) => critique.id === drawer.critiqueId) ?? null)
      : null;
    if (critiqueFromDrawer) return critiqueFromDrawer;

    const critiqueFromCitation =
      popoverCitation?.kind === 'critique'
        ? (critiqueByCitation.get(popoverCitation.key) ?? null)
        : null;
    return critiqueFromCitation ?? critiques[0] ?? null;
  }, [critiqueByCitation, critiques, drawer.critiqueId, popoverCitation]);

  const filteredActivity = (activityLog.data ?? []).filter((event) => {
    const eventSlug = typeof event.data.slug === 'string' ? event.data.slug : undefined;
    const sessionId = typeof event.data.session_id === 'string' ? event.data.session_id : undefined;
    return eventSlug === slug || (campaign?.sessionId && sessionId === campaign.sessionId);
  });

  const latestActivity = filteredActivity[0];
  const sessionsCount = Math.max(
    1,
    new Set(
      filteredActivity
        .map((event) => String(event.data.persona ?? event.data.raven ?? ''))
        .filter(Boolean),
    ).size,
  );

  const learnings = useMemo(
    () => (learningsArtifact ? parseListCards(learningsArtifact.body) : []),
    [learningsArtifact],
  );
  const followups = useMemo(
    () => (followupsArtifact ? parseListCards(followupsArtifact.body) : []),
    [followupsArtifact],
  );

  const memoryGroups = useMemo(() => {
    const published = parsedArtifacts.filter((artifact) => artifact.publishState === 'published');
    const reviewReady = parsedArtifacts.filter(
      (artifact) => artifact.publishState === 'unpublished',
    );
    const notebook = parsedArtifacts.filter((artifact) => artifact.publishState === 'unknown');
    return { published, reviewReady, notebook };
  }, [parsedArtifacts]);
  const campaignPanelState = campaign ? deriveCampaignState(campaign, []) : 'draft';
  const evidenceOpen = evidenceOpenOverride ?? campaignPanelState === 'published';
  const skepticOpen =
    skepticOpenOverride ??
    (campaignPanelState === 'running' ||
      campaignPanelState === 'review' ||
      campaignPanelState === 'blocked');
  const memoryOpen = memoryOpenOverride ?? campaignPanelState === 'published';
  const learningsOpen =
    learningsOpenOverride ??
    (campaignPanelState === 'published' || campaignPanelState === 'review');

  const visibleCitationSourceLabels = sourceList.slice(0, 3).map((source) => source.citation);
  const popoverSource =
    popoverCitation?.kind === 'source' ? (sourceByCitation.get(popoverCitation.key) ?? null) : null;
  const popoverCritique =
    popoverCitation?.kind === 'critique'
      ? (critiqueByCitation.get(popoverCitation.key) ?? null)
      : null;

  function openDrawer(next: Partial<DrawerState>) {
    setDrawer((current) => ({
      open: true,
      tab: next.tab ?? current.tab,
      file: next.file ?? current.file,
      sourceId: next.sourceId ?? current.sourceId,
      critiqueId: next.critiqueId ?? current.critiqueId,
      operatorSub: next.operatorSub ?? current.operatorSub ?? 'activity',
    }));
  }

  async function handleDeleteCampaign() {
    if (!campaign) return;
    if (typeof window !== 'undefined') {
      const confirmed = window.confirm(
        `Delete research campaign "${campaign.name}"? This removes the campaign record from Ting but does not delete any existing Mimir pages.`,
      );
      if (!confirmed) return;
    }
    await deleteCampaign.mutateAsync(campaign.slug);
    await queryClient.invalidateQueries({ queryKey: ['ting', 'research', 'campaigns'] });
    void navigate({ to: '/ting/research' });
  }

  return {
    slug,
    navigate,
    campaign,
    isLoading,
    isError,
    error,
    deleteCampaign,
    drawer,
    setDrawer,
    viewMode,
    setViewMode,
    popoverCitation,
    setPopoverCitation,
    artifactByKind,
    parsedArtifacts,
    openMimirPage,
    sourceList,
    critiques,
    finalArtifact,
    manifestArtifact,
    learningsArtifact,
    followupsArtifact,
    workingThesis,
    derivedState,
    activeStage,
    confidence,
    selectedFilePath,
    selectedFileArtifact,
    selectedSource,
    selectedCritique,
    filteredActivity,
    latestActivity,
    sessionsCount,
    learnings,
    followups,
    memoryGroups,
    evidenceOpen,
    setEvidenceOpenOverride,
    skepticOpen,
    setSkepticOpenOverride,
    memoryOpen,
    setMemoryOpenOverride,
    learningsOpen,
    setLearningsOpenOverride,
    visibleCitationSourceLabels,
    popoverSource,
    popoverCritique,
    openDrawer,
    handleDeleteCampaign,
  };
}

type ResearchCampaignPageResult = ReturnType<typeof useResearchCampaignPage>;

export type ResearchCampaignPageController = Omit<ResearchCampaignPageResult, 'campaign'> & {
  campaign: NonNullable<ResearchCampaignPageResult['campaign']>;
};
