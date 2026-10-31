/** Classification shared by the Forge document viewer, matching native Lexi FileClassification. */
export type PreviewKind =
  | 'image'
  | 'pdf'
  | 'markdown'
  | 'mermaid'
  | 'html'
  | 'code'
  | 'text'
  | 'video'
  | 'audio'
  | 'download';
const languages: Record<string, string> = {
  ts: 'typescript',
  tsx: 'tsx',
  js: 'javascript',
  jsx: 'jsx',
  mjs: 'javascript',
  cjs: 'javascript',
  py: 'python',
  swift: 'swift',
  rs: 'rust',
  go: 'go',
  rb: 'ruby',
  java: 'java',
  kt: 'kotlin',
  c: 'c',
  h: 'c',
  cc: 'cpp',
  cpp: 'cpp',
  cs: 'csharp',
  css: 'css',
  scss: 'scss',
  json: 'json',
  jsonc: 'jsonc',
  yaml: 'yaml',
  yml: 'yaml',
  toml: 'toml',
  xml: 'xml',
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',
  sql: 'sql',
  diff: 'diff',
  patch: 'diff',
  vue: 'vue',
  svelte: 'svelte',
  php: 'php',
  dockerfile: 'dockerfile',
  graphql: 'graphql',
};
export function classifyPreview(name: string, mime = ''): { kind: PreviewKind; language?: string } {
  const lower = name.toLowerCase();
  const ext = lower.split('.').at(-1) ?? '';
  if (mime.startsWith('image/') || /^(png|jpe?g|gif|webp|svg|avif|bmp|ico)$/.test(ext))
    return { kind: 'image' };
  if (mime === 'application/pdf' || ext === 'pdf') return { kind: 'pdf' };
  if (/^(md|markdown|mdown|mdx)$/.test(ext) || mime === 'text/markdown')
    return { kind: 'markdown', language: 'markdown' };
  if (/^(mmd|mermaid)$/.test(ext)) return { kind: 'mermaid', language: 'mermaid' };
  if (/^(html?|xhtml)$/.test(ext) || mime === 'text/html')
    return { kind: 'html', language: 'html' };
  if (mime.startsWith('video/') || /^(mp4|webm|mov|m4v|ogv)$/.test(ext)) return { kind: 'video' };
  if (mime.startsWith('audio/') || /^(mp3|wav|ogg|m4a|aac|flac)$/.test(ext))
    return { kind: 'audio' };
  const language = languages[ext] ?? (lower === 'dockerfile' ? 'dockerfile' : undefined);
  if (language) return { kind: 'code', language };
  if (
    mime.startsWith('text/') ||
    /^(txt|log|csv|tsv|ini|env|conf|cfg|rst|tex)$/.test(ext) ||
    /^(readme|license|changelog|makefile|gitignore)$/.test(lower.replace(/^\./, ''))
  )
    return { kind: 'text' };
  return { kind: 'download' };
}
export function isTextPreview(kind: PreviewKind) {
  return ['markdown', 'mermaid', 'html', 'code', 'text'].includes(kind);
}
