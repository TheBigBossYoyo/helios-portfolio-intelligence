/**
 * Target allocation arithmetic. Planning only: Helios never places an order.
 *
 * Holdings with a target form the plan; their targets are scaled to add up to 100% of the
 * planned part, so a plan that deliberately leaves some holdings out still reads correctly.
 * Holdings without a target sit outside the plan and are left as they are.
 *
 * A deposit is split in proportion to how far below target each holding would be once the
 * deposit is in, so it only ever buys, and money goes first where the gap is largest.
 */

export interface AllocationInput {
  ticker: string;
  name: string | null;
  /** Current value in euros. */
  value: number;
  /** Target share, 0..1, or null for no target. */
  target: number | null;
}

export interface AllocationRow {
  ticker: string;
  name: string | null;
  value: number;
  /** Share of the planned part now. */
  weight: number;
  /** Target scaled to the planned part. */
  target: number;
  /** Weight minus target: positive is overweight. */
  drift: number;
  /** Euros of the deposit to put here. */
  buy: number;
  /** Share of the planned part after the deposit. */
  weightAfter: number;
  /** Euros to buy (positive) or sell (negative) to be exactly on target now, with no deposit. */
  fullTrade: number;
}

export interface AllocationPlan {
  rows: AllocationRow[];
  outside: AllocationInput[];
  /** Targets as entered, before scaling (1 = 100%). */
  targetSum: number;
  plannedValue: number;
  deposit: number;
  /** Largest absolute drift after the deposit, for "how close does this get you". */
  maxDriftAfter: number;
}

export function planAllocation(inputs: AllocationInput[], deposit: number): AllocationPlan {
  const inPlan = inputs.filter((row) => row.target !== null && row.target > 0);
  const outside = inputs.filter((row) => row.target === null || row.target <= 0);
  const targetSum = inPlan.reduce((sum, row) => sum + (row.target ?? 0), 0);
  const plannedValue = inPlan.reduce((sum, row) => sum + Math.max(row.value, 0), 0);
  const amount = Math.max(deposit, 0);
  const after = plannedValue + amount;

  const scaled = inPlan.map((row) => ({ ...row, share: targetSum > 0 ? (row.target ?? 0) / targetSum : 0 }));
  const shortfalls = scaled.map((row) => Math.max(row.share * after - row.value, 0));
  const totalShortfall = shortfalls.reduce((sum, value) => sum + value, 0);

  const rows = scaled.map((row, index) => {
    const buy = totalShortfall > 0 ? (amount * shortfalls[index]) / totalShortfall : 0;
    const weight = plannedValue > 0 ? row.value / plannedValue : 0;
    return {
      ticker: row.ticker,
      name: row.name,
      value: row.value,
      weight,
      target: row.share,
      drift: weight - row.share,
      buy,
      weightAfter: after > 0 ? (row.value + buy) / after : 0,
      fullTrade: row.share * plannedValue - row.value,
    };
  });
  const maxDriftAfter = rows.reduce((max, row) => Math.max(max, Math.abs(row.weightAfter - row.target)), 0);
  return { rows, outside, targetSum, plannedValue, deposit: amount, maxDriftAfter };
}

/** Round a split to cents so it adds up exactly to the deposit (largest remainders win). */
export function roundToCents(amounts: number[], total: number): number[] {
  const cents = amounts.map((value) => value * 100);
  const floored = cents.map(Math.floor);
  let left = Math.round(total * 100) - floored.reduce((sum, value) => sum + value, 0);
  const order = cents
    .map((value, index) => ({ index, remainder: value - Math.floor(value) }))
    .sort((a, b) => b.remainder - a.remainder);
  for (const { index } of order) {
    if (left <= 0) break;
    floored[index] += 1;
    left -= 1;
  }
  return floored.map((value) => value / 100);
}
