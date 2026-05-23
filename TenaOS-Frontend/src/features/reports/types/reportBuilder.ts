// Types mirroring the TenaAgent service report-builder data shapes.
// Keep in sync with TenaAgent/service/tena_agent_service/report_drafts.py
// and TenaAgent/service/tena_agent_service/report_builder.py.

export type ReportStatus = "draft" | "running" | "ready" | "failed" | "archived";
export type ReportType = "count" | "cohort" | "indicator" | "pivot";
export type EventActor = "user" | "gemma" | "middleware" | "system";
export type ConversationState = "awaiting_name" | "awaiting_question" | "ready";

export type FilterMode = "value_concept" | "value_boolean" | "client_numeric" | "any_value";
export type NumericOperator = "eq" | "gt" | "ge" | "lt" | "le";
export type JoinMode = "and" | "or";
export type GroupDimension = "sex" | "age_group" | "concept_id" | "date_month";
export type DenominatorKind = "ciel_concept" | "encounters_in_range";
export type VisualizationTemplate =
  | "filter_bar"
  | "indicator_rate"
  | "pivot_grouped_bar"
  | "pivot_stacked_bar"
  | "pivot_heatmap"
  | "time_series_bar"
  | "time_series_line"
  | "stacked_time_series"
  | "rate_over_time";

export interface ReportFilter {
  filterId: string;
  conceptId: string;
  label: string;
  filterMode: FilterMode;
  valueConceptId: string | null;
  valueBool: boolean | null;
  operator: NumericOperator | null;
  numericThreshold: number | null;
}

export interface Denominator {
  kind: DenominatorKind;
  conceptId: string | null;
  label: string;
  valueConceptId: string | null;
  valueBool: boolean | null;
  operator: NumericOperator | null;
  numericThreshold: number | null;
}

export interface GroupBy {
  dimension: GroupDimension;
  conceptId: string | null;
  label: string;
}

export interface ReportVisualization {
  template: VisualizationTemplate;
  title: string;
  reason: string;
}

export interface ReportSpec {
  reportType: ReportType;
  dateFrom: string | null;
  dateTo: string | null;
  dateRangeLabel: string | null;
  filters: ReportFilter[];
  joinMode: JoinMode;
  denominator: Denominator | null;
  groupBy: GroupBy[];
  visualization: ReportVisualization | null;
}

export interface ValidationIssue {
  severity: "error" | "warning";
  path: string;
  message: string;
}

export interface ValidationReport {
  issues: ValidationIssue[];
}

export interface CompiledFilterView {
  filterId: string;
  label: string;
  codeUuid: string;
  filterMode: FilterMode;
  valueConceptUuid: string | null;
  valueBool: boolean | null;
  operator: NumericOperator | null;
  numericThreshold: number | null;
}

export interface CompiledQuery {
  reportType: ReportType;
  dateFrom: string | null;
  dateTo: string | null;
  dateRangeLabel: string | null;
  joinMode: JoinMode;
  filters: CompiledFilterView[];
  denominator: CompiledFilterView | null;
  denominatorKind: DenominatorKind | null;
  groupBy: GroupBy[];
  visualization: ReportVisualization | null;
}

export interface BarChartDatum {
  label: string;
  value: number;
}

export interface PivotChartRow {
  label: string;
  values: BarChartDatum[];
}

export interface TimeSeriesPoint {
  period: string;
  value?: number;
  numerator?: number;
  denominator?: number;
  rate?: number | null;
}

export type VisualizationData =
  | {
      xLabel: string;
      yLabel: string;
      bars: BarChartDatum[];
      total?: number | null;
      rate?: number | null;
    }
  | {
      xLabel: string;
      yLabel: string;
      rowLabels: string[];
      colLabels: string[];
      rows: PivotChartRow[];
      maxCell: number;
    }
  | {
      xLabel: string;
      yLabel: string;
      points: TimeSeriesPoint[];
    };

export interface ResultVisualization extends ReportVisualization {
  data: VisualizationData;
}

export interface CohortPatient {
  uuid: string;
  displayName: string;
  gender: string | null;
  birthdate: string | null;
}

export interface CountResult {
  reportType: "count";
  total: number;
  dateFrom: string | null;
  dateTo: string | null;
  dateRangeLabel: string | null;
  joinMode: JoinMode;
  filterCounts: Array<{ filterId: string; label: string; count: number }>;
  visualization?: ResultVisualization | null;
}

export interface CohortResult extends Omit<CountResult, "reportType"> {
  reportType: "cohort";
  patients: CohortPatient[];
  truncated: boolean;
}

export interface IndicatorResult extends Omit<CountResult, "reportType" | "total"> {
  reportType: "indicator";
  numerator: number;
  denominator: number;
  rate: number | null;
  denominatorSource: DenominatorKind | null;
  denominatorLabel: string;
  rateSeries?: TimeSeriesPoint[];
}

export interface PivotGrid {
  rowLabels: string[];
  colLabels: string[];
  cells: number[][];
}

export interface PivotResult extends Omit<CountResult, "reportType" | "total"> {
  reportType: "pivot";
  pivot: PivotGrid;
}

export type ReportResult = CountResult | CohortResult | IndicatorResult | PivotResult;

export interface ReportDraft {
  draftId: string;
  owner: string | null;
  status: ReportStatus;
  name: string;
  description: string | null;
  published?: boolean;
  reportType: ReportType;
  spec: ReportSpec;
  lastQuery: CompiledQuery | null;
  lastResult: ReportResult | null;
  lastRunAt: string | null;
  createdAt: string;
  updatedAt: string;
  conversationState: ConversationState;
  conversationContext: Record<string, unknown>;
}

export interface ReportDraftEvent {
  eventId: string;
  draftId: string;
  timestamp: string;
  actor: EventActor;
  operation: string;
  detail: string;
  payload: Record<string, unknown>;
}

/** Structured op grammar — matches `report_builder_tool_loop.py`. */
export type ReportOperation =
  | { op: "set_report_type"; reportType: ReportType }
  | { op: "set_date_range"; text: string }
  | { op: "set_join_mode"; joinMode: JoinMode }
  | {
      op: "add_filter";
      conceptId: string;
      valueConceptId?: string;
      valueBool?: boolean;
      operator?: NumericOperator;
      numericThreshold?: number;
      label?: string;
    }
  | { op: "remove_filter"; filterId: string }
  | { op: "set_denominator"; kind: "encounters_in_range" }
  | {
      op: "set_denominator";
      kind: "ciel_concept";
      conceptId: string;
      valueConceptId?: string;
      valueBool?: boolean;
      operator?: NumericOperator;
      numericThreshold?: number;
      label?: string;
    }
  | { op: "clear_denominator" }
  | { op: "add_group_by"; dimension: GroupDimension; conceptId?: string; label?: string }
  | { op: "remove_group_by"; dimension: GroupDimension }
  | { op: "set_visualization"; template: VisualizationTemplate; title?: string; reason?: string };

export type ReportAction =
  | { action: "set_report_type"; payload: { reportType: ReportType } }
  | { action: "rerun"; payload: Record<string, never> };
