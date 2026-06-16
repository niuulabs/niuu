import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HighlightedCode } from './HighlightedCode';

vi.mock('shiki', () => ({
  codeToHtml: vi
    .fn()
    .mockResolvedValue(
      '<pre class="shiki"><code data-testid="shiki-output">def run()</code></pre>',
    ),
}));

describe('HighlightedCode', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders plain code first, then the highlighted output', async () => {
    const code = 'def run():\n    return {}';
    render(<HighlightedCode code={code} lang="python" testId="code" />);
    // Plain fallback shows immediately while shiki resolves.
    expect(screen.getByTestId('code-plain')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('code-highlighted')).toBeInTheDocument());
    const { codeToHtml } = await import('shiki');
    expect(codeToHtml).toHaveBeenCalledWith(code, expect.objectContaining({ lang: 'python' }));
  });

  it('copies the code to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<HighlightedCode code="print('hi')" lang="python" testId="code" />);

    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    expect(writeText).toHaveBeenCalledWith("print('hi')");
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument();
  });

  it('falls back to plain text when highlighting fails', async () => {
    const { codeToHtml } = await import('shiki');
    vi.mocked(codeToHtml).mockRejectedValueOnce(new Error('no grammar'));
    render(<HighlightedCode code="weird()" lang="nope" testId="code" />);
    await waitFor(() => expect(screen.getByTestId('code-plain')).toHaveTextContent('weird()'));
    expect(screen.queryByTestId('code-highlighted')).not.toBeInTheDocument();
  });
});
