from __future__ import annotations

import folium
import streamlit as st
import math
from pathlib import Path
from folium.plugins import Fullscreen
from streamlit_folium import st_folium

from Data_preprocessing import (
    Graph,
    load_graph,
    ScenarioConfig,
    ScenarioGenerator,
    Hotspot,
    RainCell,
    haversine_m,
    nearest_node,
    roads
)

from Astar import bidirection_Astar

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "archive"
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

@st.cache_resource(show_spinner=False)
def get_graph(bbox):
    return load_graph(DATA_DIR, bbox)

def geo_line(graph: Graph, edge):
    source = graph.vertices[edge.source]
    target = graph.vertices[edge.target]

    return [
        [source.x, source.y],
        [target.x, target.y]
    ]

def add_lines(map_object, lines, color, weight, opacity):
    if lines:
        folium.GeoJson(
            {
                "type": "MultiLineString",
                "coordinates": lines
            },
            style_function=lambda _: {
                "color": color,
                "weight": weight,
                "opacity": opacity,
            }
        ).add_to(map_object)

def create_map(bbox):
    north, south, east, west = bbox

    center_lat = (north + south) / 2
    center_lon = (east + west) / 2

    map_object = folium.Map(
        [center_lat, center_lon],
        zoom_start=13,
        tiles=None,
        prefer_canvas=True
    )

    map_object.get_root().header.add_child(
        folium.Element(
            "<style>.leaflet-container{background:#020508!important}</style>"
        )
    )

    Fullscreen(position="bottomright").add_to(map_object)

    map_object.fit_bounds([[south, west], [north, east]])
    return map_object

def add_network(map_object, graph):
    lines = [
        geo_line(graph, edge)
        for edges in graph.adj.values()
        for edge in edges.values()
    ]

    add_lines(
        map_object,
        lines,
        "#21e6e6",
        1.15,
        0.58
    )

def bearing_degrees(source, target):
    lat1 = math.radians(source.y)
    lat2 = math.radians(target.y)

    d_lon = math.radians(target.x - source.x)

    x = math.sin(d_lon) * math.cos(lat2)
    y = (
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    )

    return (math.degrees(math.atan2(x, y)) + 360) % 360

def add_one_way_roads(map_object, graph, min_gap_m=120, min_edge_m=40):
    M_PER_DEG = 111_320
    cell = min_gap_m / M_PER_DEG          # grid cell size in degrees
    occupied = set()

    shafts, heads = [], []

    for edge, twin, midpoint in roads(graph):
        if twin is not None:
            continue

        s = graph.vertices[edge.source]
        t = graph.vertices[edge.target]

        mid_lat, mid_lon = midpoint
        k = math.cos(math.radians(mid_lat))

        # direction in metric-ish space (x scaled by cos(lat))
        vx = (t.x - s.x) * k
        vy = (t.y - s.y)
        length_m = math.hypot(vx, vy) * M_PER_DEG
        if length_m < min_edge_m:
            continue

        # thinning: one arrow per grid cell
        key = (int(mid_lat / cell), int(mid_lon / cell))
        if key in occupied:
            continue
        occupied.add(key)

        vx, vy = vx / math.hypot(vx, vy), vy / math.hypot(vx, vy)

        L = 0.00012          # half shaft length (deg of latitude)
        H = L * 0.6          # head length
        W = L * 0.35         # head half-width

        def pt(ax, ay):      # metric offset -> lon/lat
            return [mid_lon + ax / k, mid_lat + ay]

        tail = pt(-vx * L, -vy * L)
        tip  = pt( vx * L,  vy * L)

        left  = pt(vx * (L - H) - vy * W, vy * (L - H) + vx * W)
        right = pt(vx * (L - H) + vy * W, vy * (L - H) - vx * W)

        shafts.append([tail, tip])
        heads.append([left, tip, right])   # one polyline, not two

    add_lines(map_object, shafts + heads, "#00ff66", 2.0, 0.9)

def add_search_trace(map_object, graph: Graph):
    trace = st.session_state.trace

    if not trace:
        return

    start = st.session_state.start
    goal = st.session_state.goal

    forward = {
        node
        for node in trace["forward"]
        if haversine_m(
            start[0],
            start[1],
            graph.vertices[node].y,
            graph.vertices[node].x
        ) <= 1250
    }

    backward = {
        node
        for node in trace["backward"]
        if haversine_m(
            goal[0],
            goal[1],
            graph.vertices[node].y,
            graph.vertices[node].x
        ) <= 1250
    }

    forward_lines = [
        geo_line(graph, edge)
        for node in forward
        for edge in graph.adj[node].values()
        if edge.target in forward
    ]

    backward_lines = [
        geo_line(graph, edge)
        for node in backward
        for edge in graph.radj[node].values()
        if edge.source in backward
    ]

    add_lines(
        map_object,
        forward_lines,
        "#b900ff",
        2.1,
        .7
    )

    add_lines(
        map_object,
        backward_lines,
        "#ff00c8",
        2.1,
        .7
    )

def add_route(map_object, graph: Graph):
    if not st.session_state.path:
        return

    route = [
        [graph.vertices[node].y, graph.vertices[node].x]
        for node in st.session_state.path
    ]

    folium.PolyLine(
        route,
        color="#8c00ff",
        weight=12,
        opacity=.30
    ).add_to(map_object)

    folium.PolyLine(
        route,
        color="#ff00f5",
        weight=4,
        opacity=1
    ).add_to(map_object)

def add_markers(map_object):
    for point, label, color in [
        (st.session_state.start, "Start", "#ff22f2"),
        (st.session_state.goal, "Goal", "#20eff2")
    ]:
        folium.CircleMarker(
            point,
            radius=8,
            color="white",
            weight=2,
            fill=True,
            fill_color=color,
            fill_opacity=1,
            tooltip=label
        ).add_to(map_object)

def add_hotspots(map_object, hotspots):
    for hotspot in hotspots:

        folium.Circle(
            location=[hotspot.lat, hotspot.lon],
            radius=hotspot.radius,
            color="#ff0000",
            weight=2,
            fill=True,
            fill_color="#ff0000",
            fill_opacity=0.25,
            tooltip=(
                f"Traffic hotspot · "
                f"intensity {hotspot.intensity:.2f}"
            ),
        ).add_to(map_object)

def add_rain_cells(map_object, rain_cells):
    for cell in rain_cells:
        bounds = [
            [cell.min_lat, cell.min_lon],
            [cell.max_lat, cell.max_lon],
        ]

        folium.Rectangle(
            bounds=bounds,
            tooltip=f"Rain · factor {cell.weather_factor:.2f}",
        ).add_to(map_object)

def add_blocked_roads(map_object, graph: Graph):
    lines = [
        geo_line(graph, edge)
        for edge, _, _ in roads(graph)
        if edge.blocked
    ]

    add_lines(
        map_object,
        lines,
        "#ff3b30",
        4,
        0.9
    )

def add_scenario(map_object, graph: Graph, config: ScenarioConfig):
    add_hotspots(map_object, config.hotspots)
    add_rain_cells(map_object, config.rain_cells)
    add_blocked_roads(map_object, graph)

def build_map(graph, config, show_directions=False):
    map_object = create_map(BBOX)

    add_network(map_object, graph)
    add_one_way_roads(map_object, graph)

    add_scenario(map_object, graph, config)
    add_search_trace(map_object, graph)
    add_route(map_object, graph)
    add_markers(map_object)

    return map_object

def run_route(graph, generator):
    generator.apply(graph)

    print("========== SCENARIO ==========")
    print("BASE DENSITY:", generator.config.base_density)
    print("MAX DENSITY:", generator.config.max_density)
    print("DENSITY DEVIATION:", generator.config.density_deviation)
    print("HOTSPOTS:", len(generator.config.hotspots))
    print("BLOCK PROBABILITY:", generator.config.block_probability)
    print("GLOBAL WEATHER:", generator.config.global_weather_factor)
    print("RAIN CELLS:", len(generator.config.rain_cells))

    # --------------------------------------------------------
    # Check generated edge state
    # --------------------------------------------------------

    edges = [
        edge
        for targets in graph.adj.values()
        for edge in targets.values()
    ]

    print("EDGES:", len(edges))
    print(
        "DENSITY:",
        min(edge.density for edge in edges),
        "->",
        max(edge.density for edge in edges)
    )
    print(
        "BLOCKED:",
        sum(edge.blocked for edge in edges)
    )
    print(
        "WEATHER:",
        min(edge.weather_factor for edge in edges),
        "->",
        max(edge.weather_factor for edge in edges)
    )

    # --------------------------------------------------------
    # Routing
    # --------------------------------------------------------

    start_node = nearest_node(graph, st.session_state.start)
    goal_node = nearest_node(graph, st.session_state.goal)

    print("START:", start_node)
    print("GOAL:", goal_node)
    print("START OUT:", len(graph.adj[start_node]))
    print("START IN:", len(graph.radj[start_node]))
    print("GOAL OUT:", len(graph.adj[goal_node]))
    print("GOAL IN:", len(graph.radj[goal_node]))

    path, cost, trace = bidirection_Astar(
        graph,
        start_node,
        goal_node
    )

    print("PATH:", path)
    print("COST:", cost)
    print("FORWARD:", len(trace["forward"]))
    print("BACKWARD:", len(trace["backward"]))

    st.session_state.path = path
    st.session_state.trace = trace
    st.session_state.route_stats = (
        get_stats(graph, path, trace)
        if path
        else None
    )

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

defaults = {
    "start": LANDMARKS["Đại học Bách Khoa"],
    "goal": LANDMARKS["Landmark 81"],
    "start_name": "Đại học Bách Khoa",
    "goal_name": "Landmark 81",

    "path": None,
    "trace": None,
    "route_stats": None,
    "clicked": None,
    "demo_done": False,

    "scenario_config": ScenarioConfig(
        base_density=0.1,
        max_density=0.9,
        density_deviation=0.05,

        hotspots=[
            Hotspot(
                lat=10.775,
                lon=106.700,
                radius=500,
                intensity=0.8,
            )
        ],

        block_probability=0.01,

        global_weather_factor=1.0,

        rain_cells=[
            RainCell(
                min_lat=10.771,
                max_lat=10.775,
                min_lon=106.696,
                max_lon=106.700,
                weather_factor=0.65,
            )
        ],
    ),

    "scenario_seed": 42,
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

# Give each hotspot a stable ID for its widget keys.
# This prevents editing/removing one hotspot from mixing up
# the values of the remaining hotspots.
if "hotspot_ids" not in st.session_state:
    st.session_state.hotspot_ids = list(
        range(len(st.session_state.scenario_config.hotspots))
    )

if "next_hotspot_id" not in st.session_state:
    st.session_state.next_hotspot_id = (
        max(st.session_state.hotspot_ids, default=-1) + 1
    )

if "rain_cell_ids" not in st.session_state:
    st.session_state.rain_cell_ids = list(
        range(len(st.session_state.scenario_config.rain_cells))
    )

if "next_rain_cell_id" not in st.session_state:
    st.session_state.next_rain_cell_id = (
        max(st.session_state.rain_cell_ids, default=-1) + 1
    )

try:
    with st.spinner("Loading HCMC street network…"):
        graph = get_graph(BBOX)
except Exception as error:
    st.error(str(error))
    st.stop()


# ============================================================
# Initial demo route
# ============================================================

if not st.session_state.demo_done:
    generator = ScenarioGenerator(
        seed=st.session_state.scenario_seed,
        config=st.session_state.scenario_config,
    )

    run_route(graph, generator)

    st.session_state.demo_done = True


# ============================================================
# Sidebar
# ============================================================

with st.sidebar:
    st.markdown("## Route explorer")
    st.caption("Bidirectional A* using the HCMC road network.")

    st.write(
        f"**{len(graph.vertices):,}** nodes · "
        f"**{sum(map(len, graph.adj.values())):,}** edges"
    )

    # --------------------------------------------------------
    # Route selection
    # --------------------------------------------------------

    click_mode = st.radio(
        "Map click sets",
        ["Start", "Goal"],
        horizontal=True,
    )

    names = list(LANDMARKS)

    start_name = st.selectbox(
        "Start",
        names,
        index=(
            names.index(st.session_state.start_name)
            if st.session_state.start_name in names
            else 0
        ),
    )

    goal_name = st.selectbox(
        "Goal",
        names,
        index=(
            names.index(st.session_state.goal_name)
            if st.session_state.goal_name in names
            else 1
        ),
    )

    if st.button(
        "Use selected places",
        use_container_width=True,
    ):
        st.session_state.start = LANDMARKS[start_name]
        st.session_state.goal = LANDMARKS[goal_name]

        st.session_state.start_name = start_name
        st.session_state.goal_name = goal_name

        st.session_state.path = None
        st.session_state.trace = None
        st.session_state.route_stats = None

    # --------------------------------------------------------
    # Scenario configuration
    # --------------------------------------------------------

    st.markdown("### Scenario")

    config = st.session_state.scenario_config

    base_density = st.slider(
        "Base density",
        min_value=0.0,
        max_value=1.0,
        value=float(config.base_density),
        step=0.01,
    )

    max_density = st.slider(
        "Maximum density",
        min_value=0.0,
        max_value=1.0,
        value=float(config.max_density),
        step=0.01,
    )

    density_deviation = st.slider(
        "Density deviation",
        min_value=0.0,
        max_value=0.25,
        value=float(config.density_deviation),
        step=0.01,
    )

    block_probability = st.slider(
        "Road block probability",
        min_value=0.0,
        max_value=0.10,
        value=float(config.block_probability),
        step=0.005,
    )

    global_weather_factor = st.slider(
        "Global weather factor",
        min_value=0.05,
        max_value=1.0,
        value=float(config.global_weather_factor),
        step=0.05,
    )

    # --------------------------------------------------------
    # Hotspot management
    # --------------------------------------------------------

    st.markdown("#### Traffic hotspots")

    config = st.session_state.scenario_config
    hotspot_ids = st.session_state.hotspot_ids

    remove_hotspot_id = None

    if not config.hotspots:
        st.caption("No hotspots configured.")

    for index, (hotspot, hotspot_id) in enumerate(
        zip(config.hotspots, hotspot_ids)
    ):
        with st.expander(f"Hotspot {index + 1}", expanded=True):
            col1, col2 = st.columns(2)

            with col1:
                lat = st.number_input(
                    "Latitude",
                    min_value=-90.0,
                    max_value=90.0,
                    value=float(hotspot.lat),
                    step=0.001,
                    format="%.6f",
                    key=f"hotspot_lat_{hotspot_id}",
                )

            with col2:
                lon = st.number_input(
                    "Longitude",
                    min_value=-180.0,
                    max_value=180.0,
                    value=float(hotspot.lon),
                    step=0.001,
                    format="%.6f",
                    key=f"hotspot_lon_{hotspot_id}",
                )

            radius = st.number_input(
                "Radius (meters)",
                min_value=50,
                max_value=5000,
                value=int(hotspot.radius),
                step=50,
                key=f"hotspot_radius_{hotspot_id}",
            )

            intensity = st.slider(
                "Intensity",
                min_value=0.0,
                max_value=1.0,
                value=float(hotspot.intensity),
                step=0.05,
                key=f"hotspot_intensity_{hotspot_id}",
            )

            # Keep the persistent config synchronized with
            # the hotspot widgets.
            hotspot.lat = lat
            hotspot.lon = lon
            hotspot.radius = radius
            hotspot.intensity = intensity

            if st.button(
                f"Remove hotspot {index + 1}",
                key=f"remove_hotspot_{hotspot_id}",
                use_container_width=True,
            ):
                remove_hotspot_id = hotspot_id

    # Process removal after rendering the current list.
    if remove_hotspot_id is not None:
        remove_index = hotspot_ids.index(remove_hotspot_id)

        config.hotspots.pop(remove_index)
        hotspot_ids.pop(remove_index)

        # Invalidate the previous route because the scenario changed.
        st.session_state.path = None
        st.session_state.trace = None
        st.session_state.route_stats = None

        st.rerun()

    if st.button(
        "＋ Add hotspot",
        use_container_width=True,
    ):
        new_hotspot_id = st.session_state.next_hotspot_id
        st.session_state.next_hotspot_id += 1

        config.hotspots.append(
            Hotspot(
                lat=10.775,
                lon=106.700,
                radius=500,
                intensity=0.8,
            )
        )

        hotspot_ids.append(new_hotspot_id)

        st.session_state.path = None
        st.session_state.trace = None
        st.session_state.route_stats = None

        st.rerun()

    # --------------------------------------------------------
    # Rain-cell management
    # --------------------------------------------------------

    st.markdown("#### Rain cells")

    config = st.session_state.scenario_config
    rain_cell_ids = st.session_state.rain_cell_ids

    remove_rain_cell_id = None

    if not config.rain_cells:
        st.caption("No rain cells configured.")

    for index, (rain_cell, rain_cell_id) in enumerate(
        zip(config.rain_cells, rain_cell_ids)
    ):
        with st.expander(
            f"Rain cell {index + 1}",
            expanded=True,
        ):
            st.caption("Rectangular weather region")

            col1, col2 = st.columns(2)

            with col1:
                min_lat = st.number_input(
                    "South latitude",
                    min_value=-90.0,
                    max_value=90.0,
                    value=float(rain_cell.min_lat),
                    step=0.001,
                    format="%.6f",
                    key=f"rain_min_lat_{rain_cell_id}",
                )

            with col2:
                max_lat = st.number_input(
                    "North latitude",
                    min_value=-90.0,
                    max_value=90.0,
                    value=float(rain_cell.max_lat),
                    step=0.001,
                    format="%.6f",
                    key=f"rain_max_lat_{rain_cell_id}",
                )

            col1, col2 = st.columns(2)

            with col1:
                min_lon = st.number_input(
                    "West longitude",
                    min_value=-180.0,
                    max_value=180.0,
                    value=float(rain_cell.min_lon),
                    step=0.001,
                    format="%.6f",
                    key=f"rain_min_lon_{rain_cell_id}",
                )

            with col2:
                max_lon = st.number_input(
                    "East longitude",
                    min_value=-180.0,
                    max_value=180.0,
                    value=float(rain_cell.max_lon),
                    step=0.001,
                    format="%.6f",
                    key=f"rain_max_lon_{rain_cell_id}",
                )

            weather_factor = st.slider(
                "Weather factor",
                min_value=0.05,
                max_value=1.0,
                value=float(rain_cell.weather_factor),
                step=0.05,
                key=f"rain_factor_{rain_cell_id}",
            )

            # Validate geographic bounds.
            if min_lat >= max_lat:
                st.error(
                    "South latitude must be smaller than north latitude."
                )

            if min_lon >= max_lon:
                st.error(
                    "West longitude must be smaller than east longitude."
                )

            # Keep persistent configuration synchronized.
            rain_cell.min_lat = min_lat
            rain_cell.max_lat = max_lat
            rain_cell.min_lon = min_lon
            rain_cell.max_lon = max_lon
            rain_cell.weather_factor = weather_factor

            if st.button(
                f"Remove rain cell {index + 1}",
                key=f"remove_rain_cell_{rain_cell_id}",
                use_container_width=True,
            ):
                remove_rain_cell_id = rain_cell_id


    # --------------------------------------------------------
    # Process rain-cell removal
    # --------------------------------------------------------

    if remove_rain_cell_id is not None:
        remove_index = rain_cell_ids.index(remove_rain_cell_id)

        config.rain_cells.pop(remove_index)
        rain_cell_ids.pop(remove_index)

        st.session_state.path = None
        st.session_state.trace = None
        st.session_state.route_stats = None

        st.rerun()


    # --------------------------------------------------------
    # Add rain cell
    # --------------------------------------------------------

    if st.button(
        "＋ Add rain cell",
        use_container_width=True,
    ):
        new_rain_cell_id = st.session_state.next_rain_cell_id
        st.session_state.next_rain_cell_id += 1

        config.rain_cells.append(
            RainCell(
                min_lat=10.771,
                max_lat=10.775,
                min_lon=106.696,
                max_lon=106.700,
                weather_factor=0.65,
            )
        )

        rain_cell_ids.append(new_rain_cell_id)

        st.session_state.path = None
        st.session_state.trace = None
        st.session_state.route_stats = None

        st.rerun()

    seed = st.number_input(
        "Scenario seed",
        min_value=0,
        max_value=9999,
        value=int(st.session_state.scenario_seed),
        step=1,
    )

    # --------------------------------------------------------
    # Validate scenario configuration
    # --------------------------------------------------------

    if max_density < base_density:
        st.error(
            "Maximum density must be greater than or equal "
            "to base density."
        )

    # --------------------------------------------------------
    # Run route
    # --------------------------------------------------------

    if st.button(
        "Run Bidirectional A*",
        type="primary",
        use_container_width=True,
        disabled=max_density < base_density,
    ):
        # Update the persistent configuration.
        config.base_density = base_density
        config.max_density = max_density
        config.density_deviation = density_deviation
        config.block_probability = block_probability
        config.global_weather_factor = global_weather_factor

        st.session_state.scenario_seed = int(seed)

        # Use the persistent configuration.
        generator = ScenarioGenerator(
            seed=st.session_state.scenario_seed,
            config=st.session_state.scenario_config,
        )

        run_route(graph, generator)

        st.rerun()

    # --------------------------------------------------------
    # Route result
    # --------------------------------------------------------

    if st.session_state.route_stats:
        info = st.session_state.route_stats

        st.markdown("### Route result")

        st.markdown(
            f'<div class="metric">Travel time '
            f'<b>{info["time"] / 60:.1f} min</b></div>'
            f'<div class="metric">Distance '
            f'<b>{info["distance"] / 1000:.2f} km</b></div>'
            f'<div class="metric">Expanded '
            f'<b>{info["expanded"]:,}</b></div>',
            unsafe_allow_html=True,
        )

        with st.expander("Road sequence"):
            for index, road in enumerate(info["roads"], 1):
                st.caption(f"{index:02d}  {road}")


# ============================================================
# Map
# ============================================================

st.markdown(
    '<div class="map-title">'
    '<div class="city">Ho Chi Minh City · Traffic Network</div>'
    '<div class="main">Bidirectional A*</div>'
    '<div class="sub">Heuristic: Great-circle Distance</div>'
    '</div>',
    unsafe_allow_html=True,
)

output = st_folium(
    build_map(
        graph,
        st.session_state.scenario_config,
    ),
    height=850,
    use_container_width=True,
    returned_objects=["last_clicked"],
    key="hcmc-dark-map",
)


# ============================================================
# Map legend / status
# ============================================================

st.markdown(
    f'<div class="badge">'
    f'CYAN · STREET NETWORK&nbsp;&nbsp;&nbsp; '
    f'MAGENTA · SEARCH + ROUTE&nbsp;&nbsp;&nbsp; '
    f'{len(graph.vertices):,} NODES'
    f'</div>',
    unsafe_allow_html=True,
)


# ============================================================
# Map click handling
# ============================================================

clicked = output.get("last_clicked") if output else None

if clicked:
    signature = (
        round(clicked["lat"], 6),
        round(clicked["lng"], 6),
        click_mode,
    )

    if signature != st.session_state.clicked:
        point = (
            clicked["lat"],
            clicked["lng"],
        )

        if click_mode == "Start":
            st.session_state.start = point
            st.session_state.start_name = "Custom map point"
        else:
            st.session_state.goal = point
            st.session_state.goal_name = "Custom map point"

        st.session_state.clicked = signature

        st.session_state.path = None
        st.session_state.trace = None
        st.session_state.route_stats = None

        st.rerun()