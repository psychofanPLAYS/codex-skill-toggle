"""Optional Textual dashboard for the skill-toggle registry."""

from __future__ import annotations

from pathlib import Path


STATE_ORDER = {"collision": 0, "disabled": 1, "enabled": 2, "mixed": 3, "missing": 4}
STATE_COLORS = {
    "collision": "red bold",
    "disabled": "yellow",
    "enabled": "green",
    "mixed": "yellow",
    "missing": "red",
}


def build_rows(report: dict) -> list[dict]:
    """Return dashboard rows in the same status-first order as the human list."""
    rows = [dict(entry) for entry in report.get("entries", [])]
    rows.sort(key=lambda row: (STATE_ORDER.get(row["state"], 99), row["name"].casefold()))
    for position, row in enumerate(rows, 1):
        row["position"] = position
    return rows


def run_ui(location: dict[str, Path], registry: dict, report_builder, registry_loader) -> int:
    try:
        from rich.text import Text
        from textual.app import App, ComposeResult
        from textual.widgets import DataTable, Footer, Header, Input, Static
    except ImportError as error:
        requirements = Path(__file__).resolve().parents[1] / "requirements-ui.txt"
        raise SystemExit(
            "st ui requires the optional Textual dependency. "
            f"Install it with: python3 -m pip install -r {requirements}"
        ) from error

    class SkillToggleApp(App):
        TITLE = "Codex Skill Toggle"
        CSS = """
        Screen { background: $surface; }
        #summary { height: 3; padding: 1 2; color: $text; background: $panel; }
        #filter { margin: 1 2; }
        #table { height: 1fr; margin: 0 2; }
        #details { height: 8; padding: 1 2; margin: 1 2; border: round $accent; }
        """
        BINDINGS = [
            ("q", "quit", "Quit"),
            ("r", "refresh_rows", "Refresh"),
            ("s", "cycle_sort", "Sort"),
        ]

        def __init__(self, initial_location: dict[str, Path], initial_registry: dict) -> None:
            super().__init__()
            self.location = initial_location
            self.registry = initial_registry
            self.sort_mode = "status"
            self.rows_by_key: dict[str, dict] = {}

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Static("", id="summary")
            yield Input(placeholder="Filter by name, kind, or status", id="filter")
            yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
            yield Static("Select a row to see its source and disabled paths.", id="details")
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#table", DataTable)
            table.add_columns("#", "STATUS", "KIND", "NAME", "COPIES")
            self.refresh_rows()

        def refresh_rows(self) -> None:
            report = report_builder(self.location, self.registry)
            rows = build_rows(report)
            query = self.query_one("#filter", Input).value.casefold()
            if query:
                rows = [
                    row for row in rows
                    if query in " ".join((row["name"], row["kind"], row["state"])).casefold()
                ]
            if self.sort_mode == "name":
                rows.sort(key=lambda row: row["name"].casefold())
            elif self.sort_mode == "kind":
                rows.sort(key=lambda row: (row["kind"].casefold(), row["name"].casefold()))
            self.rows_by_key = {str(row["position"]): row for row in rows}
            table = self.query_one("#table", DataTable)
            table.clear(columns=False)
            for row in rows:
                status = row["state"]
                table.add_row(
                    str(row["position"]),
                    Text(status.upper(), style=STATE_COLORS.get(status, "red")),
                    row["kind"],
                    row["name"],
                    str(row.get("entry_count", 1)),
                    key=str(row["position"]),
                )
            counts = {state: sum(row["state"] == state for row in report.get("entries", [])) for state in STATE_ORDER}
            self.query_one("#summary", Static).update(
                f"GROUPED ROWS: {len(report.get('entries', []))}   "
                f"COLLISIONS: {counts['collision']}   DISABLED: {counts['disabled']}   ENABLED: {counts['enabled']}   "
                f"SORT: {self.sort_mode}"
            )

        def on_input_changed(self, event: Input.Changed) -> None:
            if event.input.id == "filter":
                self.refresh_rows()

        def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
            row = self.rows_by_key.get(str(event.row_key.value))
            if not row:
                return
            paths = "\n".join(
                [
                    f"{row['name']} [{row['kind']}] — {row['state']}",
                    f"source: {', '.join(row.get('source_paths', []))}",
                    f"disabled: {', '.join(row.get('disabled_paths', []))}",
                ]
            )
            self.query_one("#details", Static).update(paths)

        def action_cycle_sort(self) -> None:
            self.sort_mode = {"status": "name", "name": "kind", "kind": "status"}[self.sort_mode]
            self.refresh_rows()

        def action_refresh_rows(self) -> None:
            self.registry = registry_loader(self.location["registry"])
            self.refresh_rows()

    SkillToggleApp(location, registry).run()
    return 0
