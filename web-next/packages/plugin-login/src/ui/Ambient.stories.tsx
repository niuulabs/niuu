import type { Meta, StoryObj } from '@storybook/react';
import type { AmbientVariant } from './useAmbient';
import { AmbientTopology } from './AmbientTopology';
import { AmbientConstellation } from './AmbientConstellation';
import { AmbientLattice } from './AmbientLattice';
import './Ambient.stories.css';

// ─── Shared wrapper ───────────────────────────────────────────────────────────
function AmbientPreview({ variant }: { variant: AmbientVariant }) {
  const components: Record<AmbientVariant, React.ComponentType> = {
    topology: AmbientTopology,
    constellation: AmbientConstellation,
    lattice: AmbientLattice,
  };

  const Component = components[variant];
  return (
    <div className="ambient-preview">
      <Component />
    </div>
  );
}

// ─── Topology ─────────────────────────────────────────────────────────────────
const topologyMeta: Meta = {
  title: 'Login/Ambient/Topology',
  component: AmbientTopology,
  parameters: { layout: 'fullscreen' },
};
export default topologyMeta;

export const Topology: StoryObj = {
  render: () => <AmbientPreview variant="topology" />,
};

export const Constellation: StoryObj = {
  render: () => <AmbientPreview variant="constellation" />,
};

export const Lattice: StoryObj = {
  render: () => <AmbientPreview variant="lattice" />,
};
