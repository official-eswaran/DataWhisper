"""PDF session-report export.

Fixes vs. the previous version:
* Queries by ``session_id`` (the old code filtered a non-existent ``user_id``
  column and crashed).
* Verifies the caller owns the session before returning its history (IDOR fix).
* Wraps long cells instead of truncating at 60 chars (#46) — the generated SQL
  is the compliance artifact, so a clipped ``WHERE`` clause defeats the export's
  whole purpose. Cells are now ReportLab ``Paragraph``s that wrap to the column
  width; the only length limit is a generous runaway guard.
"""
from __future__ import annotations

from io import BytesIO
from typing import Annotated
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.database import fetch_session_logs, user_can_access_session
from app.core.security import get_current_user

router = APIRouter()

# A runaway guard, not a meaningful truncation: full statements (the reason this
# export exists) fit comfortably, but a pathological multi-megabyte cell can't
# blow up the renderer. The audit log keeps the untruncated text regardless.
CELL_TEXT_LIMIT = 4000

_styles = getSampleStyleSheet()
# Wrap mode "CJK" breaks anywhere, so a long unbroken token (no spaces) still
# stays inside its column instead of overflowing the page.
_CELL_STYLE = ParagraphStyle(
    "report_cell",
    parent=_styles["BodyText"],
    fontSize=7,
    leading=8.5,
    wordWrap="CJK",
)


def _safe(val, limit: int = CELL_TEXT_LIMIT) -> str:
    return str(val)[:limit] if val is not None else ""


def _fmt_time(val) -> str:
    if val is None:
        return ""
    s = str(val)
    return s[:19] if len(s) > 19 else s


def _cell(val) -> Paragraph:
    """A wrapping table cell. Escapes markup and keeps newlines as line breaks
    so multi-line SQL survives, then hands ReportLab something safe to render."""
    text = escape(_safe(val)).replace("\n", "<br/>")
    return Paragraph(text, _CELL_STYLE)


def build_session_pdf(session_id: str, rows) -> bytes:
    """Render a session report to PDF bytes. Pure (no auth/DB) so it can be
    unit-tested directly — the route below just feeds it fetched rows."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    elements = [
        Paragraph("DataWhisper — Session Report", _styles["Title"]),
        Spacer(1, 20),
        Paragraph(f"Session: {escape(str(session_id))}", _styles["Normal"]),
        Spacer(1, 20),
    ]

    if rows:
        table_data = [["#", "Question", "SQL", "Result", "Time"]]
        for i, row in enumerate(rows, 1):
            table_data.append([str(i), _cell(row[0]), _cell(row[1]), _cell(row[2]), _fmt_time(row[3])])
        # Fixed widths (pts) sum to ~451, the usable A4 width at the default
        # 72pt margins, so the wrapping columns have a definite size to wrap to.
        t = Table(table_data, colWidths=[16, 110, 165, 90, 70], repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("FONTSIZE", (0, 0), (-1, 0), 7),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(t)
    else:
        elements.append(Paragraph("No queries found for this session.", _styles["Normal"]))

    doc.build(elements)
    return buffer.getvalue()


@router.get("/pdf/{session_id}")
def export_session_report(
    session_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    org_id = current_user.get("org_id", -1)
    if not user_can_access_session(session_id, current_user.get("sub", ""), current_user.get("role", ""), org_id):
        raise HTTPException(404, "Session not found.")

    rows = fetch_session_logs(session_id, org_id)
    pdf_bytes = build_session_pdf(session_id, rows)

    return StreamingResponse(
        BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=report_{session_id}.pdf"},
    )
