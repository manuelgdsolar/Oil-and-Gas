"""
Argentina Well Intelligence Dashboard
=====================================
Live connection to Argentina's Secretaría de Energía CKAN API.
Uses SQL aggregation endpoint for fast company/basin rollups and
filter-scoped queries for per-well detail.
"""

import json
import urllib3
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# Silence the "InsecureRequestWarning" we emit because the server's cert chain
# is not trusted by the bundled Anaconda Python on some macOS installs.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Page config & CSS
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Argentina Well Intelligence Dashboard",
    page_icon="🛢️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    [data-testid="stSidebar"] {
        background-color: #1e1e1e;
    }
    [data-testid="stSidebar"] * {
        color: #eaeaea !important;
    }
    [data-testid="stSidebar"] .stSelectbox label,
    [data-testid="stSidebar"] .stCheckbox label {
        color: #eaeaea !important;
    }
    .basin-card {
        background: #ffffff;
        border-radius: 10px;
        padding: 1rem 1.2rem;
        margin-bottom: 0.6rem;
        border-left: 4px solid #2563eb;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }
    .basin-card h4 { margin: 0 0 .3rem 0; color:#0f172a; }
    .basin-card .meta { color:#475569; font-size:.9rem; }
    .pill-green  { background:#16a34a; color:#fff; padding:2px 10px; border-radius:4px; font-size:.8rem; font-weight:600;}
    .pill-orange { background:#ea580c; color:#fff; padding:2px 10px; border-radius:4px; font-size:.8rem; font-weight:600;}
    .pill-red    { background:#dc2626; color:#fff; padding:2px 10px; border-radius:4px; font-size:.8rem; font-weight:600;}
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
    .muted { color:#64748b; font-size:.9rem; }
    @media (max-width: 768px) { .block-container { padding: .5rem; } }
</style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Constants — API resources
# ---------------------------------------------------------------------------
API_BASE = "https://datos.energia.gob.ar/api/3/action"
API_SEARCH = f"{API_BASE}/datastore_search"
API_SQL = f"{API_BASE}/datastore_search_sql"

# "Producción de Pozos de Gas y Petróleo" — yearly resources (all well types).
# Source: https://datos.energia.gob.ar/dataset/produccin-de-pozos-de-gas-y-petrleo-por-pozo
RESOURCES_BY_YEAR = {
    2026: "fb7a47a0-cba9-4667-a004-6f6c1c346c23",
    2025: "d774b5d7-0756-48fe-88f2-8729b57b22da",
    2024: "43a09dce-1742-44d0-bc13-f193deaab563",
    2023: "231c39b3-e81e-4398-af8d-b115807f2c25",
    2022: "876b3746-85e2-4039-adeb-b1354436159f",
    2021: "465be754-a372-4c31-b855-81dc5fe3309f",
    2020: "c4a4a6a0-e75a-4e12-ae5c-54d53a70348c",
    2019: "8bc0d61c-0408-43d4-a7bc-7178fcb5d37e",
    2018: "333fd72a-9b83-4bc1-bc94-0f5940b52331",
}
# "Capítulo IV — Pozos" (padrón de pozos — well metadata, drilling info, coords)
PADRON_RESOURCE = "cb5c0f04-7835-45cd-b982-3e25ca7d7751"

HTTP_TIMEOUT = 90
VERIFY_SSL = False  # Anaconda's bundled certs fail against this gov CA chain.

# Mock ownership — well-known joint ventures (public info). Used in detail screen.
OWNERSHIP_MOCK = {
    "LOMA CAMPANA":       {"YPF S.A.": 50.0, "CHEVRON ARGENTINA": 50.0},
    "CERRO DRAGON":       {"PAN AMERICAN ENERGY": 60.0, "BP ARGENTINA": 40.0},
    "MANANTIALES BEHR":   {"SINOPEC ARGENTINA": 70.0, "YPF S.A.": 30.0},
    "AGUADA PICHANA":     {"TOTAL AUSTRAL": 27.27, "WINTERSHALL DEA": 27.27, "PAN AMERICAN ENERGY": 45.46},
    "EL OREJANO":         {"TECPETROL S.A.": 100.0},
    "RINCON DEL MANGRULLO": {"YPF S.A.": 50.0, "PAMPA ENERGIA": 50.0},
}


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def sql_query(sql: str, timeout: int = HTTP_TIMEOUT) -> pd.DataFrame | None:
    """Run a SQL query against the CKAN datastore and return a DataFrame.

    Returns None on network/parse failure. Caller decides how to handle.
    """
    try:
        r = requests.get(API_SQL, params={"sql": sql}, timeout=timeout, verify=VERIFY_SSL)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            err = data.get("error", {})
            st.session_state["_last_api_error"] = str(err)[:400]
            return None
        records = data["result"]["records"]
        return pd.DataFrame(records)
    except requests.exceptions.RequestException as e:
        st.session_state["_last_api_error"] = f"Network: {e}"
        return None
    except ValueError as e:
        st.session_state["_last_api_error"] = f"Parse: {e}"
        return None


def sql_escape(value: str) -> str:
    """SQL-escape a single-quoted literal."""
    return str(value).replace("'", "''")


def coerce_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ---------------------------------------------------------------------------
# Cached data accessors
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def get_latest_data_date(year: int) -> tuple[int, int] | None:
    """Última fecha (anio, mes) con registros en el resource del año dado."""
    rid = RESOURCES_BY_YEAR.get(year)
    if rid is None:
        return None
    sql = f'SELECT MAX("anio"*100+"mes") AS ym FROM "{rid}"'
    df = sql_query(sql, timeout=15)
    if df is None or df.empty:
        return None
    val = df.iloc[0].get("ym")
    try:
        ym = int(float(val))
    except (TypeError, ValueError):
        return None
    if ym <= 0:
        return None
    return (ym // 100, ym % 100)


@st.cache_data(ttl=3600, show_spinner=False)
def get_latest_year_with_data() -> int:
    """El año más reciente (de RESOURCES_BY_YEAR) con datos publicados."""
    for y in sorted(RESOURCES_BY_YEAR.keys(), reverse=True):
        last = get_latest_data_date(y)
        if last is not None:
            return y
    # Fallback defensivo
    return max(RESOURCES_BY_YEAR.keys())


@st.cache_data(ttl=3600 * 24, show_spinner="Cargando empresas operadoras...")
def get_all_companies(year: int) -> list[str]:
    rid = RESOURCES_BY_YEAR.get(year)
    if rid is None:
        return []
    sql = f'SELECT DISTINCT "empresa" FROM "{rid}" ORDER BY "empresa"'
    df = sql_query(sql)
    if df is None or df.empty or "empresa" not in df.columns:
        return []
    return [e for e in df["empresa"].dropna().tolist() if e.strip()]


@st.cache_data(ttl=3600, show_spinner=False)
def get_company_summary(company: str, year: int) -> dict:
    rid = RESOURCES_BY_YEAR[year]
    e = sql_escape(company)
    sql = f"""
        SELECT COUNT(DISTINCT "idpozo")   AS wells,
               COUNT(DISTINCT "cuenca")   AS basins,
               COUNT(DISTINCT "provincia") AS provinces,
               SUM("prod_pet")            AS total_oil,
               SUM("prod_gas")            AS total_gas,
               SUM("prod_agua")           AS total_water
        FROM "{rid}" WHERE "empresa" = '{e}'
    """
    df = sql_query(sql)
    if df is None or df.empty:
        return {}
    row = df.iloc[0].to_dict()
    return {k: pd.to_numeric(v, errors="coerce") for k, v in row.items()}


@st.cache_data(ttl=3600, show_spinner=False)
def get_company_basin_rollup(company: str, year: int) -> pd.DataFrame:
    rid = RESOURCES_BY_YEAR[year]
    e = sql_escape(company)
    sql = f"""
        SELECT "cuenca",
               COUNT(DISTINCT "idpozo") AS wells,
               SUM("prod_pet") AS oil,
               SUM("prod_gas") AS gas,
               SUM("prod_agua") AS water
        FROM "{rid}" WHERE "empresa" = '{e}'
        GROUP BY "cuenca" ORDER BY oil DESC NULLS LAST
    """
    df = sql_query(sql)
    if df is None:
        return pd.DataFrame()
    return coerce_numeric(df, ["wells", "oil", "gas", "water"])


@st.cache_data(ttl=3600, show_spinner=False)
def get_company_wells(
    company: str, year: int, basin: str | None = None,
    tipo_recurso: str | None = None, provincia: str | None = None,
    yacimiento: str | None = None,
) -> pd.DataFrame:
    rid = RESOURCES_BY_YEAR[year]
    e = sql_escape(company)
    where = [f'"empresa" = \'{e}\'']
    if basin:
        where.append(f'"cuenca" = \'{sql_escape(basin)}\'')
    if tipo_recurso:
        where.append(f'"tipo_de_recurso" = \'{sql_escape(tipo_recurso)}\'')
    if provincia:
        where.append(f'"provincia" = \'{sql_escape(provincia)}\'')
    if yacimiento:
        where.append(f'"areayacimiento" = \'{sql_escape(yacimiento)}\'')
    where_clause = " AND ".join(where)
    sql = f"""
        SELECT "idpozo","sigla","cuenca","provincia","tipo_de_recurso",
               "areayacimiento","formacion",
               SUM("prod_pet")  AS cum_oil,
               SUM("prod_gas")  AS cum_gas,
               SUM("prod_agua") AS cum_water,
               MAX("prod_pet")  AS peak_oil,
               COUNT(*)          AS months,
               SUM("tef")        AS total_tef
        FROM "{rid}" WHERE {where_clause}
        GROUP BY "idpozo","sigla","cuenca","provincia","tipo_de_recurso",
                 "areayacimiento","formacion"
        ORDER BY cum_oil DESC NULLS LAST
    """
    df = sql_query(sql)
    if df is None:
        return pd.DataFrame()
    return coerce_numeric(df, ["cum_oil", "cum_gas", "cum_water", "peak_oil", "months", "total_tef"])


@st.cache_data(ttl=3600, show_spinner=False)
def get_company_fields(
    company: str, year: int, basin: str | None = None,
    tipo_recurso: str | None = None, provincia: str | None = None,
) -> pd.DataFrame:
    """Yacimiento-level rollup for a company (optionally scoped to basin/tipo/province)."""
    rid = RESOURCES_BY_YEAR[year]
    e = sql_escape(company)
    where = [f'"empresa" = \'{e}\'',
             '"areayacimiento" IS NOT NULL',
             "\"areayacimiento\" != ''"]
    if basin:
        where.append(f'"cuenca" = \'{sql_escape(basin)}\'')
    if tipo_recurso:
        where.append(f'"tipo_de_recurso" = \'{sql_escape(tipo_recurso)}\'')
    if provincia:
        where.append(f'"provincia" = \'{sql_escape(provincia)}\'')
    where_clause = " AND ".join(where)

    # Q1: totals + distinct well count per yacimiento
    sql1 = f"""
        SELECT "areayacimiento","cuenca","provincia",
               COUNT(DISTINCT "idpozo") AS wells,
               SUM("prod_pet")  AS cum_oil,
               SUM("prod_gas")  AS cum_gas,
               SUM("prod_agua") AS cum_water,
               SUM("tef")        AS total_tef
        FROM "{rid}" WHERE {where_clause}
        GROUP BY "areayacimiento","cuenca","provincia"
    """
    df_tot = sql_query(sql1)
    if df_tot is None or df_tot.empty:
        return pd.DataFrame()
    df_tot = coerce_numeric(df_tot, ["wells", "cum_oil", "cum_gas", "cum_water", "total_tef"])

    # Q2: consolidated monthly peak per yacimiento (via CTE)
    sql2 = f"""
        SELECT "areayacimiento",
               MAX(oil_month) AS peak_oil_month,
               MAX(gas_month) AS peak_gas_month,
               MAX(wells_month) AS peak_concurrent_wells,
               COUNT(*) AS n_months,
               MAX("anio" * 100 + "mes") AS last_yyyymm
        FROM (
            SELECT "areayacimiento","anio","mes",
                   SUM("prod_pet") AS oil_month,
                   SUM("prod_gas") AS gas_month,
                   COUNT(DISTINCT "idpozo") AS wells_month
            FROM "{rid}" WHERE {where_clause}
            GROUP BY "areayacimiento","anio","mes"
        ) m
        GROUP BY "areayacimiento"
    """
    df_peak = sql_query(sql2)
    if df_peak is None:
        df_peak = pd.DataFrame(columns=["areayacimiento", "peak_oil_month", "peak_gas_month",
                                        "peak_concurrent_wells", "n_months", "last_yyyymm"])
    df_peak = coerce_numeric(df_peak, ["peak_oil_month", "peak_gas_month",
                                       "peak_concurrent_wells", "n_months", "last_yyyymm"])

    # Q3: last-month production per yacimiento
    sql3 = f"""
        SELECT "areayacimiento", "anio", "mes",
               SUM("prod_pet") AS oil_last, SUM("prod_gas") AS gas_last,
               SUM("tef") AS tef_last
        FROM "{rid}" WHERE {where_clause}
          AND ("anio" * 100 + "mes") IN (
                SELECT MAX("anio" * 100 + "mes") FROM "{rid}"
                WHERE {where_clause} GROUP BY "areayacimiento"
              )
        GROUP BY "areayacimiento","anio","mes"
    """
    df_last = sql_query(sql3)
    if df_last is None:
        df_last = pd.DataFrame(columns=["areayacimiento", "anio", "mes",
                                        "oil_last", "gas_last", "tef_last"])
    df_last = coerce_numeric(df_last, ["anio", "mes", "oil_last", "gas_last", "tef_last"])
    # The subquery above may return the last month across ALL yacimientos rather than per one.
    # To be safe, recompute last-month per yacimiento in pandas from a simpler query:
    sql3b = f"""
        SELECT "areayacimiento", "anio", "mes",
               SUM("prod_pet") AS oil_m, SUM("prod_gas") AS gas_m, SUM("tef") AS tef_m
        FROM "{rid}" WHERE {where_clause}
        GROUP BY "areayacimiento","anio","mes"
    """
    df_m = sql_query(sql3b)
    if df_m is not None and not df_m.empty:
        df_m = coerce_numeric(df_m, ["anio", "mes", "oil_m", "gas_m", "tef_m"])
        df_m["yyyymm"] = df_m["anio"].fillna(0).astype(int) * 100 + df_m["mes"].fillna(0).astype(int)
        idx = df_m.groupby("areayacimiento")["yyyymm"].idxmax()
        df_last = df_m.loc[idx, ["areayacimiento", "anio", "mes", "oil_m", "gas_m", "tef_m"]]
        df_last = df_last.rename(columns={"oil_m": "oil_last", "gas_m": "gas_last", "tef_m": "tef_last"})

    # Merge all pieces
    out = df_tot.merge(df_peak, on="areayacimiento", how="left")
    out = out.merge(df_last[["areayacimiento", "oil_last", "gas_last", "tef_last", "anio", "mes"]],
                    on="areayacimiento", how="left")
    out = out.rename(columns={"anio": "last_anio", "mes": "last_mes"})
    out = out.sort_values("cum_oil", ascending=False, na_position="last").reset_index(drop=True)
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def get_field_timeseries(
    company: str, yacimiento: str, years: tuple[int, ...],
) -> pd.DataFrame:
    """Consolidated monthly production for a yacimiento (summed across its wells)."""
    c = sql_escape(company)
    y = sql_escape(yacimiento)
    parts = []
    for year in years:
        rid = RESOURCES_BY_YEAR.get(year)
        if not rid:
            continue
        sql = f"""
            SELECT "anio","mes",
                   SUM("prod_pet")  AS oil,
                   SUM("prod_gas")  AS gas,
                   SUM("prod_agua") AS water,
                   SUM("tef")        AS tef,
                   COUNT(DISTINCT "idpozo") AS wells
            FROM "{rid}"
            WHERE "empresa" = '{c}' AND "areayacimiento" = '{y}'
            GROUP BY "anio","mes" ORDER BY "anio","mes"
        """
        df = sql_query(sql)
        if df is not None and not df.empty:
            parts.append(df)
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df = coerce_numeric(df, ["anio", "mes", "oil", "gas", "water", "tef", "wells"])
    df["fecha"] = pd.to_datetime(
        df["anio"].astype("Int64").astype(str) + "-" +
        df["mes"].astype("Int64").astype(str).str.zfill(2) + "-01",
        errors="coerce",
    )
    # Days in month for BPD calendar calc
    df["days_in_month"] = df["fecha"].dt.days_in_month
    return df.sort_values("fecha").reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def get_top_yacimientos(year: int, top: int = 10) -> pd.DataFrame:
    """Top yacimientos de Argentina por producción YTD — para el panel general."""
    rid = RESOURCES_BY_YEAR[year]

    # Q1: totals per (yacimiento, empresa, cuenca, provincia), top N × 3 for buffer
    sql1 = f"""
        SELECT "areayacimiento","empresa","cuenca","provincia",
               COUNT(DISTINCT "idpozo") AS wells,
               SUM("prod_pet")  AS cum_oil,
               SUM("prod_gas")  AS cum_gas,
               SUM("tef")       AS total_tef,
               COUNT(DISTINCT "anio"*100+"mes") AS n_months,
               MAX("anio"*100+"mes") AS last_yyyymm
        FROM "{rid}"
        WHERE "areayacimiento" IS NOT NULL AND "areayacimiento" != ''
        GROUP BY "areayacimiento","empresa","cuenca","provincia"
        ORDER BY cum_oil DESC NULLS LAST
        LIMIT {int(top) * 3}
    """
    df = sql_query(sql1)
    if df is None or df.empty:
        return pd.DataFrame()
    df = coerce_numeric(df, ["wells", "cum_oil", "cum_gas", "total_tef", "n_months", "last_yyyymm"])

    # Keep the top N by cum_oil
    df = df.sort_values("cum_oil", ascending=False, na_position="last").head(int(top)).reset_index(drop=True)

    # Q2: monthly rollup for just these top yacimientos — compute last-month BPD
    yac_list = df["areayacimiento"].dropna().unique().tolist()
    if yac_list:
        in_clause = ",".join("'" + sql_escape(y) + "'" for y in yac_list)
        sql2 = f"""
            SELECT "areayacimiento","empresa","anio","mes",
                   SUM("prod_pet") AS oil_m, SUM("tef") AS tef_m,
                   COUNT(DISTINCT "idpozo") AS wells_m
            FROM "{rid}"
            WHERE "areayacimiento" IN ({in_clause})
            GROUP BY "areayacimiento","empresa","anio","mes"
        """
        df_m = sql_query(sql2)
        if df_m is not None and not df_m.empty:
            df_m = coerce_numeric(df_m, ["anio", "mes", "oil_m", "tef_m", "wells_m"])
            df_m["yyyymm"] = (
                df_m["anio"].fillna(0).astype(int) * 100
                + df_m["mes"].fillna(0).astype(int)
            )
            idx = df_m.groupby(["areayacimiento", "empresa"])["yyyymm"].idxmax()
            df_last = df_m.loc[idx, ["areayacimiento", "empresa", "anio", "mes",
                                     "oil_m", "tef_m", "wells_m"]].rename(
                columns={"oil_m": "oil_last", "tef_m": "tef_last", "wells_m": "wells_last",
                         "anio": "last_anio", "mes": "last_mes"})
            df = df.merge(df_last, on=["areayacimiento", "empresa"], how="left")

    return df


@st.cache_data(ttl=3600, show_spinner=False)
def get_basin_field_totals(basin: str, year: int) -> pd.DataFrame:
    """Cumulative oil per yacimiento in a basin — for P10/P50/P90 benchmarks."""
    rid = RESOURCES_BY_YEAR[year]
    e = sql_escape(basin)
    sql = f"""
        SELECT "areayacimiento", "empresa",
               COUNT(DISTINCT "idpozo") AS wells,
               SUM("prod_pet") AS cum_oil,
               SUM("prod_gas") AS cum_gas
        FROM "{rid}"
        WHERE "cuenca" = '{e}' AND "areayacimiento" IS NOT NULL AND "areayacimiento" != ''
        GROUP BY "areayacimiento", "empresa"
    """
    df = sql_query(sql)
    if df is None:
        return pd.DataFrame()
    return coerce_numeric(df, ["wells", "cum_oil", "cum_gas"])


@st.cache_data(ttl=3600, show_spinner=False)
def get_well_timeseries(idpozo: str, years: tuple[int, ...]) -> pd.DataFrame:
    """Fetch monthly time series for one well across one or more years."""
    parts = []
    idpozo_e = sql_escape(str(idpozo))
    for y in years:
        rid = RESOURCES_BY_YEAR.get(y)
        if not rid:
            continue
        sql = f"""
            SELECT "anio","mes","prod_pet","prod_gas","prod_agua","tef",
                   "empresa","sigla","cuenca","provincia","tipo_de_recurso",
                   "areayacimiento","formacion","tipoestado","tipoextraccion"
            FROM "{rid}" WHERE "idpozo" = '{idpozo_e}'
        """
        df = sql_query(sql)
        if df is not None and not df.empty:
            parts.append(df)
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df = coerce_numeric(df, ["anio", "mes", "prod_pet", "prod_gas", "prod_agua", "tef"])
    # Build date column
    df["fecha"] = pd.to_datetime(
        df["anio"].astype("Int64").astype(str) + "-" + df["mes"].astype("Int64").astype(str).str.zfill(2) + "-01",
        errors="coerce",
    )
    return df.sort_values("fecha").reset_index(drop=True)


@st.cache_data(ttl=3600 * 24, show_spinner=False)
def get_well_padron(sigla: str) -> dict:
    e = sql_escape(sigla)
    sql = f'SELECT * FROM "{PADRON_RESOURCE}" WHERE "sigla" = \'{e}\' LIMIT 1'
    df = sql_query(sql)
    if df is None or df.empty:
        return {}
    return df.iloc[0].to_dict()


@st.cache_data(ttl=3600, show_spinner=False)
def get_basin_well_totals(basin: str, year: int) -> pd.DataFrame:
    """Cumulative oil per well across the entire basin — for P10/P50/P90 and ranking."""
    rid = RESOURCES_BY_YEAR[year]
    e = sql_escape(basin)
    sql = f"""
        SELECT "idpozo","sigla","empresa",
               SUM("prod_pet") AS cum_oil
        FROM "{rid}" WHERE "cuenca" = '{e}'
        GROUP BY "idpozo","sigla","empresa"
    """
    df = sql_query(sql)
    if df is None:
        return pd.DataFrame()
    return coerce_numeric(df, ["cum_oil"])


@st.cache_data(ttl=3600, show_spinner=False)
def get_basin_operator_leaderboard(basin: str | None, year: int, top: int = 5) -> pd.DataFrame:
    rid = RESOURCES_BY_YEAR[year]
    where = f'WHERE "cuenca" = \'{sql_escape(basin)}\'' if basin else ""
    sql = f"""
        SELECT "empresa",
               COUNT(DISTINCT "idpozo") AS wells,
               SUM("prod_pet") AS oil
        FROM "{rid}" {where}
        GROUP BY "empresa" ORDER BY oil DESC NULLS LAST LIMIT {top}
    """
    df = sql_query(sql)
    if df is None:
        return pd.DataFrame()
    return coerce_numeric(df, ["wells", "oil"])


# ---------------------------------------------------------------------------
# Computation helpers
# ---------------------------------------------------------------------------
def quality_pill(val: float, p33: float, p66: float) -> str:
    if pd.isna(val):
        return "—"
    if val >= p66:
        return "🟢"
    if val >= p33:
        return "🟠"
    return "🔴"


def compute_decline_rate(series: pd.Series) -> float:
    """Exponential decline rate D (per month) from a positive production series."""
    s = series.dropna()
    s = s[s > 0]
    if len(s) < 2:
        return 0.0
    try:
        t = np.arange(len(s))
        coeffs = np.polyfit(t, np.log(s.values), 1)
        return max(-coeffs[0], 0.0)
    except Exception:
        return 0.0


def well_status(series: pd.Series) -> str:
    """Marginal if last-3-month avg has dropped >30% vs prior-3; Inactivo if all zero."""
    s = series.dropna().tolist()
    if not s:
        return "Sin datos"
    if len(s) < 3:
        return "Activo"
    last3 = s[-3:]
    if all(v == 0 for v in last3):
        return "Inactivo"
    if len(s) >= 6:
        prev3_avg = np.mean(s[-6:-3]) or 0
        last3_avg = np.mean(last3) or 0
        if prev3_avg > 0 and last3_avg < prev3_avg * 0.7:
            return "Marginal"
    return "Activo"


def navigate(screen: str, **kwargs) -> None:
    st.session_state["screen"] = screen
    for k, v in kwargs.items():
        st.session_state[k] = v


def fmt_m3(x: float) -> str:
    if x is None or pd.isna(x):
        return "—"
    x = float(x)
    if abs(x) >= 1_000_000:
        return f"{x/1_000_000:,.2f} Mm³"
    if abs(x) >= 1_000:
        return f"{x/1_000:,.1f} km³"
    return f"{x:,.0f} m³"


# ---------------------------------------------------------------------------
# Barrel conversions
# ---------------------------------------------------------------------------
M3_TO_BBL = 6.2898  # 1 cubic metre = 6.2898 US oil barrels

def m3_to_bbl(m3: float) -> float:
    return (m3 or 0) * M3_TO_BBL


def bpd_calendar(m3: float, days: float) -> float:
    """BPD calendar: barrels per calendar day over the given period."""
    if not days or days <= 0 or m3 is None or pd.isna(m3):
        return 0.0
    return float(m3) * M3_TO_BBL / float(days)


def bpd_ontime(m3: float, tef_days: float) -> float:
    """BPD on-time: barrels per effective producing day (uses tef)."""
    if not tef_days or tef_days <= 0 or m3 is None or pd.isna(m3):
        return 0.0
    return float(m3) * M3_TO_BBL / float(tef_days)


def fmt_bpd(x: float) -> str:
    if x is None or pd.isna(x):
        return "—"
    x = float(x)
    if abs(x) >= 1_000_000:
        return f"{x/1_000_000:,.2f} M bpd"
    if abs(x) >= 1_000:
        return f"{x/1_000:,.1f} k bpd"
    return f"{x:,.0f} bpd"


def days_in_period(year: int, last_anio, last_mes) -> int:
    """Number of calendar days elapsed in `year` through the end of (last_anio, last_mes)."""
    try:
        la = int(float(last_anio)) if last_anio is not None and not pd.isna(last_anio) else year
        lm = int(float(last_mes)) if last_mes is not None and not pd.isna(last_mes) else 12
    except Exception:
        la, lm = year, 12
    if la != year:
        return 365
    # Sum days of months 1..lm
    from calendar import monthrange
    return sum(monthrange(year, m)[1] for m in range(1, max(1, lm) + 1))


def fmt_int(x) -> str:
    if x is None or pd.isna(x):
        return "—"
    return f"{int(float(x)):,}"


MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]

def fmt_last_date(last: tuple[int, int] | None) -> str:
    if not last:
        return "—"
    y, m = last
    try:
        return f"{MESES_ES[int(m) - 1]} {int(y)}"
    except (IndexError, ValueError):
        return f"{y}-{m:02d}"


# ---------------------------------------------------------------------------
# Session init
# ---------------------------------------------------------------------------
st.session_state.setdefault("screen", "operator")
st.session_state.setdefault("selected_operator", None)
st.session_state.setdefault("selected_basin", None)
st.session_state.setdefault("selected_yacimiento", None)
st.session_state.setdefault("selected_well_id", None)
st.session_state.setdefault("selected_well_sigla", None)
st.session_state.setdefault("year", get_latest_year_with_data())
st.session_state.setdefault("include_prior_year", True)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### ⚙️ Configuración")
    years_available = sorted(RESOURCES_BY_YEAR.keys(), reverse=True)
    year = st.selectbox(
        "Año de producción",
        years_available,
        index=years_available.index(st.session_state["year"]) if st.session_state["year"] in years_available else 0,
        help="Las estadísticas acumulativas son para el año seleccionado (YTD).",
    )
    if year != st.session_state["year"]:
        st.session_state["year"] = year
        st.session_state["selected_operator"] = None
        st.session_state["screen"] = "operator"
        st.rerun()

    include_prior = st.checkbox(
        "Incluir año previo en detalle",
        value=st.session_state["include_prior_year"],
        help="Extiende la serie temporal al año anterior para una mejor curva y DCA.",
    )
    st.session_state["include_prior_year"] = include_prior

    st.markdown("---")
    if st.button(
        "🔄 Actualizar datos ahora",
        use_container_width=True,
        help="Limpia el caché y re-consulta al API de la Secretaría de Energía.",
    ):
        st.cache_data.clear()
        st.session_state["_last_refresh"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.rerun()
    if st.session_state.get("_last_refresh"):
        st.caption(f"Última actualización manual: {st.session_state['_last_refresh']}")

    st.markdown("---")
    _sb_last = get_latest_data_date(st.session_state["year"])
    _sb_last_str = fmt_last_date(_sb_last)
    st.markdown(
        f"""<div class='muted'>
        Datos en vivo del portal oficial <b>datos.energia.gob.ar</b> —
        Secretaría de Energía, Capítulo IV (producción mensual por pozo).<br>
        <b>Último dato publicado:</b> {_sb_last_str}
        </div>""",
        unsafe_allow_html=True,
    )
    if st.session_state.get("_last_api_error"):
        with st.expander("Último error de API", expanded=False):
            st.code(st.session_state["_last_api_error"])


# ---------------------------------------------------------------------------
# SCREEN 1 — Operator selector
# ---------------------------------------------------------------------------
def screen_operator() -> None:
    st.title("🛢️ Argentina Well Intelligence Dashboard")
    last_date = get_latest_data_date(st.session_state["year"])
    last_str = fmt_last_date(last_date)
    st.caption(
        f"Fuente: Secretaría de Energía · Año {st.session_state['year']} · "
        f"Producción mensual por pozo (Capítulo IV) · "
        f"**Último dato publicado: {last_str}**"
    )

    companies = get_all_companies(st.session_state["year"])
    if not companies:
        st.error(
            "No se pudo obtener la lista de empresas. "
            "Verifique conectividad con datos.energia.gob.ar o revise el error en la barra lateral."
        )
        return

    st.markdown(
        f"**{len(companies)} empresas operadoras** con producción registrada en "
        f"{st.session_state['year']}."
    )

    col_a, col_b = st.columns([3, 1])
    with col_a:
        selected = st.selectbox(
            "Buscar empresa",
            options=["-- Seleccioná una empresa --"] + companies,
            index=0,
            key="op_selector",
            help="Escribí para filtrar (YPF, Vista, Pan American, Pampa, Tecpetrol...).",
        )
    with col_b:
        st.metric("Total de empresas", len(companies))

    if selected.startswith("--"):
        # ---------- Pulso del país: top yacimientos ----------
        year_cur = st.session_state["year"]
        last_ranking = get_latest_data_date(year_cur)
        last_ranking_str = fmt_last_date(last_ranking)
        st.markdown(f"### 🏆 Top 10 yacimientos de Argentina · YTD {year_cur}")
        st.caption(
            f"Ranking por petróleo acumulado en {year_cur} · "
            f"Último mes publicado: **{last_ranking_str}** · "
            f"BPD calculado sobre días calendario."
        )

        with st.spinner("Calculando ranking nacional..."):
            top_yac = get_top_yacimientos(year_cur, top=10)

        if top_yac.empty:
            st.info("No se pudo obtener el ranking nacional.")
        else:
            t = top_yac.copy()
            # BPD YTD: cum_oil × 6.2898 / (n_months × 30.44)
            t["days_covered"] = (t["n_months"].fillna(0) * 30.44).clip(lower=1)
            t["bpd_ytd"] = t["cum_oil"] * M3_TO_BBL / t["days_covered"]
            # BPD último mes calendario
            if "oil_last" in t.columns:
                t["bpd_last"] = t["oil_last"].fillna(0) * M3_TO_BBL / 30.44
            else:
                t["bpd_last"] = 0.0

            t.insert(0, "#", range(1, len(t) + 1))
            disp = t.rename(columns={
                "areayacimiento": "Yacimiento",
                "empresa": "Operador",
                "cuenca": "Cuenca",
                "provincia": "Provincia",
                "wells": "Pozos",
                "cum_oil": "Petróleo YTD (m³)",
                "bpd_ytd": "BPD YTD",
                "bpd_last": "BPD últ. mes",
                "cum_gas": "Gas (miles m³)",
            })[["#", "Yacimiento", "Operador", "Cuenca", "Provincia",
                "Pozos", "Petróleo YTD (m³)", "BPD YTD", "BPD últ. mes",
                "Gas (miles m³)"]]

            for c in ["Petróleo YTD (m³)", "Gas (miles m³)"]:
                disp[c] = disp[c].apply(lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
            for c in ["BPD YTD", "BPD últ. mes"]:
                disp[c] = disp[c].apply(lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
            disp["Pozos"] = disp["Pozos"].apply(
                lambda v: f"{int(v):,}" if pd.notna(v) else "—"
            )

            st.dataframe(disp, use_container_width=True, hide_index=True, height=400)

            # Totales de la vista
            total_bpd_ytd = t["bpd_ytd"].sum()
            total_bpd_last = t["bpd_last"].sum()
            total_wells = int(t["wells"].fillna(0).sum())
            m1, m2, m3 = st.columns(3)
            m1.metric("BPD YTD (top 10)", fmt_bpd(total_bpd_ytd))
            m2.metric("BPD último mes (top 10)", fmt_bpd(total_bpd_last))
            m3.metric("Pozos (top 10)", f"{total_wells:,}")

            # Drill-in directo desde el ranking nacional
            st.markdown("**Saltar al detalle del yacimiento:**")
            pick = st.selectbox(
                "Elegí un yacimiento del top",
                ["-- Elegir --"] + [
                    f"{r['areayacimiento']}  —  {r['empresa']}"
                    for _, r in top_yac.iterrows()
                ],
                key="top_yac_picker", label_visibility="collapsed",
            )
            if pick != "-- Elegir --":
                yac_pick, emp_pick = [x.strip() for x in pick.split("—", 1)]
                row = top_yac[
                    (top_yac["areayacimiento"] == yac_pick)
                    & (top_yac["empresa"] == emp_pick)
                ].iloc[0]
                navigate(
                    "field_detail",
                    selected_operator=emp_pick,
                    selected_yacimiento=yac_pick,
                    selected_basin=row["cuenca"],
                )
                st.rerun()

        with st.expander("Ver todas las empresas (top 20 por pozos)", expanded=False):
            top = get_basin_operator_leaderboard(None, st.session_state["year"], top=20)
            if not top.empty:
                top_disp = top.rename(
                    columns={"empresa": "Empresa", "wells": "Pozos", "oil": "Petróleo (m³)"}
                )
                top_disp["Petróleo (m³)"] = top_disp["Petróleo (m³)"].apply(lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
                top_disp["Pozos"] = top_disp["Pozos"].apply(lambda v: f"{int(v):,}" if pd.notna(v) else "—")
                top_disp.index = range(1, len(top_disp) + 1)
                st.dataframe(top_disp, use_container_width=True)
        return

    st.session_state["selected_operator"] = selected

    with st.spinner(f"Consultando datos de {selected}..."):
        summary = get_company_summary(selected, st.session_state["year"])
        basins_df = get_company_basin_rollup(selected, st.session_state["year"])

    if not summary or basins_df.empty:
        st.warning("No se encontraron datos para esta empresa en el año seleccionado.")
        return

    # ---------- Summary KPI row ----------
    st.markdown("### Resumen")
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Pozos activos", fmt_int(summary.get("wells")))
    k2.metric("Petróleo (m³)", fmt_m3(summary.get("total_oil")))
    k3.metric("Gas (miles m³)", fmt_m3(summary.get("total_gas")))
    k4.metric("Cuencas", fmt_int(summary.get("basins")))

    st.markdown("")
    b1, b2 = st.columns([3, 1])
    with b2:
        if st.button("📊 Ver todos los yacimientos", type="primary", use_container_width=True):
            navigate("fields", selected_operator=selected, selected_basin=None)
            st.rerun()

    # ---------- Basin cards ----------
    st.markdown("### Cuencas donde opera")
    max_oil = basins_df["oil"].max() if "oil" in basins_df.columns and len(basins_df) else 1

    cols = st.columns(min(len(basins_df), 3) or 1)
    for idx, (_, row) in enumerate(basins_df.iterrows()):
        col = cols[idx % len(cols)]
        with col:
            oil = row.get("oil") or 0
            gas = row.get("gas") or 0
            wells = row.get("wells") or 0
            pct = (oil / max_oil) if (max_oil and max_oil > 0) else 0
            st.markdown(
                f"""
<div class="basin-card">
    <h4>{row['cuenca']}</h4>
    <div class="meta">
        <b>{int(wells):,}</b> pozos · {oil:,.0f} m³ petróleo · {gas:,.0f} miles m³ gas
    </div>
</div>
""",
                unsafe_allow_html=True,
            )
            st.progress(min(max(pct, 0), 1.0))
            if st.button("Ver yacimientos →", key=f"basin_{row['cuenca']}", use_container_width=True):
                navigate("fields", selected_operator=selected, selected_basin=row["cuenca"])
                st.rerun()


# ---------------------------------------------------------------------------
# SCREEN 2 — Field (yacimiento) ranking
# ---------------------------------------------------------------------------
def screen_fields() -> None:
    operator = st.session_state.get("selected_operator")
    basin = st.session_state.get("selected_basin")
    year = st.session_state["year"]

    if not operator:
        navigate("operator")
        st.rerun()

    back_col, _ = st.columns([2, 10])
    with back_col:
        if st.button("← Volver a empresas", use_container_width=True, key="back_fields"):
            navigate("operator")
            st.rerun()
    st.title(f"{operator}")
    st.caption(f"{basin or 'Todas las cuencas'} · Año {year}")

    with st.spinner("Cargando yacimientos..."):
        fields = get_company_fields(operator, year, basin=basin)

    if fields.empty:
        st.warning("No se encontraron yacimientos para este filtro.")
        return

    # ---------- Filters ----------
    f1, f2 = st.columns(2)
    with f1:
        if not basin:
            basins_list = ["Todas"] + sorted(fields["cuenca"].dropna().unique().tolist())
            b_sel = st.selectbox("Cuenca", basins_list, key="b_filter_f")
            if b_sel != "Todas":
                fields = fields[fields["cuenca"] == b_sel]
    with f2:
        provs = ["Todas"] + sorted(fields["provincia"].dropna().unique().tolist())
        p_sel = st.selectbox("Provincia", provs, key="p_filter_f")
        if p_sel != "Todas":
            fields = fields[fields["provincia"] == p_sel]

    if fields.empty:
        st.info("Sin yacimientos con los filtros seleccionados.")
        return

    # ---------- Derived BPD metrics ----------
    fields = fields.copy()
    # BPD YTD: use n_months × 30.44 as a proxy for calendar days covered
    fields["days_covered"] = (fields["n_months"].fillna(0) * 30.44).clip(lower=1)
    fields["bpd_ytd"] = fields["cum_oil"] * M3_TO_BBL / fields["days_covered"]
    # BPD last month calendar (use 30.44)
    fields["bpd_last_month"] = fields["oil_last"] * M3_TO_BBL / 30.44
    # BPD peak month
    fields["bpd_peak_month"] = fields["peak_oil_month"] * M3_TO_BBL / 30.44

    # Quality based on cum_oil percentiles
    if len(fields) >= 3:
        p33 = fields["cum_oil"].quantile(0.33)
        p66 = fields["cum_oil"].quantile(0.66)
    else:
        p33 = p66 = 0
    fields["quality"] = fields["cum_oil"].apply(lambda v: quality_pill(v, p33, p66))
    fields["status"] = fields["oil_last"].apply(
        lambda v: "Activo" if (v or 0) > 0 else "Inactivo"
    )

    fields = fields.sort_values("cum_oil", ascending=False, na_position="last").reset_index(drop=True)
    fields.insert(0, "rank", fields.index + 1)

    # ---------- Layout ----------
    main, side = st.columns([3, 1])

    with main:
        st.subheader(f"{len(fields):,} yacimientos")

        display_cols = {
            "rank": "#",
            "areayacimiento": "Yacimiento",
            "cuenca": "Cuenca",
            "provincia": "Provincia",
            "wells": "Pozos",
            "cum_oil": "Petróleo (m³)",
            "bpd_ytd": "BPD YTD",
            "oil_last": "Últ. mes (m³)",
            "bpd_last_month": "BPD últ. mes",
            "cum_gas": "Gas (miles m³)",
            "peak_oil_month": "Pico mes (m³)",
            "bpd_peak_month": "BPD pico",
            "n_months": "Meses",
            "status": "Estado",
            "quality": "Calidad",
        }
        display = fields.rename(columns=display_cols)[list(display_cols.values())]

        # Formatting
        for c in ["Petróleo (m³)", "Últ. mes (m³)", "Gas (miles m³)", "Pico mes (m³)"]:
            display[c] = display[c].apply(lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
        for c in ["BPD YTD", "BPD últ. mes", "BPD pico"]:
            display[c] = display[c].apply(lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
        for c in ["Pozos", "Meses"]:
            display[c] = display[c].apply(lambda v: f"{int(v):,}" if pd.notna(v) else "—")

        st.dataframe(display, use_container_width=True, height=520, hide_index=True)

        st.markdown("**Seleccionar yacimiento para ver detalle:**")
        yac_options = fields["areayacimiento"].tolist()
        chosen = st.selectbox(
            "Yacimiento", ["-- Elegir --"] + yac_options,
            key="field_picker", label_visibility="collapsed",
        )
        if chosen != "-- Elegir --":
            row = fields[fields["areayacimiento"] == chosen].iloc[0]
            navigate(
                "field_detail",
                selected_yacimiento=chosen,
                selected_basin=row["cuenca"],
            )
            st.rerun()

    with side:
        st.subheader("Leaderboard")
        st.caption(f"Top 5 operadores — {basin or 'Argentina'}")
        leader = get_basin_operator_leaderboard(basin, year, top=5)
        if not leader.empty:
            leader_disp = leader.rename(
                columns={"empresa": "Empresa", "wells": "Pozos", "oil": "Petróleo (m³)"}
            )
            leader_disp["Petróleo (m³)"] = leader_disp["Petróleo (m³)"].apply(
                lambda v: f"{v:,.0f}" if pd.notna(v) else "—"
            )
            leader_disp["Pozos"] = leader_disp["Pozos"].apply(
                lambda v: f"{int(v):,}" if pd.notna(v) else "—"
            )
            leader_disp.index = range(1, len(leader_disp) + 1)
            st.dataframe(leader_disp, use_container_width=True, height=240)

        # Quick stats for this view
        st.markdown("---")
        st.markdown("**Totales de la vista**")
        st.metric("Yacimientos", f"{len(fields):,}")
        st.metric("Pozos", f"{int(fields['wells'].sum()):,}")
        st.metric("Petróleo", fmt_m3(fields["cum_oil"].sum()))
        st.metric("BPD YTD (suma)", fmt_bpd(fields["bpd_ytd"].sum()))
        st.metric("Gas (miles m³)", fmt_m3(fields["cum_gas"].sum()))


# ---------------------------------------------------------------------------
# SCREEN 3 — Field (yacimiento) detail — consolidated across all its wells
# ---------------------------------------------------------------------------
def screen_field_detail() -> None:
    operator = st.session_state.get("selected_operator")
    yacimiento = st.session_state.get("selected_yacimiento")
    basin = st.session_state.get("selected_basin")
    year = st.session_state["year"]
    include_prior = st.session_state["include_prior_year"]

    if not operator or not yacimiento:
        navigate("fields")
        st.rerun()

    back_col, _ = st.columns([2, 10])
    with back_col:
        if st.button("← Volver a yacimientos", use_container_width=True, key="back_field_detail"):
            navigate("fields")
            st.rerun()

    # ---------- Gather consolidated time series ----------
    years = [year, year - 1] if include_prior and (year - 1) in RESOURCES_BY_YEAR else [year]
    with st.spinner(f"Cargando serie consolidada ({', '.join(str(y) for y in years)})..."):
        ts = get_field_timeseries(operator, yacimiento, tuple(sorted(years)))
        wells_df = get_company_wells(operator, year, basin=basin, yacimiento=yacimiento)

    if ts.empty:
        st.error("No se encontraron datos de producción para este yacimiento.")
        return

    # Fill in basin/province from the wells query if it's missing in state
    if not basin and not wells_df.empty:
        basin = wells_df.iloc[0].get("cuenca", "")
    province = wells_df.iloc[0].get("provincia", "") if not wells_df.empty else ""
    tipo = wells_df.iloc[0].get("tipo_de_recurso", "") if not wells_df.empty else ""
    n_wells_total = int(wells_df["idpozo"].nunique()) if not wells_df.empty else int(ts["wells"].max() or 0)

    st.title(yacimiento)
    st.caption(
        f"**{operator}** · {basin or '—'} · {province or '—'} · {tipo or '—'} · "
        f"{n_wells_total} pozos totales"
    )

    # ---------- Ownership ----------
    st.subheader("Participación")
    yac_upper = str(yacimiento).upper()
    ownership = None
    for key, parts in OWNERSHIP_MOCK.items():
        if key in yac_upper:
            ownership = parts
            break
    if ownership is None:
        ownership = {operator: 100.0}
        st.caption("_Sin datos públicos de JV — operador único supuesto_")
    else:
        st.caption("_Participaciones aproximadas de JV conocidas (referencia)_")

    own_df = pd.DataFrame([{"Operador": k, "Stake": v} for k, v in ownership.items()])
    fig_own = px.bar(
        own_df, x="Stake", y=["Participación"] * len(own_df),
        color="Operador", orientation="h",
        text=own_df["Stake"].apply(lambda v: f"{v:.1f}%"),
        color_discrete_sequence=px.colors.qualitative.Set2,
    )
    fig_own.update_layout(
        height=90, margin=dict(l=0, r=0, t=5, b=0),
        xaxis=dict(range=[0, 100], title=None, showgrid=False),
        yaxis=dict(title=None),
        legend=dict(orientation="h", yanchor="bottom", y=1.05),
    )
    st.plotly_chart(fig_own, use_container_width=True)

    # ---------- Build consolidated monthly series ----------
    monthly = ts.sort_values("fecha").reset_index(drop=True).copy()
    # Ensure numeric
    for c in ["oil", "gas", "water", "tef", "wells", "days_in_month"]:
        if c in monthly.columns:
            monthly[c] = pd.to_numeric(monthly[c], errors="coerce").fillna(0)

    # Focus KPI period = the selected year
    cur_year = monthly[monthly["anio"].astype("Int64").astype(float) == float(year)].copy()
    cum_oil = cur_year["oil"].sum()
    cum_gas = cur_year["gas"].sum()
    cum_water = cur_year["water"].sum()
    n_months = len(cur_year)
    days_cov = cur_year["days_in_month"].sum() or max(1, int(n_months * 30.44))
    tef_cov = cur_year["tef"].sum()

    peak_row = monthly.loc[monthly["oil"].idxmax()] if monthly["oil"].max() > 0 else None
    peak_oil_m3 = float(peak_row["oil"]) if peak_row is not None else 0.0
    peak_days = float(peak_row["days_in_month"]) if peak_row is not None else 30.44
    peak_date = peak_row["fecha"] if peak_row is not None else None
    peak_wells = int(peak_row["wells"]) if peak_row is not None else 0

    last_row = monthly.iloc[-1]
    last_oil_m3 = float(last_row["oil"])
    last_days = float(last_row["days_in_month"]) or 30.44
    last_tef = float(last_row["tef"])
    last_wells = int(last_row["wells"])
    last_date = last_row["fecha"]

    bpd_ytd = bpd_calendar(cum_oil, days_cov)
    bpd_ytd_ontime = bpd_ontime(cum_oil, tef_cov)
    bpd_last = bpd_calendar(last_oil_m3, last_days)
    bpd_last_ontime = bpd_ontime(last_oil_m3, last_tef)
    bpd_peak = bpd_calendar(peak_oil_m3, peak_days)
    bpd_per_well_last = bpd_last / last_wells if last_wells > 0 else 0.0

    D = compute_decline_rate(monthly["oil"].tail(12))
    efficiency = (last_oil_m3 / peak_oil_m3 * 100) if peak_oil_m3 > 0 else 0
    total_liquid = cum_oil + cum_water
    water_cut = (cum_water / total_liquid * 100) if total_liquid > 0 else 0
    gor = (cum_gas * 1000 / cum_oil) if cum_oil > 0 else 0
    status = well_status(monthly["oil"])
    months_active = int((monthly["oil"] > 0).sum())

    # ---------- KPIs ----------
    st.subheader(
        f"KPIs consolidados · Estado: "
        f":{'green' if status=='Activo' else 'orange' if status=='Marginal' else 'red'}[{status}]"
    )

    r1 = st.columns(4)
    r1[0].metric(
        "Producción último mes",
        f"{last_oil_m3:,.0f} m³",
        help=f"{last_date.strftime('%Y-%m') if pd.notna(last_date) else '—'} · {last_wells} pozos activos",
    )
    r1[1].metric("BPD último mes (calendario)", fmt_bpd(bpd_last))
    r1[2].metric("BPD último mes (on-time)", fmt_bpd(bpd_last_ontime),
                 help="Barriles por día efectivo (TEF).")
    r1[3].metric("BPD por pozo (últ. mes)", fmt_bpd(bpd_per_well_last))

    r2 = st.columns(4)
    r2[0].metric(f"Petróleo {year} (YTD)", fmt_m3(cum_oil))
    r2[1].metric(f"BPD {year} (YTD)", fmt_bpd(bpd_ytd))
    r2[2].metric(f"BPD {year} on-time (YTD)", fmt_bpd(bpd_ytd_ontime))
    r2[3].metric("Pozos (máx concurrente)", f"{int(monthly['wells'].max() or 0)}")

    r3 = st.columns(4)
    r3[0].metric(
        "Pico mensual histórico",
        f"{peak_oil_m3:,.0f} m³",
        help=f"{peak_date.strftime('%Y-%m') if peak_date is not None and pd.notna(peak_date) else '—'} · {peak_wells} pozos",
    )
    r3[1].metric("BPD pico", fmt_bpd(bpd_peak))
    r3[2].metric("Eficiencia (últ. vs pico)", f"{efficiency:.1f}%")
    r3[3].metric("Decline rate (mensual)", f"{D*100:.1f}%")

    r4 = st.columns(4)
    r4[0].metric("Gas acumulado (YTD)", f"{cum_gas:,.0f} miles m³")
    r4[1].metric("Water cut", f"{water_cut:.1f}%")
    r4[2].metric("GOR (m³/m³)", f"{gor:,.0f}")
    r4[3].metric("Meses con datos", f"{months_active}")

    if months_active < 6:
        st.warning("⚠️ Menos de 6 meses de datos consolidados — modelo predictivo no confiable.")

    # ---------- Curve + DCA ----------
    st.subheader("Curva consolidada & proyección (DCA)")
    c1, c2 = st.columns([3, 1])
    with c2:
        mode = st.radio(
            "Vista",
            ["Volumen (m³)", "BPD (calendario)", "Ingresos (USD)"],
            key="chart_mode",
        )
        price = 70.0
        if mode == "Ingresos (USD)":
            price = st.number_input(
                "Precio crudo (USD/bbl)", value=70.0, min_value=1.0, step=5.0
            )

    if mode == "Ingresos (USD)":
        monthly["value"] = monthly["oil"] * M3_TO_BBL * price
        y_label = "Ingresos mensuales (USD)"
    elif mode == "BPD (calendario)":
        dim = monthly["days_in_month"].replace(0, np.nan).fillna(30.44)
        monthly["value"] = monthly["oil"] * M3_TO_BBL / dim
        y_label = "BPD (barriles / día calendario)"
    else:
        monthly["value"] = monthly["oil"]
        y_label = "Petróleo (m³/mes)"

    with c1:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=monthly["fecha"], y=monthly["value"],
            mode="lines+markers", name="Producción consolidada",
            line=dict(color="#2563eb", width=2.5),
            marker=dict(size=5),
            customdata=np.stack([monthly["wells"], monthly["oil"]], axis=-1),
            hovertemplate=(
                "<b>%{x|%Y-%m}</b><br>"
                "Valor: %{y:,.1f}<br>"
                "Pozos activos: %{customdata[0]:.0f}<br>"
                "Petróleo: %{customdata[1]:,.0f} m³<extra></extra>"
            ),
        ))

        if months_active >= 6 and D > 0:
            qi = monthly["oil"].tail(3).mean()
            economic_limit = max(2.0, 0.001 * peak_oil_m3)  # 0.1% of peak
            forecast_oil = []
            for t in range(1, 361):
                qt = qi * np.exp(-D * t)
                if qt < economic_limit:
                    break
                forecast_oil.append(qt)
            if forecast_oil:
                last_d = monthly["fecha"].iloc[-1]
                dates = pd.date_range(last_d + pd.DateOffset(months=1),
                                      periods=len(forecast_oil), freq="MS")
                if mode == "Ingresos (USD)":
                    fvals = [v * M3_TO_BBL * price for v in forecast_oil]
                elif mode == "BPD (calendario)":
                    fvals = [v * M3_TO_BBL / 30.44 for v in forecast_oil]
                else:
                    fvals = forecast_oil
                fig.add_trace(go.Scatter(
                    x=dates, y=fvals, mode="lines", name="Proyección DCA",
                    line=dict(color="#f97316", width=2, dash="dash"),
                ))
                eur = cum_oil + sum(forecast_oil)
                fig.add_annotation(
                    x=dates[len(dates) // 2], y=max(fvals) * 0.8 if fvals else 0,
                    text=f"<b>EUR: {eur:,.0f} m³ ({eur*M3_TO_BBL/1000:,.0f} k bbl)</b>",
                    showarrow=True, arrowhead=2, bgcolor="#fff", bordercolor="#f97316",
                    font=dict(size=12, color="#c2410c"),
                )

        fig.update_layout(
            height=440, margin=dict(l=40, r=20, t=30, b=40),
            xaxis_title="Fecha", yaxis_title=y_label,
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True)

    # ---------- Active-well count evolution ----------
    st.subheader("Pozos activos por mes")
    fig_w = go.Figure()
    fig_w.add_trace(go.Bar(
        x=monthly["fecha"], y=monthly["wells"],
        marker_color="#0ea5e9", name="Pozos activos",
        hovertemplate="%{x|%Y-%m}<br>%{y:.0f} pozos<extra></extra>",
    ))
    fig_w.update_layout(
        height=220, margin=dict(l=40, r=20, t=10, b=40),
        xaxis_title="Fecha", yaxis_title="Nº pozos",
    )
    st.plotly_chart(fig_w, use_container_width=True)

    # ---------- Basin benchmarking (yacimiento vs yacimiento) ----------
    st.subheader("Benchmark de cuenca (yacimientos)")
    if basin:
        with st.spinner("Calculando percentiles de la cuenca..."):
            basin_fields = get_basin_field_totals(basin, year)
        # Aggregate basin rollup to yacimiento-level (sum across operators)
        if not basin_fields.empty:
            basin_fields = (
                basin_fields.groupby("areayacimiento", as_index=False)
                .agg(cum_oil=("cum_oil", "sum"), wells=("wells", "sum"))
            )
        if basin_fields.empty or len(basin_fields) < 5:
            st.info("Cuenca con pocos yacimientos; no se calculan percentiles.")
        else:
            p10 = basin_fields["cum_oil"].quantile(0.10)
            p50 = basin_fields["cum_oil"].quantile(0.50)
            p90 = basin_fields["cum_oil"].quantile(0.90)
            bench = go.Figure()
            bench.add_trace(go.Bar(
                y=[yacimiento], x=[cum_oil], orientation="h",
                marker_color="#2563eb", name=yacimiento,
                text=[f"{cum_oil:,.0f} m³ ({cum_oil*M3_TO_BBL/1000:,.0f} k bbl)"],
                textposition="auto",
            ))
            for label, val, color in [
                ("P10", p10, "#dc2626"),
                ("P50 (mediana)", p50, "#ea580c"),
                ("P90", p90, "#16a34a"),
            ]:
                bench.add_vline(
                    x=val, line_dash="dash", line_color=color,
                    annotation_text=f"{label}: {val:,.0f}",
                    annotation_position="top",
                )
            bench.update_layout(
                height=180, margin=dict(l=0, r=20, t=40, b=20),
                xaxis_title=f"Petróleo acumulado {year} (m³)",
                showlegend=False,
            )
            st.plotly_chart(bench, use_container_width=True)

            basin_sorted = basin_fields.sort_values("cum_oil", ascending=False).reset_index(drop=True)
            basin_sorted.index = basin_sorted.index + 1
            try:
                rank_pos = int(basin_sorted[basin_sorted["areayacimiento"] == yacimiento].index[0])
            except (IndexError, KeyError):
                rank_pos = None

            if rank_pos:
                st.markdown(
                    f"**Ranking en cuenca {basin}: #{rank_pos} de {len(basin_sorted):,} yacimientos**"
                )
                lo = max(1, rank_pos - 5)
                hi = min(len(basin_sorted), rank_pos + 5)
                neighbors = basin_sorted.loc[lo:hi].copy()
                neighbors_disp = neighbors.rename(columns={
                    "areayacimiento": "Yacimiento",
                    "wells": "Pozos",
                    "cum_oil": "Petróleo (m³)",
                })[["Yacimiento", "Pozos", "Petróleo (m³)"]]
                neighbors_disp["Petróleo (m³)"] = neighbors_disp["Petróleo (m³)"].apply(
                    lambda v: f"{v:,.0f}" if pd.notna(v) else "—"
                )
                neighbors_disp["Pozos"] = neighbors_disp["Pozos"].apply(
                    lambda v: f"{int(v):,}" if pd.notna(v) else "—"
                )

                def _row_style(row):
                    return [
                        "background-color: #fde68a" if row.name == rank_pos else ""
                        for _ in row
                    ]

                st.dataframe(
                    neighbors_disp.style.apply(_row_style, axis=1),
                    use_container_width=True,
                    height=min(40 + 35 * len(neighbors_disp), 450),
                )

    # ---------- Drill-down: individual wells ----------
    with st.expander(f"🔎 Pozos individuales ({n_wells_total})", expanded=False):
        if wells_df.empty:
            st.info("Sin datos de pozos para este yacimiento en el año seleccionado.")
        else:
            w = wells_df.copy()
            w["bpd_ytd"] = w["cum_oil"] * M3_TO_BBL / max(1, n_months * 30.44)
            w = w.sort_values("cum_oil", ascending=False).reset_index(drop=True)
            w.insert(0, "#", w.index + 1)
            w_disp = w.rename(columns={
                "sigla": "Pozo",
                "formacion": "Formación",
                "tipo_de_recurso": "Tipo",
                "cum_oil": "Petróleo (m³)",
                "cum_gas": "Gas (miles m³)",
                "peak_oil": "Pico mes (m³)",
                "months": "Meses",
                "bpd_ytd": "BPD YTD",
            })[["#", "Pozo", "Formación", "Tipo", "Petróleo (m³)",
                 "BPD YTD", "Gas (miles m³)", "Pico mes (m³)", "Meses"]]
            for c in ["Petróleo (m³)", "Gas (miles m³)", "Pico mes (m³)"]:
                w_disp[c] = w_disp[c].apply(lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
            w_disp["BPD YTD"] = w_disp["BPD YTD"].apply(
                lambda v: f"{v:,.0f}" if pd.notna(v) else "—"
            )
            w_disp["Meses"] = w_disp["Meses"].apply(
                lambda v: f"{int(v):,}" if pd.notna(v) else "—"
            )
            st.dataframe(w_disp, use_container_width=True, hide_index=True, height=360)

    # ---------- Export ----------
    st.subheader("Exportar")
    export = monthly.copy()
    export.insert(0, "yacimiento", yacimiento)
    export.insert(0, "empresa", operator)
    export["bpd_calendar"] = export.apply(
        lambda r: bpd_calendar(r["oil"], r["days_in_month"]) if r["days_in_month"] else 0.0,
        axis=1,
    )
    export["bpd_ontime"] = export.apply(
        lambda r: bpd_ontime(r["oil"], r["tef"]), axis=1
    )
    export["decline_rate_monthly"] = D
    export["water_cut_pct"] = water_cut
    export["gor_m3_m3"] = gor
    csv_bytes = export.to_csv(index=False).encode("utf-8")
    safe_name = str(yacimiento).replace("/", "_").replace(" ", "_")
    st.download_button(
        "📥 Descargar reporte del yacimiento (CSV)",
        data=csv_bytes,
        file_name=f"{safe_name}_{year}_report.csv",
        mime="text/csv",
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
screen = st.session_state.get("screen", "operator")
try:
    if screen == "fields":
        screen_fields()
    elif screen == "field_detail":
        screen_field_detail()
    else:
        screen_operator()
except Exception as exc:  # surface errors loudly
    st.error(f"Error inesperado: {exc}")
    st.exception(exc)
