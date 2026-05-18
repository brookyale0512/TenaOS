import { useState } from "react";
import {
  Calendar,
  ChevronDown,
  ChevronRight,
  Filter,
  Layers,
  Plus,
  Sigma,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type {
  GroupDimension,
  JoinMode,
  ReportFilter,
  ReportOperation,
  ReportSpec,
  ReportType,
  VisualizationTemplate,
} from "../types/reportBuilder";

interface ReportSpecPanelProps {
  spec: ReportSpec;
  disabled?: boolean;
  onOperation: (operations: ReportOperation[]) => void;
}

const TYPE_LABELS: Record<ReportType, string> = {
  count: "Count",
  cohort: "Cohort",
  indicator: "Indicator",
  pivot: "Pivot",
};

const GROUP_LABELS: Record<GroupDimension, string> = {
  sex: "Sex",
  age_group: "Age group",
  date_month: "Month",
  concept_id: "CIEL concept",
};

const VISUALIZATION_LABELS: Record<VisualizationTemplate, string> = {
  filter_bar: "Filter bar chart",
  indicator_rate: "Indicator rate chart",
  pivot_grouped_bar: "Pivot grouped bars",
  pivot_stacked_bar: "Pivot stacked bars",
  pivot_heatmap: "Pivot heatmap",
  time_series_bar: "Time series bars",
  time_series_line: "Time series line",
  stacked_time_series: "Stacked time series",
  rate_over_time: "Rate over time",
};

const VISUALIZATION_OPTIONS: Record<ReportType, VisualizationTemplate[]> = {
  count: ["filter_bar"],
  cohort: ["filter_bar"],
  indicator: ["indicator_rate", "filter_bar", "rate_over_time"],
  pivot: ["pivot_grouped_bar", "pivot_stacked_bar", "pivot_heatmap", "time_series_bar", "time_series_line", "stacked_time_series"],
};

export function ReportSpecPanel({ spec, disabled, onOperation }: ReportSpecPanelProps) {
  const [open, setOpen] = useState(false);
  const [dateDraft, setDateDraft] = useState({ source: spec.dateRangeLabel ?? "", value: spec.dateRangeLabel ?? "" });
  const [filterConceptId, setFilterConceptId] = useState("");
  const [filterLabel, setFilterLabel] = useState("");
  const [filterValueMode, setFilterValueMode] = useState("auto");
  const [filterValueConceptId, setFilterValueConceptId] = useState("");
  const [filterOperator, setFilterOperator] = useState("ge");
  const [filterThreshold, setFilterThreshold] = useState("");
  const [denominatorConceptId, setDenominatorConceptId] = useState("");
  const [denominatorLabel, setDenominatorLabel] = useState("");
  const [groupDimension, setGroupDimension] = useState<GroupDimension>("sex");
  const [groupConceptId, setGroupConceptId] = useState("");
  const [groupLabel, setGroupLabel] = useState("");

  const dateLabel = spec.dateRangeLabel
    ? spec.dateRangeLabel
    : spec.dateFrom && spec.dateTo
    ? `${spec.dateFrom} to ${spec.dateTo}`
    : "Date range: any";
  const dateSource = spec.dateRangeLabel ?? "";
  const dateText = dateDraft.source === dateSource ? dateDraft.value : dateSource;

  const filterCount = spec.filters.length;
  const groupCount = spec.groupBy.length;
  const visualizationOptions = VISUALIZATION_OPTIONS[spec.reportType];
  const selectedVisualization = spec.visualization?.template ?? visualizationOptions[0];
  const canAddFilter =
    filterConceptId.trim().length > 0 &&
    (filterValueMode !== "coded" || filterValueConceptId.trim().length > 0) &&
    (filterValueMode !== "numeric" || filterThreshold.trim().length > 0);
  const canAddConceptGroup = groupDimension !== "concept_id" || groupConceptId.trim().length > 0;

  const addFilter = () => {
    if (!canAddFilter) return;
    const operation: Extract<ReportOperation, { op: "add_filter" }> = {
      op: "add_filter",
      conceptId: filterConceptId.trim(),
    };
    if (filterLabel.trim()) operation.label = filterLabel.trim();
    if (filterValueMode === "boolean_true") operation.valueBool = true;
    if (filterValueMode === "boolean_false") operation.valueBool = false;
    if (filterValueMode === "coded") operation.valueConceptId = filterValueConceptId.trim();
    if (filterValueMode === "numeric") {
      operation.operator = filterOperator as never;
      operation.numericThreshold = Number(filterThreshold);
    }
    onOperation([operation]);
    setFilterConceptId("");
    setFilterLabel("");
    setFilterValueMode("auto");
    setFilterValueConceptId("");
    setFilterThreshold("");
  };

  const addDenominatorConcept = () => {
    const conceptId = denominatorConceptId.trim();
    if (!conceptId) return;
    onOperation([
      {
        op: "set_denominator",
        kind: "ciel_concept",
        conceptId,
        ...(denominatorLabel.trim() ? { label: denominatorLabel.trim() } : {}),
      },
    ]);
    setDenominatorConceptId("");
    setDenominatorLabel("");
  };

  const addGroupBy = () => {
    if (!canAddConceptGroup) return;
    onOperation([
      {
        op: "add_group_by",
        dimension: groupDimension,
        ...(groupDimension === "concept_id" ? { conceptId: groupConceptId.trim() } : {}),
        ...(groupLabel.trim() ? { label: groupLabel.trim() } : {}),
      },
    ]);
    setGroupConceptId("");
    setGroupLabel("");
  };

  return (
    <div className="flex max-h-full min-h-0 flex-col overflow-hidden rounded-2xl border border-[#b2e8e2] bg-[#dff6f3]">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left hover:bg-[#caf0eb] transition-colors"
      >
        <div className="flex items-center gap-2">
          <SlidersHorizontal size={15} className="text-[var(--clinic-blue)] shrink-0" />
          <span className="text-sm font-semibold text-[var(--clinic-ink)]">Review report spec</span>
          <Badge variant="secondary" className="text-xs">
            {TYPE_LABELS[spec.reportType]} · {filterCount} filter{filterCount === 1 ? "" : "s"}
          </Badge>
        </div>
        {open ? (
          <ChevronDown size={14} className="shrink-0 text-[hsl(var(--muted-foreground))]" />
        ) : (
          <ChevronRight size={14} className="shrink-0 text-[hsl(var(--muted-foreground))]" />
        )}
      </button>

      {open && (
        <div className="min-h-0 overflow-y-auto overscroll-contain border-t border-[#b2e8e2] p-3">
          <div className="space-y-3">
            <div className="rounded-xl border bg-white p-3 space-y-3">
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                <div className="space-y-1.5">
                  <Label className="text-xs">Report type</Label>
                  <Select
                    value={spec.reportType}
                    disabled={disabled}
                    onValueChange={(value) => onOperation([{ op: "set_report_type", reportType: value as ReportType }])}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(TYPE_LABELS).map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-1.5">
                  <Label className="text-xs">Date range</Label>
                  <div className="flex gap-2">
                    <Input
                      value={dateText}
                      onChange={(event) => setDateDraft({ source: dateSource, value: event.target.value })}
                      placeholder={dateLabel}
                      disabled={disabled}
                    />
                    <Button
                      type="button"
                      variant="secondary"
                      disabled={disabled}
                      onClick={() => onOperation([{ op: "set_date_range", text: dateText.trim() }])}
                    >
                      Apply
                    </Button>
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label className="text-xs">Join filters</Label>
                  <Select
                    value={spec.joinMode}
                    disabled={disabled || spec.filters.length < 2}
                    onValueChange={(value) => onOperation([{ op: "set_join_mode", joinMode: value as JoinMode }])}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="and">AND</SelectItem>
                      <SelectItem value="or">OR</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className="text-xs gap-1 inline-flex items-center">
                  <Calendar size={12} />
                  {dateLabel}
                </Badge>
                {spec.filters.length >= 2 ? (
                  <Badge variant="outline" className="text-xs">
                    Join: {spec.joinMode.toUpperCase()}
                  </Badge>
                ) : null}
                <Badge variant="outline" className="text-xs">
                  Visualization: {VISUALIZATION_LABELS[selectedVisualization]}
                </Badge>
              </div>
            </div>

            <div className="rounded-xl border bg-white p-3 space-y-2">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--clinic-ink)]">
                <SlidersHorizontal size={12} /> Visualization
              </div>
              <Select
                value={selectedVisualization}
                disabled={disabled}
                onValueChange={(value) =>
                  onOperation([
                    {
                      op: "set_visualization",
                      template: value as VisualizationTemplate,
                      title: VISUALIZATION_LABELS[value as VisualizationTemplate],
                    },
                  ])
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {visualizationOptions.map((template) => (
                    <SelectItem key={template} value={template}>
                      {VISUALIZATION_LABELS[template]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <div className="text-xs text-[hsl(var(--muted-foreground))]">
                Chart values are derived from the validated report result, not from model-generated numbers.
              </div>
            </div>

            <div className="rounded-xl border bg-white p-3 space-y-2">
              <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--clinic-ink)]">
                <Filter size={12} /> Filters
              </div>
              {spec.filters.length === 0 ? (
                <div className="text-xs text-[hsl(var(--muted-foreground))]">No filters yet. Ask the assistant or add a CIEL concept below.</div>
              ) : (
                <ul className="space-y-1.5">
                  {spec.filters.map((filter) => (
                    <FilterChip
                      key={filter.filterId}
                      filter={filter}
                      disabled={disabled}
                      onRemove={() => onOperation([{ op: "remove_filter", filterId: filter.filterId }])}
                    />
                  ))}
                </ul>
              )}

              <div className="rounded-lg border bg-[var(--clinic-ice)] p-3 space-y-2">
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-2">
                  <Input
                    value={filterConceptId}
                    onChange={(event) => setFilterConceptId(event.target.value)}
                    placeholder="CIEL concept ID"
                    disabled={disabled}
                  />
                  <Input
                    value={filterLabel}
                    onChange={(event) => setFilterLabel(event.target.value)}
                    placeholder="Label (optional)"
                    disabled={disabled}
                  />
                  <Select value={filterValueMode} disabled={disabled} onValueChange={setFilterValueMode}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="auto">Auto / any value</SelectItem>
                      <SelectItem value="boolean_true">Boolean: true</SelectItem>
                      <SelectItem value="boolean_false">Boolean: false</SelectItem>
                      <SelectItem value="coded">Coded answer</SelectItem>
                      <SelectItem value="numeric">Numeric threshold</SelectItem>
                    </SelectContent>
                  </Select>
                  {filterValueMode === "coded" ? (
                    <Input
                      value={filterValueConceptId}
                      onChange={(event) => setFilterValueConceptId(event.target.value)}
                      placeholder="Answer CIEL ID"
                      disabled={disabled}
                    />
                  ) : filterValueMode === "numeric" ? (
                    <div className="flex gap-2">
                      <Select value={filterOperator} disabled={disabled} onValueChange={setFilterOperator}>
                        <SelectTrigger className="w-24">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="eq">=</SelectItem>
                          <SelectItem value="gt">&gt;</SelectItem>
                          <SelectItem value="ge">&gt;=</SelectItem>
                          <SelectItem value="lt">&lt;</SelectItem>
                          <SelectItem value="le">&lt;=</SelectItem>
                        </SelectContent>
                      </Select>
                      <Input
                        value={filterThreshold}
                        onChange={(event) => setFilterThreshold(event.target.value)}
                        placeholder="Value"
                        type="number"
                        disabled={disabled}
                      />
                    </div>
                  ) : (
                    <Button type="button" disabled={disabled || !canAddFilter} onClick={addFilter}>
                      <Plus size={14} className="mr-1.5" /> Add filter
                    </Button>
                  )}
                </div>
                {(filterValueMode === "coded" || filterValueMode === "numeric") && (
                  <Button type="button" disabled={disabled || !canAddFilter} onClick={addFilter}>
                    <Plus size={14} className="mr-1.5" /> Add filter
                  </Button>
                )}
              </div>
            </div>

            {spec.reportType === "indicator" && (
              <div className="rounded-xl border bg-white p-3 space-y-2">
                <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--clinic-ink)]">
                  <Sigma size={12} /> Denominator
                </div>
                {spec.denominator ? (
                  <div className="flex items-center justify-between gap-2 rounded-lg border bg-[var(--clinic-ice)] px-3 py-2 text-xs">
                    <div>
                      <div className="font-medium text-[var(--clinic-ink)]">
                        {spec.denominator.kind === "encounters_in_range" ? "Encounters in range" : "CIEL concept"}
                      </div>
                      {spec.denominator.conceptId && (
                        <div className="font-mono text-[hsl(var(--muted-foreground))]">
                          {spec.denominator.label} (CIEL {spec.denominator.conceptId})
                        </div>
                      )}
                    </div>
                    <Button
                      size="icon"
                      variant="ghost"
                      disabled={disabled}
                      onClick={() => onOperation([{ op: "clear_denominator" }])}
                      aria-label="Clear denominator"
                    >
                      <Trash2 size={14} />
                    </Button>
                  </div>
                ) : (
                  <div className="text-xs text-[hsl(var(--muted-foreground))]">No denominator set. Indicators require one.</div>
                )}
                <div className="grid grid-cols-1 md:grid-cols-[auto_1fr_1fr_auto] gap-2 rounded-lg border bg-[var(--clinic-ice)] p-3">
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={disabled}
                    onClick={() => onOperation([{ op: "set_denominator", kind: "encounters_in_range" }])}
                  >
                    Use encounters
                  </Button>
                  <Input
                    value={denominatorConceptId}
                    onChange={(event) => setDenominatorConceptId(event.target.value)}
                    placeholder="Denominator CIEL ID"
                    disabled={disabled}
                  />
                  <Input
                    value={denominatorLabel}
                    onChange={(event) => setDenominatorLabel(event.target.value)}
                    placeholder="Label (optional)"
                    disabled={disabled}
                  />
                  <Button
                    type="button"
                    disabled={disabled || !denominatorConceptId.trim()}
                    onClick={addDenominatorConcept}
                  >
                    Apply
                  </Button>
                </div>
              </div>
            )}

            {spec.reportType === "pivot" && (
              <div className="rounded-xl border bg-white p-3 space-y-2">
                <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--clinic-ink)]">
                  <Layers size={12} /> Group by
                </div>
                {groupCount === 0 ? (
                  <div className="text-xs text-[hsl(var(--muted-foreground))]">
                    No grouping yet. Pivot reports need at least one dimension.
                  </div>
                ) : (
                  <ul className="flex flex-wrap gap-1.5">
                    {spec.groupBy.map((group) => (
                      <li key={`${group.dimension}-${group.conceptId ?? ""}`}>
                        <Badge variant="outline" className="text-xs gap-1.5 inline-flex items-center">
                          {GROUP_LABELS[group.dimension] ?? group.dimension}
                          {group.conceptId && <span className="font-mono">{group.conceptId}</span>}
                          {!disabled && (
                            <button
                              type="button"
                              onClick={() => onOperation([{ op: "remove_group_by", dimension: group.dimension }])}
                              className="text-[hsl(var(--destructive))] hover:opacity-80"
                              aria-label={`Remove group_by ${group.dimension}`}
                            >
                              <X size={10} />
                            </button>
                          )}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                )}
                <div className="grid grid-cols-1 md:grid-cols-[12rem_1fr_1fr_auto] gap-2 rounded-lg border bg-[var(--clinic-ice)] p-3">
                  <Select
                    value={groupDimension}
                    disabled={disabled}
                    onValueChange={(value) => setGroupDimension(value as GroupDimension)}
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="sex">Sex</SelectItem>
                      <SelectItem value="age_group">Age group</SelectItem>
                      <SelectItem value="date_month">Month</SelectItem>
                      <SelectItem value="concept_id">CIEL concept</SelectItem>
                    </SelectContent>
                  </Select>
                  <Input
                    value={groupConceptId}
                    onChange={(event) => setGroupConceptId(event.target.value)}
                    placeholder="CIEL ID for concept group"
                    disabled={disabled || groupDimension !== "concept_id"}
                  />
                  <Input
                    value={groupLabel}
                    onChange={(event) => setGroupLabel(event.target.value)}
                    placeholder="Label (optional)"
                    disabled={disabled}
                  />
                  <Button type="button" disabled={disabled || !canAddConceptGroup} onClick={addGroupBy}>
                    <Plus size={14} className="mr-1.5" /> Add group
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function FilterChip({
  filter,
  disabled,
  onRemove,
}: {
  filter: ReportFilter;
  disabled?: boolean;
  onRemove: () => void;
}) {
  const valueLabel = filterValueLabel(filter);
  return (
    <li className="flex items-center justify-between gap-2 rounded-lg border bg-[var(--clinic-ice)] px-3 py-2 text-xs">
      <div className="min-w-0">
        <div className="font-medium text-[var(--clinic-ink)] truncate">{filter.label}</div>
        <div className="font-mono text-[hsl(var(--muted-foreground))]">
          CIEL {filter.conceptId} · {filter.filterMode}
          {valueLabel ? ` · ${valueLabel}` : ""}
        </div>
      </div>
      <Button size="icon" variant="ghost" disabled={disabled} onClick={onRemove} aria-label={`Remove ${filter.label}`}>
        <Trash2 size={14} />
      </Button>
    </li>
  );
}

function filterValueLabel(filter: ReportFilter): string {
  if (filter.filterMode === "value_boolean") return filter.valueBool ? "Yes" : "No";
  if (filter.filterMode === "value_concept" && filter.valueConceptId)
    return `=${filter.valueConceptId}`;
  if (filter.filterMode === "client_numeric" && filter.operator && filter.numericThreshold !== null)
    return `${filter.operator} ${filter.numericThreshold}`;
  return "";
}
