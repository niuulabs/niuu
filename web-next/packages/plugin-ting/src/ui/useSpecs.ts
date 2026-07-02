import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { CreateSpecCampaignRequest, ISpecsService, ReviewSpecCampaignRequest } from '../ports';

export function useSpecCampaigns() {
  const svc = useService<ISpecsService>('ting.specs');
  return useQuery({
    queryKey: ['ting', 'specs', 'campaigns'],
    queryFn: () => svc.listCampaigns(),
    refetchInterval: 15000,
  });
}

export function useSpecCampaign(slug: string) {
  const svc = useService<ISpecsService>('ting.specs');
  return useQuery({
    queryKey: ['ting', 'specs', 'campaign', slug],
    queryFn: () => svc.getCampaign(slug),
    enabled: !!slug,
    refetchInterval: 15000,
  });
}

export function useCreateSpecCampaign() {
  const svc = useService<ISpecsService>('ting.specs');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateSpecCampaignRequest) => svc.createCampaign(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ting', 'specs', 'campaigns'] });
    },
  });
}

export function useDeleteSpecCampaign() {
  const svc = useService<ISpecsService>('ting.specs');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) => svc.deleteCampaign(slug),
    onSuccess: (_result, slug) => {
      queryClient.removeQueries({ queryKey: ['ting', 'specs', 'campaign', slug] });
      void queryClient.invalidateQueries({ queryKey: ['ting', 'specs', 'campaigns'] });
    },
  });
}

export function useSpecArtifact(slug: string, path: string | null) {
  const svc = useService<ISpecsService>('ting.specs');
  return useQuery({
    queryKey: ['ting', 'specs', 'campaign', slug, 'artifact', path],
    queryFn: () => (path ? svc.getArtifact(slug, path) : Promise.resolve(null)),
    enabled: !!slug && !!path,
  });
}

export function useReviewSpecCampaign(slug: string) {
  const svc = useService<ISpecsService>('ting.specs');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: ReviewSpecCampaignRequest) => svc.reviewCampaign(slug, request),
    onSuccess: (campaign) => {
      queryClient.setQueryData(['ting', 'specs', 'campaign', slug], campaign);
      void queryClient.invalidateQueries({ queryKey: ['ting', 'specs', 'campaigns'] });
      void queryClient.invalidateQueries({ queryKey: ['ting', 'specs', 'campaign', slug] });
    },
  });
}
