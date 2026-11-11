# Native example charts and timing

`src/charts.neo` provides small, server-rendered charts as `WebHtml`. Import it
from a NeoKit application, as `examples/saas/model.neo` does. Chart styles live in
that example's `styles/site.css`; the helper emits semantic HTML and SVG, without
a CSS or JavaScript dependency.

| Function | Input | Output |
|---|---|---|
| `web_line_chart(title, labels, values, previous)` | Matching arrays of 2–366 labels, current values and previous values; dollar values from 0 through 10¹² | Current/previous line chart, area, keyboard-focusable points with native titles, and an expandable complete data table |
| `web_percentage_bars(title, labels, values)` | Matching arrays of 1–12 labels and integer percentages from 0 through 100 | Labeled percentage bars on a fixed 0–100 scale |

Invalid shapes or values return a no-data paragraph. Text/attribute labels are
escaped, and all geometry is computed from bounded numeric inputs. The line
chart's units are dollars; this is a deliberately small API rather than a
general chart grammar. `examples/saas/client/chart.js` optionally adds text-only
tooltips and focused-point styling. The native table and SVG remain useful
without that module.

Dynamic strings are flattened by Neo's `std:strings` function
`str_concat_parts(parts)`. It accepts an array of byte strings, preserves their
bytes (including UTF-8 and NUL), allocates the result once and copies each part
in order. It is available to other Neo applications independently of NeoKit.

`[server] timing=true` enables Neo's `std:http_observability` helper
`web_server_timing(wire, started, ended)` around NeoKit's buffered handler.
Inputs are monotonic nanoseconds; the emitted `render` duration is milliseconds
with three decimal places. Invalid/reversed timestamps clamp to zero. The helper
preserves the response body and does not decorate an empty async placeholder.
The default is off. Complete deferred-stream duration is not measured by this
option: once headers are sent, this metric cannot be rewritten.

`client/performance.js` displays the response's measured metric from the browser
Navigation Timing entry, with navigation TTFB separately in its tooltip. It uses
an unquantified label when Server Timing is unsupported, and identifies static
exported HTML explicitly. Server render time excludes its own timing header,
socket delivery, asset loading and browser layout/paint.

[Showcase measurements and browser captures](../bench/showcase-results/2026-11-08/REPORT.md)
include the full release HTML work rather than just a string-building benchmark.
