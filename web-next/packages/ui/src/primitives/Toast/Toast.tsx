import * as RadixToast from '@radix-ui/react-toast';
import { createContext, useCallback, useContext, useState } from 'react';
import type { ReactNode } from 'react';
import { cn } from '../../utils/cn';
import './Toast.css';

export type ToastTone = 'default' | 'success' | 'critical' | 'warning';

const DEFAULT_TOAST_DURATION_MS = 5000;

export interface ToastOptions {
  title: string;
  description?: string;
  tone?: ToastTone;
  duration?: number;
}

interface ToastItem extends Required<Omit<ToastOptions, 'description'>> {
  id: string;
  description: string | undefined;
  open: boolean;
}

interface ToastContextValue {
  toast: (opts: ToastOptions) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export interface ToastProviderProps {
  children: ReactNode;
  swipeDirection?: 'right' | 'left' | 'up' | 'down';
}

export function ToastProvider({ children, swipeDirection = 'right' }: ToastProviderProps) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const toast = useCallback((opts: ToastOptions) => {
    const id = crypto.randomUUID();
    setToasts((prev) => [
      ...prev,
      {
        id,
        title: opts.title,
        description: opts.description,
        tone: opts.tone ?? 'default',
        duration: opts.duration ?? DEFAULT_TOAST_DURATION_MS,
        open: true,
      },
    ]);
  }, []);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      <RadixToast.Provider swipeDirection={swipeDirection}>
        {children}
        {toasts.map((t) => (
          <RadixToast.Root
            key={t.id}
            className={cn('niuu-toast', `niuu-toast--${t.tone}`)}
            open={t.open}
            onOpenChange={(open) => {
              if (!open) dismiss(t.id);
            }}
            duration={t.duration}
          >
            <div className="niuu-toast-body">
              <RadixToast.Title className="niuu-toast-title">{t.title}</RadixToast.Title>
              {t.description && (
                <RadixToast.Description className="niuu-toast-description">
                  {t.description}
                </RadixToast.Description>
              )}
            </div>
            <RadixToast.Close className="niuu-toast-close" aria-label="Dismiss">
              <span aria-hidden="true">✕</span>
            </RadixToast.Close>
          </RadixToast.Root>
        ))}
        <RadixToast.Viewport className="niuu-toast-viewport" />
      </RadixToast.Provider>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (ctx === null) {
    throw new Error('useToast must be used within a <ToastProvider>');
  }
  return ctx;
}
