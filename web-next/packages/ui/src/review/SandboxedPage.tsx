import { useEffect, useMemo, useRef, useState } from 'react';
import type { IFileSystemPort } from './IFileSystemPort';
import './SandboxedPage.css';

const CHANNEL = 'niuu.review';
const PAGE_SIZE_LIMIT = 2 * 1024 * 1024;
const MESSAGE_SIZE_LIMIT = 4000;

/** Scripts run, but nothing reaches a network: only what the page holds or is handed. */
const PAGE_POLICY = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  'img-src data: blob:',
  'media-src data: blob:',
  'font-src data:',
  'connect-src blob: data:',
  "base-uri 'none'",
  "form-action 'none'",
].join('; ');

/** What a page calls. It can show and suggest; sending and deciding stay with the person. */
const PAGE_BRIDGE = `(() => {
  const waiting = new Map();
  let nextId = 0;
  const call = (type, payload) =>
    new Promise((resolve, reject) => {
      nextId += 1;
      waiting.set(nextId, { resolve, reject });
      parent.postMessage({ channel: '${CHANNEL}', id: nextId, type, ...payload }, '*');
    });
  addEventListener('message', (event) => {
    if (event.source !== parent || event.data?.channel !== '${CHANNEL}') return;
    const call = waiting.get(event.data.id);
    if (!call) return;
    waiting.delete(event.data.id);
    if (event.data.error) call.reject(new Error(event.data.error));
    else call.resolve(event.data.result);
  });
  window.niuu = {
    review: {
      context: () => call('context'),
      quote: (text) => call('quote', { text }),
      ask: (text) => call('ask', { text }),
      file: (name) => call('file', { name }),
    },
  };
})();`;

/** A page's whole document: policy first, so nothing it contains runs before it applies. */
export function pageDocument(html: string): string {
  return `<!doctype html><meta http-equiv="Content-Security-Policy" content="${PAGE_POLICY}"><script>${PAGE_BRIDGE}</script>${html}`;
}

/** Nothing a frame can do stops it navigating itself, only the document that embeds it. */
const FRAME_POLICY = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  "frame-src 'none'",
].join('; ');

/**
 * The frame around a page. Its policy is what keeps a page from carrying data out
 * in a URL, which a page's own policy cannot do, and it relays the bridge.
 */
export function frameDocument(html: string): string {
  const inner = JSON.stringify(pageDocument(html)).replace(/</g, '\\u003c');
  return `<!doctype html><meta http-equiv="Content-Security-Policy" content="${FRAME_POLICY}"><style>html,body{margin:0;height:100%}iframe{display:block;width:100%;height:100%;border:0}</style><script>(() => {
  const page = document.createElement('iframe');
  page.setAttribute('sandbox', 'allow-scripts');
  let loads = 0;
  page.addEventListener('load', () => {
    loads += 1;
    if (loads > 1) parent.postMessage({ channel: '${CHANNEL}', id: 0, type: 'left' }, '*');
  });
  addEventListener('message', (event) => {
    if (event.source === page.contentWindow) {
      parent.postMessage(event.data, '*');
    } else if (event.source === parent) {
      const bytes = event.data?.result;
      page.contentWindow.postMessage(event.data, '*', bytes instanceof ArrayBuffer ? [bytes] : []);
    }
  });
  page.srcdoc = ${inner};
  document.documentElement.append(page);
})();</script>`;
}

export interface SandboxedPageProps {
  title: string;
  html: string;
  /** Plain facts the page may read about this review. Never credentials. */
  context: Readonly<Record<string, string | number>>;
  /** Files the workflow declared; anything else is refused. */
  files: readonly string[];
  readFile: (name: string) => Promise<ArrayBuffer>;
  /** Puts text in front of the person, who decides whether to send it. */
  onQuote?: (text: string) => void;
  onAsk?: (text: string) => void;
}

function asText(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error('Expected some text');
  if (value.length > MESSAGE_SIZE_LIMIT) {
    throw new Error(`Text is over the ${MESSAGE_SIZE_LIMIT} character limit`);
  }
  return value;
}

/** A workflow's own page, cut off from Niuu: it has no origin, no session and no network. */
export function SandboxedPage({
  title,
  html,
  context,
  files,
  readFile,
  onQuote,
  onAsk,
}: SandboxedPageProps) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [left, setLeft] = useState(false);
  const document = useMemo(() => frameDocument(html), [html]);

  useEffect(() => {
    async function answer(request: Record<string, unknown>): Promise<unknown> {
      switch (request.type) {
        case 'context':
          return context;
        case 'quote': {
          if (!onQuote) throw new Error('Quoting is closed: this review is decided');
          onQuote(asText(request.text));
          return true;
        }
        case 'ask': {
          if (!onAsk) throw new Error('There is no coordinator to ask');
          onAsk(asText(request.text));
          return true;
        }
        case 'file': {
          const name = String(request.name);
          if (!files.includes(name)) throw new Error(`This workflow did not declare ${name}`);
          return readFile(name);
        }
        default:
          throw new Error(`Unknown request: ${String(request.type)}`);
      }
    }

    function onMessage(event: MessageEvent) {
      const target = frame.current?.contentWindow;
      const request = event.data as Record<string, unknown> | null;
      if (!target || event.source !== target || request?.channel !== CHANNEL) return;
      // The frame blocks a page leaving and says so; a page gains nothing by faking it.
      if (request.type === 'left') {
        setLeft(true);
        return;
      }
      const reply = { channel: CHANNEL, id: request.id };
      answer(request).then(
        (result) => {
          const transfer = result instanceof ArrayBuffer ? [result] : [];
          target.postMessage({ ...reply, result }, '*', transfer);
        },
        (failure: unknown) => {
          const error = failure instanceof Error ? failure.message : 'Request failed';
          target.postMessage({ ...reply, error }, '*');
        },
      );
    }

    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [context, files, onAsk, onQuote, readFile]);

  return (
    <div className="niuu-sandboxed-page">
      <div className="niuu-sandboxed-page__bar">
        <span>{title}</span>
        <span>Page from this workflow · sandboxed</span>
      </div>
      {left ? (
        <p role="alert">This page tried to leave its document, so Niuu closed it.</p>
      ) : (
        <iframe
          ref={frame}
          key={document}
          title={title}
          sandbox="allow-scripts"
          referrerPolicy="no-referrer"
          srcDoc={document}
        />
      )}
    </div>
  );
}

/** A page from a session's workspace, with the files its workflow declared. */
export function SessionPage({
  sessionId,
  path,
  title,
  context,
  files,
  filesystem,
  onQuote,
  onAsk,
}: Omit<SandboxedPageProps, 'html' | 'readFile'> & {
  sessionId: string;
  /** Workspace-relative, as the workflow declares it. */
  path: string;
  filesystem: IFileSystemPort;
}) {
  const [read, setRead] = useState<{ path: string; html?: string; error?: string }>();
  const download = filesystem.downloadFile?.bind(filesystem);

  useEffect(() => {
    if (!download) return;
    const abort = new AbortController();
    void download(sessionId, `/workspace/${path}`, abort.signal)
      .then(async (blob) => {
        if (blob.size > PAGE_SIZE_LIMIT) {
          throw new Error(
            `This page is ${blob.size} bytes, over the ${PAGE_SIZE_LIMIT} byte limit. Declare large assets as files.`,
          );
        }
        const html = await blob.text();
        if (!abort.signal.aborted) setRead({ path, html });
      })
      .catch((failure: unknown) => {
        if (abort.signal.aborted) return;
        const error = failure instanceof Error ? failure.message : 'Could not load this page';
        setRead({ path, error });
      });
    return () => abort.abort();
    // `download` is rebuilt each render; the filesystem it wraps is what matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filesystem, path, sessionId]);

  const readFile = useMemo(
    () => async (name: string) => {
      if (!download) throw new Error('File downloads are unavailable on this backend.');
      return (await download(sessionId, `/workspace/${name}`)).arrayBuffer();
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filesystem, sessionId],
  );

  if (!download) return <p role="alert">File downloads are unavailable on this backend.</p>;
  const current = read?.path === path ? read : undefined;
  if (current?.error) return <p role="alert">{current.error}</p>;
  if (current?.html === undefined) return <p role="status">Loading page…</p>;
  return (
    <SandboxedPage
      title={title}
      html={current.html}
      context={context}
      files={files}
      readFile={readFile}
      onQuote={onQuote}
      onAsk={onAsk}
    />
  );
}
