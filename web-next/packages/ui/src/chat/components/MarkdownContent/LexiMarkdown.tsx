import { createContext, useContext, memo, type ReactNode } from 'react';
import ReactMarkdown, {
  defaultUrlTransform,
  type Components,
  type ExtraProps,
} from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { ConversationLink, ConversationImage } from '../ConversationResources';
import { OutcomeCard } from '../OutcomeCard';
import { MarkdownCodeBlock } from './MarkdownCodeBlock';
import { MermaidDiagram } from './MermaidDiagram';
import { remarkLinkifyPaths } from './remarkLinkifyPaths';

const Streaming = createContext(false);
// Native MathML is accessible, scalable and does not need a second webfont payload.
const rehypes: NonNullable<React.ComponentProps<typeof ReactMarkdown>['rehypePlugins']> = [
  [rehypeKatex, { output: 'mathml', strict: false, trust: false, throwOnError: false }],
];

function CodeFence({ node }: ExtraProps) {
  const streaming = useContext(Streaming);
  const code = node?.children[0];
  if (code?.type !== 'element') return null;
  const language = String(code.properties.className ?? '')
    .replace(/^language-/, '')
    .split(' ')[0];
  const text = code.children.map((child) => (child.type === 'text' ? child.value : '')).join('');
  if (language === 'outcome') return <OutcomeCard raw={text} />;
  if (language === 'mermaid') return <MermaidDiagram source={text} isStreaming={streaming} />;
  return <MarkdownCodeBlock language={language} code={text} />;
}

function childText(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(childText).join('');
  if (node && typeof node === 'object' && 'props' in node)
    return childText((node.props as { children?: ReactNode }).children);
  return '';
}
function headingId(children: ReactNode) {
  return childText(children)
    .toLowerCase()
    .trim()
    .replace(/[^\p{L}\p{N}\s_-]/gu, '')
    .replace(/\s+/g, '-');
}

// Stable component identities keep disclosure state, selection and scroll anchors while streaming.
const components: Components = {
  a: ({ href, children }) => <ConversationLink href={href ?? ''}>{children}</ConversationLink>,
  img: ({ src, alt }) => (
    <ConversationImage href={typeof src === 'string' ? src : ''} alt={alt ?? ''} />
  ),
  pre: CodeFence,
  code: ({ children }) => <code className="niuu-chat-md-inline-code">{children}</code>,
  p: ({ children }) => <p className="niuu-chat-md-p">{children}</p>,
  h1: ({ children }) => (
    <h1 id={headingId(children)} className="niuu-chat-md-h1">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 id={headingId(children)} className="niuu-chat-md-h2">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 id={headingId(children)} className="niuu-chat-md-h3">
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 id={headingId(children)} className="niuu-chat-md-h4">
      {children}
    </h4>
  ),
  h5: ({ children }) => (
    <h5 id={headingId(children)} className="niuu-chat-md-h5">
      {children}
    </h5>
  ),
  h6: ({ children }) => (
    <h6 id={headingId(children)} className="niuu-chat-md-h6">
      {children}
    </h6>
  ),
  ul: ({ children, className }) => (
    <ul className={`niuu-chat-md-ul ${className ?? ''}`}>{children}</ul>
  ),
  ol: ({ children, start }) => (
    <ol className="niuu-chat-md-ol" start={start}>
      {children}
    </ol>
  ),
  blockquote: ({ children }) => (
    <blockquote className="niuu-chat-md-blockquote">{children}</blockquote>
  ),
  table: ({ children }) => (
    <div className="niuu-chat-md-table-wrap">
      <table className="niuu-chat-md-table">{children}</table>
    </div>
  ),
  th: ({ children, style }) => (
    <th className="niuu-chat-md-table-head" style={style}>
      {children}
    </th>
  ),
  td: ({ children, style }) => (
    <td className="niuu-chat-md-table-cell" style={style}>
      {children}
    </td>
  ),
};
const inlineComponents: Components = { ...components, p: ({ children }) => <>{children}</> };

interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  data?: Record<string, unknown>;
}
/** Lexi/GitHub callouts retain nested Markdown and expose their tone without raw HTML. */
function remarkCallouts() {
  return (tree: unknown) => {
    function walk(node: MdNode) {
      if (node.type === 'blockquote') {
        const first = node.children?.[0]?.children?.[0];
        const match = first?.value?.match(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\](?:\s|$)/);
        if (match && first) {
          first.value = first.value!.slice(match[0].length);
          node.data = {
            hProperties: {
              className: `niuu-chat-md-callout niuu-chat-md-callout--${match[1]!.toLowerCase()}`,
              'data-callout': match[1],
            },
          };
        }
      }
      node.children?.forEach(walk);
    }
    walk(tree as MdNode);
  };
}
const allRemarks = [remarkGfm, remarkMath, remarkLinkifyPaths, remarkCallouts, remarkBreaks];
components.blockquote = ({ children, node }) => (
  <blockquote
    className={`niuu-chat-md-blockquote ${String(node?.properties.className ?? '')}`}
    data-callout={node?.properties['data-callout']}
  >
    {children}
  </blockquote>
);

/** CommonMark/GFM renderer adapted from Lexi's React renderer, styled with Niuu tokens. */
export const LexiMarkdown = memo(function LexiMarkdown({
  content,
  isStreaming = false,
  inline = false,
}: {
  content: string;
  isStreaming?: boolean;
  inline?: boolean;
}) {
  const source = isStreaming ? preserveArrivingFence(content) : content;
  return (
    <Streaming.Provider value={isStreaming}>
      <ReactMarkdown
        remarkPlugins={allRemarks}
        rehypePlugins={rehypes}
        components={inline ? inlineComponents : components}
        urlTransform={(url) => (/^file:\/\//i.test(url) ? url : defaultUrlTransform(url))}
        skipHtml
      >
        {source}
      </ReactMarkdown>
    </Streaming.Provider>
  );
});

function preserveArrivingFence(content: string): string {
  const lines = content.split('\n');
  let open: string | undefined;
  for (let i = 0; i < lines.length; i++) {
    const match = lines[i]!.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (!match) continue;
    const marker = match[1]!;
    if (open) {
      if (marker[0] === open[0] && marker.length >= open.length && !match[2]!.trim())
        open = undefined;
    } else if (i === lines.length - 1) {
      lines[i] = lines[i]!.replace(/[`~]/g, (c) => `\\${c}`);
    } else open = marker;
  }
  return lines.join('\n');
}
