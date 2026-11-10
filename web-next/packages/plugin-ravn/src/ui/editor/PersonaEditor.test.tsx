import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { load } from 'js-yaml';
import { ravnServices } from '../../testing/mountRavn';
import { makePersonaDetail, makePersonaSummary } from '../../testing/fixtures';
import { PersonaEditor, type PersonaEditorProps } from './PersonaEditor';

const PERSONAS = [
  makePersonaSummary({
    name: 'coder',
    summary: 'Writes code.',
    outcomeEvents: { pass: 'code.changed' },
  }),
  makePersonaSummary(),
];

function mount(props: Partial<PersonaEditorProps> = {}, services = ravnServices()) {
  const onSaved = vi.fn();
  const onCancel = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={services}>
        <PersonaEditor personas={PERSONAS} onSaved={onSaved} onCancel={onCancel} {...props} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return { services, onSaved, onCancel, store: services['ravn.personas'] };
}

function next() {
  fireEvent.click(screen.getByTestId('persona-wizard-next'));
}

function savedYaml(mock: ReturnType<typeof vi.fn>, call = 0): Record<string, unknown> {
  const args = mock.mock.calls[call]!;
  return load(String(args[args.length - 1])) as Record<string, unknown>;
}

describe('PersonaEditor — the wizard', () => {
  it('walks a new persona through every step and saves its YAML', async () => {
    const { store, onSaved } = mount();
    store.createPersonaFromYaml.mockResolvedValue(
      makePersonaDetail({ name: 'migration-reviewer' }),
    );
    expect(screen.getByTestId('persona-start-blank')).toHaveAttribute('aria-pressed', 'true');
    next();

    fireEvent.change(screen.getByTestId('persona-editor-name'), {
      target: { value: 'migration-reviewer' },
    });
    fireEvent.change(screen.getByTestId('persona-editor-description'), {
      target: { value: 'Checks a migration before it runs.' },
    });
    expect(screen.getByTestId('persona-editor-title')).toHaveTextContent('migration-reviewer');
    next();

    fireEvent.click(screen.getByTestId('persona-permission-workspace_write'));
    expect(screen.getByTestId('persona-permission-workspace_write')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    next();

    fireEvent.click(screen.getByTestId('persona-starts-suggest-code.changed'));
    fireEvent.change(screen.getByTestId('persona-starts-add-input'), {
      target: { value: 'Migration Requested!' },
    });
    fireEvent.keyDown(screen.getByTestId('persona-starts-add-input'), { key: 'Enter' });
    const picked = screen.getByTestId('persona-starts-picked');
    expect(picked).toHaveTextContent('Sent by coder on pass');
    expect(picked).toHaveTextContent('migration_requested');
    expect(picked).toHaveTextContent('Nothing sends this yet');
    fireEvent.click(within(picked).getByRole('button', { name: 'Remove migration_requested' }));
    expect(picked).not.toHaveTextContent('migration_requested');
    fireEvent.change(screen.getByTestId('persona-starts-add-input'), {
      target: { value: 'migration.requested' },
    });
    fireEvent.click(screen.getByTestId('persona-starts-add-button'));
    next();

    expect(screen.getByTestId('persona-outcome-event-done')).toHaveValue('migration_reviewer.done');
    const done = screen.getByTestId('persona-outcome-name-done');
    fireEvent.change(done, { target: { value: 'Approve' } });
    fireEvent.blur(done);
    expect(screen.getByTestId('persona-outcome-event-approve')).toHaveValue(
      'migration_reviewer.approve',
    );
    expect(screen.getByTestId('persona-outcome-when-approve')).toHaveAttribute(
      'data-missing',
      'true',
    );
    fireEvent.change(screen.getByTestId('persona-outcome-when-approve'), {
      target: { value: 'It can run twice.' },
    });
    fireEvent.change(screen.getByTestId('persona-outcome-add-input'), {
      target: { value: 'request changes' },
    });
    fireEvent.click(screen.getByTestId('persona-outcome-add-button'));
    fireEvent.change(screen.getByTestId('persona-outcome-event-request_changes'), {
      target: { value: 'migration.changes_requested' },
    });
    fireEvent.change(screen.getByTestId('persona-outcome-when-request_changes'), {
      target: { value: 'Something must change first.' },
    });
    expect(screen.getByTestId('persona-card-preview')).toHaveTextContent('request_changes');
    next();

    fireEvent.change(screen.getByTestId('persona-editor-instructions'), {
      target: { value: 'Review the migration.' },
    });
    next();

    expect(screen.getByTestId('persona-editor-summary')).toHaveTextContent(
      'Something must change first.',
    );
    await waitFor(() => expect(screen.getByTestId('persona-wizard-save')).toBeEnabled());
    fireEvent.click(screen.getByTestId('persona-wizard-save'));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('migration-reviewer'));
    expect(savedYaml(store.createPersonaFromYaml)).toEqual({
      name: 'migration-reviewer',
      description: 'Checks a migration before it runs.',
      permission_mode: 'workspace_write',
      system_prompt_template: 'Review the migration.',
      consumes: { event_types: ['code.changed', 'migration.requested'] },
      produces: {
        event_type: 'migration_reviewer.completed',
        event_type_map: {
          approve: 'migration_reviewer.approve',
          request_changes: 'migration.changes_requested',
        },
        when: {
          approve: 'It can run twice.',
          request_changes: 'Something must change first.',
        },
        schema: {
          verdict: {
            type: 'enum',
            values: ['approve', 'request_changes'],
            description: 'the outcome',
          },
          summary: { type: 'string', description: 'one-line summary' },
        },
      },
    });
  });

  it('starts from a copy of an existing persona, keeping its outcomes and rules', async () => {
    const { store } = mount();
    fireEvent.click(screen.getByTestId('persona-start-coder'));
    await waitFor(() =>
      expect(screen.getByTestId('persona-start-coder')).toHaveAttribute('aria-pressed', 'true'),
    );
    expect(store.getPersonaDocument).toHaveBeenCalledWith('coder');
    expect(screen.getByTestId('persona-editor-title')).toHaveTextContent('New persona');
    expect(screen.getByTestId('persona-card-preview')).toHaveTextContent('needs_changes');
    fireEvent.click(screen.getByTestId('persona-start-blank'));
    expect(screen.getByTestId('persona-card-preview')).not.toHaveTextContent('needs_changes');
  });

  it('says why a persona to start from could not be loaded', async () => {
    const services = ravnServices();
    services['ravn.personas'].getPersonaDocument = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('x'), { detail: 'registry offline' }));
    mount({}, services);
    fireEvent.click(screen.getByTestId('persona-start-coder'));
    expect(await screen.findByText('registry offline')).toBeInTheDocument();
  });

  it('will not save until every problem is fixed, and links each to its step', async () => {
    mount();
    fireEvent.click(screen.getByTestId('persona-step-review'));
    const problems = screen.getByTestId('persona-review-problems');
    expect(problems).toHaveTextContent('Give it a name.');
    expect(problems).toHaveTextContent('Say when to report done.');
    expect(problems).toHaveTextContent('Tell it what to do.');
    expect(screen.getByTestId('persona-wizard-save')).toBeDisabled();
    expect(screen.getByTestId('persona-editor-save')).toBeDisabled();
    fireEvent.click(within(problems).getByRole('button', { name: 'Fix in Instructions' }));
    expect(screen.getByTestId('persona-step-body-instructions')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('persona-step-name'));
    fireEvent.change(screen.getByTestId('persona-editor-name'), { target: { value: 'reviewer' } });
    fireEvent.click(screen.getByTestId('persona-step-review'));
    expect(screen.getByTestId('persona-review-problems')).toHaveTextContent(
      'A persona named reviewer already exists.',
    );
  });

  it('removes an outcome and refuses a duplicate name', () => {
    mount();
    fireEvent.click(screen.getByTestId('persona-step-finishes'));
    fireEvent.change(screen.getByTestId('persona-outcome-add-input'), {
      target: { value: 'blocked' },
    });
    fireEvent.click(screen.getByTestId('persona-outcome-add-button'));
    fireEvent.change(screen.getByTestId('persona-outcome-add-input'), {
      target: { value: 'blocked' },
    });
    fireEvent.click(screen.getByTestId('persona-outcome-add-button'));
    expect(screen.getAllByTestId('persona-outcome')).toHaveLength(2);
    const blocked = screen.getByTestId('persona-outcome-name-blocked');
    fireEvent.change(blocked, { target: { value: 'done' } });
    fireEvent.keyDown(blocked, { key: 'Enter' });
    expect(blocked).toHaveValue('blocked');
    fireEvent.click(screen.getByRole('button', { name: 'Remove done' }));
    expect(screen.getAllByTestId('persona-outcome')).toHaveLength(1);
  });

  it('cancels', () => {
    const { onCancel } = mount();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onCancel).toHaveBeenCalled();
  });
});

describe('PersonaEditor — YAML', () => {
  it('edits the same persona as text and saves exactly what was typed', async () => {
    const { store, onSaved } = mount();
    store.createPersonaFromYaml.mockResolvedValue(makePersonaDetail({ name: 'helper' }));
    fireEvent.click(screen.getByTestId('persona-editor-yaml'));
    const source = screen.getByTestId('persona-editor-source') as HTMLTextAreaElement;
    expect(source.value).toContain('event_type_map:');

    fireEvent.change(source, { target: { value: 'name: [' } });
    expect(screen.getByTestId('persona-editor-status')).toHaveTextContent(/Line \d+/);
    expect(screen.getByTestId('persona-editor-wizard')).toBeDisabled();

    const typed = [
      'name: helper',
      'description: Helps.',
      'system_prompt_template: Help.',
      'produces:',
      '  event_type_map:',
      '    ok: helper.ok',
      '  when:',
      '    ok: It helped.',
      '# a comment the wizard would not keep',
      '',
    ].join('\n');
    fireEvent.change(source, { target: { value: typed } });
    expect(screen.getByTestId('persona-card-preview')).toHaveTextContent('helper');
    await waitFor(() =>
      expect(screen.getByTestId('persona-editor-status')).toHaveTextContent('Valid persona'),
    );
    await waitFor(() => expect(store.checkPersonaYaml).toHaveBeenCalledWith(typed));
    fireEvent.click(screen.getByTestId('persona-editor-save'));
    await waitFor(() => expect(store.createPersonaFromYaml).toHaveBeenCalledWith(typed));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('helper'));
  });

  it('shows what the server refuses and keeps the persona unsaved', async () => {
    const services = ravnServices();
    services['ravn.personas'].checkPersonaYaml = vi
      .fn()
      .mockResolvedValue({ valid: false, errors: ['unsupported field(s): tools'] });
    mount({}, services);
    fireEvent.click(screen.getByTestId('persona-editor-yaml'));
    fireEvent.change(screen.getByTestId('persona-editor-source'), {
      target: {
        value:
          'name: helper\nsystem_prompt_template: Help.\ntools: []\nproduces:\n  event_type_map:\n    ok: helper.ok\n  when:\n    ok: Done.\n',
      },
    });
    expect(await screen.findByText('unsupported field(s): tools')).toBeInTheDocument();
    expect(screen.getByTestId('persona-editor-save')).toBeDisabled();
  });

  it('reads the YAML back into the wizard', () => {
    mount();
    fireEvent.click(screen.getByTestId('persona-editor-yaml'));
    fireEvent.change(screen.getByTestId('persona-editor-source'), {
      target: { value: 'name: helper\ndescription: Helps people.\n' },
    });
    expect(screen.getByTestId('persona-editor-status')).toHaveTextContent(
      'Add at least one way it can finish.',
    );
    fireEvent.click(screen.getByTestId('persona-editor-wizard'));
    fireEvent.click(screen.getByTestId('persona-step-name'));
    expect(screen.getByTestId('persona-editor-description')).toHaveValue('Helps people.');
  });
});

describe('PersonaEditor — editing', () => {
  it('loads the persona, keeps its name and replaces it', async () => {
    const { store, onSaved } = mount({ name: 'reviewer', isBuiltin: true });
    store.replacePersonaYaml.mockResolvedValue(makePersonaDetail());
    expect(await screen.findByTestId('persona-editor-name')).toHaveAttribute('readonly');
    expect(screen.getByRole('status')).toHaveTextContent('reviewer is built in');
    expect(screen.queryByTestId('persona-step-start')).not.toBeInTheDocument();
    fireEvent.change(screen.getByTestId('persona-editor-description'), {
      target: { value: 'Reviews with our house rules.' },
    });
    await waitFor(() => expect(screen.getByTestId('persona-editor-save')).toBeEnabled());
    fireEvent.click(screen.getByTestId('persona-editor-save'));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith('reviewer'));
    expect(store.replacePersonaYaml.mock.calls[0]![0]).toBe('reviewer');
    expect(savedYaml(store.replacePersonaYaml)).toMatchObject({
      name: 'reviewer',
      description: 'Reviews with our house rules.',
      produces: { when: { pass: 'It is ready.' } },
    });
  });

  it('refuses a rename and says to fork instead', async () => {
    mount({ name: 'reviewer' });
    fireEvent.click(await screen.findByTestId('persona-editor-yaml'));
    const source = screen.getByTestId('persona-editor-source') as HTMLTextAreaElement;
    fireEvent.change(source, {
      target: { value: source.value.replace('name: reviewer', 'name: x') },
    });
    expect(screen.getByTestId('persona-editor-status')).toHaveTextContent(
      'This is reviewer; fork it to save it under another name.',
    );
  });

  it('shows why saving failed', async () => {
    const { store } = mount({ name: 'reviewer' });
    store.replacePersonaYaml.mockRejectedValue(
      Object.assign(new Error('x'), { detail: 'read-only registry' }),
    );
    await screen.findByTestId('persona-editor-name');
    await waitFor(() => expect(screen.getByTestId('persona-editor-save')).toBeEnabled());
    fireEvent.click(screen.getByTestId('persona-editor-save'));
    expect(await screen.findByText('read-only registry')).toBeInTheDocument();
  });

  it('says why the persona could not be loaded', async () => {
    const services = ravnServices();
    services['ravn.personas'].getPersonaDocument = vi
      .fn()
      .mockRejectedValue(Object.assign(new Error('x'), { detail: 'Persona not found: reviewer' }));
    mount({ name: 'reviewer' }, services);
    expect(await screen.findByText('Persona not found: reviewer')).toBeInTheDocument();
  });
});
