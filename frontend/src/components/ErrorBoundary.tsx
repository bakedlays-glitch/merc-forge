import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/** Top-level error boundary. Catches render-time exceptions in any route
 * so a malformed sidecar response or a downstream bug surfaces as a
 * recoverable error screen instead of a blank page. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Stack to the console for post-mortem debugging via the webview devtools.
    console.error("ErrorBoundary caught:", error, info);
  }

  private handleReset = (): void => {
    this.setState({ error: null });
    window.location.hash = "/";
  };

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="flex h-full items-center justify-center">
          <div className="card max-w-md">
            <h1 className="text-xl font-bold text-rust-400 mb-2">Something went wrong</h1>
            <p className="text-wasteland-200 text-sm mb-4">
              {this.state.error.message || "An unexpected error occurred."}
            </p>
            <button className="btn" onClick={this.handleReset}>
              Back to Hub
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
