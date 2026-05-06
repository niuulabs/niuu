import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MentionMenu } from './MentionMenu';
import type { MentionItem } from '../useMentionMenu';
import type { RoomParticipant } from '@/modules/shared/hooks/useSkuldChat';

function makeParticipant(overrides: Partial<RoomParticipant> = {}): RoomParticipant {
  return {
    peerId: 'peer-1',
    persona: 'Ravn-Alpha',
    color: '#a855f7',
    participantType: 'ravn',
    status: 'idle',
    joinedAt: new Date(),
    ...overrides,
  };
}

const mockFileItems: MentionItem[] = [
  { kind: 'file', entry: { name: 'src', path: 'src', type: 'directory' }, depth: 0 },
  { kind: 'file', entry: { name: 'package.json', path: 'package.json', type: 'file' }, depth: 0 },
  { kind: 'file', entry: { name: 'README.md', path: 'README.md', type: 'file' }, depth: 0 },
];

const mockAgentItems: MentionItem[] = [
  { kind: 'agent', participant: makeParticipant({ peerId: 'peer-alpha', persona: 'Ravn-Alpha' }) },
  {
    kind: 'agent',
    participant: makeParticipant({ peerId: 'peer-beta', persona: 'Ravn-Beta', color: '#06b6d4' }),
  },
];

describe('MentionMenu', () => {
  it('renders file items', () => {
    render(
      <MentionMenu
        items={mockFileItems}
        selectedIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />
    );

    expect(screen.getByText('src')).toBeDefined();
    expect(screen.getByText('package.json')).toBeDefined();
    expect(screen.getByText('README.md')).toBeDefined();
  });

  it('shows loading state when no items and loading', () => {
    render(
      <MentionMenu
        items={[]}
        selectedIndex={0}
        loading={true}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />
    );

    expect(screen.getByText('Loading files...')).toBeDefined();
  });

  it('shows empty state when no items and not loading', () => {
    render(
      <MentionMenu
        items={[]}
        selectedIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />
    );

    expect(screen.getByText('No matches')).toBeDefined();
  });

  it('calls onSelect for file items', () => {
    const onSelect = vi.fn();
    render(
      <MentionMenu
        items={mockFileItems}
        selectedIndex={0}
        loading={false}
        onSelect={onSelect}
        onExpand={vi.fn()}
      />
    );

    fireEvent.click(screen.getByText('package.json'));
    expect(onSelect).toHaveBeenCalledWith(mockFileItems[1]);
  });

  it('calls onExpand for directory items on click', () => {
    const onExpand = vi.fn();
    render(
      <MentionMenu
        items={mockFileItems}
        selectedIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onExpand={onExpand}
      />
    );

    fireEvent.click(screen.getByText('src'));
    expect(onExpand).toHaveBeenCalledWith(mockFileItems[0]);
  });

  it('calls onSelect for directory items on double click', () => {
    const onSelect = vi.fn();
    render(
      <MentionMenu
        items={mockFileItems}
        selectedIndex={0}
        loading={false}
        onSelect={onSelect}
        onExpand={vi.fn()}
      />
    );

    fireEvent.doubleClick(screen.getByText('src'));
    expect(onSelect).toHaveBeenCalledWith(mockFileItems[0]);
  });

  it('highlights selected item', () => {
    render(
      <MentionMenu
        items={mockFileItems}
        selectedIndex={1}
        loading={false}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />
    );

    const buttons = screen.getAllByRole('button');
    // selectedIndex=1 → second button (index 1 = package.json)
    expect(buttons[1].dataset.selected).toBe('true');
    expect(buttons[0].dataset.selected).toBe('false');
  });

  it('shows dir badge for directory items', () => {
    render(
      <MentionMenu
        items={mockFileItems}
        selectedIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />
    );

    expect(screen.getByText('dir')).toBeDefined();
  });

  it('renders the menu with test id', () => {
    render(
      <MentionMenu
        items={mockFileItems}
        selectedIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />
    );

    expect(screen.getByTestId('mention-menu')).toBeDefined();
  });

  // ── Agent section tests ────────────────────────────────────────────────

  it('renders Agents section when agent items present', () => {
    const items = [...mockAgentItems, ...mockFileItems];
    render(
      <MentionMenu
        items={items}
        selectedIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />
    );

    expect(screen.getByText('Agents')).toBeDefined();
    expect(screen.getByText('Files')).toBeDefined();
  });

  it('renders agent persona names', () => {
    render(
      <MentionMenu
        items={mockAgentItems}
        selectedIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />
    );

    expect(screen.getByText('Ravn-Alpha')).toBeDefined();
    expect(screen.getByText('Ravn-Beta')).toBeDefined();
  });

  it('calls onSelect when agent item is clicked', () => {
    const onSelect = vi.fn();
    render(
      <MentionMenu
        items={mockAgentItems}
        selectedIndex={0}
        loading={false}
        onSelect={onSelect}
        onExpand={vi.fn()}
      />
    );

    fireEvent.click(screen.getByText('Ravn-Alpha'));
    expect(onSelect).toHaveBeenCalledWith(mockAgentItems[0]);
  });

  it('shows only Files & Directories header when no agents', () => {
    render(
      <MentionMenu
        items={mockFileItems}
        selectedIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />
    );

    expect(screen.getByText('Files & Directories')).toBeDefined();
    expect(screen.queryByText('Agents')).toBeNull();
    expect(screen.queryByText('Files')).toBeNull();
  });

  it('agent items appear before file items in mixed list', () => {
    const items = [...mockAgentItems, ...mockFileItems];
    render(
      <MentionMenu
        items={items}
        selectedIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />
    );

    const buttons = screen.getAllByRole('button');
    // First two buttons are agents
    expect(buttons[0].dataset.kind).toBe('agent');
    expect(buttons[1].dataset.kind).toBe('agent');
    // Next are file items (no data-kind on file items in current impl)
  });

  it('selectedIndex highlights correct item across sections', () => {
    // agents: indices 0,1 — files: indices 2,3,4
    const items = [...mockAgentItems, ...mockFileItems];
    render(
      <MentionMenu
        items={items}
        selectedIndex={2}
        loading={false}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />
    );

    const buttons = screen.getAllByRole('button');
    // index 2 → third button (first file: src)
    expect(buttons[2].dataset.selected).toBe('true');
    expect(buttons[0].dataset.selected).toBe('false');
    expect(buttons[1].dataset.selected).toBe('false');
  });
});
