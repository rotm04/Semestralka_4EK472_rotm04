"""
Příprava dlouhého datasetu pozorování "linka x průzkum" z oblastních průzkumů PID.

Zdroj: oficiální sborníky PID (Sestava 9 – souhrn za linku), roky 2017–2019.
Základní jednotka: jedna linka v jednom konkrétním průzkumu (route-survey observation).
Opakované linky se NIKDY nesčítají ani neprůměrují – každý průzkum = samostatné pozorování.

Spuštění:
    python prepare_pid_dataset.py                 # všech 5 průzkumů
    python prepare_pid_dataset.py --surveys 2019_East
    python prepare_pid_dataset.py --dump-text     # uloží text kandidátních stran pro ruční kontrolu
    python prepare_pid_dataset.py --strict        # nenulový exit kód při jakékoli chybě kontroly
PDF se hledají v data_raw/ (a případně stáhnou). Lze je tam dát i ručně.
"""
import argparse
import datetime as dt
import io
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pdfplumber
import requests

ROOT = Path(__file__).resolve().parent
PDF_DIR = ROOT / "data_raw"
DATA_DIR = ROOT / "data"

MIN_ROUTE, MAX_ROUTE = 100, 299          # rozsah městských linek
LOW_SERVICE_THRESHOLD = 10               # JEN příznak, nikoli filtr
RATIO_TOLERANCE = 0.06                   # PDF uvádí osobokm/vozokm na 1 des. místo

MAIN_CSV = DATA_DIR / "praha_mestske_linky_2017_2019.csv"
META_CSV = DATA_DIR / "survey_metadata.csv"
QUALITY_CSV = DATA_DIR / "extraction_quality_report.csv"
ROWCHECK_CSV = DATA_DIR / "extraction_row_checks.csv"
UNMATCHED_CSV = DATA_DIR / "extraction_unmatched_lines.csv"
PER_SURVEY_DIR = DATA_DIR / "pruzkumy"

# ------------------------------------------------------------------
# KONFIGURACE PRŮZKUMŮ
# Pole označená *_status říkají, odkud hodnota pochází. Nic z toho,
# co nepochází z PDF, nesmí být považováno za ověřené.
#  - pdf_pages: None = automatická detekce; jinak seznam stran (od 1)
#  - expected_n_routes: počet městských linek uvedený v ÚVODU zprávy;
#    None = zatím neověřeno (doplnit po přečtení PDF)
#  - control_values: {označení_linky_v_tabulce: (osoby, vozokm, osobokm)}
#    ručně opsané z PDF; prázdné = kontrola neprovedena
#  - special_routes: {štítek_v_tabulce: {"original": ..., "evaluated": ...}}
#  - school_routes: seznam označení školních linek (po ručním ověření v PDF)
# ------------------------------------------------------------------
SURVEYS = [
    dict(survey_id="2017_North", area_cs="Sever", area_en="North", year=2017,
         period="April", month_label="duben 2017",
         url="https://data.pid.cz/pruzkumy/2017_BUS_SEVER_MESTSKE_LINKY_sbornik.pdf",
         pdf_pages=None,
         expected_n_routes=28, expected_n_total=73, expected_total_persons=315415,
         expected_n_routes_status="PDF_intro: 28 městských, 20 příměstských, 25 regionálních",
         extra_city_routes=["551", "557", "575"],   # městské linky mimo 100-299: 25 + 3 = 28 (viz úvod)
         control_values={"102": (7473, 1102.5, 19372.0), "136": (40635, 5083.5, 159989.6),
                         "575": (181, 6.6, 383.6)},
         special_routes={}, school_routes=None,
         school_status="NOT_STATED_IN_PDF_INTRO (551/557/575 pravděpodobně školní – NEOVĚŘENO)",
         notes="28 = 25 linek 100-299 + 551, 557, 575. Úvodní seznam i součty to potvrzují."),
    dict(survey_id="2017_Northwest", area_cs="Severozápad", area_en="Northwest", year=2017,
         period="October", month_label="říjen 2017",
         url="https://data.pid.cz/pruzkumy/2017_BUS_SEVEROZAPAD_MESTSKE_LINKY_sbornik.pdf",
         pdf_pages=None,
         expected_n_routes=25, expected_n_total=39, expected_total_persons=207781,
         expected_n_routes_status="PDF_intro: 25 městských (vč. 3 školních), 14 příměstských",
         extra_city_routes=[], control_values={"107": (22805, 2293.8, 93738.3), "255": (55, 11.0, 231.0)}, special_routes={},
         school_routes=["255", "260", "265"],
         school_status="PDF_intro: 3 školní linky; identita = 25x-26x (počet souhlasí) – inferováno",
         notes="Linky 100 a 119 sledovány v září 2016 mimo tento sborník; v Sestavě 9 NEJSOU."),
    dict(survey_id="2018_Southwest", area_cs="Jihozápad", area_en="Southwest", year=2018,
         period="April", month_label="duben 2018",
         url="https://data.pid.cz/pruzkumy/2018_BUS_JIHOZAPAD_MESTSKE_LINKY_sbornik.pdf",
         pdf_pages=None,
         expected_n_routes=24, expected_n_total=58, expected_total_persons=143971,
         expected_n_routes_status="PDF_intro: 24 městských (vč. 5 školních), 34 příměstských",
         extra_city_routes=[], control_values={"100": (3454, 1197.6, 29024.6), "269": (27, 3.4, 104.4)}, special_routes={},
         school_routes=["258", "261", "266", "269", "271"],
         school_status="PDF_intro: 5 školních linek; identita = 25x-27x (počet souhlasí) – inferováno",
         notes=""),
    dict(survey_id="2018_South", area_cs="Jih", area_en="South", year=2018,
         period="October", month_label="říjen 2018",
         url="https://data.pid.cz/pruzkumy/2018_BUS_JIH_MESTSKE_LINKY_sbornik.pdf",
         pdf_pages=None,
         expected_n_routes=30, expected_n_total=61, expected_total_persons=363876,
         expected_n_routes_status="PDF_intro: 30 městských (vč. 4 školních), 31 příměstských a regionálních",
         extra_city_routes=[], control_values={"106": (9194, 1368.4, 25420.5), "X113": (101, 72.0, 276.0)},
         special_routes={
             "X113": {"original": "X113", "evaluated": 293, "status": "PDF_intro: ve vyhodnocení jako linka 293"},
             "X363": {"original": "X363", "evaluated": 743, "status": "PDF_intro: ve vyhodnocení jako linka 743"},
         },
         school_routes=["254", "256", "270", "272"],
         school_status="PDF_intro: 4 školní linky; identita = 25x-27x (počet souhlasí) – inferováno",
         notes="X113 a X363 vyhodnoceny jako 293 a 743 – nejsou to běžné linky 293/743."),
    dict(survey_id="2019_East", area_cs="Východ", area_en="East", year=2019,
         period="April", month_label="duben 2019",
         url="https://data.pid.cz/pruzkumy/2019_BUS19VY-SBORNIK.pdf",
         pdf_pages=[14, 15, 16],   # ověřeno v původním projektu
         expected_n_routes=34, expected_n_total=None, expected_total_persons=None,
         expected_n_routes_status="from_previous_project_output_not_PDF_intro (úvod 2019 nebyl zkontrolován)",
         extra_city_routes=[],
         control_values={   # převzato z původního skriptu (ověřeno vůči PDF autorem projektu)
             "101": (3998, 815.7, 9736.2), "109": (7425, 1767.0, 31930.6),
             "207": (13946, 1407.3, 26117.6), "223": (8541, 1855.3, 29879.5),
             "240": (5233, 1933.2, 33877.7)},
         special_routes={}, school_routes=None,
         school_status="NOT_CHECKED (úvod 2019 nezkontrolován; 252,253,259,262,264,267 jsou pravděpodobně školní)",
         notes=""),
]

MONTHS_GEN = {"ledna": 1, "února": 2, "března": 3, "dubna": 4, "května": 5, "června": 6,
              "července": 7, "srpna": 8, "září": 9, "října": 10, "listopadu": 11, "prosince": 12}

# ------------------------------------------------------------------
# ŘÁDEK SESTAVY 9: linka, osoby, vozokm, stat.km, osobokm, osobokm/vozokm,
# %OBS-ST5, %ST5, spoje, nevyhovující spoje. Řádky TAM/ZPĚT se nezachytí.
# Pozn.: pořadí a význam sloupců je převzato z původního (ověřeného) skriptu pro
# 2019; u ostatních PDF to musí potvrdit hlavička tabulky (viz --dump-text).
# ------------------------------------------------------------------
_DEC = r"\d+(?:,\d+)?"
ROW_PATTERN = re.compile(
    r"^\s*(?P<route>X?\d{3})(?P<marker>\*)?\s+"
    rf"(?P<passengers>\d+)\s+(?P<vehicle_km>{_DEC})\s+(?P<statistical_km>{_DEC})\s+"
    rf"(?P<passenger_km>{_DEC})\s+(?P<ratio>{_DEC})\s+(?P<obs_st5>{_DEC})\s+(?P<st5>{_DEC})\s+"
    r"(?P<trips>\d+)\s+(?P<unsuitable>\d+)\s*$"
)
# Kandidát na datový řádek, který vzor nezachytil (kontrola, aby nic nezmizelo potichu)
CANDIDATE_PATTERN = re.compile(r"^\s*X?\d{3}\*?\s+\d")
S9_HEADING = re.compile(r"Sestava\s*9", re.IGNORECASE)
CELKEM_PATTERN = re.compile(
    rf"^\s*CELKEM\s+(?P<passengers>\d+)\s+(?P<vehicle_km>{_DEC})\s+(?P<statistical_km>{_DEC})\s+"
    rf"(?P<passenger_km>{_DEC})\s+(?P<ratio>{_DEC})\s+(?P<obs_st5>{_DEC})\s+(?P<st5>{_DEC})\s+"
    r"(?P<trips>\d+)\s+(?P<unsuitable>\d+)\s*$")
HDR_DATE = re.compile(r"DATUM PRŮZKUMU:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})")
HDR_WINDOW = re.compile(r"ZVOLENÉ OBDOBÍ:\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")
INTRO_DATES = re.compile(
    r"ve\s+dnech\s+(\d{1,2})\.\s*(?:([a-zěščřžýáíéúůďťňó]+)\s+)?[–-]\s*(\d{1,2})\.\s*"
    r"([a-zěščřžýáíéúůďťňó]+)\s+(\d{4})", re.IGNORECASE)

COLUMNS = [
    "route_num", "route_id_original", "route_num_evaluated", "route_label_table", "route_name_intro",
    "survey_id", "survey_year", "survey_area", "survey_area_cs",
    "survey_date_start", "survey_date_end", "survey_main_date", "survey_period",
    "observation_window", "line_class", "in_core_range_100_299", "is_school_route", "is_special_route",
    "source_pdf_url", "source_pdf_page", "extraction_status", "analysis_ready",
    "low_service_count_flag",
    "output_cestujici_pruzkum", "input_vozokm_den", "statisticke_km",
    "output_osobokm_den", "osobokm_na_vozokm_pdf", "podil_osobokm_obs_st5_pdf",
    "podil_osobokm_st5_pdf", "input_pocet_spoju_den", "nevyhovujici_spoje",
    "podil_nevyhovujicich_spoju_pct", "osobokm_na_vozokm", "cestujici_na_spoj",
]
NUMERIC_COLS = ["output_cestujici_pruzkum", "input_vozokm_den", "statisticke_km",
                "output_osobokm_den", "osobokm_na_vozokm_pdf", "input_pocet_spoju_den",
                "nevyhovujici_spoje"]


def czech_number(text):
    return float(text.replace(",", "."))


# ------------------------------------------------------------------
# PDF
# ------------------------------------------------------------------
def get_pdf(cfg, pdf_dir, allow_download=True):
    """Vrátí (cesta, stav). Nikdy nevyhazuje výjimku – stav popisuje, co se stalo."""
    path = Path(pdf_dir) / Path(cfg["url"]).name
    if path.exists() and path.stat().st_size > 0:
        return path, "LOCAL_FILE"
    if not allow_download:
        return None, "PDF_NOT_AVAILABLE_NO_DOWNLOAD"
    try:
        r = requests.get(cfg["url"], headers={"User-Agent": "Mozilla/5.0"}, timeout=180)
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
        return path, "DOWNLOADED"
    except Exception as exc:  # síťová chyba, 404, ...
        return None, f"PDF_NOT_AVAILABLE ({type(exc).__name__}: {str(exc)[:120]})"


def read_pages(path):
    with pdfplumber.open(path) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


def detect_pages(pages_text, cfg):
    """Strany se Sestavou 9. Konfigurace má přednost před automatickou detekcí."""
    if cfg.get("pdf_pages"):
        return list(cfg["pdf_pages"]), "CONFIG"
    found = []
    for i, txt in enumerate(pages_text, start=1):
        if S9_HEADING.search(txt) and any(ROW_PATTERN.match(l) for l in txt.splitlines()):
            found.append(i)
    return found, "AUTO_DETECT"


def classify(label, cfg):
    """Třída linky podle číselné konvence PID + údajů úvodu zprávy (extra_city_routes, X-linky).
    mestska: 100-299, speciální X-linky a linky, které úvod výslovně počítá mezi městské (např. 551);
    prijmestska: 300-399; regionalni: 400+ (konvence podle čísla, ne výrok PDF)."""
    if label in cfg["special_routes"] or label.startswith("X"):
        return "mestska"
    n = int(label)
    if MIN_ROUTE <= n <= MAX_ROUTE or label in cfg.get("extra_city_routes", []):
        return "mestska"
    return "prijmestska" if 300 <= n < 400 else "regionalni"


def parse_pages(pages_text, pages, cfg):
    """Vrátí (všechny řádky linek všech tříd, nezachycené kandidátní řádky, CELKEM řádek nebo None)."""
    records, unmatched, celkem = [], [], None
    for pno in pages:
        for line in pages_text[pno - 1].splitlines():
            mc = CELKEM_PATTERN.match(line)
            if mc:
                celkem = {k: (int(v) if k in ("passengers", "trips", "unsuitable") else czech_number(v))
                          for k, v in mc.groupdict().items()}
                continue
            m = ROW_PATTERN.match(line)
            if not m:
                if CANDIDATE_PATTERN.match(line):
                    unmatched.append({"survey_id": cfg["survey_id"], "page": pno, "line": line.strip()})
                continue
            g = m.groupdict()
            records.append(dict(
                label=g["route"], marker=g["marker"] or "", page=pno,
                passengers=int(g["passengers"]), vehicle_km=czech_number(g["vehicle_km"]),
                statistical_km=czech_number(g["statistical_km"]),
                passenger_km=czech_number(g["passenger_km"]), ratio=czech_number(g["ratio"]),
                obs_st5=czech_number(g["obs_st5"]), st5=czech_number(g["st5"]),
                trips=int(g["trips"]), unsuitable=int(g["unsuitable"]),
                special=cfg["special_routes"].get(g["route"]),
                line_class=classify(g["route"], cfg)))
    return records, unmatched, celkem


def parse_header(pages_text, pages):
    """Datum hlavního dne a časové okno z hlavičky tabulky Sestavy 9."""
    dates, windows = set(), set()
    for p in pages:
        for d, m, y in HDR_DATE.findall(pages_text[p - 1]):
            dates.add(dt.date(int(y), int(m), int(d)).isoformat())
        for h1, m1, h2, m2 in HDR_WINDOW.findall(pages_text[p - 1]):
            windows.add(f"{int(h1)}:{m1}-{int(h2)}:{m2}")
    return sorted(dates), sorted(windows)


def parse_intro_dates(pages_text, max_pages=8):
    """Rozsah dnů průzkumu z úvodu ('ve dnech 10. – 12. října 2017'); (None, None) když nenalezen."""
    text = " ".join(pages_text[:max_pages]).replace("\n", " ")
    m = INTRO_DATES.search(text)
    if not m:
        return None, None
    d1, m1, d2, m2, y = m.groups()
    mon2 = MONTHS_GEN.get(m2.lower())
    mon1 = MONTHS_GEN.get((m1 or m2).lower())
    if not mon1 or not mon2:
        return None, None
    return dt.date(int(y), mon1, int(d1)).isoformat(), dt.date(int(y), mon2, int(d2)).isoformat()


INTRO_ROUTE = re.compile(r"^\s*(X?\d{3})\s+([^\d\s].*?)\s*$")


def parse_intro_route_names(pages_text, max_pages=8):
    """Názvy tras ze seznamu 'Průzkum byl proveden na těchto linkách'. Číslo linky samo
    NEIDENTIFIKUJE trasu (např. linka 269 je v Severu 2017 a Jihozápadu 2018 jiná trasa)."""
    names = {}
    for txt in pages_text[:max_pages]:
        for ln in txt.splitlines():
            m = INTRO_ROUTE.match(ln)
            if m and m.group(1) not in names:
                names[m.group(1)] = m.group(2)
    return names


def build_observations(records, cfg, header):
    """Dlouhý formát podle COLUMNS – jen MĚSTSKÉ linky (ostatní třídy jdou jen do kontrol součtů)."""
    dates, windows = header
    main_date = dates[0] if len(dates) == 1 else pd.NA
    window = windows[0] if len(windows) == 1 else pd.NA
    start, end = cfg.get("_intro_dates", (None, None))
    rows = []
    for r in records:
        if r["line_class"] != "mestska":
            continue
        sp = r["special"]
        is_special = sp is not None or r["label"].startswith("X")
        label_num = int(r["label"].lstrip("X"))
        school = cfg.get("school_routes")
        rows.append({
            "route_name_intro": cfg.get("_route_names", {}).get(r["label"], pd.NA),
            "route_num": np.nan if is_special else label_num,
            "route_id_original": sp["original"] if sp else r["label"],
            "route_num_evaluated": sp["evaluated"] if sp else label_num,
            "route_label_table": r["label"] + r["marker"],
            "survey_id": cfg["survey_id"], "survey_year": cfg["year"],
            "survey_area": cfg["area_en"], "survey_area_cs": cfg["area_cs"],
            "survey_date_start": start or pd.NA, "survey_date_end": end or pd.NA,
            "survey_main_date": main_date, "survey_period": cfg["period"],
            "observation_window": window, "line_class": r["line_class"],
            "in_core_range_100_299": (not is_special) and MIN_ROUTE <= label_num <= MAX_ROUTE,
            "is_school_route": pd.NA if school is None else (r["label"] in school),
            "is_special_route": is_special,
            "source_pdf_url": cfg["url"], "source_pdf_page": r["page"],
            "extraction_status": "OK",
            "output_cestujici_pruzkum": r["passengers"], "input_vozokm_den": r["vehicle_km"],
            "statisticke_km": r["statistical_km"], "output_osobokm_den": r["passenger_km"],
            "osobokm_na_vozokm_pdf": r["ratio"], "podil_osobokm_obs_st5_pdf": r["obs_st5"],
            "podil_osobokm_st5_pdf": r["st5"], "input_pocet_spoju_den": r["trips"],
            "nevyhovujici_spoje": r["unsuitable"],
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    spoje = df["input_pocet_spoju_den"].where(df["input_pocet_spoju_den"] > 0)
    vozokm = df["input_vozokm_den"].where(df["input_vozokm_den"] > 0)
    df["podil_nevyhovujicich_spoju_pct"] = 100 * df["nevyhovujici_spoje"] / spoje
    df["osobokm_na_vozokm"] = df["output_osobokm_den"] / vozokm
    df["cestujici_na_spoj"] = df["output_cestujici_pruzkum"] / spoje
    df["low_service_count_flag"] = df["input_pocet_spoju_den"] < LOW_SERVICE_THRESHOLD
    df["analysis_ready"] = (df["input_vozokm_den"] > 0) & (df["output_osobokm_den"] > 0)
    return df


# ------------------------------------------------------------------
# KONTROLY KVALITY
# ------------------------------------------------------------------
def _q(survey_id, check, value, expected, status, detail=""):
    return dict(survey_id=survey_id, check=check, value=value, expected=expected,
                status=status, detail=detail)


def ratio_inconsistent(rec):
    """Poměr osobokm/vozokm z PDF vs. dopočtený. PDF uvádí vstupy na 0,1 a poměr na 0,1,
    proto se porovnává s intervalem odpovídajícím možnému zaokrouhlení (jinak by drobné linky hlásily falešné chyby)."""
    if rec["vehicle_km"] <= 0.05:
        return False
    lo = max(rec["passenger_km"] - 0.05, 0) / (rec["vehicle_km"] + 0.05)
    hi = (rec["passenger_km"] + 0.05) / (rec["vehicle_km"] - 0.05)
    return not (lo - 0.051 <= rec["ratio"] <= hi + 0.051)


def quality_checks(df, records, celkem, cfg, pages, page_mode, unmatched, header, pdf_status):
    sid, q, row_issues = cfg["survey_id"], [], []
    q.append(_q(sid, "pdf_available", pdf_status, "LOCAL_FILE|DOWNLOADED",
                "OK" if pdf_status in ("LOCAL_FILE", "DOWNLOADED") else "FAIL"))
    if not records:
        q.append(_q(sid, "rows_extracted", 0, cfg["expected_n_routes"], "FAIL",
                    "Nebyla extrahována žádná pozorování."))
        return q, row_issues
    q.append(_q(sid, "pages_used", ",".join(map(str, pages)), "", "INFO", f"zdroj: {page_mode}"))

    def cmp(check, value, expected, detail=""):
        st = "NOT_VERIFIED" if expected is None else ("OK" if value == expected else "MISMATCH")
        q.append(_q(sid, check, value, "" if expected is None else expected, st, detail))

    n_city = sum(r["line_class"] == "mestska" for r in records)
    cmp("rows_city_vs_intro", n_city, cfg["expected_n_routes"], f"zdroj: {cfg['expected_n_routes_status']}")
    cmp("rows_all_classes_vs_intro", len(records), cfg.get("expected_n_total"),
        "součet městských + příměstských + regionálních linek podle úvodu")
    by_class = pd.Series([r["line_class"] for r in records]).value_counts().to_dict()
    q.append(_q(sid, "rows_by_class", str(by_class), "", "INFO", "třída podle čísla linky (viz classify)"))
    cmp("sum_passengers_vs_intro_total", sum(r["passengers"] for r in records),
        cfg.get("expected_total_persons"), "celkový počet přepravených osob uvedený v úvodu zprávy")

    # součty všech řádků vs. řádek CELKEM tabulky
    if celkem is None:
        q.append(_q(sid, "celkem_row_found", "NO", "YES", "REVIEW",
                    "Řádek CELKEM nenalezen na použitých stranách – kontrola součtů neproběhla."))
    else:
        n = len(records)
        tol = 0.05 * n + 0.2          # součet n hodnot zaokrouhlených na 0,1
        for col, key, tolerance in [("passengers", "passengers", 0), ("trips", "trips", 0),
                                    ("unsuitable", "unsuitable", 0), ("vehicle_km", "vehicle_km", tol),
                                    ("statistical_km", "statistical_km", tol * 10),
                                    ("passenger_km", "passenger_km", tol)]:
            sm = sum(r[key] for r in records)
            ok = abs(sm - celkem[col]) <= tolerance
            q.append(_q(sid, f"celkem_{col}", round(sm, 1), celkem[col], "OK" if ok else "FAIL",
                        f"součet {n} řádků vs. řádek CELKEM; tolerance {round(tolerance, 2)}"))

    dates, windows = header
    q.append(_q(sid, "header_main_date", ",".join(dates), "1 datum",
                "OK" if len(dates) == 1 else "REVIEW", ""))
    q.append(_q(sid, "header_observation_window", ",".join(windows), "1 okno",
                "OK" if len(windows) == 1 else "REVIEW", ""))
    start, end = cfg.get("_intro_dates", (None, None))
    if start and dates:
        q.append(_q(sid, "main_date_within_intro_range", dates[0], f"{start}..{end}",
                    "OK" if start <= dates[0] <= end else "MISMATCH"))
    elif not start:
        q.append(_q(sid, "intro_dates", "", "", "NOT_VERIFIED", "Rozsah dnů z úvodu se nepodařilo načíst."))

    if df.empty:
        q.append(_q(sid, "city_rows", 0, cfg["expected_n_routes"], "FAIL", "Žádné městské linky."))
        return q, row_issues
    nm = int(df["route_name_intro"].notna().sum())
    q.append(_q(sid, "route_names_from_intro", nm, len(df), "OK" if nm == len(df) else "REVIEW",
                "názvy tras z úvodního seznamu; chybějící: " + " ".join(df.loc[df["route_name_intro"].isna(), "route_label_table"])))
    q.append(_q(sid, "route_labels_city", " ".join(df["route_label_table"]), "", "INFO"))
    noncity = sorted({r["label"] for r in records if r["line_class"] != "mestska"}, key=lambda x: int(x.lstrip("X")))
    q.append(_q(sid, "route_labels_noncity_excluded", len(noncity), "", "INFO", " ".join(noncity)))
    alll = pd.Series([r["label"] for r in records])
    dup = alll[alll.duplicated(keep=False)]
    q.append(_q(sid, "duplicate_route_labels", dup.nunique(), 0, "OK" if dup.empty else "FAIL",
                " ".join(sorted(set(dup)))))
    for lab in dup.unique():
        row_issues.append(dict(survey_id=sid, route_label_table=lab, issue="DUPLICATE_LABEL_IN_SURVEY"))
    q.append(_q(sid, "unmatched_candidate_lines", len(unmatched), 0,
                "OK" if not unmatched else "REVIEW", "viz extraction_unmatched_lines.csv"))
    miss = int(df[NUMERIC_COLS].isna().sum().sum())
    q.append(_q(sid, "missing_values_numeric", miss, 0, "OK" if miss == 0 else "FAIL"))
    nonpos = df[(df["input_vozokm_den"] <= 0) | (df["output_osobokm_den"] <= 0)]
    q.append(_q(sid, "nonpositive_vozokm_or_osobokm", len(nonpos), 0, "OK" if nonpos.empty else "REVIEW",
                "tyto řádky mají analysis_ready = FALSE"))
    bad = [r["label"] for r in records if r["line_class"] == "mestska" and ratio_inconsistent(r)]
    q.append(_q(sid, "ratio_pdf_vs_computed", len(bad), 0, "OK" if not bad else "REVIEW",
                f"porovnání s ohledem na zaokrouhlení; podezřelé: {' '.join(bad)}"))
    for lab in bad:
        row_issues.append(dict(survey_id=sid, route_label_table=lab, issue="RATIO_PDF_MISMATCH"))
    badu = df[df["nevyhovujici_spoje"] > df["input_pocet_spoju_den"]]
    q.append(_q(sid, "unsuitable_gt_trips", len(badu), 0, "OK" if badu.empty else "FAIL"))
    for lab in badu["route_label_table"]:
        row_issues.append(dict(survey_id=sid, route_label_table=lab, issue="UNSUITABLE_GT_TRIPS"))
    q.append(_q(sid, "low_service_count_rows", int(df["low_service_count_flag"].sum()), "", "INFO",
                f"spoje < {LOW_SERVICE_THRESHOLD}; pouze příznak, řádky NEJSOU vyřazeny"))
    school = cfg.get("school_routes")
    if school is None:
        q.append(_q(sid, "school_routes", "NA", "", "NOT_VERIFIED", cfg.get("school_status", "")))
    else:
        missing = [x for x in school if x not in set(df["route_label_table"])]
        q.append(_q(sid, "school_routes", " ".join(school), "", "OK" if not missing else "FAIL",
                    f"{cfg.get('school_status', '')}; chybějící v datech: {' '.join(missing)}"))
    if not cfg["control_values"]:
        q.append(_q(sid, "control_values", 0, "", "NOT_VERIFIED",
                    "Nejsou zadány kontrolní hodnoty opsané z PDF – ručně ověřit vzorek."))
    for lab, vals in cfg["control_values"].items():
        r = df[df["route_label_table"] == lab]
        if r.empty:
            q.append(_q(sid, f"control_{lab}", "chybí", vals, "FAIL"))
            continue
        act = (int(r.iloc[0]["output_cestujici_pruzkum"]), round(float(r.iloc[0]["input_vozokm_den"]), 1),
               round(float(r.iloc[0]["output_osobokm_den"]), 1))
        q.append(_q(sid, f"control_{lab}", act, vals, "OK" if act == tuple(vals) else "FAIL"))
    return q, row_issues


BLOCKING = ("control_", "celkem_", "duplicate_route_labels", "rows_city_vs_intro",
            "rows_all_classes_vs_intro", "sum_passengers_vs_intro_total", "school_routes")


def process_survey(cfg, pdf_path, pdf_status):
    """Zpracuje jeden průzkum. Vrací (df, quality, row_issues, unmatched, pages_text)."""
    empty = pd.DataFrame(columns=COLUMNS)
    if pdf_path is None:
        q, _ = quality_checks(empty, [], None, cfg, [], "", [], ([], []), pdf_status)
        return empty, q, [], [], []
    pages_text = read_pages(pdf_path)
    pages, mode = detect_pages(pages_text, cfg)
    if not pages:
        q = [_q(cfg["survey_id"], "pages_used", "", "", "FAIL",
                "Sestava 9 nenalezena – nastavte pdf_pages v SURVEYS (viz --dump-text).")]
        return empty, q, [], [], pages_text
    invalid = [p for p in pages if not 1 <= p <= len(pages_text)]
    if invalid:
        q = [_q(cfg["survey_id"], "pages_used", ",".join(map(str, pages)), f"1..{len(pages_text)}", "FAIL",
                f"Strany {invalid} neexistují (PDF má {len(pages_text)} stran) – opravte pdf_pages v SURVEYS.")]
        return empty, q, [], [], pages_text
    cfg = dict(cfg)
    cfg["_intro_dates"] = parse_intro_dates(pages_text)
    cfg["_route_names"] = parse_intro_route_names(pages_text)
    records, unmatched, celkem = parse_pages(pages_text, pages, cfg)
    header = parse_header(pages_text, pages)
    df = build_observations(records, cfg, header)
    q, issues = quality_checks(df, records, celkem, cfg, pages, mode, unmatched, header, pdf_status)
    failed = [x for x in q if x["status"] in ("FAIL", "MISMATCH") and x["check"].startswith(BLOCKING)]
    if not df.empty:
        if failed:
            df["extraction_status"] = "SURVEY_CHECK_FAILED"
            df["analysis_ready"] = False
        for iss in issues:
            if iss["issue"] == "RATIO_PDF_MISMATCH":
                mask = (df["route_label_table"] == iss["route_label_table"]) & (df["extraction_status"] == "OK")
                df.loc[mask, "extraction_status"] = "REVIEW_RATIO_MISMATCH"
    df.attrs["intro_dates"] = cfg["_intro_dates"]
    df.attrs["header"] = header
    return df, q, issues, unmatched, pages_text


def finalize_columns(df):
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[COLUMNS].copy()
    for c in ["input_pocet_spoju_den", "nevyhovujici_spoje", "output_cestujici_pruzkum",
              "source_pdf_page", "route_num", "route_num_evaluated"]:
        df[c] = df[c].astype("Int64")
    for c in ["is_school_route", "is_special_route", "analysis_ready", "low_service_count_flag",
              "in_core_range_100_299"]:
        df[c] = df[c].astype("boolean")
    return df


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--surveys", nargs="*", help="survey_id (výchozí: všechny)")
    ap.add_argument("--pdf-dir", default=str(PDF_DIR))
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--dump-text", action="store_true",
                    help="uloží text stran se Sestavou 9 do data_raw/text/ pro ruční kontrolu")
    ap.add_argument("--strict", action="store_true", help="exit 1 při FAIL/MISMATCH")
    args = ap.parse_args(argv)

    cfgs = [c for c in SURVEYS if not args.surveys or c["survey_id"] in args.surveys]
    DATA_DIR.mkdir(exist_ok=True)
    PER_SURVEY_DIR.mkdir(parents=True, exist_ok=True)
    all_df, all_q, all_issues, all_unmatched, meta = [], [], [], [], []

    for cfg in cfgs:
        print(f"[{cfg['survey_id']}] zpracovávám…")
        path, status = get_pdf(cfg, args.pdf_dir, allow_download=not args.no_download)
        df, q, issues, unmatched, pages_text = process_survey(cfg, path, status)
        intro_dates = df.attrs.get("intro_dates") or (None, None)
        hdr_dates = (df.attrs.get("header") or ([], []))[0]
        if args.dump_text and pages_text:
            tdir = Path(args.pdf_dir) / "text"
            tdir.mkdir(parents=True, exist_ok=True)
            cand = [i for i, t in enumerate(pages_text, 1) if S9_HEADING.search(t)] or []
            for i in cand:
                (tdir / f"{cfg['survey_id']}_p{i:03d}.txt").write_text(pages_text[i - 1], encoding="utf-8")
        df = finalize_columns(df)
        df.to_csv(PER_SURVEY_DIR / f"{cfg['survey_id']}.csv", index=False, encoding="utf-8-sig")
        all_df.append(df)
        all_q += q
        all_issues += issues
        all_unmatched += unmatched
        meta.append(dict(
            survey_id=cfg["survey_id"], survey_year=cfg["year"], survey_area=cfg["area_en"],
            survey_area_cs=cfg["area_cs"], survey_period=cfg["period"], survey_month_label=cfg["month_label"],
            survey_date_start=intro_dates[0] or pd.NA, survey_date_end=intro_dates[1] or pd.NA,
            survey_main_date=(hdr_dates or [pd.NA])[0],
            survey_dates_status="PARSED_FROM_PDF" if intro_dates[0] else "NOT_PARSED",
            source_pdf_url=cfg["url"], pdf_status=status,
            n_observations=len(df), n_unique_route_ids=df["route_id_original"].nunique(),
            n_analysis_ready=int(df["analysis_ready"].fillna(False).sum()),
            expected_n_city_routes=cfg["expected_n_routes"], expected_n_total_routes=cfg.get("expected_n_total"),
            expected_n_routes_status=cfg["expected_n_routes_status"], notes=cfg["notes"]))

    main_df = pd.concat(all_df, ignore_index=True) if all_df else pd.DataFrame(columns=COLUMNS)
    main_df = main_df.sort_values(["survey_year", "survey_id", "route_num_evaluated", "route_id_original"],
                                  kind="stable").reset_index(drop=True)
    main_df.to_csv(MAIN_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(meta).to_csv(META_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(all_q).to_csv(QUALITY_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(all_issues, columns=["survey_id", "route_label_table", "issue"]).to_csv(
        ROWCHECK_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(all_unmatched, columns=["survey_id", "page", "line"]).to_csv(
        UNMATCHED_CSV, index=False, encoding="utf-8-sig")

    print("\n=== SOUHRN ===")
    print(pd.DataFrame(meta)[["survey_id", "pdf_status", "n_observations", "n_unique_route_ids"]].to_string(index=False))
    bad = [x for x in all_q if x["status"] in ("FAIL", "MISMATCH")]
    nv = [x for x in all_q if x["status"] == "NOT_VERIFIED"]
    print(f"\nKontroly: FAIL/MISMATCH = {len(bad)}, NOT_VERIFIED = {len(nv)} (viz {QUALITY_CSV.name})")
    for x in bad:
        print(f"  ! {x['survey_id']}: {x['check']} -> {x['value']} (očekáváno {x['expected']}) {x['detail']}")
    return 1 if (args.strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
