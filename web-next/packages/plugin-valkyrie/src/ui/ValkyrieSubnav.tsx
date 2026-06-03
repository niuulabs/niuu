import { Brain, Radio, Shield, Users } from 'lucide-react';

const ITEM =
  'niuu:flex niuu:items-center niuu:gap-2 niuu:rounded-md niuu:px-2 niuu:py-1.5 niuu:text-sm niuu:text-text-muted';

export function ValkyrieSubnav() {
  return (
    <nav data-testid="valkyrie-subnav" className="niuu:flex niuu:flex-col niuu:gap-1">
      <span className={ITEM}>
        <Shield size={15} aria-hidden="true" />
        Environments
      </span>
      <span className={ITEM}>
        <Users size={15} aria-hidden="true" />
        Flocks
      </span>
      <span className={ITEM}>
        <Radio size={15} aria-hidden="true" />
        Signals
      </span>
      <span className={ITEM}>
        <Brain size={15} aria-hidden="true" />
        Learning
      </span>
    </nav>
  );
}
