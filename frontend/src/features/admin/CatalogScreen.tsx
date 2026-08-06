import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  useCreateQualityLevel,
  useEstimateDurations,
  useQualityCatalog,
  useReorderQualityLevels,
  useUpdateDuration,
  useUpdateQualityLevel,
} from "../../api/queries";
import type {
  CatalogDurationTarget,
  CatalogLevel,
  CatalogModePreset,
  DurationEstimateResponse,
  Mode,
  QualityCatalog,
} from "../../api/types";

function modeKeys(level: CatalogLevel): Mode[] {
  return Object.keys(level.modes) as Mode[];
}

function reorderedLabels(order: string[], from: number, to: number): string[] {
  const next = [...order];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

// Every field on this screen auto-saves on change/blur -- no page-wide
// "Save" button, matching this app's existing immediate-action UX
// (Delete/Revoke/Redo already commit immediately elsewhere). See
// ARCHITECTURE.md's "generation" app bullet for the 4 endpoints this
// screen drives and why "removing" something here is always is_active:
// false, never a real delete.
export function CatalogScreen() {
  const catalogQuery = useQualityCatalog();
  const reorderLevels = useReorderQualityLevels();
  const estimateDurations = useEstimateDurations();
  const [pendingDurations, setPendingDurations] = useState<number[]>([]);
  const [draggedLabel, setDraggedLabel] = useState<string | null>(null);
  const [estimateMode, setEstimateMode] = useState<Mode | null>(null);
  const catalog = catalogQuery.data;

  const activeDurationKeys = useMemo(() => {
    const set = new Set<string>();
    if (!catalog) return set;
    for (const row of catalog.durations) {
      for (const [label, byMode] of Object.entries(row.targets)) {
        for (const [mode, target] of Object.entries(byMode)) {
          if (target?.is_active) set.add(`${label}:${mode}`);
        }
      }
    }
    return set;
  }, [catalog]);

  if (catalogQuery.isLoading) {
    return (
      <div className="admin-tab-panel">
        <p className="hint">Loading…</p>
      </div>
    );
  }
  if (catalogQuery.isError || !catalog) {
    return (
      <div className="admin-tab-panel">
        <p className="error">Couldn't load the quality/duration catalog.</p>
      </div>
    );
  }

  const labelOrder = catalog.levels.map((l) => l.label);

  function moveLevel(from: number, to: number) {
    if (to < 0 || to >= labelOrder.length || from === to) return;
    reorderLevels.mutate(reorderedLabels(labelOrder, from, to));
  }

  function handleDropOnRow(targetIndex: number) {
    if (draggedLabel) {
      const fromIndex = labelOrder.indexOf(draggedLabel);
      if (fromIndex !== -1) moveLevel(fromIndex, targetIndex);
    }
    setDraggedLabel(null);
  }

  function openEstimate(mode: Mode) {
    estimateDurations.reset();
    setEstimateMode(mode);
    estimateDurations.mutate({ mode });
  }

  function closeEstimate() {
    setEstimateMode(null);
    estimateDurations.reset();
  }

  const existingSeconds = new Set(catalog.durations.map((d) => d.duration_seconds));
  const visiblePending = pendingDurations.filter((s) => !existingSeconds.has(s));
  const rows = [
    ...catalog.durations,
    ...visiblePending.map((s) => ({ duration_seconds: s, targets: {} }) as QualityCatalog["durations"][number]),
  ].sort((a, b) => a.duration_seconds - b.duration_seconds);

  return (
    <div className="admin-tab-panel">
      <h1>Quality &amp; Duration</h1>
      <p className="hint">
        Quality tiers (megapixels/steps per mode) and which clip lengths each offers. Unchecking
        anything here disables it rather than deleting it — jobs that already used it aren't
        affected. Drag the ⠿ handle or use the arrows to reorder levels — this is also the order
        shown in the quality dropdown on the Generate screen.
      </p>

      <h2>Quality levels</h2>
      <div className="catalog-table-scroll">
        <table className="catalog-table">
          <thead>
            <tr>
              <th rowSpan={2}>Order</th>
              <th rowSpan={2}>Label</th>
              <th rowSpan={2}>Draft</th>
              {catalog.modes.map((mode) => (
                <th key={mode} colSpan={3}>
                  {mode}
                </th>
              ))}
            </tr>
            <tr>
              {catalog.modes.flatMap((mode) => [
                <th key={`${mode}-mp`} className="catalog-mode-subheader">
                  MP
                </th>,
                <th key={`${mode}-steps`} className="catalog-mode-subheader">
                  Steps
                </th>,
                <th key={`${mode}-active`} className="catalog-mode-subheader">
                  Active
                </th>,
              ])}
            </tr>
          </thead>
          <tbody>
            {catalog.levels.map((level, index) => (
              <LevelRow
                key={level.label}
                level={level}
                modes={catalog.modes}
                activeDurationKeys={activeDurationKeys}
                index={index}
                totalLevels={catalog.levels.length}
                onMove={(direction) => moveLevel(index, index + direction)}
                draggedLabel={draggedLabel}
                onDragStart={() => setDraggedLabel(level.label)}
                onDragEnd={() => setDraggedLabel(null)}
                onDrop={() => handleDropOnRow(index)}
              />
            ))}
          </tbody>
        </table>
      </div>
      <AddLevelForm existingLabels={catalog.levels.map((l) => l.label)} modes={catalog.modes} />

      <h2>Duration options</h2>
      <div className="catalog-estimate-toolbar">
        <span className="hint">Estimate from completed jobs, pooled across every level:</span>
        {catalog.modes.map((mode) => (
          <button key={mode} type="button" onClick={() => openEstimate(mode)}>
            {mode}
          </button>
        ))}
      </div>
      <div className="catalog-table-scroll">
        <table className="catalog-table catalog-duration-table">
          <thead>
            <tr>
              <th rowSpan={2}>Seconds</th>
              {catalog.levels.map((level) => (
                <th key={level.label} colSpan={modeKeys(level).length}>
                  {level.label}
                </th>
              ))}
            </tr>
            <tr>
              {catalog.levels.flatMap((level) =>
                modeKeys(level).map((mode) => (
                  <th key={`${level.label}-${mode}`} className="catalog-mode-subheader">
                    {mode}
                  </th>
                )),
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.duration_seconds}>
                <td className="catalog-duration-seconds">{row.duration_seconds}s</td>
                {catalog.levels.flatMap((level) =>
                  modeKeys(level).map((mode) => (
                    <DurationCell
                      key={`${level.label}-${mode}`}
                      label={level.label}
                      mode={mode}
                      durationSeconds={row.duration_seconds}
                      target={row.targets[level.label]?.[mode]}
                    />
                  )),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <AddDurationControl
        onAdd={(values) => setPendingDurations((prev) => [...new Set([...prev, ...values])])}
      />

      {estimateMode && (
        <EstimateModal
          mode={estimateMode}
          levels={catalog.levels}
          result={estimateDurations.data}
          isPending={estimateDurations.isPending}
          isError={estimateDurations.isError}
          onApply={() =>
            estimateDurations.mutate({ mode: estimateMode, apply: true }, { onSuccess: closeEstimate })
          }
          onClose={closeEstimate}
        />
      )}
    </div>
  );
}

function LevelRow({
  level,
  modes,
  activeDurationKeys,
  index,
  totalLevels,
  onMove,
  draggedLabel,
  onDragStart,
  onDragEnd,
  onDrop,
}: {
  level: CatalogLevel;
  modes: Mode[];
  activeDurationKeys: Set<string>;
  index: number;
  totalLevels: number;
  onMove: (direction: -1 | 1) => void;
  draggedLabel: string | null;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDrop: () => void;
}) {
  const updateLevel = useUpdateQualityLevel();
  const [label, setLabel] = useState(level.label);
  const [isDraft, setIsDraft] = useState(level.is_draft);

  useEffect(() => setLabel(level.label), [level.label]);
  useEffect(() => setIsDraft(level.is_draft), [level.is_draft]);

  function handleLabelBlur() {
    const trimmed = label.trim();
    if (trimmed && trimmed !== level.label) {
      updateLevel.mutate({ label: level.label, newLabel: trimmed });
    } else {
      setLabel(level.label);
    }
  }

  return (
    <tr
      className={`catalog-level-row${draggedLabel === level.label ? " catalog-level-row-dragging" : ""}`}
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
    >
      <td className="catalog-order-cell">
        <span
          className="catalog-drag-handle"
          draggable
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
          title="Drag to reorder"
        >
          ⠿
        </span>
        <div className="catalog-order-buttons">
          <button type="button" onClick={() => onMove(-1)} disabled={index === 0} title="Move up">
            ▲
          </button>
          <button
            type="button"
            onClick={() => onMove(1)}
            disabled={index === totalLevels - 1}
            title="Move down"
          >
            ▼
          </button>
        </div>
      </td>
      <td>
        <input
          type="text"
          className="catalog-label-input"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          onBlur={handleLabelBlur}
        />
      </td>
      <td>
        <input
          type="checkbox"
          checked={isDraft}
          onChange={(e) => {
            setIsDraft(e.target.checked);
            updateLevel.mutate({ label: level.label, isDraft: e.target.checked });
          }}
          title="Draft preset (fast/low-step preview, not a final render)"
        />
      </td>
      {modes.map((mode) => (
        <LevelModeCell
          key={mode}
          levelLabel={level.label}
          mode={mode}
          preset={level.modes[mode]}
          hasActiveDurations={activeDurationKeys.has(`${level.label}:${mode}`)}
        />
      ))}
    </tr>
  );
}

function LevelModeCell({
  levelLabel,
  mode,
  preset,
  hasActiveDurations,
}: {
  levelLabel: string;
  mode: Mode;
  preset?: CatalogModePreset;
  hasActiveDurations: boolean;
}) {
  const updateLevel = useUpdateQualityLevel();
  const [megapixels, setMegapixels] = useState(preset ? String(preset.megapixels) : "");
  const [steps, setSteps] = useState(preset ? String(preset.steps) : "");
  const [active, setActive] = useState(preset?.is_active ?? false);
  const [enabling, setEnabling] = useState(false);
  const [newMegapixels, setNewMegapixels] = useState("0.2");
  const [newSteps, setNewSteps] = useState("20");

  useEffect(() => {
    setMegapixels(preset ? String(preset.megapixels) : "");
    setSteps(preset ? String(preset.steps) : "");
    setActive(preset?.is_active ?? false);
    if (preset) setEnabling(false);
  }, [preset]);

  if (!preset) {
    if (!enabling) {
      return (
        <td colSpan={3} className="catalog-mode-cell catalog-cell-empty">
          <button type="button" onClick={() => setEnabling(true)}>
            + enable
          </button>
        </td>
      );
    }
    return (
      <td colSpan={3} className="catalog-mode-cell catalog-cell-empty">
        <div className="catalog-cell-enabling-inner">
          <input
            type="number"
            step="any"
            min="0.05"
            value={newMegapixels}
            onChange={(e) => setNewMegapixels(e.target.value)}
            title="Megapixels"
          />
          <input
            type="number"
            min="1"
            value={newSteps}
            onChange={(e) => setNewSteps(e.target.value)}
            title="Sampler steps"
          />
          <button
            type="button"
            onClick={() => {
              const mp = Number(newMegapixels);
              const st = Number(newSteps);
              if (!(mp > 0) || !(st > 0)) return;
              updateLevel.mutate({
                label: levelLabel,
                modes: { [mode]: { megapixels: mp, steps: st, is_active: true } },
              });
            }}
            disabled={updateLevel.isPending}
          >
            Add
          </button>
        </div>
      </td>
    );
  }

  return (
    <>
      <td className="catalog-mode-cell">
        <input
          type="number"
          step="any"
          min="0.05"
          className="catalog-mp-input"
          value={megapixels}
          onChange={(e) => setMegapixels(e.target.value)}
          onBlur={() => {
            const mp = Number(megapixels);
            if (mp > 0 && mp !== preset.megapixels) {
              updateLevel.mutate({ label: levelLabel, modes: { [mode]: { megapixels: mp } } });
            } else {
              setMegapixels(String(preset.megapixels));
            }
          }}
          title="Megapixels"
        />
      </td>
      <td className="catalog-mode-cell">
        <input
          type="number"
          min="1"
          className="catalog-steps-input"
          value={steps}
          onChange={(e) => setSteps(e.target.value)}
          onBlur={() => {
            const st = Number(steps);
            if (st > 0 && st !== preset.steps) {
              updateLevel.mutate({ label: levelLabel, modes: { [mode]: { steps: st } } });
            } else {
              setSteps(String(preset.steps));
            }
          }}
          title="Sampler steps"
        />
      </td>
      <td className="catalog-mode-cell">
        <input
          type="checkbox"
          checked={active}
          onChange={(e) => {
            setActive(e.target.checked);
            updateLevel.mutate({ label: levelLabel, modes: { [mode]: { is_active: e.target.checked } } });
          }}
          title="Active (unchecking hides it from Generate, doesn't delete it)"
        />
        {active && !hasActiveDurations && (
          <span
            className="catalog-warning-badge"
            title="No active durations -- unusable on Generate until one is enabled below"
          >
            0 durations
          </span>
        )}
      </td>
    </>
  );
}

function AddLevelForm({ existingLabels, modes }: { existingLabels: string[]; modes: Mode[] }) {
  const createLevel = useCreateQualityLevel();
  const [label, setLabel] = useState("");
  const [isDraft, setIsDraft] = useState(false);
  const [included, setIncluded] = useState<Partial<Record<Mode, boolean>>>({});
  const [mp, setMp] = useState<Partial<Record<Mode, string>>>({});
  const [steps, setSteps] = useState<Partial<Record<Mode, string>>>({});
  const [copyFrom, setCopyFrom] = useState(existingLabels[0] ?? "");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const modesPayload: Partial<Record<Mode, { megapixels: number; steps: number }>> = {};
    for (const mode of modes) {
      if (!included[mode]) continue;
      const megapixels = Number(mp[mode]);
      const stepCount = Number(steps[mode]);
      if (!(megapixels > 0) || !(stepCount > 0)) continue;
      modesPayload[mode] = { megapixels, steps: stepCount };
    }
    if (!label.trim() || Object.keys(modesPayload).length === 0) return;

    await createLevel.mutateAsync({
      label: label.trim(),
      isDraft,
      modes: modesPayload,
      copyDurationsFrom: copyFrom || undefined,
    });
    setLabel("");
    setIsDraft(false);
    setIncluded({});
    setMp({});
    setSteps({});
  }

  return (
    <form className="catalog-add-level" onSubmit={handleSubmit}>
      <h3>Add quality level</h3>
      <div className="catalog-add-level-fields">
        <label className="toolbar-control">
          <span>Label</span>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. Ultra"
          />
        </label>
        <label className="catalog-draft-toggle">
          <input type="checkbox" checked={isDraft} onChange={(e) => setIsDraft(e.target.checked)} />
          draft preset
        </label>
        {existingLabels.length > 0 && (
          <label className="toolbar-control">
            <span>Copy durations from</span>
            <select value={copyFrom} onChange={(e) => setCopyFrom(e.target.value)}>
              <option value="">(none — add durations after)</option>
              {existingLabels.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div className="catalog-add-level-modes">
        {modes.map((mode) => (
          <fieldset key={mode} className="catalog-add-level-mode">
            <legend>
              <label>
                <input
                  type="checkbox"
                  checked={!!included[mode]}
                  onChange={(e) => setIncluded((prev) => ({ ...prev, [mode]: e.target.checked }))}
                />
                {mode}
              </label>
            </legend>
            <input
              type="number"
              step="any"
              min="0.05"
              placeholder="megapixels"
              disabled={!included[mode]}
              value={mp[mode] ?? ""}
              onChange={(e) => setMp((prev) => ({ ...prev, [mode]: e.target.value }))}
            />
            <input
              type="number"
              min="1"
              placeholder="steps"
              disabled={!included[mode]}
              value={steps[mode] ?? ""}
              onChange={(e) => setSteps((prev) => ({ ...prev, [mode]: e.target.value }))}
            />
          </fieldset>
        ))}
      </div>
      <button type="submit" className="button-primary" disabled={createLevel.isPending}>
        {createLevel.isPending ? "Creating…" : "Add level"}
      </button>
      {createLevel.isError && (
        <p className="error">
          Couldn't create that level — check the label is unique and every included mode has
          megapixels/steps.
        </p>
      )}
    </form>
  );
}

function DurationCell({
  label,
  mode,
  durationSeconds,
  target,
}: {
  label: string;
  mode: Mode;
  durationSeconds: number;
  target?: CatalogDurationTarget;
}) {
  const updateDuration = useUpdateDuration();
  const [checked, setChecked] = useState(target?.is_active ?? false);
  const [estimate, setEstimate] = useState(
    target?.estimated_render_seconds != null ? String(target.estimated_render_seconds) : "",
  );

  useEffect(() => {
    setChecked(target?.is_active ?? false);
    setEstimate(target?.estimated_render_seconds != null ? String(target.estimated_render_seconds) : "");
  }, [target?.is_active, target?.estimated_render_seconds]);

  function handleToggle(next: boolean) {
    setChecked(next);
    if (!next) {
      updateDuration.mutate({ durationSeconds, targets: [{ label, mode, isActive: false }] });
    }
    // Turning on doesn't save yet -- the estimate field's onBlur does, so a
    // freshly-checked box always has a real render-time estimate attached.
  }

  function handleEstimateBlur() {
    if (!checked) return;
    const value = Number(estimate);
    if (!estimate || Number.isNaN(value) || value <= 0) {
      setChecked(false);
      return;
    }
    updateDuration.mutate({
      durationSeconds,
      targets: [{ label, mode, isActive: true, estimatedRenderSeconds: value }],
    });
  }

  return (
    <td className={`catalog-duration-cell${updateDuration.isError ? " catalog-cell-error" : ""}`}>
      <input type="checkbox" checked={checked} onChange={(e) => handleToggle(e.target.checked)} />
      <input
        type="number"
        min={1}
        className="catalog-duration-estimate"
        value={estimate}
        disabled={!checked}
        onChange={(e) => setEstimate(e.target.value)}
        onBlur={handleEstimateBlur}
        placeholder="secs"
      />
    </td>
  );
}

function AddDurationControl({ onAdd }: { onAdd: (values: number[]) => void }) {
  const [value, setValue] = useState("");

  function handleAdd(e: FormEvent) {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed) return;

    const rangeMatch = trimmed.match(/^(\d+)\s*-\s*(\d+)$/);
    if (rangeMatch) {
      const from = Number(rangeMatch[1]);
      const to = Number(rangeMatch[2]);
      if (from > 0 && to >= from && to - from <= 60) {
        setValue("");
        onAdd(Array.from({ length: to - from + 1 }, (_, i) => from + i));
      }
      return;
    }

    const single = Number(trimmed);
    if (single > 0) {
      setValue("");
      onAdd([single]);
    }
  }

  return (
    <form className="catalog-add-duration" onSubmit={handleAdd}>
      <label className="toolbar-control">
        <span>Add a duration value (seconds, or a range like 21-25)</span>
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="e.g. 22 or 21-25"
        />
      </label>
      <button type="submit">+ Add row</button>
    </form>
  );
}

const LEVEL_COLORS = [
  "#7c5cff",
  "#1a8a4a",
  "#d13c3c",
  "#c9820a",
  "#0a84c9",
  "#a02fa0",
  "#0aa39a",
  "#8a6d3b",
];

function colorForLevel(levelOrder: string[], label: string): string {
  const index = levelOrder.indexOf(label);
  return LEVEL_COLORS[(index >= 0 ? index : 0) % LEVEL_COLORS.length];
}

// seconds = intercept + slope*workload, using whichever segment `workload`
// falls into when the backend selected the piecewise model.
function fittedSecondsForWorkload(result: DurationEstimateResponse, workload: number): number {
  if (result.model === "piecewise" && result.piecewise) {
    const segment =
      workload < result.piecewise.breakpoint_workload
        ? result.piecewise.segment_low
        : result.piecewise.segment_high;
    return segment.intercept + segment.slope * workload;
  }
  if (result.linear) return result.linear.intercept + result.linear.slope * workload;
  return 0;
}

interface ChartPoint {
  x: number;
  y: number;
  color: string;
  title: string;
}

interface ChartLine {
  points: { x: number; y: number }[];
  color?: string;
  dashed?: boolean;
}

function Chart({
  points,
  lines,
  xLabel,
  yLabel,
}: {
  points: ChartPoint[];
  lines: ChartLine[];
  xLabel: string;
  yLabel: string;
}) {
  const width = 360;
  const height = 220;
  const pad = 36;

  const allX = [...points.map((p) => p.x), ...lines.flatMap((l) => l.points.map((p) => p.x))];
  const allY = [0, ...points.map((p) => p.y), ...lines.flatMap((l) => l.points.map((p) => p.y))];
  if (allX.length === 0) {
    return <p className="hint">No data points yet.</p>;
  }
  const xMin = Math.min(...allX);
  const xMax = Math.max(...allX);
  const yMin = 0;
  const yMax = Math.max(...allY) || 1;
  const xSpan = xMax - xMin || 1;
  const ySpan = yMax - yMin || 1;
  const sx = (x: number) => pad + ((x - xMin) / xSpan) * (width - pad * 2);
  const sy = (y: number) => height - pad - ((y - yMin) / ySpan) * (height - pad * 2);

  return (
    <svg className="catalog-chart" viewBox={`0 0 ${width} ${height}`}>
      <line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} className="catalog-chart-axis" />
      <line x1={pad} y1={pad} x2={pad} y2={height - pad} className="catalog-chart-axis" />
      <text x={width / 2} y={height - 6} className="catalog-chart-label" textAnchor="middle">
        {xLabel}
      </text>
      <text x={pad} y={14} className="catalog-chart-label" textAnchor="start">
        {yLabel}
      </text>
      {lines.map((line, i) => (
        <polyline
          key={i}
          points={line.points.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ")}
          fill="none"
          stroke={line.color ?? "var(--text)"}
          strokeWidth={1.5}
          strokeDasharray={line.dashed ? "4 3" : undefined}
        />
      ))}
      {points.map((p, i) => (
        <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r={3.5} fill={p.color}>
          <title>{p.title}</title>
        </circle>
      ))}
    </svg>
  );
}

function EstimateModal({
  mode,
  levels,
  result,
  isPending,
  isError,
  onApply,
  onClose,
}: {
  mode: Mode;
  levels: CatalogLevel[];
  result?: DurationEstimateResponse;
  isPending: boolean;
  isError: boolean;
  onApply: () => void;
  onClose: () => void;
}) {
  const levelOrder = levels.map((l) => l.label);
  const applicableCount = result?.estimates?.filter((e) => e.current_estimate != null).length ?? 0;
  const samples = result?.samples ?? [];

  const durationPoints: ChartPoint[] = samples.map((s) => ({
    x: s.duration_seconds,
    y: s.render_seconds,
    color: colorForLevel(levelOrder, s.label),
    title: `${s.label} · ${s.duration_seconds}s · ${Math.round(s.render_seconds)}s actual`,
  }));
  const workloadPoints: ChartPoint[] = samples.map((s) => ({
    x: s.workload,
    y: s.render_seconds,
    color: colorForLevel(levelOrder, s.label),
    title: `${s.label} · workload ${s.workload.toFixed(1)} · ${Math.round(s.render_seconds)}s actual`,
  }));

  // One duration-space curve per level -- each level's own (megapixels,
  // steps) turns the shared workload model into its own line (or, if the
  // breakpoint workload falls inside this level's plotted range, a kink at
  // whatever duration that corresponds to for THIS level). This is what
  // makes different levels' curves "mirror" each other: same underlying
  // model, scaled by each level's own workload-per-second.
  const durationCurves: ChartLine[] = result?.fit_available
    ? levels
        .filter((level) => level.modes[mode])
        .map((level): ChartLine | null => {
          const preset = level.modes[mode]!;
          const durations = samples.filter((s) => s.label === level.label).map((s) => s.duration_seconds);
          const estimateDurations = (result.estimates ?? [])
            .filter((e) => e.label === level.label)
            .map((e) => e.duration_seconds);
          const allDurations = [...durations, ...estimateDurations];
          if (allDurations.length === 0) return null;
          const dMin = Math.min(...allDurations);
          const dMax = Math.max(...allDurations);
          const xs = [dMin, dMax];
          if (result.model === "piecewise" && result.piecewise) {
            const breakDuration = result.piecewise.breakpoint_workload / (preset.steps * preset.megapixels);
            if (breakDuration > dMin && breakDuration < dMax) xs.splice(1, 0, breakDuration);
          }
          return {
            points: xs.map((d) => ({
              x: d,
              y: fittedSecondsForWorkload(result, preset.steps * preset.megapixels * d),
            })),
            color: colorForLevel(levelOrder, level.label),
          };
        })
        .filter((line): line is ChartLine => line !== null)
    : [];

  const workloadLines: ChartLine[] = [];
  if (result?.fit_available && workloadPoints.length > 0) {
    const xs = workloadPoints.map((p) => p.x);
    const xMin = Math.min(...xs);
    const xMax = Math.max(...xs);
    if (result.model === "piecewise" && result.piecewise) {
      const bp = result.piecewise.breakpoint_workload;
      workloadLines.push({
        points: [
          { x: xMin, y: fittedSecondsForWorkload(result, xMin) },
          { x: bp, y: fittedSecondsForWorkload(result, bp) },
        ],
      });
      workloadLines.push({
        points: [
          { x: bp, y: fittedSecondsForWorkload(result, bp) },
          { x: xMax, y: fittedSecondsForWorkload(result, xMax) },
        ],
        dashed: true,
      });
    } else {
      workloadLines.push({
        points: [
          { x: xMin, y: fittedSecondsForWorkload(result, xMin) },
          { x: xMax, y: fittedSecondsForWorkload(result, xMax) },
        ],
      });
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
          ×
        </button>
        <h2>Estimate durations: {mode}</h2>
        {isPending && !result && <p className="hint">Fitting a curve to completed jobs…</p>}
        {isError && <p className="error">Couldn't compute an estimate.</p>}
        {result && !result.fit_available && (
          <p className="hint">
            Not enough completed jobs yet across any quality level of this mode — need at least 2
            different workload values (have {result.sample_count} completed job
            {result.sample_count === 1 ? "" : "s"} across {result.distinct_workloads} distinct
            workload{result.distinct_workloads === 1 ? "" : "s"}).
          </p>
        )}
        {result?.fit_available && (
          <>
            <p className="hint">
              Fit from {result.sample_count} completed job{result.sample_count === 1 ? "" : "s"}{" "}
              across every level of {mode}, against workload = steps × megapixels × duration_seconds.{" "}
              {result.model === "piecewise" && result.piecewise ? (
                <>
                  Two segments: seconds ≈ {result.piecewise.segment_low.intercept.toFixed(1)} +{" "}
                  {result.piecewise.segment_low.slope.toFixed(3)} × workload below a breakpoint at
                  workload ≈ {result.piecewise.breakpoint_workload.toFixed(1)}, then seconds ≈{" "}
                  {result.piecewise.segment_high.intercept.toFixed(1)} +{" "}
                  {result.piecewise.segment_high.slope.toFixed(3)} × workload above it — a possible
                  sign of a resource cliff (VRAM → system RAM → swap) that different levels hit at
                  different durations but the same workload.
                </>
              ) : (
                <>
                  seconds ≈ {result.linear?.intercept.toFixed(1)} + {result.linear?.slope.toFixed(3)}{" "}
                  × workload.
                </>
              )}
            </p>
            <div className="catalog-chart-row">
              <div>
                <h3>By duration (the "gap" between levels at the same length)</h3>
                <Chart points={durationPoints} lines={durationCurves} xLabel="duration (s)" yLabel="seconds" />
              </div>
              <div>
                <h3>By workload (levels pooled — where the correlation should show)</h3>
                <Chart points={workloadPoints} lines={workloadLines} xLabel="workload" yLabel="seconds" />
              </div>
            </div>
            <div className="catalog-chart-legend">
              {levels
                .filter((level) => level.modes[mode])
                .map((level) => (
                  <span key={level.label} className="catalog-chart-legend-item">
                    <span
                      className="catalog-chart-swatch"
                      style={{ background: colorForLevel(levelOrder, level.label) }}
                    />
                    {level.label}
                  </span>
                ))}
            </div>
            <div className="catalog-table-scroll">
              <table className="catalog-table">
                <thead>
                  <tr>
                    <th>Level</th>
                    <th>Duration</th>
                    <th>Current</th>
                    <th>Fitted</th>
                  </tr>
                </thead>
                <tbody>
                  {result.estimates?.map((e) => (
                    <tr key={`${e.label}-${e.duration_seconds}`}>
                      <td>{e.label}</td>
                      <td>{e.duration_seconds}s</td>
                      <td>{e.current_estimate ?? "—"}</td>
                      <td>{e.fitted_estimate}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="modal-actions">
              <button type="button" className="button-primary" onClick={onApply} disabled={isPending}>
                {isPending
                  ? "Applying…"
                  : `Apply to ${applicableCount} existing duration${applicableCount === 1 ? "" : "s"}`}
              </button>
              <button type="button" onClick={onClose}>
                Cancel
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
