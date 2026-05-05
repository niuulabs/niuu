import type { Meta, StoryObj } from '@storybook/react';
import { createMockAuditLogService } from '../../adapters/mock';
import { buildWrapper } from './storyWrappers';
import { AuditLogSection } from './AuditLogSection';

const meta: Meta<typeof AuditLogSection> = {
  title: 'Plugins / Tyr / Settings / AuditLogSection',
  component: AuditLogSection,
};
export default meta;

type Story = StoryObj<typeof AuditLogSection>;

export const Data: Story = {
  decorators: [
    (Story) => {
      const Wrapper = buildWrapper({ 'tyr.audit': createMockAuditLogService() });
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
        'tyr.audit': {
          listAuditEntries() {
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
        'tyr.audit': {
          async listAuditEntries() {
            throw new Error('Audit log service unreachable');
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
