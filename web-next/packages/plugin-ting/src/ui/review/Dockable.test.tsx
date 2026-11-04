import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Dockable } from './Dockable';

function Column() {
  const [floating, setFloating] = useState(false);
  return (
    <div data-testid="page">
      <Dockable
        label="Coordinator"
        floating={floating}
        onFloatingChange={setFloating}
        className="column"
        actions={<button type="button">Hide</button>}
      >
        <p>The chat</p>
      </Dockable>
    </div>
  );
}

describe('Dockable', () => {
  it('sits in its column until popped out', () => {
    render(<Column />);
    const column = screen.getByRole('complementary', { name: 'Coordinator' });
    expect(screen.getByTestId('page')).toContainElement(column);
    expect(screen.getByRole('button', { name: 'Hide' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('floats over the page when popped out, and closing puts it back', () => {
    render(<Column />);
    fireEvent.click(screen.getByRole('button', { name: 'Pop out' }));

    const floating = screen.getByRole('dialog', { name: 'Coordinator' });
    expect(floating).toHaveClass('ting-dockable--floating');
    expect(screen.getByTestId('page')).not.toContainElement(floating);
    expect(floating).toHaveTextContent('The chat');

    fireEvent.click(screen.getByRole('button', { name: 'Put back in its column' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByTestId('page')).toHaveTextContent('The chat');
  });

  it('goes back to its column on Escape', () => {
    render(<Column />);
    fireEvent.click(screen.getByRole('button', { name: 'Pop out' }));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('moves with the pointer by its title and stays on screen', () => {
    render(<Column />);
    fireEvent.click(screen.getByRole('button', { name: 'Pop out' }));
    const panel = screen.getByRole('dialog', { name: 'Coordinator' });
    const title = screen.getByText('Coordinator');
    const head = title.parentElement!;
    head.setPointerCapture = () => undefined;

    fireEvent.pointerDown(head, { clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(head, { clientX: 110, clientY: 60, pointerId: 1 });
    expect(panel.style.left).toBe('100px');
    expect(panel.style.top).toBe('50px');

    fireEvent.pointerMove(head, { clientX: -500, clientY: -500, pointerId: 1 });
    expect(panel.style.left).toBe('0px');
    expect(panel.style.top).toBe('0px');
    fireEvent.pointerUp(head, { pointerId: 1 });
  });
});
