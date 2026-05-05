import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MentionMenu } from './MentionMenu';
import type { MentionMenuItem } from '../../hooks/useMentionMenu';

describe('MentionMenu', () => {
  const agentItem: MentionMenuItem = {
    kind: 'agent',
    participant: { peerId: 'peer-1', persona: 'Odin' },
  };
  const fileItem: MentionMenuItem = {
    kind: 'file',
    entry: { name: 'index.ts', path: '/src/index.ts', type: 'file' },
  };
  const dirItem: MentionMenuItem = {
    kind: 'file',
    entry: { name: 'src', path: '/src', type: 'directory' },
  };

  it('renders agent section', () => {
    render(
      <MentionMenu
        items={[agentItem]}
        selectedIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />,
    );
    expect(screen.getByText('Agents')).toBeInTheDocument();
    expect(screen.getByText('Odin')).toBeInTheDocument();
  });

  it('renders file section', () => {
    render(
      <MentionMenu
        items={[fileItem]}
        selectedIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />,
    );
    expect(screen.getByText('Files')).toBeInTheDocument();
    expect(screen.getByText('index.ts')).toBeInTheDocument();
  });

  it('calls onSelect when file clicked', () => {
    const onSelect = vi.fn();
    render(
      <MentionMenu
        items={[fileItem]}
        selectedIndex={0}
        loading={false}
        onSelect={onSelect}
        onExpand={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByText('index.ts'));
    expect(onSelect).toHaveBeenCalledWith(fileItem);
  });

  it('calls onExpand when directory clicked', () => {
    const onExpand = vi.fn();
    render(
      <MentionMenu
        items={[dirItem]}
        selectedIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onExpand={onExpand}
      />,
    );
    fireEvent.click(screen.getByText('src'));
    expect(onExpand).toHaveBeenCalledWith(dirItem);
  });

  it('shows loading spinner', () => {
    render(
      <MentionMenu
        items={[]}
        selectedIndex={0}
        loading={true}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />,
    );
    expect(
      screen.getByTestId('mention-menu').querySelector('.niuu-chat-mention-spinner'),
    ).toBeInTheDocument();
  });

  it('shows empty state when no items and not loading', () => {
    render(
      <MentionMenu
        items={[]}
        selectedIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />,
    );
    expect(screen.getByText('No results')).toBeInTheDocument();
  });

  it('marks selected item with selected class', () => {
    render(
      <MentionMenu
        items={[agentItem]}
        selectedIndex={0}
        loading={false}
        onSelect={vi.fn()}
        onExpand={vi.fn()}
      />,
    );
    expect(screen.getByRole('option')).toHaveClass('niuu-chat-mention-item--selected');
  });
});
