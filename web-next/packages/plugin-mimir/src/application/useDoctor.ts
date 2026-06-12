import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';

const DOCTOR_KEY = ['mimir', 'doctor'] as const;

/** Instance health checklist — null when the backend has no doctor. */
export function useDoctor() {
  const service = useService<IMimirService>('mimir');
  const queryClient = useQueryClient();

  const report = useQuery({
    queryKey: DOCTOR_KEY,
    queryFn: () => service.mounts.getDoctor(),
  });

  const fix = useMutation({
    mutationFn: () => service.mounts.runDoctorFixes(),
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
