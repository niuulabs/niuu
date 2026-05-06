import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ToolBadge } from './ToolBadge';

describe('ToolBadge', () => {
  it('renders the tool name', () => {
    render(<ToolBadge tool="file" />);
    expect(screen.getByText('file')).toBeInTheDocument();
  });

  it('renders git tool', () => {
    render(<ToolBadge tool="git" />);
    expect(screen.getByText('git')).toBeInTheDocument();
  });

  it('renders terminal tool', () => {
    render(<ToolBadge tool="terminal" />);
    expect(screen.getByText('terminal')).toBeInTheDocument();
  });

  it('renders web tool', () => {
    render(<ToolBadge tool="web" />);
    expect(screen.getByText('web')).toBeInTheDocument();
  });

  it('renders todo tool', () => {
    render(<ToolBadge tool="todo" />);
    expect(screen.getByText('todo')).toBeInTheDocument();
  });
});
