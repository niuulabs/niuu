import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RawSourcePane } from './RawSourcePane';

const SOURCES = [
  {
    id: 'src-001',
    title: 'Architecture wiki',
    originType: 'web',
    content: 'Hexagonal architecture uses [[arch/overview]] and [[ports/overview]] patterns.',
  },
  {
    id: 'src-002',
    title: 'ADR-001',
    originType: 'file',
    content: 'Plain text with no wikilinks.',
  },
];

describe('RawSourcePane', () => {
  it('renders the "Raw sources" heading', () => {
    render(<RawSourcePane sources={[]} />);
    expect(screen.getByText(/raw sources backing this page/i)).toBeInTheDocument();
  });

  it('shows empty state when no sources', () => {
    render(<RawSourcePane sources={[]} />);
    expect(screen.getByText(/no sources attributed yet/i)).toBeInTheDocument();
  });

  it('renders each source card with id and title', () => {
    render(<RawSourcePane sources={SOURCES} />);
    expect(screen.getByText('src-001')).toBeInTheDocument();
    expect(screen.getByText('Architecture wiki')).toBeInTheDocument();
    expect(screen.getByText('src-002')).toBeInTheDocument();
    expect(screen.getByText('ADR-001')).toBeInTheDocument();
  });

  it('shows origin type for each source', () => {
    render(<RawSourcePane sources={SOURCES} />);
    expect(screen.getByText('web')).toBeInTheDocument();
    expect(screen.getByText('file')).toBeInTheDocument();
  });

  it('renders wikilinks as clickable buttons', () => {
    render(<RawSourcePane sources={SOURCES} />);
    const wikilinkBtns = screen.getAllByRole('button', { name: /navigate to/ });
    expect(wikilinkBtns).toHaveLength(2);
  });

  it('calls onNavigate when a wikilink is clicked', () => {
    const spy = vi.fn();
    render(<RawSourcePane sources={SOURCES} onNavigate={spy} />);
    const btn = screen.getByRole('button', { name: /navigate to arch\/overview/ });
    fireEvent.click(btn);
    expect(spy).toHaveBeenCalledWith('arch/overview');
  });

  it('renders plain text content without buttons', () => {
    render(<RawSourcePane sources={[SOURCES[1]!]} />);
    expect(screen.queryByRole('button', { name: /navigate to/ })).toBeNull();
    expect(screen.getByText(/plain text with no wikilinks/i)).toBeInTheDocument();
  });

  it('has aria-label on the root container', () => {
    render(<RawSourcePane sources={SOURCES} />);
    const container = screen.getByLabelText('raw sources');
    expect(container).toBeInTheDocument();
  });

  it('renders source card with correct aria-label', () => {
    render(<RawSourcePane sources={[SOURCES[0]!]} />);
    expect(screen.getByLabelText('source src-001')).toBeInTheDocument();
  });
});
