import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useOptionalService, useService } from '@niuulabs/plugin-sdk';
import { isEnrollmentActive, type ConnectIntegrationInput } from '../domain/setup';
import type { ISetupService } from '../ports';

export const SETUP_SERVICE_KEY = 'setup';
export const setupKeys = {
  state: ['setup', 'state'] as const,
  system: ['setup', 'system'] as const,
  catalog: ['setup', 'catalog'] as const,
  integrations: ['setup', 'integrations'] as const,
  enrollment: (id: string) => ['setup', 'enrollment', id] as const,
};

/** How often the wizard asks the platform about a running sign-in. */
export const ENROLLMENT_POLL_MS = 2000;

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

export function useStartEnrollment() {
  const service = useSetupService();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ slug, credentialName }: { slug: string; credentialName: string }) =>
      service.startEnrollment(slug, credentialName),
    onSuccess: (enrollment) => client.setQueryData(setupKeys.enrollment(enrollment.id), enrollment),
  });
}

/** Polls a sign-in while it is running; refreshes connections once it completes. */
export function useEnrollment(enrollmentId: string | null) {
  const service = useSetupService();
  const client = useQueryClient();
  return useQuery({
    queryKey: setupKeys.enrollment(enrollmentId ?? ''),
    queryFn: async () => {
      const enrollment = await service.getEnrollment(enrollmentId ?? '');
      if (enrollment.state === 'complete') {
        await client.invalidateQueries({ queryKey: setupKeys.integrations });
      }
      return enrollment;
    },
    enabled: enrollmentId !== null,
    // The start mutation seeds the cache; poll from the first render anyway.
    staleTime: 0,
    refetchInterval: (query) => (isEnrollmentActive(query.state.data) ? ENROLLMENT_POLL_MS : false),
    // The user finishes the sign-in in the provider's tab, so this tab is in
    // the background for the whole time that matters.
    refetchIntervalInBackground: true,
  });
}

export function useCancelEnrollment() {
  const service = useSetupService();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (enrollmentId: string) => service.cancelEnrollment(enrollmentId),
    onSuccess: (enrollment) => client.setQueryData(setupKeys.enrollment(enrollment.id), enrollment),
  });
}

export function useSubmitEnrollmentCode() {
  const service = useSetupService();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ enrollmentId, code }: { enrollmentId: string; code: string }) =>
      service.submitEnrollmentCode(enrollmentId, code),
    onSuccess: async (enrollment) => {
      client.setQueryData(setupKeys.enrollment(enrollment.id), enrollment);
      if (enrollment.state === 'complete') {
        await client.invalidateQueries({ queryKey: setupKeys.integrations });
      }
    },
  });
}
