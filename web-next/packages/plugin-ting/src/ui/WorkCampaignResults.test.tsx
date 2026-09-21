import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi } from 'vitest';
import { WorkCampaignResults } from './WorkCampaignResults';

const result = {
  path: '/research/report.md',
  title: 'Recovery findings',
  summary: 'Verified recovery procedure',
  publishState: 'published',
  sourceIds: ['ticket:OPS-42'],
  content: '# Recovery evidence\n\nThe rollback was verified.',
};
const gate = {
  id: 'gate-42',
  node_id: 'spec-sdd-gate',
  summary: 'Review system design',
  instructions: 'Review the rollback evidence.',
};

function setup(
  options: {
    kind?: 'research' | 'specification';
    noCampaign?: boolean;
    noArtifacts?: boolean;
    campaignError?: boolean;
    artifactError?: boolean;
    noArtifact?: boolean;
    reviewError?: boolean;
    gate?: typeof gate | null;
    gates?: (typeof gate)[];
  } = {},
) {
  const data = {
    artifacts: options.noArtifacts ? [] : [result],
    metadata: {
      pending_workflow_gates:
        options.gates ?? (options.gate === null ? [] : [options.gate ?? gate]),
    },
  };
  const service = {
    getCampaign: options.campaignError
      ? vi.fn().mockRejectedValue(new Error('Campaign access denied'))
      : vi.fn().mockResolvedValue(options.noCampaign ? null : data),
    getArtifact: options.artifactError
      ? vi.fn().mockRejectedValue(new Error('Result access denied'))
      : vi.fn().mockResolvedValue(options.noArtifact ? null : result),
    reviewCampaign: options.reviewError
      ? vi.fn().mockRejectedValue(new Error('Gate already resolved'))
      : vi.fn().mockResolvedValue({}),
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ 'ting.research': service, 'ting.specs': service }}>
        <WorkCampaignResults kind={options.kind ?? 'research'} slug="recovery" />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return { service, client };
}

describe('Work campaign results', () => {
  it('loads evidence only on request, renders it in place and preserves the work context on close', async () => {
    const { service } = setup();
    const open = await screen.findByRole('button', { name: /Recovery findings/ });
    expect(service.getArtifact).not.toHaveBeenCalled();
    fireEvent.click(open);
    expect(await screen.findByRole('heading', { name: 'Recovery evidence' })).toBeInTheDocument();
    expect(service.getArtifact).toHaveBeenCalledWith('recovery', '/research/report.md');
    expect(screen.getByText('ticket:OPS-42')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Close', exact: true })[0]);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(open).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve specification' })).not.toBeInTheDocument();
  });

  it('reviews only the authoritative specification gate and refreshes work', async () => {
    const { service, client } = setup({ kind: 'specification' });
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const approve = await screen.findByRole('button', { name: 'Approve specification' });
    expect(screen.getByRole('button', { name: 'Request changes' })).toBeDisabled();
    fireEvent.click(approve);
    await waitFor(() =>
      expect(service.reviewCampaign).toHaveBeenCalledWith('recovery', {
        decision: 'approve',
        gateId: 'gate-42',
        nodeId: 'spec-sdd-gate',
        notes: undefined,
      }),
    );
    expect(await screen.findByText('Review submitted.')).toBeInTheDocument();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['ting', 'work'] });
  });

  it('keeps requested changes and evidence available when a review fails', async () => {
    const { service } = setup({ kind: 'specification', reviewError: true });
    fireEvent.change(await screen.findByLabelText('Review notes'), {
      target: { value: ' Clarify rollback ownership. ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Request changes' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Gate already resolved');
    expect(service.reviewCampaign).toHaveBeenCalledWith('recovery', {
      decision: 'changes_requested',
      gateId: 'gate-42',
      nodeId: 'spec-sdd-gate',
      notes: 'Clarify rollback ownership.',
    });
    expect(screen.getByLabelText('Review notes')).toHaveValue(' Clarify rollback ownership. ');
  });

  it('keeps independent review notes attached to their own gate', async () => {
    const other = {
      ...gate,
      id: 'gate-43',
      node_id: 'spec-prd-gate',
      summary: 'Review requirements',
    };
    const { service } = setup({ kind: 'specification', gates: [gate, other] });
    fireEvent.change(await screen.findByLabelText('Review notes for Review system design'), {
      target: { value: 'Design checked.' },
    });
    fireEvent.change(screen.getByLabelText('Review notes for Review requirements'), {
      target: { value: 'Clarify scope.' },
    });
    fireEvent.click(
      within(screen.getByRole('region', { name: 'Review requirements' })).getByRole('button', {
        name: 'Request changes',
      }),
    );
    await waitFor(() =>
      expect(service.reviewCampaign).toHaveBeenCalledWith('recovery', {
        decision: 'changes_requested',
        gateId: 'gate-43',
        nodeId: 'spec-prd-gate',
        notes: 'Clarify scope.',
      }),
    );
    await screen.findByText('Review submitted.');
    expect(screen.getByLabelText('Review notes for Review system design')).toHaveValue(
      'Design checked.',
    );
  });

  it('does not expose an approval for a gate without an identity', async () => {
    setup({ kind: 'specification', gate: { ...gate, id: '' }, noArtifacts: true });
    expect(await screen.findByText('No results have been published yet.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve specification' })).not.toBeInTheDocument();
  });

  it('distinguishes a missing campaign from a failed campaign read', async () => {
    setup({ noCampaign: true });
    expect(await screen.findByText('This campaign is no longer available.')).toBeInTheDocument();
  });

  it('preserves campaign read errors', async () => {
    setup({ campaignError: true });
    expect(await screen.findByRole('alert')).toHaveTextContent('Campaign access denied');
  });

  it('shows artifact permission errors inside the preview', async () => {
    setup({ artifactError: true });
    fireEvent.click(await screen.findByRole('button', { name: /Recovery findings/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Result access denied');
  });

  it('reports a removed artifact without inventing an empty document', async () => {
    setup({ noArtifact: true });
    fireEvent.click(await screen.findByRole('button', { name: /Recovery findings/ }));
    expect(await screen.findByText('This result is no longer available.')).toBeInTheDocument();
  });
});
