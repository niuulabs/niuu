import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MarkdownContent } from './MarkdownContent';

describe('streaming Markdown block boundaries', () => {
  it('renders preceding prose once while an unfinished fence grows and closes', () => {
    const { container, rerender } = render(
      <MarkdownContent content={'Intro café 東京\n\n```swift\nlet a = 1\n\n'} isStreaming />,
    );
    expect(container.textContent?.match(/Intro café 東京/g)).toHaveLength(1);
    const code = container.querySelector('pre code');
    expect(code?.textContent).toBe('let a = 1\n\n');
    rerender(
      <MarkdownContent
        content={'Intro café 東京\n\n```swift\nlet a = 1\n\nlet b = 2\n```\n\nDone'}
      />,
    );
    expect(container.querySelector('pre code')).toBe(code);
    expect(code?.textContent).toBe('let a = 1\n\nlet b = 2\n');
    expect(container.textContent?.match(/Intro café 東京/g)).toHaveLength(1);
  });
  it('keeps embedded shorter fences literal and supports tilde fences', () => {
    const { container } = render(
      <MarkdownContent content={'~~~~text\none\n```swift\ntwo\n```\n~~~~\nAfter'} />,
    );
    expect(container.querySelectorAll('pre')).toHaveLength(1);
    expect(container.querySelector('code')?.textContent).toBe('one\n```swift\ntwo\n```\n');
    expect(container.textContent).toContain('After');
  });
  it('does not hide ticks or duplicate prose while a fence header is unfinished', () => {
    const { container } = render(<MarkdownContent content={'Intro\n\n```'} isStreaming />);
    expect(container.textContent?.match(/Intro/g)).toHaveLength(1);
    expect(container.textContent).toContain('```');
  });
  it('keeps loose lists and their DOM anchors intact as another item arrives', () => {
    const { container, rerender } = render(
      <MarkdownContent content={'Intro\n\n1. First\n\n2. Second'} isStreaming />,
    );
    const list = container.querySelector('ol');
    expect(container.querySelectorAll('ol')).toHaveLength(1);
    expect(list?.querySelectorAll('li')).toHaveLength(2);
    rerender(<MarkdownContent content={'Intro\n\n1. First\n\n2. Second\n\n3. Third'} />);
    expect(container.querySelector('ol')).toBe(list);
    expect(list?.querySelectorAll('li')).toHaveLength(3);
  });
  it('preserves tables, bold text, links and Unicode through live to settled rendering', () => {
    const markdown =
      '**Summary**\n\n| Name | Value |\n| --- | --- |\n| café | 東京 |\n\n[Guide](https://example.invalid)';
    const { container, rerender } = render(<MarkdownContent content={markdown} isStreaming />);
    const table = container.querySelector('table');
    expect(table?.textContent).toContain('café東京');
    expect(container.querySelector('strong')?.textContent).toBe('Summary');
    expect(container.querySelector('a')?.getAttribute('href')).toBe('https://example.invalid');
    rerender(<MarkdownContent content={markdown} />);
    expect(container.querySelector('table')).toBe(table);
  });
});
