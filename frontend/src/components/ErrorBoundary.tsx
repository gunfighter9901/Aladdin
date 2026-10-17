import { Component } from "react";
import type { ReactNode } from "react";

type Props = { children: ReactNode };
type State = { failed: boolean };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    // Visible for internal debugging; no PII is rendered.
    console.error("UI crashed:", error);
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="app">
          <div className="empty" role="alert" style={{ margin: 24 }}>
            <strong>Something went wrong in the interface.</strong>
            <p>Reload the page to continue. Your sources and sessions are kept on the local API.</p>
            <button className="btn-ghost" type="button" onClick={() => window.location.reload()}>
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
