import type { Meta, StoryObj } from '@storybook/react';
import { createMockTyrSettingsService } from '../../adapters/mock';
import { buildWrapper } from './storyWrappers';
import { NotificationsSection } from './NotificationsSection';

const meta: Meta<typeof NotificationsSection> = {
  title: 'Plugins / Tyr / Settings / NotificationsSection',
  component: NotificationsSection,
};
export default meta;

type Story = StoryObj<typeof NotificationsSection>;

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
          getNotificationSettings() {
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
          async getNotificationSettings() {
            throw new Error('Notification service unreachable');
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
