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


def _resolve_stop_total(stops: dict) -> float:
    """
    Tenta extrair a duração total de paradas do objeto stops.
    A API pode retornar esse campo com nomes diferentes dependendo da versão.
    """
    for key in ["duration", "total_duration", "totalDuration", "stop_duration", "stopDuration", "total_time"]:
        val = safe_number(stops.get(key))
        if val > 0:
            return val
    return 0


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

            stop_total_s = _resolve_stop_total(stops)
            idle_s = safe_number(stops.get("idle_time_duration"))
            parked_s = max(0, stop_total_s - idle_s)

            drive_s = safe_number(trip.get("duration"))
            start_text = (start_address.get("date") or "").replace("T", " ")
            end_text = (end_address.get("date") or "").replace("T", " ")

            # Fallback para drive_s zerado
            if drive_s == 0 and start_text and end_text:
                try:
                    start_dt = datetime.fromisoformat(start_text)
                    end_dt = datetime.fromisoformat(end_text)
                    active_s = (end_dt - start_dt).total_seconds()
                    drive_s = max(0, active_s - stop_total_s)
                except ValueError:
                    pass

            # -------------------------------------------------------
            # Fallback para parked_s zerado:
            # Se a API não retornou o total de paradas (ou retornou só
            # o tempo ocioso sem o total), calcula pela diferença entre
            # o período completo do dia e os tempos já conhecidos.
            # -------------------------------------------------------
            if parked_s == 0 and start_text and end_text:
                try:
                    start_dt = datetime.fromisoformat(start_text)
                    end_dt = datetime.fromisoformat(end_text)
                    total_period_s = (end_dt - start_dt).total_seconds()
                    parked_s = max(0, total_period_s - drive_s - idle_s)
                except ValueError:
                    pass

            rows.append(
                {
                    "data": raw_date,
                    "inicio": start_text,
                    "fim": end_text,
                    "distancia_km": safe_number(trip.get("distance_in_meters")) / 1000,
                    "tempo_rodando_s": drive_s,
                    "tempo_rodando_h": drive_s / 3600,
                    "tempo_parado_s": stop_total_s,
                    "tempo_ocioso_s": idle_s,
                    "tempo_ocioso_h": idle_s / 3600,
                    "tempo_parado_normal_s": parked_s,
                    "tempo_parado_normal_h": parked_s / 3600,
                    "paradas": int(safe_number(stops.get("count"))),
                    "paradas_geofence": int(safe_number(stops.get("count_in_geofence"))),
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    df = df.dropna(subset=["data"]).sort_values("data").reset_index(drop=True)
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


def make_timeline_chart(segments: pd.DataFrame, plate: str) -> go.Figure:
    reference = datetime(2000, 1, 1)

    def to_reference_time(dt: datetime) -> datetime:
        return reference + timedelta(hours=dt.hour, minutes=dt.minute, seconds=dt.second)

    days = sorted(segments["inicio"].dt.date.unique(), reverse=True)
    y_map = {day: index for index, day in enumerate(days)}

    fig = go.Figure()
    legend_added = set()

    for day, group in segments.groupby(segments["inicio"].dt.date, sort=False):
        y = y_map[day]
        day_label = pd.Timestamp(day).strftime("%d/%m/%Y")

        fig.add_trace(
            go.Scatter(
                x=[reference, reference + timedelta(hours=24)],
                y=[y, y],
                mode="lines",
                line=dict(color=COLOR_FUNDO_LINHA, width=20),
                hoverinfo="skip",
                showlegend=False,
            )
        )

        for _, segment in group.iterrows():
            start = max(segment["inicio"].to_pydatetime(), datetime.combine(day, datetime.min.time()))
            end = min(segment["fim"].to_pydatetime(), datetime.combine(day, datetime.max.time().replace(second=59)))

            if start >= end:
                continue

            tipo = segment["tipo"]
            label = TIMELINE_LABELS.get(tipo, tipo)
            color = TIMELINE_COLORS.get(tipo, "#000000")
            duration = fmt_duration(segment["duracao_s"])
            show_legend = label not in legend_added

            fig.add_trace(
                go.Scatter(
                    x=[to_reference_time(start), to_reference_time(end)],
                    y=[y, y],
                    mode="lines",
                    name=label,
                    legendgroup=label,
                    line=dict(color=color, width=16),
                    hovertemplate=(
                        f"<b>{plate} — {day_label}</b><br>"
                        f"{label}<br>"
                        f"{start.strftime('%H:%M')} → {end.strftime('%H:%M')}<br>"
                        f"Duração: {duration}<extra></extra>"
                    ),
                    showlegend=show_legend,
                )
            )

            if show_legend:
                legend_added.add(label)

    tick_hours = list(range(0, 25, 2))
    fig.update_layout(
        title="Linha do tempo — movimento, motor ocioso e parado normal",
        height=max(300, len(days) * 55 + 120),
        margin=dict(l=10, r=10, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        xaxis=dict(
            tickvals=[reference + timedelta(hours=h) for h in tick_hours],
            ticktext=[f"{h:02d}:00" for h in tick_hours],
            range=[reference, reference + timedelta(hours=24)],
            title="Horário",
            showgrid=True,
        ),
        yaxis=dict(
            tickvals=list(y_map.values()),
            ticktext=[pd.Timestamp(day).strftime("%d/%m") for day in days],
            title="Dia",
        ),
    )

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
