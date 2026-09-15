import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { RepoRecord } from '@niuulabs/ui';
import {
  WorkflowLaunchForm,
  useWorkflowLaunchDraft,
  workflowLaunchRequest,
  type WorkflowLaunchDraft,
} from './WorkflowLaunchForm';

const repos: RepoRecord[] = [
  {
    provider: 'github',
    org: 'niuulabs',
    name: 'volundr',
    url: 'https://github.com/niuulabs/volundr',
    cloneUrl: 'https://github.com/niuulabs/volundr.git',
    defaultBranch: 'main',
    branches: ['main', 'feat/x'],
  },
];

function Harness({
  workflowKey = 'wf-1',
  initial,
  onSubmit,
  showSessionName = true,
  withRepos = true,
  loadBranches,
}: {
  workflowKey?: string;
  initial?: Partial<WorkflowLaunchDraft>;
  onSubmit?: () => void;
  showSessionName?: boolean;
  withRepos?: boolean;
  loadBranches?: (repoUrl: string) => Promise<string[]>;
}) {
  const [key, setKey] = useState(workflowKey);
  const draft = useWorkflowLaunchDraft(key, initial);
  return (
    <div>
      <WorkflowLaunchForm
        values={draft.values}
        onChange={draft.update}
        repos={withRepos ? repos : []}
        loadBranches={loadBranches}
        showSessionName={showSessionName}
        onPromptSubmit={onSubmit}
      />
      <button type="button" onClick={() => setKey('wf-2')}>
        switch workflow
      </button>
      <button type="button" onClick={draft.reset}>
        reset
      </button>
      <output data-testid="request">{JSON.stringify(workflowLaunchRequest(draft.values))}</output>
    </div>
  );
}

describe('useWorkflowLaunchDraft', () => {
  it('starts from the initial values and drops blanks out of the request', () => {
    render(<Harness initial={{ repo: repos[0]!.cloneUrl, branch: 'feat/x' }} />);
    expect(screen.getByTestId('workflow-launch-repo-select')).toHaveValue(repos[0]!.cloneUrl);
    expect(screen.getByTestId('workflow-launch-branch-select')).toHaveValue('feat/x');
    expect(screen.getByTestId('request')).toHaveTextContent(
      `{"prompt":"","repo":"${repos[0]!.cloneUrl}","branch":"feat/x"}`,
    );
  });

  it('forgets the draft when the workflow changes', () => {
    render(<Harness />);
    fireEvent.change(screen.getByTestId('workflow-launch-prompt'), { target: { value: 'do it' } });
    expect(screen.getByTestId('workflow-launch-prompt')).toHaveValue('do it');

    fireEvent.click(screen.getByRole('button', { name: 'switch workflow' }));
    expect(screen.getByTestId('workflow-launch-prompt')).toHaveValue('');
  });

  it('resets on demand', () => {
    render(<Harness />);
    fireEvent.change(screen.getByTestId('workflow-launch-prompt'), { target: { value: 'do it' } });
    fireEvent.click(screen.getByRole('button', { name: 'reset' }));
    expect(screen.getByTestId('workflow-launch-prompt')).toHaveValue('');
  });
});

describe('WorkflowLaunchForm', () => {
  it('loads branches only for the selected repository', async () => {
    const loadBranches = vi.fn().mockResolvedValue(['main', 'release']);
    render(<Harness loadBranches={loadBranches} />);
    expect(loadBranches).not.toHaveBeenCalled();

    fireEvent.change(screen.getByTestId('workflow-launch-repo-select'), {
      target: { value: repos[0]!.cloneUrl },
    });

    expect(await screen.findByRole('option', { name: 'release' })).toBeInTheDocument();
    expect(loadBranches).toHaveBeenCalledExactlyOnceWith(repos[0]!.cloneUrl);
  });

  it('submits on ⌘/Ctrl+Enter and ignores other keys', () => {
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);
    const prompt = screen.getByTestId('workflow-launch-prompt');

    fireEvent.keyDown(prompt, { key: 'Enter' });
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.keyDown(prompt, { key: 'a', metaKey: true });
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.keyDown(prompt, { key: 'Enter', metaKey: true });
    expect(onSubmit).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(prompt, { key: 'Enter', ctrlKey: true });
    expect(onSubmit).toHaveBeenCalledTimes(2);
  });

  it('does nothing on ⌘+Enter without a submit handler', () => {
    render(<Harness />);
    fireEvent.keyDown(screen.getByTestId('workflow-launch-prompt'), {
      key: 'Enter',
      metaKey: true,
    });
    expect(screen.getByTestId('workflow-launch-prompt')).toBeInTheDocument();
  });

  it('hides the session-name field when the surface does not want it', () => {
    render(<Harness showSessionName={false} />);
    expect(screen.queryByTestId('workflow-launch-session-name')).not.toBeInTheDocument();
  });

  it('falls back to plain inputs without a repo catalog', () => {
    render(<Harness withRepos={false} />);
    fireEvent.change(screen.getByTestId('workflow-launch-repo'), {
      target: { value: ' org/repo ' },
    });
    fireEvent.change(screen.getByTestId('workflow-launch-branch'), {
      target: { value: ' dev ' },
    });
    expect(screen.getByTestId('request')).toHaveTextContent(
      '{"prompt":"","repo":"org/repo","branch":"dev"}',
    );
  });

  it('takes the default branch with the repo and keeps the session name', () => {
    render(<Harness />);
    fireEvent.change(screen.getByTestId('workflow-launch-session-name'), {
      target: { value: 'run-1' },
    });
    fireEvent.change(screen.getByTestId('workflow-launch-repo-select'), {
      target: { value: repos[0]!.cloneUrl },
    });
    expect(screen.getByTestId('workflow-launch-branch-select')).toHaveValue('main');
    expect(screen.getByTestId('request')).toHaveTextContent('"sessionName":"run-1"');
  });

  it('shows the error the surface put on the draft', () => {
    render(<Harness initial={{ error: 'cluster is full' }} />);
    expect(screen.getByRole('alert')).toHaveTextContent('cluster is full');
  });
});
