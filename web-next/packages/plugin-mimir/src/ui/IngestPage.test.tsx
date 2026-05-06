import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { IngestPage } from './IngestPage';
import { renderWithMimir } from '../testing/renderWithMimir';

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    to,
    className,
    children,
  }: {
    to: string;
    className?: string;
    children: React.ReactNode;
  }) => (
    <a href={to} className={className}>
      {children}
    </a>
  ),
}));

const wrap = renderWithMimir;

describe('IngestPage', () => {
  it('renders the ingest page with data-testid', () => {
    wrap(<IngestPage />);
    expect(screen.getByTestId('ingest-page')).toBeInTheDocument();
  });

  it('renders the "Ingest a source" heading', () => {
    wrap(<IngestPage />);
    expect(screen.getByRole('heading', { name: /ingest a source/i })).toBeInTheDocument();
  });

  it('renders the "Write routing" heading', () => {
    wrap(<IngestPage />);
    expect(screen.getByRole('heading', { name: /write routing/i })).toBeInTheDocument();
  });

  it('renders the "Recent sources" heading', () => {
    wrap(<IngestPage />);
    expect(screen.getByRole('heading', { name: /recent sources/i })).toBeInTheDocument();
  });

  it('renders source title input with default value', () => {
    wrap(<IngestPage />);
    const input = screen.getByLabelText(/source title/i) as HTMLInputElement;
    expect(input.value).toContain('dispatch protocol');
  });

  it('renders target page path input', () => {
    wrap(<IngestPage />);
    const input = screen.getByLabelText(/target page path/i) as HTMLInputElement;
    expect(input.value).toBe('projects/niuu/dispatch.md');
  });

  it('renders raw content textarea', () => {
    wrap(<IngestPage />);
    expect(screen.getByLabelText(/raw content/i)).toBeInTheDocument();
  });

  it('renders Ingest, Fetch URL, and Upload file buttons', () => {
    wrap(<IngestPage />);
    expect(screen.getByRole('button', { name: /ingest source/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /fetch url/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /upload file/i })).toBeInTheDocument();
  });

  it('renders routing rules after data loads', async () => {
    wrap(<IngestPage />);
    await waitFor(() =>
      expect(screen.getAllByTestId('routing-rule-row').length).toBeGreaterThan(0),
    );
  });

  it('renders the resolved route box', async () => {
    wrap(<IngestPage />);
    await waitFor(() => expect(screen.getByTestId('resolved-route')).toBeInTheDocument());
  });

  it('resolved route updates when path changes', async () => {
    wrap(<IngestPage />);
    await waitFor(() => expect(screen.getByTestId('resolved-route')).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(/target page path/i), {
      target: { value: '/infra/k8s' },
    });
    await waitFor(() => expect(screen.getByTestId('resolved-route')).toHaveTextContent(/infra/));
  });

  it('renders recent source rows after data loads', async () => {
    wrap(<IngestPage />);
    await waitFor(() =>
      expect(screen.getAllByTestId('recent-source-row').length).toBeGreaterThan(0),
    );
  });
});
