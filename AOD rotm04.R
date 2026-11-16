# Instalace balíčků (spusťte jednou)
# install.packages(c("Benchmarking", "frontier", "topsis", "dplyr", "ggplot2"))

library(Benchmarking) # DEA modely
library(frontier) # SFA modely
library(topsis) # metoda TOPSIS
library(dplyr) # Práce s daty
library(ggplot2) # Vykreslování grafů

# 1. Načtení vygenerovaného datasetu
df <- read.csv("praha_autobusy_dea_dataset.csv", fileEncoding = "UTF-8")
rownames(df) <- paste0("Linka_", df$route_short_name)

# Definice matice vstupů (X) a výstupů (Y) – funkce dea() nesnese normální df
X <- as.matrix(df[, c("input_vozokm_den", "input_vozohodiny_den", "input_pocet_zastavek")])
Y <- as.matrix(df[, c("output_cestujici_pruzkum", "output_osobokm_den")])


# Analýza obalu dat (DEA - vstupově orientovaný model CCR a BCC) ----------
# Orientace na vstupy – výstupy (poptávka) jsou dány zvenčí
dea_ccr <- dea(X, Y, RTS = "crs", ORIENTATION = "in") # CCR model – konstantní výnosy z rozsahu (porovnává všechny linky bez ohledu na jejich velikost)
dea_bcc <- dea(X, Y, RTS = "vrs", ORIENTATION = "in") # BCC model – variabilní výnosy z rozsahu (porovnává malé linky s malými a velké s velkými)

df$eff_DEA_CCR <- round(eff(dea_ccr), 4)
df$eff_DEA_BCC <- round(eff(dea_bcc), 4)
df$eff_rozsah_SE <- round(df$eff_DEA_CCR / df$eff_DEA_BCC, 4) # Efektivnost z rozsahu


# Stochastická hraniční analýza (SFA - Cobb-Douglas produkční funkce) -----

df$ln_osobokm    <- log(df$output_osobokm_den)
df$ln_vozokm     <- log(df$input_vozokm_den)
df$ln_vozohodiny <- log(df$input_vozohodiny_den)
df$ln_zastavky   <- log(df$input_pocet_zastavek)

sfa_model <- sfa(ln_osobokm ~ ln_vozokm + ln_vozohodiny + ln_zastavky, data = df) # Odhad maximálně věrohodných parametrů
summary(sfa_model)
df$eff_SFA <- round(efficiencies(sfa_model), 4) # Odhad efektivnosti každé linky


# Vícekriteriální hodnocení variant (TOPSIS na poměrových ukazatelích) ----

mat_topsis <- cbind(
  df$output_cestujici_pruzkum / df$input_vozokm_den, # Kritérium: cestující na vozokm (max) – vytížení trasy
  df$output_osobokm_den / df$input_vozohodiny_den, # Kritérium: osobokm na vozohodinu (max) – produktivita zdrojů
  df$prumerna_rychlost_kmh # Kritérium: průměrná rychlost (max) – plynulost průjezdu
) # Kriteriální matice
vahy <- c(0.45, 0.40, 0.15) # Váhy kritérií
impacts <- c("+", "+", "+") # Všechna kritéria jsou maximalizační

res_topsis <- topsis(mat_topsis, vahy, impacts) # Řeší metodu TOPSIS
df$score_TOPSIS <- round(res_topsis$score, 4) # Uloží vzdálenost každé linky od ideální varianty


# Porovnání výsledků a test hypotézy (páteřní vs. obslužné linky) ---------

## Spearmanova korelace pořadí mezi všemi 3 metodami
print("Spearmanova korelační matice výsledků tří metod:")
print(cor(df[, c("eff_DEA_CCR", "eff_DEA_BCC", "eff_SFA", "score_TOPSIS")], method = "spearman")) # Vyhodnocení konzistence napříč metodami

## Kruskal-Wallisův test: Jsou páteřní linky (řada 1xy) statisticky významně efektivnější než obslužné (řada 2xy)?
print(kruskal.test(eff_DEA_CCR ~ typ_linky, data = df))

# Uložení kompletních výsledků pro tabulky do semestrální práce
write.csv(df, "praha_autobusy_vysledky_3_metody.csv", row.names = FALSE, fileEncoding = "UTF-8", dec = ",")