import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ReportResultPanel } from "./ReportResultPanel";
import type { IndicatorResult, PivotResult } from "../types/reportBuilder";

describe("ReportResultPanel visualizations", () => {
  it("renders indicator chart metadata alongside the existing tiles", () => {
    const result: IndicatorResult = {
      reportType: "indicator",
      numerator: 2,
      denominator: 5,
      rate: 40,
      denominatorSource: "encounters_in_range",
      denominatorLabel: "Encounters in range",
      dateFrom: "2026-01-01",
      dateTo: "2026-03-31",
      dateRangeLabel: "last quarter",
      joinMode: "and",
      filterCounts: [{ filterId: "f1", label: "Cough", count: 2 }],
      visualization: {
        template: "indicator_rate",
        title: "TB screening rate",
        reason: "Requested by the report builder.",
        data: {
          xLabel: "Metric",
          yLabel: "Patients",
          bars: [
            { label: "Numerator", value: 2 },
            { label: "Denominator", value: 5 },
          ],
          rate: 40,
        },
      },
    };

    render(<ReportResultPanel result={result} status="ready" lastRunAt="2026-05-15T12:00:00Z" />);

    expect(screen.getByText("Indicator report")).toBeInTheDocument();
    expect(screen.getByText("TB screening rate")).toBeInTheDocument();
    expect(screen.getByText("Rate 40.0%")).toBeInTheDocument();
  });

  it("keeps the pivot table while adding a heatmap visualization", () => {
    const result: PivotResult = {
      reportType: "pivot",
      dateFrom: "2026-01-01",
      dateTo: "2026-03-31",
      dateRangeLabel: "last quarter",
      joinMode: "and",
      filterCounts: [{ filterId: "f1", label: "Cough", count: 3 }],
      pivot: {
        rowLabels: ["Female", "Male"],
        colLabels: ["<5", "5-14"],
        cells: [
          [1, 2],
          [0, 3],
        ],
      },
      visualization: {
        template: "pivot_heatmap",
        title: "TB cases by sex and age",
        reason: "Requested by the report builder.",
        data: {
          xLabel: "Group",
          yLabel: "Patients",
          rowLabels: ["Female", "Male"],
          colLabels: ["<5", "5-14"],
          rows: [
            { label: "Female", values: [{ label: "<5", value: 1 }, { label: "5-14", value: 2 }] },
            { label: "Male", values: [{ label: "<5", value: 0 }, { label: "5-14", value: 3 }] },
          ],
          maxCell: 3,
        },
      },
    };

    render(<ReportResultPanel result={result} status="ready" lastRunAt="2026-05-15T12:00:00Z" />);

    expect(screen.getByText("Pivot report")).toBeInTheDocument();
    expect(screen.getByText("TB cases by sex and age")).toBeInTheDocument();
    expect(screen.getAllByText("Female").length).toBeGreaterThan(1);
    expect(screen.getAllByText("5-14").length).toBeGreaterThan(1);
  });

  it("renders a temporal bar chart for month-by-month pivots", () => {
    const result: PivotResult = {
      reportType: "pivot",
      dateFrom: "2026-01-01",
      dateTo: "2026-02-28",
      dateRangeLabel: "last 2 months",
      joinMode: "and",
      filterCounts: [{ filterId: "f1", label: "Weight loss", count: 1 }],
      pivot: {
        rowLabels: ["2026-01", "2026-02"],
        colLabels: ["Count"],
        cells: [[0], [1]],
      },
      visualization: {
        template: "time_series_bar",
        title: "Weight loss by month",
        reason: "Default temporal visualization.",
        data: {
          xLabel: "Month",
          yLabel: "Patients",
          points: [{ period: "2026-01", value: 0 }, { period: "2026-02", value: 1 }],
        },
      },
    };

    render(<ReportResultPanel result={result} status="ready" lastRunAt="2026-05-15T12:00:00Z" />);

    expect(screen.getByText("Weight loss by month")).toBeInTheDocument();
    expect(screen.getAllByText("2026-02").length).toBeGreaterThan(0);
  });

  it("renders rate over time for temporal indicator reports", () => {
    const result: IndicatorResult = {
      reportType: "indicator",
      numerator: 2,
      denominator: 3,
      rate: 66.7,
      denominatorSource: "encounters_in_range",
      denominatorLabel: "Encounters in range",
      dateFrom: "2026-01-01",
      dateTo: "2026-02-28",
      dateRangeLabel: "last 2 months",
      joinMode: "and",
      filterCounts: [{ filterId: "f1", label: "Weight loss", count: 2 }],
      rateSeries: [
        { period: "2026-01", numerator: 1, denominator: 1, rate: 100 },
        { period: "2026-02", numerator: 1, denominator: 2, rate: 50 },
      ],
      visualization: {
        template: "rate_over_time",
        title: "Weight loss rate over time",
        reason: "Requested by the report builder.",
        data: {
          xLabel: "Month",
          yLabel: "Rate (%)",
          points: [
            { period: "2026-01", numerator: 1, denominator: 1, rate: 100 },
            { period: "2026-02", numerator: 1, denominator: 2, rate: 50 },
          ],
        },
      },
    };

    render(<ReportResultPanel result={result} status="ready" lastRunAt="2026-05-15T12:00:00Z" />);

    expect(screen.getByText("Weight loss rate over time")).toBeInTheDocument();
    expect(screen.getByText("2026-02: 1/2")).toBeInTheDocument();
  });

  it("renders line and stacked temporal templates", () => {
    const lineResult: PivotResult = {
      reportType: "pivot",
      dateFrom: "2026-01-01",
      dateTo: "2026-02-28",
      dateRangeLabel: "last 2 months",
      joinMode: "and",
      filterCounts: [{ filterId: "f1", label: "Weight loss", count: 1 }],
      pivot: { rowLabels: ["2026-01", "2026-02"], colLabels: ["Count"], cells: [[0], [1]] },
      visualization: {
        template: "time_series_line",
        title: "Weight loss trend",
        reason: "Requested by the report builder.",
        data: {
          xLabel: "Month",
          yLabel: "Patients",
          points: [{ period: "2026-01", value: 0 }, { period: "2026-02", value: 1 }],
        },
      },
    };

    const { rerender } = render(<ReportResultPanel result={lineResult} status="ready" lastRunAt="2026-05-15T12:00:00Z" />);
    expect(screen.getByText("Weight loss trend")).toBeInTheDocument();

    const stackedResult: PivotResult = {
      ...lineResult,
      pivot: { rowLabels: ["2026-01", "2026-02"], colLabels: ["Female", "Male"], cells: [[1, 0], [1, 1]] },
      visualization: {
        template: "stacked_time_series",
        title: "Weight loss by month and sex",
        reason: "Requested by the report builder.",
        data: {
          xLabel: "Month",
          yLabel: "Patients",
          rowLabels: ["2026-01", "2026-02"],
          colLabels: ["Female", "Male"],
          rows: [
            { label: "2026-01", values: [{ label: "Female", value: 1 }, { label: "Male", value: 0 }] },
            { label: "2026-02", values: [{ label: "Female", value: 1 }, { label: "Male", value: 1 }] },
          ],
          maxCell: 1,
        },
      },
    };

    rerender(<ReportResultPanel result={stackedResult} status="ready" lastRunAt="2026-05-15T12:00:00Z" />);
    expect(screen.getByText("Weight loss by month and sex")).toBeInTheDocument();
  });
});
