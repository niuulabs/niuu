import { describe, expect, it } from 'vitest';
import {
  exceedsDragThreshold,
  includeCardSubtitle,
  resolveNodeCardVisuals,
  resolveNodeMouseDownAction,
  resourceModeVisuals,
  subworkflowTemplatesSummary,
  triggerCardSubtitle,
  triggerOutputEvent,
  truncateLabel,
  type NodeCardColors,
} from './graphViewLogic';
import type { WorkflowSubworkflowNode } from './workflow';

const colors: NodeCardColors = {
  defaultBackground: 'default-bg',
  defaultBorder: 'default-border',
  errorBackground: 'error-bg',
  errorBorder: 'error-border',
  warningBackground: 'warning-bg',
  warningBorder: 'warning-border',
  selectedBorder: 'selected-border',
};

const colorsWithSelectedBackground: NodeCardColors = {
  ...colors,
  selectedBackground: 'selected-bg',
};

describe('resolveNodeCardVisuals', () => {
  it('uses default background/border and width 1 when unselected with no issue', () => {
    expect(resolveNodeCardVisuals(false, null, colors)).toEqual({
      background: 'default-bg',
      borderColor: 'default-border',
      borderWidth: 1,
    });
  });

  it('prioritizes error over warning for background and border, width 2', () => {
    expect(resolveNodeCardVisuals(false, 'error', colors)).toEqual({
      background: 'error-bg',
      borderColor: 'error-border',
      borderWidth: 2,
    });
  });

  it('uses warning background/border when issue is warning', () => {
    expect(resolveNodeCardVisuals(false, 'warning', colors)).toEqual({
      background: 'warning-bg',
      borderColor: 'warning-border',
      borderWidth: 2,
    });
  });

  it('selection overrides border but not background when no selectedBackground is configured', () => {
    expect(resolveNodeCardVisuals(true, null, colors)).toEqual({
      background: 'default-bg',
      borderColor: 'selected-border',
      borderWidth: 2,
    });
  });

  it('selection still lets an issue drive the background when no selectedBackground is configured', () => {
    expect(resolveNodeCardVisuals(true, 'error', colors)).toEqual({
      background: 'error-bg',
      borderColor: 'selected-border',
      borderWidth: 2,
    });
  });

  it('selection wins the background over any issue when selectedBackground is configured', () => {
    expect(resolveNodeCardVisuals(true, 'error', colorsWithSelectedBackground)).toEqual({
      background: 'selected-bg',
      borderColor: 'selected-border',
      borderWidth: 2,
    });
  });

  it('selection with no issue uses the configured selectedBackground', () => {
    expect(resolveNodeCardVisuals(true, null, colorsWithSelectedBackground)).toEqual({
      background: 'selected-bg',
      borderColor: 'selected-border',
      borderWidth: 2,
    });
  });
});

describe('truncateLabel', () => {
  it('returns the label unchanged when at or under maxLength', () => {
    expect(truncateLabel('exact', 5, 3)).toBe('exact');
    expect(truncateLabel('short', 10, 3)).toBe('short');
  });

  it('truncates and appends an ellipsis when over maxLength', () => {
    expect(truncateLabel('a much longer label', 10, 8)).toBe('a much l…');
  });
});

describe('subworkflowTemplatesSummary', () => {
  function subworkflow(templates: Record<string, string>): WorkflowSubworkflowNode {
    return {
      id: 'sw-1',
      kind: 'subworkflow',
      label: 'Subworkflow',
      maxChildren: 3,
      templates,
      position: { x: 0, y: 0 },
    } as WorkflowSubworkflowNode;
  }

  it('prompts to choose a child workflow when there are no templates', () => {
    expect(subworkflowTemplatesSummary(subworkflow({}))).toBe('choose a child workflow');
  });

  it('prompts to choose a child workflow when templates is undefined', () => {
    const node = subworkflow({});
    delete (node as { templates?: Record<string, string> }).templates;
    expect(subworkflowTemplatesSummary(node)).toBe('choose a child workflow');
  });

  it('uses the sole template alias when present', () => {
    expect(subworkflowTemplatesSummary(subworkflow({ research: 'Research thread' }))).toBe(
      'Research thread',
    );
  });

  it('falls back to a prompt using the template name when the sole alias is empty', () => {
    expect(subworkflowTemplatesSummary(subworkflow({ research: '' }))).toBe(
      'research: choose a child workflow',
    );
  });

  it('joins a short multi-template name list, sorted, when it fits', () => {
    expect(subworkflowTemplatesSummary(subworkflow({ zeta: 'z', alpha: 'a' }))).toBe('alpha, zeta');
  });

  it('collapses to a compact count when the joined name list would not fit', () => {
    expect(
      subworkflowTemplatesSummary(
        subworkflow({
          'a-very-long-template-name-one': 'one',
          'a-very-long-template-name-two': 'two',
          'a-very-long-template-name-three': 'three',
        }),
      ),
    ).toBe('3 child workflows');
  });
});

describe('triggerOutputEvent', () => {
  it('uses children.completed for a subworkflow node', () => {
    expect(triggerOutputEvent({ kind: 'subworkflow' })).toBe('children.completed');
  });

  it('uses the trigger dispatchEvent when set', () => {
    expect(triggerOutputEvent({ kind: 'trigger', dispatchEvent: 'custom.dispatch' })).toBe(
      'custom.dispatch',
    );
  });

  it('falls back to code.requested for a trigger with no dispatchEvent', () => {
    expect(triggerOutputEvent({ kind: 'trigger' })).toBe('code.requested');
  });
});

describe('triggerCardSubtitle', () => {
  it('builds the child-count + templates summary for a subworkflow node', () => {
    const node = {
      kind: 'subworkflow',
      maxChildren: 4,
      templates: { research: 'Research' },
    } as WorkflowSubworkflowNode;
    expect(triggerCardSubtitle(node, 'children.completed')).toBe('1–4 · Research');
  });

  it('uses the output event directly for a plain trigger node', () => {
    expect(triggerCardSubtitle({ kind: 'trigger' }, 'code.requested')).toBe('code.requested');
  });
});

describe('resourceModeVisuals', () => {
  it('describes an ephemeral_local binding', () => {
    expect(resourceModeVisuals('ephemeral_local')).toEqual({
      modeLabel: 'EPHEMERAL',
      modeStroke: 'var(--status-emerald)',
      modeFill: 'color-mix(in srgb, var(--status-emerald) 14%, var(--color-bg-primary))',
    });
  });

  it('describes a registry binding', () => {
    expect(resourceModeVisuals('registry')).toEqual({
      modeLabel: 'REGISTRY',
      modeStroke: 'var(--color-brand)',
      modeFill: 'color-mix(in srgb, var(--color-brand) 14%, var(--color-bg-primary))',
    });
  });
});

describe('resolveNodeMouseDownAction', () => {
  it('ignores a non-primary button click', () => {
    expect(resolveNodeMouseDownAction(1, false, false, false)).toBe('ignore');
  });

  it('ignores a ctrl-modified primary click even in connecting mode', () => {
    expect(resolveNodeMouseDownAction(0, true, true, false)).toBe('ignore');
  });

  it('completes the connection while in connecting mode', () => {
    expect(resolveNodeMouseDownAction(0, false, true, false)).toBe('complete-connect');
  });

  it('only selects a read-only card', () => {
    expect(resolveNodeMouseDownAction(0, false, false, true)).toBe('select-only');
  });

  it('starts a drag for a plain primary click on an editable card', () => {
    expect(resolveNodeMouseDownAction(0, false, false, false)).toBe('start-drag');
  });
});

describe('exceedsDragThreshold', () => {
  it('is false within the default 4px threshold on both axes', () => {
    expect(exceedsDragThreshold(0, 0)).toBe(false);
    expect(exceedsDragThreshold(4, 4)).toBe(false);
    expect(exceedsDragThreshold(-4, -4)).toBe(false);
  });

  it('is true once either axis exceeds the threshold', () => {
    expect(exceedsDragThreshold(5, 0)).toBe(true);
    expect(exceedsDragThreshold(0, -5)).toBe(true);
  });

  it('honors a custom threshold', () => {
    expect(exceedsDragThreshold(6, 0, 10)).toBe(false);
    expect(exceedsDragThreshold(11, 0, 10)).toBe(true);
  });
});

describe('includeCardSubtitle', () => {
  it('prompts to choose a workflow when none is set', () => {
    expect(includeCardSubtitle(undefined, 0)).toBe('choose a workflow · 0 nodes');
  });

  it('pluralizes for more than one node', () => {
    expect(includeCardSubtitle('planning', 3)).toBe('planning · 3 nodes');
  });

  it('does not pluralize for exactly one node', () => {
    expect(includeCardSubtitle('planning', 1)).toBe('planning · 1 node');
  });
});
