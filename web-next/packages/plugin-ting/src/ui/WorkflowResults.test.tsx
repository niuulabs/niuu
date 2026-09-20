import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WorkflowResults } from './WorkflowResults';

afterEach(() => vi.restoreAllMocks());

describe('WorkflowResults', () => {
  it('renders accessible Markdown results and generic context', () => {
    render(
      <WorkflowResults
        title="Release results"
        status="Completed"
        context={[
          { label: 'Workflow', value: 'release' },
          { label: 'Revision', value: 'plan-2' },
        ]}
        markdown={
          '## Checks\n\n| Contract | Status |\n| --- | --- |\n| unit | passed |\n\n[Runbook](https://example.com/runbook)\n\n```text\nready\n```'
        }
      />,
    );

    expect(screen.getByRole('region', { name: 'Release results' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Release results', level: 2 })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Checks' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Runbook' })).toHaveClass('niuu-chat-md-link');
    expect(screen.getByText('plan-2')).toBeInTheDocument();
    expect(screen.getByText('ready')).toBeInTheDocument();
  });

  it('copies the exact Markdown and announces success', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    render(<WorkflowResults title="Results" status="Running" markdown="# Exact report" />);

    fireEvent.click(screen.getByRole('button', { name: 'Copy Markdown' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('# Exact report'));
    expect(screen.getByRole('status')).toHaveTextContent('Markdown copied');
  });

  it('announces clipboard failure', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined });
    render(<WorkflowResults title="Results" status="Failed" markdown="# Report" />);

    fireEvent.click(screen.getByRole('button', { name: 'Copy Markdown' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Unable to copy Markdown');
  });

  it('downloads the exact Markdown with a stable filename', () => {
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:report');
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined);
    render(<WorkflowResults title="Release Results" status="Completed" markdown="# Report" />);

    fireEvent.click(screen.getByRole('button', { name: 'Download Markdown' }));

    expect(createObjectURL).toHaveBeenCalledOnce();
    const blob = createObjectURL.mock.calls[0]![0] as Blob;
    expect(blob.type).toBe('text/markdown;charset=utf-8');
    expect(click).toHaveBeenCalledOnce();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:report');
  });
});
