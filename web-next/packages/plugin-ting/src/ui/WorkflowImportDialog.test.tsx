import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi } from 'vitest';
import type { Workflow } from '../domain/workflow';
import { WorkflowImportDialog } from './WorkflowImportDialog';
import type { WorkflowRegistryMount } from './WorkflowBuilder/mimirRegistry';

const workflow: Workflow = {
  id: '00000000-0000-4000-8000-000000000001',
  name: 'Imported review',
  version: '1.0.0',
  nodes: [],
  edges: [],
};

function wrapper(service: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ 'ting.workflows': service }}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('WorkflowImportDialog', () => {
  it('requires review of changed mappings and bindings before applying that exact preview', async () => {
    const preview = {
      workflow,
      personas: [
        {
          alias: 'reviewer',
          id: 'portable-reviewer',
          revision: '4',
          digest: 'sha256:abc',
          status: 'conflict' as const,
          message: 'The local revision differs.',
          definition: {
            permission_mode: 'read_only',
            allowed_tools: ['Read', 'Search'],
            system_prompt_template: 'Review the proposed change.',
          },
        },
      ],
      requirements: [
        {
          id: 'mimir-1',
          kind: 'mimir',
          message: 'Choose a local knowledge registry.',
          resolved: false,
        },
      ],
      errors: [],
      canApply: true,
      previewDigest: 'preview-digest',
    };
    const service = {
      previewWorkflowImport: vi.fn().mockResolvedValue(preview),
      applyWorkflowImport: vi.fn().mockResolvedValue(workflow),
    };
    const onImported = vi.fn();
    const onClose = vi.fn();

    render(
      <WorkflowImportDialog
        open
        workflows={[]}
        personas={[
          {
            name: 'local-reviewer',
            permissionMode: 'default',
            allowedTools: [],
            iterationBudget: 10,
            isBuiltin: false,
            hasOverride: false,
            producesEvent: 'review.completed',
            consumesEvents: [],
          },
        ]}
        registryMounts={[
          {
            id: 'local-mimir',
            name: 'Local Mimir',
            lifecycle: 'registered',
            enabled: true,
          } as WorkflowRegistryMount,
        ]}
        onClose={onClose}
        onImported={onImported}
      />,
      { wrapper: wrapper(service) },
    );

    fireEvent.change(screen.getByTestId('workflow-import-file'), {
      target: { files: [new File(['schema_version: 1'], 'review.yaml')] },
    });
    await waitFor(() => expect(screen.getByTestId('preview-workflow-import')).toBeEnabled());
    fireEvent.click(screen.getByTestId('preview-workflow-import'));
    await waitFor(() => expect(screen.getByTestId('workflow-import-preview')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Review portable persona behavior'));
    expect(screen.getByText(/read_only/)).toBeInTheDocument();
    expect(screen.getByText(/Read, Search/)).toBeInTheDocument();
    expect(screen.getByText('Review the proposed change.')).toBeInTheDocument();

    fireEvent.change(screen.getByTestId('persona-mapping-reviewer'), {
      target: { value: 'local-reviewer' },
    });
    fireEvent.change(screen.getByTestId('requirement-binding-mimir-1'), {
      target: { value: 'local-mimir' },
    });
    expect(screen.getByTestId('apply-workflow-import')).toBeDisabled();
    expect(
      screen.getByText('Preview the current mappings and bindings before importing.'),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('preview-workflow-import'));
    await waitFor(() => expect(screen.getByTestId('apply-workflow-import')).toBeEnabled());
    fireEvent.click(screen.getByTestId('apply-workflow-import'));

    await waitFor(() => expect(service.applyWorkflowImport).toHaveBeenCalledTimes(1));
    expect(service.previewWorkflowImport).toHaveBeenCalledTimes(2);
    expect(service.previewWorkflowImport).toHaveBeenLastCalledWith(
      expect.objectContaining({
        mappings: { reviewer: 'local-reviewer' },
        bindings: { 'mimir-1': 'local-mimir' },
      }),
    );
    expect(service.applyWorkflowImport).toHaveBeenCalledWith(
      expect.objectContaining({ previewDigest: 'preview-digest' }),
    );
    expect(onImported).toHaveBeenCalledWith(workflow);
    expect(onClose).toHaveBeenCalled();
  });

  it('does not silently replace the displayed preview immediately before apply', async () => {
    const preview = {
      workflow,
      personas: [],
      requirements: [],
      errors: [],
      canApply: true,
      previewDigest: 'reviewed-digest',
    };
    const service = {
      previewWorkflowImport: vi.fn().mockResolvedValue(preview),
      applyWorkflowImport: vi
        .fn()
        .mockRejectedValue(new Error('Import preview changed; preview again')),
    };
    const onImported = vi.fn();
    const onClose = vi.fn();

    render(
      <WorkflowImportDialog
        open
        workflows={[]}
        personas={[]}
        registryMounts={[]}
        onClose={onClose}
        onImported={onImported}
      />,
      { wrapper: wrapper(service) },
    );

    fireEvent.change(screen.getByTestId('workflow-import-file'), {
      target: { files: [new File(['schema_version: 1'], 'review.yaml')] },
    });
    await waitFor(() => expect(screen.getByTestId('preview-workflow-import')).toBeEnabled());
    fireEvent.click(screen.getByTestId('preview-workflow-import'));
    await waitFor(() => expect(screen.getByTestId('apply-workflow-import')).toBeEnabled());
    fireEvent.click(screen.getByTestId('apply-workflow-import'));

    await waitFor(() => expect(service.applyWorkflowImport).toHaveBeenCalledTimes(1));
    expect(service.previewWorkflowImport).toHaveBeenCalledTimes(1);
    expect(service.applyWorkflowImport).toHaveBeenCalledWith(
      expect.objectContaining({ previewDigest: 'reviewed-digest' }),
    );
    expect(await screen.findByText('Import preview changed; preview again')).toBeInTheDocument();
    expect(onImported).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });
});
