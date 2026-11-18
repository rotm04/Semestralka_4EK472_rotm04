"""
Testy mechaniky extrakce. POZOR: používají SYNTETICKÁ PDF vytvořená v testu
(reportlab) – ověřují logiku kódu, NEOVĚŘUJÍ skutečné rozložení PDF od PID.
Skutečnou extrakci musí potvrdit extraction_quality_report.csv a ruční kontrola.
"""
import copy
import sys
from pathlib import Path

import pandas as pd
import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import prepare_pid_dataset as P  # noqa: E402

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def fmt(x):
    return str(x).replace(".", ",")


def line(label, pas, vkm, skm, okm, ratio, obs, st5, trips, unsuit):
    return f"{label} {pas} {fmt(vkm)} {fmt(skm)} {fmt(okm)} {fmt(ratio)} {fmt(obs)} {fmt(st5)} {trips} {unsuit}"


def make_pdf(path, pages):
    """pages = seznam seznamů řádků textu."""
    pdfmetrics.registerFont(TTFont("DV", FONT))
    c = canvas.Canvas(str(path))
    for lines in pages:
        c.setFont("DV", 9)
        y = 800
        for ln in lines:
            c.drawString(40, y, ln)
            y -= 14
        c.showPage()
    c.save()


HEAD = ["Sestava 9 – Souhrn za linku", "Linka Osoby Vozokm Stat.km Osobokm os/vkm %OBS-ST5 %ST5 Spoje Nevyhov."]


@pytest.fixture()
def synthetic(tmp_path):
    pages = [
        ["Úvod zprávy – obsah a metodika", line("150", 1, 1, 1, 1, 1, 1, 1, 1, 1)],   # lákadlo bez nadpisu
        HEAD + [
            line("101", 3998, 815.7, 57531.0, 9736.2, 11.9, 20.5, 30.1, 86, 0),
            "101 TAM 1999 400,0 28000,0 4800,0 12,0 20,0 30,0 43 0",                 # TAM – nesmí projít
            line("293", 800, 100.0, 5000.0, 1000.0, 10.0, 1.0, 2.0, 12, 1),         # speciální (konfig)
            line("301", 10, 10.0, 100.0, 50.0, 5.0, 1.0, 2.0, 5, 0),                # mimo rozsah
            "130 1234 50,0 abc 100,0 2,0 1,0 2,0 5 0",                               # poškozený řádek
        ],
        HEAD + [
            line("109", 7425, 1767.0, 123693.5, 31930.6, 18.1, 20.5, 30.1, 176, 4),
            line("252", 143, 6.4, 100.0, 143.4, 22.4, 1.0, 2.0, 1, 0),              # nízký počet spojů
            line("160", 500, 40.0, 900.0, 200.0, 9.9, 1.0, 2.0, 20, 0),              # poměr 5.0 vs 9.9 -> REVIEW
            line("109", 100, 10.0, 90.0, 20.0, 2.0, 1.0, 2.0, 10, 0),                # duplicita štítku
        ],
    ]
    p = tmp_path / "synt.pdf"
    make_pdf(p, pages)
    cfg = dict(survey_id="TEST_A", area_cs="Test", area_en="Test", year=2099, period="April",
               month_label="test", url="https://example.invalid/synt.pdf", pdf_pages=None,
               expected_n_routes=6, expected_n_routes_status="test",
               control_values={"101": (3998, 815.7, 9736.2)},
               special_routes={"293": {"original": "X113", "evaluated": 293, "status": "test"}},
               school_routes=[], notes="")
    return p, cfg


def run(p, cfg):
    return P.process_survey(cfg, p, "LOCAL_FILE")


def test_autodetect_pages_ignores_page_without_heading(synthetic):
    p, cfg = synthetic
    df, q, *_ = run(p, cfg)
    assert set(df["source_pdf_page"]) == {2, 3}


def test_tam_zpet_and_out_of_range_not_in_dataset(synthetic):
    p, cfg = synthetic
    df, q, issues, unmatched, _ = run(p, cfg)
    assert "301" not in set(df["route_label_table"])
    assert len(df) == 6                      # 101, 293, 109, 252, 160, 109(dup)
    assert any("130" in u["line"] for u in unmatched)   # poškozený řádek NEZMIZEL potichu
    assert not any("TAM" in u["line"] for u in unmatched)


def test_special_route_not_confused_with_regular(synthetic):
    p, cfg = synthetic
    df, *_ = run(p, cfg)
    r = df[df["route_label_table"] == "293"].iloc[0]
    assert r["is_special_route"] and r["route_id_original"] == "X113"
    assert pd.isna(r["route_num"]) and r["route_num_evaluated"] == 293


def test_duplicates_flagged_not_merged(synthetic):
    p, cfg = synthetic
    df, q, *_ = run(p, cfg)
    assert (df["route_label_table"] == "109").sum() == 2       # nic se nesčítá ani nevybírá
    assert any(x["check"] == "duplicate_route_labels" and x["status"] == "FAIL" for x in q)
    assert (df["extraction_status"] == "SURVEY_CHECK_FAILED").all()
    assert not df["analysis_ready"].any()


def test_low_service_kept_and_flagged_only(synthetic):
    p, cfg = synthetic
    df, *_ = run(p, cfg)
    r = df[df["route_label_table"] == "252"].iloc[0]
    assert bool(r["low_service_count_flag"]) is True
    assert len(df) == 6                       # žádný filtr na počet spojů


def test_ratio_mismatch_reported(synthetic):
    p, cfg = synthetic
    df, q, issues, *_ = run(p, cfg)
    assert any(i["issue"] == "RATIO_PDF_MISMATCH" and i["route_label_table"] == "160" for i in issues)


def test_control_value_mismatch_fails(synthetic):
    p, cfg = synthetic
    cfg = copy.deepcopy(cfg)
    cfg["control_values"] = {"101": (9999, 815.7, 9736.2)}
    _, q, *_ = run(p, cfg)
    assert any(x["check"] == "control_101" and x["status"] == "FAIL" for x in q)


def test_expected_count_mismatch_reported(synthetic):
    p, cfg = synthetic
    cfg = copy.deepcopy(cfg)
    cfg["expected_n_routes"] = 28
    _, q, *_ = run(p, cfg)
    assert any(x["check"] == "rows_city_vs_intro" and x["status"] == "MISMATCH" for x in q)


def test_no_pages_found_fails_loudly(tmp_path):
    p = tmp_path / "empty.pdf"
    make_pdf(p, [["nic zde není"]])
    cfg = dict(survey_id="T", area_cs="", area_en="", year=2099, period="", month_label="", url="u",
               pdf_pages=None, expected_n_routes=None, expected_n_routes_status="", control_values={},
               special_routes={}, school_routes=[], notes="")
    df, q, *_ = P.process_survey(cfg, p, "LOCAL_FILE")
    assert df.empty and any(x["status"] == "FAIL" for x in q)


REAL_2019 = Path(__file__).resolve().parents[1] / "praha_mestske_linky_2019_pruzkum.csv"


@pytest.mark.skipif(not REAL_2019.exists(), reason="chybí původní CSV 2019")
def test_roundtrip_real_2019_values(tmp_path):
    """Reálné hodnoty z původního CSV 2019 -> syntetické PDF -> nový extraktor -> shodné hodnoty.
    Ověřuje parsování čísel a odvozené sloupce; NE rozložení skutečného PDF."""
    ref = pd.read_csv(REAL_2019)
    lines = [line(str(r.route_num), r.output_cestujici_pruzkum, r.input_vozokm_den, r.statisticke_km,
                  r.output_osobokm_den, r.osobokm_na_vozokm_pdf, 0.0, 0.0,
                  r.input_pocet_spoju_den, r.nevyhovujici_spoje) for r in ref.itertuples()]
    pages = [HEAD + lines[i:i + 12] for i in range(0, len(lines), 12)]
    p = tmp_path / "r2019.pdf"
    make_pdf(p, pages)
    cfg = copy.deepcopy([c for c in P.SURVEYS if c["survey_id"] == "2019_East"][0])
    cfg["pdf_pages"] = None
    df, q, *_ = P.process_survey(cfg, p, "LOCAL_FILE")
    assert len(df) == 34
    m = df.merge(ref, left_on="route_num", right_on="route_num", suffixes=("", "_ref"))
    for col in ["output_cestujici_pruzkum", "input_vozokm_den", "output_osobokm_den",
                "input_pocet_spoju_den", "nevyhovujici_spoje"]:
        assert (m[col].astype(float) - m[col + "_ref"].astype(float)).abs().max() < 1e-9, col
    assert (m["podil_nevyhovujicich_spoju_pct"] - m["podil_nevyhovujicich_spoju_pct_ref"]).abs().max() < 1e-9
    assert (m["cestujici_na_spoj"] - m["cestujici_na_spoj_ref"]).abs().max() < 1e-9
    assert int(df["low_service_count_flag"].sum()) == 6          # shoda s původními 6 linkami
    assert all(x["status"] == "OK" for x in q if x["check"].startswith("control_"))
    assert not any(x["status"] in ("FAIL", "MISMATCH") for x in q)


@pytest.mark.skipif(not REAL_2019.exists(), reason="chybí původní CSV 2019")
def test_main_end_to_end_idempotent_and_unique_key(tmp_path, monkeypatch):
    """main(): všechny soubory fyzicky vzniknou, 2. běh dá identický výstup, klíč je unikátní,
    chybějící PDF ostatních průzkumů nevytvoří žádná fiktivní pozorování."""
    ref = pd.read_csv(REAL_2019)
    lines = [line(str(r.route_num), r.output_cestujici_pruzkum, r.input_vozokm_den, r.statisticke_km,
                  r.output_osobokm_den, r.osobokm_na_vozokm_pdf, 0.0, 0.0,
                  r.input_pocet_spoju_den, r.nevyhovujici_spoje) for r in ref.itertuples()]
    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    make_pdf(pdf_dir / "2019_BUS19VY-SBORNIK.pdf", [HEAD + lines[i:i + 12] for i in range(0, 34, 12)])
    data = tmp_path / "data"
    surveys = copy.deepcopy(P.SURVEYS)
    for c in surveys:
        c["pdf_pages"] = None          # syntetické PDF má jiné číslování stran než skutečné
    monkeypatch.setattr(P, "SURVEYS", surveys)
    for name, val in dict(DATA_DIR=data, MAIN_CSV=data / "main.csv", META_CSV=data / "meta.csv",
                          QUALITY_CSV=data / "q.csv", ROWCHECK_CSV=data / "rc.csv",
                          UNMATCHED_CSV=data / "um.csv", PER_SURVEY_DIR=data / "ps").items():
        monkeypatch.setattr(P, name, val)

    P.main(["--pdf-dir", str(pdf_dir), "--no-download"])
    first = (data / "main.csv").read_bytes()
    for f in ["main.csv", "meta.csv", "q.csv", "rc.csv", "um.csv"]:
        assert (data / f).exists(), f
    assert len(list((data / "ps").glob("*.csv"))) == 5
    P.main(["--pdf-dir", str(pdf_dir), "--no-download"])
    assert (data / "main.csv").read_bytes() == first                     # idempotence

    main_df = pd.read_csv(data / "main.csv")
    assert not main_df.duplicated(["survey_id", "route_id_original"]).any()
    assert set(main_df["survey_id"]) == {"2019_East"} and len(main_df) == 34
    meta = pd.read_csv(data / "meta.csv")
    assert len(meta) == 5 and (meta.set_index("survey_id").loc["2017_North", "n_observations"] == 0)


def test_configured_pages_beyond_pdf_fail_loudly(tmp_path):
    p = tmp_path / "short.pdf"
    make_pdf(p, [HEAD + [line("101", 1, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 20, 0)]])
    cfg = dict(survey_id="T", area_cs="", area_en="", year=2099, period="", month_label="", url="u",
               pdf_pages=[14, 15], expected_n_routes=None, expected_n_routes_status="",
               control_values={}, special_routes={}, school_routes=[], notes="")
    df, q, *_ = P.process_survey(cfg, p, "LOCAL_FILE")
    assert df.empty and any(x["status"] == "FAIL" and "neexistují" in x["detail"] for x in q)


# ---------------- nové testy: klasifikace, CELKEM, datum z úvodu, zaokrouhlení ----------------
def _cfg(**kw):
    base = dict(survey_id="T", special_routes={}, extra_city_routes=[], school_routes=None)
    base.update(kw)
    return base


def test_classify_city_suburban_regional_special():
    c = _cfg(extra_city_routes=["551"], special_routes={"X113": {"original": "X113", "evaluated": 293}})
    assert P.classify("102", c) == "mestska" and P.classify("299", c) == "mestska"
    assert P.classify("551", c) == "mestska"          # městská linka mimo 100-299 podle úvodu
    assert P.classify("X113", c) == "mestska" and P.classify("X999", _cfg()) == "mestska"
    assert P.classify("340", c) == "prijmestska" and P.classify("470", c) == "regionalni"
    assert P.classify("551", _cfg()) == "regionalni"   # bez zápisu v konfiguraci se nepočítá


def test_intro_dates_parsing():
    txt = ["Úvod\nuskutečnil ve dnech 10. – 12. října 2017 přepravní průzkum"]
    assert P.parse_intro_dates(txt) == ("2017-10-10", "2017-10-12")
    txt = ["ve dnech 30. března – 1. dubna 2018 průzkum"]
    assert P.parse_intro_dates(txt) == ("2018-03-30", "2018-04-01")
    assert P.parse_intro_dates(["nic"]) == (None, None)


def test_ratio_check_respects_rounding():
    ok = dict(passenger_km=231.0, vehicle_km=11.0, ratio=21.0)
    assert not P.ratio_inconsistent(ok)
    tiny = dict(passenger_km=143.4, vehicle_km=6.4, ratio=22.5)   # 22.41 vs 22.5: v mezích zaokrouhlení
    assert not P.ratio_inconsistent(tiny)
    assert P.ratio_inconsistent(dict(passenger_km=200.0, vehicle_km=40.0, ratio=9.9))


def test_celkem_reconciliation_detects_missing_row(tmp_path):
    lines = HEAD + [
        line("101", 100, 10.0, 100.0, 50.0, 5.0, 1.0, 2.0, 20, 1),
        line("102", 200, 20.0, 200.0, 100.0, 5.0, 1.0, 2.0, 30, 2),
        line("301", 50, 5.0, 50.0, 25.0, 5.0, 1.0, 2.0, 10, 0),
        "CELKEM 350 35,0 350,0 175,0 5,0 1,0 2,0 60 3",
    ]
    p = tmp_path / "c.pdf"
    make_pdf(p, [lines])
    cfg = dict(survey_id="TC", area_cs="", area_en="", year=2099, period="", month_label="", url="u",
               pdf_pages=None, expected_n_routes=2, expected_n_total=3, expected_total_persons=350,
               expected_n_routes_status="t", extra_city_routes=[], control_values={},
               special_routes={}, school_routes=None, notes="")
    df, q, *_ = P.process_survey(cfg, p, "LOCAL_FILE")
    assert len(df) == 2                               # 301 je příměstská, není v datasetu
    assert all(x["status"] == "OK" for x in q if x["check"].startswith("celkem_"))
    # chybějící řádek -> součet nesedí -> FAIL a průzkum se nepustí do analýzy
    lines2 = [l for l in lines if not l.startswith("102")]
    make_pdf(p, [lines2])
    df2, q2, *_ = P.process_survey(cfg, p, "LOCAL_FILE")
    assert any(x["check"] == "celkem_passengers" and x["status"] == "FAIL" for x in q2)
    assert (df2["extraction_status"] == "SURVEY_CHECK_FAILED").all() and not df2["analysis_ready"].any()
