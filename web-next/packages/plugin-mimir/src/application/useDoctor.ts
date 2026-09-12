import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { useActiveMount } from './useActiveMount';
import type { IMimirService } from '../ports';

/** Instance health checklist — null when the backend has no doctor. */
export function useDoctor() {
  const { mountName } = useActiveMount();
  const DOCTOR_KEY = ['mimir', 'doctor', mountName ?? null] as const;
  const service = useService<IMimirService>('mimir');
  const queryClient = useQueryClient();

  const report = useQuery({
    queryKey: DOCTOR_KEY,
    queryFn: () => service.mounts.getDoctor(mountName),
  });

  const fix = useMutation({
    mutationFn: () => service.mounts.runDoctorFixes(mountName),
    onSuccess: (updated) => {
      if (updated) {
        queryClient.setQueryData(DOCTOR_KEY, updated);
        return;
      }
      void queryClient.invalidateQueries({ queryKey: DOCTOR_KEY });
    },
  });

  return {
    report: report.data ?? null,
    isLoading: report.isLoading,
    isError: report.isError,
    error: report.error,
    runFixes: () => fix.mutate(),
    isFixing: fix.isPending,
  };
}
