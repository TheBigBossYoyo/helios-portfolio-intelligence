import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ContributionChart } from "@/components/charts/contribution-chart";
import { NavChart } from "@/components/charts/nav-chart";
import { RollingChart } from "@/components/charts/rolling-chart";
import { DataTable, type Column } from "@/components/data-table";
import { MetricTile } from "@/components/metric-tile";
import { Panel, Unavailable } from "@/components/panel";
import { StatusBadge } from "@/components/status-badge";
import { formatPercent } from "@/lib/format";
import { toNavRows, toRollingRows } from "@/lib/series";
import { metric, performanceReport } from "./fixtures";

const report = performanceReport();

describe("MetricTile", () => {
  it("renders the formatted value when the metric is ok", () => {
    render(<MetricTile label="Sharpe" metric={metric(0.4595)} render={(v) => v.toFixed(2)} />);

    expect(screen.getByText("0.46")).toBeInTheDocument();
  });

  it("shows the status, not a number, when the backend could not compute it", () => {
    render(
      <MetricTile label="Sortino" metric={metric(null)} render={(v) => v.toFixed(2)} />,
    );

    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("Insufficient data")).toBeInTheDocument();
    expect(screen.getByText("Not enough history.")).toBeInTheDocument();
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();
  });
});

describe("StatusBadge", () => {
  it("pairs a glyph with a text label so status never rides on color alone", () => {
    render(<StatusBadge status="failed" />);

    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("✕")).toHaveAttribute("aria-hidden", "true");
  });
});

describe("DataTable", () => {
  interface Row {
    name: string;
    value: string;
  }
  const columns: Column<Row>[] = [
    { key: "name", header: "Name", render: (row) => row.name },
    { key: "value", header: "Value", numeric: true, render: (row) => row.value },
  ];

  it("renders headers and rows", () => {
    render(
      <DataTable
        columns={columns}
        rowKey={(row) => row.name}
        rows={[{ name: "AAPL", value: "1.00" }]}
      />,
    );

    expect(screen.getByRole("columnheader", { name: "Name" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "AAPL" })).toBeInTheDocument();
  });

  it("shows an explicit empty message rather than a bare table", () => {
    render(
      <DataTable columns={columns} empty="No rows here" rowKey={(row) => row.name} rows={[]} />,
    );

    expect(screen.getByText("No rows here")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("right-aligns numeric columns with tabular figures", () => {
    render(
      <DataTable
        columns={columns}
        rowKey={(row) => row.name}
        rows={[{ name: "AAPL", value: "1.00" }]}
      />,
    );

    expect(screen.getByRole("cell", { name: "1.00" }).className).toContain("tabular-nums");
  });
});

describe("Panel and Unavailable", () => {
  it("renders the reason and detail for an unavailable section", () => {
    render(
      <Panel subtitle="sub" title="Attribution">
        <Unavailable detail={report.attribution.detail} reason="Unavailable" />
      </Panel>,
    );

    expect(screen.getByRole("heading", { name: "Attribution" })).toBeInTheDocument();
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    expect(screen.getByText(/licensed index-constituent source/)).toBeInTheDocument();
  });
});

describe("charts", () => {
  it("renders a NAV chart with no legend when there is a single series", () => {
    const { container } = render(
      <NavChart data={toNavRows(report.navSeries)} passiveLabel={null} />,
    );

    expect(container.querySelector("svg")).not.toBeNull();
    expect(screen.queryByText("Portfolio value")).not.toBeInTheDocument();
  });

  it("adds a legend as soon as the counterfactual series is present", () => {
    render(
      <NavChart
        data={toNavRows(report.navSeries, report.passiveCounterfactual.series)}
        passiveLabel="FTSE All-World ETF proxy"
      />,
    );

    expect(screen.getByText("Portfolio value")).toBeInTheDocument();
    expect(screen.getByText("FTSE All-World ETF proxy")).toBeInTheDocument();
  });

  it("draws money put in beside the value, so deposits never read as profit", () => {
    const rows = toNavRows([
      { ...report.navSeries[0], netDepositsToDateEur: "1000" },
      { ...report.navSeries[1], netDepositsToDateEur: "1500" },
    ]);

    render(<NavChart data={rows} passiveLabel={null} showInvested />);

    expect(rows.map((row) => row.invested)).toEqual([1000, 1500]);
    expect(screen.getByText("Money put in")).toBeInTheDocument();
    expect(screen.getByText("Portfolio value")).toBeInTheDocument();
  });

  it("labels both rolling windows in the legend", () => {
    render(
      <RollingChart
        data={toRollingRows(report.rollingVolatility30d, report.rollingVolatility90d)}
        longLabel="90-day"
        shortLabel="30-day"
        valueFormat="percent"
      />,
    );

    const legend = screen.getByRole("list");
    expect(within(legend).getByText("30-day")).toBeInTheDocument();
    expect(within(legend).getByText("90-day")).toBeInTheDocument();
  });

  it("names both signs in the contribution legend so sign is not color-only", () => {
    render(
      <ContributionChart
        data={[
          { key: "AAPL_US_EQ", contribution: 0.03 },
          { key: "SHEL_EQ", contribution: -0.01 },
        ]}
      />,
    );

    expect(screen.getByText("Positive contribution")).toBeInTheDocument();
    expect(screen.getByText("Negative contribution")).toBeInTheDocument();
  });
});

describe("formatPercent integration", () => {
  it("formats a rolling volatility tick", () => {
    expect(formatPercent(0.2275, 1)).toBe("22.8%");
  });
});
