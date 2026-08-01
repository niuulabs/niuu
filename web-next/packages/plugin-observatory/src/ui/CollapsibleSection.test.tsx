import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CollapsibleSection } from './CollapsibleSection';

function renderSection(props: Partial<Parameters<typeof CollapsibleSection>[0]> = {}) {
  return render(
    <CollapsibleSection title="Realms" testId="realms" {...props}>
      <button data-testid="child">asgard</button>
    </CollapsibleSection>,
  );
}

describe('CollapsibleSection', () => {
  it('renders the title and its children', () => {
    renderSection();
    expect(screen.getByText('Realms')).toBeInTheDocument();
    expect(screen.getByTestId('child')).toBeInTheDocument();
  });

  it('is open by default', () => {
    renderSection();
    expect(screen.getByTestId('subnav-section-realms')).toHaveAttribute('open');
  });

  it('can start collapsed', () => {
    renderSection({ defaultOpen: false });
    expect(screen.getByTestId('subnav-section-realms')).not.toHaveAttribute('open');
  });

  it('collapses and expands when the header is activated', () => {
    renderSection();
    const section = screen.getByTestId('subnav-section-realms') as HTMLDetailsElement;
    const toggle = screen.getByTestId('subnav-toggle-realms');

    fireEvent.click(toggle);
    expect(section.open).toBe(false);

    fireEvent.click(toggle);
    expect(section.open).toBe(true);
  });

  it('renders meta content beside the title', () => {
    renderSection({ meta: <span data-testid="meta">5</span> });
    expect(screen.getByTestId('meta')).toHaveTextContent('5');
  });

  it('uses a summary so disclosure semantics come from the element', () => {
    renderSection();
    expect(screen.getByTestId('subnav-toggle-realms').tagName).toBe('SUMMARY');
  });
});
