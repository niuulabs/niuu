import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  ConversationLink,
  ConversationImage,
  PresentedFileCard,
  safeExternalUrl,
} from './ConversationResources';

describe('conversation resource controls', () => {
  it('keeps external and fragment links navigable while rejecting unsafe schemes', () => {
    render(
      <>
        <ConversationLink href="https://example.com">External</ConversationLink>
        <ConversationLink href="#section">Section</ConversationLink>
        <ConversationLink href="javascript:alert(1)">Unsafe</ConversationLink>
      </>,
    );
    expect(screen.getByRole('link', { name: 'External' })).toHaveAttribute(
      'href',
      'https://example.com',
    );
    expect(screen.getByRole('link', { name: 'Section' })).toHaveAttribute('href', '#section');
    expect(screen.queryByRole('link', { name: 'Unsafe' })).not.toBeInTheDocument();
    expect(safeExternalUrl('data:text/html,bad')).toBeNull();
    expect(safeExternalUrl('mailto:review@example.com')).toBe('mailto:review@example.com');
    expect(safeExternalUrl('not a URL')).toBeNull();
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
