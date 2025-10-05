# -*- coding: utf-8 -*-
"""
RSCE Agility → PDFs a JSON por año (robusto en descarga y extracción)
- Descarga con User-Agent y valida PDF (%PDF y Content-Type)
- Dos pasadas de extracción: lines + auto
- Propaga ORGANIZADOR/LUGAR/FECHA por página
- JSON estricto (sin NaN), limpia columnas vacías Col_*
"""

import re
import json
from pathlib import Path
from typing import List, Any, Dict
import numpy as np
import pandas as pd
import requests
import pdfplumber
import subprocess, shlex, os, certifi
# ----------------- Config -----------------
URLS = {
    "2025": "https://www.rsce.es/wp-content/uploads/2025/09/Resultados_Agility_2025.pdf",
    "2024": "https://www.rsce.es/wp-content/uploads/2025/09/Resultados_Agility_2024.pdf",
    "2023": "https://www.rsce.es/wp-content/uploads/2024/11/Resultados_Agility_2023.pdf",
    "2022": "https://www.rsce.es/wp-content/uploads/2024/11/Resultados_Agility_2022.pdf",
}

BASE = Path(".").resolve()
RAW = BASE / "data" / "agility" / "raw"
OUT = BASE / "data" / "agility" / "processed"
RAW.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)

HEADER_TOKENS = {
    "categoria", "categoría", "nº lic", "nº lic.", "lic", "grado",
    "ejemplar", "loe", "rrc", "loe / rrc", "club",
    "agility", "jumping", "pen", "pen.", "vel", "vel."
}

CANON = {
    "categoria": "CATEGORIA",
    "categoría": "CATEGORIA",
    "nº lic.": "Nº LIC.",
    "nº lic": "Nº LIC.",
    "grado": "GRADO",
    "ejemplar": "EJEMPLAR",
    "loe / rrc": "LOE / RRC",
    "loe/rrc": "LOE / RRC",
    "club": "CLUB",
    "agility": "AGILITY",
    "jumping": "JUMPING",
}

FINAL_ORDER = [
    "AÑO", "PAGINA",
    "ORGANIZADOR", "LUGAR", "FECHA",
    "CATEGORIA", "Nº LIC.", "GRADO", "EJEMPLAR", "LOE / RRC", "CLUB",
    "AGI_PEN", "AGI_VEL", "JMP_PEN", "JMP_VEL",
    "ELIM_AGI", "ELIM_JMP"
]

TABLE_SETTINGS_LINES = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "intersection_tolerance": 5,
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "edge_min_length": 20,
    "min_words_vertical": 1,
    "min_words_horizontal": 1,
    "keep_blank_chars": False,
    "text_tolerance": 2,
}

# ----------------- Utilidades -----------------


def dload(url: str, dst: Path):
    """
    Descarga validando que realmente es un PDF.
    Secuencia de fallbacks:
      1) requests (verify=True)
      2) requests (verify=certifi.where())
      3) curl --cacert <certifi>
      4) (opcional) si ALLOW_INSECURE_SSL=true → curl --insecure / requests verify=False
    """
    need = (not dst.exists()) or (dst.stat().st_size < 1024)
    if not need:
        return

    UA = "Mozilla/5.0 (RSCE-Agility-Scraper/1.0)"
    cert_path = certifi.where()
    allow_insecure = os.environ.get("ALLOW_INSECURE_SSL", "").lower() in {"1","true","yes"}

    def _validate_pdf(bytes_or_path):
        if isinstance(bytes_or_path, (bytes, bytearray)):
            return bytes_or_path.startswith(b"%PDF")
        p = Path(bytes_or_path)
        return p.exists() and p.stat().st_size > 1024 and p.read_bytes().startswith(b"%PDF")

    # 1) requests normal
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
        r.raise_for_status()
        if not _validate_pdf(r.content):
            raise ValueError(f"Respuesta no es PDF (CT={r.headers.get('Content-Type')})")
        dst.write_bytes(r.content)
        return
    except Exception as e1:
        print(f"[dload] requests verify=True falló: {e1}")

    # 2) requests con certifi.where() explícito
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60, verify=cert_path)
        r.raise_for_status()
        if not _validate_pdf(r.content):
            raise ValueError(f"Respuesta no es PDF (CT={r.headers.get('Content-Type')})")
        dst.write_bytes(r.content)
        return
    except Exception as e2:
        print(f"[dload] requests verify=certifi.where() falló: {e2}")

    # 3) curl con --cacert
    try:
        cmd = f'curl --fail --location --silent --show-error --user-agent "{UA}" --cacert "{cert_path}" --output "{dst}" "{url}"'
        print(f"[dload] {cmd}")
        subprocess.run(shlex.split(cmd), check=True)
        if not _validate_pdf(dst):
            raise ValueError("curl --cacert descargó algo que no es PDF")
        return
    except Exception as e3:
        print(f"[dload] curl --cacert falló: {e3}")

    # 4) *Opcional* inseguro (si lo permites por env)
    if allow_insecure:
        try:
            cmd = f'curl --fail --location --silent --show-error --insecure --user-agent "{UA}" --output "{dst}" "{url}"'
            print(f"[dload] ⚠️ usando --insecure por ALLOW_INSECURE_SSL=true")
            subprocess.run(shlex.split(cmd), check=True)
            if not _validate_pdf(dst):
                raise ValueError("curl --insecure descargó algo que no es PDF")
            return
        except Exception as e4:
            print(f"[dload] curl --insecure falló: {e4}")
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=60, verify=False)
            r.raise_for_status()
            if not _validate_pdf(r.content):
                raise ValueError(f"Respuesta no es PDF (CT={r.headers.get('Content-Type')})")
            dst.write_bytes(r.content)
            return
        except Exception as e5:
            print(f"[dload] requests verify=False falló: {e5}")

    raise RuntimeError(f"No se pudo descargar PDF de {url} con ningún método")

def norm_ws(x: Any) -> Any:
    if isinstance(x, str):
        return re.sub(r"\s+", " ", x).strip()
    return x

def is_header_row(row: List[Any]) -> bool:
    txt = " ".join([str(x or "").lower() for x in row])
    hits = sum(1 for t in HEADER_TOKENS if t in txt)
    return hits >= 3

def make_unique(cols: List[str], base="Col") -> List[str]:
    seen = {}
    out = []
    for i, c in enumerate(cols, 1):
        c = (c or "").strip()
        if not c:
            c = f"{base}_{i}"
        if c in seen:
            seen[c] += 1
            c = f"{c}_{seen[c]}"
        else:
            seen[c] = 1
        out.append(c)
    return out

def canon_headers(cols: List[str]) -> List[str]:
    out = []
    for c in cols:
        key = (c or "").strip().lower()
        key = key.replace("  ", " ")
        out.append(CANON.get(key, (c or "").strip()))
    return make_unique(out)

def spanish_num(x):
    if x is None:
        return None
    if isinstance(x, (int, float)) and not pd.isna(x):
        return float(x)
    s = str(x).strip()
    if not s or s.lower().startswith("elim"):
        return None
    s = s.replace(" ", "").replace("\xa0", "")
    if re.fullmatch(r"[-+]?\d{1,3}(\.\d{3})*,\d+|[-+]?\d+,\d+", s):
        s = s.replace(".", "").replace(",", ".")
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", s):
        try:
            return float(s)
        except Exception:
            return None
    return s

def detect_scores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    cols = list(df.columns)

    def right_vel(start):
        for j in range(start + 1, min(start + 4, len(cols))):
            cj = cols[j].lower()
            if "vel" in cj or cj.startswith("col_"):
                return j
        return None

    if "AGILITY" in df.columns:
        ai = cols.index("AGILITY")
        vi = right_vel(ai)
        df = df.rename(columns={"AGILITY": "AGI_PEN"})
        cols = list(df.columns)
        if vi is not None:
            vname = cols[vi]
            if vname not in ("AGI_PEN", "JUMPING"):
                df = df.rename(columns={vname: "AGI_VEL"})
                cols = list(df.columns)

    if "JUMPING" in df.columns:
        ji = cols.index("JUMPING")
        vi = right_vel(ji)
        df = df.rename(columns={"JUMPING": "JMP_PEN"})
        cols = list(df.columns)
        if vi is not None:
            vname = cols[vi]
            if vname not in ("AGI_PEN", "JMP_PEN"):
                df = df.rename(columns={vname: "JMP_VEL"})
                cols = list(df.columns)

    for need in ("AGI_PEN", "AGI_VEL", "JMP_PEN", "JMP_VEL"):
        if need not in df.columns:
            df[need] = None

    front = [c for c in ["CATEGORIA", "Nº LIC.", "GRADO", "EJEMPLAR", "LOE / RRC", "CLUB",
                         "AGI_PEN", "AGI_VEL", "JMP_PEN", "JMP_VEL"] if c in df.columns]
    df = df[front + [c for c in df.columns if c not in front]]
    return df

def clean_table(raw: List[List[Any]]) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame()
    rows = [[norm_ws(c) for c in r] for r in raw]
    rows = [r for r in rows if any(str(x or "").strip() for x in r)]
    if not rows:
        return pd.DataFrame()

    if is_header_row(rows[0]):
        cols = canon_headers([str(x or "") for x in rows[0]])
        body = rows[1:]
    else:
        width = max(len(r) for r in rows)
        cols = make_unique([""] * width)
        body = [r + [""] * (len(cols) - len(r)) for r in rows]

    df = pd.DataFrame(body, columns=cols)
    df = df.loc[:, df.notna().any(axis=0)]
    df.columns = canon_headers(list(df.columns))
    df = detect_scores(df)

    keys = [c for c in ["Nº LIC.", "EJEMPLAR", "CLUB"] if c in df.columns]
    if keys:
        df = df.dropna(how="all", subset=keys)

    for c in ["AGI_PEN", "AGI_VEL", "JMP_PEN", "JMP_VEL"]:
        if c in df.columns:
            df[c] = df[c].map(spanish_num)

    df["ELIM_AGI"] = df["AGI_PEN"].isna() & df["AGI_VEL"].apply(lambda v: (str(v) if v is not None else "") != "")
    df["ELIM_JMP"] = df["JMP_PEN"].isna() & df["JMP_VEL"].apply(lambda v: (str(v) if v is not None else "") != "")

    for c in ["CATEGORIA", "Nº LIC.", "GRADO", "EJEMPLAR", "LOE / RRC", "CLUB"]:
        if c not in df.columns:
            df[c] = None

    df = df[[c for c in FINAL_ORDER if c in df.columns] + [c for c in df.columns if c not in FINAL_ORDER]]
    return df.reset_index(drop=True)

# -------- Cabeceras de página --------
def parse_header_text_lines(lines: List[str]) -> Dict[str, Any]:
    org = lugar = fecha_raw = None
    idx = -1
    for i, ln in enumerate(lines):
        if re.search(r"ENTIDAD\s+ORGANIZADOR(?:A|AS)?\b", ln, flags=re.I):
            idx = i
            m = re.search(r"ENTIDAD\s+ORGANIZADOR(?:A|AS)?[:\s]*(.*)$", ln, flags=re.I)
            if m and m.group(1).strip():
                org = m.group(1).strip()
            break
    if org is None and idx >= 0 and idx + 1 < len(lines):
        nxt = lines[idx + 1].strip()
        if not re.match(r"^(LUGAR|FECHA)\b", nxt, flags=re.I):
            org = nxt
    for ln in lines:
        m = re.search(r"^LUGAR[:\s]+(.+)$", ln, flags=re.I)
        if m:
            lugar = m.group(1).strip(); break
    for ln in lines:
        m = re.search(r"^FECHA[:\s]+(.+)$", ln, flags=re.I)
        if m:
            fecha_raw = m.group(1).strip(); break
    fecha_iso = None
    if fecha_raw:
        meses = {"enero":"01","febrero":"02","marzo":"03","abril":"04","mayo":"05","junio":"06",
                 "julio":"07","agosto":"08","septiembre":"09","setiembre":"09","octubre":"10",
                 "noviembre":"11","diciembre":"12"}
        mm = re.search(r"(\d{1,2})\s+de\s+([A-Za-záéíóúñ]+)\s+de\s+(\d{4})", fecha_raw, flags=re.I)
        if mm:
            d, mes_txt, y = mm.group(1), mm.group(2).lower(), mm.group(3)
            mes = meses.get(mes_txt)
            if mes:
                try:
                    fecha_iso = f"{y}-{mes}-{int(d):02d}"
                except Exception:
                    fecha_iso = None
    return {"ORGANIZADOR": org, "LUGAR": lugar, "FECHA": fecha_iso or fecha_raw}

def parse_header_from_words(page) -> Dict[str, Any]:
    try:
        words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
    except Exception:
        words = []
    if not words:
        return {"ORGANIZADOR": None, "LUGAR": None, "FECHA": None}
    lines_map = {}
    for w in words:
        top = w.get("top", 0.0)
        line_key = round(float(top), 1)
        lines_map.setdefault(line_key, []).append(w)
    ordered_lines = []
    for k in sorted(lines_map.keys()):
        ws = sorted(lines_map[k], key=lambda d: d.get("x0", 0.0))
        txt = " ".join([norm_ws(w.get("text", "")) for w in ws if w.get("text")])
        if txt.strip():
            ordered_lines.append(txt.strip())
    return parse_header_text_lines(ordered_lines)

def parse_header(page) -> Dict[str, Any]:
    try:
        raw_text = page.extract_text() or ""
    except Exception:
        raw_text = ""
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in raw_text.splitlines() if ln.strip()]
    header = parse_header_text_lines(lines)
    if not header.get("ORGANIZADOR"):
        header2 = parse_header_from_words(page)
        for k in ("ORGANIZADOR", "LUGAR", "FECHA"):
            if not header.get(k) and header2.get(k):
                header[k] = header2[k]
    return header

# ----------------- Extracción principal -----------------
def extract_tables_from_page(page) -> List[pd.DataFrame]:
    """Dos pasadas: líneas y auto."""
    dfs: List[pd.DataFrame] = []
    # PASS 1: líneas
    try:
        tables = page.extract_tables(table_settings=TABLE_SETTINGS_LINES)
    except Exception:
        tables = []
    for tb in (tables or []):
        df = clean_table(tb)
        if not df.empty:
            dfs.append(df)
    # PASS 2: auto (si no hubo tablas útiles)
    if not dfs:
        try:
            tables2 = page.extract_tables()
        except Exception:
            tables2 = []
        for tb in (tables2 or []):
            df = clean_table(tb)
            if not df.empty:
                dfs.append(df)
    return dfs

def extract_pdf(pdf_path: Path, year: str) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    with pdfplumber.open(pdf_path) as pdf:
        for p, page in enumerate(pdf.pages, 1):
            header = parse_header(page)
            dfs = extract_tables_from_page(page)
            print(f"[p{p:02d}] tablas detectadas: {len(dfs)}")
            for df in dfs:
                df["AÑO"] = int(year)
                df["PAGINA"] = p
                df["ORGANIZADOR"] = header.get("ORGANIZADOR")
                df["LUGAR"] = header.get("LUGAR")
                df["FECHA"] = header.get("FECHA")
                frames.append(df)

    if not frames:
        return pd.DataFrame()

    final = pd.concat(frames, ignore_index=True, sort=False)
    # Limpia Col_* vacías
    for c in list(final.columns):
        if c.startswith("Col_"):
            serie = final[c]
            if (serie.isna() | (serie.astype(str).str.strip() == "")).all():
                final = final.drop(columns=[c])
    subset = [x for x in ["AÑO", "PAGINA", "Nº LIC.", "EJEMPLAR", "AGI_PEN", "JMP_PEN"] if x in final.columns]
    if subset:
        final = final.drop_duplicates(subset=subset, keep="first")
    return final

def save_json_records(df: pd.DataFrame, out_path: Path):
    if df is None or df.empty:
        records = []
    else:
        df = df.replace({np.nan: None})
        for c in df.columns:
            if c.startswith("Col_"):
                df[c] = df[c].map(lambda x: None if (isinstance(x, str) and x.strip() == "") else x)
        records = df.to_dict(orient="records")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2, allow_nan=False)

def main():
    for year, url in URLS.items():
        pdf_file = RAW / f"resultados_{year}.pdf"
        json_file = OUT / f"resultados_{year}.json"

        try:
            print(f"[↓] {year} → {url}")
            dload(url, pdf_file)
        except Exception as e:
            print(f"[!] No se pudo descargar {year}: {e}")
            save_json_records(pd.DataFrame(), json_file)
            continue

        try:
            print(f"[•] Extrayendo {pdf_file.name} ...")
            df = extract_pdf(pdf_file, year)
        except Exception as e:
            print(f"[!] Error extrayendo {year}: {e}")
            df = pd.DataFrame()

        save_json_records(df, json_file)
        filas = 0 if df is None or df.empty else len(df)
        print(f"[✓] JSON {year} → {json_file} ({filas} filas)")

if __name__ == "__main__":
    main()
