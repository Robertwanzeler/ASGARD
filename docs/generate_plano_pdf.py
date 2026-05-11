#!/usr/bin/env python3
"""
GreenRAN - Plano de Implementação Completo - PDF Generator
============================================================
"""

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
import os

# Configuração do PDF
OUTPUT_DIR = "/home/robert/orange_nuclear/docs"
PDF_FILE = os.path.join(OUTPUT_DIR, "PLANO_IMPLEMENTACAO_COMPLETO.pdf")

def create_pdf():
    """Cria o PDF com o plano de implementação completo"""
    
    c = canvas.Canvas(PDF_FILE, pagesize=A4)
    width, height = A4
    
    # Cores
    TITLE_COLOR = "#1a365d"
    HEADER_COLOR = "#2c5282"
    ACCENT_COLOR = "#2b6cb0"
    TEXT_COLOR = "#2d3748"
    
    # Estilos
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'Title',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=TITLE_COLOR,
        alignment=TA_CENTER,
        spaceAfter=20
    )
    heading_style = ParagraphStyle(
        'Heading',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=HEADER_COLOR,
        spaceAfter=12,
        spaceBefore=20
    )
    subheading_style = ParagraphStyle(
        'SubHeading',
        parent=styles['Heading3'],
        fontSize=12,
        textColor=ACCENT_COLOR,
        spaceAfter=8,
        spaceBefore=10
    )
    body_style = ParagraphStyle(
        'Body',
        parent=styles['BodyText'],
        fontSize=10,
        textColor=TEXT_COLOR,
        alignment=TA_LEFT,
        spaceAfter=8,
        leading=14
    )
    
    def draw_header(title, c, width, y):
        """Desenha o cabeçalho da página"""
        c.setFillColor(HEADER_COLOR)
        c.rect(0, y-30, width, 30, fill=1, stroke=0)
        c.setFillColor("white")
        c.setFont("Helvetica-Bold", 16)
        c.drawString(50, y-20, title)
    
    def add_content(c, content_list, start_y, width):
        """Adiciona conteúdo na página"""
        y = start_y
        for item in content_list:
            if y < 100:
                c.showPage()
                c.setFillColor(HEADER_COLOR)
                c.rect(0, height-30, width, 30, fill=1, stroke=0)
                c.setFillColor("white")
                c.setFont("Helvetica-Bold", 16)
                c.drawString(50, height-20, "GreenRAN - Plano de Implementação")
                y = height - 60
            
            c.setFont("Helvetica", 10)
            lines = item.split('\n')
            for line in lines:
                if y < 50:
                    c.showPage()
                    y = height - 40
                c.drawString(50, y, line)
                y -= 14
            y -= 10
        return y
    
    # ============ PÁGINA 1: CAPA ============
    c.setFillColor(TITLE_COLOR)
    c.rect(0, height-200, width, 200, fill=1, stroke=0)
    
    c.setFillColor("white")
    c.setFont("Helvetica-Bold", 28)
    c.drawCentredString(width/2, height-80, "GREENRAN")
    
    c.setFont("Helvetica", 18)
    c.drawCentredString(width/2, height-120, "Plano de Implementação Completo")
    
    c.setFont("Helvetica", 14)
    c.drawCentredString(width/2, height-150, "Transfer Learning | Priority Scheduler | Federated Learning")
    
    c.setFont("Helvetica", 12)
    c.drawCentredString(width/2, height-180, "UFPA - Universidade Federal do Pará")
    
    c.setFont("Helvetica", 10)
    c.drawCentredString(width/2, height-350, "Abril 2026")
    
    c.showPage()
    
    # ============ PÁGINA 2: RESUMO EXECUTIVO ============
    draw_header("1. RESUMO EXECUTIVO", c, width, height)
    
    content = [
        "OBJETIVO:",
        "Desenvolver e implementar três melhorias baseadas nos artigos científicos e na proposta",
        "oficial do projeto GreenRAN, garantindo o SLA para câmeras de vigilância 4K.",
        "",
        "PRIORIDADES DEFINIDAS:",
        "1. Priority Scheduler (CRÍTICO) - Garantir PRIORITY para câmeras",
        "2. Transfer Learning (MÉDIA) - Melhorar predição ML",
        "3. Federated Learning (ALTA) - Treinamento distribuído",
        "",
        "CENÁRIO ATUAL (verificado):",
        "- Câmeras: 3 dispositivos, latência ~1-1.6ms, throughput ~75 Mbps",
        "- Sensores: 17 dispositivos mMTC",
        "- Latência câmera: < 100ms ✓",
        "- Throughput câmera: ≥ 25 Mbps ✓",
    ]
    
    y = height - 60
    for line in content:
        c.setFont("Helvetica", 10)
        c.drawString(50, y, line)
        y -= 14
    
    # ============ PÁGINA 3: HIERARQUIA DE DECISÃO ============
    c.showPage()
    draw_header("2. HIERARQUIA DE DECISÃO", c, width, height)
    
    content = [
        "REGRAS PARA CÂMERAS (eMBB) - PRIORIDADE MÁXIMA:",
        "----------------------------------------",
        "Latência       Throughput      Decisão    Potência",
        "< 60ms          ≥ 25 Mbps       ALLOWED    25-60%",
        "60-80ms        ≥ 25 Mbps       CONDIT.    70-90%",
        "≥ 80ms         qualquer        BLOCKED    100%",
        "qualquer       < 25 Mbps       BLOCKED    100%",
        "",
        "REGRAS PARA SENSORES (mMTC) - Baseadas na Proposta:",
        "----------------------------------------",
        "Packet Loss     Energia         Decisão",
        "< 5%            normal          ALLOWED",
        "5-10%           normal          CONDIT.",
        "≥ 10%           qualquer        BLOCKED",
        "",
        "ORDEM DE VERIFICAÇÃO (PRIORIDADE):",
        "1. Throughput Câmera < 25Mbps → BLOCKED",
        "2. Latência Câmera ≥ 80ms → BLOCKED",
        "3. Latência Câmera 60-80ms → CONDITIONAL",
        "4. Packet Loss Sensores ≥ 10% → BLOCKED",
        "5. Packet Loss Sensores 5-10% → CONDITIONAL",
        "6. Sensores CVaR + Slope → regras atuais",
        "7. Tudo OK → ALLOWED",
    ]
    
    y = height - 60
    for line in content:
        c.setFont("Courier", 9)
        c.drawString(50, y, line)
        y -= 12
    
    # ============ PÁGINA 4: PLANO 1 - TRANSFER LEARNING ============
    c.showPage()
    draw_header("3. PLANO 1: TRANSFER LEARNING", c, width, height)
    
    content = [
        "OBJETIVO:",
        "Usar Transfer Learning para treinar modelos ML com menos dados,",
        "transferindo conhecimento de domínios similares.",
        "",
        "ARQUITETURA:",
        "Domínio Original (synthetic) → Feature Extractor (CNN) → Fine-tune → Domínio Alvo",
        "",
        "FASES:",
        "FASE 1: Feature Extractor CNN (Semana 1)",
        "FASE 2: Transfer Learning Module (Semana 1-2)",
        "FASE 3: Integração com GreenRAN (Semana 2)",
        "FASE 4: Validação (Semana 2-3)",
        "",
        "ARQUIVOS A CRIAR:",
        "- src/tl/feature_extractor.py",
        "- src/tl/transfer_learning.py",
        "- src/tl/domain_adapter.py",
        "- src/tl/fine_tuner.py",
        "",
        "ARQUIVOS A MODIFICAR:",
        "- src/rapp_ml_predictor.py",
        "- config/ml_thresholds.json",
    ]
    
    y = height - 60
    for line in content:
        c.setFont("Helvetica", 10)
        c.drawString(50, y, line)
        y -= 14
    
    # ============ PÁGINA 5: PLANO 2 - PRIORITY SCHEDULER ============
    c.showPage()
    draw_header("4. PLANO 2: PRIORITY SCHEDULER", c, width, height)
    
    content = [
        "OBJETIVO:",
        "Aprimorar o xApp Slicer com Priority Queue para garantir QoS",
        "diferenciado por tipo de serviço (Câmeras 4K vs Sensores).",
        "",
        "CLASSES DE PRIORITY:",
        "- CRITICAL: Câmaras (SLA < 100ms, 25 Mbps)",
        "- HIGH: Sensores críticos",
        "- NORMAL: UEs gerais",
        "- LOW: Background",
        "",
        "FASES:",
        "FASE 1: Priority Classes (Semana 1)",
        "FASE 2: Priority Queue (Semana 1)",
        "FASE 3: Integração com xApp Slicer (Semana 1-2)",
        "FASE 4: Monitoring (Semana 2)",
        "",
        "ARQUIVOS A CRIAR:",
        "- config/priority_classes.json",
        "- src/scheduler/priority_queue.py",
        "- src/scheduler/packet_classifier.py",
    ]
    
    y = height - 60
    for line in content:
        c.setFont("Helvetica", 10)
        c.drawString(50, y, line)
        y -= 14
    
    # ============ PÁGINA 6: PLANO 3 - FEDERATED LEARNING ============
    c.showPage()
    draw_header("5. PLANO 3: FEDERATED LEARNING", c, width, height)
    
    content = [
        "OBJETIVO:",
        "Implementar Federated Learning para treinar modelos colaborativamente",
        "entre xApps sem centralizar dados.",
        "",
        "ARQUITETURA:",
        "xApp Slicer ─┐",
        "             ├──→ Global Aggregator (rApp) ──→ Updated Model",
        "xApp Energy ─┘",
        "",
        "ALGORITMO: FedAvg (Federated Averaging)",
        "",
        "FASES:",
        "FASE 1: FL Framework (Semana 1-2)",
        "FASE 2: Integração com xApps (Semana 2)",
        "FASE 3: Privacy e Security (Semana 2-3)",
        "FASE 4: Integração com rApp (Semana 3)",
        "FASE 5: Testes (Semana 3-4)",
    ]
    
    y = height - 60
    for line in content:
        c.setFont("Helvetica", 10)
        c.drawString(50, y, line)
        y -= 14
    
    # ============ PÁGINA 7: CRONOGRAMA ============
    c.showPage()
    draw_header("6. CRONOGRAMA", c, width, height)
    
    content = [
        "SEMANA 1:",
        "├── Priority Scheduler: Classes + Queue",
        "├── Transfer Learning: CNN Architecture",
        "└── Federated Learning: FL Framework",
        "",
        "SEMANA 2:",
        "├── Priority Scheduler: Slicer Integration",
        "├── Transfer Learning: TL Pipeline",
        "└── Federated Learning: xApps Integration",
        "",
        "SEMANA 3:",
        "├── Priority Scheduler: Monitoring",
        "├── Transfer Learning: Validation",
        "└── Federated Learning: Privacy + rApp",
        "",
        "SEMANA 4:",
        "├── Priority Scheduler: Tests",
        "├── Transfer Learning: Tests",
        "└── Federated Learning: Tests + Integration",
        "",
        "TOTAL: 4 SEMANAS (PARALELO)",
    ]
    
    y = height - 60
    for line in content:
        c.setFont("Courier", 9)
        c.drawString(50, y, line)
        y -= 12
    
    # ============ PÁGINA 8: REFERÊNCIAS ============
    c.showPage()
    draw_header("7. REFERÊNCIAS", c, width, height)
    
    content = [
        "ARTIGOS CIENTÍFICOS:",
        "- Artigo00: Energy-Efficient O-RAN with Distributed Learning",
        "           and Priority-Aware Slicing",
        "- Artigo01: Transfer Learning para Otimização de Energia em RAN",
        "",
        "PROPOSTA OFICIAL:",
        "- 19175_Proposta_Ajustada (3).pdf",
        "- App1-Vigilância (eMBB): 25 Mbps + <100ms latência",
        "- App2-Monitoramento (mMTC): packet loss < 5%",
        "",
        "ARQUITETURA GREENRAN:",
        "- rApp Orchestrator (Non-RT RIC)",
        "- xApp Slicer (Near-RT RIC)",
        "- xApp Energy Saver (Near-RT RIC)",
        "- ns-3 (simulador)",
        "- FlexRIC (nearRT-RIC)",
    ]
    
    y = height - 60
    for line in content:
        c.setFont("Helvetica", 10)
        c.drawString(50, y, line)
        y -= 14
    
    # Salvar PDF
    c.save()
    print(f"PDF criado com sucesso: {PDF_FILE}")

if __name__ == "__main__":
    create_pdf()