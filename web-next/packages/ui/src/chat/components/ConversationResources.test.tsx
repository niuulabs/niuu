import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  ConversationLink,
  ConversationImage,
  PresentedFileCard,
  safeExternalUrl,
} from './ConversationResources';

describe('conversation resource controls', () => {
  it('previews external links, keeps fragment navigation and rejects unsafe schemes', () => {
    render(
      <>
        <ConversationLink href="https://example.com">External</ConversationLink>
        <ConversationLink href="#section">Section</ConversationLink>
        <ConversationLink href="javascript:alert(1)">Unsafe</ConversationLink>
      </>,
    );
    expect(screen.getByRole('button', { name: 'External' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Section' })).toHaveAttribute('href', '#section');
    expect(screen.queryByRole('link', { name: 'Unsafe' })).not.toBeInTheDocument();
    expect(safeExternalUrl('data:text/html,bad')).toBeNull();
    expect(safeExternalUrl('mailto:review@example.com')).toBe('mailto:review@example.com');
    expect(safeExternalUrl('not a URL')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'External' }));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByTitle('Preview of example.com')).toHaveAttribute(
      'src',
      'https://example.com',
    );
    expect(screen.getByRole('link', { name: 'Open in new tab' })).toHaveAttribute(
      'href',
      'https://example.com',
    );
  });
  it('shows real image loading errors and unavailable deliveries', () => {
    render(
      <>
        <ConversationImage href="https://example.com/image.png" alt="External diagram" />
        <PresentedFileCard
          block={{ type: 'tool_use', id: 'file', name: 'present_file', input: {} }}
        />
      </>,
    );
    fireEvent.error(screen.getByRole('img'));
    expect(screen.getByText(/Could not load image/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open file' })).toBeDisabled();
    expect(screen.getByText('File delivery is incomplete.')).toBeInTheDocument();
  });
  it('marks local images without a session as unavailable instead of loading forever', () => {
    render(<ConversationImage href="/other/repo/image.png" alt="Outside image" />);
    expect(screen.getByRole('status')).toHaveTextContent(
      'Outside image: Image is unavailable in this session’s workspace.',
    );
    expect(screen.queryByText('Loading image…')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry image' })).not.toBeInTheDocument();
  });
  it('retries remote image decoding without opening a new tab', () => {
    render(<ConversationImage href="https://example.com/image.png" alt="Diagram" />);
    const first = screen.getByRole('img');
    fireEvent.error(first);
    fireEvent.click(screen.getByRole('button', { name: 'Retry image' }));
    expect(screen.getByRole('img')).not.toBe(first);
    expect(screen.getByRole('img')).toHaveAttribute('src', 'https://example.com/image.png');
  });
});

it('opens remote Markdown images in the viewport without navigating away', async () => {
  render(<ConversationImage href="https://example.com/image.png" alt="Reference image" />);
  fireEvent.click(screen.getByRole('button', { name: 'Open image Reference image' }));
  expect(screen.getByRole('dialog', { name: 'Reference image' })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Open original image' })).toHaveAttribute(
    'href',
    'https://example.com/image.png',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Close', exact: true }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('opens HTTP image hyperlinks in the same preview without navigating away', async () => {
  render(
    <ConversationLink href="https://example.test/diagram.png?revision=2">
      Image reference
    </ConversationLink>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Image reference' }));
  expect(screen.getByRole('dialog', { name: 'diagram.png' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Zoom in' })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Open original image' })).toHaveAttribute(
    'href',
    'https://example.test/diagram.png?revision=2',
  );
});
