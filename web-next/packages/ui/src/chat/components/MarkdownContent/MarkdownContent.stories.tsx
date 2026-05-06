import type { Meta, StoryObj } from '@storybook/react';
import { MarkdownContent } from './MarkdownContent';

const meta: Meta<typeof MarkdownContent> = {
  title: 'Chat/MarkdownContent',
  component: MarkdownContent,
};
export default meta;

type Story = StoryObj<typeof MarkdownContent>;

export const PlainText: Story = {
  args: { content: 'This is plain text with **bold** and `inline code`.' },
};

export const WithCodeBlock: Story = {
  args: { content: '```typescript\nconst x: number = 42;\nconsole.log(x);\n```' },
};

export const WithOutcome: Story = {
  args: { content: '```outcome\nverdict: pass\nsummary: All checks passed\n```' },
};

export const Streaming: Story = {
  args: { content: 'Generating response...', isStreaming: true },
};

export const Complex: Story = {
  args: {
    content: `# Analysis Results

Here is the summary:

- **Item 1**: passed
- **Item 2**: needs review

\`\`\`bash
npm test
\`\`\`

> Note: Review required for item 2.

\`\`\`outcome
verdict: needs_changes
summary: One item needs review
\`\`\``,
  },
};
