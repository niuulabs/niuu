import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { createMockValkyrieService } from '../adapters/mock';
import { wrapWithValkyrie } from '../testing/wrapWithValkyrie';
import { learningCorrelation, learningEyebrow, LearningViewer } from './LearningViewer';

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
