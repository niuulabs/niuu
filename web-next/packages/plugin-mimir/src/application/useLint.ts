import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { IMimirService } from '../ports';
import type { LintReport } from '../domain/lint';

export interface UseLintReturn {
  report: LintReport | undefined;
  issues: LintReport['issues'];
  summary: { error: number; warn: number; info: number };
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  runAutoFix: (issueIds?: string[]) => void;
  reassignIssues: (issueIds: string[], assignee: string) => void;
  isFixing: boolean;
  isReassigning: boolean;
}

export function useLint(mountName?: string): UseLintReturn {
  const service = useService<IMimirService>('mimir');
  const queryClient = useQueryClient();
  const queryKey = ['mimir', 'lint', mountName ?? null];

  const {
    data: report,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey,
    queryFn: () => service.lint.getLintReport(mountName),
  });

  const autoFixMutation = useMutation({
    mutationFn: (issueIds?: string[]) => service.lint.runAutoFix(issueIds),
    onSuccess: (updated) => {
      queryClient.setQueryData(queryKey, updated);
    },
  });

  const reassignMutation = useMutation({
    mutationFn: ({ issueIds, assignee }: { issueIds: string[]; assignee: string }) =>
      service.lint.reassignIssues(issueIds, assignee),
    onSuccess: (updated) => {
      queryClient.setQueryData(queryKey, updated);
    },
  });

  return {
    report,
    issues: report?.issues ?? [],
    summary: report?.summary ?? { error: 0, warn: 0, info: 0 },
    isLoading,
    isError,
    error,
    runAutoFix: (issueIds?: string[]) => autoFixMutation.mutate(issueIds),
    reassignIssues: (issueIds: string[], assignee: string) =>
      reassignMutation.mutate({ issueIds, assignee }),
    isFixing: autoFixMutation.isPending,
    isReassigning: reassignMutation.isPending,
  };
}
