"""
Argentina Well Intelligence Dashboard
=====================================
Live connection to Argentina's Secretaría de Energía CKAN API.
Uses SQL aggregation endpoint for fast company/basin rollups and
filter-scoped queries for per-well detail.
"""

import json
import os
import time
import urllib3
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# TLS strategy
# ---------------------------------------------------------------------------
# Priority:
#   1. `SECRETARIA_CA_BUNDLE` env var → path to a .pem bundle (preferred).
#   2. `REQUESTS_CA_BUNDLE` env var → standard requests convention.
#   3. `SECRETARIA_VERIFY_SSL=1` → use system defaults (requires valid certs).
#   4. Otherwise → verify=False (fallback; Anaconda macOS bundle often lacks
#      the gov CA chain). Warning suppressed, TLS still encrypts but no MITM
#      protection. Acceptable for public read-only data.
_CA_BUNDLE = os.getenv("SECRETARIA_CA_BUNDLE") or os.getenv("REQUESTS_CA_BUNDLE")
if _CA_BUNDLE and os.path.exists(_CA_BUNDLE):
    VERIFY_SSL: bool | str = _CA_BUNDLE
    TLS_MODE = f"CA bundle: {os.path.basename(_CA_BUNDLE)}"
elif os.getenv("SECRETARIA_VERIFY_SSL") in ("1", "true", "yes"):
    VERIFY_SSL = True
    TLS_MODE = "System CA store"
else:
    VERIFY_SSL = False
    TLS_MODE = "Unverified (fallback)"
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------------------
# Page config & CSS
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Argentina Well Intelligence Dashboard",
    page_icon=":material/oil_barrel:",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
    /* ───────────────────────────────────────────────
       Palette (warm neutrals)
       cream-50  #F5EFE6  main bg
       cream-100 #EDE5D8  secondary bg / cards
       taupe-200 #D8CDBC  borders / muted
       taupe-400 #B9A999  muted text
       brown-500 #9A8577  accent-light
       brown-600 #7A5D4B  primary accent
       brown-700 #5C3E29  headings
       espresso  #2E1E12  primary text / dark surfaces
       ─────────────────────────────────────────────── */

    /* ── Sidebar (dark espresso with cream text) ── */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #2E1E12 0%, #3D2A1C 100%);
        border-right: 1px solid #5C3E29;
    }
    [data-testid="stSidebar"] *,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] div {
        color: #EDE5D8 !important;
    }
    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3,
    [data-testid="stSidebar"] h4 {
        color: #F5EFE6 !important;
    }
    [data-testid="stSidebar"] hr {
        border-color: #5C3E29 !important;
    }
    /* Sidebar inputs: readable on dark */
    [data-testid="stSidebar"] [data-baseweb="select"] > div,
    [data-testid="stSidebar"] input,
    [data-testid="stSidebar"] textarea {
        background-color: #3D2A1C !important;
        color: #F5EFE6 !important;
        border-color: #5C3E29 !important;
    }

    /* ── Main area headings (warm dark brown) ── */
    .stApp h1, .stApp h2, .stApp h3, .stApp h4 { color: #2E1E12; }

    /* ── Cards (basin / callouts) ── */
    .basin-card {
        background: #FFFFFF;
        border-radius: 14px;
        padding: 1.1rem 1.3rem;
        margin-bottom: .6rem;
        border: 1px solid #EDE5D8;
        border-left: 4px solid #7A5D4B;
        box-shadow: 0 2px 6px rgba(46, 30, 18, 0.06);
        transition: box-shadow .15s ease, transform .15s ease;
    }
    .basin-card:hover {
        box-shadow: 0 4px 14px rgba(46, 30, 18, 0.10);
        transform: translateY(-1px);
    }
    .basin-card h4 { margin: 0 0 .35rem 0; color: #2E1E12; }
    .basin-card .meta { color: #7A5D4B; font-size: .9rem; }

    /* ── Status pills ── */
    .pill-green  { background:#4F7A4A; color:#F5EFE6; padding:2px 10px; border-radius:20px; font-size:.8rem; font-weight:600; }
    .pill-orange { background:#B8663A; color:#F5EFE6; padding:2px 10px; border-radius:20px; font-size:.8rem; font-weight:600; }
    .pill-red    { background:#8B3A2E; color:#F5EFE6; padding:2px 10px; border-radius:20px; font-size:.8rem; font-weight:600; }

    /* ── Buttons ── */
    .stButton > button {
        border-radius: 10px !important;
        border: 1px solid #D8CDBC !important;
        background-color: #FFFFFF !important;
        color: #2E1E12 !important;
        transition: all .15s ease;
    }
    .stButton > button:hover {
        background-color: #EDE5D8 !important;
        border-color: #B9A999 !important;
    }
    .stButton > button[kind="primary"] {
        background-color: #7A5D4B !important;
        border-color: #7A5D4B !important;
        color: #F5EFE6 !important;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: #5C3E29 !important;
        border-color: #5C3E29 !important;
    }

    /* Sidebar buttons (inverted) */
    [data-testid="stSidebar"] .stButton > button {
        background-color: #5C3E29 !important;
        color: #F5EFE6 !important;
        border-color: #7A5D4B !important;
    }
    [data-testid="stSidebar"] .stButton > button:hover {
        background-color: #7A5D4B !important;
    }

    /* ── Progress bars ── */
    [data-testid="stProgressBar"] > div > div {
        background-color: #7A5D4B !important;
    }

    /* ── Metrics ── */
    [data-testid="stMetricLabel"] { color: #7A5D4B !important; font-weight: 500; }
    [data-testid="stMetricValue"] { color: #2E1E12 !important; }
    [data-testid="stMetric"] {
        background: #FFFFFF;
        border-radius: 12px;
        padding: .8rem 1rem;
        border: 1px solid #EDE5D8;
    }

    /* ── Tabs ── */
    .stTabs [data-baseweb="tab-list"] { gap: 6px; }
    .stTabs [data-baseweb="tab"] {
        background-color: transparent;
        border-radius: 10px 10px 0 0;
        color: #7A5D4B;
    }
    .stTabs [aria-selected="true"] {
        background-color: #EDE5D8 !important;
        color: #2E1E12 !important;
    }

    /* ── DataFrames / tables ── */
    [data-testid="stDataFrame"] {
        border-radius: 12px;
        overflow: hidden;
        border: 1px solid #EDE5D8;
    }

    /* ── Expander ── */
    .streamlit-expanderHeader, [data-testid="stExpander"] summary {
        background-color: #FFFFFF !important;
        border-radius: 10px !important;
        border: 1px solid #EDE5D8 !important;
    }

    /* ── Layout ── */
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1400px; }
    .muted { color: #B9A999; font-size: .9rem; }

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
    # Años históricos importables via CSV local (no tienen resource_id de API porque
    # el bulk-fetch no los descarga; load_year_from_csv los acepta igual).
    2017: "local-csv-2017",
    2016: "local-csv-2016",
    2015: "local-csv-2015",
    2014: "local-csv-2014",
    2013: "local-csv-2013",
    2012: "local-csv-2012",
    2011: "local-csv-2011",
}
# "Capítulo IV — Pozos" (padrón de pozos — well metadata, drilling info, coords)
PADRON_RESOURCE = "cb5c0f04-7835-45cd-b982-3e25ca7d7751"

# "Año vivo" — el único que se actualiza mensualmente vía API. Los años
# anteriores son históricos estáticos (no cambian) y se cargan por CSV.
LIVE_YEAR = 2026

HTTP_TIMEOUT = 90
# VERIFY_SSL is resolved above from env (SECRETARIA_CA_BUNDLE / REQUESTS_CA_BUNDLE
# / SECRETARIA_VERIFY_SSL) with a safe fallback for local dev on Anaconda macOS.

# Browser-like User-Agent — some corporate firewalls (Fortinet/WAFs) and CDNs
# reject bare python-requests UAs even for public APIs.
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
}

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
# Local disk cache for bulk-fetched year data
# ---------------------------------------------------------------------------
# The SQL endpoint (/api/3/action/datastore_search_sql) was disabled by the
# publisher in April 2026. We now fetch the full yearly resource via the
# REST datastore_search endpoint (paginated, rate-limited to 5 req/s), cache
# it to disk as parquet, and do all aggregations locally in pandas.
CACHE_DIR = Path.home() / ".macro_pm_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
MANIFEST_PATH = CACHE_DIR / "manifest.json"

PAGE_SIZE = 32000          # CKAN datastore_search default hard cap
RATE_LIMIT_SLEEP = 0.22    # ~4.5 req/s, under the 5 req/s policy


def _year_cache_path(year: int) -> Path:
    # pickle+gzip en lugar de parquet: evita dependencia de pyarrow (que tiene
    # binary-compatibility issues en algunos Anaconda). Es ~3x más grande que
    # parquet pero sigue siendo mucho más chico que el CSV original.
    return CACHE_DIR / f"{year}.pkl.gz"


def _read_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {}
    try:
        return json.loads(MANIFEST_PATH.read_text())
    except Exception:
        return {}


def _write_manifest(m: dict) -> None:
    try:
        MANIFEST_PATH.write_text(json.dumps(m, indent=2, default=str))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
_RETRY_BACKOFF = (0.5, 1.5, 4.0)  # seconds before attempts 2, 3, 4
_QUERY_LOG_CAP = 50  # keep last N entries in session state


def _log_query(sql: str, elapsed: float, rows: int, error: str | None) -> None:
    """Append a query telemetry row to session state (capped)."""
    log = st.session_state.setdefault("_query_log", [])
    log.append({
        "ts": datetime.now().strftime("%H:%M:%S"),
        "ms": int(elapsed * 1000),
        "rows": rows,
        "error": (error[:120] if error else ""),
        "sql_head": " ".join(sql.split())[:140],
    })
    if len(log) > _QUERY_LOG_CAP:
        del log[: len(log) - _QUERY_LOG_CAP]


def _fetch_page(resource_id: str, offset: int, limit: int, timeout: int = HTTP_TIMEOUT) -> dict | None:
    """Fetch a single page from datastore_search with retries. Returns parsed JSON result dict."""
    last_err: str | None = None
    for attempt in range(len(_RETRY_BACKOFF) + 1):
        try:
            r = requests.get(
                API_SEARCH,
                params={"resource_id": resource_id, "offset": offset, "limit": limit},
                timeout=timeout, verify=VERIFY_SSL, headers=HTTP_HEADERS,
            )
            if 500 <= r.status_code < 600:
                last_err = f"HTTP {r.status_code}"
                if attempt < len(_RETRY_BACKOFF):
                    time.sleep(_RETRY_BACKOFF[attempt]); continue
                break
            if r.status_code == 403:
                body = r.text.lower() if r.text else ""
                if "fortinet" in body or "webfilter" in body or "blocked" in body:
                    last_err = "HTTP 403 — bloqueado por firewall corporativo."
                else:
                    last_err = "HTTP 403 — rate-limit (5 req/s) o IP baneada 24hs. Esperá o cambiá de red."
                break
            r.raise_for_status()
            data = r.json()
            if not data.get("success"):
                last_err = f"API: {str(data.get('error', ''))[:300]}"
                break
            return data["result"]
        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = f"Network: {e}"
            if attempt < len(_RETRY_BACKOFF):
                time.sleep(_RETRY_BACKOFF[attempt]); continue
            break
        except requests.exceptions.RequestException as e:
            last_err = f"HTTP: {e}"
            break
        except ValueError as e:
            last_err = f"Parse: {e}"
            break
    st.session_state["_last_api_error"] = last_err
    return None


def fetch_resource_paginated(
    resource_id: str,
    progress_cb=None,
) -> tuple[pd.DataFrame | None, int]:
    """Fetch an entire resource by paginating datastore_search.

    Respects the 5 req/s rate limit via RATE_LIMIT_SLEEP between pages.
    Returns (DataFrame, expected_total). The DataFrame may be partial if a page
    repeatedly failed even after extended retries — the caller must compare
    len(df) vs expected_total to decide whether to persist.
    """
    t0 = time.perf_counter()
    first = _fetch_page(resource_id, offset=0, limit=PAGE_SIZE)
    if first is None:
        _log_query(f"bulk {resource_id}", time.perf_counter() - t0, 0,
                   st.session_state.get("_last_api_error"))
        return None, 0
    total = int(first.get("total") or 0)
    records = list(first.get("records") or [])
    if progress_cb:
        progress_cb(len(records), total)

    # Outer retries: if a page fails after its inner retries (4 attempts),
    # pause longer and try again instead of silently giving up.
    OUTER_RETRY_SLEEPS = (15.0, 45.0, 90.0)  # 3 extra outer attempts

    offset = len(records)
    while offset < total:
        time.sleep(RATE_LIMIT_SLEEP)
        page = _fetch_page(resource_id, offset=offset, limit=PAGE_SIZE)
        if page is None:
            # Inner retries exhausted — try a few outer retries with long pauses
            recovered = False
            for outer_sleep in OUTER_RETRY_SLEEPS:
                time.sleep(outer_sleep)
                page = _fetch_page(resource_id, offset=offset, limit=PAGE_SIZE)
                if page is not None:
                    recovered = True
                    break
            if not recovered:
                # Definitive failure — abort and let caller see partial
                break
        recs = page.get("records") or []
        if not recs:
            break
        records.extend(recs)
        offset += len(recs)
        if progress_cb:
            progress_cb(len(records), total)

    df = pd.DataFrame(records)
    _log_query(f"bulk {resource_id}", time.perf_counter() - t0, len(df), None)
    return df, total


# Columns we expect to coerce to numeric in the production resource
_NUMERIC_COLS = [
    "anio", "mes", "idpozo", "prod_pet", "prod_gas", "prod_agua",
    "iny_agua", "iny_gas", "iny_co2", "iny_otro", "tef", "vida_util",
    "profundidad", "coordenadax", "coordenaday", "idusuario",
]


def _postprocess_year_df(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce types, strip whitespace on text columns, derive helpers."""
    for c in _NUMERIC_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    # Strip trailing/leading whitespace on likely-text columns
    for c in ("empresa", "areayacimiento", "cuenca", "provincia",
              "tipo_de_recurso", "formacion", "sigla", "tipopozo"):
        if c in df.columns:
            df[c] = df[c].astype("string").str.strip()
    if "anio" in df.columns and "mes" in df.columns:
        df["yyyymm"] = (df["anio"].fillna(0).astype(int) * 100
                        + df["mes"].fillna(0).astype(int))
    return df


def load_year(year: int, force: bool = False, progress_cb=None) -> pd.DataFrame | None:
    """Return the full DataFrame for a year, using disk cache when available.

    If `force=True`, re-download even if the parquet exists on disk.
    Si el resource_id es placeholder (local-csv-YYYY), nunca consulta la API —
    ese año solo se carga vía CSV local.
    """
    rid = RESOURCES_BY_YEAR.get(year)
    if rid is None:
        return None

    path = _year_cache_path(year)
    if path.exists() and not force:
        try:
            return pd.read_pickle(path, compression="gzip")
        except Exception:
            pass  # corrupt cache — re-download

    # Año sin resource_id real (histórico estático) — solo vía CSV local
    if str(rid).startswith("local-csv-"):
        st.session_state["_last_api_error"] = (
            f"El año {year} no se descarga vía API (es histórico). "
            f"Usá 'Cargar desde CSV local' para importarlo."
        )
        return None

    df, expected_total = fetch_resource_paginated(rid, progress_cb=progress_cb)
    if df is None or df.empty:
        return df

    df = _postprocess_year_df(df)

    # Detectar fetch parcial: si la API reportó N filas y bajamos < 99% de N,
    # NO persistir como caché bueno (causaría que la app crea estar al día con
    # datos truncados, como pasó con 2026: 113k de 241k).
    is_partial = expected_total > 0 and len(df) < int(expected_total * 0.99)
    if is_partial:
        st.session_state["_last_api_error"] = (
            f"⚠️ Descarga incompleta: {len(df):,} de {expected_total:,} filas "
            f"({len(df)*100//max(expected_total,1)}%). Se cayeron páginas. "
            f"Volvé a apretar 'Actualizar datos {year}' para reintentar."
        )
        # NO escribir el pickle si ya existe uno bueno; si no existe, escribir
        # pero marcar partial=True en el manifest para que la próxima corrida
        # intente completar.
        if not path.exists():
            try:
                df.to_pickle(path, compression="gzip")
            except Exception as e:
                st.session_state["_last_api_error"] = f"No pude escribir caché: {e}"
                return df
            mani = _read_manifest()
            mani[str(year)] = {
                "downloaded_at": datetime.now().isoformat(timespec="seconds"),
                "rows": int(len(df)),
                "expected_rows": int(expected_total),
                "partial": True,
                "last_yyyymm": int(df["yyyymm"].max()) if "yyyymm" in df.columns and len(df) else None,
            }
            _write_manifest(mani)
        return df

    # Persist to disk (full fetch)
    try:
        df.to_pickle(path, compression="gzip")
    except Exception as e:
        st.session_state["_last_api_error"] = f"No pude escribir caché: {e}"
        return df  # devolvemos igual para que la sesión actual funcione

    # Update manifest
    mani = _read_manifest()
    mani[str(year)] = {
        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
        "rows": int(len(df)),
        "expected_rows": int(expected_total) if expected_total else int(len(df)),
        "partial": False,
        "last_yyyymm": int(df["yyyymm"].max()) if "yyyymm" in df.columns and len(df) else None,
    }
    _write_manifest(mani)

    return df


@st.cache_data(ttl=None, show_spinner=False)
def _cached_year(year: int, cache_token: str) -> pd.DataFrame | None:
    """In-memory cache of load_year(). `cache_token` is bumped by the Refresh
    button to invalidate without touching disk (disk is handled by load_year)."""
    return load_year(year, force=False)


def year_df(year: int) -> pd.DataFrame | None:
    """Primary accessor — use this in data functions."""
    tok = st.session_state.get("_cache_token", "v1")
    return _cached_year(year, tok)


# ---------------------------------------------------------------------------
# Fallback: cargar CSV local (cuando la API del gobierno está caída)
# ---------------------------------------------------------------------------
_CSV_SCAN_DIRS = [
    Path("/Users/Manuel/Desktop/Macro PM"),
    Path("/Users/Manuel/Desktop/Macro PM/CSVs"),
    Path("/Users/Manuel/Desktop/Macro PM/csv_downloads"),
    Path("/Users/Manuel/Desktop/Macro PM/csv_raw"),
    Path.home() / "Downloads",
]


def scan_local_csvs() -> dict[int, Path]:
    """Escanea carpetas conocidas buscando CSVs de producción; devuelve {año: path}."""
    import re
    found: dict[int, Path] = {}
    for d in _CSV_SCAN_DIRS:
        if not d.exists():
            continue
        for p in d.glob("*.csv"):
            name = p.name.lower()
            if "pozo" not in name and "petr" not in name and "gas" not in name:
                continue
            m = re.search(r"(20\d{2})", p.name)
            if not m:
                continue
            y = int(m.group(1))
            if y not in RESOURCES_BY_YEAR:
                continue
            # Preferir el archivo más grande (la versión completa) si hay duplicados
            if y not in found or p.stat().st_size > found[y].stat().st_size:
                found[y] = p
    return dict(sorted(found.items(), reverse=True))


def load_year_from_csv(year: int, csv_path: Path, progress_cb=None) -> pd.DataFrame | None:
    """Lee un CSV local de producción, lo post-procesa y guarda como parquet."""
    if not csv_path.exists():
        st.session_state["_last_api_error"] = f"CSV no existe: {csv_path}"
        return None
    try:
        if progress_cb:
            progress_cb(0, None)
        # encoding utf-8-sig para sacar BOM; low_memory=False para tipos consistentes
        df = pd.read_csv(csv_path, encoding="utf-8-sig", low_memory=False)
    except Exception as e:
        st.session_state["_last_api_error"] = f"Error leyendo CSV: {e}"
        return None

    df = _postprocess_year_df(df)

    # Filtrar al año pedido (por si el CSV trae varios)
    if "anio" in df.columns:
        df = df[df["anio"] == year].copy()
        if df.empty:
            st.session_state["_last_api_error"] = (
                f"El CSV no contiene filas con anio={year}"
            )
            return None

    # Persist
    path = _year_cache_path(year)
    try:
        df.to_pickle(path, compression="gzip")
    except Exception as e:
        st.session_state["_last_api_error"] = f"No pude escribir caché: {e}"
        return df

    mani = _read_manifest()
    mani[str(year)] = {
        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
        "rows": int(len(df)),
        "last_yyyymm": int(df["yyyymm"].max()) if "yyyymm" in df.columns and len(df) else None,
        "source": f"csv:{csv_path.name}",
    }
    _write_manifest(mani)

    if progress_cb:
        progress_cb(len(df), len(df))
    return df


# ---------------------------------------------------------------------------
# Legacy SQL shim — the SQL endpoint is dead (disabled by publisher in Apr 2026).
# Every remaining call raises so we catch missed callsites in testing. All
# aggregations now run locally on the DataFrame returned by `year_df(year)`.
# ---------------------------------------------------------------------------
def sql_query(sql: str, timeout: int = HTTP_TIMEOUT) -> pd.DataFrame | None:
    st.session_state["_last_api_error"] = (
        "sql_query() fue invocado pero el endpoint SQL fue deshabilitado. "
        "Esta función ya no se usa — re-chequear callsites."
    )
    _log_query(sql, 0.0, 0, "SQL endpoint disabled")
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
# Cached data accessors — all operate on the in-memory DataFrame from load_year()
# ---------------------------------------------------------------------------
def _empty_df(cols: list[str]) -> pd.DataFrame:
    return pd.DataFrame(columns=cols)


def _filter_valid_yac(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows with missing/empty areayacimiento."""
    if df is None or df.empty:
        return df
    yac = df["areayacimiento"].fillna("").astype(str).str.strip()
    return df[yac != ""]


def get_latest_data_date(year: int) -> tuple[int, int] | None:
    """Última fecha (anio, mes) con registros en el año dado."""
    df = year_df(year)
    if df is None or df.empty or "yyyymm" not in df.columns:
        return None
    ym = int(df["yyyymm"].max())
    if ym <= 0:
        return None
    return (ym // 100, ym % 100)


def get_latest_year_with_data() -> int:
    """El año más reciente (de RESOURCES_BY_YEAR) con datos publicados."""
    for y in sorted(RESOURCES_BY_YEAR.keys(), reverse=True):
        last = get_latest_data_date(y)
        if last is not None:
            return y
    # Fallback defensivo
    return max(RESOURCES_BY_YEAR.keys())


@st.cache_data(ttl=3600 * 24, show_spinner=False)
def get_field_first_production(operator: str, yacimiento: str) -> tuple[int, int] | None:
    """Primer (anio, mes) con petróleo o gas > 0 para este operador×yacimiento.

    Recorre sólo los años ya cacheados en disco (para no forzar descargas pesadas).
    """
    op = str(operator).strip()
    yac = str(yacimiento).strip()
    for y in sorted(RESOURCES_BY_YEAR.keys()):
        # Sólo usar años ya descargados en disco
        if not _year_cache_path(y).exists():
            continue
        df = year_df(y)
        if df is None or df.empty:
            continue
        m = (
            (df["empresa"].fillna("").str.strip() == op)
            & (df["areayacimiento"].fillna("").str.strip() == yac)
            & ((df["prod_pet"].fillna(0) > 0) | (df["prod_gas"].fillna(0) > 0))
        )
        sub = df.loc[m, "yyyymm"]
        if sub.empty:
            continue
        ym = int(sub.min())
        if ym > 0:
            return (ym // 100, ym % 100)
    return None


@st.cache_data(ttl=3600 * 6, show_spinner=False)
def get_all_yacimientos(year: int) -> pd.DataFrame:
    """Listado de yacimientos con su operador dominante para el año dado."""
    df = year_df(year)
    if df is None or df.empty:
        return pd.DataFrame()
    df = _filter_valid_yac(df)
    out = (
        df.groupby(["areayacimiento", "empresa", "cuenca", "provincia"], dropna=False)
        .agg(cum_oil=("prod_pet", "sum"), wells=("idpozo", "nunique"))
        .reset_index()
    )
    return out.sort_values(
        ["areayacimiento", "cum_oil"], ascending=[True, False]
    ).reset_index(drop=True)


@st.cache_data(ttl=3600 * 24, show_spinner="Cargando empresas operadoras...")
def get_all_companies(year: int) -> list[str]:
    df = year_df(year)
    if df is None or df.empty or "empresa" not in df.columns:
        return []
    vals = df["empresa"].dropna().astype(str).str.strip().unique().tolist()
    return sorted([e for e in vals if e])


@st.cache_data(ttl=3600, show_spinner=False)
def get_company_summary(company: str, year: int) -> dict:
    df = year_df(year)
    if df is None or df.empty:
        return {}
    sub = df[df["empresa"].fillna("").str.strip() == str(company).strip()]
    if sub.empty:
        return {}
    return {
        "wells": sub["idpozo"].nunique(),
        "basins": sub["cuenca"].nunique(),
        "provinces": sub["provincia"].nunique(),
        "total_oil": float(sub["prod_pet"].sum()),
        "total_gas": float(sub["prod_gas"].sum()),
        "total_water": float(sub["prod_agua"].sum()),
    }


@st.cache_data(ttl=3600, show_spinner=False)
def get_company_basin_rollup(company: str, year: int) -> pd.DataFrame:
    df = year_df(year)
    if df is None or df.empty:
        return pd.DataFrame()
    sub = df[df["empresa"].fillna("").str.strip() == str(company).strip()]
    if sub.empty:
        return pd.DataFrame()
    out = (
        sub.groupby("cuenca", dropna=False)
        .agg(
            wells=("idpozo", "nunique"),
            oil=("prod_pet", "sum"),
            gas=("prod_gas", "sum"),
            water=("prod_agua", "sum"),
        )
        .reset_index()
        .sort_values("oil", ascending=False, na_position="last")
        .reset_index(drop=True)
    )
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def get_company_wells(
    company: str, year: int, basin: str | None = None,
    tipo_recurso: str | None = None, provincia: str | None = None,
    yacimiento: str | None = None,
) -> pd.DataFrame:
    df = year_df(year)
    if df is None or df.empty:
        return pd.DataFrame()
    m = df["empresa"].fillna("").str.strip() == str(company).strip()
    if basin:
        m &= df["cuenca"].fillna("").str.strip() == str(basin).strip()
    if tipo_recurso:
        m &= df["tipo_de_recurso"].fillna("").str.strip() == str(tipo_recurso).strip()
    if provincia:
        m &= df["provincia"].fillna("").str.strip() == str(provincia).strip()
    if yacimiento:
        m &= df["areayacimiento"].fillna("").str.strip() == str(yacimiento).strip()
    sub = df[m]
    if sub.empty:
        return pd.DataFrame()
    group_cols = ["idpozo", "sigla", "cuenca", "provincia", "tipo_de_recurso",
                  "areayacimiento", "formacion"]
    out = (
        sub.groupby(group_cols, dropna=False)
        .agg(
            cum_oil=("prod_pet", "sum"),
            cum_gas=("prod_gas", "sum"),
            cum_water=("prod_agua", "sum"),
            peak_oil=("prod_pet", "max"),
            months=("prod_pet", "size"),
            total_tef=("tef", "sum"),
        )
        .reset_index()
        .sort_values("cum_oil", ascending=False, na_position="last")
        .reset_index(drop=True)
    )
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def get_company_fields(
    company: str, year: int, basin: str | None = None,
    tipo_recurso: str | None = None, provincia: str | None = None,
) -> pd.DataFrame:
    """Yacimiento-level rollup for a company (optionally scoped to basin/tipo/province)."""
    df = year_df(year)
    if df is None or df.empty:
        return pd.DataFrame()
    df = _filter_valid_yac(df)
    m = df["empresa"].fillna("").str.strip() == str(company).strip()
    if basin:
        m &= df["cuenca"].fillna("").str.strip() == str(basin).strip()
    if tipo_recurso:
        m &= df["tipo_de_recurso"].fillna("").str.strip() == str(tipo_recurso).strip()
    if provincia:
        m &= df["provincia"].fillna("").str.strip() == str(provincia).strip()
    sub = df[m]
    if sub.empty:
        return pd.DataFrame()

    # Totals per yacimiento
    df_tot = (
        sub.groupby(["areayacimiento", "cuenca", "provincia"], dropna=False)
        .agg(
            wells=("idpozo", "nunique"),
            cum_oil=("prod_pet", "sum"),
            cum_gas=("prod_gas", "sum"),
            cum_water=("prod_agua", "sum"),
            total_tef=("tef", "sum"),
        )
        .reset_index()
    )

    # Monthly rollup per (yacimiento, anio, mes)
    df_m = (
        sub.groupby(["areayacimiento", "anio", "mes"], dropna=False)
        .agg(
            oil_month=("prod_pet", "sum"),
            gas_month=("prod_gas", "sum"),
            tef_month=("tef", "sum"),
            wells_month=("idpozo", "nunique"),
        )
        .reset_index()
    )
    df_m["yyyymm"] = df_m["anio"].fillna(0).astype(int) * 100 + df_m["mes"].fillna(0).astype(int)

    df_peak = (
        df_m.groupby("areayacimiento", dropna=False)
        .agg(
            peak_oil_month=("oil_month", "max"),
            peak_gas_month=("gas_month", "max"),
            peak_concurrent_wells=("wells_month", "max"),
            n_months=("yyyymm", "nunique"),
            last_yyyymm=("yyyymm", "max"),
        )
        .reset_index()
    )

    idx = df_m.groupby("areayacimiento")["yyyymm"].idxmax()
    df_last = df_m.loc[idx, ["areayacimiento", "anio", "mes",
                             "oil_month", "gas_month", "tef_month"]].rename(
        columns={"oil_month": "oil_last", "gas_month": "gas_last", "tef_month": "tef_last",
                 "anio": "last_anio", "mes": "last_mes"}
    )

    out = df_tot.merge(df_peak, on="areayacimiento", how="left")
    out = out.merge(df_last, on="areayacimiento", how="left")
    out = out.sort_values("cum_oil", ascending=False, na_position="last").reset_index(drop=True)
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def get_field_timeseries(
    company: str, yacimiento: str, years: tuple[int, ...],
) -> pd.DataFrame:
    """Consolidated monthly production for a yacimiento (summed across its wells)."""
    c = str(company).strip()
    y = str(yacimiento).strip()
    parts = []
    for year in years:
        # Sólo años ya cacheados
        if not _year_cache_path(year).exists():
            continue
        df = year_df(year)
        if df is None or df.empty:
            continue
        sub = df[
            (df["empresa"].fillna("").str.strip() == c)
            & (df["areayacimiento"].fillna("").str.strip() == y)
        ]
        if sub.empty:
            continue
        g = (
            sub.groupby(["anio", "mes"], dropna=False)
            .agg(
                oil=("prod_pet", "sum"),
                gas=("prod_gas", "sum"),
                water=("prod_agua", "sum"),
                tef=("tef", "sum"),
                wells=("idpozo", "nunique"),
            )
            .reset_index()
        )
        parts.append(g)
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df["fecha"] = pd.to_datetime(
        df["anio"].astype("Int64").astype(str) + "-" +
        df["mes"].astype("Int64").astype(str).str.zfill(2) + "-01",
        errors="coerce",
    )
    df["days_in_month"] = df["fecha"].dt.days_in_month
    return df.sort_values("fecha").reset_index(drop=True)


@st.cache_data(ttl=3600, show_spinner=False)
def get_top_yacimientos(year: int, top: int = 10) -> pd.DataFrame:
    """Top yacimientos de Argentina por producción YTD — para el panel general."""
    df = year_df(year)
    if df is None or df.empty:
        return pd.DataFrame()
    df = _filter_valid_yac(df)

    totals = (
        df.groupby(["areayacimiento", "empresa", "cuenca", "provincia"], dropna=False)
        .agg(
            wells=("idpozo", "nunique"),
            cum_oil=("prod_pet", "sum"),
            cum_gas=("prod_gas", "sum"),
            total_tef=("tef", "sum"),
            n_months=("yyyymm", "nunique"),
            last_yyyymm=("yyyymm", "max"),
        )
        .reset_index()
        .sort_values("cum_oil", ascending=False, na_position="last")
        .head(int(top))
        .reset_index(drop=True)
    )
    if totals.empty:
        return totals

    yac_list = totals["areayacimiento"].dropna().unique().tolist()
    if yac_list:
        sub = df[df["areayacimiento"].isin(yac_list)]
        df_m = (
            sub.groupby(["areayacimiento", "empresa", "anio", "mes"], dropna=False)
            .agg(
                oil_m=("prod_pet", "sum"),
                tef_m=("tef", "sum"),
                wells_m=("idpozo", "nunique"),
            )
            .reset_index()
        )
        df_m["yyyymm"] = df_m["anio"].fillna(0).astype(int) * 100 + df_m["mes"].fillna(0).astype(int)
        if not df_m.empty:
            idx = df_m.groupby(["areayacimiento", "empresa"])["yyyymm"].idxmax()
            df_last = df_m.loc[idx, ["areayacimiento", "empresa", "anio", "mes",
                                     "oil_m", "tef_m", "wells_m"]].rename(
                columns={"oil_m": "oil_last", "tef_m": "tef_last", "wells_m": "wells_last",
                         "anio": "last_anio", "mes": "last_mes"})
            totals = totals.merge(df_last, on=["areayacimiento", "empresa"], how="left")

    return totals


@st.cache_data(ttl=3600, show_spinner=False)
def get_basin_field_totals(basin: str, year: int) -> pd.DataFrame:
    """Per-yacimiento rollup for a basin — includes monthly breakdown so we can
    compute both YTD BPD and last-month BPD for benchmarks."""
    df = year_df(year)
    if df is None or df.empty:
        return pd.DataFrame()
    df = _filter_valid_yac(df)
    sub = df[df["cuenca"].fillna("").str.strip() == str(basin).strip()]
    if sub.empty:
        return pd.DataFrame()
    df_m = (
        sub.groupby(["areayacimiento", "empresa", "anio", "mes"], dropna=False)
        .agg(
            wells_m=("idpozo", "nunique"),
            oil_m=("prod_pet", "sum"),
            gas_m=("prod_gas", "sum"),
        )
        .reset_index()
    )
    df_m["yyyymm"] = df_m["anio"].fillna(0).astype(int) * 100 + df_m["mes"].fillna(0).astype(int)

    # Totals per (yacimiento, empresa)
    totals = (
        df_m.groupby(["areayacimiento", "empresa"], as_index=False)
        .agg(
            wells=("wells_m", "max"),  # usamos el pico concurrente del año
            cum_oil=("oil_m", "sum"),
            cum_gas=("gas_m", "sum"),
            n_months=("yyyymm", "nunique"),
            last_yyyymm=("yyyymm", "max"),
        )
    )

    # Last-month slice
    idx = df_m.groupby(["areayacimiento", "empresa"])["yyyymm"].idxmax()
    last = df_m.loc[idx, ["areayacimiento", "empresa", "oil_m", "wells_m", "anio", "mes"]].rename(
        columns={"oil_m": "oil_last", "wells_m": "wells_last",
                 "anio": "last_anio", "mes": "last_mes"}
    )
    out = totals.merge(last, on=["areayacimiento", "empresa"], how="left")

    # Días del último mes (para BPD calendario del último mes)
    def _dim(row):
        try:
            return pd.Timestamp(int(row["last_anio"]), int(row["last_mes"]), 1).days_in_month
        except Exception:
            return 30.44
    out["last_days"] = out.apply(_dim, axis=1)
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def get_well_timeseries(idpozo: str, years: tuple[int, ...]) -> pd.DataFrame:
    """Fetch monthly time series for one well across one or more years."""
    parts = []
    idp = str(idpozo).strip()
    cols = ["anio", "mes", "prod_pet", "prod_gas", "prod_agua", "tef",
            "empresa", "sigla", "cuenca", "provincia", "tipo_de_recurso",
            "areayacimiento", "formacion", "tipoestado", "tipoextraccion"]
    for y in years:
        if not _year_cache_path(y).exists():
            continue
        df = year_df(y)
        if df is None or df.empty:
            continue
        sub = df[df["idpozo"].astype(str).str.strip() == idp]
        if sub.empty:
            continue
        keep = [c for c in cols if c in sub.columns]
        parts.append(sub[keep].copy())
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df["fecha"] = pd.to_datetime(
        df["anio"].astype("Int64").astype(str) + "-" + df["mes"].astype("Int64").astype(str).str.zfill(2) + "-01",
        errors="coerce",
    )
    return df.sort_values("fecha").reset_index(drop=True)


@st.cache_data(ttl=3600 * 24, show_spinner=False)
def get_well_padron(sigla: str) -> dict:
    # El padrón es un resource aparte y no se bulk-fetchea: stub vacío por ahora.
    return {}


@st.cache_data(ttl=3600, show_spinner=False)
def get_basin_well_totals(basin: str, year: int) -> pd.DataFrame:
    """Cumulative oil per well across the entire basin — for P10/P50/P90 and ranking."""
    df = year_df(year)
    if df is None or df.empty:
        return pd.DataFrame()
    sub = df[df["cuenca"].fillna("").str.strip() == str(basin).strip()]
    if sub.empty:
        return pd.DataFrame()
    out = (
        sub.groupby(["idpozo", "sigla", "empresa"], dropna=False)
        .agg(cum_oil=("prod_pet", "sum"))
        .reset_index()
    )
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def get_basin_operator_leaderboard(basin: str | None, year: int, top: int = 5) -> pd.DataFrame:
    df = year_df(year)
    if df is None or df.empty:
        return pd.DataFrame()
    if basin:
        df = df[df["cuenca"].fillna("").str.strip() == str(basin).strip()]
    if df.empty:
        return pd.DataFrame()
    out = (
        df.groupby("empresa", dropna=False)
        .agg(wells=("idpozo", "nunique"), oil=("prod_pet", "sum"))
        .reset_index()
        .sort_values("oil", ascending=False, na_position="last")
        .head(int(top))
        .reset_index(drop=True)
    )
    return out


# ---------------------------------------------------------------------------
# Computation helpers
# ---------------------------------------------------------------------------
def quality_pill(val: float, p33: float, p66: float) -> str:
    if pd.isna(val):
        return "—"
    if val >= p66:
        return "Alta"
    if val >= p33:
        return "Media"
    return "Baja"


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

EARTH_PALETTE = ["#5C3D2E", "#7B5B47", "#B8A89A", "#D4C5B0", "#2C1810", "#8B6355", "#A07060"]

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
    st.markdown("### Configuración")
    years_available = sorted(RESOURCES_BY_YEAR.keys(), reverse=True)
    year = st.selectbox(
        "Año de producción",
        years_available,
        index=years_available.index(st.session_state["year"]) if st.session_state["year"] in years_available else 0,
        help="Las estadísticas acumulativas son para el año seleccionado (YTD).",
    )
    if year != st.session_state["year"]:
        st.session_state["year"] = year
        # Limpiar TODO el estado de navegación: si no, al cambiar de año se
        # arrastra el operador/yacimiento/pozo/cuenca del año anterior y
        # parece que los datos se "mezclan" entre años.
        st.session_state["selected_operator"] = None
        st.session_state["selected_basin"] = None
        st.session_state["selected_yacimiento"] = None
        st.session_state["selected_well_id"] = None
        st.session_state["selected_well_sigla"] = None
        st.session_state["screen"] = "operator"
        # Invalidar caches in-memory de accessors @st.cache_data
        st.cache_data.clear()
        st.rerun()

    include_prior = st.checkbox(
        "Incluir año previo en detalle",
        value=st.session_state["include_prior_year"],
        help="Extiende la serie temporal al año anterior para una mejor curva y DCA.",
    )
    st.session_state["include_prior_year"] = include_prior

    st.markdown("---")
    # Mostrar estado del caché por año (desde el manifest)
    _mani = _read_manifest()
    _yr_key = str(st.session_state["year"])
    _yr_info = _mani.get(_yr_key)
    if _yr_info:
        _dl = _yr_info.get("downloaded_at", "—")
        _rows = _yr_info.get("rows", 0)
        st.caption(f"Caché local ({_yr_key}): {int(_rows):,} filas · bajado {_dl}")
    else:
        st.caption(f"Caché local ({_yr_key}): sin descarga previa")

    if st.button(
        f"Actualizar datos {LIVE_YEAR}",
        width="stretch",
        help=f"Re-descarga {LIVE_YEAR} (el año vivo) desde datos.energia.gob.ar. "
             f"Los años anteriores son históricos estáticos y no se vuelven a bajar.",
    ):
        _yr = LIVE_YEAR
        _prog = st.progress(0.0, text=f"Descargando {_yr} desde datos.energia.gob.ar…")

        def _cb(done: int, total: int | None) -> None:
            if total and total > 0:
                frac = min(done / total, 1.0)
                _prog.progress(frac, text=f"Descargando {_yr}: {done:,} / {total:,} filas")
            else:
                _prog.progress(0.0, text=f"Descargando {_yr}: {done:,} filas…")

        try:
            df_new = load_year(_yr, force=True, progress_cb=_cb)
            if df_new is None or df_new.empty:
                _prog.empty()
                st.error("No se pudo descargar. Revisá el error en la barra lateral.")
            else:
                _prog.progress(1.0, text=f"Listo: {len(df_new):,} filas guardadas en caché.")
                # Invalidar caché en memoria bumpeando token
                st.session_state["_cache_token"] = datetime.now().isoformat(timespec="seconds")
                st.cache_data.clear()
                st.session_state["_last_refresh"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                st.rerun()
        except Exception as e:
            _prog.empty()
            st.error(f"Falló la descarga: {e}")

    if st.session_state.get("_last_refresh"):
        st.caption(f"Última actualización manual: {st.session_state['_last_refresh']}")

    # --- Fallback: cargar desde CSV local ---
    with st.expander("Cargar desde CSV local (si la API falla)", expanded=False):
        _found = scan_local_csvs()
        if not _found:
            st.caption(
                "No encontré CSVs en: `/Users/Manuel/Desktop/Macro PM`, "
                "`csv_downloads/`, `~/Downloads`. Bajá el CSV desde "
                "datos.energia.gob.ar y dejalo en alguna de esas carpetas."
            )
        else:
            _opts = {f"{y} — {p.name} ({p.stat().st_size/1e6:.0f} MB)": (y, p)
                     for y, p in _found.items()}
            # Mostrar estado de caché al lado de cada CSV — "importado" solo si
            # manifest Y archivo parquet/pickle existen en disco.
            _mani_state = _read_manifest()
            _rows = []
            for _lbl, (_y, _p) in _opts.items():
                _cache_file = _year_cache_path(_y)
                _cached = str(_y) in _mani_state and _cache_file.exists()
                _rows.append(f"- **{_y}** · {_p.name} · {_p.stat().st_size/1e6:.0f} MB · "
                             f"{'ya importado' if _cached else 'pendiente'}")
            st.markdown("\n".join(_rows))

            _pick = st.selectbox("Importar un año específico", list(_opts.keys()), key="csv_pick")
            col_imp1, col_imp2 = st.columns(2)
            with col_imp1:
                _do_one = st.button("Importar este", width="stretch", key="csv_import_btn")
            with col_imp2:
                _do_all = st.button("Importar TODOS los pendientes",
                                    width="stretch", key="csv_import_all_btn",
                                    help="Salta años que ya estén en caché.")

            if _do_one or _do_all:
                if _do_all:
                    _targets = [(y, p) for y, p in _found.items()
                                if not (str(y) in _mani_state and _year_cache_path(y).exists())]
                    if not _targets:
                        st.success("Todos los años detectados ya están en caché.")
                        _targets = []
                else:
                    _targets = [_opts[_pick]]

                _prog = st.progress(0.0, text="Iniciando…")
                _ok = 0
                for _i, (_yr, _path) in enumerate(_targets):
                    _prog.progress(_i / max(len(_targets), 1),
                                   text=f"[{_i+1}/{len(_targets)}] Leyendo {_path.name}…")
                    try:
                        df_new = load_year_from_csv(_yr, _path, progress_cb=None)
                        if df_new is not None and not df_new.empty:
                            _ok += 1
                    except Exception as e:
                        st.warning(f"Falló {_yr}: {e}")

                if _targets:
                    _prog.progress(1.0, text=f"Listo: {_ok}/{len(_targets)} años importados.")
                    st.session_state["_cache_token"] = datetime.now().isoformat(timespec="seconds")
                    st.cache_data.clear()
                    st.session_state["_last_refresh"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    st.rerun()

    st.markdown("---")
    _sb_last = get_latest_data_date(st.session_state["year"])
    _sb_last_str = fmt_last_date(_sb_last)
    st.markdown(
        f"""<div class='muted'>
        Datos del portal oficial <b>datos.energia.gob.ar</b> —
        Secretaría de Energía, Capítulo IV (producción mensual por pozo).
        El año vivo ({LIVE_YEAR}) se actualiza con <i>Actualizar datos {LIVE_YEAR}</i>;
        los años anteriores son históricos y se cargan una sola vez vía CSV.<br>
        <b>Último dato publicado:</b> {_sb_last_str}
        </div>""",
        unsafe_allow_html=True,
    )
    st.caption(f"TLS: {TLS_MODE}")

    if st.session_state.get("_last_api_error"):
        with st.expander("Último error de API", expanded=False):
            st.code(st.session_state["_last_api_error"])

    _qlog = st.session_state.get("_query_log") or []
    if _qlog:
        with st.expander(f"Log de consultas ({len(_qlog)})", expanded=False):
            q_df = pd.DataFrame(_qlog[::-1])  # most recent first
            st.caption(
                f"Promedio: {int(np.mean([q['ms'] for q in _qlog]))} ms · "
                f"Máx: {max(q['ms'] for q in _qlog)} ms · "
                f"Errores: {sum(1 for q in _qlog if q['error'])}"
            )
            st.dataframe(q_df, width="stretch", hide_index=True, height=200)


# ---------------------------------------------------------------------------
# SCREEN 1 — Operator selector
# ---------------------------------------------------------------------------
def screen_operator() -> None:
    st.title("Argentina Well Intelligence Dashboard")
    last_date = get_latest_data_date(st.session_state["year"])
    last_str = fmt_last_date(last_date)
    st.caption(
        f"Fuente: Secretaría de Energía · Año {st.session_state['year']} · "
        f"Producción mensual por pozo (Capítulo IV) · "
        f"**Último dato publicado: {last_str}**"
    )

    companies = get_all_companies(st.session_state["year"])
    if not companies:
        _mani_now = _read_manifest()
        _has_cache = str(st.session_state["year"]) in _mani_now
        _yr_sel = st.session_state["year"]
        if not _has_cache:
            if _yr_sel == LIVE_YEAR:
                st.warning(
                    f"No hay datos en caché local para {_yr_sel}. Apretá "
                    f"**Actualizar datos {LIVE_YEAR}** en la barra lateral para "
                    f"descargar desde datos.energia.gob.ar (toma 30-60 segundos). "
                    f"Si la API sigue caída, bajá el CSV de {_yr_sel} a mano y usá "
                    f"**Cargar desde CSV local**."
                )
            else:
                st.warning(
                    f"No hay datos en caché local para {_yr_sel} (año histórico). "
                    f"En la barra lateral, expandí **Cargar desde CSV local** e importá "
                    f"el archivo correspondiente."
                )
        else:
            st.error(
                f"El caché local de {_yr_sel} está pero no se pueden listar empresas. "
                f"Probá re-importar el CSV de ese año o contactá al dev."
            )
        _err = st.session_state.get("_last_api_error")
        if _err:
            with st.expander("Detalle técnico del último error", expanded=True):
                st.code(_err)
        return

    st.markdown(
        f"**{len(companies)} empresas operadoras** con producción registrada en "
        f"{st.session_state['year']}."
    )

    # ---------- Buscadores: empresa & yacimiento ----------
    tab_emp, tab_yac = st.tabs(["Buscar por empresa", "Buscar por yacimiento"])

    with tab_emp:
        col_a, col_b = st.columns([3, 1])
        with col_a:
            selected = st.selectbox(
                "Empresa",
                options=["-- Seleccioná una empresa --"] + companies,
                index=0,
                key="op_selector",
                help="Escribí para filtrar (YPF, Vista, Pan American, Pampa, Tecpetrol...).",
            )
        with col_b:
            st.metric("Total empresas", len(companies))

    with tab_yac:
        yac_df = get_all_yacimientos(st.session_state["year"])
        if yac_df.empty:
            st.info("No se pudo cargar el listado de yacimientos.")
            yac_pick = "-- Elegir --"
        else:
            # Formato dropdown: "LOMA CAMPANA — YPF S.A. (Neuquina)"
            yac_df = yac_df.copy()
            yac_df["label"] = (
                yac_df["areayacimiento"].astype(str)
                + "  —  " + yac_df["empresa"].astype(str)
                + "  (" + yac_df["cuenca"].astype(str).str.title() + ")"
            )
            col_y1, col_y2 = st.columns([3, 1])
            with col_y1:
                yac_pick = st.selectbox(
                    "Yacimiento",
                    options=["-- Elegir --"] + yac_df["label"].tolist(),
                    index=0,
                    key="yac_selector",
                    help=(
                        "Escribí parte del nombre. Ej: 'Loma Campana', 'Bandurria', "
                        "'El Trapial'. Te lleva directo al detalle consolidado."
                    ),
                )
            with col_y2:
                st.metric(
                    "Yacimientos",
                    f"{yac_df['areayacimiento'].nunique():,}",
                )
        if yac_pick != "-- Elegir --":
            row = yac_df[yac_df["label"] == yac_pick].iloc[0]
            navigate(
                "field_detail",
                selected_operator=row["empresa"],
                selected_yacimiento=row["areayacimiento"],
                selected_basin=row["cuenca"],
            )
            st.rerun()

    if selected.startswith("--"):
        # ---------- Pulso del país: top yacimientos ----------
        year_cur = st.session_state["year"]
        last_ranking = get_latest_data_date(year_cur)
        last_ranking_str = fmt_last_date(last_ranking)
        st.markdown(f"### Top 10 yacimientos de Argentina · YTD {year_cur}")
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

            # Tendencia: último vs YTD
            t["trend_ratio"] = np.where(
                t["bpd_ytd"] > 0, t["bpd_last"] / t["bpd_ytd"], 0.0
            )
            def _arrow(r):
                if r <= 0:
                    return "—"
                if r > 1.05:
                    return "↑"
                if r < 0.95:
                    return "↓"
                return "="
            t["Tend."] = t["trend_ratio"].apply(_arrow)

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
                "cum_gas": "Gas (MMm³)",
            })[["#", "Yacimiento", "Operador", "Cuenca", "Provincia",
                "Pozos", "Petróleo YTD (m³)", "BPD YTD", "BPD últ. mes", "Tend.",
                "Gas (MMm³)"]]

            disp["Gas (MMm³)"] = disp["Gas (MMm³)"].apply(
                lambda v: f"{v/1000:,.2f}" if pd.notna(v) else "—"
            )
            disp["Petróleo YTD (m³)"] = disp["Petróleo YTD (m³)"].apply(
                lambda v: f"{v:,.0f}" if pd.notna(v) else "—"
            )
            for c in ["BPD YTD", "BPD últ. mes"]:
                disp[c] = disp[c].apply(lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
            disp["Pozos"] = disp["Pozos"].apply(
                lambda v: f"{int(v):,}" if pd.notna(v) else "—"
            )

            st.dataframe(disp, width="stretch", hide_index=True, height=400)

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
                st.dataframe(top_disp, width="stretch")
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
    _g = summary.get("total_gas") or 0
    k3.metric("Gas (MMm³)", f"{_g/1000:,.2f}" if _g else "—")
    k4.metric("Cuencas", fmt_int(summary.get("basins")))

    st.markdown("")
    b1, b2 = st.columns([3, 1])
    with b2:
        if st.button("Ver todos los yacimientos", type="primary", width="stretch"):
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
        <b>{int(wells):,}</b> pozos · {oil:,.0f} m³ petróleo · {gas/1000:,.2f} MMm³ gas
    </div>
</div>
""",
                unsafe_allow_html=True,
            )
            st.progress(min(max(pct, 0), 1.0))
            if st.button("Ver yacimientos →", key=f"basin_{row['cuenca']}", width="stretch"):
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
        if st.button("← Volver a empresas", width="stretch", key="back_fields"):
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
    # Tendencia: último mes vs YTD
    _trend_ratio = np.where(
        fields["bpd_ytd"] > 0, fields["bpd_last_month"] / fields["bpd_ytd"], 0.0
    )
    def _arrow(r):
        if r <= 0:
            return "—"
        if r > 1.05:
            return "↑"
        if r < 0.95:
            return "↓"
        return "="
    fields["trend"] = [_arrow(r) for r in _trend_ratio]

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
            "trend": "Tend.",
            "cum_gas": "Gas (MMm³)",
            "peak_oil_month": "Pico mes (m³)",
            "bpd_peak_month": "BPD pico",
            "n_months": "Meses",
            "status": "Estado",
            "quality": "Calidad",
        }
        display = fields.rename(columns=display_cols)[list(display_cols.values())]

        # Formatting
        for c in ["Petróleo (m³)", "Últ. mes (m³)", "Pico mes (m³)"]:
            display[c] = display[c].apply(lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
        display["Gas (MMm³)"] = display["Gas (MMm³)"].apply(
            lambda v: f"{v/1000:,.2f}" if pd.notna(v) else "—"
        )
        for c in ["BPD YTD", "BPD últ. mes", "BPD pico"]:
            display[c] = display[c].apply(lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
        for c in ["Pozos", "Meses"]:
            display[c] = display[c].apply(lambda v: f"{int(v):,}" if pd.notna(v) else "—")

        st.dataframe(display, width="stretch", height=520, hide_index=True)

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
            st.dataframe(leader_disp, width="stretch", height=240)

        # Quick stats for this view
        st.markdown("---")
        st.markdown("**Totales de la vista**")
        st.metric("Yacimientos", f"{len(fields):,}")
        st.metric("Pozos", f"{int(fields['wells'].sum()):,}")
        st.metric("Petróleo", fmt_m3(fields["cum_oil"].sum()))
        st.metric("BPD YTD (suma)", fmt_bpd(fields["bpd_ytd"].sum()))
        st.metric("Gas (MMm³)", f"{fields['cum_gas'].sum()/1000:,.2f}")


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
        if st.button("← Volver a yacimientos", width="stretch", key="back_field_detail"):
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
        color_discrete_sequence=EARTH_PALETTE,
    )
    fig_own.update_layout(
        height=90, margin=dict(l=0, r=0, t=5, b=0),
        xaxis=dict(range=[0, 100], title=None, showgrid=False),
        yaxis=dict(title=None),
        legend=dict(orientation="h", yanchor="bottom", y=1.05),
    )
    st.plotly_chart(fig_own, width="stretch")

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

    cum_oil_bbl = cum_oil * M3_TO_BBL
    cum_gas_MMm3 = cum_gas / 1000.0  # prod_gas viene en miles m³ → MMm³

    r2 = st.columns(4)
    r2[0].metric(
        f"Petróleo {year} (YTD)",
        fmt_m3(cum_oil),
        help=f"{cum_oil_bbl:,.0f} bbl ({cum_oil_bbl/1000:,.0f} k bbl)",
    )
    r2[1].metric(f"Petróleo {year} (bbl)", f"{cum_oil_bbl/1000:,.0f} k bbl")
    r2[2].metric(
        f"Gas {year} (MMm³)",
        f"{cum_gas_MMm3:,.2f}",
        help=f"{cum_gas:,.0f} miles m³ · {cum_gas*1000:,.0f} m³",
    )
    r2[3].metric(f"BPD {year} (YTD)", fmt_bpd(bpd_ytd))

    r3 = st.columns(4)
    r3[0].metric(
        "Pico mensual histórico",
        f"{peak_oil_m3:,.0f} m³",
        help=f"{peak_date.strftime('%Y-%m') if peak_date is not None and pd.notna(peak_date) else '—'} · {peak_wells} pozos",
    )
    r3[1].metric("BPD pico", fmt_bpd(bpd_peak))
    r3[2].metric("Eficiencia (últ. vs pico)", f"{efficiency:.1f}%")
    r3[3].metric("Decline rate (mensual)", f"{D*100:.1f}%")

    # First-production date (across all published years)
    first_prod = get_field_first_production(operator, yacimiento)
    if first_prod is not None:
        fp_year, fp_month = first_prod
        fp_label = f"{MESES_ES[fp_month-1]} {fp_year}" if 1 <= fp_month <= 12 else f"{fp_year}"
        fp_help = (
            f"Primer mes con producción publicada para este yacimiento×operador "
            f"(historia Secretaría desde {min(RESOURCES_BY_YEAR)})."
        )
    else:
        fp_label = "—"
        fp_help = "Sin registros en la serie publicada."

    r4 = st.columns(4)
    r4[0].metric("Inicio de producción", fp_label, help=fp_help)
    r4[1].metric("Pozos (máx concurrente)", f"{int(monthly['wells'].max() or 0)}")
    r4[2].metric(f"BPD {year} on-time (YTD)", fmt_bpd(bpd_ytd_ontime))
    r4[3].metric("Meses con datos", f"{months_active}")

    r5 = st.columns(3)
    r5[0].metric("Water cut", f"{water_cut:.1f}%")
    r5[1].metric("GOR (m³/m³)", f"{gor:,.0f}")
    r5[2].metric(
        "Gas YTD (MMm³)",
        f"{cum_gas_MMm3:,.2f}",
        help=f"{cum_gas:,.0f} miles m³ · {cum_gas*1000:,.0f} m³",
    )

    if months_active < 6:
        st.warning("Menos de 6 meses de datos consolidados — modelo predictivo no confiable.")

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

    # Precompute mode-independent metrics so every hover shows the full picture
    dim = monthly["days_in_month"].replace(0, np.nan).fillna(30.44)
    monthly["_bpd"] = monthly["oil"] * M3_TO_BBL / dim
    monthly["_bbl"] = monthly["oil"] * M3_TO_BBL

    if mode == "Ingresos (USD)":
        monthly["value"] = monthly["_bbl"] * price
        y_label = "Ingresos mensuales (USD)"
        primary_line = "Ingresos: $%{y:,.0f}<br>"
    elif mode == "BPD (calendario)":
        monthly["value"] = monthly["_bpd"]
        y_label = "BPD (barriles / día calendario)"
        primary_line = "BPD: %{y:,.0f} bbl/d<br>"
    else:
        monthly["value"] = monthly["oil"]
        y_label = "Petróleo (m³/mes)"
        primary_line = "Petróleo: %{y:,.0f} m³<br>"

    with c1:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=monthly["fecha"], y=monthly["value"],
            mode="lines+markers", name="Producción consolidada",
            line=dict(color="#5C3D2E", width=2.5),
            marker=dict(size=5),
            customdata=np.stack([
                monthly["wells"], monthly["oil"], monthly["_bbl"],
                monthly["_bpd"], monthly["gas"] / 1000.0,
            ], axis=-1),
            hovertemplate=(
                "<b>%{x|%Y-%m}</b><br>"
                + primary_line
                + "Pozos activos: %{customdata[0]:.0f}<br>"
                + "Petróleo: %{customdata[1]:,.0f} m³ "
                + "(%{customdata[2]:,.0f} bbl)<br>"
                + "BPD (calendario): %{customdata[3]:,.0f}<br>"
                + "Gas: %{customdata[4]:,.2f} MMm³"
                + "<extra></extra>"
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
                    line=dict(color="#B8663A", width=2, dash="dash"),
                ))
                eur = cum_oil + sum(forecast_oil)
                fig.add_annotation(
                    x=dates[len(dates) // 2], y=max(fvals) * 0.8 if fvals else 0,
                    text=f"<b>EUR: {eur:,.0f} m³ ({eur*M3_TO_BBL/1000:,.0f} k bbl)</b>",
                    showarrow=True, arrowhead=2, bgcolor="#FAF6F1", bordercolor="#B8663A",
                    font=dict(size=12, color="#5C3D2E"),
                )

        fig.update_layout(
            height=440, margin=dict(l=40, r=20, t=30, b=40),
            xaxis_title="Fecha", yaxis_title=y_label,
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            hovermode="x unified",
        )
        st.plotly_chart(fig, width="stretch")

    # ---------- Active-well count evolution ----------
    st.subheader("Pozos activos por mes")
    fig_w = go.Figure()
    fig_w.add_trace(go.Bar(
        x=monthly["fecha"], y=monthly["wells"],
        marker_color="#B8A89A", name="Pozos activos",
        hovertemplate="%{x|%Y-%m}<br>%{y:.0f} pozos<extra></extra>",
    ))
    fig_w.update_layout(
        height=220, margin=dict(l=40, r=20, t=10, b=40),
        xaxis_title="Fecha", yaxis_title="Nº pozos",
    )
    st.plotly_chart(fig_w, width="stretch")

    # ---------- Basin benchmarking (yacimiento vs yacimiento) ----------
    st.subheader("Benchmark de cuenca (yacimientos)")
    if basin:
        with st.spinner("Calculando percentiles de la cuenca..."):
            basin_fields = get_basin_field_totals(basin, year)
        # Aggregate basin rollup to yacimiento-level (sum across operators, keeping
        # the most recent month across JV partners for last-month metrics)
        if not basin_fields.empty:
            basin_fields = (
                basin_fields.groupby("areayacimiento", as_index=False)
                .agg(
                    cum_oil=("cum_oil", "sum"),
                    wells=("wells", "sum"),
                    n_months=("n_months", "max"),
                    oil_last=("oil_last", "sum"),
                    last_days=("last_days", "max"),
                )
            )
        if basin_fields.empty or len(basin_fields) < 5:
            st.info("Cuenca con pocos yacimientos; no se calculan percentiles.")
        else:
            # Filter out yacimientos with zero production before computing percentiles
            # (muchos campos en la base tienen 0 este año — meten ruido a la mediana)
            active = basin_fields[basin_fields["cum_oil"] > 0]
            if len(active) >= 5:
                p10 = active["cum_oil"].quantile(0.10)
                p50 = active["cum_oil"].quantile(0.50)
                p90 = active["cum_oil"].quantile(0.90)
            else:
                p10 = p50 = p90 = 0.0

            # Top-N yacimientos of the basin, with the selected one forced in
            TOP_N = 15
            top_df = basin_fields.sort_values("cum_oil", ascending=False).head(TOP_N).copy()
            if yacimiento not in set(top_df["areayacimiento"]):
                sel_row = basin_fields[basin_fields["areayacimiento"] == yacimiento]
                if not sel_row.empty:
                    top_df = pd.concat([top_df, sel_row], ignore_index=True)
            top_df = top_df.sort_values("cum_oil", ascending=True).reset_index(drop=True)
            top_df["bbl"] = top_df["cum_oil"] * M3_TO_BBL
            # BPD YTD por yacimiento: bbl acumulados / días con datos reportados
            # (usamos n_months*30.44 por yacimiento para ser fiel a su propia ventana)
            top_df["ytd_days"] = top_df["n_months"].fillna(0) * 30.44
            top_df["bpd_ytd"] = np.where(
                top_df["ytd_days"] > 0,
                top_df["bbl"] / top_df["ytd_days"],
                0.0,
            )
            # BPD último mes: bbl del último mes / días de ese mes
            top_df["last_bbl"] = top_df["oil_last"].fillna(0) * M3_TO_BBL
            top_df["bpd_last"] = np.where(
                top_df["last_days"].fillna(0) > 0,
                top_df["last_bbl"] / top_df["last_days"].replace(0, 30.44),
                0.0,
            )
            # Tendencia: último mes vs promedio YTD (>1 = subiendo)
            top_df["trend"] = np.where(
                top_df["bpd_ytd"] > 0,
                top_df["bpd_last"] / top_df["bpd_ytd"],
                0.0,
            )

            colors = [
                "#B8663A" if name == yacimiento else "#B8A89A"
                for name in top_df["areayacimiento"]
            ]
            bench = go.Figure()
            # Arrow ↑ si último mes > YTD, ↓ si viene bajando, = estable
            def _arrow(r):
                if r <= 0:
                    return ""
                if r > 1.05:
                    return "↑"
                if r < 0.95:
                    return "↓"
                return "="
            bench.add_trace(go.Bar(
                y=top_df["areayacimiento"],
                x=top_df["bbl"] / 1000,   # k bbl
                orientation="h",
                marker_color=colors,
                customdata=np.stack([
                    top_df["cum_oil"],
                    top_df["wells"],
                    top_df["bpd_ytd"],
                    top_df["bpd_last"],
                    top_df["trend"],
                ], axis=-1),
                hovertemplate=(
                    "<b>%{y}</b><br>"
                    "Petróleo YTD: %{x:,.0f} k bbl (%{customdata[0]:,.0f} m³)<br>"
                    "BPD YTD: %{customdata[2]:,.0f} bbl/d<br>"
                    "BPD último mes: %{customdata[3]:,.0f} bbl/d<br>"
                    "Tendencia (últ/YTD): %{customdata[4]:.2f}x<br>"
                    "Pozos: %{customdata[1]:.0f}<extra></extra>"
                ),
                text=[
                    f"{b/1000:,.0f} kbbl · YTD {ytd:,.0f} · últ {last:,.0f} bbl/d {_arrow(tr)}"
                    for b, ytd, last, tr in zip(
                        top_df["bbl"], top_df["bpd_ytd"],
                        top_df["bpd_last"], top_df["trend"],
                    )
                ],
                textposition="outside",
                cliponaxis=False,
            ))
            # Mediana de la cuenca como referencia (solo si hay suficientes activos)
            p50_bbl = p50 * M3_TO_BBL / 1000
            if p50 > 0:
                bench.add_vline(
                    x=p50_bbl, line_dash="dash", line_color="#5C3D2E",
                    annotation_text=f"Mediana cuenca: {p50_bbl:,.0f} k bbl",
                    annotation_position="top right",
                    annotation_font_color="#5C3D2E",
                )
            bench.update_layout(
                height=max(320, 28 * len(top_df) + 80),
                margin=dict(l=0, r=100, t=40, b=40),
                xaxis_title=f"Petróleo acumulado {year} (k bbl)",
                yaxis_title=None,
                showlegend=False,
            )
            st.caption(
                f"Top {TOP_N} yacimientos activos de {basin} — eje: petróleo YTD (k bbl). "
                f"Cada barra: **YTD** (promedio año) · **últ** (BPD último mes) · "
                f"↑ = último mes > YTD (subiendo), ↓ = bajando, = estable. "
                f"Percentiles sobre {len(active):,} yacimientos activos: "
                f"P10 {p10*M3_TO_BBL/1000:,.0f} · "
                f"P50 {p50*M3_TO_BBL/1000:,.0f} · "
                f"P90 {p90*M3_TO_BBL/1000:,.0f} k bbl."
            )
            st.plotly_chart(bench, width="stretch")

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
                neighbors["bbl"] = neighbors["cum_oil"] * M3_TO_BBL
                neighbors["ytd_days"] = neighbors["n_months"].fillna(0) * 30.44
                neighbors["bpd_ytd"] = np.where(
                    neighbors["ytd_days"] > 0,
                    neighbors["bbl"] / neighbors["ytd_days"], 0.0,
                )
                neighbors["bpd_last"] = np.where(
                    neighbors["last_days"].fillna(0) > 0,
                    neighbors["oil_last"].fillna(0) * M3_TO_BBL
                    / neighbors["last_days"].replace(0, 30.44),
                    0.0,
                )
                neighbors["k bbl YTD"] = (neighbors["bbl"] / 1000).round(0)
                neighbors_disp = neighbors.rename(columns={
                    "areayacimiento": "Yacimiento",
                    "wells": "Pozos",
                    "bpd_ytd": "BPD YTD",
                    "bpd_last": "BPD últ. mes",
                })[["Yacimiento", "Pozos", "k bbl YTD", "BPD YTD", "BPD últ. mes"]]
                for c in ["k bbl YTD", "BPD YTD", "BPD últ. mes"]:
                    neighbors_disp[c] = neighbors_disp[c].apply(
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
                    width="stretch",
                    height=min(40 + 35 * len(neighbors_disp), 450),
                )

    # ---------- Drill-down: individual wells ----------
    with st.expander(f"Pozos individuales ({n_wells_total})", expanded=False):
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
                "cum_gas": "Gas (MMm³)",
                "peak_oil": "Pico mes (m³)",
                "months": "Meses",
                "bpd_ytd": "BPD YTD",
            })[["#", "Pozo", "Formación", "Tipo", "Petróleo (m³)",
                 "BPD YTD", "Gas (MMm³)", "Pico mes (m³)", "Meses"]]
            for c in ["Petróleo (m³)", "Pico mes (m³)"]:
                w_disp[c] = w_disp[c].apply(lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
            w_disp["Gas (MMm³)"] = w_disp["Gas (MMm³)"].apply(
                lambda v: f"{v/1000:,.2f}" if pd.notna(v) else "—"
            )
            w_disp["BPD YTD"] = w_disp["BPD YTD"].apply(
                lambda v: f"{v:,.0f}" if pd.notna(v) else "—"
            )
            w_disp["Meses"] = w_disp["Meses"].apply(
                lambda v: f"{int(v):,}" if pd.notna(v) else "—"
            )
            st.dataframe(w_disp, width="stretch", hide_index=True, height=360)

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
        "Descargar reporte del yacimiento (CSV)",
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
