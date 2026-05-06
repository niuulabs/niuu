import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RenderedContent } from './RenderedContent';

Object.assign(navigator, { clipboard: { writeText: () => Promise.resolve() } });

describe('RenderedContent', () => {
  it('renders plain text', () => {
    render(<RenderedContent content="Hello world" />);
    expect(screen.getByText('Hello world')).toBeInTheDocument();
  });

  it('renders code blocks', () => {
    render(<RenderedContent content={'```js\nconsole.log(1)\n```'} />);
    expect(screen.getByTestId('rendered-code-block')).toBeInTheDocument();
  });

  it('renders outcome card', () => {
    render(<RenderedContent content={'```outcome\nverdict: pass\n```'} />);
    expect(screen.getByTestId('outcome-card')).toBeInTheDocument();
  });

  it('renders outcome block via XML-style tag', () => {
    render(<RenderedContent content={'<outcome>verdict: pass</outcome>'} />);
    expect(screen.getByTestId('outcome-card')).toBeInTheDocument();
  });

  it('copies code on copy button click', async () => {
    render(<RenderedContent content={'```js\nconsole.log(1)\n```'} />);
    const copyBtn = screen.getByRole('button');
    fireEvent.click(copyBtn);
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.getByTitle('Copied!')).toBeInTheDocument();
  });

  it('renders with className applied', () => {
    const { container } = render(<RenderedContent content="Hello" className="custom-class" />);
    expect(container.firstChild).toHaveClass('custom-class');
  });
});
