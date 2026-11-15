import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { describeError, ErrorBlock, Notice, QueryState, Segmented, Stat, Swatch } from "../components/ui";

describe("Segmented", () => {
  it("is a labeled radio group that reports changes", async () => {
    const onChange = vi.fn();
    render(
      <Segmented
        label="Metric"
        value="count"
        onChange={onChange}
        options={[
          { value: "count", label: "Detections" },
          { value: "frp", label: "Fire power" },
        ]}
      />,
    );
    const group = screen.getByRole("group", { name: "Metric" });
    const radios = within(group).getAllByRole("radio");
    expect(radios).toHaveLength(2);
    expect(within(group).getByRole("radio", { name: "Detections" })).toBeChecked();
    await userEvent.click(within(group).getByRole("radio", { name: "Fire power" }));
    expect(onChange).toHaveBeenCalledWith("frp");
  });

  it("can be operated from the keyboard", async () => {
    const onChange = vi.fn();
    render(
      <Segmented label="Resolution" value="weekly" onChange={onChange} options={[{ value: "weekly", label: "Weekly" }, { value: "daily", label: "Daily" }]} />,
    );
    await userEvent.tab();
    expect(screen.getByRole("radio", { name: "Weekly" })).toHaveFocus();
    await userEvent.keyboard("{ArrowRight}");
    expect(onChange).toHaveBeenCalledWith("daily");
  });
});

describe("error presentation", () => {
  it("explains API errors with their request id and offers a retry", async () => {
    const retry = vi.fn();
    const error = new ApiError(503, "store_not_ready", "No fire data has been ingested yet.", "abc123def4567890");
    render(<ErrorBlock error={error} onRetry={retry} />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("No fire data has been ingested yet");
    expect(alert).toHaveTextContent("Request id abc123def4567890");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("maps error codes to plain titles and never leaks internals for unknown errors", () => {
    expect(describeError(new ApiError(0, "network_error", "The server could not be reached.", null)).title).toBe("Cannot reach the server");
    expect(describeError(new ApiError(400, "invalid_area", "Only Polygon areas are supported", "r1")).title).toBe("This area cannot be used");
    expect(describeError("boom")).toEqual({ title: "Something went wrong", detail: "Unknown error", requestId: null });
  });
});

describe("QueryState", () => {
  const base = { isLoading: false, isError: false, error: null, refetch: vi.fn() };

  it("shows a loading status while there is no data", () => {
    render(<QueryState query={{ ...base, data: undefined, isLoading: true }} loadingLabel="Building the calendar">{() => <p>ready</p>}</QueryState>);
    expect(screen.getByRole("status")).toHaveTextContent("Building the calendar");
    expect(screen.queryByText("ready")).not.toBeInTheDocument();
  });

  it("renders the data when present, even during a background error", () => {
    render(
      <QueryState query={{ ...base, data: { n: 3 }, isError: true, error: new Error("stale") }} loadingLabel="Loading">
        {(d) => <p>value {d.n}</p>}
      </QueryState>,
    );
    expect(screen.getByText("value 3")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows the error with a working retry when nothing can be displayed", async () => {
    const refetch = vi.fn();
    render(
      <QueryState query={{ ...base, refetch, data: undefined, isError: true, error: new ApiError(429, "rate_limited", "Too many requests.", null) }} loadingLabel="Loading">
        {() => <p>ready</p>}
      </QueryState>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Too many requests");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(refetch).toHaveBeenCalledOnce();
  });
});

describe("small pieces", () => {
  it("renders a stat with its hint and a legend swatch with a text label", () => {
    render(
      <>
        <Stat label="Peak year" value={2016} hint="112.6K detections" tone="fire" />
        <Swatch color="#4fb3ff" label="Observed by VIIRS S-NPP" />
        <Notice tone="warn">Only 62.0% of this area lies inside the ingested countries.</Notice>
      </>,
    );
    expect(screen.getByText("Peak year")).toBeInTheDocument();
    expect(screen.getByText("2016")).toHaveClass("tone-fire");
    expect(screen.getByText("Observed by VIIRS S-NPP")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("62.0%");
  });
});
