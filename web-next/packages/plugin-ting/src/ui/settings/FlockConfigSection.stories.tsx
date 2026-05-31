import type { Meta, StoryObj } from '@storybook/react';
import { createMockTingSettingsService } from '../../adapters/mock';
import { buildWrapper } from './storyWrappers';
import { FlockConfigSection } from './FlockConfigSection';

const meta: Meta<typeof FlockConfigSection> = {
  title: 'Plugins / Ting / Settings / FlockConfigSection',
  component: FlockConfigSection,
};
export default meta;

type Story = StoryObj<typeof FlockConfigSection>;

export const Data: Story = {
  decorators: [
    (Story) => {
      const Wrapper = buildWrapper({ 'ting.settings': createMockTingSettingsService() });
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
        'ting.settings': {
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

export const ErrorState: Story = {
  name: 'Error',
  decorators: [
    (Story) => {
      const Wrapper = buildWrapper({
        'ting.settings': {
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
