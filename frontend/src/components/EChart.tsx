import { BarChart, CustomChart, LineChart, ScatterChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import { init, use as registerModules, type EChartsCoreOption, type EChartsType } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import { THEME } from "../lib/colors";
import { saveDataUrl } from "../lib/download";

registerModules([
  BarChart,
  LineChart,
  ScatterChart,
  CustomChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  DataZoomComponent,
  CanvasRenderer,
]);

export type ChartOption = EChartsCoreOption;

export const AXIS_STYLE = {
  axisLine: { lineStyle: { color: THEME.border } },
  axisTick: { lineStyle: { color: THEME.border } },
  // Labels that would collide are dropped instead of drawn on top of each other.
  axisLabel: { color: THEME.muted, fontSize: 11, hideOverlap: true },
  splitLine: { lineStyle: { color: THEME.grid } },
  nameTextStyle: { color: THEME.muted, fontSize: 11 },
};

/**
 * Plot area for a chart that has a legend. The legend takes the first row, the axis title the
 * second, and the plot starts below both, so the two never share a line.
 */
export const LEGEND_GRID_TOP = 62;

export const TOOLTIP_STYLE = {
  backgroundColor: "#0b0e13",
  borderColor: THEME.border,
  borderWidth: 1,
  textStyle: { color: THEME.text, fontSize: 12 },
  extraCssText: "box-shadow: 0 6px 24px rgba(0,0,0,0.45); border-radius: 6px;",
  confine: true,
};

export const LEGEND_STYLE = {
  // One row at the top left that pages sideways when the entries do not fit.
  type: "scroll",
  top: 0,
  left: 0,
  textStyle: { color: THEME.muted, fontSize: 11 },
  inactiveColor: THEME.faint,
  itemWidth: 14,
  itemHeight: 8,
  pageIconColor: THEME.muted,
  pageIconInactiveColor: THEME.faint,
  pageIconSize: 10,
  pageTextStyle: { color: THEME.muted, fontSize: 10 },
};

export function EChart({
  option,
  height = 300,
  ariaLabel,
  onClick,
  downloadName,
}: {
  option: ChartOption;
  height?: number;
  ariaLabel: string;
  onClick?: (params: { dataIndex: number; seriesName?: string; name: string; value: unknown }) => void;
  /** When set, a "Save image" button exports the chart as a PNG with this file name stem. */
  downloadName?: string;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const chart = useRef<EChartsType | null>(null);
  const clickRef = useRef(onClick);

  useEffect(() => {
    clickRef.current = onClick;
  }, [onClick]);

  useEffect(() => {
    const element = host.current;
    if (!element) return undefined;
    const instance = init(element, undefined, { renderer: "canvas" });
    chart.current = instance;
    instance.on("click", (params) => {
      const p = params as { dataIndex: number; seriesName?: string; name: string; value: unknown };
      clickRef.current?.({ dataIndex: p.dataIndex, seriesName: p.seriesName, name: p.name, value: p.value });
    });
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(element);
    return () => {
      observer.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(
      {
        backgroundColor: "transparent",
        textStyle: { color: THEME.text, fontFamily: "inherit" },
        animationDuration: 250,
        ...option,
      },
      { notMerge: true },
    );
  }, [option]);

  const save = () => {
    const instance = chart.current;
    if (!instance || !downloadName) return;
    saveDataUrl(instance.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: THEME.surface }), `${downloadName}.png`);
  };

  return (
    <div className="chart-wrap">
      {downloadName && (
        <button type="button" className="btn btn-mini save-image" onClick={save} aria-label={`Save image: ${ariaLabel}`}>
          Save image
        </button>
      )}
      <div ref={host} className="chart" style={{ height }} role="img" aria-label={ariaLabel} />
    </div>
  );
}
