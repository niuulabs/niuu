import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ToolPicker } from './ToolPicker';
import type { ToolRegistry } from '@niuulabs/domain';

const REGISTRY: ToolRegistry = [
  { id: 'read', group: 'fs', destructive: false, desc: 'Read a file' },
  { id: 'write', group: 'fs', destructive: true, desc: 'Write a file' },
  { id: 'bash', group: 'shell', destructive: true, desc: 'Run a shell command' },
  { id: 'git.status', group: 'git', destructive: false, desc: 'Show git status' },
  { id: 'mimir.read', group: 'mimir', destructive: false, desc: 'Query Mímir' },
];

describe('ToolPicker', () => {
  it('does not render when closed', () => {
    const { container } = render(
      <ToolPicker
        open={false}
        onOpenChange={vi.fn()}
        registry={REGISTRY}
        selected={[]}
        onToggle={vi.fn()}
      />,
    );
    // Dialog portal is not visible when closed
    expect(container.querySelector('[role="dialog"]')).not.toBeInTheDocument();
  });

  it('renders all tools grouped by provider when open', () => {
    render(
      <ToolPicker
        open={true}
        onOpenChange={vi.fn()}
        registry={REGISTRY}
        selected={[]}
        onToggle={vi.fn()}
      />,
    );
    expect(screen.getByText('File System')).toBeInTheDocument();
    expect(screen.getByText('Shell')).toBeInTheDocument();
    expect(screen.getByText('Git')).toBeInTheDocument();
    expect(screen.getByText('Mímir')).toBeInTheDocument();

    expect(screen.getByText('read')).toBeInTheDocument();
    expect(screen.getByText('bash')).toBeInTheDocument();
  });

  it('marks selected tools with aria-selected=true', () => {
    render(
      <ToolPicker
        open={true}
        onOpenChange={vi.fn()}
        registry={REGISTRY}
        selected={['read', 'bash']}
        onToggle={vi.fn()}
      />,
    );
    const readBtn = screen.getByRole('option', { name: /^read\s/i });
    expect(readBtn).toHaveAttribute('aria-selected', 'true');
  });

  it('calls onToggle when a tool is clicked', () => {
    const onToggle = vi.fn();
    render(
      <ToolPicker
        open={true}
        onOpenChange={vi.fn()}
        registry={REGISTRY}
        selected={[]}
        onToggle={onToggle}
      />,
    );
    fireEvent.click(screen.getByRole('option', { name: /git.status/ }));
    expect(onToggle).toHaveBeenCalledWith('git.status');
  });

  it('excludes tools already in the opposing list', () => {
    render(
      <ToolPicker
        open={true}
        onOpenChange={vi.fn()}
        registry={REGISTRY}
        selected={[]}
        excluded={['bash']}
        onToggle={vi.fn()}
      />,
    );
    expect(screen.queryByText('bash')).not.toBeInTheDocument();
  });

  it('shows the custom label in the dialog title', () => {
    render(
      <ToolPicker
        open={true}
        onOpenChange={vi.fn()}
        registry={REGISTRY}
        selected={[]}
        onToggle={vi.fn()}
        label="Allow list — pick tools"
      />,
    );
    expect(screen.getByText('Allow list — pick tools')).toBeInTheDocument();
  });

  it('filters tools when typing in the search box', () => {
    render(
      <ToolPicker
        open={true}
        onOpenChange={vi.fn()}
        registry={REGISTRY}
        selected={[]}
        onToggle={vi.fn()}
      />,
    );
    const search = screen.getByPlaceholderText('Search tools…');
    fireEvent.change(search, { target: { value: 'mimir' } });
    expect(screen.getByText('mimir.read')).toBeInTheDocument();
    expect(screen.queryByText('read')).not.toBeInTheDocument();
  });

  it('shows empty message when search yields no results', () => {
    render(
      <ToolPicker
        open={true}
        onOpenChange={vi.fn()}
        registry={REGISTRY}
        selected={[]}
        onToggle={vi.fn()}
      />,
    );
    const search = screen.getByPlaceholderText('Search tools…');
    fireEvent.change(search, { target: { value: 'zzznomatch' } });
    expect(screen.getByText('No tools match your search.')).toBeInTheDocument();
  });

  it('shows a check mark for selected tools', () => {
    render(
      <ToolPicker
        open={true}
        onOpenChange={vi.fn()}
        registry={REGISTRY}
        selected={['read']}
        onToggle={vi.fn()}
      />,
    );
    const readItem = screen.getByRole('option', { name: /^read\s/i });
    expect(readItem.textContent).toContain('✓');
  });
});
