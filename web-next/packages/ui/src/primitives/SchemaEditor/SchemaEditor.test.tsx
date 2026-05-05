import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SchemaEditor } from './SchemaEditor';
import type { SchemaEditorValue } from './SchemaEditor';

describe('SchemaEditor', () => {
  it('renders a header row', () => {
    render(<SchemaEditor value={{}} />);
    expect(screen.getByText('Field')).toBeInTheDocument();
    expect(screen.getByText('Type')).toBeInTheDocument();
  });

  it('renders existing fields', () => {
    const value: SchemaEditorValue = { file: 'string', count: 'number' };
    render(<SchemaEditor value={value} />);
    expect(screen.getByDisplayValue('file')).toBeInTheDocument();
    expect(screen.getByDisplayValue('count')).toBeInTheDocument();
  });

  it('renders add button when not readonly', () => {
    render(<SchemaEditor value={{}} />);
    expect(screen.getByText('+ Add field')).toBeInTheDocument();
  });

  it('does not render add button when readonly', () => {
    render(<SchemaEditor value={{}} readonly />);
    expect(screen.queryByText('+ Add field')).not.toBeInTheDocument();
  });

  it('calls onChange with new field when Add is clicked', () => {
    const onChange = vi.fn();
    render(<SchemaEditor value={{}} onChange={onChange} />);
    fireEvent.click(screen.getByText('+ Add field'));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ field: 'string' }));
  });

  it('auto-increments field names when field already exists', () => {
    const onChange = vi.fn();
    render(<SchemaEditor value={{ field: 'string' }} onChange={onChange} />);
    fireEvent.click(screen.getByText('+ Add field'));
    const call = onChange.mock.calls[0]![0] as SchemaEditorValue;
    expect(Object.keys(call)).toContain('field1');
  });

  it('calls onChange when a field key is changed', () => {
    const onChange = vi.fn();
    render(<SchemaEditor value={{ file: 'string' }} onChange={onChange} />);
    const keyInput = screen.getByDisplayValue('file');
    fireEvent.change(keyInput, { target: { value: 'path' } });
    const call = onChange.mock.calls[0]![0] as SchemaEditorValue;
    expect(Object.keys(call)).toContain('path');
    expect(Object.keys(call)).not.toContain('file');
  });

  it('calls onChange when a field type is changed', () => {
    const onChange = vi.fn();
    render(<SchemaEditor value={{ file: 'string' }} onChange={onChange} />);
    const typeSelect = screen.getByDisplayValue('string');
    fireEvent.change(typeSelect, { target: { value: 'number' } });
    expect(onChange).toHaveBeenCalledWith({ file: 'number' });
  });

  it('calls onChange when a field is removed', () => {
    const onChange = vi.fn();
    render(<SchemaEditor value={{ file: 'string' }} onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'Remove field file' }));
    expect(onChange).toHaveBeenCalledWith({});
  });

  it('shows empty message when no fields', () => {
    render(<SchemaEditor value={{}} />);
    expect(screen.getByText('No fields — click Add field to start')).toBeInTheDocument();
  });

  it('shows readonly empty message', () => {
    render(<SchemaEditor value={{}} readonly />);
    expect(screen.getByText('No payload fields')).toBeInTheDocument();
  });

  it('renders type badges in readonly mode', () => {
    render(<SchemaEditor value={{ file: 'string', count: 'number' }} readonly />);
    // In readonly mode, values are shown as badges (spans) rather than selects
    expect(screen.queryAllByRole('combobox').length).toBe(0);
    expect(screen.getByText('string')).toBeInTheDocument();
    expect(screen.getByText('number')).toBeInTheDocument();
  });

  it('renders all supported types in the type select', () => {
    render(<SchemaEditor value={{ x: 'string' }} />);
    const select = screen.getByDisplayValue('string');
    const options = Array.from((select as HTMLSelectElement).options).map((o) => o.value);
    expect(options).toEqual(['string', 'number', 'boolean', 'object', 'array', 'any']);
  });
});
