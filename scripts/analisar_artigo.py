import fitz
import re

PDF_PATH = "/home/robert/orange_nuclear/docs/Task-Specific_Sharpness-Aware_O-RAN_Resource_Management_Using_Multi-Agent_Reinforcement_Learning.pdf"
doc = fitz.open(PDF_PATH)

pages_text = [page.get_text() for page in doc]
full_text = "\n".join(pages_text)

print("=" * 72)
print("  ANALISE COMPLETA: Lotfi, Rajoli & Afghah (IEEE TMLCN 2025)")
print("=" * 72)
print(f"  Paginas: {len(doc)}")
print(f"  DOI: 10.1109/TMLCN.2025.3634994")
print()

for i, text in enumerate(pages_text):
    lines = text.strip().split("\n")
    first_lines = [l.strip() for l in lines if l.strip() and len(l.strip()) > 20]
    heading = first_lines[0] if first_lines else "(vazia)"
    print(f"  Pagina {i+1}: {heading[:100]}")

print("\n" + "=" * 72)
print("  EXTRAINDO SEÇÕES ESPECÍFICAS...")
print("=" * 72)

section_starts = {}
for i, text in enumerate(pages_text):
    for line in text.split("\n"):
        s = line.strip()
        for pat in ["III.", "IV.", "V.", "VI.", "VII."]:
            if s.startswith(pat) and len(s) > 5 and len(s) < 120:
                section_starts.setdefault(pat, []).append((i, s))

for sec, locs in sorted(section_starts.items()):
    for page_num, title in locs:
        print(f"  {sec} encontrado na Pagina {page_num+1}: {title}")

print("\n" + "=" * 72)
print("  SECAO III — SYSTEM MODEL AND PROBLEM FORMULATION")
print("=" * 72)
for i in range(4, 8):
    if i < len(pages_text):
        text = pages_text[i]
        if "III." in text or "System Model" in text or "Problem Formulation" in text:
            print(f"\n--- Pagina {i+1} ---")
            print(text[:3000])
            if len(text) > 3000:
                print("... (truncado)")

print("\n" + "=" * 72)
print("  SECAO IV — PROPOSED TA-SAM MARL")
print("=" * 72)
for i in range(7, 11):
    if i < len(pages_text):
        text = pages_text[i]
        if "TA-SAM" in text or "IV." in text or "Proposed" in text:
            print(f"\n--- Pagina {i+1} ---")
            print(text[:3000])
            if len(text) > 3000:
                print("... (truncado)")

print("\n" + "=" * 72)
print("  SECAO V — EXPERIMENTAL SETUP / SIMULATION")
print("=" * 72)
for i in range(9, 15):
    if i < len(pages_text):
        text = pages_text[i]
        if "V." in text or "Simulation" in text or "Experimental" in text or "TABLE" in text.upper():
            print(f"\n--- Pagina {i+1} ---")
            print(text[:4000])
            if len(text) > 4000:
                print("... (truncado)")

print("\n" + "=" * 72)
print("  SECAO VI — RESULTS")
print("=" * 72)
for i in range(12, 17):
    if i < len(pages_text):
        text = pages_text[i]
        has_result = "VI." in text or "Result" in text or "performance" in text.lower()
        if has_result:
            print(f"\n--- Pagina {i+1} ---")
            print(text[:4000])
            if len(text) > 4000:
                print("... (truncado)")

print("\n" + "=" * 72)
print("  SECAO VII — CONCLUSION")
print("=" * 72)
for i in range(15, 17):
    if i < len(pages_text):
        text = pages_text[i]
        if "VII." in text or "Conclusion" in text:
            print(f"\n--- Pagina {i+1} ---")
            print(text[:2000])
            if len(text) > 2000:
                print("... (truncado)")

print("\n" + "=" * 72)
print("  TABELAS (texto com linhas contendo TABLE)")
print("=" * 72)
for i, text in enumerate(pages_text):
    if "TABLE" in text.upper():
        in_table = False
        table_content = []
        for line in text.split("\n"):
            if "TABLE" in line.upper() and len(line) < 100:
                in_table = True
            if in_table:
                table_content.append(line)
                if len(table_content) > 35:
                    break
        if table_content:
            print(f"\n  Pagina {i+1}:")
            for l in table_content[:35]:
                print(f"    {l.strip()[:150]}")

print("\n" + "=" * 72)
print("  EQUACOES (linhas com =, +, -, sinais matematicos)")
print("=" * 72)
for i, text in enumerate(pages_text):
    eq_lines = []
    for line in text.split("\n"):
        s = line.strip()
        if any(c in s for c in ["=", "∑", "→", "α", "β", "γ", "ρ", "η", "λ", "←"]):
            if 10 < len(s) < 200 and not s.startswith("Received") and not s.startswith("Date"):
                eq_lines.append(s)
    if eq_lines:
        print(f"\n  Pagina {i+1}:")
        for l in eq_lines[:8]:
            print(f"    {l[:180]}")

print("\n" + "=" * 72)
print("  HYPERPARAMETROS E NUMEROS DO SETUP")
print("=" * 72)

for i, text in enumerate(pages_text):
    lines = text.split("\n")
    has_params = False
    for line in lines:
        s = line.strip()
        if any(kw in s.lower() for kw in ["learning rate", "batch size", "discount factor",
            "hidden", "network", "adam", "optimizer", "activation", "relu", "layer",
            "episode", "epoch", "training", "buffer", "capacity", "replay",
            "rho", "entropy", "alpha", "tau", "target", "soft update"]):
            if len(s) > 10 and len(s) < 200:
                if not has_params:
                    print(f"\n  Pagina {i+1}:")
                    has_params = True
                print(f"    {s[:180]}")

print("\n" + "=" * 72)
print("  RESULTADOS NUMERICOS")
print("=" * 72)
for i, text in enumerate(pages_text):
    lines = text.split("\n")
    has_res = False
    for line in lines:
        s = line.strip()
        pcts = re.findall(r'\d+\.?\d*%', s)
        nums = re.findall(r'\b\d+\.\d{2,}\b', s)
        if pcts and len(s) < 200:
            if not has_res:
                print(f"\n  Pagina {i+1}:")
                has_res = True
            print(f"    {s[:180]}")
        if nums and any(kw in s.lower() for kw in ["f1", "accuracy", "completion", "utilization", "improvement", "better", "outperform"]):
            if not has_res:
                print(f"\n  Pagina {i+1}:")
                has_res = True
            print(f"    {s[:180]}")

doc.close()
print("\n" + "=" * 72)
print("  FIM DA ANALISE")
print("=" * 72)
