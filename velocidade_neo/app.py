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
st.caption("Versão simplificada, sem mapa, usando apenas Streamlit, Pandas, Plotly e Requests.")


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
    """Converte segundos para formato 0h 00min."""
    if not seconds or seconds < 0:
        return "0h 00min"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes:02d}min"


def api_get(url: str, api_key: str):
    """Faz GET na API da Cobli e trata erros HTTP."""
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
    """Converte uma data para timestamp em milissegundos respeitando o fuso."""
    if end_of_day:
        dt = datetime.combine(selected_date, datetime.max.time().replace(second=59, microsecond=0))
    else:
        dt = datetime.combine(selected_date, datetime.min.time())

    tz = pytz.timezone(timezone_name)
    return int(tz.localize(dt).timestamp() * 1000)


# =========================
# Chamadas da API
# =========================
@st.cache_data(show_spinner=False, ttl=300)
def get_paths_summary(license_plate: str, start: date, end: date, timezone_name: str, api_key: str) -> list:
    """Busca o resumo diário da placa no período informado."""
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

        has_next_page = body.get("pagination", {}).get("next")
        if not has_next_page:
            break

        page += 1

    return all_data


@st.cache_data(show_spinner=False, ttl=300)
def get_vehicle_route(vehicle_id: str, begin_ms: int, end_ms: int, timezone_name: str, api_key: str) -> list:
    """Busca os segmentos de rota para montar a linha do tempo."""
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
# Tratamento de dados
# =========================
def parse_summary_date(summary: dict):
    """Extrai a melhor data disponível no item de resumo."""
    for address_key in ["start_address", "end_address"]:
        address = summary.get(address_key) or {}
        for field in ["aggroupmentDate", "date"]:
            value = address.get(field)
            if value:
                return str(value).split("T")[0]
    return None


def build_summary_dataframe(summary_data: list) -> pd.DataFrame:
    """Transforma o retorno da API em uma tabela diária."""
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

            stop_total_s = stops.get("duration") or 0
            idle_s = stops.get("idle_time_duration") or 0
            parked_s = max(0, stop_total_s - idle_s)

            drive_s = trip.get("duration") or 0
            start_text = (start_address.get("date") or "").replace("T", " ")
            end_text = (end_address.get("date") or "").replace("T", " ")

            # Algumas respostas da API podem trazer trip.duration zerado.
            # Nesse caso, calculamos uma estimativa pelo intervalo total menos paradas.
            if drive_s == 0 and start_text and end_text:
                try:
                    start_dt = datetime.fromisoformat(start_text)
                    end_dt = datetime.fromisoformat(end_text)
                    active_s = (end_dt - start_dt).total_seconds()
                    drive_s = max(0, active_s - stop_total_s)
                except ValueError:
                    pass

            rows.append(
                {
                    "data": raw_date,
                    "inicio": start_text,
                    "fim": end_text,
                    "distancia_km": (trip.get("distance_in_meters") or 0) / 1000,
                    "tempo_rodando_s": drive_s,
                    "tempo_rodando_h": drive_s / 3600,
                    "tempo_parado_s": stop_total_s,
                    "tempo_ocioso_s": idle_s,
                    "tempo_ocioso_h": idle_s / 3600,
                    "tempo_parado_motor_off_s": parked_s,
                    "tempo_parado_motor_off_h": parked_s / 3600,
                    "paradas": stops.get("count") or 0,
                    "paradas_geofence": stops.get("count_in_geofence") or 0,
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df["data"] = pd.to_datetime(df["data"], errors="coerce")
    df = df.dropna(subset=["data"]).sort_values("data").reset_index(drop=True)
    df["data_fmt"] = df["data"].dt.strftime("%d/%m")
    return df


def timestamp_to_datetime(timestamp_ms: int, timezone_name: str) -> datetime:
    """Converte timestamp da API para datetime local."""
    if timestamp_ms > 1e10:
        timestamp_ms = timestamp_ms / 1000

    return (
        datetime.utcfromtimestamp(timestamp_ms)
        .replace(tzinfo=pytz.utc)
        .astimezone(pytz.timezone(timezone_name))
        .replace(tzinfo=None)
    )


def build_route_segments(route_data: list, timezone_name: str) -> pd.DataFrame:
    """Transforma os steps da rota em segmentos para a linha do tempo."""
    rows = []

    for route in route_data:
        for step in route.get("route_steps", []):
            start_ts = step.get("start_time")
            end_ts = step.get("end_time")

            if not start_ts or not end_ts:
                continue

            divisor = 1000 if end_ts > 1e10 else 1
            duration_s = abs((end_ts - start_ts) / divisor)

            rows.append(
                {
                    "tipo": step.get("type", ""),
                    "inicio": timestamp_to_datetime(start_ts, timezone_name),
                    "fim": timestamp_to_datetime(end_ts, timezone_name),
                    "duracao_s": duration_s,
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

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
            hovertemplate="<b>%{x}</b><br>Em movimento: %{y:.2f}h<extra></extra>",
        )
    )

    fig.add_trace(
        go.Bar(
            x=df["data_fmt"],
            y=df["tempo_ocioso_h"].round(2),
            name="Motor ocioso",
            hovertemplate="<b>%{x}</b><br>Motor ocioso: %{y:.2f}h<extra></extra>",
        )
    )

    fig.add_trace(
        go.Bar(
            x=df["data_fmt"],
            y=df["tempo_parado_motor_off_h"].round(2),
            name="Parado motor off",
            hovertemplate="<b>%{x}</b><br>Parado: %{y:.2f}h<extra></extra>",
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


def make_bar_chart(df: pd.DataFrame, column: str, title: str, y_label: str) -> go.Figure:
    fig = go.Figure(
        go.Bar(
            x=df["data_fmt"],
            y=df[column],
            hovertemplate="<b>%{x}</b><br>%{y}<extra></extra>",
        )
    )
    fig.update_layout(title=title, xaxis_title="Data", yaxis_title=y_label, height=320)
    return fig


def make_timeline_chart(segments: pd.DataFrame, plate: str) -> go.Figure:
    """Monta uma linha do tempo por dia com movimento e parada."""
    reference = datetime(2000, 1, 1)

    def to_reference_time(dt: datetime) -> datetime:
        return reference + timedelta(hours=dt.hour, minutes=dt.minute, seconds=dt.second)

    days = sorted(segments["inicio"].dt.date.unique(), reverse=True)
    y_map = {day: index for index, day in enumerate(days)}

    fig = go.Figure()

    for day, group in segments.groupby(segments["inicio"].dt.date, sort=False):
        y = y_map[day]
        day_label = pd.Timestamp(day).strftime("%d/%m/%Y")

        for _, segment in group.iterrows():
            start = max(segment["inicio"], datetime.combine(day, datetime.min.time()))
            end = min(segment["fim"], datetime.combine(day, datetime.max.time().replace(second=59)))

            if start >= end:
                continue

            label = "Em movimento" if segment["tipo"] == "trip" else "Parado"
            duration = fmt_duration(segment["duracao_s"])

            fig.add_trace(
                go.Scatter(
                    x=[to_reference_time(start), to_reference_time(end)],
                    y=[y, y],
                    mode="lines",
                    name=label,
                    line=dict(width=16),
                    hovertemplate=(
                        f"<b>{plate} — {day_label}</b><br>"
                        f"{label}<br>"
                        f"{start.strftime('%H:%M')} → {end.strftime('%H:%M')}<br>"
                        f"Duração: {duration}<extra></extra>"
                    ),
                    showlegend=False,
                )
            )

    tick_hours = list(range(0, 25, 2))
    fig.update_layout(
        title="Linha do tempo — movimento e paradas",
        height=max(300, len(days) * 55 + 120),
        margin=dict(l=10, r=10, t=50, b=40),
        xaxis=dict(
            tickvals=[reference + timedelta(hours=h) for h in tick_hours],
            ticktext=[f"{h:02d}:00" for h in tick_hours],
            range=[reference, reference + timedelta(hours=24)],
            title="Horário",
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
    total_paradas = int(df["paradas"].sum())
    active_days = int((df["distancia_km"] > 0).sum())

    st.subheader(
        f"Placa: {vehicle_plate} · {start_date.strftime('%d/%m/%Y')} → {end_date.strftime('%d/%m/%Y')}"
    )

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Dias com atividade", active_days)
    col2.metric("Distância total", f"{total_km:,.1f} km")
    col3.metric("Tempo em movimento", fmt_duration(total_drive_s))
    col4.metric("Motor ocioso", fmt_duration(total_idle_s))
    col5.metric("Total de paradas", f"{total_paradas:,}")

    st.divider()

    if vehicle_id:
        with st.spinner("Carregando linha do tempo..."):
            begin_ms = date_to_ms(start_date, timezone, end_of_day=False)
            end_ms = date_to_ms(end_date, timezone, end_of_day=True)
            route_data = get_vehicle_route(vehicle_id, begin_ms, end_ms, timezone, api_key)
            segments = build_route_segments(route_data, timezone)

        if not segments.empty:
            real_drive_s = segments.loc[segments["tipo"] == "trip", "duracao_s"].sum()
            real_stop_s = segments.loc[segments["tipo"] == "stop", "duracao_s"].sum()

            r1, r2, r3 = st.columns(3)
            r1.metric("Em movimento real", fmt_duration(real_drive_s))
            r2.metric("Parado real", fmt_duration(real_stop_s))
            r3.metric("Segmentos de parada", len(segments[segments["tipo"] == "stop"]))

            st.plotly_chart(make_timeline_chart(segments, vehicle_plate), use_container_width=True)
        else:
            st.info("Sem dados de segmentos para montar a linha do tempo.")

    st.divider()

    st.plotly_chart(make_stacked_time_chart(df), use_container_width=True)

    col_a, col_b = st.columns(2)
    with col_a:
        st.plotly_chart(
            make_bar_chart(df.assign(distancia_km=df["distancia_km"].round(1)), "distancia_km", "Distância por dia", "km"),
            use_container_width=True,
        )

    with col_b:
        st.plotly_chart(
            make_bar_chart(df, "paradas", "Paradas por dia", "Paradas"),
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
    table["Parado motor off"] = table["tempo_parado_motor_off_s"].apply(fmt_duration)
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
                "Parado motor off",
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
