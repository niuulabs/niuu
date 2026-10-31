import { act, render, screen, waitFor } from '@testing-library/react';
import { Blob as NodeBlob } from 'node:buffer';
import { describe, expect, it, vi } from 'vitest';
import type { IFileSystemPort } from './IFileSystemPort';
import {
  SandboxedPage,
  SessionPage,
  frameDocument,
  pageDocument,
  type SandboxedPageProps,
} from './SandboxedPage';

function renderPage(overrides: Partial<SandboxedPageProps> = {}) {
  const props: SandboxedPageProps = {
    title: 'Layers',
    html: '<p>Hello</p>',
    context: { title: 'Bracket batch', revision: 1 },
    files: ['report/unit-17.jpg'],
    readFile: vi.fn().mockResolvedValue(new ArrayBuffer(8)),
    onQuote: vi.fn(),
    onAsk: vi.fn(),
    ...overrides,
  };
  render(<SandboxedPage {...props} />);
  const frame = screen.getByTitle('Layers') as HTMLIFrameElement;
  const child = { postMessage: vi.fn() };
  Object.defineProperty(frame, 'contentWindow', { value: child });
  return { props, frame, child };
}

function request(child: object, data: Record<string, unknown>) {
  act(() => {
    window.dispatchEvent(
      new MessageEvent('message', {
        source: child as MessageEventSource,
        data: { channel: 'niuu.review', id: 1, ...data },
      }),
    );
  });
}

describe('pageDocument', () => {
  it('puts the policy ahead of anything the page brings', () => {
    const document = pageDocument('<script>alert(1)</script>');

    expect(document.indexOf('Content-Security-Policy')).toBeLessThan(document.indexOf('alert(1)'));
    expect(document).toContain("default-src 'none'");
    expect(document).toContain("form-action 'none'");
  });

  it('allows no connection beyond what the page was handed', () => {
    expect(pageDocument('')).toContain('connect-src blob: data:');
    expect(pageDocument('')).not.toMatch(/https?:/);
  });

  it('gives a page no way to send or decide', () => {
    const document = pageDocument('');

    expect(document).toContain('quote:');
    expect(document).not.toMatch(/approve|decide|gate|send/i);
  });
});

describe('frameDocument', () => {
  it('forbids the page inside it from navigating anywhere', () => {
    const document = frameDocument('<p>Hello</p>');

    expect(document).toContain("frame-src 'none'");
    expect(document).toContain("page.setAttribute('sandbox', 'allow-scripts')");
  });

  it('keeps a page from closing the script that holds it', () => {
    const document = frameDocument('</script><script>alert(1)</script>');

    expect(document).not.toContain('</script><script>alert(1)');
    expect(document).toContain('\\u003c/script>');
  });
});

describe('SandboxedPage', () => {
  it('cuts the page off from Niuu', () => {
    const { frame } = renderPage();

    expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
    expect(frame.getAttribute('referrerpolicy')).toBe('no-referrer');
    expect(screen.getByText('Page from this workflow · sandboxed')).toBeInTheDocument();
  });

  it('hands a quote to the person', async () => {
    const { child, props } = renderPage();

    request(child, { type: 'quote', text: 'Unit 17 strings' });

    expect(props.onQuote).toHaveBeenCalledWith('Unit 17 strings');
    await waitFor(() =>
      expect(child.postMessage).toHaveBeenCalledWith(
        { channel: 'niuu.review', id: 1, result: true },
        '*',
        [],
      ),
    );
  });

  it('hands a question to the person', () => {
    const { child, props } = renderPage();

    request(child, { type: 'ask', text: 'Is this stringing?' });

    expect(props.onAsk).toHaveBeenCalledWith('Is this stringing?');
  });

  it('answers with the review context', async () => {
    const { child } = renderPage();

    request(child, { type: 'context' });

    await waitFor(() =>
      expect(child.postMessage.mock.calls[0]?.[0]).toMatchObject({
        result: { title: 'Bracket batch', revision: 1 },
      }),
    );
  });

  it('hands over a declared file by transferring its bytes', async () => {
    const buffer = new ArrayBuffer(8);
    const { child, props } = renderPage({ readFile: vi.fn().mockResolvedValue(buffer) });

    request(child, { type: 'file', name: 'report/unit-17.jpg' });

    expect(props.readFile).toHaveBeenCalledWith('report/unit-17.jpg');
    await waitFor(() =>
      expect(child.postMessage).toHaveBeenCalledWith(
        { channel: 'niuu.review', id: 1, result: buffer },
        '*',
        [buffer],
      ),
    );
  });

  it('refuses a file the workflow did not declare', async () => {
    const { child, props } = renderPage();

    request(child, { type: 'file', name: 'secrets.env' });

    expect(props.readFile).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(child.postMessage.mock.calls[0]?.[0]).toMatchObject({
        error: 'This workflow did not declare secrets.env',
      }),
    );
  });

  it.each([
    ['an unknown request', { type: 'approve' }, 'Unknown request: approve'],
    ['empty text', { type: 'quote', text: '  ' }, 'Expected some text'],
    ['too much text', { type: 'quote', text: 'x'.repeat(4001) }, 'character limit'],
  ])('refuses %s', async (_name, data, error) => {
    const { child, props } = renderPage();

    request(child, data);

    expect(props.onQuote).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(String(child.postMessage.mock.calls[0]?.[0].error)).toContain(error),
    );
  });

  it('says when a decided review takes no more quotes or questions', async () => {
    const { child } = renderPage({ onQuote: undefined, onAsk: undefined });

    request(child, { type: 'quote', text: 'Too late' });
    await waitFor(() =>
      expect(child.postMessage.mock.calls[0]?.[0].error).toContain('this review is decided'),
    );

    request(child, { type: 'ask', text: 'Too late' });
    await waitFor(() =>
      expect(child.postMessage.mock.calls[1]?.[0].error).toContain('no coordinator'),
    );
  });

  it('ignores messages from anywhere but its own frame', () => {
    const { child, props } = renderPage();

    request({ postMessage: vi.fn() }, { type: 'quote', text: 'Forged' });
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          source: child as unknown as MessageEventSource,
          data: { channel: 'something-else', type: 'quote', text: 'Other' },
        }),
      );
    });

    expect(props.onQuote).not.toHaveBeenCalled();
    expect(child.postMessage).not.toHaveBeenCalled();
  });

  it('closes a page that tried to navigate away', () => {
    const { child } = renderPage();

    request(child, { type: 'left' });

    expect(screen.getByRole('alert')).toHaveTextContent('tried to leave its document');
    expect(screen.queryByTitle('Layers')).not.toBeInTheDocument();
  });
});

describe('SessionPage', () => {
  function filesystem(downloadFile?: IFileSystemPort['downloadFile']): IFileSystemPort {
    return {
      listTree: vi.fn(),
      expandDirectory: vi.fn(),
      readFile: vi.fn(),
      writeFile: vi.fn(),
      deletePaths: vi.fn(),
      downloadFile,
    };
  }
  const base = {
    sessionId: 's1',
    path: 'report/index.html',
    title: 'Layers',
    context: {},
    files: [],
  };

  it('loads a page from the workspace', async () => {
    const download = vi
      .fn()
      .mockResolvedValue(new NodeBlob(['<p>Hi</p>'], { type: 'text/html' }) as unknown as Blob);
    render(<SessionPage {...base} filesystem={filesystem(download)} />);

    const frame = await screen.findByTitle('Layers');

    expect(frame.getAttribute('srcdoc')).toBe(frameDocument('<p>Hi</p>'));
    expect(download.mock.calls[0]?.slice(0, 2)).toEqual(['s1', '/workspace/report/index.html']);
  });

  it('refuses a page over the size limit, saying why', async () => {
    const big = new NodeBlob(['x'.repeat(2 * 1024 * 1024 + 1)]) as unknown as Blob;
    render(<SessionPage {...base} filesystem={filesystem(vi.fn().mockResolvedValue(big))} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Declare large assets as files');
  });

  it('says when the page cannot be read', async () => {
    const download = vi.fn().mockRejectedValue(new Error('Session is stopped'));
    render(<SessionPage {...base} filesystem={filesystem(download)} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('Session is stopped');
  });

  it('says when this backend cannot download files', () => {
    render(<SessionPage {...base} filesystem={filesystem()} />);

    expect(screen.getByRole('alert')).toHaveTextContent('unavailable');
  });
});
