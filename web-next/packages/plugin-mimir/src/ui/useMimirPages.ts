import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';

export function useMimirPages(options?: { mountName?: string; category?: string }) {
  const service = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: ['mimir', 'pages', options],
    queryFn: () => service.pages.listPages(options),
  });
}

export function useMimirPage(path: string | null) {
  const service = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: ['mimir', 'page', path],
    queryFn: () => service.pages.getPage(path!),
    enabled: path !== null,
  });
}

export function useMimirPageSources(path: string | null) {
  const service = useService<IMimirService>('mimir');
  return useQuery({
    queryKey: ['mimir', 'page-sources', path],
    queryFn: () => service.pages.getPageSources(path!),
    enabled: path !== null,
  });
}
