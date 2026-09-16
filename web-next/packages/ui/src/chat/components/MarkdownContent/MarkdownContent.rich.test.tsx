import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MarkdownContent } from './MarkdownContent';
import { ConversationResourceProvider } from '../ConversationResources';

describe('Lexi Markdown in Forge messages', () => {
  it('preserves nested lists, loose paragraphs, emphasis, tasks and code inside a list', () => {
    const { container } = render(
      <MarkdownContent
        content={
          '3. **Parent** with *emphasis* and ~~old text~~\n\n   - Child\n   - [x] Finished\n   - [ ] Pending\n\n   ```ts\n   const result = 42;\n   ```\n\n4. Continue'
        }
      />,
    );
    expect(container.querySelector('ol')).toHaveAttribute('start', '3');
    expect(container.querySelector('ol > li > ul')).toBeInTheDocument();
    expect(container.querySelector('ol > li pre code')).toHaveTextContent('const result = 42;');
    expect(container.querySelector('em')).toHaveTextContent('emphasis');
    expect(container.querySelector('del')).toHaveTextContent('old text');
    expect(screen.getAllByRole('checkbox')).toHaveLength(2);
    expect(screen.getAllByRole('checkbox')[0]).toBeChecked();
    expect(screen.getAllByRole('checkbox')[1]).toBeDisabled();
  });
  it('renders callouts with nested formatting, reference links, aligned tables and math', () => {
    const { container } = render(
      <MarkdownContent
        content={
          '> [!WARNING]\n> Review **carefully**.\n\n| Item | Cost |\n| :--- | ---: |\n| [Docs][guide] | $5 |\n\n[guide]: https://example.com/docs\n\n$$\nx^2 + y^2 = z^2\n$$'
        }
      />,
    );
    expect(container.querySelector('[data-callout="WARNING"]')).toHaveTextContent(
      'Review carefully.',
    );
    expect(screen.getByRole('link', { name: 'Docs' })).toHaveAttribute(
      'href',
      'https://example.com/docs',
    );
    expect(screen.getByRole('columnheader', { name: 'Cost' })).toHaveStyle({ textAlign: 'right' });
    expect(container.querySelector('math')?.namespaceURI).toBe(
      'http://www.w3.org/1998/Math/MathML',
    );
  });
  it('opens bare, backticked and file-scheme paths through the owning session without linking framework names', () => {
    const open = vi.fn();
    const port = {
      open,
      load: vi.fn(),
      resolve: (path: string) => ({ kind: 'workspace' as const, path, name: path }),
    };
    render(
      <ConversationResourceProvider port={port}>
        <MarkdownContent
          content={
            'See src/App.tsx:12, `README.md`, and [Config](file:///workspace/config.json). Node.js and React.js are frameworks.\n\n```text\nREADME.md\n```'
          }
        />
      </ConversationResourceProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'src/App.tsx:12' }));
    expect(open).toHaveBeenLastCalledWith(expect.objectContaining({ path: 'src/App.tsx:12' }));
    fireEvent.click(screen.getByRole('button', { name: 'Config' }));
    expect(open).toHaveBeenLastCalledWith(
      expect.objectContaining({ path: 'file:///workspace/config.json' }),
    );
    expect(screen.queryByRole('button', { name: 'Node.js' })).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'README.md' }).querySelector('code'),
    ).toBeInTheDocument();
  });
  it('does not execute raw HTML or javascript links, including nested formatting', () => {
    const { container } = render(
      <MarkdownContent
        content={
          '<script>alert(1)</script>\n\n[Bad](javascript:alert%281%29) **safe _nested_**\n\n<img src=x onerror="alert(1)">'
        }
      />,
    );
    expect(container.querySelector('script, img, a[href^="javascript:"]')).toBeNull();
    expect(container.querySelector('strong em')).toHaveTextContent('nested');
  });
  it('preserves a complete closing fence during streaming and literal outcome examples', () => {
    const { container, rerender } = render(
      <MarkdownContent
        isStreaming
        content={'```text\n---outcome---\nverdict: pass\n---end---\n```'}
      />,
    );
    const code = container.querySelector('pre code');
    expect(code?.textContent).toBe('---outcome---\nverdict: pass\n---end---\n');
    expect(screen.queryByTestId('outcome-card')).not.toBeInTheDocument();
    rerender(<MarkdownContent content={'```text\n---outcome---\nverdict: pass\n---end---\n```'} />);
    expect(container.querySelector('pre code')).toBe(code);
  });
  it('highlights settled code without replacing its scroll/selection container', async () => {
    const { container } = render(
      <MarkdownContent content={'```typescript\nconst answer: number = 42;\n```'} />,
    );
    const code = container.querySelector('pre code');
    await waitFor(() => expect(code?.querySelector('span[style]')).toBeInTheDocument(), {
      timeout: 8000,
    });
    expect(container.querySelector('pre code')).toBe(code);
    expect(code?.textContent).toBe('const answer: number = 42;\n');
  });
});
