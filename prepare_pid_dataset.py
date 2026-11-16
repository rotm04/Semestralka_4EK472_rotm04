
import re
import io
import requests
import pdfplumber
import pandas as pd

# ------------------------------------------------------------
# NASTAVENÍ
# ------------------------------------------------------------

PDF_URL = "https://data.pid.cz/pruzkumy/2019_BUS19VY-SBORNIK.pdf"
OUTPUT_CSV = "praha_mestske_linky_2019_pruzkum.csv"

# Fyzické stránky PDF (číslování od 1).
# Sestava 9 pro městské linky je na stranách 14–16.
PDF_PAGES = [14, 15, 16]

MIN_ROUTE = 100
MAX_ROUTE = 299

# Linky s menším počtem spojů ponecháme v CSV,
# ale nebudou zařazeny do hlavního modelu.
MIN_TRIPS_MAIN_MODEL = 10


# ------------------------------------------------------------
# POMOCNÉ FUNKCE
# ------------------------------------------------------------

def czech_number(value):
    """Převede desetinné číslo s desetinnou čárkou na float."""
    return float(value.replace(",", "."))


def download_pdf(url):
    """Stáhne PDF a vrátí obsah v paměti."""
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=180
    )
    response.raise_for_status()
    return io.BytesIO(response.content)


# Řádek souhrnu linky:
# linka, přepravené osoby, vozové km, statistické km,
# osobokm, osobokm/vozokm, %OBS-ST5, %ST5,
# počet spojů, nevyhovující spoje.
#
# Řádky TAM a ZPĚT se tímto vzorem nezachytí.
ROW_PATTERN = re.compile(
    r"^\s*"
    r"(?P<route>\d{3})\s+"
    r"(?P<passengers>\d+)\s+"
    r"(?P<vehicle_km>[\d,]+)\s+"
    r"(?P<statistical_km>[\d,]+)\s+"
    r"(?P<passenger_km>[\d,]+)\s+"
    r"(?P<passenger_km_per_vehicle_km>[\d,]+)\s+"
    r"(?P<obs_st5>[\d,]+)\s+"
    r"(?P<st5>[\d,]+)\s+"
    r"(?P<trips>\d+)\s+"
    r"(?P<unsuitable_trips>\d+)\s*$"
)


# ------------------------------------------------------------
# EXTRAKCE SESTAVY 9
# ------------------------------------------------------------

def extract_survey_data(pdf_file):
    records = []

    with pdfplumber.open(pdf_file) as pdf:

        for page_number in PDF_PAGES:

            page = pdf.pages[page_number - 1]
            page_text = page.extract_text()

            if not page_text:
                print(f"VAROVÁNÍ: Strana {page_number} neobsahuje text.")
                continue

            for line in page_text.splitlines():

                match = ROW_PATTERN.match(line)

                if not match:
                    continue

                row = match.groupdict()

                route_num = int(row["route"])

                # Pouze městské linky 100–299.
                if not MIN_ROUTE <= route_num <= MAX_ROUTE:
                    continue

                records.append({
                    "route_num": route_num,
                    "output_cestujici_pruzkum": int(
                        row["passengers"]
                    ),
                    "input_vozokm_den": czech_number(
                        row["vehicle_km"]
                    ),
                    "statisticke_km": czech_number(
                        row["statistical_km"]
                    ),
                    "output_osobokm_den": czech_number(
                        row["passenger_km"]
                    ),
                    "osobokm_na_vozokm_pdf": czech_number(
                        row["passenger_km_per_vehicle_km"]
                    ),
                    "input_pocet_spoju_den": int(
                        row["trips"]
                    ),
                    "nevyhovujici_spoje": int(
                        row["unsuitable_trips"]
                    ),
                    "strana_pdf": page_number
                })

    df = pd.DataFrame(records)

    if df.empty:
        raise ValueError(
            "Nepodařilo se načíst žádné řádky. "
            "Zkontrolujte PDF nebo formát extrahovaného textu."
        )

    # Jedna souhrnná hodnota na linku.
    if df["route_num"].duplicated().any():
        duplicates = df.loc[
            df["route_num"].duplicated(keep=False),
            "route_num"
        ].tolist()

        raise ValueError(
            f"V datech jsou duplicitní linky: {duplicates}"
        )

    # Kontrola klíčových hodnot proti PDF.
    # Pokud se změní PDF nebo způsob extrakce, skript se zastaví.
    expected = {
        101: (3998, 815.7, 9736.2),
        109: (7425, 1767.0, 31930.6),
        207: (13946, 1407.3, 26117.6),
        223: (8541, 1855.3, 29879.5),
        240: (5233, 1933.2, 33877.7)
    }

    for route, values in expected.items():

        row = df.loc[df["route_num"] == route]

        if row.empty:
            raise ValueError(
                f"Kontrolní linka {route} nebyla nalezena."
            )

        actual = (
            int(row.iloc[0]["output_cestujici_pruzkum"]),
            round(row.iloc[0]["input_vozokm_den"], 1),
            round(row.iloc[0]["output_osobokm_den"], 1)
        )

        if actual != values:
            raise ValueError(
                f"Nesouhlasí kontrolní hodnoty linky {route}. "
                f"Očekáváno {values}, načteno {actual}."
            )

    return df


# ------------------------------------------------------------
# VYTVOŘENÍ FINÁLNÍHO DATASETU
# ------------------------------------------------------------

def build_dataset():

    print("Stahuji PDF průzkumu PID 2019...")
    pdf_file = download_pdf(PDF_URL)

    print("Načítám Sestavu 9 ze stran 14–16...")
    df = extract_survey_data(pdf_file)

    # Kategorizace podle číselné řady.
    # Nejde automaticky o klasifikaci páteřní/obslužná.
    df["linkova_rada"] = df["route_num"].apply(
        lambda x: "100" if x < 200 else "200"
    )

    # Podíl nevyhovujících spojů v procentech.
    df["podil_nevyhovujicich_spoju_pct"] = (
        100 * df["nevyhovujici_spoje"]
        / df["input_pocet_spoju_den"]
    )

    # Produktivita – pouze doplňkový poměrový ukazatel.
    df["osobokm_na_vozokm"] = (
        df["output_osobokm_den"]
        / df["input_vozokm_den"]
    )

    df["cestujici_na_spoj"] = (
        df["output_cestujici_pruzkum"]
        / df["input_pocet_spoju_den"]
    )

    # Základní podmínky pro DEA.
    df["v_hlavnim_modelu"] = (
        (df["input_pocet_spoju_den"] >= MIN_TRIPS_MAIN_MODEL)
        & (df["input_vozokm_den"] > 0)
        & (df["output_osobokm_den"] > 0)
    )

    # Metadata o zdroji a období.
    df["zdroj_dat"] = "PID_BUS19VY_Sestava_9"
    df["rok_pruzkumu"] = 2019
    df["obdobi_pruzkumu"] = "6:00–20:00"
    df["den_pruzkumu"] = "streda"

    df = df.sort_values("route_num").reset_index(drop=True)

    # Uložení všech nalezených linek.
    df.to_csv(
        OUTPUT_CSV,
        index=False,
        encoding="utf-8-sig"
    )

    # --------------------------------------------------------
    # KONTROLNÍ VÝPIS
    # --------------------------------------------------------

    print("\nDATASET ÚSPĚŠNĚ VYTVOŘEN")
    print(f"Počet nalezených linek 100–299: {len(df)}")
    print(
        "Počet linek v hlavním modelu:",
        int(df["v_hlavnim_modelu"].sum())
    )
    print(
        "Počet linek mimo hlavní model:",
        int((~df["v_hlavnim_modelu"]).sum())
    )

    print("\nNačtené linky:")
    print(df["route_num"].tolist())

    print("\nKontrola hlavních proměnných:")
    print(
        df[
            [
                "route_num",
                "output_cestujici_pruzkum",
                "input_vozokm_den",
                "output_osobokm_den",
                "input_pocet_spoju_den",
                "nevyhovujici_spoje",
                "v_hlavnim_modelu"
            ]
        ].to_string(index=False)
    )

    print(f"\nSoubor uložen: {OUTPUT_CSV}")


if __name__ == "__main__":
    build_dataset()
