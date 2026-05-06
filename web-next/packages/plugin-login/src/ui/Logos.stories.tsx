import type { Meta, StoryObj } from '@storybook/react';
import { LogoKnot } from './LogoKnot';
import { LogoTree } from './LogoTree';
import { LogoStars } from './LogoStars';
import { LogoRuneRing } from './LogoRuneRing';
import { LogoFlokk } from './LogoFlokk';
import { LogoStack } from './LogoStack';
import './Logos.stories.css';

const LOGOS = [LogoKnot, LogoTree, LogoStars, LogoRuneRing, LogoFlokk, LogoStack] as const;
const LOGO_NAMES = ['Knot', 'Tree', 'Stars', 'RuneRing', 'Flokk', 'Stack'] as const;

interface LogoGalleryProps {
  size?: number;
  stroke?: number;
  glow?: boolean;
}

function LogoGallery({ size = 56, stroke = 1.6, glow = false }: LogoGalleryProps) {
  return (
    <div className="logos-gallery">
      {LOGOS.map((Logo, i) => (
        <div key={i} className="logos-gallery__item">
          <Logo size={size} stroke={stroke} glow={glow} />
          <span className="logos-gallery__label">{LOGO_NAMES[i]}</span>
        </div>
      ))}
    </div>
  );
}

const meta: Meta<LogoGalleryProps> = {
  title: 'Login/Logos',
  component: LogoGallery,
  parameters: { layout: 'fullscreen' },
  argTypes: {
    size: { control: { type: 'range', min: 24, max: 120, step: 4 } },
    stroke: { control: { type: 'range', min: 0.5, max: 3, step: 0.1 } },
    glow: { control: 'boolean' },
  },
};
export default meta;
type Story = StoryObj<LogoGalleryProps>;

/** All six logo variants side by side — adjust size, stroke, and glow via controls. */
export const Gallery: Story = {
  args: { size: 56, stroke: 1.6, glow: false },
};

/** All logos with glow enabled. */
export const WithGlow: Story = {
  args: { size: 56, stroke: 1.6, glow: true },
};

/** Large format for detailed inspection. */
export const Large: Story = {
  args: { size: 96, stroke: 1.4, glow: false },
};
