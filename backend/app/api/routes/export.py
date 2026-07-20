"""PDF session-report export.

Fixes vs. the previous version:
* Queries by ``session_id`` (the old code filtered a non-existent ``user_id``
  column and crashed).
* Verifies the caller owns the session before returning its history (IDOR fix).
"""
from __future__ import annotations

from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.database import fetch_session_logs, user_can_access_session
from app.core.security import get_current_user

router = APIRouter()


@router.get("/pdf/{session_id}")
def export_session_report(
    session_id: str,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    org_id = current_user.get("org_id", -1)
    if not user_can_access_session(session_id, current_user.get("sub", ""), current_user.get("role", ""), org_id):
        raise HTTPException(404, "Session not found.")

    rows = fetch_session_logs(session_id, org_id)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = [
        Paragraph("DataWhisper — Session Report", styles["Title"]),
        Spacer(1, 20),
        Paragraph(f"Session: {session_id}", styles["Normal"]),
        Spacer(1, 20),
    ]

    def _safe(val, limit=60):
        return str(val)[:limit] if val is not None else ""

    def _fmt_time(val):
        if val is None:
            return ""
        s = str(val)
        return s[:19] if len(s) > 19 else s

    if rows:
        table_data = [["#", "Question", "SQL", "Result", "Time"]]
        for i, row in enumerate(rows, 1):
            table_data.append([str(i), _safe(row[0]), _safe(row[1]), _safe(row[2]), _fmt_time(row[3])])
        t = Table(table_data, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(t)
    else:
        elements.append(Paragraph("No queries found for this session.", styles["Normal"]))

    doc.build(elements)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=report_{session_id}.pdf"},
    )
