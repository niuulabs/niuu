import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { createMockValkyrieService } from '../adapters/mock';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { learningCorrelation, learningEyebrow, LearningViewer } from './LearningViewer';

/** The console's wiring in miniature: the viewer can switch its own record. */
function ViewerHarness({ initialLearningId }: { initialLearningId: string }) {
  const [learningId, setLearningId] = useState<string | null>(initialLearningId);
  return (
    <LearningViewer
      learningId={learningId}
      onClose={() => setLearningId(null)}
      onNavigate={setLearningId}
    />
  );
}

describe('LearningViewer', () => {
  it('renders nothing while no learning is selected', () => {
    render(<LearningViewer learningId={null} onClose={() => {}} />, {
      wrapper: wrapWithValkyrie(),
    });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('shows header, chips, structured payload, raw json, correlation, and evidence', async () => {
    render(<LearningViewer learningId="learn-k8s-oom-canary" onClose={() => {}} />, {
      wrapper: wrapWithValkyrie(),
    });

    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(dialog).toHaveTextContent('OOMKilled with rising queue depth'));

    // Header: newest history event as the eyebrow, plus the muted id.
    const header = screen.getByTestId('learning-viewer-header');
    expect(header).toHaveTextContent('valkyrie.evolution.activated');
    expect(header).toHaveTextContent('learn-k8s-oom-canary');

    // Chip row: status, scope, confidence.
    expect(screen.getByTestId('learning-viewer-status')).toHaveTextContent('canary');
    expect(screen.getByTestId('learning-viewer-scope')).toHaveTextContent('flock');
    expect(dialog).toHaveTextContent('81%');

    // Structured payload rows.
    const payload = screen.getByTestId('learning-viewer-payload');
    expect(payload).toHaveTextContent('env-k8s-valhalla');
    expect(payload).toHaveTextContent('valkyrie-valhalla-runa');
    expect(screen.getByTestId('learning-viewer-repetition')).toHaveTextContent('×3');
    expect(screen.getByTestId('learning-viewer-feedback')).toHaveTextContent('awaiting');
    expect(payload).toHaveTextContent('none'); // redaction
    expect(payload).toHaveTextContent('3/3 replayed incidents');

    // Raw JSON block carries the full record.
    expect(screen.getByTestId('learning-viewer-json')).toHaveTextContent(
      '"promotedTool": "k8s_memory_pressure_probe"',
    );

    // No correlation on this record.
    expect(screen.getByTestId('learning-viewer-correlation')).toHaveTextContent('—');

    // Evidence & links.
    const evidence = screen.getByTestId('learning-viewer-evidence');
    expect(evidence).toHaveTextContent('sig-k8s-oom-1');
    expect(evidence).toHaveTextContent('valkyrie-valhalla-runa');
  });

  it('hides repetition, shows the recorded feedback verdict, and defaults the eyebrow', async () => {
    render(<LearningViewer learningId="learn-printer-resin-stall" onClose={() => {}} />, {
      wrapper: wrapWithValkyrie(),
    });

    await screen.findByTestId('learning-viewer-payload');
    // No history: the eyebrow falls back to the literal recorded event.
    expect(screen.getByTestId('learning-viewer-header')).toHaveTextContent('learning.recorded');
    expect(screen.queryByTestId('learning-viewer-repetition')).toBeNull();
    expect(screen.getByTestId('learning-viewer-feedback')).toHaveTextContent('useful');
  });

  it('explains a missing learning calmly instead of showing an empty drawer (404)', async () => {
    render(<LearningViewer learningId="learn-ghost" onClose={() => {}} />, {
      wrapper: wrapWithValkyrie(),
    });

    const missing = await screen.findByTestId('learning-viewer-missing');
    expect(missing).toHaveTextContent('learn-ghost is no longer available');
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('surfaces load failures', async () => {
    const broken = {
      ...createMockValkyrieService(),
      getLearning: () => Promise.reject(new Error('learnings offline')),
    };
    render(<LearningViewer learningId="learn-k8s-oom-canary" onClose={() => {}} />, {
      wrapper: wrapWithValkyrie({ valkyrie: broken }),
    });

    expect(await screen.findByRole('alert')).toHaveTextContent('learnings offline');
  });

  it('calls onClose when the drawer is dismissed', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<LearningViewer learningId="learn-k8s-oom-canary" onClose={onClose} />, {
      wrapper: wrapWithValkyrie(),
    });

    await screen.findByRole('dialog');
    await user.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('LearningViewer feedback', () => {
  it('records a Useful verdict and updates the feedback chip', async () => {
    const user = userEvent.setup();
    render(<LearningViewer learningId="learn-email-vendor-escalation" onClose={() => {}} />, {
      wrapper: wrapWithValkyrie(),
    });

    await screen.findByTestId('learning-viewer-payload');
    expect(screen.getByTestId('learning-viewer-feedback')).toHaveTextContent('awaiting');
    expect(screen.getByTestId('learning-feedback-useful')).toHaveAttribute('aria-pressed', 'false');

    await user.click(screen.getByTestId('learning-feedback-useful'));

    await waitFor(() =>
      expect(screen.getByTestId('learning-viewer-feedback')).toHaveTextContent('useful'),
    );
    expect(screen.getByTestId('learning-feedback-useful')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByTestId('learning-feedback-error')).toBeNull();
  });

  it('wrong tier expands a picker limited to adjacent scopes and submits the chosen one', async () => {
    const user = userEvent.setup();
    render(<LearningViewer learningId="learn-k8s-oom-canary" onClose={() => {}} />, {
      wrapper: wrapWithValkyrie(),
    });
    await screen.findByTestId('learning-viewer-payload');

    await user.click(screen.getByTestId('learning-feedback-wrong_tier'));
    const picker = screen.getByTestId('learning-wrongtier-picker');
    // flock scope: only environment and domain are adjacent tiers.
    expect(within(picker).getByTestId('learning-wrongtier-scope-environment')).toBeVisible();
    expect(within(picker).getByTestId('learning-wrongtier-scope-domain')).toBeVisible();
    expect(within(picker).queryByTestId('learning-wrongtier-scope-shared')).toBeNull();
    expect(within(picker).queryByTestId('learning-wrongtier-scope-private')).toBeNull();

    await user.click(within(picker).getByTestId('learning-wrongtier-scope-environment'));

    await waitFor(() =>
      expect(screen.getByTestId('learning-viewer-feedback')).toHaveTextContent('wrong tier'),
    );
    // The picker closes once the verdict is submitted.
    expect(screen.queryByTestId('learning-wrongtier-picker')).toBeNull();
  });

  it('toggles the wrong-tier picker closed without submitting', async () => {
    const user = userEvent.setup();
    render(<LearningViewer learningId="learn-k8s-oom-canary" onClose={() => {}} />, {
      wrapper: wrapWithValkyrie(),
    });
    await screen.findByTestId('learning-viewer-payload');

    await user.click(screen.getByTestId('learning-feedback-wrong_tier'));
    expect(screen.getByTestId('learning-wrongtier-picker')).toBeVisible();
    await user.click(screen.getByTestId('learning-feedback-wrong_tier'));
    expect(screen.queryByTestId('learning-wrongtier-picker')).toBeNull();
    expect(screen.getByTestId('learning-viewer-feedback')).toHaveTextContent('awaiting');
  });

  it('surfaces feedback failures with the API error detail', async () => {
    const user = userEvent.setup();
    const broken = {
      ...createMockValkyrieService(),
      sendLearningFeedback: () =>
        Promise.reject(
          Object.assign(new Error('422'), {
            detail: 'feedback already recorded for this learning',
          }),
        ),
    };
    render(<LearningViewer learningId="learn-k8s-oom-canary" onClose={() => {}} />, {
      wrapper: wrapWithValkyrie({ valkyrie: broken }),
    });
    await screen.findByTestId('learning-viewer-payload');

    await user.click(screen.getByTestId('learning-feedback-dismissed'));

    const alert = await screen.findByTestId('learning-feedback-error');
    expect(alert).toHaveTextContent('feedback already recorded for this learning');
    expect(alert).toHaveAttribute('role', 'alert');
  });
});

describe('LearningViewer edit mode', () => {
  it('edits a candidate in place: no supersede notice, revised text shown', async () => {
    const user = userEvent.setup();
    render(<ViewerHarness initialLearningId="learn-email-vendor-escalation" />, {
      wrapper: wrapWithValkyrie(),
    });
    await screen.findByTestId('learning-viewer-payload');

    await user.click(screen.getByTestId('learning-edit'));
    const summaryInput = screen.getByLabelText('Learning summary');
    await user.clear(summaryInput);
    await user.type(summaryInput, 'Flag contract-deadline emails from unknown senders.');
    await user.type(screen.getByTestId('learning-edit-reason'), 'tighten the sender rule');
    await user.click(screen.getByTestId('learning-edit-save'));

    // Same record, updated in place — the drawer shows the revised summary.
    await waitFor(() =>
      expect(screen.getByRole('dialog')).toHaveTextContent(
        'Flag contract-deadline emails from unknown senders.',
      ),
    );
    expect(screen.getByTestId('learning-viewer-header')).toHaveTextContent(
      'learn-email-vendor-escalation',
    );
    expect(screen.queryByTestId('learning-supersede-notice')).toBeNull();
    // The form closed back into the edit affordance.
    expect(screen.queryByTestId('learning-edit-form')).toBeNull();
    expect(screen.getByTestId('learning-edit')).toBeVisible();
  });

  it('refuses to save without a reason', async () => {
    const user = userEvent.setup();
    render(<ViewerHarness initialLearningId="learn-email-vendor-escalation" />, {
      wrapper: wrapWithValkyrie(),
    });
    await screen.findByTestId('learning-viewer-payload');

    await user.click(screen.getByTestId('learning-edit'));
    expect(screen.getByTestId('learning-edit-save')).toBeDisabled();
    await user.type(screen.getByTestId('learning-edit-reason'), '  ');
    expect(screen.getByTestId('learning-edit-save')).toBeDisabled();
    await user.type(screen.getByTestId('learning-edit-reason'), 'now with a reason');
    expect(screen.getByTestId('learning-edit-save')).toBeEnabled();
  });

  it('cancel closes the form and discards pending edits', async () => {
    const user = userEvent.setup();
    render(<ViewerHarness initialLearningId="learn-email-vendor-escalation" />, {
      wrapper: wrapWithValkyrie(),
    });
    await screen.findByTestId('learning-viewer-payload');

    await user.click(screen.getByTestId('learning-edit'));
    await user.type(screen.getByLabelText('Learning title'), ' scribbles');
    await user.click(screen.getByTestId('learning-edit-cancel'));
    expect(screen.queryByTestId('learning-edit-form')).toBeNull();

    // Reopening starts from the stored record again.
    await user.click(screen.getByTestId('learning-edit'));
    expect(screen.getByLabelText('Learning title')).toHaveValue('Vendor deadline language');
  });

  it('revising an installed learning shows the supersede notice and switches records', async () => {
    const user = userEvent.setup();
    render(<ViewerHarness initialLearningId="learn-k8s-oom-canary" />, {
      wrapper: wrapWithValkyrie(),
    });
    await screen.findByTestId('learning-viewer-payload');

    await user.click(screen.getByTestId('learning-edit'));
    await user.type(screen.getByTestId('learning-edit-reason'), 'narrow the queue-depth window');
    await user.click(screen.getByTestId('learning-edit-save'));

    const noticeText =
      'Created superseding candidate learn-k8s-oom-canary:rev1 — the original stays ' +
      'installed until it passes review';
    await waitFor(() =>
      expect(screen.getByTestId('learning-supersede-notice')).toHaveTextContent(noticeText),
    );
    // The drawer switched to the new candidate record.
    await waitFor(() =>
      expect(screen.getByTestId('learning-viewer-header')).toHaveTextContent(
        'learn-k8s-oom-canary:rev1',
      ),
    );
    expect(screen.getByTestId('learning-viewer-status')).toHaveTextContent('candidate');
  });

  it('surfaces revision failures with the API error detail', async () => {
    const user = userEvent.setup();
    const broken = {
      ...createMockValkyrieService(),
      reviseLearning: () =>
        Promise.reject(
          Object.assign(new Error('422'), {
            detail: 'at least one of title, summary, or content is required',
          }),
        ),
    };
    render(<ViewerHarness initialLearningId="learn-k8s-oom-canary" />, {
      wrapper: wrapWithValkyrie({ valkyrie: broken }),
    });
    await screen.findByTestId('learning-viewer-payload');

    await user.click(screen.getByTestId('learning-edit'));
    await user.type(screen.getByTestId('learning-edit-reason'), 'why not');
    await user.click(screen.getByTestId('learning-edit-save'));

    const alert = await screen.findByTestId('learning-edit-error');
    expect(alert).toHaveTextContent('at least one of title, summary, or content is required');
    // The form stays open so the operator can retry.
    expect(screen.getByTestId('learning-edit-form')).toBeVisible();
  });
});

describe('learningEyebrow', () => {
  it('picks the newest history event type', () => {
    expect(
      learningEyebrow({
        history: [
          { eventType: 'older', status: 'candidate', summary: '', observedAt: '2026-06-01' },
          { eventType: 'newest', status: 'canary', summary: '', observedAt: '2026-06-03' },
        ],
      }),
    ).toBe('newest');
  });

  it('falls back to learning.recorded without history', () => {
    expect(learningEyebrow({})).toBe('learning.recorded');
    expect(learningEyebrow({ history: [] })).toBe('learning.recorded');
  });
});

describe('learningCorrelation', () => {
  it('prefers a correlation id from the source evidence', () => {
    expect(learningCorrelation({ sourceEvidence: { correlationId: 'corr-1' } })).toBe('corr-1');
    expect(learningCorrelation({ sourceEvidence: { correlation_id: 'corr-2' } })).toBe('corr-2');
  });

  it('falls back to the command delivery event id, then empty', () => {
    expect(learningCorrelation({ commandDelivery: { eventId: 'evt-9' } })).toBe('evt-9');
    expect(learningCorrelation({})).toBe('');
  });
});
