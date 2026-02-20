from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors

from app.core.database import get_audit_db
from app.core.security import get_current_user

router = APIRouter()


@router.get("/pdf/{session_id}")
def export_session_report(
    session_id: str,
    _user: Annotated[dict, Depends(get_current_user)] = None,
):
    """Export all queries and results from a session as a PDF report."""
    conn = get_audit_db()
    rows = conn.execute(
        "SELECT natural_query, generated_sql, result_summary, created_at "
        "FROM audit_logs WHERE user_id = ? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()
    conn.close()

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    # Title
    elements.append(Paragraph("DataWhisper — Session Report", styles["Title"]))
    elements.append(Spacer(1, 20))
    elements.append(Paragraph(f"Session: {session_id}", styles["Normal"]))
    elements.append(Spacer(1, 20))

    def _safe(val, limit=60):
        return str(val)[:limit] if val is not None else ""

    def _fmt_time(val):
        if val is None:
            return ""
        s = str(val)
        # Trim microseconds: "2024-01-15 14:23:45.123456" → "2024-01-15 14:23:45"
        return s[:19] if len(s) > 19 else s

    # Query table
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
