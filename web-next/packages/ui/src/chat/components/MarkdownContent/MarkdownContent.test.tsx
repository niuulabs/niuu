import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MarkdownContent } from './MarkdownContent';

// Stub clipboard
Object.assign(navigator, {
  clipboard: { writeText: () => Promise.resolve() },
});

describe('MarkdownContent', () => {
  it('renders plain text', () => {
    render(<MarkdownContent content="Hello world" />);
    expect(screen.getByText('Hello world')).toBeInTheDocument();
  });

  it('renders code block', () => {
    render(<MarkdownContent content={'```js\nconst x = 1\n```'} />);
    expect(screen.getByTestId('code-block')).toBeInTheDocument();
    expect(screen.getByText('js')).toBeInTheDocument();
  });

  it('shows copy button in code block and copies on click', async () => {
    render(<MarkdownContent content={'```\nsome code\n```'} />);
    const copyBtn = screen.getAllByRole('button').find((b) => b.title === 'Copy');
    expect(copyBtn).toBeDefined();
    fireEvent.click(copyBtn!);
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.getByTitle('Copied!')).toBeInTheDocument();
  });

  it('collapses code block on click', () => {
    render(<MarkdownContent content={'```js\ncode\n```'} />);
    const collapseBtn = screen.getAllByRole('button').find((b) => b.title === 'Collapse');
    fireEvent.click(collapseBtn!);
    expect(screen.queryByText('code')).not.toBeInTheDocument();
  });

  it('renders outcome card for outcome block', () => {
    render(<MarkdownContent content={'```outcome\nverdict: pass\n```'} />);
    expect(screen.getByTestId('outcome-card')).toBeInTheDocument();
  });

  it('renders headings', () => {
    render(<MarkdownContent content={'# H1\n## H2'} />);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('H1');
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('H2');
  });

  it('shows streaming cursor when isStreaming=true', () => {
    const { container } = render(<MarkdownContent content="thinking..." isStreaming />);
    expect(container.querySelector('.niuu-chat-md-cursor')).toBeInTheDocument();
  });

  it('renders unordered list', () => {
    render(<MarkdownContent content={'- item one\n- item two\n- item three'} />);
    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(3);
  });

  it('renders ordered list', () => {
    render(<MarkdownContent content={'1. first\n2. second'} />);
    expect(screen.getByRole('list')).toBeInTheDocument();
    expect(screen.getByText('first')).toBeInTheDocument();
  });

  it('renders bold text', () => {
    render(<MarkdownContent content="Hello **world**" />);
    expect(screen.getByText('world').tagName).toBe('STRONG');
  });

  it('renders inline code', () => {
    render(<MarkdownContent content="Use `const x = 1`" />);
    expect(screen.getByText('const x = 1').tagName).toBe('CODE');
  });

  it('renders link', () => {
    render(<MarkdownContent content="See [docs](https://example.com)" />);
    const link = screen.getByRole('link', { name: 'docs' });
    expect(link).toHaveAttribute('href', 'https://example.com');
  });

  it('renders outcome card embedded in text', () => {
    const content = 'Before\n```outcome\nverdict: pass\n```\nAfter';
    render(<MarkdownContent content={content} />);
    expect(screen.getByTestId('outcome-card')).toBeInTheDocument();
  });

  it('renders outcome card for dashed outcome markers', () => {
    render(<MarkdownContent content={'Before\n---outcome---\nverdict: pass\n---end---\nAfter'} />);
    expect(screen.getByTestId('outcome-card')).toBeInTheDocument();
    expect(screen.getByText('Before')).toBeInTheDocument();
    expect(screen.getByText('After')).toBeInTheDocument();
  });

  it('renders outcome card for dashed outcome markers closed by bare dashes', () => {
    render(<MarkdownContent content={'Before\n---outcome---\nverdict: pass\n---\nAfter'} />);
    expect(screen.getByTestId('outcome-card')).toBeInTheDocument();
    expect(screen.getByText('Before')).toBeInTheDocument();
    expect(screen.getByText('After')).toBeInTheDocument();
  });

  it('enables word wrap on word-wrap button click', () => {
    const { container } = render(<MarkdownContent content={'```js\nconst x = 1\n```'} />);
    const wrapBtn = screen.getAllByRole('button').find((b) => b.title === 'Enable word wrap');
    fireEvent.click(wrapBtn!);
    expect(container.querySelector('.niuu-chat-md-codeblock-pre--wrap')).toBeInTheDocument();
  });

  it('shows h3-h6 headings', () => {
    render(<MarkdownContent content={'### H3\n#### H4\n##### H5\n###### H6'} />);
    expect(screen.getByRole('heading', { level: 3 })).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 6 })).toBeInTheDocument();
  });

  it('skips empty lines gracefully', () => {
    render(<MarkdownContent content={'Line one\n\nLine two'} />);
    expect(screen.getByText('Line one')).toBeInTheDocument();
    expect(screen.getByText('Line two')).toBeInTheDocument();
  });

  it('renders blockquote', () => {
    render(<MarkdownContent content={'> This is a quote'} />);
    expect(screen.getByRole('blockquote')).toBeInTheDocument();
  });

  it('renders markdown table content', () => {
    render(
      <MarkdownContent
        content={
          '| Peer | Status |\n|------|--------|\n| **Skuld** | idle |\n| `coder` | blocked |'
        }
      />,
    );

    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Peer' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Status' })).toBeInTheDocument();
    expect(screen.getByText('Skuld').tagName).toBe('STRONG');
    expect(screen.getByText('coder').tagName).toBe('CODE');
    expect(screen.getByText('blocked')).toBeInTheDocument();
  });
});
