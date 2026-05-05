import type { Preview } from '@storybook/react';
import '@niuulabs/design-tokens/tokens.css';
import '@niuulabs/ui/styles.css';
import '@niuulabs/shell/styles.css';
import '@niuulabs/plugin-hello/styles.css';
import '@niuulabs/plugin-login/styles.css';
import '@niuulabs/plugin-mimir/styles.css';
import '@niuulabs/plugin-observatory/styles.css';
import '@niuulabs/plugin-ravn/styles.css';
import '@niuulabs/plugin-tyr/styles.css';
import '@niuulabs/plugin-volundr/styles.css';

const preview: Preview = {
  parameters: {
    backgrounds: {
      default: 'niuu-bg',
      values: [
        { name: 'niuu-bg', value: '#09090b' },
        { name: 'panel', value: '#18101b' },
      ],
    },
    controls: {
      matchers: {
        color: /(background|color)$/i,
        date: /Date$/i,
      },
    },
  },
  globalTypes: {
    theme: {
      description: 'Brand theme',
      defaultValue: 'ice',
      toolbar: {
        title: 'Theme',
        items: [
          { value: 'ice', title: 'Ice' },
          { value: 'amber', title: 'Amber' },
          { value: 'spring', title: 'Spring' },
        ],
        dynamicTitle: true,
      },
    },
  },
  decorators: [
    (Story, ctx) => {
      const theme = (ctx.globals.theme as string) ?? 'ice';
      document.documentElement.dataset.theme = theme;
      return Story();
    },
  ],
};

export default preview;
