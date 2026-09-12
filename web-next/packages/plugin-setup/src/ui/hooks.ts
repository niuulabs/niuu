import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useOptionalService, useService } from '@niuulabs/plugin-sdk';
import type { ConnectIntegrationInput } from '../domain/setup';
import type { ISetupService } from '../ports';

export const SETUP_SERVICE_KEY = 'setup';
export const setupKeys = {
  state: ['setup', 'state'] as const,
  system: ['setup', 'system'] as const,
  catalog: ['setup', 'catalog'] as const,
  integrations: ['setup', 'integrations'] as const,
};

export function useSetupService(): ISetupService {
  return useService<ISetupService>(SETUP_SERVICE_KEY);
}

export function useOptionalSetupService(): ISetupService | undefined {
  return useOptionalService<ISetupService>(SETUP_SERVICE_KEY);
}

export function useSetupState() {
  const service = useSetupService();
  return useQuery({ queryKey: setupKeys.state, queryFn: () => service.getState() });
}

export function useSystemReport() {
  const service = useSetupService();
  return useQuery({ queryKey: setupKeys.system, queryFn: () => service.getSystem() });
}

export function useCatalog() {
  const service = useSetupService();
  return useQuery({ queryKey: setupKeys.catalog, queryFn: () => service.listCatalog() });
}

export function useIntegrations() {
  const service = useSetupService();
  return useQuery({
    queryKey: setupKeys.integrations,
    queryFn: () => service.listIntegrations(),
  });
}

export function useCompleteStep() {
  const service = useSetupService();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ step, data }: { step: string; data?: Record<string, unknown> }) =>
      service.completeStep(step, data),
    onSuccess: (state) => client.setQueryData(setupKeys.state, state),
  });
}

export function useCompleteSetup() {
  const service = useSetupService();
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => service.complete(),
    onSuccess: (state) => client.setQueryData(setupKeys.state, state),
  });
}

export function useConnectIntegration() {
  const service = useSetupService();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: ConnectIntegrationInput) => service.connectIntegration(input),
    onSuccess: () => client.invalidateQueries({ queryKey: setupKeys.integrations }),
  });
}

export function useTestIntegration() {
  const service = useSetupService();
  return useMutation({
    mutationFn: (connectionId: string) => service.testIntegration(connectionId),
  });
}
