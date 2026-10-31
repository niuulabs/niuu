import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import type { ForgeNotificationPayload } from '@niuulabs/domain';
import { NotificationCard, notificationAnchorId } from './NotificationCard';
import { NotificationKindIcon, notificationTone } from './notificationPresentation';
import {
  ConversationResourceProvider,
  type ConversationResourcePort,
} from '../ConversationResources';

function payload(overrides: Partial<ForgeNotificationPayload> = {}): ForgeNotificationPayload {
  return {
    notificationId: 'n-1',
    kind: 'milestone',
    severity: 'success',
    title: 'Migration applied',
    body: 'All **42** tables migrated.',
    links: [],
    correlationId: null,
    ...overrides,
  };
}

function port(): ConversationResourcePort {
  return {
    resolve: vi.fn(() => null),
    load: vi.fn(async () => new Blob()),
    open: vi.fn(),
  };
}

afterEach(() => {
  window.location.hash = '';
});

describe('NotificationCard', () => {
  it('renders the kind, severity, title and markdown body with a severity tone', () => {
    render(<NotificationCard notification={payload()} />);
    const card = screen.getByTestId('notification-card');
    expect(card).toHaveAttribute('data-kind', 'milestone');
    expect(card).toHaveAttribute('data-severity', 'success');
    expect(card).toHaveAttribute('id', 'notification-n-1');
    expect(card).toHaveAccessibleName('Milestone: Migration applied');
    expect(within(card).getByTestId('notification-accent').className).toContain('niuu:bg-state-ok');
    expect(within(card).getByText('Milestone')).toBeInTheDocument();
    expect(within(card).getByText('Success')).toBeInTheDocument();
    expect(within(card).getByText('Migration applied')).toBeInTheDocument();
    expect(within(card).getByText('42').tagName).toBe('STRONG');
  });

  it('keeps info notifications quiet and omits empty sections', () => {
    render(
      <NotificationCard
        notification={payload({ severity: 'info', kind: 'info', body: '', notificationId: null })}
      />,
    );
    const card = screen.getByTestId('notification-card');
    expect(card).not.toHaveAttribute('id');
    expect(within(card).queryByText('Info', { selector: '.niuu\\:text-text-muted' })).toBeNull();
    expect(within(card).queryByTestId('markdown-content')).toBeNull();
    expect(within(card).queryByRole('list')).toBeNull();
  });

  it('opens file links through the presented-file port', () => {
    const resources = port();
    render(
      <ConversationResourceProvider port={resources}>
        <NotificationCard
          notification={payload({
            links: [{ label: 'report.pdf', url: null, kind: 'file', fileId: 'f_1' }],
          })}
        />
      </ConversationResourceProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'report.pdf' }));
    expect(resources.open).toHaveBeenCalledWith({
      kind: 'presented',
      path: 'f_1',
      name: 'report.pdf',
    });
  });

  it('disables file links where file access is unavailable', () => {
    render(
      <NotificationCard
        notification={payload({
          links: [{ label: 'report.pdf', url: null, kind: 'file', fileId: 'f_1' }],
        })}
      />,
    );
    expect(screen.getByRole('button', { name: 'report.pdf' })).toBeDisabled();
  });

  it('renders external, in-app and unsupported links safely', () => {
    render(
      <NotificationCard
        notification={payload({
          links: [
            { label: 'PR #12', url: 'https://example.test/pr/12', kind: 'pr', fileId: null },
            { label: 'Session', url: '/volundr/session/abc', kind: 'session', fileId: null },
            { label: 'Script', url: 'javascript:alert(1)', kind: 'url', fileId: null },
          ],
        })}
      />,
    );
    const external = screen.getByRole('link', { name: 'PR #12' });
    expect(external).toHaveAttribute('href', 'https://example.test/pr/12');
    expect(external).toHaveAttribute('target', '_blank');
    expect(external).toHaveAttribute('rel', 'noopener noreferrer');
    const app = screen.getByRole('link', { name: 'Session' });
    expect(app).toHaveAttribute('href', '/volundr/session/abc');
    expect(app).not.toHaveAttribute('target');
    expect(screen.queryByRole('link', { name: 'Script' })).toBeNull();
    expect(screen.getByText('Script')).toHaveAttribute('title', 'Unsupported link');
  });

  it('scrolls to and highlights the card a deep link targets', () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    window.location.hash = `#${notificationAnchorId('n-1')}`;
    render(<NotificationCard notification={payload()} />);
    expect(screen.getByTestId('notification-card')).toHaveAttribute('data-targeted', 'true');
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'center' });
  });

  it('does not highlight cards another deep link targets', () => {
    window.location.hash = '#notification-other';
    render(<NotificationCard notification={payload()} />);
    expect(screen.getByTestId('notification-card')).not.toHaveAttribute('data-targeted');
  });
});

describe('notification presentation', () => {
  it('maps every severity to a token tone and falls back to info', () => {
    expect(notificationTone('critical').text).toBe('niuu:text-critical');
    expect(notificationTone('warning').bar).toBe('niuu:bg-state-warn');
    expect(notificationTone('info').soft).toContain('niuu:text-brand');
    expect(notificationTone('unknown' as never)).toEqual(notificationTone('info'));
  });

  it('renders a kind icon and falls back for unknown kinds', () => {
    const { container, rerender } = render(<NotificationKindIcon kind="decision" />);
    expect(container.querySelector('[data-kind-icon="decision"]')).not.toBeNull();
    rerender(<NotificationKindIcon kind={'mystery' as never} />);
    expect(container.querySelector('svg')).not.toBeNull();
  });
});
