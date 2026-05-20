import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { WorkflowLaunchModal } from './WorkflowLaunchModal';
import type { Workflow } from '../domain/workflow';

const workflow: Workflow = {
  id: '00000000-0000-0000-0000-000000000001',
  name: 'Research Campaign',
  nodes: [],
  edges: [],
};

describe('WorkflowLaunchModal', () => {
  it('disables launch until a prompt is provided', () => {
    render(
      <WorkflowLaunchModal
        open
        onOpenChange={vi.fn()}
        workflow={workflow}
        onLaunch={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Launch' })).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText('Describe what this workflow should do.'), {
      target: { value: 'Investigate this topic deeply.' },
    });
    expect(screen.getByRole('button', { name: 'Launch' })).toBeEnabled();
  });

  it('submits trimmed fields and parsed structured context', async () => {
    const onLaunch = vi.fn().mockResolvedValue(undefined);
    render(
      <WorkflowLaunchModal open onOpenChange={vi.fn()} workflow={workflow} onLaunch={onLaunch} />,
    );

    fireEvent.change(screen.getByPlaceholderText('Describe what this workflow should do.'), {
      target: { value: '  Investigate this topic deeply.  ' },
    });
    fireEvent.change(screen.getByPlaceholderText('Optional override'), {
      target: { value: '  research-run  ' },
    });
    fireEvent.change(screen.getByPlaceholderText('Optional model override'), {
      target: { value: '  gpt-5.5  ' },
    });
    fireEvent.change(screen.getByPlaceholderText('Optional repo or org/repo'), {
      target: { value: '  niuulabs/volundr  ' },
    });
    fireEvent.change(screen.getByPlaceholderText('main'), {
      target: { value: '  feature/research  ' },
    });
    fireEvent.change(screen.getByPlaceholderText('Optional resource path override'), {
      target: { value: '  /tmp/mimir  ' },
    });
    fireEvent.change(screen.getByLabelText('Structured context'), {
      target: { value: '{"mode":"exploratory","seed_urls":["https://example.com"]}' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Launch' }));

    await waitFor(() =>
      expect(onLaunch).toHaveBeenCalledWith({
        prompt: 'Investigate this topic deeply.',
        sessionName: 'research-run',
        repo: 'niuulabs/volundr',
        branch: 'feature/research',
        model: 'gpt-5.5',
        mimirPath: '/tmp/mimir',
        context: {
          mode: 'exploratory',
          seed_urls: ['https://example.com'],
        },
      }),
    );
  });

  it('rejects invalid JSON context', async () => {
    const onLaunch = vi.fn();
    render(
      <WorkflowLaunchModal open onOpenChange={vi.fn()} workflow={workflow} onLaunch={onLaunch} />,
    );

    fireEvent.change(screen.getByPlaceholderText('Describe what this workflow should do.'), {
      target: { value: 'Investigate this topic deeply.' },
    });
    fireEvent.change(screen.getByLabelText('Structured context'), {
      target: { value: '{"mode":' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Launch' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Context must be valid JSON.');
    expect(onLaunch).not.toHaveBeenCalled();
  });

  it('rejects non-object context payloads', async () => {
    const onLaunch = vi.fn();
    render(
      <WorkflowLaunchModal open onOpenChange={vi.fn()} workflow={workflow} onLaunch={onLaunch} />,
    );

    fireEvent.change(screen.getByPlaceholderText('Describe what this workflow should do.'), {
      target: { value: 'Investigate this topic deeply.' },
    });
    fireEvent.change(screen.getByLabelText('Structured context'), {
      target: { value: '["not","an","object"]' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Launch' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Context must be a JSON object.',
    );
    expect(onLaunch).not.toHaveBeenCalled();
  });

  it('surfaces launch errors', async () => {
    const onLaunch = vi.fn().mockRejectedValue('boom');
    render(
      <WorkflowLaunchModal open onOpenChange={vi.fn()} workflow={workflow} onLaunch={onLaunch} />,
    );

    fireEvent.change(screen.getByPlaceholderText('Describe what this workflow should do.'), {
      target: { value: 'Investigate this topic deeply.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Launch' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Launch failed.');
  });

  it('surfaces launch Error messages verbatim', async () => {
    const onLaunch = vi.fn().mockRejectedValue(new Error('backend exploded'));
    render(
      <WorkflowLaunchModal open onOpenChange={vi.fn()} workflow={workflow} onLaunch={onLaunch} />,
    );

    fireEvent.change(screen.getByPlaceholderText('Describe what this workflow should do.'), {
      target: { value: 'Investigate this topic deeply.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Launch' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('backend exploded');
  });

  it('renders the generic title and launching state when no workflow is selected', () => {
    render(
      <WorkflowLaunchModal
        open
        onOpenChange={vi.fn()}
        workflow={null}
        launching
        onLaunch={vi.fn()}
      />,
    );

    expect(screen.getByText('Launch workflow')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Launching…' })).toBeDisabled();
  });
});
