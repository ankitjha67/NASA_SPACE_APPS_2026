import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { THEME } from "../lib/colors";
import { saveCanvas } from "../lib/download";

export interface CellInfo {
  title: string;
  lines: string[];
}

export interface ColumnBand {
  start: number;
  end: number;
  label: string;
}

interface HeatGridProps {
  rows: string[];
  columns: number;
  columnTicks: { index: number; label: string }[];
  /** Fill color of a cell, or null when the cell has no data (drawn hatched). */
  cellColor: (row: number, column: number) => string | null;
  cellInfo: (row: number, column: number) => CellInfo | null;
  rowAccent?: (row: number) => string | null;
  selectedRow?: number | null;
  onSelectRow?: (row: number) => void;
  bands?: ColumnBand[];
  ariaLabel: string;
  rowHeight?: number;
  /** When set, a "Save image" button exports the grid as a PNG with this file name stem. */
  downloadName?: string;
}

const LEFT = 52;
const TOP = 34;
const RIGHT = 8;
const BOTTOM = 6;

function hatchPattern(context: CanvasRenderingContext2D, scale: number): CanvasPattern | string {
  const size = 6 * scale;
  const tile = document.createElement("canvas");
  tile.width = size;
  tile.height = size;
  const t = tile.getContext("2d");
  if (!t) return THEME.missing;
  t.fillStyle = THEME.missing;
  t.fillRect(0, 0, size, size);
  t.strokeStyle = "rgba(255,255,255,0.14)";
  t.lineWidth = scale;
  t.beginPath();
  t.moveTo(0, size);
  t.lineTo(size, 0);
  t.stroke();
  return context.createPattern(tile, "repeat") ?? THEME.missing;
}

/** Canvas heat grid with hover and keyboard exploration. Rows are years, columns are weeks or days. */
export function HeatGrid({
  rows,
  columns,
  columnTicks,
  cellColor,
  cellInfo,
  rowAccent,
  selectedRow,
  onSelectRow,
  bands,
  ariaLabel,
  rowHeight = 18,
  downloadName,
}: HeatGridProps) {
  const host = useRef<HTMLDivElement | null>(null);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const [width, setWidth] = useState(0);
  const [active, setActive] = useState<{ row: number; column: number } | null>(null);
  const [pointer, setPointer] = useState<{ x: number; y: number } | null>(null);

  useEffect(() => {
    const element = host.current;
    if (!element) return undefined;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setWidth(Math.floor(entry.contentRect.width));
    });
    observer.observe(element);
    setWidth(Math.floor(element.clientWidth));
    return () => observer.disconnect();
  }, []);

  const height = TOP + rows.length * rowHeight + BOTTOM;
  const cellWidth = width > LEFT + RIGHT ? (width - LEFT - RIGHT) / columns : 0;
  const gap = cellWidth >= 5 ? 1 : 0;

  useEffect(() => {
    const element = canvas.current;
    if (!element || width <= 0 || cellWidth <= 0) return;
    const scale = Math.min(window.devicePixelRatio || 1, 2);
    element.width = Math.round(width * scale);
    element.height = Math.round(height * scale);
    const context = element.getContext("2d");
    if (!context) return;
    context.setTransform(scale, 0, 0, scale, 0, 0);
    context.clearRect(0, 0, width, height);
    context.font = "11px system-ui, sans-serif";
    context.textBaseline = "middle";

    if (bands) {
      for (const band of bands) {
        const segments = band.start <= band.end ? [[band.start, band.end]] : [[band.start, columns - 1], [0, band.end]];
        for (const [from, to] of segments) {
          if (from === undefined || to === undefined) continue;
          const x = LEFT + from * cellWidth;
          const w = (to - from + 1) * cellWidth;
          context.fillStyle = "rgba(255, 138, 61, 0.16)";
          context.fillRect(x, 2, w, 12);
          context.fillStyle = THEME.fire;
          context.fillRect(x, 13, w, 2);
        }
      }
    }

    context.fillStyle = THEME.muted;
    context.textAlign = "left";
    for (const tick of columnTicks) {
      const x = LEFT + tick.index * cellWidth;
      context.fillStyle = THEME.border;
      context.fillRect(x, TOP - 8, 1, 6);
      context.fillStyle = THEME.muted;
      context.fillText(tick.label, x + 3, TOP - 12);
    }

    const pattern = hatchPattern(context, 1);
    for (let r = 0; r < rows.length; r += 1) {
      const y = TOP + r * rowHeight;
      const accent = rowAccent?.(r);
      if (accent) {
        context.fillStyle = accent;
        context.fillRect(LEFT - 8, y + 2, 3, rowHeight - 4);
      }
      context.fillStyle = r === selectedRow ? THEME.text : THEME.muted;
      context.font = r === selectedRow ? "600 11px system-ui, sans-serif" : "11px system-ui, sans-serif";
      context.textAlign = "right";
      context.fillText(rows[r] ?? "", LEFT - 12, y + rowHeight / 2);
      for (let c = 0; c < columns; c += 1) {
        const color = cellColor(r, c);
        context.fillStyle = color ?? pattern;
        context.fillRect(LEFT + c * cellWidth, y, Math.max(cellWidth - gap, 0.6), rowHeight - 1);
      }
    }

    if (selectedRow !== null && selectedRow !== undefined && selectedRow >= 0 && selectedRow < rows.length) {
      context.strokeStyle = THEME.text;
      context.lineWidth = 1.5;
      context.strokeRect(LEFT - 0.5, TOP + selectedRow * rowHeight - 0.5, columns * cellWidth, rowHeight);
    }
    if (active) {
      context.strokeStyle = "#ffffff";
      context.lineWidth = 2;
      context.strokeRect(
        LEFT + active.column * cellWidth - 1,
        TOP + active.row * rowHeight - 1,
        Math.max(cellWidth, 3) + 1,
        rowHeight + 1,
      );
    }
  }, [width, height, cellWidth, gap, rows, columns, columnTicks, cellColor, rowAccent, selectedRow, active, bands, rowHeight]);

  const locate = useCallback(
    (event: MouseEvent<HTMLDivElement>) => {
      const rect = event.currentTarget.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      if (cellWidth <= 0 || x < LEFT || y < TOP) return { cell: null, x, y };
      const column = Math.floor((x - LEFT) / cellWidth);
      const row = Math.floor((y - TOP) / rowHeight);
      if (column < 0 || column >= columns || row < 0 || row >= rows.length) return { cell: null, x, y };
      return { cell: { row, column }, x, y };
    },
    [cellWidth, columns, rows.length, rowHeight],
  );

  const onMove = (event: MouseEvent<HTMLDivElement>) => {
    const { cell, x, y } = locate(event);
    setActive((previous) => (previous?.row === cell?.row && previous?.column === cell?.column ? previous : cell));
    setPointer(cell ? { x, y } : null);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = active ?? { row: selectedRow ?? rows.length - 1, column: 0 };
    let { row, column } = current;
    const jump = event.shiftKey ? Math.max(1, Math.round(columns / 12)) : 1;
    switch (event.key) {
      case "ArrowLeft":
        column = Math.max(0, column - jump);
        break;
      case "ArrowRight":
        column = Math.min(columns - 1, column + jump);
        break;
      case "ArrowUp":
        row = Math.max(0, row - 1);
        break;
      case "ArrowDown":
        row = Math.min(rows.length - 1, row + 1);
        break;
      case "Home":
        column = 0;
        break;
      case "End":
        column = columns - 1;
        break;
      case "Enter":
      case " ":
        onSelectRow?.(row);
        event.preventDefault();
        return;
      case "Escape":
        setActive(null);
        setPointer(null);
        return;
      default:
        return;
    }
    event.preventDefault();
    setActive({ row, column });
    setPointer({ x: LEFT + (column + 0.5) * cellWidth, y: TOP + (row + 1) * rowHeight });
  };

  const info = useMemo(() => (active ? cellInfo(active.row, active.column) : null), [active, cellInfo]);
  const tooltipLeft = pointer ? Math.min(Math.max(pointer.x + 14, 4), Math.max(width - 250, 4)) : 0;
  const tooltipTop = pointer ? pointer.y + 16 : 0;

  const save = () => {
    if (canvas.current && downloadName) void saveCanvas(canvas.current, `${downloadName}.png`, THEME.surface);
  };

  return (
    <div className="heatgrid-wrap">
      {downloadName && (
        <button type="button" className="btn btn-mini save-image" onClick={save} aria-label={`Save image: ${ariaLabel}`}>
          Save image
        </button>
      )}
    <div
      ref={host}
      className="heatgrid"
      style={{ height }}
      tabIndex={0}
      role="application"
      aria-label={`${ariaLabel}. Use the arrow keys to move between cells and Enter to select a year.`}
      onMouseMove={onMove}
      onMouseLeave={() => {
        setActive(null);
        setPointer(null);
      }}
      onClick={(event) => {
        const { cell } = locate(event);
        if (cell) onSelectRow?.(cell.row);
      }}
      onKeyDown={onKeyDown}
      onBlur={() => {
        setActive(null);
        setPointer(null);
      }}
    >
      <canvas ref={canvas} style={{ width, height }} aria-hidden="true" />
      {info && pointer && (
        <div className="heatgrid-tip" style={{ left: tooltipLeft, top: tooltipTop }} aria-hidden="true">
          <strong>{info.title}</strong>
          {info.lines.map((line) => (
            <span key={line}>{line}</span>
          ))}
        </div>
      )}
      <div className="sr-only" aria-live="polite">
        {info ? `${info.title}. ${info.lines.join(". ")}` : ""}
      </div>
    </div>
    </div>
  );
}
