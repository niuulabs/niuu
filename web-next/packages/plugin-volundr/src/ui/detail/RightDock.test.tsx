import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { RightDock } from './RightDock';

describe('RightDock', () => {
  it('shows one pane under a tab strip, switches panes and closes', () => {
    const onSelect = vi.fn();
    const onClose = vi.fn();
    render(
      <RightDock pane="changes" onSelect={onSelect} onClose={onClose}>
        <p>the diff</p>
      </RightDock>,
    );
    expect(screen.getByRole('complementary', { name: 'Right dock' })).toHaveTextContent('the diff');
    expect(screen.getByTestId('right-dock-tab-changes')).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByTestId('right-dock-tab-files'));
    expect(onSelect).toHaveBeenCalledWith('files');
    fireEvent.click(screen.getByRole('button', { name: 'Close the dock' }));
    expect(onClose).toHaveBeenCalled();
  });
});
