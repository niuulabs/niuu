import type { Meta, StoryObj } from '@storybook/react';
import { createMockTyrSettingsService } from '../../adapters/mock';
import { buildWrapper } from './storyWrappers';
import { FlockConfigSection } from './FlockConfigSection';

const meta: Meta<typeof FlockConfigSection> = {
  title: 'Plugins / Tyr / Settings / FlockConfigSection',
  component: FlockConfigSection,
};
export default meta;

type Story = StoryObj<typeof FlockConfigSection>;

export const Data: Story = {
  decorators: [
    (Story) => {
      const Wrapper = buildWrapper({ 'tyr.settings': createMockTyrSettingsService() });
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};

export const Loading: Story = {
  decorators: [
    (Story) => {
      const Wrapper = buildWrapper({
        'tyr.settings': {
          getFlockConfig() {
            return new Promise(() => {
              /* never resolves */
            });
          },
        },
      });
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};

export const Error: Story = {
  decorators: [
    (Story) => {
      const Wrapper = buildWrapper({
        'tyr.settings': {
          async getFlockConfig() {
            throw new Error('Flock config service unreachable');
          },
        },
      });
      return (
        <Wrapper>
          <Story />
        </Wrapper>
      );
    },
  ],
};
