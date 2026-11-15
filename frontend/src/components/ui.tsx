import { useId, type ReactNode } from "react";
import { ApiError } from "../api/client";

export function Card({
  title,
  subtitle,
  actions,
  children,
  className,
}: {
  title?: string;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || actions) && (
        <header className="card-head">
          <div>
            {title && <h2 className="card-title">{title}</h2>}
            {subtitle && <p className="card-subtitle">{subtitle}</p>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "fire" | "cool" | "ok" | "warn" | "danger";
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${tone ? `tone-${tone}` : ""}`}>{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  title?: string;
}

export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
  hideLabel,
}: {
  label: string;
  options: SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
  hideLabel?: boolean;
}) {
  const name = useId();
  return (
    <fieldset className="segmented">
      <legend className={hideLabel ? "sr-only" : "control-label"}>{label}</legend>
      <div className="segmented-row">
        {options.map((option) => (
          <label key={option.value} className={`segment ${option.value === value ? "is-active" : ""}`} title={option.title}>
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={option.value === value}
              onChange={() => onChange(option.value)}
            />
            <span>{option.label}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span className="spinner" role="status" aria-live="polite">
      <span className="spinner-ring" aria-hidden="true" />
      <span className="sr-only">{label}</span>
    </span>
  );
}

export function LoadingBlock({ label, height = 220 }: { label: string; height?: number }) {
  return (
    <div className="state-block" style={{ minHeight: height }} role="status" aria-live="polite">
      <span className="spinner-ring" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function describeError(error: unknown): { title: string; detail: string; requestId: string | null } {
  if (error instanceof ApiError) {
    const titles: Record<string, string> = {
      network_error: "Cannot reach the server",
      store_not_ready: "No fire data has been ingested yet",
      rate_limited: "Too many requests",
      invalid_area: "This area cannot be used",
      validation_error: "The request was not accepted",
      not_found: "Not found",
    };
    return {
      title: titles[error.code] ?? "The request failed",
      detail: error.message,
      requestId: error.requestId,
    };
  }
  return { title: "Something went wrong", detail: error instanceof Error ? error.message : "Unknown error", requestId: null };
}

export function ErrorBlock({ error, onRetry, compact }: { error: unknown; onRetry?: () => void; compact?: boolean }) {
  const info = describeError(error);
  return (
    <div className={`state-block is-error ${compact ? "is-compact" : ""}`} role="alert">
      <strong>{info.title}</strong>
      <span>{info.detail}</span>
      {info.requestId && <span className="muted small">Request id {info.requestId}</span>}
      {onRetry && (
        <button type="button" className="btn" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}

export function EmptyBlock({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="state-block is-empty">
      <strong>{title}</strong>
      {children && <span>{children}</span>}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "danger"; children: ReactNode }) {
  return (
    <div className={`notice notice-${tone}`} role={tone === "info" ? "note" : "status"}>
      {children}
    </div>
  );
}

export function Swatch({ color, label, hatched }: { color: string; label: string; hatched?: boolean }) {
  return (
    <span className="swatch">
      <span className={`swatch-chip ${hatched ? "is-hatched" : ""}`} style={{ background: hatched ? undefined : color }} aria-hidden="true" />
      <span>{label}</span>
    </span>
  );
}

export function RampLegend({ gradient, low, high, label }: { gradient: string; low: string; high: string; label: string }) {
  return (
    <div className="ramp" aria-label={`${label}: from ${low} to ${high}`}>
      <span className="ramp-label">{label}</span>
      <span className="ramp-end">{low}</span>
      <span className="ramp-bar" style={{ background: gradient }} aria-hidden="true" />
      <span className="ramp-end">{high}</span>
    </div>
  );
}

export function Badge({ color, children }: { color: string; children: ReactNode }) {
  return (
    <span className="badge" style={{ borderColor: color, color }}>
      {children}
    </span>
  );
}

/** Wraps a query result with consistent loading, error and empty handling. */
export function QueryState<T>({
  query,
  loadingLabel,
  height,
  children,
}: {
  query: { data: T | undefined; isLoading: boolean; isError: boolean; error: unknown; refetch: () => unknown; isFetching?: boolean };
  loadingLabel: string;
  height?: number;
  children: (data: T) => ReactNode;
}) {
  if (query.isError && query.data === undefined) {
    return <ErrorBlock error={query.error} onRetry={() => void query.refetch()} />;
  }
  if (query.data === undefined) {
    return <LoadingBlock label={loadingLabel} height={height} />;
  }
  return <>{children(query.data)}</>;
}
