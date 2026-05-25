import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it } from 'vitest';
import { createMockResearchService } from '../adapters/mock';
import {
  useCreateResearchCampaign,
  useDeleteResearchCampaign,
  useResearchArtifact,
  useResearchCampaign,
  useResearchCampaigns,
  useUpdateResearchCampaign,
} from './useResearch';

function wrap(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('useResearch', () => {
  it('lists and reads campaigns from the research service', async () => {
    const wrapper = wrap({ 'ting.research': createMockResearchService() });
    const campaigns = renderHook(() => useResearchCampaigns(), { wrapper });
    await waitFor(() => expect(campaigns.result.current.data?.length).toBeGreaterThan(0));
    const slug = campaigns.result.current.data?.[0]?.slug ?? '';
    const campaign = renderHook(() => useResearchCampaign(slug), { wrapper });
    await waitFor(() => expect(campaign.result.current.data?.slug).toBe(slug));
  });

  it('creates and deletes campaigns while updating the cached list', async () => {
    const wrapper = wrap({ 'ting.research': createMockResearchService() });
    const campaigns = renderHook(() => useResearchCampaigns(), { wrapper });
    const createCampaign = renderHook(() => useCreateResearchCampaign(), { wrapper });
    const deleteCampaign = renderHook(() => useDeleteResearchCampaign(), { wrapper });

    await waitFor(() => expect(campaigns.result.current.data?.length).toBe(1));

    await act(async () => {
      await createCampaign.result.current.mutateAsync({
        question: 'Should we add a research center?',
        mode: 'evaluative',
      });
    });

    await waitFor(() => expect(campaigns.result.current.data?.length).toBe(2));
    const created = campaigns.result.current.data?.find((item) =>
      item.slug.includes('should-we-add-a-research-center'),
    );
    expect(created).toBeTruthy();
    const campaign = renderHook(() => useResearchCampaign(created!.slug), { wrapper });
    await waitFor(() => expect(campaign.result.current.data?.slug).toBe(created!.slug));

    await act(async () => {
      await deleteCampaign.result.current.mutateAsync(created!.slug);
    });

    await waitFor(() =>
      expect(
        campaigns.result.current.data?.some((item) => item.slug === created!.slug),
      ).toBe(false),
    );

    const deletedCampaign = renderHook(() => useResearchCampaign(created!.slug), { wrapper });
    await waitFor(() => expect(deletedCampaign.result.current.data).toBeNull());
  });

  it('loads artifact detail when a path is provided', async () => {
    const wrapper = wrap({ 'ting.research': createMockResearchService() });
    const artifact = renderHook(
      () => useResearchArtifact('rag-landscape', 'research/campaigns/rag-landscape/final.md'),
      { wrapper },
    );
    await waitFor(() =>
      expect(artifact.result.current.data?.content).toContain('Mock content'),
    );
  });

  it('updates a campaign and keeps the cached campaign detail in sync', async () => {
    const wrapper = wrap({ 'ting.research': createMockResearchService() });
    const campaign = renderHook(() => useResearchCampaign('rag-landscape'), { wrapper });
    const updateCampaign = renderHook(() => useUpdateResearchCampaign('rag-landscape'), {
      wrapper,
    });

    await waitFor(() => expect(campaign.result.current.data?.slug).toBe('rag-landscape'));

    await act(async () => {
      await updateCampaign.result.current.mutateAsync({
        name: 'RAG landscape revised',
        status: 'blocked',
        metadata: { audience: 'platform leadership' },
      });
    });

    await waitFor(() =>
      expect(campaign.result.current.data?.name).toBe('RAG landscape revised'),
    );
    expect(campaign.result.current.data?.status).toBe('blocked');
    expect(campaign.result.current.data?.metadata.audience).toBe('platform leadership');
  });

  it('keeps detail and artifact queries idle until identifiers are provided', async () => {
    const wrapper = wrap({ 'ting.research': createMockResearchService() });
    const campaign = renderHook(() => useResearchCampaign(''), { wrapper });
    const artifact = renderHook(() => useResearchArtifact('rag-landscape', null), { wrapper });

    expect(campaign.result.current.status).toBe('pending');
    expect(campaign.result.current.fetchStatus).toBe('idle');
    expect(campaign.result.current.data).toBeUndefined();

    expect(artifact.result.current.status).toBe('pending');
    expect(artifact.result.current.fetchStatus).toBe('idle');
    expect(artifact.result.current.data).toBeUndefined();
  });
});
