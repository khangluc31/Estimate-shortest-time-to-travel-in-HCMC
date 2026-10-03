"""Dark visual UI for the HCMC traffic-aware Bidirectional A* demo.

Run with: streamlit run app.py
The app reads the supplied CSV archive directly; set HCMC_DATA_DIR if needed.
"""

from __future__ import annotations

import heapq
import math
import os
import random
from dataclasses import dataclass
from pathlib import Path

import folium
import pandas as pd
import streamlit as st
from folium.plugins import Fullscreen
from streamlit_folium import st_folium


ROOT = Path(__file__).resolve().parent
BBOX = (10.8235, 10.7545, 106.7420, 106.6500)  # north, south, east, west
LANDMARKS = {
    "Đại học Bách Khoa": (10.7721, 106.6578),
    "Chợ Bến Thành": (10.7725, 106.6980),
    "Dinh Độc Lập": (10.7770, 106.6953),
    "Nhà thờ Đức Bà": (10.7798, 106.6990),
    "Bưu điện Thành phố": (10.7801, 106.6999),
    "Phố đi bộ Nguyễn Huệ": (10.7741, 106.7034),
    "Bến Bạch Đằng": (10.7753, 106.7075),
    "Ga Sài Gòn": (10.7821, 106.6779),
    "Bệnh viện Chợ Rẫy": (10.7572, 106.6595),
    "Landmark 81": (10.7950, 106.7218),
    "Thảo Cầm Viên": (10.7870, 106.7051),
    "Công viên Tao Đàn": (10.7744, 106.6920),
}
SPEEDS = {
    "motorway": 120.0, "motorway_link": 80.0, "trunk": 80.0,
    "trunk_link": 60.0, "primary": 60.0, "primary_link": 40.0,
    "secondary": 50.0, "secondary_link": 40.0, "tertiary": 40.0,
    "tertiary_link": 30.0, "residential": 30.0,
    "unclassified": 30.0, "service": 20.0,
}
LOS_DENSITY = {"A": .08, "B": .20, "C": .35, "D": .50, "E": .68, "F": .84}


@dataclass
class Vertex:
    node_id: int
    lon: float
    lat: float


@dataclass
class Edge:
    segment_id: int
    source: int
    target: int
    length: float
    speed_limit: float
    name: str
    observed_density: float
    density: float
    weather: float = 1.0
    blocked: bool = False

    @property
    def travel_time(self):
        if self.blocked:
            return math.inf
        speed = max(self.speed_limit * (1 - self.density) * self.weather, self.speed_limit * .05)
        return self.length / (speed / 3.6)


class Graph:
    def __init__(self):
        self.vertices: dict[int, Vertex] = {}
        self.adj: dict[int, dict[int, Edge]] = {}
        self.radj: dict[int, dict[int, Edge]] = {}
        self.v_max = 40.0

    def add_vertex(self, vertex: Vertex):
        self.vertices[vertex.node_id] = vertex
        self.adj.setdefault(vertex.node_id, {})
        self.radj.setdefault(vertex.node_id, {})

    def add_edge(self, edge: Edge):
        old = self.adj[edge.source].get(edge.target)
        if old is not None and old.length <= edge.length:
            return
        self.adj[edge.source][edge.target] = edge
        self.radj[edge.target][edge.source] = edge
        self.v_max = max(self.v_max, edge.speed_limit)


def find_data_dir() -> Path:
    configured = os.environ.get("HCMC_DATA_DIR")
    candidates = [
        Path(configured).expanduser() if configured else None,
        ROOT / "archive", ROOT / "data", Path.home() / "Downloads" / "archive",
    ]
    needed = {"nodes.csv", "segments.csv", "segment_status.csv", "train.csv"}
    for folder in candidates:
        if folder and folder.is_dir() and needed.issubset({p.name for p in folder.glob("*.csv")}):
            return folder
    raise FileNotFoundError("Không tìm thấy archive/. Đặt HCMC_DATA_DIR trỏ đến folder dữ liệu.")


@st.cache_resource(show_spinner=False)
def load_graph(folder_text: str) -> Graph:
    folder = Path(folder_text)
    north, south, east, west = BBOX
    nodes = pd.read_csv(folder / "nodes.csv", usecols=["_id", "long", "lat"])
    nodes = nodes[nodes.lat.between(south, north) & nodes.long.between(west, east)]
    node_ids = set(nodes._id)

    segments = pd.read_csv(folder / "segments.csv", usecols=[
        "_id", "s_node_id", "e_node_id", "length", "max_velocity", "street_name", "street_type",
    ])
    segments = segments[
        segments.s_node_id.isin(node_ids) & segments.e_node_id.isin(node_ids)
        & segments.street_type.isin(SPEEDS) & (segments.length > 0)
    ]
    status = pd.read_csv(folder / "segment_status.csv", usecols=["updated_at", "segment_id", "velocity"])
    latest_velocity = status.sort_values("updated_at").groupby("segment_id").velocity.last().to_dict()
    train = pd.read_csv(folder / "train.csv", usecols=["segment_id", "LOS"])
    los = train.groupby("segment_id").LOS.agg(
        lambda values: values.mode().iat[0] if not values.mode().empty else "A"
    ).to_dict()

    used = set(segments.s_node_id) | set(segments.e_node_id)
    coords = {
        int(node_id): (float(lon), float(lat))
        for node_id, lon, lat in nodes[nodes._id.isin(used)].itertuples(index=False, name=None)
    }
    graph = Graph()
    for node_id, (lon, lat) in coords.items():
        graph.add_vertex(Vertex(node_id, lon, lat))
    for sid, source, target, length, limit, name, road_type in segments.itertuples(index=False, name=None):
        sid, source, target = int(sid), int(source), int(target)
        speed = SPEEDS[str(road_type)] if pd.isna(limit) or limit <= 0 else float(limit)
        measured = latest_velocity.get(sid)
        density = (
            max(0.0, min(.95, 1 - max(0.0, float(measured)) / speed))
            if measured is not None else LOS_DENSITY.get(str(los.get(sid, "A")), .18)
        )
        graph.add_edge(Edge(sid, source, target, float(length), speed,
                            "" if pd.isna(name) else str(name), density, density))
    return graph


def haversine(lat1, lon1, lat2, lon2):
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def nearest_node(graph: Graph, point: tuple[float, float]):
    lat, lon = point
    return min(graph.vertices, key=lambda node: haversine(lat, lon, graph.vertices[node].lat, graph.vertices[node].lon))


def heuristic(graph: Graph, node: int, goal: int):
    a, b = graph.vertices[node], graph.vertices[goal]
    return haversine(a.lat, a.lon, b.lat, b.lon) / (graph.v_max / 3.6)


def bidirectional_astar(graph: Graph, start: int, goal: int):
    if start == goal:
        return [start], 0.0, {"forward": {start}, "backward": {goal}}
    qf, qb, counter = [(heuristic(graph, start, goal), 0, start)], [(heuristic(graph, goal, start), 1, goal)], 2
    gf, gb, pf, pb = {start: 0.0}, {goal: 0.0}, {start: None}, {goal: None}
    cf, cb, best, meet = set(), set(), math.inf, None
    while qf and qb:
        if qf[0][0] >= best and qb[0][0] >= best:
            break
        forward = qf[0][0] <= qb[0][0]
        queue, closed, costs, parents = (qf, cf, gf, pf) if forward else (qb, cb, gb, pb)
        _, _, node = heapq.heappop(queue)
        if node in closed:
            continue
        closed.add(node)
        edges = graph.adj[node].values() if forward else graph.radj[node].values()
        for edge in edges:
            if edge.blocked:
                continue
            nxt = edge.target if forward else edge.source
            candidate = costs[node] + edge.travel_time
            if candidate >= costs.get(nxt, math.inf):
                continue
            costs[nxt], parents[nxt], counter = candidate, node, counter + 1
            destination = goal if forward else start
            heapq.heappush(queue, (candidate + heuristic(graph, nxt, destination), counter, nxt))
            other_costs = gb if forward else gf
            if nxt in other_costs and candidate + other_costs[nxt] < best:
                best, meet = candidate + other_costs[nxt], nxt
    trace = {"forward": cf, "backward": cb}
    if meet is None:
        return None, math.inf, trace
    first, node = [], meet
    while node is not None:
        first.append(node)
        node = pf[node]
    first.reverse()
    second, node = [], pb[meet]
    while node is not None:
        second.append(node)
        node = pb[node]
    return first + second, best, trace


def apply_scenario(graph: Graph, traffic: float, weather: float, incidents: float, seed: int):
    rng, blocked = random.Random(seed), {}
    for edges in graph.adj.values():
        for edge in edges.values():
            edge.density = min(.95, edge.observed_density * traffic)
            edge.weather = weather
            blocked.setdefault(edge.segment_id, rng.random() < incidents)
            edge.blocked = blocked[edge.segment_id]


def geo_line(graph: Graph, edge: Edge):
    a, b = graph.vertices[edge.source], graph.vertices[edge.target]
    return [[a.lon, a.lat], [b.lon, b.lat]]


def add_lines(map_object, lines, color, weight, opacity):
    if lines:
        folium.GeoJson({"type": "MultiLineString", "coordinates": lines}, style_function=lambda _: {
            "color": color, "weight": weight, "opacity": opacity,
        }).add_to(map_object)


def build_map(graph: Graph):
    north, south, east, west = BBOX
    map_object = folium.Map([10.789, 106.696], zoom_start=13, tiles=None, prefer_canvas=True)
    map_object.get_root().header.add_child(folium.Element("<style>.leaflet-container{background:#020508!important}</style>"))
    Fullscreen(position="bottomright").add_to(map_object)
    add_lines(map_object, [geo_line(graph, edge) for edges in graph.adj.values() for edge in edges.values()], "#21e6e6", 1.15, .58)

    trace = st.session_state.trace
    if trace:
        start, goal = st.session_state.start, st.session_state.goal
        forward = {node for node in trace["forward"] if haversine(start[0], start[1], graph.vertices[node].lat, graph.vertices[node].lon) <= 1250}
        backward = {node for node in trace["backward"] if haversine(goal[0], goal[1], graph.vertices[node].lat, graph.vertices[node].lon) <= 1250}
        add_lines(map_object, [geo_line(graph, e) for n in forward for e in graph.adj[n].values() if e.target in forward], "#b900ff", 2.1, .7)
        add_lines(map_object, [geo_line(graph, e) for n in backward for e in graph.radj[n].values() if e.source in backward], "#ff00c8", 2.1, .7)
    if st.session_state.path:
        route = [[graph.vertices[node].lat, graph.vertices[node].lon] for node in st.session_state.path]
        folium.PolyLine(route, color="#8c00ff", weight=12, opacity=.30).add_to(map_object)
        folium.PolyLine(route, color="#ff00f5", weight=4, opacity=1).add_to(map_object)
    for point, label, color in [(st.session_state.start, "Start", "#ff22f2"), (st.session_state.goal, "Goal", "#20eff2")]:
        folium.CircleMarker(point, radius=8, color="white", weight=2, fill=True, fill_color=color, fill_opacity=1, tooltip=label).add_to(map_object)
    map_object.fit_bounds([[south, west], [north, east]])
    return map_object


def get_stats(graph: Graph, path, trace):
    edges = [graph.adj[a][b] for a, b in zip(path, path[1:])]
    roads = []
    for edge in edges:
        name = edge.name or "Đường không tên"
        if not roads or roads[-1] != name:
            roads.append(name)
    return {"time": sum(e.travel_time for e in edges), "distance": sum(e.length for e in edges),
            "expanded": len(trace["forward"] | trace["backward"]), "roads": roads}


st.set_page_config(page_title="Bidirectional A* — HCMC", page_icon="✦", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;600&family=Playfair+Display:wght@500&display=swap');
html,body,[data-testid="stAppViewContainer"],.stApp{background:#020508} footer,#MainMenu,[data-testid="stToolbar"],[data-testid="stDecoration"]{display:none!important}
header[data-testid="stHeader"]{background:transparent!important;z-index:10001!important}.block-container{padding:0!important;max-width:none!important}
section[data-testid="stSidebar"]{min-width:340px!important;width:340px!important;transform:translateX(0)!important;background:rgba(2,7,11,.97);border-right:1px solid rgba(33,230,230,.28)}
section[data-testid="stSidebar"][aria-expanded="false"]{margin-left:0!important;transform:translateX(0)!important}
[data-testid="stSidebar"] *{font-family:'DM Sans',sans-serif}[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3{color:#efffff}[data-testid="stSidebar"] p,[data-testid="stSidebar"] label{color:#9bc9cc}
[data-testid="stSidebar"] .stButton button{background:linear-gradient(90deg,#aa00ff,#ff00c8);color:white;border:0;font-weight:700}
.map-title{position:fixed;top:44px;right:4vw;z-index:9999;color:white;pointer-events:none;font-family:'Playfair Display',Georgia,serif;text-shadow:0 3px 24px #000}.map-title .city{font:600 11px 'DM Sans';letter-spacing:.22em;text-transform:uppercase;color:#21e6e6;margin-bottom:10px}.map-title .main{font-size:clamp(38px,4vw,68px);line-height:.98;letter-spacing:-.04em}.map-title .sub{font-size:clamp(25px,3vw,51px);line-height:1.06;margin-top:8px}
.badge{position:fixed;left:360px;bottom:18px;z-index:9999;color:#8eeff1;background:rgba(0,8,13,.76);border:1px solid rgba(33,230,230,.3);border-radius:99px;padding:8px 13px;font:600 10px 'DM Sans';letter-spacing:.08em;pointer-events:none}.metric{color:#ddffff;border:1px solid rgba(33,230,230,.2);background:rgba(33,230,230,.05);border-radius:7px;padding:9px 11px;margin:7px 0}.metric b{float:right;color:white}@media(max-width:900px){.map-title{display:none}.badge{left:360px}} iframe{display:block;border:0!important}
</style>""", unsafe_allow_html=True)

defaults = {"start": LANDMARKS["Đại học Bách Khoa"], "goal": LANDMARKS["Landmark 81"], "start_name": "Đại học Bách Khoa", "goal_name": "Landmark 81", "path": None, "trace": None, "route_stats": None, "clicked": None, "demo_done": False}
for key, value in defaults.items():
    st.session_state.setdefault(key, value)

try:
    folder = find_data_dir()
    with st.spinner("Loading HCMC street network…"):
        graph = load_graph(str(folder))
except Exception as error:
    st.error(str(error)); st.stop()

if not st.session_state.demo_done:
    apply_scenario(graph, 1.0, 1.0, 0.0, 42)
    path, _, trace = bidirectional_astar(graph, nearest_node(graph, st.session_state.start), nearest_node(graph, st.session_state.goal))
    st.session_state.path, st.session_state.trace = path, trace
    st.session_state.route_stats = get_stats(graph, path, trace) if path else None
    st.session_state.demo_done = True

with st.sidebar:
    st.markdown("## Route explorer")
    st.caption("Bidirectional A* using the supplied HCMC CSV data.")
    st.write(f"**{len(graph.vertices):,}** nodes · **{sum(map(len, graph.adj.values())):,}** edges")
    click_mode = st.radio("Map click sets", ["Start", "Goal"], horizontal=True)
    names = list(LANDMARKS)
    start_name = st.selectbox("Start", names, index=names.index(st.session_state.start_name) if st.session_state.start_name in names else 0)
    goal_name = st.selectbox("Goal", names, index=names.index(st.session_state.goal_name) if st.session_state.goal_name in names else 1)
    if st.button("Use selected places", use_container_width=True):
        st.session_state.start, st.session_state.goal = LANDMARKS[start_name], LANDMARKS[goal_name]
        st.session_state.start_name, st.session_state.goal_name = start_name, goal_name
        st.session_state.path = st.session_state.trace = st.session_state.route_stats = None
    traffic = st.slider("Traffic intensity", .6, 1.6, 1., .05)
    weather_name = st.select_slider("Weather", ["Clear", "Light rain", "Heavy rain"], value="Clear")
    incidents = st.slider("Incident probability", 0., .08, 0., .01)
    seed = st.number_input("Scenario seed", 0, 9999, 42)
    if st.button("Run Bidirectional A*", type="primary", use_container_width=True):
        weather = {"Clear": 1.0, "Light rain": .82, "Heavy rain": .64}[weather_name]
        apply_scenario(graph, traffic, weather, incidents, int(seed))
        path, _, trace = bidirectional_astar(graph, nearest_node(graph, st.session_state.start), nearest_node(graph, st.session_state.goal))
        st.session_state.path, st.session_state.trace = path, trace
        st.session_state.route_stats = get_stats(graph, path, trace) if path else None
        st.rerun()
    if st.session_state.route_stats:
        info = st.session_state.route_stats
        st.markdown("### Route result")
        st.markdown(f'<div class="metric">Travel time <b>{info["time"]/60:.1f} min</b></div><div class="metric">Distance <b>{info["distance"]/1000:.2f} km</b></div><div class="metric">Expanded <b>{info["expanded"]:,}</b></div>', unsafe_allow_html=True)
        with st.expander("Road sequence"):
            for index, road in enumerate(info["roads"], 1):
                st.caption(f"{index:02d}  {road}")

st.markdown('<div class="map-title"><div class="city">Ho Chi Minh City · Traffic Network</div><div class="main">Bidirectional A*</div><div class="sub">Heuristic: Great-circle Distance</div></div>', unsafe_allow_html=True)
output = st_folium(build_map(graph), height=850, use_container_width=True, returned_objects=["last_clicked"], key="hcmc-dark-map")
st.markdown(f'<div class="badge">CYAN · STREET NETWORK&nbsp;&nbsp;&nbsp; MAGENTA · SEARCH + ROUTE&nbsp;&nbsp;&nbsp; {len(graph.vertices):,} NODES</div>', unsafe_allow_html=True)

clicked = output.get("last_clicked") if output else None
if clicked:
    signature = (round(clicked["lat"], 6), round(clicked["lng"], 6), click_mode)
    if signature != st.session_state.clicked:
        point = (clicked["lat"], clicked["lng"])
        if click_mode == "Start":
            st.session_state.start, st.session_state.start_name = point, "Custom map point"
        else:
            st.session_state.goal, st.session_state.goal_name = point, "Custom map point"
        st.session_state.clicked = signature
        st.session_state.path = st.session_state.trace = st.session_state.route_stats = None
        st.rerun()
