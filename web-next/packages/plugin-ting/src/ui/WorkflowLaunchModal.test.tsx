import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { RepoRecord } from '@niuulabs/ui';
import { describe, expect, it, vi } from 'vitest';
import type { Workflow } from '../domain/workflow';
import { WorkflowLaunchModal } from './WorkflowLaunchModal';

const workflow: Workflow = {
  id: '00000000-0000-0000-0000-000000000001',
  name: 'Research Campaign',
  nodes: [],
  edges: [],
};

const repos: RepoRecord[] = [
  {
    provider: 'github',
    org: 'niuulabs',
    name: 'volundr',
    url: 'https://github.com/niuulabs/volundr',
    cloneUrl: 'https://github.com/niuulabs/volundr.git',
    defaultBranch: 'main',
    branches: ['main', 'feat/research'],
  },
];

describe('WorkflowLaunchModal', () => {
  it('disables launch until a prompt is provided', () => {
    render(
      <WorkflowLaunchModal open onOpenChange={vi.fn()} workflow={workflow} onLaunch={vi.fn()} />,
    );

    expect(screen.getByRole('button', { name: 'Launch' })).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText('Describe what this workflow should do.'), {
      target: { value: 'Investigate this topic deeply.' },
    });
    expect(screen.getByRole('button', { name: 'Launch' })).toBeEnabled();
  });

  it('submits prompt, name, repo, and branch from the repo pickers', async () => {
    const onLaunch = vi.fn().mockResolvedValue(undefined);
    render(
      <WorkflowLaunchModal
        open
        onOpenChange={vi.fn()}
        workflow={workflow}
        repos={repos}
        onLaunch={onLaunch}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText('Describe what this workflow should do.'), {
      target: { value: '  Investigate this topic deeply.  ' },
    });
    fireEvent.change(screen.getByPlaceholderText('Optional override'), {
      target: { value: '  research-run  ' },
    });
    fireEvent.change(screen.getByTestId('workflow-launch-repo-select'), {
      target: { value: 'https://github.com/niuulabs/volundr.git' },
    });
    fireEvent.change(screen.getByTestId('workflow-launch-branch-select'), {
      target: { value: 'feat/research' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Launch' }));

    await waitFor(() =>
      expect(onLaunch).toHaveBeenCalledWith({
        prompt: 'Investigate this topic deeply.',
        sessionName: 'research-run',
        repo: 'https://github.com/niuulabs/volundr.git',
        branch: 'feat/research',
      }),
    );
  });

  it('falls back to manual repo and branch entry without catalog data', async () => {
    const onLaunch = vi.fn().mockResolvedValue(undefined);
    render(
      <WorkflowLaunchModal open onOpenChange={vi.fn()} workflow={workflow} onLaunch={onLaunch} />,
    );

    fireEvent.change(screen.getByPlaceholderText('Describe what this workflow should do.'), {
      target: { value: 'Investigate this topic deeply.' },
    });
    fireEvent.change(screen.getByPlaceholderText('Optional repo or org/repo'), {
      target: { value: '  niuulabs/volundr  ' },
    });
    fireEvent.change(screen.getByPlaceholderText('Optional branch'), {
      target: { value: '  feature/research  ' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Launch' }));

    await waitFor(() =>
      expect(onLaunch).toHaveBeenCalledWith({
        prompt: 'Investigate this topic deeply.',
        repo: 'niuulabs/volundr',
        branch: 'feature/research',
      }),
    );
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
