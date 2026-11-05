import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { SubagentCard, subagentStatus, teammateSpawn } from './SubagentCard';
import { ProviderErrorCard } from './ProviderErrorCard';
import { CompactionMarker, compactionTokens } from './CompactionMarker';
import { formatTokenCount } from './formatTokenCount';
import type { ToolResultBlock, ToolUseBlock } from '../ToolBlock/groupContentBlocks';

const task: ToolUseBlock = {
  type: 'tool_use',
  id: 'task-1',
  name: 'Task',
  input: {
    description: 'Check the other gate entry points',
    subagent_type: 'Explore',
    prompt: 'Look at every writer of gate decisions.',
  },
};
const result = (content: unknown, is_error = false): ToolResultBlock => ({
  type: 'tool_result',
  tool_use_id: 'task-1',
  content,
  ...(is_error ? { is_error } : {}),
});

describe('SubagentCard', () => {
  it('shows the kicker, description, subagent type and a running pill while it has no result', () => {
    render(<SubagentCard block={task} />);
    const card = screen.getByTestId('subagent-card');
    expect(card).toHaveTextContent('Subagent');
    expect(card).toHaveTextContent('Check the other gate entry points');
    expect(card).toHaveTextContent('Explore');
    expect(screen.getByTestId('subagent-status')).toHaveTextContent('Running');
    expect(screen.queryByTestId('subagent-result')).toBeNull();
  });

  it('shows a done pill and the first lines of the result, saying what it cut', () => {
    render(
      <SubagentCard block={task} result={result('line 1\n\nline 2\nline 3\nline 4\nline 5')} />,
    );
    expect(screen.getByTestId('subagent-status')).toHaveTextContent('Done');
    const text = screen.getByTestId('subagent-result').textContent;
    expect(text).toContain('line 1\nline 2\nline 3');
    expect(text).not.toContain('line 4');
    expect(text).toContain('… 2 more lines');
  });

  it('does not mark a short result as cut', () => {
    render(<SubagentCard block={task} result={result([{ type: 'text', text: 'only' }])} />);
    expect(screen.getByTestId('subagent-result')).toHaveTextContent(/^only$/);
  });

  it('says a teammate started instead of dumping its spawn record', () => {
    const spawn = JSON.stringify({
      agentId: 'tsx-counter@session-c3a8ba72',
      agent_id: 'tsx-counter@session-c3a8ba72',
      agent_type: 'general-purpose',
      is_splitpane: true,
      model: 'claude-fable-5-1',
      name: 'tsx-counter',
      prompt: 'Read-only task, change no files.',
    });
    render(<SubagentCard block={task} result={result(spawn)} />);
    expect(screen.getByTestId('subagent-status')).toHaveTextContent('Started');
    expect(screen.getByTestId('subagent-card')).toHaveAttribute('data-status', 'started');
    expect(screen.getByTestId('subagent-result')).toHaveTextContent(
      'Teammate tsx-counter · general-purpose · Claude Fable 5.1',
    );
    expect(screen.getByTestId('subagent-card')).not.toHaveTextContent('Read-only task');
  });

  it('reads only a JSON record with an agent id and a name as a spawn', () => {
    expect(teammateSpawn(result('{"name": "x"}'))).toBeNull();
    expect(teammateSpawn(result('{not json at all'))).toBeNull();
    expect(teammateSpawn(result('Counted 41 files.'))).toBeNull();
    expect(teammateSpawn(result('{"agent_id": "a@s", "name": "a"}'))).toEqual({ name: 'a' });
  });

  it('shows a failed pill for an error result', () => {
    render(<SubagentCard block={task} result={result('boom', true)} />);
    expect(screen.getByTestId('subagent-status')).toHaveTextContent('Failed');
    expect(screen.getByTestId('subagent-card')).toHaveAttribute('data-status', 'failed');
  });

  it('falls back from description to subagent type to the prompt for its title', () => {
    const { rerender } = render(
      <SubagentCard block={{ ...task, input: { subagent_type: 'Explore' } }} />,
    );
    expect(screen.getByRole('button', { name: 'Open subagent: Explore' })).toBeInTheDocument();
    rerender(<SubagentCard block={{ ...task, name: 'Agent', input: { prompt: 'Do it\nnow' } }} />);
    expect(screen.getByRole('button', { name: 'Open subagent: Do it' })).toBeInTheDocument();
    rerender(<SubagentCard block={{ ...task, input: {} }} />);
    expect(screen.getByRole('button', { name: 'Open subagent: Subagent' })).toBeInTheDocument();
  });

  it('does not claim a finished message still runs a call that never returned', () => {
    render(<SubagentCard block={task} isStreaming={false} />);
    expect(screen.queryByTestId('subagent-status')).toBeNull();
    expect(subagentStatus(undefined, false)).toBeNull();
    expect(subagentStatus(undefined, true)).toBe('running');
    expect(subagentStatus(undefined)).toBe('running');
  });

  it('opens the same sheet of tool calls on click and closes on Escape', async () => {
    const user = userEvent.setup();
    render(<SubagentCard block={task} result={result('done')} />);
    await user.click(screen.getByRole('button', { name: /Open subagent/ }));
    const dialog = screen.getByRole('dialog', { name: 'Check the other gate entry points' });
    expect(within(dialog).getAllByTestId('tool-block')).toHaveLength(1);
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('ProviderErrorCard', () => {
  it('titles the failure and shows the message exactly as received', () => {
    render(<ProviderErrorCard message="HTTP 429 rate_limit_exceeded · req_7f3a" />);
    const card = screen.getByRole('alert');
    expect(card).toHaveTextContent("The model couldn't run this turn");
    expect(card).toHaveTextContent('HTTP 429 rate_limit_exceeded · req_7f3a');
  });
});

describe('CompactionMarker', () => {
  it('renders a centred rule saying the context was compacted', () => {
    render(<CompactionMarker />);
    expect(screen.getByRole('separator')).toHaveTextContent(/^Context compacted$/);
  });

  it('adds the token figures only when both are carried', () => {
    const { rerender } = render(<CompactionMarker tokensBefore={182000} tokensAfter={41000} />);
    expect(screen.getByTestId('compaction-marker')).toHaveTextContent(
      'Context compacted · 182k → 41k tokens',
    );
    rerender(<CompactionMarker tokensBefore={182000} />);
    expect(screen.getByTestId('compaction-marker')).toHaveTextContent(/^Context compacted$/);
  });

  it('reads Muse figures from the part and ignores anything that is not a count', () => {
    expect(compactionTokens({ tokensBefore: 10, tokensAfter: 4 })).toEqual({
      tokensBefore: 10,
      tokensAfter: 4,
    });
    expect(compactionTokens({ pre_tokens: 9, post_tokens: 3 })).toEqual({
      tokensBefore: 9,
      tokensAfter: 3,
    });
    expect(compactionTokens({ tokensBefore: '10', tokensAfter: -1 })).toEqual({
      tokensBefore: undefined,
      tokensAfter: undefined,
    });
  });
});

describe('formatTokenCount', () => {
  it('rounds to compact units and treats bad counts as zero', () => {
    expect(formatTokenCount(150000)).toBe('150k');
    expect(formatTokenCount(234)).toBe('234');
    expect(formatTokenCount(-1)).toBe('0');
    expect(formatTokenCount(Number.NaN)).toBe('0');
  });
});
