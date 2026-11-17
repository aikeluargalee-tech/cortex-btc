"""Parser for the external "Data Hub" telemetry snapshot.

The snapshot is a markdown report produced elsewhere. It is ingested as
**contextual evidence only**: nothing parsed here can change a stance. Its value
is breadth — derivatives, options, ETF flows, macro — that our own feed does not
collect.

Parsing is deliberately tolerant: unknown sections and rows are kept verbatim so
a changed report degrades into "more raw table" rather than an exception.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SECTION_RE = re.compile(r"^#{2,3}\s+(.*)$")
BULLET_RE = re.compile(r"^\s*[-*]\s+\*\*(.+?):?\*\*\s*:?\s*(.+)$")
STATUSES = {"OK", "UNAVAILABLE", "STALE", "PROXY", "PARTIAL_VENUE", "PARTIAL VENUE"}
NUMBER_RE = re.compile(r"[-+]?\$?\d[\d,]*\.?\d*")


@dataclass(frozen=True, slots=True)
class TelemetryRow:
    section: str
    label: str
    value: str
    source: str = ""
    status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "label": self.label,
            "value": self.value,
            "source": self.source,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class TelemetryReport:
    source: str
    as_of: str | None
    spot_price: float | None
    coverage: str
    rows: tuple[TelemetryRow, ...]
    lookup: dict[str, TelemetryRow] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def row(self, label_fragment: str) -> TelemetryRow | None:
        for key, row in self.lookup.items():
            if label_fragment.lower() in key.lower():
                return row
        return None

    def number(self, label_fragment: str) -> float | None:
        row = self.row(label_fragment)
        if row is None:
            return None
        match = NUMBER_RE.search(row.value.replace(",", ""))
        return float(match.group(0).replace("$", "")) if match else None

    def section(self, name_fragment: str) -> list[TelemetryRow]:
        return [r for r in self.rows if name_fragment.lower() in r.section.lower()]

    def gate_zero(self) -> dict[str, Any]:
        """Their hard-down-trend veto, read verbatim. Not our gate."""
        veto = self.row("Veto Status")
        condition_a = self.row("Condition A")
        condition_b = self.row("Condition B")
        portfolio = self.row("Portfolio Status")
        entry = self.row("New BTC Entry")
        return {
            "veto_status": _clean(veto.value) if veto else None,
            "condition_a": _clean(condition_a.value) if condition_a else None,
            "condition_b": _clean(condition_b.value) if condition_b else None,
            "portfolio_status": _clean(portfolio.value) if portfolio else None,
            "new_entry": _clean(entry.value) if entry else None,
            "spot_price": self.spot_price,
            "as_of": self.as_of,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "as_of": self.as_of,
            "spot_price": self.spot_price,
            "coverage": self.coverage,
            "gate_zero": self.gate_zero(),
            "sections": _group(self.rows),
            "warnings": list(self.warnings),
        }


def _clean(value: str) -> str:
    return value.replace("`", "").strip()


def _group(rows: Iterable[TelemetryRow]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.section, []).append(row.to_dict())
    return [{"section": name, "rows": items} for name, items in grouped.items()]


def _split_row(line: str) -> list[str]:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return cells


def parse_telemetry(text: str, *, source: str = "telemetry") -> TelemetryReport:
    lines = text.splitlines()
    rows: list[TelemetryRow] = []
    section = ""
    warnings: list[str] = []
    as_of: str | None = None
    spot_price: float | None = None
    coverage = ""

    header_match = re.search(r"Retrieval Time \(UTC\):\*\*\s*`([^`]+)`", text)
    if header_match:
        as_of = header_match.group(1).strip()
    price_match = re.search(r"Spot Price \(BTCUSDT\):\*\*\s*`\$([\d,\.]+)`", text)
    if price_match:
        spot_price = float(price_match.group(1).replace(",", ""))
    coverage_match = re.search(r"Coverage:\*\*\s*`([^`]+)`", text)
    if coverage_match:
        coverage = coverage_match.group(1).strip()

    if as_of is None:
        warnings.append("retrieval timestamp not found")

    index = 0
    while index < len(lines):
        line = lines[index]
        heading = SECTION_RE.match(line)
        if heading:
            section = _clean(heading.group(1))
            index += 1
            continue
        if line.strip().startswith("|") and index + 1 < len(lines) and re.match(r"^\|[\s\-:|]+\|$", lines[index + 1].strip()):
            header = _split_row(line)
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = _split_row(lines[index])
                if cells and any(cells):
                    rows.append(_row_from_cells(section, header, cells))
                index += 1
            continue
        bullet = BULLET_RE.match(line)
        if bullet:
            rows.append(
                TelemetryRow(
                    section=section,
                    label=_clean(bullet.group(1)),
                    value=_clean(bullet.group(2)),
                    source="",
                    status="",
                )
            )
            index += 1
            continue
        index += 1

    lookup: dict[str, TelemetryRow] = {}
    for row in rows:
        lookup.setdefault(row.label, row)

    if not rows:
        warnings.append("no table rows parsed")
    return TelemetryReport(
        source=source,
        as_of=as_of,
        spot_price=spot_price,
        coverage=coverage,
        rows=tuple(rows),
        lookup=lookup,
        warnings=tuple(warnings),
    )


def _row_from_cells(section: str, header: list[str], cells: list[str]) -> TelemetryRow:
    padded = cells + [""] * (4 - len(cells))
    label = _clean(padded[0])
    value = _clean(padded[1])
    source = _clean(padded[2])
    status = _clean(padded[3])
    if status.upper() not in STATUSES:
        # Some tables have no status column; keep whatever the third cell holds.
        if source.upper() in STATUSES:
            status, source = source, ""
        else:
            source, status = source, ""
    return TelemetryRow(section=section, label=label, value=value, source=source, status=status)


def load_telemetry(path: str | Path | None) -> TelemetryReport | None:
    if path is None:
        return None
    target = Path(path)
    if not target.exists():
        return None
    text = target.read_text(encoding="utf-8-sig", errors="replace")
    return parse_telemetry(text, source=str(target.name))


def freshness_warning(report: TelemetryReport, *, now: datetime | None = None, max_age_minutes: int = 90) -> str | None:
    """Telemetry older than the limit is context that may no longer describe the market."""
    if not report.as_of:
        return "external telemetry has no retrieval timestamp"
    try:
        stamp = datetime.strptime(report.as_of, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    except ValueError:
        return f"external telemetry timestamp not understood: {report.as_of}"
    now = now or datetime.now(timezone.utc)
    age = (now - stamp).total_seconds() / 60
    if age > max_age_minutes:
        return f"external telemetry is {age/60:.1f}h old (limit {max_age_minutes/60:.1f}h)"
    return None


__all__ = [
    "TelemetryReport",
    "TelemetryRow",
    "freshness_warning",
    "load_telemetry",
    "parse_telemetry",
]
