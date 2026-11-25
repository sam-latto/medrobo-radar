/**
 * Turns a brief's Markdown (pipeline/synthesis_agent.py) into blocks the Briefing screen lays out
 * like the approved mockup: headline, dek, "The bottom line", "By the numbers", themed sections,
 * "Why it matters", "What to watch" and a numbered source list.
 *
 * Only the Markdown the brief writer uses is understood (headings, bullets, numbered lists,
 * paragraphs, **bold**, *italic*, [links](url)). Nothing is rendered as HTML, and only http(s)
 * links become sources. Older briefs (bold "Theme: Title" lines instead of ## headings) still parse.
 */

export type Inline =
  | { t: "text"; text: string }
  | { t: "b"; children: Inline[] }
  | { t: "i"; children: Inline[] }
  | { t: "cite"; n: number }; // superscript source number

export type Block =
  | { type: "callout"; title: string; items: Inline[][] }
  | { type: "section"; label: string | null; title: string }
  | { type: "p"; content: Inline[] }
  | { type: "why"; content: Inline[] }
  | { type: "ul"; items: Inline[][] }
  | { type: "ol"; items: Inline[][] }
  | { type: "watch"; items: Inline[][] };

export type Source = { n: number; label: string; url: string; host: string };
export type Figure = { figure: string; caption: Inline[] };

export type BriefDoc = {
  headline: string | null;
  deck: string | null;
  numbers: Figure[];
  blocks: Block[];
  sources: Source[];
  words: number;
};

const LINK = /\[([^\]]+)\]\(([^)\s]+)\)/;
// A parenthesised group of links only, e.g. "([openFDA](u))" or "([Reuters](u1); [FDA](u2))"
const CITE_GROUP = /\s*\(\s*(?:\[[^\]]+\]\([^)\s]+\)\s*[;,]?\s*)+\)/;

class SourceList {
  private byUrl = new Map<string, Source>();

  cite(label: string, url: string): number | null {
    let parsed: URL;
    try {
      parsed = new URL(url);
    } catch {
      return null;
    }
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
    const existing = this.byUrl.get(parsed.href);
    if (existing) return existing.n;
    const source = { n: this.byUrl.size + 1, label, url: parsed.href, host: parsed.hostname.replace(/^www\./, "") };
    this.byUrl.set(parsed.href, source);
    return source.n;
  }

  all(): Source[] {
    return [...this.byUrl.values()];
  }
}

function parseInline(text: string, sources: SourceList): Inline[] {
  const out: Inline[] = [];
  let rest = text;
  while (rest) {
    const group = rest.match(CITE_GROUP);
    const link = rest.match(LINK);
    const bold = rest.match(/\*\*(.+?)\*\*/);
    const italic = rest.match(/(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])/);
    const next = [group, link, bold, italic]
      .filter((m): m is RegExpMatchArray => m !== null && m.index !== undefined)
      .sort((a, b) => a.index! - b.index! || b[0].length - a[0].length)[0];
    if (!next) {
      out.push({ t: "text", text: rest });
      break;
    }
    if (next.index! > 0) out.push({ t: "text", text: rest.slice(0, next.index) });
    if (next === group) {
      // Citation only: the link text is a source name, shown in the source list
      for (const m of next[0].matchAll(new RegExp(LINK, "g"))) {
        const n = sources.cite(m[1], m[2]);
        if (n) out.push({ t: "cite", n });
      }
    } else if (next === link) {
      out.push(...parseInline(next[1], sources));
      const n = sources.cite(next[1], next[2]);
      if (n) out.push({ t: "cite", n });
    } else if (next === bold) {
      out.push({ t: "b", children: parseInline(next[1], sources) });
    } else {
      out.push({ t: "i", children: parseInline(next[1], sources) });
    }
    rest = rest.slice(next.index! + next[0].length);
  }
  return out;
}

export function plainText(nodes: Inline[]): string {
  return nodes.map((n) => (n.t === "text" ? n.text : n.t === "cite" ? "" : plainText(n.children))).join("");
}

/** "Regulatory: Autonomy gets a template" → label + title; a bare "Top Stories" → label only, so every
 * section opens with the same small label. */
function splitTheme(heading: string): { label: string | null; title: string } {
  const i = heading.indexOf(":");
  if (i > 0 && i < 40) return { label: heading.slice(0, i).trim(), title: heading.slice(i + 1).trim() };
  return { label: heading.trim(), title: "" };
}

const stripMarks = (s: string) => s.replace(/\*\*|__/g, "").trim();
// Older briefs leaked the writer's event numbers: "(index 6)", "(indices 83–84)", "(index 59/99)",
// and inside other brackets: "($200M, index 0)" → "($200M)"
const stripIndexRefs = (s: string) =>
  s
    .replace(/\s*\((?:index|indices)\s[\d\s,/–-]+\)/gi, "")
    .replace(/[,;]\s*(?:index|indices)\s[\d\s,/–-]+(?=\))/gi, "");
const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const FIGURE = /^\*\*(.+?)\*\*\s*[—–-]\s*(.*)$/;

export function parseBrief(markdown: string): BriefDoc {
  const sources = new SourceList();
  const doc: BriefDoc = { headline: null, deck: null, numbers: [], blocks: [], sources: [], words: 0 };
  const lines = stripIndexRefs(markdown).replace(/\r\n?/g, "\n").split("\n");

  // Which special section we're in: list items go to it instead of a plain list
  let mode: "none" | "bottom" | "numbers" | "watch" = "none";
  let list: { type: "ul" | "ol"; items: Inline[][] } | null = null;
  let para: string[] = [];

  const flushPara = () => {
    if (!para.length) return;
    const lines = para;
    para = [];
    const text = lines.join(" ").trim();
    if (!text) return;
    const why = text.match(/^\*\*Why it matters:?\*\*:?\s*(.*)$/i);
    if (why) {
      doc.blocks.push({ type: "why", content: parseInline(why[1], sources) });
      return;
    }
    // Older briefs: a line that is only bold ("**Lead Story: Title**", "**Top Stories**") is a
    // section heading, with its text on the following lines
    const heading = lines[0].match(/^\*\*([^*]{3,140})\*\*$/);
    if (heading && !/[.!?]$/.test(heading[1])) {
      doc.blocks.push({ type: "section", ...splitTheme(heading[1]) });
      const rest = lines.slice(1).join(" ").trim();
      if (rest) doc.blocks.push({ type: "p", content: parseInline(rest, sources) });
      return;
    }
    doc.blocks.push({ type: "p", content: parseInline(text, sources) });
  };
  const flushList = () => {
    if (list?.items.length) doc.blocks.push(list);
    list = null;
  };
  const flush = () => {
    flushPara();
    flushList();
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();
    // Blank lines end a paragraph; "---" dividers between sections are dropped (sections have their own spacing)
    if (!trimmed || /^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flush();
      continue;
    }

    const h1 = trimmed.match(/^#\s+(.*)$/);
    if (h1 && !doc.headline) {
      flush();
      doc.headline = stripMarks(h1[1]);
      continue;
    }
    if (doc.headline && !doc.deck && doc.blocks.length === 0 && /^\*[^*].*\*$/.test(trimmed)) {
      doc.deck = trimmed.slice(1, -1).trim();
      continue;
    }

    const h2 = trimmed.match(/^#{2,4}\s+(.*)$/);
    if (h2) {
      flush();
      const heading = stripMarks(h2[1]);
      const key = heading.toLowerCase();
      if (key === "the bottom line") {
        mode = "bottom";
        doc.blocks.push({ type: "callout", title: heading, items: [] });
      } else if (key === "by the numbers") {
        mode = "numbers";
      } else if (key === "what to watch") {
        mode = "watch";
        doc.blocks.push({ type: "section", label: heading, title: "" });
        doc.blocks.push({ type: "watch", items: [] });
      } else if (/daily briefing/i.test(heading) && !doc.headline && doc.blocks.length === 0) {
        // Older briefs open with "## <Name> — Daily Briefing | <date>": that's the header strip, not content
        mode = "none";
      } else {
        mode = "none";
        doc.blocks.push({ type: "section", ...splitTheme(heading) });
      }
      continue;
    }

    const bullet = trimmed.match(BULLET) ?? trimmed.match(NUMBERED);
    if (bullet) {
      flushPara();
      const body = bullet[1];
      const last = doc.blocks[doc.blocks.length - 1];
      if (mode === "numbers") {
        const fig = body.match(FIGURE);
        if (fig) doc.numbers.push({ figure: fig[1].trim(), caption: parseInline(fig[2], sources) });
        continue;
      }
      if (mode === "bottom" && last?.type === "callout") {
        last.items.push(parseInline(body, sources));
        continue;
      }
      if (mode === "watch" && last?.type === "watch") {
        last.items.push(parseInline(body, sources));
        continue;
      }
      const type = BULLET.test(trimmed) ? "ul" : "ol";
      if (!list || list.type !== type) {
        flushList();
        list = { type, items: [] };
      }
      list.items.push(parseInline(body, sources));
      continue;
    }

    flushList();
    para.push(trimmed);
  }
  flush();

  // Older briefs have no "# headline": promote the lead section's title (keeping its label)
  if (!doc.headline) {
    const lead = doc.blocks.find((b) => b.type === "section");
    if (lead?.type === "section" && lead.label && lead.title) {
      doc.headline = lead.title;
      lead.title = "";
    }
  }

  doc.sources = sources.all();
  doc.words = markdown.split(/\s+/).filter(Boolean).length;
  return doc;
}
