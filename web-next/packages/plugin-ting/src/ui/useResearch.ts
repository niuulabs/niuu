import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type {
  IResearchService,
  CreateResearchCampaignRequest,
  UpdateResearchCampaignRequest,
} from '../ports';

export function useResearchCampaigns() {
  const svc = useService<IResearchService>('ting.research');
  return useQuery({
    queryKey: ['ting', 'research', 'campaigns'],
    queryFn: () => svc.listCampaigns(),
    refetchInterval: 15000,
  });
}

export function useResearchCampaign(slug: string) {
  const svc = useService<IResearchService>('ting.research');
  return useQuery({
    queryKey: ['ting', 'research', 'campaign', slug],
    queryFn: () => svc.getCampaign(slug),
    enabled: !!slug,
    refetchInterval: 15000,
  });
}

export function useCreateResearchCampaign() {
  const svc = useService<IResearchService>('ting.research');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateResearchCampaignRequest) => svc.createCampaign(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ting', 'research', 'campaigns'] });
    },
  });
}

export function useUpdateResearchCampaign(slug: string) {
  const svc = useService<IResearchService>('ting.research');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: UpdateResearchCampaignRequest) => svc.updateCampaign(slug, request),
    onSuccess: (campaign) => {
      queryClient.setQueryData(['ting', 'research', 'campaign', slug], campaign);
      void queryClient.invalidateQueries({ queryKey: ['ting', 'research', 'campaigns'] });
    },
  });
}

export function useDeleteResearchCampaign() {
  const svc = useService<IResearchService>('ting.research');
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) => svc.deleteCampaign(slug),
    onSuccess: (_result, slug) => {
      queryClient.removeQueries({ queryKey: ['ting', 'research', 'campaign', slug] });
      void queryClient.invalidateQueries({ queryKey: ['ting', 'research', 'campaigns'] });
    },
  });
}

export function useResearchArtifact(slug: string, path: string | null) {
  const svc = useService<IResearchService>('ting.research');
  return useQuery({
    queryKey: ['ting', 'research', 'campaign', slug, 'artifact', path],
    queryFn: () => (path ? svc.getArtifact(slug, path) : Promise.resolve(null)),
    enabled: !!slug && !!path,
  });
}
