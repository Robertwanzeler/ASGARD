#!/usr/bin/env python3
"""
Gera um PDF consolidado da documentação principal do GreenRAN.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


DOCS_DIR = Path("/home/robert/orange_nuclear/docs")
OUTPUT = DOCS_DIR / "GREENRAN_DOCUMENTACAO_CONSOLIDADA_2026_05.pdf"


def read_doc(name: str) -> str:
    return (DOCS_DIR / name).read_text(encoding="utf-8")


def parse_text(text: str):
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            yield ("spacer", "")
        elif line.startswith("# "):
            yield ("h1", line[2:].strip())
        elif line.startswith("## "):
            yield ("h2", line[3:].strip())
        elif line.startswith("### "):
            yield ("h3", line[4:].strip())
        elif line.startswith("- "):
            yield ("bullet", line[2:].strip())
        elif line.startswith("|"):
            yield ("code", raw)
        else:
            yield ("body", raw)


def build():
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "title",
        parent=styles["Heading1"],
        fontSize=20,
        leading=24,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#12355b"),
        spaceAfter=14,
    )
    h1 = ParagraphStyle(
        "h1",
        parent=styles["Heading1"],
        fontSize=16,
        leading=20,
        textColor=colors.HexColor("#1d4e89"),
        spaceBefore=10,
        spaceAfter=8,
    )
    h2 = ParagraphStyle(
        "h2",
        parent=styles["Heading2"],
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#2463a6"),
        spaceBefore=8,
        spaceAfter=6,
    )
    h3 = ParagraphStyle(
        "h3",
        parent=styles["Heading3"],
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#2c6fb2"),
        spaceBefore=6,
        spaceAfter=4,
    )
    body = ParagraphStyle(
        "body",
        parent=styles["BodyText"],
        fontSize=9.5,
        leading=13,
        alignment=TA_JUSTIFY,
        spaceAfter=4,
    )
    bullet = ParagraphStyle(
        "bullet",
        parent=body,
        leftIndent=14,
        firstLineIndent=-8,
    )
    code = ParagraphStyle(
        "code",
        parent=body,
        fontName="Courier",
        fontSize=8,
        leading=10,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#333333"),
        backColor=colors.HexColor("#f5f7fa"),
    )

    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=1.7 * cm,
        rightMargin=1.7 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )

    story = []
    story.append(Paragraph("GreenRAN - Documentação Consolidada", title))
    story.append(Paragraph(f"Gerado em {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", body))
    story.append(Spacer(1, 0.3 * cm))
    story.append(
        Paragraph(
            "Este PDF reúne a visão consolidada do projeto, a trilha article00, os aplicativos App1/App2/App3, a arquitetura veicular em CARLA 2D, a hierarquia do rApp e os caminhos futuros de adaptação para ML e DRL.",
            body,
        )
    )
    story.append(PageBreak())

    ordered_docs = [
        "README.md",
        "APP1_APP2_ANALISE.md",
        "APP3_VEICULAR_ARQUITETURA.md",
        "CARLA_2D_MODELO_VEICULAR.md",
        "CARLA_NS3_INTEGRACAO.md",
        "ARTICLE00_RESUMO_FINAL.md",
        "ARTICLE00_METODO_E_EXPERIMENTOS.md",
        "NS3_ARTICLE00_TEMPORAL_PIPELINE.md",
    ]

    style_map = {
        "h1": h1,
        "h2": h2,
        "h3": h3,
        "body": body,
        "bullet": bullet,
        "code": code,
    }

    for idx, name in enumerate(ordered_docs):
        for kind, value in parse_text(read_doc(name)):
            if kind == "spacer":
                story.append(Spacer(1, 0.12 * cm))
            elif kind == "bullet":
                story.append(Paragraph(f"• {value}", style_map[kind]))
            elif kind == "code":
                story.append(Paragraph(value.replace(" ", "&nbsp;"), style_map[kind]))
            else:
                story.append(Paragraph(value, style_map[kind]))
        if idx != len(ordered_docs) - 1:
            story.append(PageBreak())

    doc.build(story)
    print(OUTPUT)


if __name__ == "__main__":
    build()
