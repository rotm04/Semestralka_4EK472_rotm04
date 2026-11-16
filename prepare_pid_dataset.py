from bs4 import BeautifulSoup # Funkce pro analýzu URL adres
import io # Práce v RAM paměti bez nutnosti ukládání dočasných dat na HDD
import numpy as np # Práce s číselnými daty
import pandas as pd # Práce s datasety
import pdfplumber # Extrakce textu z PDF souborů
import re # Práce s regulárními výrazy v textu
import requests # Stahování dat z internetu
from urllib.parse import urljoin # Funkce pro spojování kusů URL adres
import zipfile # Práce se zazipovanými soubory

REFERENCE_DATE = "2026-11-11" # Referenční pracovní den pro analýzu

GTFS_URL = "https://data.pid.cz/PID_GTFS.zip"
SURVEYS_URL = "https://pid.cz/o-systemu/dopravni-pruzkumy/"
OUTPUT_CSV = "praha_autobusy_dea_dataset.csv"


def time_to_seconds(t_str): # Sjednocení časových jednotek
    """Převede GTFS čas (včetně hodnot > 24:00:00 pro noční přechody) na sekundy."""
    if pd.isna(t_str):
        return np.nan
    parts = str(t_str).strip().split(":")
    if len(parts) != 3:
        return np.nan
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])

def get_active_services(calendar, calendar_dates, reference_date):
    """
    Vrátí service_id, které jsou aktivní v konkrétní datum.
    Zohledňuje běžný kalendář i výjimky calendar_dates.txt.
    """

    date = pd.Timestamp(reference_date)
    date_str = date.strftime("%Y%m%d")

    weekday_column = date.day_name().lower()

    active_services = set()

    # 1. Běžná pravidla z calendar.txt
    if not calendar.empty:

        start_dates = pd.to_datetime(
            calendar["start_date"].astype(str),
            format="%Y%m%d"
        )

        end_dates = pd.to_datetime(
            calendar["end_date"].astype(str),
            format="%Y%m%d"
        )

        mask = (
            (calendar[weekday_column] == 1)
            & (start_dates <= date)
            & (end_dates >= date)
        )

        active_services = set(
            calendar.loc[mask, "service_id"].astype(str)
        )

    # 2. Výjimky z calendar_dates.txt
    if not calendar_dates.empty:

        exceptions = calendar_dates[
            calendar_dates["date"].astype(str).str.zfill(8)
            == date_str
        ]

        for _, row in exceptions.iterrows():

            service_id = str(row["service_id"])
            exception_type = int(row["exception_type"])

            if exception_type == 1:
                # Služba je pro daný den mimořádně přidána.
                active_services.add(service_id)

            elif exception_type == 2:
                # Služba je pro daný den mimořádně odebrána.
                active_services.discard(service_id)

    return active_services

def extract_gtfs_inputs(): # Stáhne data o JŘ s pojistkou proti čekání při nefunkčnosti volaných serverů
    print("[1/3] Stahuji GTFS jízdní řády ze serveru data.pid.cz (cca 50-80 MB)...")
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    r = requests.get(GTFS_URL, headers=headers, timeout=120)
    r.raise_for_status()
    """Ze stažených dat načte údaje o existujících linkách, provozu v jednotlivých dnech, vypravovaných spojích a časech na zastávkách"""
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:

    print("      Načítám tabulky GTFS...")

    routes = pd.read_csv(
        z.open("routes.txt"),
        low_memory=False
    )

    calendar = pd.read_csv(
        z.open("calendar.txt"),
        low_memory=False,
        dtype={"service_id": str}
    )

    trips = pd.read_csv(
        z.open("trips.txt"),
        low_memory=False,
        dtype={"service_id": str}
    )

    stop_times = pd.read_csv(
        z.open("stop_times.txt"),
        usecols=[
            "trip_id",
            "arrival_time",
            "departure_time",
            "stop_id",
            "stop_sequence",
            "shape_dist_traveled",
        ],
        low_memory=False,
    )

    # Výjimky z běžného kalendáře – jsou-li v GTFS k dispozici.
    if "calendar_dates.txt" in z.namelist():
        calendar_dates = pd.read_csv(
            z.open("calendar_dates.txt"),
            low_memory=False,
            dtype={"service_id": str}
        )
    else:
        calendar_dates = pd.DataFrame(
            columns=["service_id", "date", "exception_type"]
        )

    # 1. Filtr na denní městské autobusové linky v Praze (route_type == 3, čísla 100 až 250)
    routes["route_num"] = pd.to_numeric(routes["route_short_name"], errors="coerce") # Nečíselné linky jsou vyřazeny
    bus_routes = routes[
        (routes["route_type"] == 3) # Kontroluji pouze autobusy
        & (routes["route_num"] >= 100) # Městské autobusy mají čísla od 100...
        & (routes["route_num"] <= 299) # ... do 299
    ].copy()

    # 2. Výběr spojů aktivních v konkrétní referenční den
    active_services = get_active_services(
        calendar,
        calendar_dates,
        REFERENCE_DATE
    )
    
    print(
        f"      Referenční datum: {REFERENCE_DATE}"
    )
    
    print(
        f"      Počet aktivních service_id: {len(active_services)}"
    )
    
    # Výběr všech spojů, které v daný den skutečně platí.
    bus_trips = trips[
        trips["route_id"].isin(bus_routes["route_id"])
        & trips["service_id"].astype(str).isin(active_services)
    ].copy()
    
    print(
        f"      Počet vybraných spojů: {len(bus_trips)}"
    )
    
    if bus_trips.empty:
        raise ValueError(
            "Pro zvolené datum nebyly nalezeny žádné spoje. "
            "Zkontrolujte datum a platnost GTFS kalendáře."
        )

    """ Počítá dobu jízdy každého spoje a výsledky vrací v hodinách """
    print("      Počítám ujeté vozokilometry, vozohodiny a zastávky pro jednotlivé spoje...")
    st = stop_times[stop_times["trip_id"].isin(bus_trips["trip_id"])].copy()
    st["arr_sec"] = st["arrival_time"].apply(time_to_seconds)
    st["dep_sec"] = st["departure_time"].apply(time_to_seconds)

    # Agregace na úroveň jednotlivého spoje (trip_id)
    trip_stats = st.groupby("trip_id").agg(
        start_sec=("dep_sec", "min"),
        end_sec=("arr_sec", "max"),
        dist_km=("shape_dist_traveled", "max"),
        stops_in_trip=("stop_id", "nunique"),
    ).reset_index()
    
    # Kontrola chybějících a nulových vzdáleností

    print(
        "Podíl spojů bez použitelné vzdálenosti:",
        round(
            (trip_stats["dist_km"].isna() |
             (trip_stats["dist_km"] <= 0)).mean() * 100,
            2
        ),
        "%"
    )
    
    print(
        "Počet spojů s neplatnou dobou jízdy:",
        (
            trip_stats["duration_hours"].isna()
            | (trip_stats["duration_hours"] <= 0)
        ).sum()
    )

    trip_stats["duration_hours"] = (trip_stats["end_sec"] - trip_stats["start_sec"]) / 3600.0

    # Chybí-li u některého spoje informace o délce trasy, odhadne ji z průměrné rychlosti autobusů 23 km/h
    missing_dist = trip_stats["dist_km"].isna() | (trip_stats["dist_km"] <= 0)
    trip_stats.loc[missing_dist, "dist_km"] = trip_stats.loc[missing_dist, "duration_hours"] * 23.0

    # Převod případných metrů na kilometry (v PID GTFS je shape_dist_traveled v km)
    if trip_stats["dist_km"].median() > 500: # Pojistka pro metry – některá data mohou být v m, jiná v km
        trip_stats["dist_km"] = trip_stats["dist_km"] / 1000.0

    merged_trips = bus_trips.merge(trip_stats, on="trip_id", how="inner")

    # Počet unikátních zastávek na celé lince
    st_with_route = st.merge(bus_trips[["trip_id", "route_id"]], on="trip_id")
    route_stops = (
        st_with_route.groupby("route_id")["stop_id"]
        .nunique()
        .reset_index()
        .rename(columns={"stop_id": "input_pocet_zastavek"})
    )

    # Agregace na úroveň linky (DMU)
    route_agg = (
        merged_trips.groupby("route_id")
        .agg(
            input_pocet_spoju_den=("trip_id", "count"), # Kolik spojů denně linku obslouží
            input_vozokm_den=("dist_km", "sum"), # Kolik km všechny vozy na lince denně najedou (jak moc linka opotřebovává autobusy)
            input_vozohodiny_den=("duration_hours", "sum"), # Kolik h všechny vozy na lince denně najedou (jak moc linka zaměstnává řidiče)
            prumerna_delka_spoje_km=("dist_km", "mean"), # Průměrná délka spojů na linku vypravovaných
        )
        .reset_index()
    )

    df_inputs = (
        bus_routes[["route_id", "route_short_name", "route_num", "route_long_name"]]
        .merge(route_agg, on="route_id", how="inner")
        .merge(route_stops, on="route_id", how="left")
    )

    # Odhad potřebného počtu vypravených vozů ve špičce (vozohodiny / 14 h provozu + 15% záloha)
    df_inputs["input_odhad_vozu"] = np.ceil(df_inputs["input_vozohodiny_den"] / 14.0 * 1.15)
    df_inputs["prumerna_rychlost_kmh"] = (
        df_inputs["input_vozokm_den"] / df_inputs["input_vozohodiny_den"]
    ) # Průměrná rychlost linky

    return df_inputs.sort_values("route_num").reset_index(drop=True)


def scrape_pid_pdf_surveys():

    print(
        "[2/3] PDF průzkumy zatím nejsou automaticky zpracovávány."
    )

    return pd.DataFrame(
        columns=[
            "route_num",
            "output_cestujici_pruzkum",
            "output_max_usek_cestujici",
            "zdroj_vystupu",
        ]
    )


def build_final_dataset():
    df = extract_gtfs_inputs()
    df_pdf = scrape_pid_pdf_surveys()

    print("[3/3] Spojuji vstupy (GTFS) a výstupy do finálního datasetu...")
    if not df_pdf.empty: # Spojí pro každou linku data z předpisů, měření a průzkumů
        df = df.merge(df_pdf, on="route_num", how="left")
    else:
        df["output_cestujici_pruzkum"] = np.nan
        df["output_max_usek_cestujici"] = np.nan
        df["zdroj_vystupu"] = None

    # Neověřené hodnoty poptávky ponecháme jako chybějící. Nebudeme je nahrazovat náhodně generovanými odhady.
    
    df["zdroj_vystupu"] = df["zdroj_vystupu"].fillna(
        "NEDOSTUPNY_UDAJ"
    )

    # Druhý klíčový výstup: Přepravní výkon v osobokilometrech (oskm) za pracovní den (průměrná délka cesty cestujícího v MHD Praha činí cca 42 % délky linky, min. 2.2 km)
    # avg_trip_len = np.clip(df["prumerna_delka_spoje_km"] * 0.42, 2.2, 8.5)
    # df["output_osobokm_den"] = np.round(df["output_cestujici_pruzkum"] * avg_trip_len, 1)

    # Zaokrouhlení vstupů pro přehlednost
    df["input_vozokm_den"] = df["input_vozokm_den"].round(1)
    df["input_vozohodiny_den"] = df["input_vozohodiny_den"].round(2)
    df["prumerna_delka_spoje_km"] = df["prumerna_delka_spoje_km"].round(2)
    df["prumerna_rychlost_kmh"] = df["prumerna_rychlost_kmh"].round(2)

    # Kategorizace pro testování hypotéz v R (páteřní vs. obslužná linka)
    df["typ_linky"] = np.where(df["route_num"] < 200, "Paterni_100", "Obsluzna_200")

    # Vyřazení extrémních okrajových záznamů (např. linky s méně než 10 spoji za den - školní/speciální)
    df = df[df["input_pocet_spoju_den"] >= 10].reset_index(drop=True)

    # Uložení do CSV
    df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\nHOTOVO! Dataset byl uložen do souboru: {OUTPUT_CSV}")
    print(f"Celkový počet produkčních jednotek (autobusových linek DMU): {len(df)}")
    print("\nUkázka prvních 5 řádků:")
    print(
        df[
            [
                "route_short_name",
                "typ_linky",
                "input_vozokm_den",
                "input_vozohodiny_den",
                "input_pocet_zastavek",
                "output_cestujici_pruzkum",
                "output_osobokm_den",
            ]
        ].head()
    )


if __name__ == "__main__":
    build_final_dataset()
