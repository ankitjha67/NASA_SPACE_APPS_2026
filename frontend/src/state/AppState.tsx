import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { AreaGeometry } from "../api/types";
import { geometryKey } from "../lib/geo";
import { decodeState, encodeState, type AppState } from "./urlState";

const STORAGE_PREFIX = "agnisetu.area.";
const MAX_STORED_AREAS = 12;

export interface StoredArea {
  geometry: AreaGeometry;
  name: string;
  savedAt: number;
}

export function loadStoredArea(key: string): StoredArea | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredArea;
    if (!parsed || typeof parsed !== "object" || !parsed.geometry) return null;
    return parsed;
  } catch {
    return null;
  }
}

/** Keep a custom area in local storage so its URL key can be resolved on reload. */
export function storeArea(geometry: AreaGeometry, name: string): string {
  const key = geometryKey(geometry);
  try {
    const entries: { key: string; savedAt: number }[] = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const k = window.localStorage.key(i);
      if (k && k.startsWith(STORAGE_PREFIX)) {
        const item = loadStoredArea(k.slice(STORAGE_PREFIX.length));
        entries.push({ key: k, savedAt: item?.savedAt ?? 0 });
      }
    }
    entries.sort((a, b) => a.savedAt - b.savedAt);
    while (entries.length >= MAX_STORED_AREAS) {
      const oldest = entries.shift();
      if (oldest) window.localStorage.removeItem(oldest.key);
    }
    const value: StoredArea = { geometry, name, savedAt: Date.now() };
    window.localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(value));
  } catch {
    // Storage may be full or disabled. The area still works for this session through memory.
  }
  sessionAreas.set(key, { geometry, name, savedAt: Date.now() });
  return key;
}

const sessionAreas = new Map<string, StoredArea>();

export function resolveArea(key: string): StoredArea | null {
  return sessionAreas.get(key) ?? loadStoredArea(key);
}

interface AppStateContextValue {
  state: AppState;
  update: (patch: Partial<AppState>) => void;
}

const AppStateContext = createContext<AppStateContextValue | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AppState>(() => decodeState(window.location.search));

  const update = useCallback((patch: Partial<AppState>) => {
    setState((previous) => {
      const next = { ...previous, ...patch };
      // Choosing a new area clears selections that only make sense for the previous one.
      const areaChanged =
        ("presetId" in patch && patch.presetId !== previous.presetId) ||
        ("customKey" in patch && patch.customKey !== previous.customKey);
      if (areaChanged) {
        next.periodId = "periodId" in patch ? (patch.periodId ?? null) : null;
        next.baseline = "baseline" in patch ? (patch.baseline ?? null) : null;
      }
      return next;
    });
  }, []);

  useEffect(() => {
    const target = `${window.location.pathname}${encodeState(state)}`;
    if (target !== `${window.location.pathname}${window.location.search}`) {
      window.history.replaceState(null, "", target);
    }
  }, [state]);

  useEffect(() => {
    const onPop = () => setState(decodeState(window.location.search));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const value = useMemo(() => ({ state, update }), [state, update]);
  return <AppStateContext.Provider value={value}>{children}</AppStateContext.Provider>;
}

export function useAppState(): AppStateContextValue {
  const value = useContext(AppStateContext);
  if (!value) throw new Error("useAppState must be used inside AppStateProvider");
  return value;
}
