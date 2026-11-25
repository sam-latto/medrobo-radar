/** Display names for the pipeline's event types and segments (stored as keys like "partnership_ma"). */

const EVENT_TYPES: Record<string, string> = {
  funding: "Funding",
  regulatory: "Regulatory",
  launch: "Launch",
  partnership_ma: "Partnership / M&A",
  research: "Research",
  news: "News",
  deployment: "Deployment",
};

const ACRONYMS = new Set(["ai", "ma", "ev", "or", "us", "uk", "eu", "ar", "vr", "iot", "saas"]);

function titleize(key: string): string {
  const words = key.split(/[_\s]+/).filter(Boolean);
  return words
    .map((w, i) => (ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : i === 0 ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}

export function eventTypeLabel(key: string): string {
  return EVENT_TYPES[key] ?? titleize(key);
}

export function segmentLabel(key: string): string {
  return key === "other" ? "Other" : titleize(key);
}
