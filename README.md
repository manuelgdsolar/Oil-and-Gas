# Argentina Well Intelligence Dashboard

Dashboard interactivo en Streamlit con datos en vivo de la Secretaría de
Energía — **Capítulo IV** (producción mensual por pozo). Soporta ~66
empresas operadoras, ~83 000 pozos, todas las cuencas (Neuquina, Golfo
San Jorge, Austral, Noroeste, Cuyana) y ambos tipos de recurso
(Convencional / No Convencional).

## Instalación

```bash
pip install -r requirements.txt
```

## Ejecución

```bash
streamlit run app.py
```

Se abre en `http://localhost:8501`.

> **Importante — no se ejecuta desde Spyder/PyCharm.** Streamlit corre
> un servidor propio; siempre se lanza con `streamlit run app.py` desde
> la Terminal.

## Cómo usarlo

1. **Pantalla 1 — Empresa:** elegí una empresa del dropdown (por
   ejemplo *VISTA ENERGY ARGENTINA SAU*, *YPF S.A.*, *PAN AMERICAN
   ENERGY SL*). Ves KPIs totales y las cuencas donde opera.
2. **Pantalla 2 — Pozos:** ranking de todos los pozos de la empresa
   (o filtrado por cuenca). Filtros por tipo de recurso y provincia.
   Panel lateral con el top-5 de operadores de la cuenca.
3. **Pantalla 3 — Detalle del pozo:** serie temporal multianual,
   KPIs (water cut, GOR, decline rate, eficiencia), proyección DCA
   exponencial con EUR, benchmark P10/P50/P90 de la cuenca, ranking
   posicional, metadata del padrón y descarga CSV.

## Arquitectura de datos

- **Endpoint SQL** (`datastore_search_sql`) para rollups por empresa y
  cuenca — aggregations ejecutadas en el servidor, respuesta en ~0.7 s
  aunque el dataset tenga 990 000 registros.
- **Endpoint de búsqueda filtrada** para el detalle de un pozo.
- Recurso principal: `d774b5d7-0756-48fe-88f2-8729b57b22da`
  (*Producción de Pozos de Gas y Petróleo – 2025*). En la barra
  lateral podés cambiar el año (2018-2026).
- Recurso secundario para metadata: `cb5c0f04-7835-45cd-b982-3e25ca7d7751`
  (*Capítulo IV — Pozos*, padrón con 85 000 pozos).
- Todas las consultas se cachean con `st.cache_data` (TTL 1-24 h).

## Notas técnicas

- `verify=False` en `requests` porque el bundle SSL de Anaconda en
  macOS no reconoce la cadena del certificado de
  `datos.energia.gob.ar`. Podés quitarlo si usás otro Python donde
  `certifi` tiene la CA raíz correcta.
- La "participación" mostrada en la pantalla de detalle es un mock
  de JVs públicos conocidas (Loma Campana, Cerro Dragón, etc.) — el
  API no expone composición accionaria.
