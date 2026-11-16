
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


# ============================================================
# SOUHRNNÁ KONTROLA VÝSLEDKŮ DEA, SFA A TOPSIS
# ============================================================

metody <- c(
  "eff_DEA_CCR",
  "eff_DEA_BCC",
  "eff_rozsah_SE",
  "eff_SFA",
  "score_TOPSIS"
)

# 1. Základní statistiky
summary_stats <- data.frame(
  metoda = metody,
  prumer = sapply(df[metody], mean, na.rm = TRUE),
  minimum = sapply(df[metody], min, na.rm = TRUE),
  maximum = sapply(df[metody], max, na.rm = TRUE),
  smerodatna_odchylka = sapply(df[metody], sd, na.rm = TRUE)
)

print(summary_stats)

# 2. Efektivní jednotky DEA
cat("\nEfektivní linky CCR:\n")
print(df$route_num[abs(df$eff_DEA_CCR - 1) < 1e-6])

cat("\nEfektivní linky BCC:\n")
print(df$route_num[abs(df$eff_DEA_BCC - 1) < 1e-6])

# 3. Kontrola měřítkové efektivnosti
df$SE_kontrola <- df$eff_DEA_CCR / df$eff_DEA_BCC

cat("\nMaximální rozdíl ve výpočtu SE:\n")
print(max(abs(df$eff_rozsah_SE - df$SE_kontrola)))

# 4. Spearmanova korelace
korelacni_matice <- cor(
  df[metody],
  method = "spearman",
  use = "complete.obs"
)

print(round(korelacni_matice, 3))

# 5. Pořadí linek podle jednotlivých metod
for (metoda in metody) {
  cat("\nPořadí podle:", metoda, "\n")
  
  vysledek <- df[
    order(df[[metoda]], decreasing = TRUE),
    c("route_num", metoda)
  ]
  
  print(vysledek, row.names = FALSE)
}

# 6. Kontrola vstupních kritérií TOPSIS
topsis_kriteria <- c(
  "osobokm_na_vozokm",
  "cestujici_na_spoj",
  "podil_nevyhovujicich_spoju_pct"
)

cat("\nKorelace kritérií TOPSIS:\n")
print(round(
  cor(df[topsis_kriteria], method = "spearman"),
  3
))


# Citlivostní analýza DEA -------------------------------------------------

# ============================================================
# CITLIVOSTNÍ ANALÝZA DEA – 28 VS. 34 LINEK
# ============================================================

# Všech 34 linek s kladnými vstupy a výstupy
df_all34 <- df_raw %>%
  filter(
    input_vozokm_den > 0,
    output_osobokm_den > 0
  )

# Funkce pro výpočet DEA CCR, BCC a měřítkové efektivnosti
calculate_dea <- function(data) {
  
  X <- as.matrix(data[, "input_vozokm_den", drop = FALSE])
  Y <- as.matrix(data[, "output_osobokm_den", drop = FALSE])
  
  rownames(X) <- paste0("Linka_", data$route_num)
  rownames(Y) <- paste0("Linka_", data$route_num)
  
  model_ccr <- Benchmarking::dea(
    X = X,
    Y = Y,
    RTS = "crs",
    ORIENTATION = "in"
  )
  
  model_bcc <- Benchmarking::dea(
    X = X,
    Y = Y,
    RTS = "vrs",
    ORIENTATION = "in"
  )
  
  ccr <- as.numeric(Benchmarking::eff(model_ccr))
  bcc <- as.numeric(Benchmarking::eff(model_bcc))
  
  data.frame(
    route_num = data$route_num,
    CCR = ccr,
    BCC = bcc,
    SE = ccr / bcc
  )
}

# Výpočet pro původních 28 linek
dea_28 <- calculate_dea(df)

# Výpočet pro všech 34 linek
dea_34 <- calculate_dea(df_all34)

# Porovnání původních 28 linek
dea_comparison <- merge(
  dea_28,
  dea_34,
  by = "route_num",
  suffixes = c("_28", "_34")
)

# Rozdíly efektivnosti
dea_comparison$rozdil_CCR <-
  dea_comparison$CCR_34 - dea_comparison$CCR_28

dea_comparison$rozdil_BCC <-
  dea_comparison$BCC_34 - dea_comparison$BCC_28

dea_comparison$rozdil_SE <-
  dea_comparison$SE_34 - dea_comparison$SE_28

# Výpis výsledků
dea_comparison <- dea_comparison[
  order(dea_comparison$route_num),
]

print(dea_comparison)

# Souhrn změn
cat("\nPrůměrná absolutní změna CCR:\n")
print(mean(abs(dea_comparison$rozdil_CCR)))

cat("\nPrůměrná absolutní změna BCC:\n")
print(mean(abs(dea_comparison$rozdil_BCC)))

cat("\nPrůměrná absolutní změna měřítkové efektivnosti:\n")
print(mean(abs(dea_comparison$rozdil_SE)))

# Korelace pořadí mezi oběma variantami DEA
cat("\nSpearmanova korelace CCR:\n")
print(cor(
  dea_comparison$CCR_28,
  dea_comparison$CCR_34,
  method = "spearman"
))

cat("\nSpearmanova korelace BCC:\n")
print(cor(
  dea_comparison$BCC_28,
  dea_comparison$BCC_34,
  method = "spearman"
))

# Efektivní linky při zahrnutí všech 34 linek
cat("\nEfektivní linky CCR – 34 linek:\n")
print(dea_34$route_num[abs(dea_34$CCR - 1) < 1e-6])

cat("\nEfektivní linky BCC – 34 linek:\n")
print(dea_34$route_num[abs(dea_34$BCC - 1) < 1e-6])

# ============================================================
# CITLIVOSTNÍ ANALÝZA TOPSIS – ZMĚNA VAH
# ============================================================

# Kritéria
criteria <- c(
  "osobokm_na_vozokm",
  "cestujici_na_spoj",
  "podil_nevyhovujicich_spoju_pct"
)

# Směr kritérií
impacts <- c("+", "+", "-")

# Funkce pro výpočet standardního TOPSIS
calculate_topsis <- function(data, criteria, weights, impacts) {
  
  M <- as.matrix(data[, criteria, drop = FALSE])
  
  # Kontrola dat
  if (any(!is.finite(M))) {
    stop("TOPSIS obsahuje chybějící nebo neplatné hodnoty.")
  }
  
  if (any(weights < 0) || sum(weights) <= 0) {
    stop("Váhy musí být nezáporné a jejich součet musí být kladný.")
  }
  
  # Normalizace vah
  weights <- weights / sum(weights)
  
  # Vektorová normalizace
  norms <- sqrt(colSums(M^2))
  
  if (any(norms == 0)) {
    stop("Některé kritérium má nulovou normu.")
  }
  
  R <- sweep(M, 2, norms, "/")
  
  # Vážená normalizovaná matice
  V <- sweep(R, 2, weights, "*")
  
  # Ideální a bazální řešení
  ideal_best <- sapply(seq_along(impacts), function(j) {
    if (impacts[j] == "+") {
      max(V[, j])
    } else {
      min(V[, j])
    }
  })
  
  ideal_worst <- sapply(seq_along(impacts), function(j) {
    if (impacts[j] == "+") {
      min(V[, j])
    } else {
      max(V[, j])
    }
  })
  
  # Vzdálenosti od ideálního a bazálního řešení
  d_best <- sqrt(rowSums(
    sweep(V, 2, ideal_best, "-")^2
  ))
  
  d_worst <- sqrt(rowSums(
    sweep(V, 2, ideal_worst, "-")^2
  ))
  
  # Výsledné TOPSIS skóre
  score <- d_worst / (d_best + d_worst)
  
  data.frame(
    route_num = data$route_num,
    score = score
  )
}

# Definice variant vah
weight_scenarios <- list(
  rovnomerne = c(1/3, 1/3, 1/3),
  osobokm = c(0.50, 0.25, 0.25),
  cestujici = c(0.25, 0.50, 0.25),
  kvalita = c(0.25, 0.25, 0.50),
  bez_osobokm = c(NA, 0.50, 0.50),
  bez_cestujicich = c(0.50, NA, 0.50)
)

# Výpočet všech variant
topsis_results <- lapply(
  names(weight_scenarios),
  function(scenario) {
    
    weights <- weight_scenarios[[scenario]]
    active <- !is.na(weights)
    
    result <- calculate_topsis(
      data = df,
      criteria = criteria[active],
      weights = weights[active],
      impacts = impacts[active]
    )
    
    names(result)[2] <- scenario
    result
  }
)

# Spojení výsledků do jedné tabulky
topsis_comparison <- Reduce(
  function(x, y) merge(x, y, by = "route_num"),
  topsis_results
)

# Seřazení podle původní rovnoměrné varianty
topsis_comparison <- topsis_comparison[
  order(-topsis_comparison$rovnomerne),
]

print(topsis_comparison)