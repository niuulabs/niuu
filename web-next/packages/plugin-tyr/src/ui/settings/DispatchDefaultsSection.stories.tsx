import type { Meta, StoryObj } from '@storybook/react';
import { createMockTyrSettingsService } from '../../adapters/mock';
import { buildWrapper } from './storyWrappers';
import { DispatchDefaultsSection } from './DispatchDefaultsSection';

const meta: Meta<typeof DispatchDefaultsSection> = {
  title: 'Plugins / Tyr / Settings / DispatchDefaultsSection',
  component: DispatchDefaultsSection,
};
export default meta;

type Story = StoryObj<typeof DispatchDefaultsSection>;

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
          getDispatchDefaults() {
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
          async getDispatchDefaults() {
            throw new Error('Dispatch service unreachable');
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
