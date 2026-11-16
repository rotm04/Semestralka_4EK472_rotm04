
# ============================================================
# ANALÝZA EFEKTIVITY MĚSTSKÝCH AUTOBUSOVÝCH LINEK PID
# Data: průzkum PID BUS19VY, rok 2019, Sestava 9
# ============================================================

# Instalace balíčků – pouze při prvním spuštění:
# install.packages(c("Benchmarking", "frontier",
#                    "topsis", "dplyr", "ggplot2"))

library(Benchmarking)
library(frontier)
library(topsis)
library(dplyr)
library(ggplot2)

# ------------------------------------------------------------
# 1. NAČTENÍ A KONTROLA DAT
# ------------------------------------------------------------

# Načtení kompletního datasetu
df_raw <- read.csv(
  "praha_mestske_linky_2019_pruzkum.csv",
  stringsAsFactors = FALSE,
  check.names = FALSE
)

# Převod textového sloupce na logické hodnoty
df_raw$v_hlavnim_modelu <- tolower(
  trimws(df_raw$v_hlavnim_modelu)
) == "true"

# Výběr linek pro hlavní model
df <- df_raw %>%
  filter(
    v_hlavnim_modelu,
    input_vozokm_den > 0,
    output_osobokm_den > 0,
    input_pocet_spoju_den >= 10
  )

# Kontrola počtu vybraných linek
cat("Počet linek v původním datasetu:", nrow(df_raw), "\n")
cat("Počet linek v hlavním modelu:", nrow(df), "\n")

# Přiřazení názvů řádků
rownames(df) <- paste0("Linka_", df$route_num)

# Kontrola výsledku
print(df[, c(
  "route_num",
  "input_vozokm_den",
  "output_osobokm_den",
  "input_pocet_spoju_den"
)])

# Základní statistická charakteristika
summary(df[, c(
  "input_vozokm_den",
  "output_osobokm_den",
  "output_cestujici_pruzkum",
  "input_pocet_spoju_den"
)])

# Korelace vstupu a výstupů
cor(
  df[, c(
    "input_vozokm_den",
    "output_osobokm_den",
    "output_cestujici_pruzkum"
  )],
  method = "spearman"
)

# Kontrola chybějících hodnot
colSums(is.na(df))


# ------------------------------------------------------------
# 2. DEA – HLAVNÍ MODEL
# ------------------------------------------------------------

# Vstup: vozové kilometry
# Výstup: osobokilometry

X <- as.matrix(df[, "input_vozokm_den", drop = FALSE])
Y <- as.matrix(df[, "output_osobokm_den", drop = FALSE])

# CCR – konstantní výnosy z rozsahu.
dea_ccr <- dea(
  X,
  Y,
  RTS = "crs",
  ORIENTATION = "in"
)

# BCC – variabilní výnosy z rozsahu.
dea_bcc <- dea(
  X,
  Y,
  RTS = "vrs",
  ORIENTATION = "in"
)

# Efektivnosti nejprve ponecháme nezaokrouhlené.
df$eff_DEA_CCR <- as.numeric(eff(dea_ccr))
df$eff_DEA_BCC <- as.numeric(eff(dea_bcc))

# Efektivnost z rozsahu = CCR / BCC.
df$eff_rozsah_SE <- (
  df$eff_DEA_CCR / df$eff_DEA_BCC
)

# Zaokrouhlení až pro prezentaci výsledků.
df <- df %>%
  mutate(
    eff_DEA_CCR = round(eff_DEA_CCR, 4),
    eff_DEA_BCC = round(eff_DEA_BCC, 4),
    eff_rozsah_SE = round(eff_rozsah_SE, 4)
  )

cat("\nVýsledky DEA:\n")

print(
  df %>%
    select(
      route_num,
      eff_DEA_CCR,
      eff_DEA_BCC,
      eff_rozsah_SE
    )
)


# ------------------------------------------------------------
# 3. DEA – CITLIVOSTNÍ ANALÝZA
# ------------------------------------------------------------

# Ověříme, zda se výsledky významně změní,
# pokud jako výstup použijeme počet přepravených osob
# namísto osobokilometrů.

Y_passengers <- as.matrix(
  df[, "output_cestujici_pruzkum", drop = FALSE]
)

dea_ccr_passengers <- dea(
  X,
  Y_passengers,
  RTS = "crs",
  ORIENTATION = "in"
)

dea_bcc_passengers <- dea(
  X,
  Y_passengers,
  RTS = "vrs",
  ORIENTATION = "in"
)

df$eff_DEA_CCR_pocet_osob <- as.numeric(
  eff(dea_ccr_passengers)
)

df$eff_DEA_BCC_pocet_osob <- as.numeric(
  eff(dea_bcc_passengers)
)

df$eff_DEA_CCR_pocet_osob <- round(
  df$eff_DEA_CCR_pocet_osob, 4
)

df$eff_DEA_BCC_pocet_osob <- round(
  df$eff_DEA_BCC_pocet_osob, 4
)


# ------------------------------------------------------------
# 4. SFA – STOCHASTICKÁ HRANIČNÍ ANALÝZA
# ------------------------------------------------------------

# Cobb-Douglasova produkční funkce s jedním vstupem:
#
# ln(osobokm) = beta0 + beta1 * ln(vozokm) + v - u
#
# v = náhodná složka
# u = technická neefektivnost

df$ln_osobokm <- log(df$output_osobokm_den)
df$ln_vozokm <- log(df$input_vozokm_den)

sfa_model <- sfa(
  ln_osobokm ~ ln_vozokm,
  data = df
)

cat("\nVýsledky SFA:\n")
print(summary(sfa_model))

df$eff_SFA <- as.numeric(
  efficiencies(sfa_model)
)

df$eff_SFA <- round(df$eff_SFA, 4)


# ------------------------------------------------------------
# 5. TOPSIS – DOPLŇKOVÉ HODNOCENÍ
# ------------------------------------------------------------

# Kritérium 1: osobokm na vozový km – maximalizace
# Kritérium 2: cestující na spoj – maximalizace
# Kritérium 3: podíl nevyhovujících spojů – minimalizace

mat_topsis <- cbind(
  osobokm_na_vozokm =
    df$output_osobokm_den / df$input_vozokm_den,
  
  cestujici_na_spoj =
    df$output_cestujici_pruzkum /
    df$input_pocet_spoju_den,
  
  podil_nevyhovujicich =
    df$podil_nevyhovujicich_spoju_pct
)

# Rovnoměrné váhy – výchozí varianta.
vahy <- c(1/3, 1/3, 1/3)

# První dvě kritéria maximalizujeme,
# třetí kritérium minimalizujeme.
impacts <- c("+", "+", "-")

res_topsis <- topsis(
  mat_topsis,
  vahy,
  impacts
)

df$score_TOPSIS <- round(
  res_topsis$score,
  4
)


# ------------------------------------------------------------
# 6. POROVNÁNÍ METOD
# ------------------------------------------------------------

# Spearmanova korelace pořadí.
cor_matrix <- cor(
  df[
    ,
    c(
      "eff_DEA_CCR",
      "eff_DEA_BCC",
      "eff_SFA",
      "score_TOPSIS"
    )
  ],
  method = "spearman",
  use = "complete.obs"
)

cat("\nSpearmanova korelační matice:\n")
print(cor_matrix)


# ------------------------------------------------------------
# 7. POROVNÁNÍ ŘAD 100 A 200
# ------------------------------------------------------------

# Pozor: jde o porovnání číselných řad,
# nikoliv automaticky páteřních a obslužných linek.

cat("\nKruskal-Wallisův test – CCR podle číselné řady:\n")

print(
  kruskal.test(
    eff_DEA_CCR ~ linkova_rada,
    data = df
  )
)


# ------------------------------------------------------------
# 8. GRAFY
# ------------------------------------------------------------

ggplot(
  df,
  aes(
    x = reorder(as.factor(route_num), eff_DEA_CCR),
    y = eff_DEA_CCR
  )
) +
  geom_col() +
  coord_flip() +
  labs(
    title = "Technická efektivnost autobusových linek – DEA CCR",
    x = "Autobusová linka",
    y = "Efektivnost CCR"
  ) +
  theme_minimal()


# ------------------------------------------------------------
# 9. EXPORT VÝSLEDKŮ
# ------------------------------------------------------------

write.csv2(
  df,
  "praha_mestske_linky_vysledky_3_metody.csv",
  row.names = FALSE,
  fileEncoding = "UTF-8"
)

cat(
  "\nHotovo. Výsledky byly uloženy do souboru:",
  "praha_mestske_linky_vysledky_3_metody.csv\n"
)
