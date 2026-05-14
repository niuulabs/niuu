import type { Meta, StoryObj } from '@storybook/react';
import { createMockTingSettingsService } from '../../adapters/mock';
import { buildWrapper } from './storyWrappers';
import { DispatchDefaultsSection } from './DispatchDefaultsSection';

const meta: Meta<typeof DispatchDefaultsSection> = {
  title: 'Plugins / Ting / Settings / DispatchDefaultsSection',
  component: DispatchDefaultsSection,
};
export default meta;

type Story = StoryObj<typeof DispatchDefaultsSection>;

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

export const ErrorState: Story = {
  name: 'Error',
  decorators: [
    (Story) => {
      const Wrapper = buildWrapper({
        'ting.settings': {
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
