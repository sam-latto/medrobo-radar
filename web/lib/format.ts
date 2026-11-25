/** Formatting rules from docs/DESIGN.md section 7. */

/** API dates are "YYYY-MM-DD" calendar dates: read them as local dates, never shifted by time zone. */
export function parseDate(value: string): Date {
  const [y, m, d] = value.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}

/** "Sep 29" (tables, side lists) */
export function shortDate(value: string): string {
  return parseDate(value).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/** "Sep 29, 2026" (date picker) */
export function mediumDate(value: string): string {
  return parseDate(value).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

/** "Tuesday, September 29, 2026" (brief header) */
export function longDate(value: string): string {
  return parseDate(value).toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

/** "$7.31M", "$523K", "$1.2B" */
export function money(amount: number | null | undefined): string | null {
  if (amount == null || !Number.isFinite(amount) || amount <= 0) return null;
  const units: [number, string][] = [
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ];
  for (const [size, suffix] of units) {
    if (amount >= size) {
      const value = amount / size;
      const digits = value >= 100 ? 0 : value >= 10 ? 1 : 2;
      return `$${Number(value.toFixed(digits))}${suffix}`;
    }
  }
  return `$${amount.toLocaleString("en-US")}`;
}
