import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { renderWithMimir as wrap } from '../testing/renderWithMimir';
import { SourcesView } from './SourcesView';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';

describe('SourcesView', () => {
  // ── Origin filter tabs ────────────────────────────────────────────────
  it('renders origin filter tabs', () => {
    wrap(<SourcesView />);
    expect(screen.getByRole('tab', { name: 'all' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'web' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'file' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'arxiv' })).toBeInTheDocument();
  });

  it('"all" tab is selected by default', () => {
    wrap(<SourcesView />);
    const allTab = screen.getByRole('tab', { name: 'all' });
    expect(allTab).toHaveAttribute('aria-selected', 'true');
  });

  it('shows all sources by default', async () => {
    wrap(<SourcesView />);
    await waitFor(() => expect(screen.getByText('7 sources')).toBeInTheDocument());
  });

  it('filters sources by origin when a tab is clicked', async () => {
    wrap(<SourcesView />);
    await waitFor(() => expect(screen.getByText('7 sources')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('tab', { name: 'web' }));
    await waitFor(() => expect(screen.getByText(/1 source/)).toBeInTheDocument());
  });

  it('filters to "file" origin shows only file sources', async () => {
    wrap(<SourcesView />);
    await waitFor(() => expect(screen.getByText('7 sources')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('tab', { name: 'file' }));
    await waitFor(() => expect(screen.getByText(/2 sources/)).toBeInTheDocument());
  });

  it('shows "not compiled yet" for sources not attributed to any page', async () => {
    wrap(<SourcesView />);
    await waitFor(() => expect(screen.getAllByText('not compiled yet').length).toBeGreaterThan(0));
  });

  it('renders origin badges for each source', async () => {
    wrap(<SourcesView />);
    await waitFor(() => expect(screen.getAllByLabelText(/origin:/i).length).toBeGreaterThan(0));
  });

  // ── Loading / error states ─────────────────────────────────────────────
  it('shows loading state', () => {
    const pendingService: IMimirService = {
      ...createMimirMockAdapter(),
      pages: {
        ...createMimirMockAdapter().pages,
        listSources: () => new Promise(() => {}),
      },
    };
    wrap(<SourcesView />, pendingService);
    expect(screen.getByText(/loading sources/)).toBeInTheDocument();
  });

  it('shows error state', async () => {
    const failingService: IMimirService = {
      ...createMimirMockAdapter(),
      pages: {
        ...createMimirMockAdapter().pages,
        listSources: async () => {
          throw new Error('sources unavailable');
        },
      },
    };
    wrap(<SourcesView />, failingService);
    await waitFor(() => expect(screen.getByText('sources unavailable')).toBeInTheDocument());
  });

  // ── Ingest form — mode toggle ──────────────────────────────────────────
  it('renders the ingest form section', () => {
    wrap(<SourcesView />);
    expect(screen.getByLabelText('Ingest source')).toBeInTheDocument();
  });

  it('shows URL mode by default', () => {
    wrap(<SourcesView />);
    expect(screen.getByTestId('url-input')).toBeInTheDocument();
    expect(screen.queryByTestId('file-dropzone')).not.toBeInTheDocument();
  });

  it('switches to file mode when File button is clicked', () => {
    wrap(<SourcesView />);
    fireEvent.click(screen.getByTestId('mode-file'));
    expect(screen.getByTestId('file-dropzone')).toBeInTheDocument();
    expect(screen.queryByTestId('url-input')).not.toBeInTheDocument();
  });

  it('switches back to URL mode', () => {
    wrap(<SourcesView />);
    fireEvent.click(screen.getByTestId('mode-file'));
    fireEvent.click(screen.getByTestId('mode-url'));
    expect(screen.getByTestId('url-input')).toBeInTheDocument();
  });

  // ── Ingest form — URL mode ─────────────────────────────────────────────
  it('Fetch button is disabled when URL is empty', () => {
    wrap(<SourcesView />);
    expect(screen.getByTestId('fetch-button')).toBeDisabled();
  });

  it('Fetch button is enabled when URL has a value', async () => {
    wrap(<SourcesView />);
    fireEvent.change(screen.getByTestId('url-input'), {
      target: { value: 'https://example.com' },
    });
    await waitFor(() => expect(screen.getByTestId('fetch-button')).not.toBeDisabled());
  });

  it('calls ingestUrl and shows success message', async () => {
    const mockAdapter = createMimirMockAdapter();
    const ingestUrl = vi.fn().mockResolvedValue({
      id: 'src-new',
      title: 'https://example.com',
      originType: 'web',
      originUrl: 'https://example.com',
      ingestedAt: '2026-04-21T00:00:00Z',
      ingestAgent: 'ravn-fjolnir',
      compiledInto: [],
      content: '',
    });
    const service: IMimirService = {
      ...mockAdapter,
      pages: { ...mockAdapter.pages, ingestUrl },
    };

    wrap(<SourcesView />, service);

    fireEvent.change(screen.getByTestId('url-input'), {
      target: { value: 'https://example.com' },
    });
    fireEvent.click(screen.getByTestId('fetch-button'));

    await waitFor(() => expect(ingestUrl).toHaveBeenCalledWith('https://example.com'));
    await waitFor(() => expect(screen.getByTestId('ingest-success')).toBeInTheDocument());
  });

  it('success banner is cleared when a new mutation starts', async () => {
    const mockAdapter = createMimirMockAdapter();
    let callCount = 0;
    const ingestUrl = vi.fn().mockImplementation(async () => {
      callCount++;
      if (callCount > 1) throw new Error('second attempt fails');
      return {
        id: 'src-new',
        title: 'https://example.com',
        originType: 'web' as const,
        originUrl: 'https://example.com',
        ingestedAt: '2026-04-21T00:00:00Z',
        ingestAgent: 'ravn-fjolnir',
        compiledInto: [],
        content: '',
      };
    });
    const service: IMimirService = { ...mockAdapter, pages: { ...mockAdapter.pages, ingestUrl } };

    wrap(<SourcesView />, service);

    // First ingest — success banner appears
    fireEvent.change(screen.getByTestId('url-input'), {
      target: { value: 'https://example.com' },
    });
    fireEvent.click(screen.getByTestId('fetch-button'));
    await waitFor(() => expect(screen.getByTestId('ingest-success')).toBeInTheDocument());

    // Second ingest — success banner must disappear before result
    fireEvent.change(screen.getByTestId('url-input'), {
      target: { value: 'https://example.com' },
    });
    fireEvent.click(screen.getByTestId('fetch-button'));
    await waitFor(() => expect(screen.queryByTestId('ingest-success')).not.toBeInTheDocument());
  });

  it('shows error message when ingestUrl fails', async () => {
    const mockAdapter = createMimirMockAdapter();
    const service: IMimirService = {
      ...mockAdapter,
      pages: {
        ...mockAdapter.pages,
        ingestUrl: async () => {
          throw new Error('fetch failed');
        },
      },
    };

    wrap(<SourcesView />, service);

    fireEvent.change(screen.getByTestId('url-input'), {
      target: { value: 'https://example.com' },
    });
    fireEvent.click(screen.getByTestId('fetch-button'));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('fetch failed'));
  });

  // ── Ingest form — file mode ────────────────────────────────────────────
  it('calls ingestFile when a file is selected', async () => {
    const mockAdapter = createMimirMockAdapter();
    const ingestFile = vi.fn().mockResolvedValue({
      id: 'src-file-new',
      title: 'test.md',
      originType: 'file',
      originPath: 'test.md',
      ingestedAt: '2026-04-21T00:00:00Z',
      ingestAgent: 'ravn-fjolnir',
      compiledInto: [],
      content: '',
    });
    const service: IMimirService = {
      ...mockAdapter,
      pages: { ...mockAdapter.pages, ingestFile },
    };

    wrap(<SourcesView />, service);
    fireEvent.click(screen.getByTestId('mode-file'));

    const fileInput = screen.getByTestId('file-input');
    const file = new File(['# hello'], 'test.md', { type: 'text/markdown' });
    Object.defineProperty(fileInput, 'files', { value: [file] });
    fireEvent.change(fileInput);

    await waitFor(() => expect(ingestFile).toHaveBeenCalledWith(file));
    await waitFor(() => expect(screen.getByTestId('ingest-success')).toBeInTheDocument());
  });

  it('shows error message when ingestFile fails', async () => {
    const mockAdapter = createMimirMockAdapter();
    const service: IMimirService = {
      ...mockAdapter,
      pages: {
        ...mockAdapter.pages,
        ingestFile: async () => {
          throw new Error('upload failed');
        },
      },
    };

    wrap(<SourcesView />, service);
    fireEvent.click(screen.getByTestId('mode-file'));

    const fileInput = screen.getByTestId('file-input');
    const file = new File(['content'], 'test.txt', { type: 'text/plain' });
    Object.defineProperty(fileInput, 'files', { value: [file] });
    fireEvent.change(fileInput);

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('upload failed'));
  });
});
