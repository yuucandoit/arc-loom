import { describe, expect, test } from "bun:test";
import {
  INITIAL_FUNCTIONS,
  INITIAL_LEDGER_ROWS,
  computeFinOpsTotals,
  computeRoleBreakdown,
  evaluateBreakEven,
  formatUsd8,
} from "../src/finopsEngine";

describe("ARC-Loom FinOps Dashboard Engine", () => {
  test("computes Net Savings equation accurately across all roles", () => {
    const totals = computeFinOpsTotals(INITIAL_LEDGER_ROWS, "all");
    expect(totals.totalRequests).toBe(12);
    expect(totals.arcHits).toBe(11);
    expect(totals.counterfactualCostUsd).toBeGreaterThan(0.45);

    const expectedNet = Number(
      (
        totals.counterfactualCostUsd -
        (totals.sourceCostUsd +
          totals.builderCostUsd +
          totals.cacheExecutionCostUsd)
      ).toFixed(8)
    );
    expect(totals.netSavingsUsd).toBe(expectedNet);
    expect(formatUsd8(totals.netSavingsUsd)).toStartWith("$0.");
  });

  test("evaluates Break-Even call threshold per semantic function", () => {
    const finansiaFn = INITIAL_FUNCTIONS[0];
    const be1 = evaluateBreakEven(finansiaFn);
    expect(be1.breakEvenCalls).toBe(3);
    expect(be1.isProfitable).toBe(true);
    expect(be1.remainingCalls).toBe(0);

    const imaFn = INITIAL_FUNCTIONS[1];
    const be2 = evaluateBreakEven(imaFn);
    expect(be2.breakEvenCalls).toBe(3);
    expect(be2.isProfitable).toBe(false);
    expect(be2.remainingCalls).toBe(1);
  });

  test("breaks down token usage across source, builder, target, extractor, and converter", () => {
    const roles = computeRoleBreakdown(INITIAL_LEDGER_ROWS, "all");
    expect(roles.source.calls).toBe(1);
    expect(roles.builder.calls).toBe(1);
    expect(roles.extractor.calls).toBe(10);
    expect(roles.target.calls).toBe(1);
    expect(roles.converter.calls).toBe(1);
  });
});
