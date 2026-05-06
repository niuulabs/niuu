import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { resolveRoute } from '../domain/routing';
import type { IMimirService } from '../ports';
import type { WriteRoutingRule, RouteTestResult } from '../domain/routing';

export interface UseRoutingReturn {
  rules: WriteRoutingRule[];
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  testPath: string;
  setTestPath: (p: string) => void;
  testResult: RouteTestResult | null;
  upsertRule: (rule: WriteRoutingRule) => void;
  deleteRule: (id: string) => void;
  isSaving: boolean;
  isDeleting: boolean;
}

export function useRouting(): UseRoutingReturn {
  const [testPath, setTestPath] = useState('');
  const service = useService<IMimirService>('mimir');
  const queryClient = useQueryClient();

  const {
    data: rules = [],
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['mimir', 'routing'],
    queryFn: () => service.mounts.listRoutingRules(),
  });

  const upsertMutation = useMutation({
    mutationFn: (rule: WriteRoutingRule) => service.mounts.upsertRoutingRule(rule),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['mimir', 'routing'] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => service.mounts.deleteRoutingRule(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['mimir', 'routing'] }),
  });

  const testResult: RouteTestResult | null = testPath.trim()
    ? resolveRoute(rules, testPath.trim())
    : null;

  return {
    rules,
    isLoading,
    isError,
    error,
    testPath,
    setTestPath,
    testResult,
    upsertRule: upsertMutation.mutate,
    deleteRule: deleteMutation.mutate,
    isSaving: upsertMutation.isPending,
    isDeleting: deleteMutation.isPending,
  };
}
