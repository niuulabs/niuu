// Adapted from @lexi/markdown (lexi-frontend, 552b770).
/**
 * remark plugin: turn bare file-path references in prose into links.
 *
 * Coding agents mention files inline ("see src/App.tsx" / "edit ./styles/x.css" /
 * "open CLAUDE.md" / "wrote /home/thor/notes.md") without markdown link syntax.
 * This walks the mdast and wraps path-like tokens in `link` nodes so the app's
 * `<a>` renderer (which knows the workspace root) can open them in the file
 * viewer. Two tiers, tuned against real coding-session transcripts:
 *
 *   - A token WITH a slash (relative, `./`, `../`, or absolute `/abs`) is a path:
 *     accept any letter-led extension. A slash is a strong signal.
 *   - A BARE filename (no slash, e.g. `CLAUDE.md`, `gemma4-serve.sh`) is linked
 *     ONLY when its extension is in {@link KNOWN_EXT}. This catches real files
 *     while leaving CSS selectors and prose alone (`div.mermaidToo`, `rect.text`,
 *     `.marker.cross`, `Node.js`/`React.js` library names).
 *
 * A `:line[:col]` editor suffix is allowed and preserved (the link resolver
 * strips it and recovers the line). Existing `link` nodes are never touched (no
 * nested anchors); `inlineCode` paths (very common in tables/prose) are wrapped
 * too so backticked paths stay monospace AND become clickable.
 */

interface MdNode {
  type: string;
  value?: string;
  url?: string;
  children?: MdNode[];
}

// Path-like token: optional `/`, `./`, or `../` lead, zero or more `segment/`
// dirs, then `name.ext`, optional `:line[:col]`. The dir count is zero-or-more
// so BARE filenames match too — the slash/extension gating happens in
// `acceptPath`, not the regex, so we can apply different rules to each tier.
const PATH_BODY =
  '(?:\\/|\\.\\.?\\/)?(?:[\\w.@~-]+\\/)*[\\w.@~-]+\\.[A-Za-z][\\w]{0,9}(?::\\d+(?::\\d+)?)?';
const PATH_RE = new RegExp(PATH_BODY, 'g');
// Anchored variant: the WHOLE string is a single path (for inline-code spans).
const PATH_FULL_RE = new RegExp(`^${PATH_BODY}$`);

// Extensions trusted for BARE filenames (no directory). Slash-bearing paths skip
// this gate entirely. Lowercased; the filename's case is irrelevant.
const KNOWN_EXT = new Set([
  // code
  'ts',
  'tsx',
  'js',
  'jsx',
  'mjs',
  'cjs',
  'py',
  'pyi',
  'rb',
  'go',
  'rs',
  'java',
  'kt',
  'kts',
  'swift',
  'c',
  'h',
  'cc',
  'cpp',
  'hpp',
  'cxx',
  'cs',
  'php',
  'scala',
  'clj',
  'cljs',
  'ex',
  'exs',
  'erl',
  'hs',
  'ml',
  'lua',
  'r',
  'dart',
  'sql',
  'pl',
  'pm',
  'groovy',
  'gradle',
  'vb',
  'fs',
  'jl',
  'nim',
  'zig',
  // shell / scripts
  'sh',
  'bash',
  'zsh',
  'fish',
  'ps1',
  'bat',
  'cmd',
  // web / markup / styles
  'html',
  'htm',
  'xhtml',
  'css',
  'scss',
  'sass',
  'less',
  'styl',
  'vue',
  'svelte',
  'astro',
  'xml',
  'svg',
  // data / config
  'json',
  'json5',
  'jsonc',
  'yaml',
  'yml',
  'toml',
  'ini',
  'env',
  'csv',
  'tsv',
  'proto',
  'graphql',
  'gql',
  'lock',
  'cfg',
  'conf',
  'config',
  'properties',
  'plist',
  'editorconfig',
  // docs / text
  'md',
  'mdx',
  'markdown',
  'txt',
  'rst',
  'adoc',
  'tex',
  'org',
  'log',
  'diff',
  'patch',
  // certs / keys
  'pem',
  'key',
  'crt',
  'cert',
  'cer',
  'pub',
  // build / infra
  'make',
  'mk',
  'cmake',
  'dockerfile',
  'tf',
  'tfvars',
  // notebooks
  'ipynb',
]);

// Bare dotted tokens that are library/framework NAMES, not files — these have
// file-ish extensions (`js`) but are prose, so exclude them in the no-slash tier.
const SKIP_BARE = new Set([
  'node.js',
  'react.js',
  'vue.js',
  'next.js',
  'nuxt.js',
  'three.js',
  'd3.js',
  'express.js',
  'jquery.js',
  'angular.js',
  'ember.js',
  'backbone.js',
  'next.ts',
]);

/** Lowercased extension of a path token (after stripping any `:line` suffix). */
function extOf(token: string): string {
  const noLine = token.replace(/:\d+(?::\d+)?$/, '');
  const dot = noLine.lastIndexOf('.');
  return dot === -1 ? '' : noLine.slice(dot + 1).toLowerCase();
}

/** Decide whether a matched path-like token should become a link. */
function acceptPath(token: string): boolean {
  // Slash present -> it's a path. Accept any extension.
  if (token.includes('/')) return true;
  // Bare filename -> only trusted extensions, and never a known library name.
  if (SKIP_BARE.has(token.toLowerCase())) return false;
  return KNOWN_EXT.has(extOf(token));
}

/** True if the entire trimmed string is one acceptable file path. */
function isFullPath(value: string): boolean {
  const v = value.trim();
  return PATH_FULL_RE.test(v) && acceptPath(v);
}

function splitTextValue(value: string): MdNode[] | null {
  const nodes: MdNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  PATH_RE.lastIndex = 0;
  while ((m = PATH_RE.exec(value)) !== null) {
    const matched = m[0];
    const start = m.index;
    // Skip when this looks like part of a URL — preceded by "/" (so `://host/a/b.ts`
    // matches starting after the host, prev "/") or ":" (scheme). gfm autolinks
    // real URLs into link nodes we never reach; this guards the leftovers.
    // Absolute paths still pass: their match starts AT the leading "/", whose
    // prev is whitespace/punctuation, not "/".
    const prev = start > 0 ? value[start - 1] : '';
    if (prev === '/' || prev === ':') continue;
    if (!acceptPath(matched)) continue;
    if (start > last) nodes.push({ type: 'text', value: value.slice(last, start) });
    nodes.push({ type: 'link', url: matched, children: [{ type: 'text', value: matched }] });
    last = start + matched.length;
  }
  if (nodes.length === 0) return null;
  if (last < value.length) nodes.push({ type: 'text', value: value.slice(last) });
  return nodes;
}

function walk(node: MdNode): void {
  const children = node.children;
  if (!children || children.length === 0) return;
  // Never linkify inside an existing link (avoid nested anchors).
  if (node.type === 'link') return;
  const next: MdNode[] = [];
  for (const child of children) {
    if (child.type === 'text' && typeof child.value === 'string') {
      const split = splitTextValue(child.value);
      if (split) next.push(...split);
      else next.push(child);
    } else if (
      child.type === 'inlineCode' &&
      typeof child.value === 'string' &&
      isFullPath(child.value)
    ) {
      // A backticked path (very common in tables/prose, e.g. `src/App.tsx` or
      // `CLAUDE.md`) — wrap it so it's clickable while keeping monospace styling.
      next.push({ type: 'link', url: child.value.trim(), children: [child] });
    } else {
      walk(child);
      next.push(child);
    }
  }
  node.children = next;
}

/** remark plugin factory. Use in `remarkPlugins` (gated by a feature flag). */
export function remarkLinkifyPaths() {
  return (tree: unknown): void => walk(tree as MdNode);
}
