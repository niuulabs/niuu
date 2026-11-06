/**
 * remark plugin: turn a bare loopback address in prose into a link.
 *
 * Agents report dev servers as `localhost:5173`, `127.0.0.1:8000` or `0.0.0.0:3000`,
 * often in backticks and without a scheme, so GFM's autolinks never see them. Each
 * becomes an `http://` link the host can open where it makes sense (a Forge session's
 * live preview, which serves that port on its own origin). Existing links are left alone.
 */

interface MdNode {
  type: string;
  value?: string;
  url?: string;
  children?: MdNode[];
}

const HOST = '(?:localhost|127\\.0\\.0\\.1|0\\.0\\.0\\.0|\\[::1\\])';
// The port is required; a path may follow. A trailing sentence period is not part of it.
const LOCAL_BODY = `${HOST}:\\d{1,5}(?:\\/[^\\s)\\]"'\`<>]*)?`;
const LOCAL_RE = new RegExp(`(?<![\\w./:-])${LOCAL_BODY}`, 'g');
const LOCAL_FULL_RE = new RegExp(`^(?:https?:\\/\\/)?${LOCAL_BODY}$`);
const TRAILING_PUNCTUATION = /[.,;:!?]+$/;

function href(token: string): string {
  return /^https?:\/\//.test(token) ? token : `http://${token}`;
}

function splitText(value: string): MdNode[] | null {
  const nodes: MdNode[] = [];
  let last = 0;
  for (const match of value.matchAll(LOCAL_RE)) {
    const token = match[0].replace(TRAILING_PUNCTUATION, '');
    const start = match.index ?? 0;
    if (start > last) nodes.push({ type: 'text', value: value.slice(last, start) });
    nodes.push({ type: 'link', url: href(token), children: [{ type: 'text', value: token }] });
    last = start + token.length;
  }
  if (nodes.length === 0) return null;
  if (last < value.length) nodes.push({ type: 'text', value: value.slice(last) });
  return nodes;
}

function walk(node: MdNode): void {
  if (!node.children?.length || node.type === 'link') return;
  const next: MdNode[] = [];
  for (const child of node.children) {
    if (child.type === 'text' && typeof child.value === 'string') {
      next.push(...(splitText(child.value) ?? [child]));
    } else if (
      child.type === 'inlineCode' &&
      typeof child.value === 'string' &&
      LOCAL_FULL_RE.test(child.value.trim())
    ) {
      next.push({ type: 'link', url: href(child.value.trim()), children: [child] });
    } else {
      walk(child);
      next.push(child);
    }
  }
  node.children = next;
}

export function remarkLinkifyLocalhost() {
  return (tree: unknown): void => walk(tree as MdNode);
}
