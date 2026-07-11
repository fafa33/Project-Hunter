from __future__ import annotations

from html import escape

from hunter.dashboard.models import DashboardPanel, DashboardView


class HtmlDashboardRenderer:
    def render(self, view: DashboardView) -> str:
        panels = "\n".join(_panel(panel) for panel in view.panels)
        return (
            "<!doctype html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '  <meta charset="utf-8">\n'
            '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"  <title>{escape(view.title)}</title>\n"
            f"  <style>{_css()}</style>\n"
            "</head>\n"
            "<body>\n"
            "  <main>\n"
            f"    <header><h1>{escape(view.title)}</h1><p>Generated {escape(view.generated_at.isoformat())}</p></header>\n"
            f"{panels}\n"
            "  </main>\n"
            "</body>\n"
            "</html>\n"
        )


def _panel(panel: DashboardPanel) -> str:
    metrics = "".join(
        f'<div class="metric"><span>{escape(metric.label)}</span><strong>{escape(str(metric.value))}</strong></div>'
        for metric in panel.metrics
    )
    rows = "".join(_row(row.values) for row in panel.rows)
    table = ""
    if panel.rows:
        headers = tuple(panel.rows[0].values.keys())
        table = (
            "<table><thead><tr>"
            + "".join(f"<th>{escape(header.replace('_', ' ').title())}</th>" for header in headers)
            + "</tr></thead><tbody>"
            + rows
            + "</tbody></table>"
        )
    return (
        f'    <section id="{escape(panel.panel_id)}">\n'
        f"      <h2>{escape(panel.title)}</h2>\n"
        f'      <div class="metrics">{metrics}</div>\n'
        f"      {table}\n"
        "    </section>"
    )


def _row(values: object) -> str:
    if not isinstance(values, dict) and not hasattr(values, "items"):
        return ""
    return "<tr>" + "".join(f"<td>{escape(str(value))}</td>" for _, value in values.items()) + "</tr>"


def _css() -> str:
    return """
:root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
body { margin: 0; background: #f6f7f9; color: #15171a; }
main { max-width: 1180px; margin: 0 auto; padding: 32px 20px 48px; }
header { margin-bottom: 24px; }
h1 { margin: 0 0 6px; font-size: 28px; font-weight: 700; }
h2 { margin: 0 0 14px; font-size: 18px; }
p { margin: 0; color: #5f6872; }
section { margin: 18px 0; padding: 18px; background: #ffffff; border: 1px solid #dfe3e8; border-radius: 8px; }
.metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 14px; }
.metric { border: 1px solid #e5e8ec; border-radius: 6px; padding: 10px; background: #fbfcfd; }
.metric span { display: block; color: #68717d; font-size: 12px; }
.metric strong { display: block; margin-top: 4px; font-size: 20px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 9px 8px; border-top: 1px solid #e5e8ec; text-align: left; vertical-align: top; }
th { color: #4f5965; font-weight: 600; background: #fbfcfd; }
td { overflow-wrap: anywhere; }
"""
