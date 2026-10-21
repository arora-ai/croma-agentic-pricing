import { Component, type ReactNode } from "react";

/** Keeps a failure in one panel from taking down the whole app. */
export class Boundary extends Component<{ children: ReactNode; label: string }, { error: string | null }> {
  state = { error: null as string | null };

  static getDerivedStateFromError(error: Error) {
    return { error: error.message };
  }

  render() {
    if (this.state.error === null) return this.props.children;
    return (
      <div className="placeholder">
        <p>{this.props.label} could not be shown.</p>
        <button className="ghost sm" onClick={() => this.setState({ error: null })}>Try again</button>
      </div>
    );
  }
}
