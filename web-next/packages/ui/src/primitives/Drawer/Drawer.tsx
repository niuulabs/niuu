import * as RadixDialog from '@radix-ui/react-dialog';
import type { CSSProperties, ReactNode } from 'react';
import { cn } from '../../utils/cn';
import './Drawer.css';

export type DrawerSide = 'left' | 'right';

export interface DrawerProps {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
}

export interface DrawerContentProps {
  title: string;
  description?: string;
  side?: DrawerSide;
  width?: number;
  children: ReactNode;
  className?: string;
}

const DEFAULT_DRAWER_WIDTH = 360;

export function Drawer({ open, onOpenChange, children }: DrawerProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      {children}
    </RadixDialog.Root>
  );
}

export const DrawerTrigger = RadixDialog.Trigger;
export const DrawerClose = RadixDialog.Close;

export function DrawerContent({
  title,
  description,
  side = 'right',
  width = DEFAULT_DRAWER_WIDTH,
  children,
  className,
}: DrawerContentProps) {
  return (
    <RadixDialog.Portal>
      <RadixDialog.Overlay className="niuu-drawer-overlay" />
      <RadixDialog.Content
        className={cn('niuu-drawer-content', `niuu-drawer-content--${side}`, className)}
        style={{ '--niuu-drawer-width': `${width}px` } as CSSProperties}
        aria-label={title}
        {...(!description && { 'aria-describedby': undefined })}
      >
        <div className="niuu-drawer-header">
          <RadixDialog.Title className="niuu-drawer-title">{title}</RadixDialog.Title>
          <RadixDialog.Close className="niuu-drawer-close" aria-label="Close">
            <span aria-hidden="true">✕</span>
          </RadixDialog.Close>
        </div>
        {description && (
          <RadixDialog.Description className="niuu-drawer-description">
            {description}
          </RadixDialog.Description>
        )}
        <div className="niuu-drawer-body">{children}</div>
      </RadixDialog.Content>
    </RadixDialog.Portal>
  );
}
