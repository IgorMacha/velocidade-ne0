import requests
import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, date, timedelta
import pytz


# =========================
# Configuração da página
# =========================
st.set_page_config(
    page_title="Cobli – Tempo Rodado",
    page_icon="🚗",
    layout="wide",
)

st.title("🚗 Cobli — Tempo Rodado por Período")
st.caption("Versão simplificada, sem mapa, com linha do tempo separando movimento, motor ocioso e parado normal.")

# =========================
# Cores fixas
# =========================
COLOR_MOVIMENTO = "#1565C0"
COLOR_OCIOSO = "#FF8F00"
COLOR_PARADO_NORMAL = "#2E7D32"
COLOR_FUNDO_LINHA = "#E0E0E0"
COLOR_DISTANCIA = "#455A64"
COLOR_PARADAS = "#6A1B9A"

TIMELINE_COLORS = {
    "trip": COLOR_MOVIMENTO,
    "idle": COLOR_OCIOSO,
    "parked": COLOR_PARADO_NORMAL,
}

TIMELINE_LABELS = {
    "trip": "Em movimento",
    "idle": "Motor ocioso",
    "parked": "Parado normal",
}


# =========================
# Sidebar
# =========================
with st.sidebar:
    st.header("Configurações")

    api_key = st.text_input(
        "Chave API",
        type="password",
        placeholder="Insira sua chave API",
        help="Painel > Configurações > Chaves de API",
    )

    license_plate = st.text_input(
        "Placa do veículo",
        placeholder="Ex: ABC1234",
    ).strip().upper()

    st.subheader("Período")
    today = date.today()
    date_range = st.date_input(
        "Intervalo de datas",
        value=(today - timedelta(days=30), today),
        max_value=today,
        format="DD/MM/YYYY",
    )

    timezone = st.selectbox(
        "Fuso horário",
        [
            "America/Sao_Paulo",
            "America/Manaus",
            "America/Belem",
            "America/Fortaleza",
            "America/Noronha",
        ],
    )

    buscar = st.button("Buscar dados", type="primary", use_container_width=True)


# =========================
# Funções auxiliares
# =========================
def fmt_duration(seconds: float) -> str:
    if not seconds or seconds < 0:
        return "0h 00min"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes:02d}min"


def api_get(url: str, api_key: str):
    response = requests.get(
        url,
        headers={"cobli-api-key": api_key},
        timeout=30,
    )

    if not response.ok:
        raise requests.exceptions.HTTPError(
            f"{response.status_code} — {response.text or '(sem corpo)'}",
            response=response,
        )

    return response.json()


def date_to_ms(selected_date: date, timezone_name: str, end_of_day: bool = False) -> int:
    if end_of_day:
        dt = datetime.combine(selected_date, datetime.max.time().replace(second=59, microsecond=0))
    else:
        dt = datetime.combine(selected_date, datetime.min.time())

    tz = pytz.timezone(timezone_name)
    return int(tz.localize(dt).timestamp() * 1000)


def timestamp_to_datetime(timestamp_value: int, timezone_name: str) -> datetime:
    if timestamp_value > 1e10:
        timestamp_value = timestamp_value / 1000

    return (
        datetime.utcfromtimestamp(timestamp_value)
        .replace(tzinfo=pytz.utc)
        .astimezone(pytz.timezone(timezone_name))
        .replace(tzinfo=None)
    )


def safe_number(value, default: float = 0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


# =========================
# Chamadas da API
# =========================
@st.cache_data(show_spinner=False, ttl=300)
def get_paths_summary(license_plate: str, start: date, end: date, timezone_name: str, api_key: str) -> list:
    all_data = []
    page = 1

    while True:
        url = (
            "https://api.cobli.co/public/v1/paths/summary"
            f"?startDate={start}"
            f"&endDate={end}"
            "&periodAggroupment=daily"
            "&limit=2000"
            f"&page={page}"
            f"&licensePlates={license_plate}"
            f"&timezone={timezone_name}"
        )

        body = api_get(url, api_key)
        all_data.extend(body.get("data", []))

        if not body.get("pagination", {}).get("next"):
            break

        page += 1

    return all_data


@st.cache_data(show_spinner=False, ttl=300)
def get_vehicle_route(vehicle_id: str, begin_ms: int, end_ms: int, timezone_name: str, api_key: str) -> list:
    url = (
        "https://api.cobli.co/herbie-1.1/costs/vehicle-route"
        f"?vehicle_id={vehicle_id}"
        f"&begin={begin_ms}"
        f"&end={end_ms}"
        f"&tz={timezone_name}"
        "&utmSource=StreamlitDashboard"
    )
    return api_get(url, api_key)


# =========================
# Tratamento do resumo diário
# =========================
def parse_summary_date(summary: dict):
    for address_key in ["start_address", "end_address"]:
        address = summary.get(address_key) or {}
        for field in ["aggroupmentDate", "date"]:
            value = address.get(field)
            if value:
                return str(value).split("T")[0]
    return None


def _parse_dt(text: str):
    """Converte string ISO da API em datetime, ou None se inválida."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("T", " ").strip())
    except ValueError:
        return None


def build_summary_dataframe(summary_data: list) -> pd.DataFrame:
    rows = []

    for item in summary_data:
        for summary in item.get("summaries", []):
            raw_date = parse_summary_date(summary)
            if not raw_date:
                continue

            trip = summary.get("trip") or {}
            stops = summary.get("stops") or {}
            start_address = summary.get("start_address") or {}
            end_address = summary.get("end_address") or {}

            # Converte datas para datetime agora — evita strings vazias no groupby
            inicio_dt = _parse_dt(start_address.get("date") or "")
            fim_dt = _parse_dt(end_address.get("date") or "")

            # Campos confirmados pelo schema da API Cobli
            drive_s     = safe_number(trip.get("duration"))
            stop_total_s = safe_number(stops.get("duration"))   # total paradas (ocioso + normal)
            idle_s      = safe_number(stops.get("idle_time_duration"))

            # ── Fallback para drive_s ──────────────────────────────────────────
            # trip.duration pode vir zerado na API para alguns veículos/períodos.
            # Se temos o total de paradas (stops.duration > 0), estimamos o tempo
            # em movimento como: período_total - tempo_de_parada.
            # Isso evita que o fallback de parked roube o espaço do movimento.
            if drive_s == 0 and stop_total_s > 0 and inicio_dt and fim_dt:
                periodo_s = (fim_dt - inicio_dt).total_seconds()
                drive_s = max(0.0, periodo_s - stop_total_s)

            rows.append(
                {
                    "data": raw_date,
                    "inicio_dt": inicio_dt,
                    "fim_dt": fim_dt,
                    "inicio": (start_address.get("date") or "").replace("T", " ").strip(),
                    "fim": (end_address.get("date") or "").replace("T", " ").strip(),
                    "distancia_km": safe_number(trip.get("distance_in_meters")) / 1000,
                    "tempo_rodando_s": drive_s,
                    "tempo_ocioso_s": idle_s,
                    "tempo_parado_s": stop_total_s,
                    "paradas": int(safe_number(stops.get("count"))),
                    "paradas_geofence": int(safe_number(stops.get("count_in_geofence"))),
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    df = df.dropna(subset=["data"])

    # A API pode retornar múltiplas entradas por dia (ex: troca de motorista).
    # Agrupa por data: soma durações, mantém o intervalo mais amplo do dia.
    # Usa objetos datetime (não strings) no min/max para evitar bug com string vazia.
    df["inicio_dt"] = pd.to_datetime(df["inicio_dt"])
    df["fim_dt"] = pd.to_datetime(df["fim_dt"])

    df = (
        df.groupby("data", as_index=False)
        .agg(
            inicio_dt=("inicio_dt", "min"),
            fim_dt=("fim_dt", "max"),
            inicio=("inicio", lambda s: next((v for v in sorted(s) if v), "")),
            fim=("fim", lambda s: next((v for v in sorted(s, reverse=True) if v), "")),
            distancia_km=("distancia_km", "sum"),
            tempo_rodando_s=("tempo_rodando_s", "sum"),
            tempo_ocioso_s=("tempo_ocioso_s", "sum"),
            tempo_parado_s=("tempo_parado_s", "sum"),
            paradas=("paradas", "sum"),
            paradas_geofence=("paradas_geofence", "sum"),
        )
    )

    def calc_parked(row):
        # Tentativa 1: stops.duration existe → parado normal = total_paradas - ocioso
        if row["tempo_parado_s"] > 0:
            return max(0.0, row["tempo_parado_s"] - row["tempo_ocioso_s"])

        # Tentativa 2: stops.duration = 0 → usa período total - movimento - ocioso
        # (só entra aqui se a API não retornou stops.duration; drive_s já foi
        #  corrigido pelo fallback por entrada, então esse total não é roubado)
        start = row["inicio_dt"]
        end = row["fim_dt"]
        if pd.notna(start) and pd.notna(end) and end > start:
            total_s = (end - start).total_seconds()
            return max(0.0, total_s - row["tempo_rodando_s"] - row["tempo_ocioso_s"])

        return 0.0

    df["tempo_parado_normal_s"] = df.apply(calc_parked, axis=1)
    df["tempo_parado_normal_h"] = df["tempo_parado_normal_s"] / 3600
    df["tempo_rodando_h"] = df["tempo_rodando_s"] / 3600
    df["tempo_ocioso_h"] = df["tempo_ocioso_s"] / 3600

    df = df.sort_values("data").reset_index(drop=True)
    df["data_fmt"] = df["data"].dt.strftime("%d/%m")
    return df


# =========================
# Tratamento dos segmentos da rota
# =========================
def extract_idle_duration_from_step(step: dict, duration_s: float) -> float:
    possible_keys = [
        "idle_time_duration",
        "idle_duration",
        "idleTimeDuration",
        "idle_time",
        "idleTime",
        "engine_idle_duration",
        "engineIdleDuration",
    ]

    for key in possible_keys:
        if key in step:
            value = safe_number(step.get(key))
            if value > duration_s * 10 and value > 1000:
                value = value / 1000
            return max(0, min(value, duration_s))

    for nested_key in ["stop", "stops", "metadata", "properties"]:
        nested = step.get(nested_key)
        if isinstance(nested, dict):
            for key in possible_keys:
                if key in nested:
                    value = safe_number(nested.get(key))
                    if value > duration_s * 10 and value > 1000:
                        value = value / 1000
                    return max(0, min(value, duration_s))

    ignition_fields = ["ignition_on", "ignitionOn", "engine_on", "engineOn", "vehicle_on", "vehicleOn"]
    for field in ignition_fields:
        if field in step and step.get(field) is True:
            return duration_s

    return 0


def build_route_segments(route_data: list, timezone_name: str) -> pd.DataFrame:
    rows = []

    for route in route_data:
        for step in route.get("route_steps", []):
            start_ts = step.get("start_time")
            end_ts = step.get("end_time")
            step_type = step.get("type", "")

            if not start_ts or not end_ts:
                continue

            divisor = 1000 if end_ts > 1e10 else 1
            duration_s = abs((end_ts - start_ts) / divisor)
            start_dt = timestamp_to_datetime(start_ts, timezone_name)
            end_dt = timestamp_to_datetime(end_ts, timezone_name)

            if duration_s <= 0:
                continue

            if step_type == "trip":
                rows.append(
                    {
                        "tipo": "trip",
                        "inicio": start_dt,
                        "fim": end_dt,
                        "duracao_s": duration_s,
                    }
                )

            elif step_type == "stop":
                idle_s = extract_idle_duration_from_step(step, duration_s)
                parked_s = max(0, duration_s - idle_s)

                if idle_s > 0:
                    idle_end = min(end_dt, start_dt + timedelta(seconds=idle_s))
                    rows.append(
                        {
                            "tipo": "idle",
                            "inicio": start_dt,
                            "fim": idle_end,
                            "duracao_s": idle_s,
                        }
                    )

                    if parked_s > 0 and idle_end < end_dt:
                        rows.append(
                            {
                                "tipo": "parked",
                                "inicio": idle_end,
                                "fim": end_dt,
                                "duracao_s": parked_s,
                            }
                        )
                else:
                    rows.append(
                        {
                            "tipo": "parked",
                            "inicio": start_dt,
                            "fim": end_dt,
                            "duracao_s": duration_s,
                        }
                    )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["inicio"] = pd.to_datetime(df["inicio"])
    df["fim"] = pd.to_datetime(df["fim"])
    return df.sort_values("inicio").reset_index(drop=True)


def build_daily_from_segments(segments: pd.DataFrame) -> pd.DataFrame:
    """
    Agrega os segmentos de rota por dia.
    Fonte dos dados: API vehicle-route (confiável para tempo).
    Retorna DataFrame com as mesmas colunas de tempo usadas no gráfico de barras.
    """
    if segments.empty:
        return pd.DataFrame()

    seg = segments.copy()
    seg["data"] = seg["inicio"].dt.normalize()  # dia sem hora

    rows = []
    for day, group in seg.groupby("data"):
        drive_s  = group.loc[group["tipo"] == "trip",   "duracao_s"].sum()
        idle_s   = group.loc[group["tipo"] == "idle",   "duracao_s"].sum()
        parked_s = group.loc[group["tipo"] == "parked", "duracao_s"].sum()
        rows.append(
            {
                "data":                  pd.Timestamp(day),
                "tempo_rodando_s":       drive_s,
                "tempo_ocioso_s":        idle_s,
                "tempo_parado_normal_s": parked_s,
                "tempo_rodando_h":       round(drive_s  / 3600, 2),
                "tempo_ocioso_h":        round(idle_s   / 3600, 2),
                "tempo_parado_normal_h": round(parked_s / 3600, 2),
            }
        )

    df = pd.DataFrame(rows).sort_values("data").reset_index(drop=True)
    df["data_fmt"] = df["data"].dt.strftime("%d/%m")
    return df


# =========================
# Gráficos
# =========================
def make_stacked_time_chart(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            x=df["data_fmt"],
            y=df["tempo_rodando_h"].round(2),
            name="Em movimento",
            marker_color=COLOR_MOVIMENTO,
            hovertemplate="<b>%{x}</b><br>Em movimento: %{y:.2f}h<extra></extra>",
        )
    )

    fig.add_trace(
        go.Bar(
            x=df["data_fmt"],
            y=df["tempo_parado_normal_h"].round(2),
            name="Parado normal",
            marker_color=COLOR_PARADO_NORMAL,
            hovertemplate="<b>%{x}</b><br>Parado normal: %{y:.2f}h<extra></extra>",
        )
    )

    fig.update_layout(
        title="Tempo por dia em horas",
        barmode="stack",
        xaxis_title="Data",
        yaxis_title="Horas",
        height=380,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    return fig


def make_bar_chart(df: pd.DataFrame, column: str, title: str, y_label: str, color: str) -> go.Figure:
    fig = go.Figure(
        go.Bar(
            x=df["data_fmt"],
            y=df[column],
            marker_color=color,
            hovertemplate="<b>%{x}</b><br>%{y}<extra></extra>",
        )
    )
    fig.update_layout(title=title, xaxis_title="Data", yaxis_title=y_label, height=320)
    return fig


# =========================
# App principal
# =========================
if not buscar:
    st.info("Preencha os campos na barra lateral e clique em **Buscar dados**.")
    st.stop()

if not api_key:
    st.warning("Insira sua chave API na barra lateral.")
    st.stop()

if not license_plate:
    st.warning("Insira a placa do veículo.")
    st.stop()

if not isinstance(date_range, (list, tuple)) or len(date_range) != 2:
    st.warning("Selecione um intervalo de datas válido.")
    st.stop()

start_date, end_date = date_range

try:
    with st.spinner("Buscando resumo diário..."):
        summary_data = get_paths_summary(license_plate, start_date, end_date, timezone, api_key)

    if not summary_data:
        st.error(f"Nenhum dado encontrado para a placa **{license_plate}** no período selecionado.")
        st.stop()

    vehicle = summary_data[0].get("vehicle", {})
    vehicle_id = vehicle.get("id")
    vehicle_plate = vehicle.get("license_plate", license_plate)

    df = build_summary_dataframe(summary_data)

    if df.empty:
        st.error("A API retornou dados, mas não foi possível montar a tabela diária.")
        st.stop()

    total_km = df["distancia_km"].sum()
    total_drive_s = df["tempo_rodando_s"].sum()
    total_idle_s = df["tempo_ocioso_s"].sum()
    total_parked_s = df["tempo_parado_normal_s"].sum()
    total_paradas = int(df["paradas"].sum())
    active_days = int((df["distancia_km"] > 0).sum())

    st.subheader(
        f"Placa: {vehicle_plate} · {start_date.strftime('%d/%m/%Y')} → {end_date.strftime('%d/%m/%Y')}"
    )

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Dias com atividade", active_days)
    col2.metric("Distância total", f"{total_km:,.1f} km")
    col3.metric("Em movimento", fmt_duration(total_drive_s))
    col4.metric("Motor ocioso", fmt_duration(total_idle_s))
    col5.metric("Parado normal", fmt_duration(total_parked_s))
    col6.metric("Total de paradas", f"{total_paradas:,}")

    st.divider()

    if vehicle_id:
        with st.spinner("Carregando linha do tempo..."):
            begin_ms = date_to_ms(start_date, timezone, end_of_day=False)
            end_ms = date_to_ms(end_date, timezone, end_of_day=True)
            route_data = get_vehicle_route(vehicle_id, begin_ms, end_ms, timezone, api_key)
            segments = build_route_segments(route_data, timezone)

        if not segments.empty:
            real_drive_s = segments.loc[segments["tipo"] == "trip", "duracao_s"].sum()
            real_idle_s = segments.loc[segments["tipo"] == "idle", "duracao_s"].sum()
            real_parked_s = segments.loc[segments["tipo"] == "parked", "duracao_s"].sum()

            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Em movimento real", fmt_duration(real_drive_s))
            r2.metric("Motor ocioso real", fmt_duration(real_idle_s))
            r3.metric("Parado normal real", fmt_duration(real_parked_s))
            r4.metric("Segmentos", len(segments))

            if real_idle_s == 0:
                st.caption(
                    "Observação: se a API de rota não retornar o tempo de motor ocioso por parada, "
                    "a linha do tempo classifica as paradas como parado normal. O gráfico diário abaixo usa o resumo da API."
                )

            st.plotly_chart(make_timeline_chart(segments, vehicle_plate), use_container_width=True)
        else:
            st.info("Sem dados de segmentos para montar a linha do tempo.")

    st.divider()

    st.plotly_chart(make_stacked_time_chart(df), use_container_width=True)

    col_a, col_b = st.columns(2)
    with col_a:
        st.plotly_chart(
            make_bar_chart(
                df.assign(distancia_km=df["distancia_km"].round(1)),
                "distancia_km",
                "Distância por dia",
                "km",
                COLOR_DISTANCIA,
            ),
            use_container_width=True,
        )

    with col_b:
        st.plotly_chart(
            make_bar_chart(df, "paradas", "Paradas por dia", "Paradas", COLOR_PARADAS),
            use_container_width=True,
        )

    st.divider()
    st.subheader("Detalhamento diário")

    table = df.copy()
    table["Data"] = table["data"].dt.strftime("%d/%m/%Y")
    table["Início"] = table["inicio"].apply(lambda value: value[:16] if value else "—")
    table["Fim"] = table["fim"].apply(lambda value: value[:16] if value else "—")
    table["Distância km"] = table["distancia_km"].round(1)
    table["Em movimento"] = table["tempo_rodando_s"].apply(fmt_duration)
    table["Motor ocioso"] = table["tempo_ocioso_s"].apply(fmt_duration)
    table["Parado normal"] = table["tempo_parado_normal_s"].apply(fmt_duration)
    table["Paradas"] = table["paradas"].astype(int)

    st.dataframe(
        table[
            [
                "Data",
                "Início",
                "Fim",
                "Distância km",
                "Em movimento",
                "Motor ocioso",
                "Parado normal",
                "Paradas",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

    csv = table.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "Baixar CSV",
        data=csv,
        file_name=f"tempo_rodado_{vehicle_plate}_{start_date}_{end_date}.csv",
        mime="text/csv",
        use_container_width=True,
    )

except requests.exceptions.HTTPError as error:
    status = error.response.status_code if error.response is not None else "?"

    if status == 401:
        st.error("Chave API inválida ou sem permissão.")
    elif status == 404:
        st.error("Veículo não encontrado. Verifique a placa e o período.")
    else:
        st.error(f"Erro na API ({status}): {error}")

except requests.exceptions.ConnectionError:
    st.error("Sem conexão com a API.")

except Exception as error:
    st.error(f"Erro inesperado: {error}")
