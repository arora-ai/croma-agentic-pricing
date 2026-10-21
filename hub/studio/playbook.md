# Croma report playbook

Read by the Planner, Insight analyst, Strategist, Writer, Reviewer and Editor. Use get_report_playbook(topic) for one part: design, spec, writing.

## DESIGN

### What a great Croma report does
The reader is a CEO-office or category leader with ten minutes. A great report:
1. **Answers first.** Page one says what happened, why it matters and what to do, in plain sentences with the key figures. Detail comes after.
2. **Explains, not lists.** Every section opens with a takeaway sentence that states the point (for example "iPhones added 47% of enterprise growth but are at 88% of plan"), then shows the evidence for that point only.
3. **Decomposes change.** When a number moved, show what moved it: by zone, category, brand, channel; price vs volume vs mix; a bridge from last year to this year. A table of levels is not an explanation.
4. **Reconciles.** Totals tie to the parts and to the headline. If two numbers that should agree do not, say why or fix it. Windows (current and comparison) are stated once and used everywhere.
5. **Prescribes.** Every action has an owner role, where it applies, the size of the prize (computed, with assumptions stated), a confidence level, a risk or guardrail, a KPI to track and a timing. Name zones, zonal managers, clusters, SKUs when the evidence supports it.
6. **Is honest.** Caveats are specific (what the data cannot show, partial lists, immaterial factors). Nothing is invented. Every figure traces to the evidence ledger.
7. **Is economical.** Choose the tables and charts that carry the argument. Do not include a table that no sentence uses.

### What the two reference reports teach (use as inspiration, not a template to copy)
**Weekly performance report (enterprise, current year vs last year).** Cover with the two windows (month to date and current week, with last-year dates aligned). Executive summary with KPI tiles for Enterprise, Stores and Online: NMV, YoY growth, plan achievement, quantity growth, volume plan achievement, week-to-date. A channel table (plan, current, last year, growth, achievement for value and quantity). Growth contribution analysis: absolute growth decomposed by zone, category, class, brand and the single biggest category x class x brand combinations. Category tables for enterprise, stores and online. Zone x category quantity achievement. Business-unit or category-family pages (tiles, category mix chart, month-to-date vs current week, zone spread, brand leadership). Own label share. Closing insights split into "what is working" and "watch-outs and actions".
**Category deep-dive (for example Apple, weekly).** Executive summary of key actions. Scorecard against Mission or AOP targets with achievement and gap. Week-on-week. Zone achievement with the zonal manager and gap in units. Non-flagship categories by zone. Day-wise pattern. Top SKUs and their zone concentration. Store vs online split. Basket attach and cross-sell (attach rate by accessory category, baskets with vs without attach, paid vs free attach, trend). Omnichannel (online share, sub-channels, online penetration index by zone, cluster opportunity ranking). CPI by zone and cluster (gaining and losing clusters). Bulking and reseller scan (repeat same-SKU buyers, GST flag, multi-customer GSTINs, cross-zone). Recommendations grouped by theme.
**Decision brief.** The question, the recommendation, the size of the prize, options considered, risks and guardrails, what you would need to believe, next steps.

### Where to do better than the references
- Bottom line and decisions at the top, in sentences, not a wall of bullet fragments.
- Bridges (waterfall charts) and driver ranking instead of only level tables. Show contribution to growth, and separate rate from mix when margin or markdown moved.
- Fewer, denser, better tables. Heat-colour achievement and growth columns. Put long tables in the appendix, not the story.
- Consistent windows, units and definitions. Reconcile totals; do not let the headline and the scorecard disagree.
- Actions with size, owner, confidence, KPI and timing, tied to evidence ids.
- Call out what changed since the previous period when you have it, and what would change the conclusion.

### Report types and sections (a menu, not a fixed list: choose what the question needs)
- weekly_performance: bottom line; channel performance; growth contribution (zone, category, brand, biggest combinations); category performance against plan; zone x category; focus categories; what is working and watch-outs; actions.
- category_deep_dive: bottom line; scorecard vs target; where the gap sits (zone, ZM, cluster); mix (SKU, price band, channel); basket and attach; omnichannel; competitive or CPI view if the data supports it; risks (bulking, GST); actions by theme.
- decision_brief: the decision; recommendation; evidence for it (3 to 5 exhibits); size of the prize; options; risks and guardrails; next steps.
- ad_hoc: bottom line; the analysis the question needs (usually 2 to 4 sections); actions; caveats.

### Depth
brief = 2 to 3 pages, 2 to 4 sections. standard = 6 to 10 pages, 4 to 7 sections. deep = 15+ pages, 8 to 12 sections. Choose from the request (audience, "full", "deep dive", "quick"). Default standard.

### Windows and definitions
- State the current window and the comparison window once (frame.period_label, frame.comparison_label). Month to date vs the same dates last year; a current week vs the same weekday-aligned week last year (364 days earlier) unless the Data agent says the data space uses another rule; follow the data space and state what it used.
- First check data coverage: the earliest and latest dates in the data. A year-on-year view needs last year's dates to exist.
- Standard definitions to put in a definitions block: NMV, merchandise profit and margin, markdown %, ASP, AOP or SOP (which one), Mission targets if used, what is excluded (Miscellaneous and Charges), and that markdown and merchandise profit exist for Stores only. Online includes Quick Commerce.

### Writing data requests for the Data agent
Each request is one self-contained question with exact dates, scope, grouping, ranking and the measures. Ask for current and comparison side by side in one result with plain column names, for example: "For stores, 2026-08-01 to 2026-08-31 and 2025-08-01 to 2025-08-31, NMV in crore and units by zone, columns zone, zonal_manager, cy_nmv, ly_nmv, cy_qty, ly_qty, plan_nmv, ordered by cy_nmv descending." One result per request. Cap long lists ("top 15"). Independent requests run in parallel. The Data agent hands back a result id (R1, R2 ...) for each.
What the data space can answer: NMV, quantity, orders, customers, ASP, by store, zone, zonal manager, cluster, channel (Stores, Croma.com, TataNeu, Quick Commerce), category, class, brand, SKU, day; markdown, discount, merchandise profit and margin (Stores only); AOP and SOP targets and phasing; iPhone Mission targets; CPI; basket composition from order lines (attach); GST flag, payment and EMI fields. Ask for something the data does not hold only if you accept it may come back empty.

## SPEC

All tools take JSON text. Save the frame once, then each section. Tables and charts reference a result id (R or T) and columns; the renderer pulls the rows, so you never type table data. Text can quote numbers, and every number must come from a result or from calc (the audit traces them).

### Frame (save_frame)
```json
{"title": "August 2026 weekly performance", "subtitle": "Enterprise, current year vs last year",
 "report_type": "weekly_performance", "audience": "CEO Office, Profit Planning",
 "period_label": "1-31 Aug 2026 and week 24-30 Aug", "comparison_label": "Same dates 2025 (week aligned 25-31 Aug 2025)",
 "scope": "All channels, merchandise", "measure": "NMV (Rs Crore) and units", "data_as_of": "30 Aug 2026",
 "headline": "NMV grew 51.5% to ₹2,491.4 Cr, 4.6% ahead of plan, but Apple is the one unit still below plan.",
 "bottom_line": ["3 to 5 sentences, each carrying a figure and a so-what"],
 "decisions": ["What you need the reader to decide or approve"],
 "section_order": ["s1", "s2"], "caveats": ["Specific data limits"]}
```
report_type is one of weekly_performance, category_deep_dive, decision_brief, ad_hoc.

### Section (save_section): {id, title, takeaway, blocks[]}
Block types:
```json
{"type": "paragraph", "text": "Plain prose. **Bold** spans allowed."}
{"type": "bullets", "items": ["...", "..."]}
{"type": "kpi_tiles", "heading": "Enterprise (MTD)", "tiles": [
  {"value": "₹2,491.4 Cr", "label": "MTD NMV", "delta": "+51.5% YoY", "sub": "LY ₹1,645.0 Cr", "tone": "good"}]}
{"type": "table", "title": "Channel performance", "result_id": "R3", "top_n": 15,
 "columns": [{"key": "channel", "label": "Channel"},
             {"key": "cy_nmv", "label": "CY NMV (₹ Cr)", "format": "num1"},
             {"key": "growth_pct", "label": "Growth", "format": "spct", "heat": "growth"},
             {"key": "ach_pct", "label": "Achv.", "format": "pct", "heat": "achievement"}],
 "total_row": true, "sum_columns": ["cy_nmv"], "note": "optional note under the table"}
{"type": "chart", "kind": "bar", "title": "Channel NMV, CY vs LY", "result_id": "R3", "x": "channel",
 "series": [{"col": "ly_nmv", "label": "LY"}, {"col": "cy_nmv", "label": "CY"}], "caption": "optional"}
{"type": "chart", "kind": "waterfall", "title": "What added ₹846 Cr", "result_id": "T2", "x": "category",
 "series": [{"col": "added"}], "start_ref": "R3:SUM:ly_nmv", "start_label": "LY", "end_label": "CY", "top_n": 8, "value_format": "num1"}
{"type": "callout", "tone": "insight|risk|action|note", "title": "Watch", "text": "..."}
{"type": "actions", "title": "Actions", "items": [
  {"priority": "P1", "action": "Imperative sentence", "where": "Delhi & NCR, MMR", "owner": "Zonal managers",
   "impact": "~₹12.4 Cr if attach reaches 5% (assumption)", "guardrail": "Keep the margin floor; no blanket discounts",
   "evidence": ["R4", "D7"], "confidence": "medium", "kpi": "AirPods attach %", "timing": "W3"}]}
{"type": "definitions", "items": [{"term": "NMV", "definition": "..."}]}
{"type": "page_break"}
```
- chart kind: bar (grouped; with two series the first is the comparison year in grey and the second the current year in navy, with the growth % labelled automatically), hbar (one series, ranked), line (trends), stacked, waterfall (increments plus a start reference).
- table/chart `format` values: text, int, num1, num2, pct (value already a percent), ratio_pct (0.51 shows 51.0%), spct (signed percent), pp (signed percentage points), cr (₹ Cr, 1 dp), cr2, inr, x, date. Use heat achievement (100+ green, 90-100 amber, below 90 red) or growth (sign) on percent columns.
- A section's blocks usually run: tiles or chart first, then the table, then a one or two sentence reading, then a callout if there is a risk. Keep a section to one idea.
- Fix anything save_section reports (untraced numbers, unknown result or column) and save the section again.

## WRITING

### Numbers
- Never calculate in your head. Call calc for every growth, share, gap, contribution, achievement, ASP, pp change or sizing, and use the value it returns. Compute from unrounded inputs.
- Use derive_table to add growth, contribution or achievement columns to a result before you chart or tabulate it. Join current and comparison results with join_with and join_on.
- Formats: ₹ Crore with one decimal (₹2,491.4 Cr); percentages one decimal; growth with a sign (+51.4%); margin and share changes in pp; units with thousands separators; western grouping throughout.
- If a base is zero or tiny, say "new" or "n/a" instead of a percentage. Do not present an immaterial factor (a tiny change on a large base) as a cause or an action.

### Insight cards (Insight analyst)
Each card: a full-sentence headline that makes the point, the finding with figures, the so-what, the evidence ids, the materiality (size against the total and why it matters) and your confidence. Prefer causes to descriptions: "Markdown rose 3.1 pp because the mix moved to TV, where markdown is 2.2x the store average" beats "Markdown rose". Rank by materiality. Include confirmations ("what is working") and anomalies, and say when something expected is not there. Test your own explanation: is there a simpler one, and does the data rule it out?

### Actions (Strategist)
Imperative, specific, owned, sized, bounded: what to do, where, who, how much it could be worth (calc, with the assumption stated as an input), how confident you are and why, the risk or guardrail (for example a margin floor), the KPI that shows it worked, and when. P1 = do now, material; P2 = this cycle; P3 = watch. Do not recommend what the evidence does not support. Say "no action" or "monitor" when that is the honest answer. Ground in Croma's own rules (the data space's rules and the findings) rather than inventing policy.

### Prose
Plain business English, written for a reader with ten minutes. No tool, SQL or system words. No filler ("it is important to note"), no restating the heading in the takeaway. Short paragraphs: claim, number, so-what. Use the business's terms: NMV, MM%, markdown %, AOP, Mission, CPI, ZM. Name zones, ZMs, clusters, brands and SKUs when they are the story.
- **Cite, but quietly.** Put evidence ids at the end of a sentence in square brackets, like [R2, D43]. The renderer removes them from the prose and gathers them into an evidence line under each section, so cite freely but never write sentences around them. Never write ids inside tables or tile values.
- **Say each caveat once.** Put a shared caveat (for example "only Stores has a comparable plan") in one place, a callout in the section where it first matters, or the frame's caveats, and do not repeat it. Do not scatter "not a forecast", "do not add" and "illustrative" through every paragraph.
- **Respect the length limits.** The spec enforces them (measured without ids): headline 300 characters, a bottom-line point 270, a section takeaway 230, a paragraph 750, a bullet 280, a callout 520, an action 300, its impact 240 and guardrail 220. If save_section rejects a field as too long, cut words and keep the point.
- **Put each thing in its field.** An action's size goes in impact (one or two sentences, with the assumption), its risk or condition in guardrail, its measure in kpi, its timing in timing. Do not pack them into the action text.
- **Lead with the point.** A bottom-line point is "what happened, how big, so what" in one sentence, not a chain of clauses.

### Consistency
The headline, bottom line, section takeaways and actions must agree. Use the same window and the same name for each measure everywhere. A number that appears twice must be the same number. If you discover a conflict, resolve it with the evidence or state it in the caveats.
