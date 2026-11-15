import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Component, lazy, Suspense, type ErrorInfo, type ReactNode } from "react";
import { ApiError } from "./api/client";
import { useMeta } from "./api/hooks";
import { Header, TabBar } from "./components/Header";
import { ErrorBlock, LoadingBlock, Notice } from "./components/ui";
import { AppStateProvider, useAppState } from "./state/AppState";
import { TABS, type TabKey } from "./state/urlState";

// Each tab is its own chunk, so the map library and the chart library load only when needed.
const ExploreTab = lazy(() => import("./tabs/ExploreTab").then((m) => ({ default: m.ExploreTab })));
const CalendarTab = lazy(() => import("./tabs/CalendarTab").then((m) => ({ default: m.CalendarTab })));
const HarmonizationTab = lazy(() => import("./tabs/HarmonizationTab").then((m) => ({ default: m.HarmonizationTab })));
const AnomaliesTab = lazy(() => import("./tabs/AnomaliesTab").then((m) => ({ default: m.AnomaliesTab })));
const WarningTab = lazy(() => import("./tabs/WarningTab").then((m) => ({ default: m.WarningTab })));
const DataTab = lazy(() => import("./tabs/DataTab").then((m) => ({ default: m.DataTab })));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: Infinity,
      gcTime: 30 * 60_000,
      refetchOnWindowFocus: false,
      retry: (failures, error) => {
        // Retry only transient conditions; validation and not found errors will not change.
        if (error instanceof ApiError) return (error.status === 0 || error.status === 503 || error.status === 429) && failures < 2;
        return failures < 1;
      },
      retryDelay: (attempt) => Math.min(8000, 1500 * 2 ** attempt),
    },
  },
});

class ErrorBoundary extends Component<{ children: ReactNode; resetKey: string }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("View failed to render", error, info.componentStack);
  }

  componentDidUpdate(previous: { resetKey: string }) {
    if (previous.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (this.state.error) {
      return <ErrorBlock error={this.state.error} onRetry={() => this.setState({ error: null })} />;
    }
    return this.props.children;
  }
}

const PANELS: Record<TabKey, () => ReactNode> = {
  explore: () => <ExploreTab />,
  calendar: () => <CalendarTab />,
  harmonization: () => <HarmonizationTab />,
  anomalies: () => <AnomaliesTab />,
  warning: () => <WarningTab />,
  data: () => <DataTab />,
};

function Shell() {
  const { state } = useAppState();
  const meta = useMeta();
  const label = TABS.find((t) => t.key === state.tab)?.label ?? "";
  const notReady = meta.data !== undefined && !meta.data.coverage.ready;

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Header />
      <TabBar />
      <main id="main" className="app-main" tabIndex={-1}>
        {meta.isError && meta.data === undefined ? (
          <ErrorBlock error={meta.error} onRetry={() => void meta.refetch()} />
        ) : meta.data === undefined ? (
          <LoadingBlock label="Connecting to the AgniSetu server" height={320} />
        ) : (
          <>
            {notReady && (
              <Notice tone="warn">
                No fire data has been ingested yet. From the backend folder run{" "}
                <code>python -m agnisetu.cli ingest-archive</code> and then <code>python -m agnisetu.cli refresh-nrt</code>.
                The download for India, Pakistan and Argentina is about 1.8 GB and takes ten to thirty minutes.
              </Notice>
            )}
            <section id={`panel-${state.tab}`} role="tabpanel" aria-labelledby={`tab-${state.tab}`} aria-label={label}>
              <ErrorBoundary resetKey={`${state.tab}|${state.presetId}|${state.customKey}`}>
                <Suspense fallback={<LoadingBlock label={`Loading the ${label} view`} height={320} />}>
                  {state.tab === "data" || !notReady ? PANELS[state.tab]() : null}
                </Suspense>
              </ErrorBoundary>
            </section>
          </>
        )}
      </main>
      <footer className="app-footer">
        <span>
          Active fire data: NASA FIRMS (MODIS Collection 6.1, VIIRS Collection 2). Imagery: NASA GIBS. Built for the NASA
          Space Apps Challenge 2026.
        </span>
        <span>
          Estimates are labeled as estimates. Missing data is never shown as zero. Not an ignition forecast.
        </span>
      </footer>
    </div>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppStateProvider>
        <Shell />
      </AppStateProvider>
    </QueryClientProvider>
  );
}
