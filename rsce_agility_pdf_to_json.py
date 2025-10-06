# -*- coding: utf-8 -*-
"""
RSCE Agility → PDFs a JSON por año
- Descarga robusta (certifi/curl; opcional modo inseguro por env ALLOW_INSECURE_SSL)
- Cabeceras ORGANIZADOR/LUGAR/FECHA:
    * Primero por líneas (extract_text)
    * Fallback por PALABRAS (extract_words) → recolecta tokens tras el rótulo
      'ENTIDAD ORGANIZADOR(A|AS)' hasta encontrar 'LUGAR' o 'FECHA',
      incluso si están en la línea siguiente o solapados
- Hereda cabecera a páginas de continuación
- Con cabecera: extracción de tablas
- Sin cabecera: reconstrucción por posición (10 columnas esperadas)
- JSON estricto (sin NaN)
"""

import os, re, json, shlex, subprocess
from pathlib import Path
from typing import List, Any, Dict, Optional
import numpy as np
import pandas as pd
import requests, pdfplumber, certifi

# ----------------- Config -----------------
URLS = {
    "2025": "https://www.rsce.es/wp-content/uploads/2025/09/Resultados_Agility_2025.pdf"

}

    # "2024": "https://www.rsce.es/wp-content/uploads/2025/09/Resultados_Agility_2024.pdf",
    # "2023": "https://www.rsce.es/wp-content/uploads/2024/11/Resultados_Agility_2023.pdf",
    # "2022": "https://www.rsce.es/wp-content/uploads/2024/11/Resultados_Agility_2022.pdf",
    
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
    "AÑO","PAGINA","ORGANIZADOR","LUGAR","FECHA",
    "CATEGORIA","Nº LIC.","GRADO","EJEMPLAR","LOE / RRC","CLUB",
    "AGI_PEN","AGI_VEL","JMP_PEN","JMP_VEL",
    "ELIM_AGI","ELIM_JMP"
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

# ----------------- Descarga robusta -----------------
def dload(url: str, dst: Path):
    need = (not dst.exists()) or (dst.stat().st_size < 1024)
    if not need:
        return
    UA = "Mozilla/5.0 (RSCE-Agility-Scraper/1.0)"
    cert_path = certifi.where()
    allow_insecure = os.environ.get("ALLOW_INSECURE_SSL", "").lower() in {"1","true","yes"}

    def _is_pdf(x):
        return (x[:4] == b"%PDF") if isinstance(x, (bytes, bytearray)) else (Path(x).read_bytes()[:4] == b"%PDF")

    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
        r.raise_for_status()
        if not _is_pdf(r.content): raise ValueError("no %PDF")
        dst.write_bytes(r.content); return
    except Exception as e: print("[dload] requests default:", e)

    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60, verify=cert_path)
        r.raise_for_status()
        if not _is_pdf(r.content): raise ValueError("no %PDF")
        dst.write_bytes(r.content); return
    except Exception as e: print("[dload] requests certifi:", e)

    try:
        cmd = f'curl --fail --location --silent --show-error --user-agent "{UA}" --cacert "{cert_path}" --output "{dst}" "{url}"'
        subprocess.run(shlex.split(cmd), check=True)
        if not _is_pdf(dst): raise ValueError("curl --cacert no %PDF")
        return
    except Exception as e: print("[dload] curl --cacert:", e)

    if allow_insecure:
        cmd = f'curl --fail --location --silent --show-error --insecure --user-agent "{UA}" --output "{dst}" "{url}"'
        subprocess.run(shlex.split(cmd), check=True)
        if not _is_pdf(dst): raise ValueError("curl --insecure no %PDF")
        return
    raise RuntimeError(f"No se pudo descargar {url}")

# ----------------- Utilidades extracción -----------------
def norm_ws(x: Any) -> Any:
    return re.sub(r"\s+", " ", x).strip() if isinstance(x, str) else x

def is_header_row(row: List[Any]) -> bool:
    txt = " ".join([str(x or "").lower() for x in row])
    return sum(1 for t in HEADER_TOKENS if t in txt) >= 3

def make_unique(cols: List[str], base="Col") -> List[str]:
    seen, out = {}, []
    for i, c in enumerate(cols, 1):
        c = (c or "").strip() or f"{base}_{i}"
        seen[c] = seen.get(c, 0) + 1
        out.append(c if seen[c] == 1 else f"{c}_{seen[c]}")
    return out

def canon_headers(cols: List[str]) -> List[str]:
    out = []
    for c in cols:
        key = (c or "").strip().lower().replace("  ", " ")
        out.append(CANON.get(key, (c or "").strip()))
    return make_unique(out)

def spanish_num(x):
    if x is None: return None
    if isinstance(x, (int,float)) and not pd.isna(x): return float(x)
    s = str(x).strip()
    if not s or s.lower().startswith("elim"): return None
    s = s.replace(" ", "").replace("\xa0", "")
    if re.fullmatch(r"[-+]?\d{1,3}(\.\d{3})*,\d+|[-+]?\d+,\d+", s): s = s.replace(".", "").replace(",", ".")
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", s):
        try: return float(s)
        except: return None
    return s

def detect_scores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy(); cols = list(df.columns)
    def right_vel(start):
        for j in range(start+1, min(start+4, len(cols))):
            cj = cols[j].lower()
            if "vel" in cj or cj.startswith("col_"): return j
        return None
    if "AGILITY" in df.columns:
        ai = cols.index("AGILITY"); vi = right_vel(ai)
        df = df.rename(columns={"AGILITY":"AGI_PEN"}); cols = list(df.columns)
        if vi is not None:
            v = cols[vi]
            if v not in ("AGI_PEN","JUMPING"): df = df.rename(columns={v:"AGI_VEL"}); cols = list(df.columns)
    if "JUMPING" in df.columns:
        ji = cols.index("JUMPING"); vi = right_vel(ji)
        df = df.rename(columns={"JUMPING":"JMP_PEN"}); cols = list(df.columns)
        if vi is not None:
            v = cols[vi]
            if v not in ("AGI_PEN","JMP_PEN"): df = df.rename(columns={v:"JMP_VEL"}); cols = list(df.columns)
    for need in ("AGI_PEN","AGI_VEL","JMP_PEN","JMP_VEL"):
        if need not in df.columns: df[need] = None
    front = [c for c in ["CATEGORIA","Nº LIC.","GRADO","EJEMPLAR","LOE / RRC","CLUB",
                         "AGI_PEN","AGI_VEL","JMP_PEN","JMP_VEL"] if c in df.columns]
    return df[front + [c for c in df.columns if c not in front]]

def clean_table_with_header(raw: List[List[Any]]) -> pd.DataFrame:
    rows = [[norm_ws(c) for c in r] for r in raw]
    rows = [r for r in rows if any(str(x or "").strip() for x in r)]
    if not rows: return pd.DataFrame()
    cols = canon_headers([str(x or "") for x in rows[0]])
    body = rows[1:]
    df = pd.DataFrame(body, columns=cols)
    df = df.loc[:, df.notna().any(axis=0)]
    df.columns = canon_headers(list(df.columns))
    df = detect_scores(df)
    keys = [c for c in ["Nº LIC.","EJEMPLAR","CLUB"] if c in df.columns]
    if keys: df = df.dropna(how="all", subset=keys)
    for c in ["AGI_PEN","AGI_VEL","JMP_PEN","JMP_VEL"]:
        if c in df.columns: df[c] = df[c].map(spanish_num)
    df["ELIM_AGI"] = df["AGI_PEN"].isna() & df["AGI_VEL"].apply(lambda v: (str(v) if v is not None else "") != "")
    df["ELIM_JMP"] = df["JMP_PEN"].isna() & df["JMP_VEL"].apply(lambda v: (str(v) if v is not None else "") != "")
    for c in ["CATEGORIA","Nº LIC.","GRADO","EJEMPLAR","LOE / RRC","CLUB"]:
        if c not in df.columns: df[c] = None
    return df.reset_index(drop=True)

# ---------- Continuación por posición (sin cabecera) ----------
POS_SCHEMA = [
    "CATEGORIA","Nº LIC.","GRADO","EJEMPLAR","LOE / RRC","CLUB",
    "AGI_PEN","AGI_VEL","JMP_PEN","JMP_VEL"
]

def table_continuation_by_position(raw: List[List[Any]]) -> pd.DataFrame:
    """Interpreta todas las filas como datos y mapea por posición a 10 columnas."""
    if not raw: return pd.DataFrame()
    rows = [[norm_ws(c) for c in r] for r in raw]
    rows = [r for r in rows if any(str(x or "").strip() for x in r)]
    if not rows: return pd.DataFrame()
    if is_header_row(rows[0]):  # si hay cabecera real, que lo trate la otra ruta
        return pd.DataFrame()
    pad_to = 10
    fixed = []
    for r in rows:
        rr = r + [""]*(pad_to - len(r)) if len(r) < pad_to else r[:pad_to]
        fixed.append(rr)
    df = pd.DataFrame(fixed, columns=[f"Col_{i}" for i in range(1, pad_to+1)])
    mapping = dict(zip(POS_SCHEMA, df.columns.tolist()))
    out = pd.DataFrame()
    for k, src in mapping.items():
        out[k] = df[src]
    for c in ["AGI_PEN","AGI_VEL","JMP_PEN","JMP_VEL"]:
        out[c] = out[c].map(spanish_num)
    out["ELIM_AGI"] = out["AGI_PEN"].isna() & out["AGI_VEL"].apply(lambda v: (str(v) if v is not None else "") != "")
    out["ELIM_JMP"] = out["JMP_PEN"].isna() & out["JMP_VEL"].apply(lambda v: (str(v) if v is not None else "") != "")
    out = out[~(out["Nº LIC."].isna() & out["EJEMPLAR"].isna())].reset_index(drop=True)
    return out

# ----------------- Cabeceras de página (tokens robustos) -----------------
def parse_header_tokens(page) -> Dict[str, Optional[str]]:
    """
    Busca rótulos por SECUENCIA DE PALABRAS y recolecta tokens posteriores:
    ENTIDAD + ORGANIZADOR(A|AS)  → ORGANIZADOR := tokens siguientes hasta LUGAR/FECHA
    LUGAR                        → tokens hasta FECHA o salto fuerte de línea
    FECHA                        → tokens siguientes (texto) que luego se normaliza a ISO si se puede
    """
    try:
        words = page.extract_words(use_text_flow=True, keep_blank_chars=False) or []
    except Exception:
        words = []
    if not words:
        return {"ORGANIZADOR": None, "LUGAR": None, "FECHA": None}

    # Ordena por (y,x)
    words = sorted(words, key=lambda w: (float(w.get("top", 0.0)), float(w.get("x0", 0.0))))
    toks = [w.get("text","").strip() for w in words]

    def find_label_idx(seq: List[str], label_parts: List[str]) -> int:
        L = len(label_parts)
        for i in range(len(seq)-L+1):
            ok = True
            for j in range(L):
                if not re.fullmatch(label_parts[j], seq[i+j], flags=re.I):
                    ok = False; break
            if ok: return i
        return -1

    # ORGANIZADOR: 'ENTIDAD' 'ORGANIZADOR(A|AS)?' (segunda parte admite A/AS)
    org_idx = -1
    for i in range(len(toks)-1):
        if re.fullmatch(r"ENTIDAD", toks[i], flags=re.I) and re.fullmatch(r"ORGANIZADOR(A|AS)?", toks[i+1], flags=re.I):
            org_idx = i + 2  # valor comienza tras el rótulo
            break

    # Recolecta valor hasta 'LUGAR' o 'FECHA'
    org_val = None
    if org_idx >= 0:
        tail = []
        for j in range(org_idx, len(toks)):
            if re.fullmatch(r"(LUGAR|FECHA)[:]?", toks[j], flags=re.I):
                break
            tail.append(toks[j])
        org_val = " ".join(tail).strip() or None

    # LUGAR
    lug_idx = find_label_idx(toks, [r"LUGAR[:]?" ])
    lugar_val = None
    if lug_idx >= 0:
        tail = []
        for j in range(lug_idx+1, len(toks)):
            if re.fullmatch(r"FECHA[:]?", toks[j], flags=re.I):
                break
            tail.append(toks[j])
        lugar_val = " ".join(tail).strip() or None

    # FECHA (texto crudo)
    fec_idx = find_label_idx(toks, [r"FECHA[:]?" ])
    fecha_raw = None
    if fec_idx >= 0:
        tail = []
        for j in range(fec_idx+1, len(toks)):
            # hasta fin de línea lógica o hasta otro rótulo por si acaso
            if re.fullmatch(r"(ENTIDAD|ORGANIZADOR(A|AS)?|LUGAR)[:]?", toks[j], flags=re.I):
                break
            tail.append(toks[j])
        fecha_raw = " ".join(tail).strip() or None

    return {"ORGANIZADOR": org_val, "LUGAR": lugar_val, "FECHA": fecha_raw}

def parse_header(page) -> Dict[str, Any]:
    """
    1) Intenta por líneas (rápido).
    2) Si falta ORGANIZADOR/LUGAR/FECHA, completa con parseo por tokens.
    3) Normaliza FECHA a ISO si es posible.
    """
    # 1) líneas
    org = None; lugar = None; fecha_raw = None
    try:
        raw_text = page.extract_text() or ""
    except Exception:
        raw_text = ""
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in raw_text.splitlines() if ln.strip()]

    # ORGANIZADOR en la misma línea o siguiente
    idx = -1
    for i, ln in enumerate(lines):
        if re.search(r"\bENTIDAD\s+ORGANIZADOR(?:A|AS)?\b", ln, flags=re.I):
            idx = i
            m = re.search(r"\bENTIDAD\s+ORGANIZADOR(?:A|AS)?[:\s]*(.*)$", ln, flags=re.I)
            if m and m.group(1).strip():
                candidate = re.split(r"\b(LUGAR|FECHA)\b[:\s]*", m.group(1), maxsplit=1, flags=re.I)[0].strip()
                org = candidate or None
            break
    if org is None and idx >= 0 and idx + 1 < len(lines):
        nxt = lines[idx + 1].strip()
        if not re.match(r"^(LUGAR|FECHA)\b", nxt, flags=re.I):
            org = nxt

    # LUGAR
    for ln in lines:
        m = re.search(r"^LUGAR[:\s]+(.+)$", ln, flags=re.I)
        if m: lugar = m.group(1).strip(); break
    # FECHA
    for ln in lines:
        m = re.search(r"^FECHA[:\s]+(.+)$", ln, flags=re.I)
        if m: fecha_raw = m.group(1).strip(); break

    # 2) Completa con tokens si falta algo
    if org is None or lugar is None or fecha_raw is None:
        tok = parse_header_tokens(page)
        if org is None:   org   = tok.get("ORGANIZADOR") or org
        if lugar is None: lugar = tok.get("LUGAR")       or lugar
        if fecha_raw is None: fecha_raw = tok.get("FECHA") or fecha_raw

    # 3) FECHA a ISO
    fecha_iso = None
    if fecha_raw:
        meses = {
            "enero":"01","febrero":"02","marzo":"03","abril":"04","mayo":"05","junio":"06",
            "julio":"07","agosto":"08","septiembre":"09","setiembre":"09","octubre":"10",
            "noviembre":"11","diciembre":"12"
        }
        mm = re.search(r"(\d{1,2})\s+de\s+([A-Za-záéíóúñ]+)\s+de\s+(\d{4})", fecha_raw, flags=re.I)
        if mm:
            d, mes_txt, y = mm.group(1), mm.group(2).lower(), mm.group(3)
            mes = meses.get(mes_txt)
            if mes:
                try: fecha_iso = f"{y}-{mes}-{int(d):02d}"
                except Exception: fecha_iso = None

    return {"ORGANIZADOR": org, "LUGAR": lugar, "FECHA": fecha_iso or fecha_raw}

# ----------------- Extracción principal -----------------
def extract_pdf(pdf_path: Path, year: str) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    last_header: Dict[str, Optional[str]] = {"ORGANIZADOR": None, "LUGAR": None, "FECHA": None}

    with pdfplumber.open(pdf_path) as pdf:
        for p, page in enumerate(pdf.pages, 1):
            # Cabecera de la página + ffill con la última conocida
            raw_header = parse_header(page)
            header = {
                "ORGANIZADOR": raw_header.get("ORGANIZADOR") or last_header.get("ORGANIZADOR"),
                "LUGAR":       raw_header.get("LUGAR")       or last_header.get("LUGAR"),
                "FECHA":       raw_header.get("FECHA")       or last_header.get("FECHA"),
            }
            last_header = header.copy()

            # 1) Páginas con cabecera (tablas normales)
            got = False
            for settings in (TABLE_SETTINGS_LINES, None):
                try:
                    raw_tables = page.extract_tables(table_settings=settings) if settings else page.extract_tables()
                except Exception:
                    raw_tables = []
                page_frames = []
                for tb in (raw_tables or []):
                    if not tb: continue
                    rows = [[norm_ws(c) for c in r] for r in tb]
                    rows = [r for r in rows if any(str(x or "").strip() for x in r)]
                    if not rows: continue
                    if is_header_row(rows[0]):
                        df = clean_table_with_header(rows)
                        if not df.empty:
                            page_frames.append(df)
                if page_frames:
                    dfp = pd.concat(page_frames, ignore_index=True, sort=False)
                    dfp["AÑO"] = int(year); dfp["PAGINA"] = p
                    dfp["ORGANIZADOR"] = header["ORGANIZADOR"]
                    dfp["LUGAR"] = header["LUGAR"]
                    dfp["FECHA"] = header["FECHA"]
                    frames.append(dfp)
                    got = True
                    break

            if got:
                continue

            # 2) Páginas de continuación (sin cabecera) → por posición
            cont_frames = []
            for settings in (TABLE_SETTINGS_LINES, None):
                try:
                    raw_tables = page.extract_tables(table_settings=settings) if settings else page.extract_tables()
                except Exception:
                    raw_tables = []
                for tb in (raw_tables or []):
                    dfc = table_continuation_by_position(tb)
                    if not dfc.empty:
                        cont_frames.append(dfc)
                if cont_frames:
                    break

            if cont_frames:
                dfa = pd.concat(cont_frames, ignore_index=True, sort=False)
                dfa["AÑO"] = int(year); dfa["PAGINA"] = p
                dfa["ORGANIZADOR"] = header["ORGANIZADOR"]
                dfa["LUGAR"] = header["LUGAR"]
                dfa["FECHA"] = header["FECHA"]
                frames.append(dfa)
            else:
                print(f"[p{p:02d}] aviso: sin cabecera y sin filas por posición.")

    if not frames:
        return pd.DataFrame()

    final = pd.concat(frames, ignore_index=True, sort=False)

    # Deduplicados evidentes
    subset = [c for c in ["AÑO","PAGINA","Nº LIC.","EJEMPLAR","AGI_PEN","JMP_PEN"] if c in final.columns]
    if subset:
        final = final.drop_duplicates(subset=subset, keep="first")

    # Orden/limpieza
    for col in FINAL_ORDER:
        if col not in final.columns: final[col] = None
    final = final[[c for c in FINAL_ORDER] + [c for c in final.columns if c not in FINAL_ORDER]]

    return final

# ----------------- Guardado JSON -----------------
def save_json_records(df: pd.DataFrame, out_path: Path):
    if df is None or df.empty:
        records = []
    else:
        df = df.replace({np.nan: None})
        records = df.to_dict(orient="records")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2, allow_nan=False)

# ----------------- Main -----------------
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
        print(f"[✓] JSON {year} → {json_file} ({0 if df is None or df.empty else len(df)} filas)")

if __name__ == "__main__":
    main()
