import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import { ErrorState, LoadingState, MarkdownContent, Modal } from '@niuulabs/ui';
import type { IResearchService, ISpecsService, ReviewSpecCampaignRequest } from '../ports';
import { actionableSpecGates } from '../domain/campaignProgress';

/** Lazily reads the selected campaign through its existing owning service. */
export function WorkCampaignResults({
  kind,
  slug,
}: {
  kind: 'research' | 'specification';
  slug: string;
}) {
  const surface = kind === 'specification' ? 'specs' : 'research';
  const service = useService<IResearchService | ISpecsService>(`ting.${surface}`);
  const queryClient = useQueryClient();
  const [path, setPath] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const queryKey = ['ting', surface, 'campaign', slug];
  const campaign = useQuery({
    queryKey,
    queryFn: () => service.getCampaign(slug),
    refetchInterval: 15_000,
  });
  const artifact = useQuery({
    queryKey: [...queryKey, 'artifact', path],
    queryFn: () => service.getArtifact(slug, path!),
    enabled: path !== null,
  });
  const review = useMutation({
    mutationFn: (request: ReviewSpecCampaignRequest) => {
      if (!('reviewCampaign' in service))
        throw new Error('This work does not support specification review.');
      return service.reviewCampaign(slug, request);
    },
    onSuccess: async (_result, request) => {
      setNotes((current) => ({ ...current, [request.gateId!]: '' }));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey }),
        queryClient.invalidateQueries({ queryKey: ['ting', 'work'] }),
      ]);
    },
  });

  if (campaign.isPending) return <LoadingState label="Loading results…" />;
  if (campaign.isError) {
    return <ErrorState title="Results could not be loaded" message={campaign.error.message} />;
  }
  if (!campaign.data) return <p role="status">This campaign is no longer available.</p>;

  const gates = kind === 'specification' ? actionableSpecGates(campaign.data) : [];
  const artifacts = campaign.data.artifacts;
  return (
    <section className="ting-work-results" aria-label="Results and decisions">
      {artifacts.length > 0 ? (
        <>
          <h2>Results</h2>
          <div className="ting-work-list">
            {artifacts.map((item) => (
              <button
                className="ting-work-result-row"
                key={item.path}
                type="button"
                onClick={() => setPath(item.path)}
              >
                <strong>{item.title || item.path}</strong>
                {item.summary ? <span>{item.summary}</span> : null}
                <small>{item.publishState}</small>
              </button>
            ))}
          </div>
        </>
      ) : (
        <p className="ting-work-results__empty">No results have been published yet.</p>
      )}

      {gates.map((gate) => (
        <section className="ting-work-review" key={gate.id} aria-label={gate.summary}>
          <h2>{gate.summary}</h2>
          {gate.instructions ? <p>{gate.instructions}</p> : null}
          <label>
            {gates.length > 1 ? `Review notes for ${gate.summary}` : 'Review notes'}
            <textarea
              value={notes[gate.id] ?? ''}
              onChange={(event) =>
                setNotes((current) => ({ ...current, [gate.id]: event.target.value }))
              }
              disabled={review.isPending}
            />
          </label>
          <div className="ting-work-review__actions">
            <button
              type="button"
              disabled={review.isPending}
              onClick={() =>
                review.mutate({
                  decision: 'approve',
                  gateId: gate.id,
                  nodeId: gate.nodeId,
                  notes: notes[gate.id]?.trim() || undefined,
                })
              }
            >
              {review.isPending ? 'Submitting…' : 'Approve specification'}
            </button>
            <button
              type="button"
              disabled={review.isPending || !notes[gate.id]?.trim()}
              onClick={() =>
                review.mutate({
                  decision: 'changes_requested',
                  gateId: gate.id,
                  nodeId: gate.nodeId,
                  notes: notes[gate.id]?.trim(),
                })
              }
            >
              Request changes
            </button>
          </div>
        </section>
      ))}
      {review.isError ? <p role="alert">{review.error.message}</p> : null}
      {review.isSuccess ? <p role="status">Review submitted.</p> : null}

      <Modal
        open={path !== null}
        onOpenChange={(open) => {
          if (!open) setPath(null);
        }}
        title={artifact.data?.title || 'Result preview'}
        className="niuu:max-w-[1000px]"
        actions={[{ label: 'Close', variant: 'secondary' }]}
      >
        {artifact.isPending ? (
          <LoadingState label="Loading result…" />
        ) : artifact.isError ? (
          <ErrorState title="Result could not be loaded" message={artifact.error.message} />
        ) : artifact.data ? (
          <article className="ting-work-result-preview">
            <MarkdownContent content={artifact.data.content} />
            <details>
              <summary>Source</summary>
              <p>{artifact.data.path}</p>
              <p>{artifact.data.sourceIds.join(', ')}</p>
            </details>
          </article>
        ) : (
          <p role="status">This result is no longer available.</p>
        )}
      </Modal>
    </section>
  );
}
