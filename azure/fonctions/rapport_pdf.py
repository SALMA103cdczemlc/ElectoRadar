import io
import os
from datetime import datetime
from zoneinfo import ZoneInfo

SEUIL = int(os.environ.get("SEUIL", "70"))
TZ = ZoneInfo("Africa/Casablanca")
HERE = os.path.dirname(os.path.abspath(__file__))


def shape(text, width_pt=None, font="DejaVu", size=8.5):
    from xml.sax.saxutils import escape
    if not any("\u0600" <= ch <= "\u06FF" for ch in text):
        return escape(text)
    import arabic_reshaper
    from bidi.algorithm import get_display
    from reportlab.pdfbase.pdfmetrics import stringWidth
    lines, cur = [], ""
    for word in text.split():
        cand = f"{cur} {word}".strip()
        if width_pt and cur and stringWidth(arabic_reshaper.reshape(cand), font, size) > width_pt:
            lines.append(cur)
            cur = word
        else:
            cur = cand
    lines.append(cur)
    return "<br/>".join(escape(get_display(arabic_reshaper.reshape(l))) for l in lines)


def build_pdf(rows, day) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    pdfmetrics.registerFont(TTFont("DejaVu", os.path.join(HERE, "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", os.path.join(HERE, "DejaVuSans-Bold.ttf")))
    h1 = ParagraphStyle("h1", fontName="DejaVu-Bold", fontSize=18, leading=24, textColor=colors.HexColor("#1F3864"), spaceAfter=6)
    sub = ParagraphStyle("sub", fontName="DejaVu", fontSize=10, textColor=colors.HexColor("#6B7280"), spaceAfter=14)
    cell = ParagraphStyle("cell", fontName="DejaVu", fontSize=8.5, leading=11)
    body = ParagraphStyle("body", fontName="DejaVu", fontSize=10, leading=14, spaceAfter=8)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.5 * cm)
    alertes = [r for r in rows if r["score_max"] >= SEUIL]
    story = [
        Paragraph("ElectoRadar — Rapport quotidien", h1),
        Paragraph(f"Journée du {day.strftime('%d/%m/%Y')} · généré le {datetime.now(TZ).strftime('%d/%m/%Y à %H:%M')}", sub),
        Paragraph(f"<b>{len(rows)}</b> rumeur(s) suivie(s) dans la journée, dont <b>{len(alertes)}</b> au-dessus du seuil "
                  f"d'alerte ({SEUIL}/100).", body),
        Spacer(1, 6),
    ]
    head = ParagraphStyle("head", fontName="DejaVu-Bold", fontSize=8.5, leading=10, textColor=colors.white)
    widths = [6.8 * cm, 1.5 * cm, 1.7 * cm, 2.0 * cm, 2.2 * cm, 3.6 * cm]
    data = [[Paragraph(h, head) for h in ("Rumeur", "Score max", "Nb publ.", "Portée", "Comptes récents", "Langues")]]
    for r in rows:
        data.append([Paragraph(shape(r["title"] or "", widths[0] - 12), cell), str(r["score_max"]), str(r["publications"]),
                     f"{r['portee']:,}".replace(",", " "), f"{round(100 * (r['part_suspects'] or 0))} %",
                     Paragraph((r["langues"] or "").replace(",", ", "), cell)])
    if len(data) == 1:
        data.append([Paragraph("Aucune rumeur détectée aujourd'hui.", cell), "", "", "", "", ""])
    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "DejaVu"), ("FONTNAME", (0, 0), (-1, 0), "DejaVu-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3864")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D1DC")), ("ALIGN", (1, 1), (4, -1), "CENTER"),
    ]
    for i, r in enumerate(rows, start=1):
        if r["score_max"] >= SEUIL:
            style.append(("BACKGROUND", (1, i), (1, i), colors.HexColor("#FDE2E1")))
    t.setStyle(TableStyle(style))
    story += [t, Spacer(1, 12),
              Paragraph("Les rumeurs sont détectées automatiquement ; seul un fact-checkeur peut les qualifier de vraies "
                        "ou fausses. Données pseudonymisées (loi 09-08).", sub)]
    doc.build(story)
    return buf.getvalue()
