import { type ReactNode } from 'react';
import { cn } from '../../utils/cn';
import './KpiStrip.css';

export interface KpiStripProps {
  children: ReactNode;
  className?: string;
}

export function KpiStrip({ children, className }: KpiStripProps) {
  return (
    <div className={cn('niuu-kpi-strip', className)} role="group" aria-label="KPI metrics">
      {children}
    </div>
  );
}
