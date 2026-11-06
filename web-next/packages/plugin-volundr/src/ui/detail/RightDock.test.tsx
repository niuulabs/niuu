import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { FileDiff, FileText, FolderOpen } from 'lucide-react';
import { RightDock } from './RightDock';

describe('RightDock', () => {
  it('shows the open panes as tabs, switches and closes them, and adds one from "+"', () => {
    const onSelect = vi.fn();
    const onClosePane = vi.fn();
    const onClose = vi.fn();
    const addFiles = vi.fn();
    render(
      <RightDock
        tabs={[
          { id: 'changes', label: 'Changes', icon: FileDiff },
          { id: 'file:/workspace/a.md', label: 'a.md', icon: FileText, title: '/workspace/a.md' },
        ]}
        active="changes"
        width={480}
        addItems={[
          { id: 'files', label: 'Files', icon: FolderOpen, onSelect: addFiles, shortcut: '⌥⌘F' },
        ]}
        onSelect={onSelect}
        onClosePane={onClosePane}
        onClose={onClose}
      >
        <p>the diff</p>
      </RightDock>,
    );
    const dock = screen.getByRole('complementary', { name: 'Right dock' });
    expect(dock).toHaveTextContent('the diff');
    expect(dock).toHaveStyle({ width: '480px' });
    expect(screen.getByTestId('right-dock-tab-changes')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('right-dock-tab-file:/workspace/a.md')).toHaveAttribute(
      'title',
      '/workspace/a.md',
    );

    fireEvent.click(screen.getByTestId('right-dock-tab-file:/workspace/a.md'));
    expect(onSelect).toHaveBeenCalledWith('file:/workspace/a.md');
    fireEvent.click(screen.getByRole('button', { name: 'Close a.md' }));
    expect(onClosePane).toHaveBeenCalledWith('file:/workspace/a.md');

    fireEvent.click(screen.getByRole('button', { name: 'Add a pane' }));
    const files = screen.getByRole('button', { name: /Files/ });
    expect(files).toHaveTextContent('⌥⌘F');
    fireEvent.click(files);
    expect(addFiles).toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Close the dock' }));
    expect(onClose).toHaveBeenCalled();
  });
});
