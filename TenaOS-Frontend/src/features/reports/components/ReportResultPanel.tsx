import { BarChart3, Users, Activity, ChartPie } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type {
  BarChartDatum,
  CohortResult,
  CountResult,
  IndicatorResult,
  PivotChartRow,
  PivotResult,
  ReportResult,
  ReportSpec,
  ResultVisualization,
  TimeSeriesPoint,
} from "../types/reportBuilder";

interface ReportResultPanelProps {
  result: ReportResult | null | undefined;
  status: string | null | undefined;
  lastRunAt: string | null | undefined;
  spec?: ReportSpec | null | undefined;
}

export function ReportResultPanel({ result, status, lastRunAt, spec }: ReportResultPanelProps) {
  if (!result) {
    return (
      <div className="rounded-2xl border bg-white p-8 text-center text-[hsl(var(--muted-foreground))]">
        <BarChart3 size={24} className="mx-auto mb-2 text-[var(--clinic-slate)]" />
        <div className="text-sm font-semibold text-[var(--clinic-ink)]">No result yet</div>
        <p className="text-xs mt-1">
          Ask the assistant a question, then click "Run report" or say "run it". Status:{" "}
          <span className="font-mono">{status ?? "draft"}</span>
        </p>
      </div>
    );
  }
  const runStamp = lastRunAt ? new Date(lastRunAt).toLocaleString() : "—";

  if (result.reportType === "count") return <CountTile result={result} runStamp={runStamp} spec={spec} />;
  if (result.reportType === "cohort") return <CohortTable result={result} runStamp={runStamp} spec={spec} />;
  if (result.reportType === "indicator") return <IndicatorTiles result={result} runStamp={runStamp} spec={spec} />;
  if (result.reportType === "pivot") return <PivotGrid result={result} runStamp={runStamp} spec={spec} />;
  return null;
}

function CountTile({ result, runStamp, spec }: { result: CountResult; runStamp: string; spec?: ReportSpec | null }) {
  return (
    <div className="rounded-2xl border bg-white p-6">
      <ReportHeader result={result} runStamp={runStamp} spec={spec} />
      <div className="flex items-center gap-2 text-sm text-[hsl(var(--muted-foreground))]">
        <BarChart3 size={14} />
        Count report
      </div>
      <div className="mt-4 text-5xl font-bold text-[var(--clinic-ink)]">{result.total}</div>
      <div className="mt-2 text-sm text-[hsl(var(--muted-foreground))]">
        patients matched in {result.dateRangeLabel ?? formatRange(result)} ({result.joinMode.toUpperCase()} across {result.filterCounts.length} filter
        {result.filterCounts.length === 1 ? "" : "s"})
      </div>
      <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-2">
        {result.filterCounts.map((f) => (
          <div key={f.filterId} className="rounded-xl border bg-[var(--clinic-ice)] px-3 py-2 text-xs">
            <div className="text-[var(--clinic-ink)] font-medium">{f.label}</div>
            <div className="font-mono text-[hsl(var(--muted-foreground))]">{f.count} matches</div>
          </div>
        ))}
      </div>
      <ResultVisualizationBlock visualization={result.visualization} fallbackBars={barsFromFilters(result.filterCounts)} />
    </div>
  );
}

function CohortTable({ result, runStamp, spec }: { result: CohortResult; runStamp: string; spec?: ReportSpec | null }) {
  return (
    <div className="rounded-2xl border bg-white p-4">
      <ReportHeader result={result} runStamp={runStamp} spec={spec} />
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm text-[hsl(var(--muted-foreground))]">
          <Users size={14} />
          Cohort report
        </div>
        <div className="text-xs text-[var(--clinic-slate)]">Last run: {runStamp}</div>
      </div>
      <div className="mt-2 text-3xl font-bold text-[var(--clinic-ink)]">{result.total} patient(s)</div>
      <div className="text-xs text-[hsl(var(--muted-foreground))]">
        {result.dateRangeLabel ?? formatRange(result)} · {result.joinMode.toUpperCase()} across {result.filterCounts.length} filter
        {result.filterCounts.length === 1 ? "" : "s"}
        {result.truncated && " · showing first 500"}
      </div>
      <ResultVisualizationBlock visualization={result.visualization} fallbackBars={barsFromFilters(result.filterCounts)} />
      <div className="mt-4 overflow-x-auto">
        <table className="min-w-full text-xs">
          <thead>
            <tr className="text-left text-[hsl(var(--muted-foreground))]">
              <th className="py-1 pr-3">Patient</th>
              <th className="py-1 pr-3">Gender</th>
              <th className="py-1 pr-3">DOB</th>
              <th className="py-1">UUID</th>
            </tr>
          </thead>
          <tbody>
            {result.patients.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-4 text-center text-[hsl(var(--muted-foreground))]">
                  No patients matched. Try widening the date range or removing a filter.
                </td>
              </tr>
            ) : (
              result.patients.map((p) => (
                <tr key={p.uuid} className="border-t">
                  <td className="py-1 pr-3 text-[var(--clinic-ink)]">{p.displayName || "(no name)"}</td>
                  <td className="py-1 pr-3">{p.gender ?? "—"}</td>
                  <td className="py-1 pr-3">{p.birthdate ?? "—"}</td>
                  <td className="py-1 font-mono text-[hsl(var(--muted-foreground))]">{p.uuid.slice(0, 8)}…</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function IndicatorTiles({ result, runStamp, spec }: { result: IndicatorResult; runStamp: string; spec?: ReportSpec | null }) {
  const rate = result.rate;
  return (
    <div className="rounded-2xl border bg-white p-6">
      <ReportHeader result={result} runStamp={runStamp} spec={spec} />
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm text-[hsl(var(--muted-foreground))]">
          <Activity size={14} />
          Indicator report
        </div>
        <div className="text-xs text-[var(--clinic-slate)]">Last run: {runStamp}</div>
      </div>
      <div className="mt-4 grid grid-cols-1 sm:grid-cols-3 gap-3">
        <Tile label="Matching patients" value={`${result.numerator}`} sub={result.filterCounts.map((f) => f.label).join(" · ")} />
        <Tile label="Population" value={`${result.denominator}`} sub={result.denominatorLabel || result.denominatorSource || "—"} />
        <Tile
          label="Rate"
          value={rate === null ? "—" : `${rate.toFixed(1)}%`}
          sub={result.dateRangeLabel ?? formatRange(result)}
        />
      </div>
      <ResultVisualizationBlock visualization={withIndicatorLabels(result)} />
    </div>
  );
}

function PivotGrid({ result, runStamp, spec }: { result: PivotResult; runStamp: string; spec?: ReportSpec | null }) {
  const { rowLabels, colLabels, cells } = result.pivot;
  const isSingleCountSeries = colLabels.length === 1 && (colLabels[0] ?? "").toLowerCase() === "count";
  return (
    <div className="rounded-2xl border bg-white p-4">
      <ReportHeader result={result} runStamp={runStamp} spec={spec} />
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm text-[hsl(var(--muted-foreground))]">
          <ChartPie size={14} />
          Pivot report
        </div>
        <div className="text-xs text-[var(--clinic-slate)]">Last run: {runStamp}</div>
      </div>
      <div className="mt-2 text-xs text-[hsl(var(--muted-foreground))]">
        {result.dateRangeLabel ?? formatRange(result)} · {rowLabels.length} rows × {colLabels.length} cols
      </div>
      <ResultVisualizationBlock visualization={result.visualization} />
      {isSingleCountSeries ? (
        <PeriodCountTable rowLabels={rowLabels} cells={cells} />
      ) : (
        <PivotMatrixTable rowLabels={rowLabels} colLabels={colLabels} cells={cells} />
      )}
    </div>
  );
}

function PeriodCountTable({ rowLabels, cells }: { rowLabels: string[]; cells: number[][] }) {
  if (rowLabels.length === 0) {
    return (
      <div className="mt-4 rounded-xl border bg-white py-4 text-center text-sm text-[hsl(var(--muted-foreground))]">
        No matching patients for this pivot.
      </div>
    );
  }
  return (
    <div className="mt-4 overflow-x-auto rounded-xl border bg-white">
      <table className="min-w-full text-sm border-collapse">
        <tbody>
          <tr className="bg-[var(--clinic-ice)]">
            <th className="sticky left-0 z-10 bg-[var(--clinic-ice)] px-3 py-3 text-left text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
              Period
            </th>
            {rowLabels.map((period) => (
              <td key={period} className="min-w-24 border-l px-3 py-3 text-center font-medium text-[var(--clinic-ink)]">
                {period}
              </td>
            ))}
          </tr>
          <tr>
            <th className="sticky left-0 z-10 bg-white px-3 py-3 text-left text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">
              Count
            </th>
            {rowLabels.map((period, index) => (
              <td key={period} className="border-l px-3 py-3 text-center font-mono text-base font-semibold text-[var(--clinic-ink)]">
                {cells[index]?.[0] ?? 0}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function PivotMatrixTable({
  rowLabels,
  colLabels,
  cells,
}: {
  rowLabels: string[];
  colLabels: string[];
  cells: number[][];
}) {
  return (
    <div className="mt-4 overflow-hidden rounded-xl border">
      <table className="min-w-full text-sm border-collapse bg-white">
        <thead>
          <tr className="bg-[var(--clinic-ice)]">
            <th className="text-left px-3 py-2 text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">Period</th>
            {colLabels.map((col) => (
              <th key={col} className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">{col}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rowLabels.map((row, rowIndex) => (
            <tr key={row} className="border-t odd:bg-white even:bg-[var(--clinic-ice)]/50">
              <td className="px-3 py-2 font-medium text-[var(--clinic-ink)]">{row}</td>
              {(cells[rowIndex] ?? []).map((cell, colIndex) => (
                <td key={colIndex} className="px-3 py-2 font-mono text-[var(--clinic-ink)]">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
          {rowLabels.length === 0 && (
            <tr>
              <td colSpan={Math.max(1, colLabels.length + 1)} className="py-4 text-center text-[hsl(var(--muted-foreground))]">
                No matching patients for this pivot.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function ReportHeader({
  result,
  runStamp,
  spec,
}: {
  result: ReportResult;
  runStamp: string;
  spec?: ReportSpec | null;
}) {
  const total = "total" in result ? result.total : result.reportType === "indicator" ? result.numerator : result.filterCounts[0]?.count ?? 0;
  return (
    <div className="mb-5 rounded-2xl border bg-[var(--clinic-ice)] p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-[hsl(var(--muted-foreground))]">Report Result</div>
          <div className="mt-1 text-2xl font-bold text-[var(--clinic-ink)]">{total} patient{total === 1 ? "" : "s"}</div>
          <div className="mt-1 text-xs text-[hsl(var(--muted-foreground))]">
            {result.dateRangeLabel ?? formatRange(result)} · {result.reportType.toUpperCase()} · Last run {runStamp}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <FilterBadge label="Date" value={result.dateRangeLabel ?? formatRange(result)} />
          <FilterBadge label="Join" value={result.joinMode.toUpperCase()} />
          {spec?.groupBy?.map((group) => (
            <FilterBadge key={`${group.dimension}-${group.conceptId ?? ""}`} label="Group" value={group.label || group.dimension} />
          ))}
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {spec?.filters
          ? spec.filters.map((filter) => {
              const conceptCount = Array.isArray(filter.conceptIds) ? filter.conceptIds.length : undefined;
              return (
                <FilterBadge
                  key={filter.filterId}
                  label="Clinical filter"
                  value={`${filter.label}${conceptCount && conceptCount > 1 ? ` (${conceptCount} CIEL concepts)` : ""}`}
                />
              );
            })
          : result.filterCounts.map((filter) => (
              <FilterBadge key={filter.filterId} label="Clinical filter" value={filter.label} />
            ))}
      </div>
    </div>
  );
}

function FilterBadge({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-full border bg-white px-3 py-1 text-xs">
      <span className="font-semibold text-[var(--clinic-ink)]">{label}: </span>
      <span className="text-[hsl(var(--muted-foreground))]">{value}</span>
    </div>
  );
}

function ResultVisualizationBlock({
  visualization,
  fallbackBars,
}: {
  visualization: ResultVisualization | null | undefined;
  fallbackBars?: BarChartDatum[];
}) {
  const template = visualization?.template ?? (fallbackBars ? "filter_bar" : null);
  if (!template) return null;

  const title = visualization?.title || "Report visualization";
  const data = visualization?.data;
  const bars = hasBars(data) ? data.bars : fallbackBars;
  const pivotRows = hasPivotRows(data) ? data.rows : [];
  const colLabels = hasPivotRows(data) ? data.colLabels : [];
  const points = hasTimeSeries(data) ? data.points : [];

  if (template === "rate_over_time" && points.length > 0) {
    return <RateOverTimeChart title={title} points={points} />;
  }

  if (template === "indicator_rate" && hasBars(data)) {
    return <IndicatorPieChart title={title} data={data} />;
  }

  if (template === "time_series_bar" && points.length > 0) {
    return <TimeSeriesBarChart title={title} points={points} />;
  }

  if (template === "time_series_line" && points.length > 0) {
    return <TimeSeriesLineChart title={title} points={points} />;
  }

  if (template === "pivot_heatmap" && hasPivotRows(data)) {
    return <PivotHeatmap title={title} rows={data.rows} colLabels={data.colLabels} maxCell={data.maxCell} />;
  }

  if ((template === "pivot_grouped_bar" || template === "pivot_stacked_bar" || template === "stacked_time_series") && pivotRows.length > 0) {
    return <PivotBarChart title={title} rows={pivotRows} colLabels={colLabels} stacked={template !== "pivot_grouped_bar"} />;
  }

  if (bars && bars.length > 0) {
    return <SimpleBarChart title={title} bars={bars} rate={hasBars(data) ? data.rate : undefined} />;
  }

  return null;
}

function withIndicatorLabels(result: IndicatorResult): ResultVisualization | null | undefined {
  const visualization = result.visualization;
  if (!visualization || visualization.template !== "indicator_rate" || !hasBars(visualization.data)) return visualization;
  const numeratorLabel = result.filterCounts.map((filter) => filter.label).filter(Boolean).join(" AND ") || "Matching patients";
  const denominatorLabel = result.denominatorLabel || result.denominatorSource || "Population";
  const numerator = result.numerator;
  const denominator = result.denominator;
  const remainder = Math.max(0, denominator - numerator);
  return {
    ...visualization,
    title: visualization.title === "Indicator numerator, denominator, and rate" ? "Patient proportion" : visualization.title,
    data: {
      ...visualization.data,
      bars: [
        { label: numeratorLabel, value: numerator },
        { label: `Other patients (${denominatorLabel})`, value: remainder },
      ],
      numeratorLabel,
      denominatorLabel,
      numerator,
      denominator,
      remainder,
      rate: result.rate,
    },
  };
}

function TimeSeriesBarChart({ title, points }: { title: string; points: TimeSeriesPoint[] }) {
  return (
    <div className="mt-5 rounded-xl border bg-[var(--clinic-ice)] p-3">
      <div className="text-xs font-semibold text-[var(--clinic-ink)]">{title}</div>
      <div className="mt-3 h-56" role="img" aria-label={title}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={points} margin={{ top: 8, right: 16, left: 0, bottom: 20 }}>
            <CartesianGrid stroke="hsl(var(--border))" vertical={false} />
            <XAxis dataKey="period" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
            <Tooltip formatter={(value) => [`${value}`, "Patients"]} />
            <Bar dataKey="value" name="Patients" fill="var(--clinic-blue)" radius={[6, 6, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function TimeSeriesLineChart({ title, points }: { title: string; points: TimeSeriesPoint[] }) {
  return (
    <div className="mt-5 rounded-xl border bg-[var(--clinic-ice)] p-3">
      <div className="text-xs font-semibold text-[var(--clinic-ink)]">{title}</div>
      <div className="mt-3 h-56" role="img" aria-label={title}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 8, right: 16, left: 0, bottom: 20 }}>
            <CartesianGrid stroke="hsl(var(--border))" vertical={false} />
            <XAxis dataKey="period" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
            <Tooltip formatter={(value) => [`${value}`, "Patients"]} />
            <Line type="monotone" dataKey="value" name="Patients" stroke="var(--clinic-blue)" strokeWidth={2} dot />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function RateOverTimeChart({ title, points }: { title: string; points: TimeSeriesPoint[] }) {
  return (
    <div className="mt-5 rounded-xl border bg-[var(--clinic-ice)] p-3">
      <div className="text-xs font-semibold text-[var(--clinic-ink)]">{title}</div>
      <div className="mt-3 h-60" role="img" aria-label={title}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 8, right: 16, left: 0, bottom: 20 }}>
            <CartesianGrid stroke="hsl(var(--border))" vertical={false} />
            <XAxis dataKey="period" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} domain={[0, 100]} tickFormatter={(value) => `${value}%`} />
            <Tooltip formatter={(value, name) => [typeof value === "number" ? `${value.toFixed(1)}%` : "—", name]} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Line type="monotone" dataKey="rate" name="Rate" stroke="var(--clinic-blue)" strokeWidth={2} dot connectNulls={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-2 grid grid-cols-2 sm:grid-cols-3 gap-1 text-[10px] text-[hsl(var(--muted-foreground))]">
        {points.map((point) => (
          <div key={point.period}>
            {point.period}: {point.numerator ?? 0}/{point.denominator ?? 0}
          </div>
        ))}
      </div>
    </div>
  );
}

function IndicatorPieChart({ title, data }: { title: string; data: Extract<ResultVisualization["data"], { bars: BarChartDatum[] }> }) {
  const slices = data.bars.filter((item) => item.value > 0);
  const rate = typeof data.rate === "number" ? data.rate : null;
  return (
    <div className="mt-5 rounded-xl border bg-[var(--clinic-ice)] p-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-xs font-semibold text-[var(--clinic-ink)]">{title}</div>
          <div className="mt-1 text-xs text-[hsl(var(--muted-foreground))]">
            {data.numeratorLabel ?? "Matching patients"} out of {data.denominatorLabel ?? "population"}
          </div>
        </div>
        {rate !== null && (
          <div className="rounded-full bg-white px-3 py-1 text-sm font-semibold text-[var(--clinic-ink)]">
            {rate.toFixed(1)}%
          </div>
        )}
      </div>
      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-[minmax(220px,280px)_1fr]">
        <div className="h-60" role="img" aria-label={title}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={slices} dataKey="value" nameKey="label" innerRadius={58} outerRadius={88} paddingAngle={2}>
                {slices.map((slice, index) => (
                  <Cell key={slice.label} fill={chartColor(index)} />
                ))}
              </Pie>
              <Tooltip formatter={(value, name) => [`${value}`, name]} />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="flex flex-col justify-center gap-2">
          {data.bars.map((item, index) => (
            <div key={item.label} className="flex items-center justify-between gap-3 rounded-lg border bg-white px-3 py-2 text-sm">
              <span className="flex min-w-0 items-center gap-2">
                <span className="size-3 rounded-full" style={{ backgroundColor: chartColor(index) }} />
                <span className="truncate text-[var(--clinic-ink)]">{item.label}</span>
              </span>
              <span className="font-mono font-semibold text-[var(--clinic-ink)]">{item.value}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function SimpleBarChart({ title, bars, rate }: { title: string; bars: BarChartDatum[]; rate?: number | null }) {
  return (
    <div className="mt-5 rounded-xl border bg-[var(--clinic-ice)] p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-semibold text-[var(--clinic-ink)]">{title}</div>
        {typeof rate === "number" && <div className="text-xs text-[var(--clinic-slate)]">Rate {rate.toFixed(1)}%</div>}
      </div>
      <div className="mt-3 h-56" role="img" aria-label={title}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={bars} margin={{ top: 8, right: 16, left: 0, bottom: 20 }}>
            <CartesianGrid stroke="hsl(var(--border))" vertical={false} />
            <XAxis dataKey="label" tick={{ fontSize: 11 }} interval={0} angle={-20} textAnchor="end" height={54} />
            <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
            <Tooltip formatter={(value) => [`${value}`, "Patients"]} />
            <Bar dataKey="value" name="Patients" fill="var(--clinic-blue)" radius={[6, 6, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function PivotBarChart({
  title,
  rows,
  colLabels,
  stacked,
}: {
  title: string;
  rows: PivotChartRow[];
  colLabels: string[];
  stacked: boolean;
}) {
  const chartData = rows.map((row) => ({
    name: row.label,
    ...Object.fromEntries(row.values.map((value) => [value.label, value.value])),
  }));
  return (
    <div className="mt-5 rounded-xl border bg-[var(--clinic-ice)] p-3">
      <div className="text-xs font-semibold text-[var(--clinic-ink)]">{title}</div>
      <div className="mt-3 h-64" role="img" aria-label={title}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 20 }}>
            <CartesianGrid stroke="hsl(var(--border))" vertical={false} />
            <XAxis dataKey="name" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} allowDecimals={false} />
            <Tooltip />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {colLabels.map((label, index) => (
              <Bar
                key={label}
                dataKey={label}
                stackId={stacked ? "pivot" : undefined}
                fill={chartColor(index)}
                radius={stacked ? undefined : [5, 5, 0, 0]}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function PivotHeatmap({
  title,
  rows,
  colLabels,
  maxCell,
}: {
  title: string;
  rows: PivotChartRow[];
  colLabels: string[];
  maxCell: number;
}) {
  return (
    <div className="mt-5 rounded-xl border bg-[var(--clinic-ice)] p-3">
      <div className="text-xs font-semibold text-[var(--clinic-ink)]">{title}</div>
      <div className="mt-3 overflow-x-auto">
        <div
          className="grid gap-1 text-xs"
          style={{ gridTemplateColumns: `minmax(90px, 1.2fr) repeat(${Math.max(1, colLabels.length)}, minmax(64px, 1fr))` }}
        >
          <div />
          {colLabels.map((label) => (
            <div key={label} className="px-2 py-1 font-medium text-[hsl(var(--muted-foreground))]">
              {label}
            </div>
          ))}
          {rows.map((row) => (
            <div key={row.label} className="contents">
              <div className="px-2 py-2 font-medium text-[var(--clinic-ink)]">
                {row.label}
              </div>
              {row.values.map((value) => (
                <div
                  key={`${row.label}-${value.label}`}
                  className="rounded-md px-2 py-2 text-center font-mono text-[var(--clinic-ink)]"
                  style={{ backgroundColor: heatColor(value.value, maxCell) }}
                >
                  {value.value}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-xl border bg-[var(--clinic-ice)] p-4 text-center">
      <div className="text-xs text-[hsl(var(--muted-foreground))]">{label}</div>
      <div className="mt-1 text-3xl font-bold text-[var(--clinic-ink)]">{value}</div>
      {sub && <div className="mt-1 text-xs text-[hsl(var(--muted-foreground))] truncate">{sub}</div>}
    </div>
  );
}

function formatRange(result: { dateFrom: string | null; dateTo: string | null }): string {
  if (!result.dateFrom && !result.dateTo) return "no date range";
  return `${result.dateFrom ?? "?"} → ${result.dateTo ?? "?"}`;
}

function barsFromFilters(filterCounts: Array<{ label: string; count: number }>): BarChartDatum[] {
  return filterCounts.map((filter) => ({ label: filter.label, value: filter.count }));
}

function hasBars(data: ResultVisualization["data"] | undefined): data is Extract<ResultVisualization["data"], { bars: BarChartDatum[] }> {
  return Boolean(data && "bars" in data && Array.isArray(data.bars));
}

function hasPivotRows(data: ResultVisualization["data"] | undefined): data is Extract<ResultVisualization["data"], { rows: PivotChartRow[] }> {
  return Boolean(data && "rows" in data && Array.isArray(data.rows));
}

function hasTimeSeries(data: ResultVisualization["data"] | undefined): data is Extract<ResultVisualization["data"], { points: TimeSeriesPoint[] }> {
  return Boolean(data && "points" in data && Array.isArray(data.points));
}

function chartColor(index: number): string {
  const colors = ["var(--clinic-blue)", "var(--clinic-teal)", "var(--clinic-slate)", "hsl(var(--muted-foreground))", "var(--clinic-ink)"];
  return colors[index % colors.length];
}

function heatColor(value: number, maxCell: number): string {
  const opacity = maxCell > 0 ? Math.max(0.12, value / maxCell) : 0.08;
  return `color-mix(in srgb, var(--clinic-blue) ${Math.round(opacity * 70)}%, white)`;
}
