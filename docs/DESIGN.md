# Design System — Industry Radar

**Status:** Draft for review
**Last Updated:** October 2026
**Source:** the approved redesign mockups (private canvas https://claude.ai/artifact/Rv3Q8Vp3AM39PsSXRmH1uV). When this doc and a mockup disagree, this doc wins; update the mockup.

Every value here is also defined once in the front end's theme (`web/tailwind.config.ts` and `web/app/globals.css`). Components use the named tokens, never raw hex codes or one-off sizes. If a design needs something that isn't here, add it here first.

---

## 1. Principles

1. **Every claim is checkable.** Facts link to their source. A number without a source doesn't ship.
2. **Calm and dense.** These users read a lot. Show plenty of information with quiet styling: white space, thin borders, little color.
3. **Color means something.** Teal is for interaction and brand, amber is for a single highlight, gray is everything else. If everything is colored, nothing stands out.
4. **One main action per screen.** At most one filled teal button in a view. Everything else is a secondary or text button.
5. **Read like a research note.** The brief uses a serif and a narrow column, like a well-edited newsletter. The rest of the app is a clean tool.
6. **Works on a phone.** Every screen works at 375px wide with no sideways scrolling.

---

## 2. Color

### Brand
| Token | Hex | Use |
|---|---|---|
| `teal` | `#1E5B72` | Main buttons, links, active states, checkboxes, focus rings, score badges |
| `teal-dark` | `#164557` | Text on teal tint, hover for teal buttons, sign-in brand panel |
| `teal-deeper` | `#123A49` | Link hover |
| `teal-tint` | `#E5EEF3` | Selected nav item, chips, "Watching" state |
| `amber` | `#D69A2D` | The single highlight: "new today" dot, logo dot, brief section marker |
| `amber-tint` | `#D69A2D` at 18–20% opacity | Background of "Top match" and "Added by you" pills |
| `amber-text` | `#7A4E0A` | Text on amber tint |

**Amber rules:** never used for buttons or links, and never more than a few marks per screen. It means "look here", not "click here".

### Neutrals
| Token | Hex | Use |
|---|---|---|
| `white` | `#FFFFFF` | Page and card background |
| `surface` | `#F6F8FA` | Sidebar, table headers, callout boxes, onboarding page background |
| `surface-subtle` | `#FBFCFD` | Footer strips inside cards |
| `chip` | `#EDF2F5` | Neutral pills, like event type |
| `border` | `#E3E8EC` | Card and table borders, dividers |
| `border-row` | `#EDF1F4` | Lines between table rows and list items |
| `border-input` | `#DAE2E8` | Inputs, selects, secondary buttons |
| `border-strong` | `#CDD7DE` | Outlined pill buttons (e.g. "+ Watch") |
| `ink` | `#131D24` | Headings and main text |
| `ink-body` | `#212D35` | Long-form brief text |
| `ink-2` | `#2E3B44` | Text on neutral pills |
| `ink-3` | `#3A4852` | Secondary text, dates, inactive nav |
| `muted` | `#596874` | Labels, captions, helper text |
| `placeholder` | `#89959E` | Placeholders, empty-state hints |
| `disabled` | `#A2ADB5` | Disabled text |

### Status (to add in the theme)
Use sparingly, always with an icon or word, never color alone.
| Token | Hex | Use |
|---|---|---|
| `success` | `#2F7D5B` | Run complete, saved |
| `warning` | `#8F5E12` | Close to a usage limit, posting may have moved |
| `danger` | `#B4413C` | Errors, posting closed, delete actions |

Each status color gets a tint (about 12% opacity) for backgrounds.

### Contrast
Body text and labels must meet WCAG AA (4.5:1). `muted` on white passes; `placeholder` and `disabled` are only for text that doesn't need to be read to use the app.

---

## 3. Type

| Font | Use |
|---|---|
| **Geist** | All app UI |
| **Geist Mono** | Dates in tables, numbers in score badges, codes (e.g. K253856, hex values) |
| **Newsreader** (serif) | The brief only: headline, deck, section headings, body, big numbers |

Load all three from Google Fonts with `next/font`.

### App scale (Geist)
| Token | Size / line height | Weight | Use |
|---|---|---|---|
| `display` | 40 / 1.15 | 600 | Sign-in hero only |
| `h1` | 32 / 1.2 | 600, tracking −0.02em | Page titles (Event tracker, Companies, Jobs) |
| `h2` | 26–28 / 1.25 | 600 | Card titles, onboarding question |
| `h3` | 18 / 1.35 | 600 | Section titles inside a page, company name in a profile |
| `body-lg` | 15 / 1.55 | 400 | Event summaries, intro text |
| `body` | 14 / 1.5 | 400–500 | Default UI text, buttons, table cells |
| `small` | 13 / 1.45 | 400–500 | Meta lines, pagination, secondary buttons in toolbars |
| `label` | 12 / 1.4 | 500 | Form labels, table headers, captions |
| `overline` | 12 / 1.4 | 600, uppercase, tracking 0.06em | Brief section labels ("THE LEAD"), kicker above page titles |

### Brief scale (Newsreader)
| Token | Size / line height | Weight | Use |
|---|---|---|---|
| `brief-headline` | 48 / 1.08 | 500 | Headline (36 on phones) |
| `brief-deck` | 21 / 1.45 | 400 | Summary under the headline |
| `brief-h2` | 30 / 1.2 | 500 | Section headings |
| `brief-number` | 36 / 1 | 500 | "By the numbers" figures |
| `brief-body` | 19 / 1.65 | 400 | Paragraphs (17 on phones) |

Callouts inside the brief ("The bottom line", "Why it matters") switch back to Geist at 15–16px so they read as notes.

---

## 4. Spacing, shape and layout

**Spacing scale (px):** 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 40, 48, 56, 64, 72, 80. Prefer 8, 12, 16, 24, 32 for most gaps.

**Radius**
| Token | px | Use |
|---|---|---|
| `radius-sm` | 6 | Icon buttons |
| `radius` | 8 | Buttons, inputs, selects, nav items |
| `radius-md` | 10 | Small cards, the sidebar run card, chip containers |
| `radius-lg` | 12 | Tables, callouts, list containers |
| `radius-xl` | 16 | Large cards (onboarding) |
| `radius-full` | 999 | Pills, chips, avatars, score badges |

**Borders:** 1px everywhere. The only exception is the 2px `ink` rule above "By the numbers" in the brief.

**Shadows:** none by default. Use borders. Dropdown menus and dialogs may use one soft shadow (`0 8px 24px rgba(19,29,36,0.08)`).

**Layout**
- App shell: 248px sidebar plus the main area. Main padding 40px top, 56px sides, 72px bottom.
- Content widths: brief article 700px, brief header 900px, forms and onboarding 680px, sign-in form 380px. Tables fill the main area.
- Brief layout: article plus a 340px side column (sources, related events), 64px gap.
- **Phone (under 760px):** the sidebar becomes a top bar with a menu button; main padding becomes 16px sides; filter grids become two columns; the brief side column moves under the article; tables scroll sideways inside their own box, never the page.

---

## 5. Components

Build these once in `web/components/` (on top of shadcn/ui) and reuse them.

### Buttons
| Variant | Look | When |
|---|---|---|
| Main | Teal fill, white text, 600 weight, height 40 (44 for key actions) | The one main action: Continue, Find jobs, Tailor resume |
| Secondary | White, `border-input` border, `ink` text, 500 weight | Export CSV, PDF, Email, Open posting, Next/Previous |
| Text | No border, teal text | Sign out, small inline actions |
| Pill toggle | Height 30, full radius, outlined teal; when on: `teal-tint` fill, `teal-dark` text, "✓" | Watch / Watching |
| Danger | White with `danger` text; filled only in the confirm dialog | Delete resume, delete account |

Every button has a visible text label (icons are optional and go on the left). Disabled buttons use `disabled` text and no pointer.

### Form fields
- Height 40, `radius` 8, `border-input` border, 14px text, padding 10–12px. A label sits above in `label` style (6px gap).
- Search inputs have a 16px magnifier icon at the left, inside the field.
- Focus: 2px teal ring with 2px offset.
- **Multi-select dropdown with search:** used for role type, location, seniority and industry in Jobs. A button showing the selection ("3 selected" or the single value) opens a panel with a search box and checkboxes. Checkboxes use `accent-color: teal`.

### Navigation (sidebar)
- Logo and name at the top; an industry switcher (select) below.
- Nav items: height 40, `radius` 8, icon 18px plus label. Active: `teal-tint` background, `teal-dark` text, 600 weight. Inactive: `ink-3`, 500 weight.
- An amber dot on Briefing means a new brief today.
- Account row at the bottom: initials avatar (`teal-tint`), name, "Sign out".
- No "Run now" button for users: runs are scheduled for everyone. (It's in the mockup from the single-user version.)

### Tables
- Inside a `radius-lg` box with a `border` border.
- Header row: `surface` background, `label` style text in `muted`.
- Rows: 16px padding, `border-row` dividers, content aligned to the top.
- Dates in Geist Mono, 13px, `ink-3`, short form ("Sep 29").
- Pagination below: "Showing 1–9 of 43" on the left, Previous/Next on the right.

### Pills and chips
- **Neutral pill** (event type, segment): `chip` background, `ink-2` text, 12px, 500 weight.
- **Highlight pill** ("Top match", "Added by you"): `amber-tint` background, `amber-text`, 12px, 600 weight. At most one per row.
- **Removable chip** (followed industries): `teal-tint`, `teal-dark` text, with a 22px "×" button that has an aria-label.

### Score badge
A 40px teal circle with the score in white Geist Mono 14px. Scores show as numbers only; no colored traffic-light icons, and no seniority-level labels on jobs.

### Collapsible panel
A row with a chevron (rotates 90° when open) and a title. Used for job descriptions and sector groups in "Choose industries". The chevron button has `aria-expanded` and a label ("Expand Healthcare").

### Source link
Teal text (500 weight) showing the site name ("FDA database", "globenewswire.com") followed by a 13px "opens in new tab" icon. Opens in a new tab with `rel="noopener"`. In the brief, sources are numbered superscripts that jump to a source list.

### Brief blocks
- **Header strip:** overline brief name, date, and a date picker plus PDF and Email buttons on the right, with a `border` line underneath.
- **By the numbers:** four columns, each a Newsreader number plus a 13px caption; 2px `ink` rule above, `border` lines between.
- **Callout ("The bottom line"):** `surface` box, `radius-lg`, overline heading with a small amber square marker, Geist bullets.
- **"Why it matters":** `border` lines above and below, overline label in `muted`, Geist 16px.

### Empty, loading and error states
- **Empty:** one sentence saying why it's empty and one action ("You're not following any industries yet. Choose industries").
- **Loading:** skeleton rows in `surface` for tables and lists. Long tasks (find jobs, tailoring) show a progress line with the current step ("Scoring 12 of 40 jobs…") instead of a spinner alone.
- **Error:** a plain sentence of what happened and what to do, with a retry button. Never show raw error text.
- **Limit reached:** say the limit and when it resets ("You've used all 30 resume tailorings this month. They reset on Dec 1.").

### Usage meter
In Jobs, show remaining uses next to the metered actions ("Find jobs · 1 left today", "Tailor · 24 of 30 left this month"). At 80% used, the text switches to `warning`.

---

## 6. Icons and logo

- **Icons:** Lucide (comes with shadcn/ui), 1.75px stroke, 16px inline and 18px in navigation, color `currentColor`.
- **Logo:** the radar mark (two teal rings, a sweep line, a teal center dot and an amber dot), 28px, beside "Industry Radar" in Geist 16px 600. On the dark sign-in panel, the rings are white.

---

## 7. Writing style

- **Plain and short.** Sentence case for all headings and buttons ("Export CSV", "Choose industries").
- **Buttons are verbs:** Find jobs, Tailor resume, Open posting, Watch.
- **Dates:** "Sep 29" in tables; "Tuesday, September 29, 2026" in the brief header. Times shown in the user's time zone.
- **Money:** "$7.31M", "$523K", "~$700M" (the tilde means approximate).
- **Numbers:** numerals always; thousands with commas ("1,240").
- **Sources by name:** "FDA database", "SEC Form D", "ClinicalTrials.gov", or the site's domain.
- **No hype.** No exclamation marks, no "amazing" or "powerful". Say what happened.
- **Privacy statements are specific:** "Your resume, job preferences and saved industries stay private to you."

---

## 8. Accessibility

- AA contrast for all text that must be read.
- Everything works with a keyboard; focus is always visible (teal ring).
- Every input has a label (visible, or visually hidden when the design shows none).
- Icon-only buttons have an `aria-label`. Decorative icons have `aria-hidden`.
- Tap targets are at least 40px tall on phones (pill buttons 30px tall need 8px of space around them).
- Toggle buttons use `aria-pressed`; collapsibles use `aria-expanded`.
- Status is never shown by color alone.
- Respect `prefers-reduced-motion`: no animated transitions beyond simple fades.

---

## 9. Dark mode

Not at launch. Because every color is a named token, adding dark mode later means defining dark values for the tokens, with no component changes.

---

## 10. Checklist for a new screen

Before a new screen or component is merged:
- [ ] Uses only tokens from this doc (no raw hex or one-off sizes)
- [ ] At most one main (teal-filled) button
- [ ] Amber used for at most a few highlights, never for actions
- [ ] Every fact has a source link
- [ ] Has empty, loading and error states
- [ ] Works at 375px with no sideways page scroll
- [ ] Keyboard and screen-reader checks from section 8 pass
