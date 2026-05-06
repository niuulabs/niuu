import { Component, type ErrorInfo, type ReactNode } from 'react';

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Top-level error boundary for the SPA. Without this, an exception during
 * a render or a commit-phase DOM mismatch (commonly from a translation
 * extension or an unhandled promise rejection inside an effect) leaves
 * React with a half-committed tree, after which any further mutation
 * throws `Failed to execute 'removeChild'/'insertBefore' on 'Node'`. With
 * the boundary, the component subtree resets cleanly and the user gets
 * a recoverable fallback.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Unhandled UI error:', error, info.componentStack);
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (error === null) {
      return this.props.children;
    }
    if (this.props.fallback) {
      return this.props.fallback(error, this.reset);
    }
    return (
      <div
        role="alert"
        style={{
          padding: '2rem',
          margin: '2rem auto',
          maxWidth: '40rem',
          color: '#fafafa',
          backgroundColor: '#18181b',
          border: '1px solid #3f3f46',
          borderRadius: '0.5rem',
          fontFamily: 'Inter, system-ui, sans-serif',
        }}
      >
        <h2 style={{ marginTop: 0 }}>Something went wrong</h2>
        <pre
          style={{
            whiteSpace: 'pre-wrap',
            fontFamily: 'JetBrains Mono, monospace',
            fontSize: '0.85rem',
            color: '#a1a1aa',
          }}
        >
          {error.message}
        </pre>
        <button
          type="button"
          onClick={this.reset}
          style={{
            marginTop: '1rem',
            padding: '0.5rem 1rem',
            background: '#3f3f46',
            color: '#fafafa',
            border: '1px solid #52525b',
            borderRadius: '0.375rem',
            cursor: 'pointer',
            fontFamily: 'inherit',
          }}
        >
          Try again
        </button>
      </div>
    );
  }
}
