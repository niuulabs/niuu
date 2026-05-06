import * as RadixDialog from '@radix-ui/react-dialog';
import type { ReactNode } from 'react';
import { cn } from '../../utils/cn';
import './Dialog.css';

export interface DialogProps {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
}

export interface DialogContentProps {
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
}

export function Dialog({ open, onOpenChange, children }: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      {children}
    </RadixDialog.Root>
  );
}

export const DialogTrigger = RadixDialog.Trigger;
export const DialogClose = RadixDialog.Close;

export function DialogContent({ title, description, children, className }: DialogContentProps) {
  return (
    <RadixDialog.Portal>
      <RadixDialog.Overlay className="niuu-dialog-overlay" />
      <RadixDialog.Content
        className={cn('niuu-dialog-content', className)}
        {...(!description && { 'aria-describedby': undefined })}
      >
        <div className="niuu-dialog-header">
          <RadixDialog.Title className="niuu-dialog-title">{title}</RadixDialog.Title>
          <RadixDialog.Close className="niuu-dialog-close" aria-label="Close">
            <span aria-hidden="true">✕</span>
          </RadixDialog.Close>
        </div>
        {description && (
          <RadixDialog.Description className="niuu-dialog-description">
            {description}
          </RadixDialog.Description>
        )}
        <div className="niuu-dialog-body">{children}</div>
      </RadixDialog.Content>
    </RadixDialog.Portal>
  );
}
