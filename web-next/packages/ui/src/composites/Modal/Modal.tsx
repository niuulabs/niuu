import type { ReactNode } from 'react';
import { Dialog, DialogContent, DialogClose } from '../../primitives/Dialog';
import { cn } from '../../utils/cn';

export interface ModalAction {
  label: string;
  onClick?: () => void;
  variant?: 'primary' | 'secondary' | 'destructive';
  disabled?: boolean;
  /** If true, clicking this action closes the modal. Default true. */
  closes?: boolean;
}

export interface ModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  actions?: ModalAction[];
  className?: string;
}

const VARIANT_CLASSES: Record<NonNullable<ModalAction['variant']>, string> = {
  primary: 'niuu-bg-brand niuu-text-bg-primary hover:niuu-bg-brand-600 niuu-font-medium',
  secondary:
    'niuu-border niuu-border-border niuu-text-text-secondary hover:niuu-text-text-primary niuu-bg-transparent',
  destructive: 'niuu-bg-critical niuu-text-bg-primary hover:niuu-opacity-90 niuu-font-medium',
};

/**
 * Opinionated modal dialog with title, body, and action buttons.
 * Wraps the low-level Dialog primitive with a standard layout.
 */
export function Modal({
  open,
  onOpenChange,
  title,
  description,
  children,
  actions,
  className,
}: ModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={title} description={description} className={className}>
        <div className="niuu-modal-body">{children}</div>
        {actions && actions.length > 0 && (
          <div className="niuu-flex niuu-justify-end niuu-gap-3 niuu-mt-5 niuu-pt-4 niuu-border-t niuu-border-border-subtle">
            {actions.map((action) => {
              const variant = action.variant ?? 'secondary';
              const closes = action.closes ?? true;
              const btn = (
                <button
                  key={action.label}
                  type="button"
                  onClick={action.onClick}
                  disabled={action.disabled}
                  className={cn(
                    'niuu-rounded-md niuu-px-4 niuu-py-2 niuu-text-sm niuu-transition-colors',
                    VARIANT_CLASSES[variant],
                    action.disabled && 'niuu-opacity-50 niuu-cursor-not-allowed',
                  )}
                >
                  {action.label}
                </button>
              );

              if (closes) {
                return (
                  <DialogClose key={action.label} asChild>
                    {btn}
                  </DialogClose>
                );
              }
              return btn;
            })}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
