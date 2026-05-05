import type { Meta, StoryObj } from '@storybook/react';
import { RenderedContent } from './RenderedContent';

const meta: Meta<typeof RenderedContent> = {
  title: 'Chat/RenderedContent',
  component: RenderedContent,
};
export default meta;

export const Plain: StoryObj<typeof RenderedContent> = {
  args: { content: 'Here is a response with some text.' },
};

export const WithCode: StoryObj<typeof RenderedContent> = {
  args: { content: 'Result:\n```js\nconsole.log("hello")\n```' },
};
