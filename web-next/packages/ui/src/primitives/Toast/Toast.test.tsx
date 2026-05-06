import { describe, it, expect } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ToastProvider, useToast } from './Toast';
import type { ToastOptions } from './Toast';

function setup() {
  return userEvent.setup();
}

function ToastTrigger({ opts }: { opts: ToastOptions }) {
  const { toast } = useToast();
  return <button onClick={() => toast(opts)}>Show toast</button>;
}

function Harness({ opts }: { opts: ToastOptions }) {
  return (
    <ToastProvider>
      <ToastTrigger opts={opts} />
    </ToastProvider>
  );
}

describe('ToastProvider / useToast', () => {
  it('renders children without showing any toast initially', () => {
    render(
      <ToastProvider>
        <span>app content</span>
      </ToastProvider>,
    );
    expect(screen.getByText('app content')).toBeInTheDocument();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('toast() displays a toast with the given title', async () => {
    const user = setup();
    render(<Harness opts={{ title: 'Operation complete' }} />);
    await user.click(screen.getByRole('button', { name: 'Show toast' }));
    await waitFor(() => expect(screen.getByText('Operation complete')).toBeInTheDocument());
  });

  it('renders optional description', async () => {
    const user = setup();
    render(<Harness opts={{ title: 'Saved', description: 'Your changes have been saved.' }} />);
    await user.click(screen.getByRole('button', { name: 'Show toast' }));
    await waitFor(() =>
      expect(screen.getByText('Your changes have been saved.')).toBeInTheDocument(),
    );
  });

  it('applies critical tone class', async () => {
    const user = setup();
    render(<Harness opts={{ title: 'Error', tone: 'critical' }} />);
    await user.click(screen.getByRole('button', { name: 'Show toast' }));
    await waitFor(() => {
      const toast = document.querySelector('.niuu-toast--critical');
      expect(toast).toBeInTheDocument();
    });
  });

  it('applies success tone class', async () => {
    const user = setup();
    render(<Harness opts={{ title: 'Done', tone: 'success' }} />);
    await user.click(screen.getByRole('button', { name: 'Show toast' }));
    await waitFor(() => expect(document.querySelector('.niuu-toast--success')).toBeInTheDocument());
  });

  it('applies warning tone class', async () => {
    const user = setup();
    render(<Harness opts={{ title: 'Warning', tone: 'warning' }} />);
    await user.click(screen.getByRole('button', { name: 'Show toast' }));
    await waitFor(() => expect(document.querySelector('.niuu-toast--warning')).toBeInTheDocument());
  });

  it('applies default tone class when no tone given', async () => {
    const user = setup();
    render(<Harness opts={{ title: 'Info' }} />);
    await user.click(screen.getByRole('button', { name: 'Show toast' }));
    await waitFor(() => expect(document.querySelector('.niuu-toast--default')).toBeInTheDocument());
  });

  it('dismiss button removes the toast', async () => {
    const user = setup();
    render(<Harness opts={{ title: 'Dismissible' }} />);
    await user.click(screen.getByRole('button', { name: 'Show toast' }));
    await waitFor(() => screen.getByText('Dismissible'));
    await user.click(screen.getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(screen.queryByText('Dismissible')).toBeNull());
  });

  it('stacks multiple toasts', async () => {
    const user = setup();
    render(
      <ToastProvider>
        <ToastTrigger opts={{ title: 'First' }} />
        <ToastTrigger opts={{ title: 'Second' }} />
      </ToastProvider>,
    );
    const [btnFirst, btnSecond] = screen.getAllByRole('button', { name: 'Show toast' });
    await act(async () => {
      await user.click(btnFirst!);
      await user.click(btnSecond!);
    });
    await waitFor(() => {
      expect(screen.getByText('First')).toBeInTheDocument();
      expect(screen.getByText('Second')).toBeInTheDocument();
    });
  });

  it('throws when useToast is called outside ToastProvider', () => {
    const original = console.error;
    console.error = () => {};
    expect(() => {
      render(<ToastTrigger opts={{ title: 'x' }} />);
    }).toThrow('useToast must be used within a <ToastProvider>');
    console.error = original;
  });
});
