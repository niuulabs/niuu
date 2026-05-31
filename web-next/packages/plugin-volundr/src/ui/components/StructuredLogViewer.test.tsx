import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { StructuredLogViewer } from './StructuredLogViewer';
import type { VolundrAggregatedLog, VolundrLogParticipant } from '../../models/volundr.model';

function makeLog(id: string, overrides: Partial<VolundrAggregatedLog> = {}): VolundrAggregatedLog {
  return {
    id,
    sessionId: 'session-1',
    timestamp: Date.parse('2026-05-13T00:00:00Z'),
    level: 'info',
    participant: 'warden-1',
    participantLabel: 'Warden One',
    participantKind: 'service',
    source: 'ravn.daemon',
    message: 'dream cycle completed',
    sequence: 1,
    stream: 'stdout',
    ...overrides,
  };
}

describe('StructuredLogViewer', () => {
  it('infers participant filters from logs when no participant list is provided', () => {
    render(
      <StructuredLogViewer
        logs={[
          makeLog('log-1'),
          makeLog('log-2', {
            participant: 'warden-2',
            participantLabel: 'Warden Two',
            participantKind: 'ravn',
            message: 'fresh source ingested',
          }),
        ]}
      />,
    );

    expect(screen.getByRole('button', { name: /All/i })).toBeInTheDocument();
    expect(screen.getByTestId('log-participant-warden-1')).toHaveTextContent('Warden One');
    expect(screen.getByTestId('log-participant-warden-2')).toHaveTextContent('Warden Two');

    fireEvent.click(screen.getByTestId('log-participant-warden-2'));
    expect(screen.getByText('fresh source ingested')).toBeInTheDocument();
    expect(screen.queryByText('dream cycle completed')).not.toBeInTheDocument();
  });

  it('filters log lines by search query and falls back to the empty state', () => {
    render(
      <StructuredLogViewer
        logs={[
          makeLog('log-1', { message: 'dream cycle completed' }),
          makeLog('log-2', { message: 'lint autofix applied', source: 'mimir.lint' }),
        ]}
        emptyText="No matching daemon logs."
      />,
    );

    fireEvent.change(screen.getByRole('textbox', { name: /search logs/i }), {
      target: { value: 'lint' },
    });
    expect(screen.getByText('lint autofix applied')).toBeInTheDocument();
    expect(screen.queryByText('dream cycle completed')).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox', { name: /search logs/i }), {
      target: { value: 'missing-entry' },
    });
    expect(screen.getByText('No matching daemon logs.')).toBeInTheDocument();
  });

  it('supports uncontrolled and controlled level filtering', () => {
    const onLevelChange = vi.fn();
    const logs = [
      makeLog('log-1', { level: 'info', message: 'daemon started' }),
      makeLog('log-2', { level: 'error', message: 'provider auth failed' }),
    ];

    const { rerender } = render(<StructuredLogViewer logs={logs} initialLevel="ERROR" />);
    expect(screen.getByText('provider auth failed')).toBeInTheDocument();
    expect(screen.queryByText('daemon started')).not.toBeInTheDocument();

    rerender(<StructuredLogViewer logs={logs} level="INFO" onLevelChange={onLevelChange} />);
    fireEvent.change(screen.getByDisplayValue('Info'), {
      target: { value: 'ERROR' },
    });
    expect(onLevelChange).toHaveBeenCalledWith('ERROR');
    expect(screen.getByText('daemon started')).toBeInTheDocument();
  });

  it('resets a stale participant filter when the participant list changes', () => {
    const logs = [
      makeLog('log-1'),
      makeLog('log-2', {
        participant: 'warden-2',
        participantLabel: 'Warden Two',
        participantKind: 'ravn',
        message: 'new page compiled',
      }),
    ];
    const initialParticipants: VolundrLogParticipant[] = [
      { id: 'warden-1', label: 'Warden One', kind: 'service' },
      { id: 'warden-2', label: 'Warden Two', kind: 'ravn' },
    ];
    const nextParticipants: VolundrLogParticipant[] = [
      { id: 'warden-1', label: 'Warden One', kind: 'service' },
    ];

    const { rerender } = render(
      <StructuredLogViewer logs={logs} participants={initialParticipants} />,
    );

    fireEvent.click(screen.getByTestId('log-participant-warden-2'));
    expect(screen.getByText('new page compiled')).toBeInTheDocument();
    expect(screen.queryByText('dream cycle completed')).not.toBeInTheDocument();

    rerender(<StructuredLogViewer logs={logs} participants={nextParticipants} />);
    expect(screen.getByText('dream cycle completed')).toBeInTheDocument();
    expect(screen.getByText('new page compiled')).toBeInTheDocument();
  });

  it('downloads the filtered logs and can hide toolbar controls', () => {
    const createObjectURL = vi.fn(() => 'blob:warden-logs');
    const revokeObjectURL = vi.fn();
    const anchorClick = vi.fn();
    const createElement = vi.spyOn(document, 'createElement');

    vi.stubGlobal(
      'URL',
      Object.assign(URL, {
        createObjectURL,
        revokeObjectURL,
      }),
    );
    createElement.mockImplementation((tagName: string) => {
      const element = document.createElementNS('http://www.w3.org/1999/xhtml', tagName);
      if (tagName.toLowerCase() === 'a') {
        Object.defineProperty(element, 'click', { value: anchorClick });
      }
      return element;
    });

    const { rerender } = render(
      <StructuredLogViewer
        logs={[makeLog('log-1')]}
        downloadFilename="warden.log"
        showParticipantFilters={false}
      />,
    );

    expect(screen.queryByRole('button', { name: /^All$/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /download/i }));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(anchorClick).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:warden-logs');

    rerender(<StructuredLogViewer logs={[makeLog('log-1')]} showDownload={false} />);
    expect(screen.queryByRole('button', { name: /download/i })).not.toBeInTheDocument();

    createElement.mockRestore();
  });

  it('shows the loading state before logs arrive', () => {
    render(<StructuredLogViewer logs={[]} loading />);
    expect(screen.getByText('Loading logs…')).toBeInTheDocument();
  });
});
