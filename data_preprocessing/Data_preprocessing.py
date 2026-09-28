import math
import re
import random
from dataclasses import dataclass, field

import osmnx as ox

DEFAULT_SPEED = 40          # km/h, used when maxspeed is missing
MIN_SPEED_FACTOR = 0.05     # keeps current_speed > 0 so travel_time never divides by zero

@dataclass
class Vertex:
    id: int
    x: float   # longitude
    y: float   # latitude

@dataclass
class Edge:
    source: int
    target: int
    length: float                  # meters
    speed_limit: float             # km/h
    oneway: bool = False
    name: str = ""
    geometry: list = field(default_factory=list)   # [(lat, lon), ...] for drawing
    density: float = 0.0           # 0 (empty) .. 1 (jammed)
    weather_factor: float = 1.0    # 0..1
    blocked: bool = False

    @property
    def current_speed(self) -> float:
        """km/h. Placeholder model: speed drops linearly with density
        (Greenshields), then weather/incident multipliers apply."""
        speed = self.speed_limit * (1 - self.density)
        speed *= self.weather_factor
        return max(speed, self.speed_limit * MIN_SPEED_FACTOR)

    @property
    def travel_time(self) -> float:
        """Seconds; infinite if the edge is blocked."""
        if self.blocked:
            return math.inf
        return self.length / (self.current_speed / 3.6)

class Graph:
    def __init__(self):
        self.vertices = {}   # id -> Vertex
        self.adj = {}        # u -> {v: Edge}   (forward search)
        self.radj = {}       # v -> {u: Edge}   (backward search)
        self.v_max = 0.0     # max speed limit in the graph, km/h

    def add_vertex(self, v: Vertex):
        self.vertices[v.id] = v
        self.adj.setdefault(v.id, {})
        self.radj.setdefault(v.id, {})

    def add_edge(self, e: Edge):
        self.adj[e.source][e.target] = e
        self.radj[e.target][e.source] = e
        self.v_max = max(self.v_max, e.speed_limit)

    def neighbors(self, u):          # outgoing edges of u
        return self.adj[u].values()

    def predecessors(self, v):       # incoming edges of v (for the backward search)
        return self.radj[v].values()

    def get_edge(self, u, v):
        return self.adj[u].get(v)

def parse_maxspeed(raw, default = DEFAULT_SPEED):
    if raw is None:
        return default
    items = raw if isinstance(raw, list) else [raw]
    speeds = []
    for item in items:
         for part in str(item).split(";"):
            m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(mph)?", part)
            if m:
                v = float(m.group(1))
                speeds.append(v * 1.609 if m.group(2) else v)
    return min(speeds) if speeds else default

def parse_name(raw) -> str:
    if raw is None:
        return ""
    return " / ".join(raw) if isinstance(raw, list) else str(raw)

def load_graph(bbox) -> Graph:
    G = ox.graph_from_bbox(bbox, network_type="drive")
    g = Graph()

    for nid, d in G.nodes(data=True):
        g.add_vertex(Vertex(nid, d["x"], d["y"]))

    for u, v, _key, d in G.edges(keys=True, data=True):
        if u == v:                       # skip self-loops
            continue
        geom = d.get("geometry")
        if geom is not None:
            coords = [(lat, lon) for lon, lat in geom.coords]
        else:
            coords = [(g.vertices[u].y, g.vertices[u].x),
                      (g.vertices[v].y, g.vertices[v].x)]
        edge = Edge(
            source=u,
            target=v,
            length=float(d["length"]),
            speed_limit=parse_maxspeed(d.get("maxspeed")),
            name=parse_name(d.get("name")),
            geometry=coords,
        )
        old = g.get_edge(u, v)           # parallel edges: keep the shortest
        if old is None or edge.length < old.length:
            g.add_edge(edge)

    for u, targets in g.adj.items():     # one-way = no edge going back
        for v, e in targets.items():
            e.oneway = u not in g.adj[v]
    return g

@dataclass
class Hotspot:
    lat: float
    lon: float
    intensity: float = 1.0
    radius: float = 500.0       # meters

def hotspot_intensity(distance, hotspot):
    return hotspot.intensity * math.exp(
        -(distance ** 2) / (2 * hotspot.radius ** 2)
    )

@dataclass
class RainCell:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    weather_factor: float = 0.6

@dataclass
class ScenarioConfig:
    base_density: float = 0.1
    max_density: float = 0.9   # keep below 1 so the speed floor is rarely hit
    density_deviation: float = 0.05
    hotspots: list[Hotspot] = field(default_factory=list)

    block_probability: float = 0.03

    global_weather_factor: float = 1.0
    rain_cells: list[RainCell] = field(default_factory=list)

def road_midpoint(geometry):
    n = len(geometry)

    if n % 2 == 1:
        return geometry[n // 2]

    (lat1, lon1), (lat2, lon2) = geometry[n // 2 - 1], geometry[n // 2]
    return (
        (lat1 + lat2) / 2,
        (lon1 + lon2) / 2
    )

def _roads(graph):
    """Yield (edge, twin) once per physical road.
    twin is the opposite direction of a two-way road, or None for a one-way road."""
    for u in sorted(graph.adj):
        for v in sorted(graph.adj[u]):
            edge = graph.adj[u][v]
            if not edge.oneway and v < u:
                continue                  # already handled from the other direction
            yield edge, (None if edge.oneway else graph.adj[v][u])


class ScenarioGenerator: #randomly assign density to edge
    def __init__(self, seed: int, config: ScenarioConfig | None = None):
        self.seed = seed
        self.config = config or ScenarioConfig()

    def apply(self, graph: Graph) -> None:
        """One call = one snapshot. Overwrites any earlier scenario on the graph."""
        self._assign_density(graph)
        self._assign_incidents(graph)
        self._assign_weather(graph)

    def _assign_density(self, graph: Graph) -> None:
        rng = random.Random(self.seed)
        cfg = self.config

        for edge, twin in _roads(graph):

            lat, lon = road_midpoint(edge.geometry)

            # General traffic level affecting every road
            density = cfg.base_density

            # Add influence from congestion hotspots
            hotspot_effect = 0.0

            for hotspot in cfg.hotspots:
                distance = haversine_m(
                    lat,
                    lon,
                    hotspot.lat,
                    hotspot.lon
                )

                influence = hotspot_intensity(distance, hotspot)

                hotspot_effect = max(
                    hotspot_effect,
                    influence
                )

            # Scale hotspot influence into density
            density += (
                cfg.max_density - cfg.base_density
            ) * hotspot_effect

            # Small random local variation
            density += rng.uniform(
                -cfg.density_deviation,
                cfg.density_deviation
            )

            # Keep density within valid range
            density = max(
                0.0,
                min(cfg.max_density, density)
            )

            edge.density = density

            if twin:
                twin.density = density

    def _assign_incidents(self, graph: Graph) -> None:
        rng = random.Random(f"incidents-{self.seed}")   # separate random stream
        for edge, twin in _roads(graph):
            edge.blocked = rng.random() < self.config.block_probability
            if twin:
                twin.blocked = edge.blocked

    def _assign_weather(self, graph: Graph) -> None:
        cfg = self.config

        for edge, twin in _roads(graph):

            lat, lon = road_midpoint(edge.geometry)

            weather = cfg.global_weather_factor

            for rain_cell in cfg.rain_cells:
                inside = (
                    rain_cell.min_lat <= lat <= rain_cell.max_lat
                    and
                    rain_cell.min_lon <= lon <= rain_cell.max_lon
                )

                if inside:
                    weather *= rain_cell.weather_factor

            edge.weather_factor = weather

            if twin:
                twin.weather_factor = weather

def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in meters."""
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))