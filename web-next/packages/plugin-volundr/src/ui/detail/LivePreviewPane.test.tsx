import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { LivePreviewPane } from './LivePreviewPane';
import { LIVE_PREVIEW_DISABLED } from '../../domain/livePreview';

const config = { enabled: true, originTemplate: 'https://{port}--{session_id}.preview.niuu.world' };

describe('LivePreviewPane', () => {
  it('embeds the open port on its own origin, with reload and open in a new tab', () => {
    render(
      <LivePreviewPane
        sessionId="s1"
        config={config}
        ports={[5173, 8000]}
        port={5173}
        onSelectPort={vi.fn()}
      />,
    );
    const frame = screen.getByTitle('Live preview of port 5173');
    expect(frame.getAttribute('src')).toBe('https://5173--s1.preview.niuu.world/');
    expect(frame.getAttribute('sandbox')).toContain('allow-scripts');
    expect(screen.getByRole('link', { name: 'Open in a new tab' }).getAttribute('href')).toBe(
      'https://5173--s1.preview.niuu.world/',
    );
    expect(screen.getByRole('link', { name: 'Open in a new tab' }).getAttribute('rel')).toContain(
      'noopener',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(screen.getByTitle('Live preview of port 5173')).not.toBe(frame);
  });

  it('navigates to a typed path', () => {
    render(
      <LivePreviewPane
        sessionId="s1"
        config={config}
        ports={[5173]}
        port={5173}
        onSelectPort={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByLabelText('Preview path'), { target: { value: 'docs/paging' } });
    fireEvent.submit(screen.getByLabelText('Preview path'));
    expect(screen.getByTitle('Live preview of port 5173').getAttribute('src')).toBe(
      'https://5173--s1.preview.niuu.world/docs/paging',
    );
  });

  it('switches ports from the chips', () => {
    const onSelectPort = vi.fn();
    render(
      <LivePreviewPane
        sessionId="s1"
        config={config}
        ports={[5173, 8000]}
        port={5173}
        onSelectPort={onSelectPort}
      />,
    );
    expect(screen.getByRole('button', { name: '5173' }).getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: '8000' }));
    expect(onSelectPort).toHaveBeenCalledWith(8000);
  });

  it('shows the first open port when none is chosen', () => {
    render(
      <LivePreviewPane
        sessionId="s1"
        config={config}
        ports={[3000]}
        port={null}
        onSelectPort={vi.fn()}
      />,
    );
    expect(screen.getByTitle('Live preview of port 3000')).toBeTruthy();
  });

  it('says so when nothing listens yet, or the chosen port closed', () => {
    const { rerender } = render(
      <LivePreviewPane
        sessionId="s1"
        config={config}
        ports={[]}
        port={null}
        onSelectPort={vi.fn()}
      />,
    );
    expect(screen.getByText(/Nothing in this session is listening yet/)).toBeTruthy();
    rerender(
      <LivePreviewPane
        sessionId="s1"
        config={config}
        ports={[]}
        port={5173}
        onSelectPort={vi.fn()}
      />,
    );
    expect(screen.getByText('Port 5173 stopped listening.')).toBeTruthy();
    expect(screen.queryByTitle('Live preview of port 5173')).toBeNull();
  });

  it('explains when the platform serves no previews', () => {
    render(
      <LivePreviewPane
        sessionId="s1"
        config={LIVE_PREVIEW_DISABLED}
        ports={[5173]}
        port={5173}
        onSelectPort={vi.fn()}
      />,
    );
    expect(screen.getByText('Live preview is not set up on this platform.')).toBeTruthy();
  });
});
