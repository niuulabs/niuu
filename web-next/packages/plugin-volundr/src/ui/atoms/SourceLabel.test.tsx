import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SourceLabel } from './SourceLabel';

describe('SourceLabel', () => {
  it('renders dash for null source', () => {
    render(<SourceLabel source={null} />);
    expect(screen.getByTestId('source-label')).toHaveTextContent('—');
  });

  it('renders git source with org/repo@branch', () => {
    render(<SourceLabel source={{ type: 'git', repo: 'niuu/volundr', branch: 'main' }} />);
    expect(screen.getByTestId('source-label')).toHaveTextContent('niuu/volundr');
    expect(screen.getByText('main')).toBeInTheDocument();
  });

  it('renders short mode (repo only)', () => {
    render(<SourceLabel source={{ type: 'git', repo: 'niuu/volundr', branch: 'main' }} short />);
    expect(screen.getByText('volundr')).toBeInTheDocument();
  });

  it('renders local mount path', () => {
    render(<SourceLabel source={{ type: 'local_mount', path: '~/code/niuu' }} />);
    expect(screen.getByText('~/code/niuu')).toBeInTheDocument();
    expect(screen.getByText('⌂')).toBeInTheDocument();
  });
});
