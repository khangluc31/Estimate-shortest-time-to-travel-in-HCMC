import csv
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SPEED = 40.0       # km/h, fallback if max_velocity is missing
MIN_SPEED_FACTOR = 0.05    # prevents zero speed

# ============================================================
# GRAPH DATA
# ============================================================

@dataclass
class Vertex:
    id: int
    x: float       # longitude
    y: float       # latitude

@dataclass
class Edge:
    source: int
    target: int
    length: float                  # meters
    speed_limit: float             # km/h
    name: str = ""
    midpoint: tuple[float, float] = (0.0, 0.0)   # (lat, lon)

    # Dynamic scenario values
    density: float = 0.0           # 0 = empty, 1 = jammed
    weather_factor: float = 1.0    # 0..1
    blocked: bool = False

    @property
    def current_speed(self) -> float:
        """
        Current speed in km/h.

        Static speed_limit comes from the CSV.
        Dynamic density and weather modify it.
        """
        speed = self.speed_limit * (1.0 - self.density)
        speed *= self.weather_factor

        return max(
            speed,
            self.speed_limit * MIN_SPEED_FACTOR
        )

    @property
    def travel_time(self) -> float:
        """
        Travel time in seconds.
        Blocked edges are unreachable.
        """
        if self.blocked:
            return math.inf

        return self.length / (self.current_speed / 3.6)

class Graph:
    def __init__(self):
        self.vertices = {}   # node_id -> Vertex
        self.adj = {}        # u -> {v: Edge}
        self.radj = {}       # v -> {u: Edge}

        # Maximum static speed limit in the graph.
        # Used by the A* heuristic.
        self.v_max = 0.0

    def add_vertex(self, vertex: Vertex):
        self.vertices[vertex.id] = vertex

        self.adj.setdefault(vertex.id, {})
        self.radj.setdefault(vertex.id, {})

    def add_edge(self, edge: Edge):
        self.adj[edge.source][edge.target] = edge
        self.radj[edge.target][edge.source] = edge

        self.v_max = max(
            self.v_max,
            edge.speed_limit
        )

    def neighbors(self, u):
        """Outgoing edges from u."""
        return self.adj[u].values()

    def predecessors(self, v):
        """Incoming edges to v."""
        return self.radj[v].values()

    def get_edge(self, u, v):
        return self.adj[u].get(v)

# ============================================================
# CSV HELPERS
# ============================================================

def load_nodes(nodes_path: Path) -> dict[int, Vertex]:
    """
    Load vertices from nodes.csv.

    Expected columns:
        _id, long, lat
    """

    vertices = {}

    with nodes_path.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:
            node_id = int(row["_id"])
            lon = float(row["long"])
            lat = float(row["lat"])

            vertices[node_id] = Vertex(
                id=node_id,
                x=lon,
                y=lat
            )

    return vertices

def validate_bbox(bbox):
    """
    Validate a bounding box in the form:

        (north, south, east, west)

    Returns:
        (north, south, east, west)
    """

    if bbox is None:
        return None

    if len(bbox) != 4:
        raise ValueError(
            "bbox must contain exactly 4 values: "
            "(north, south, east, west)"
        )

    try:
        north, south, east, west = map(float, bbox)
    except (TypeError, ValueError):
        raise ValueError(
            "bbox values must be numeric: "
            "(north, south, east, west)"
        )

    # --------------------------------------------------------
    # Check for NaN / infinity
    # --------------------------------------------------------

    values = (north, south, east, west)

    if not all(math.isfinite(value) for value in values):
        raise ValueError(
            "bbox values must be finite numbers."
        )

    # --------------------------------------------------------
    # Latitude constraints
    # --------------------------------------------------------

    if not (-90 <= south <= 90):
        raise ValueError(
            f"Invalid south latitude: {south}"
        )

    if not (-90 <= north <= 90):
        raise ValueError(
            f"Invalid north latitude: {north}"
        )

    if north <= south:
        raise ValueError(
            f"Invalid latitude range: "
            f"north ({north}) must be greater than "
            f"south ({south})."
        )

    # --------------------------------------------------------
    # Longitude constraints
    # --------------------------------------------------------

    if not (-180 <= west <= 180):
        raise ValueError(
            f"Invalid west longitude: {west}"
        )

    if not (-180 <= east <= 180):
        raise ValueError(
            f"Invalid east longitude: {east}"
        )

    if east <= west:
        raise ValueError(
            f"Invalid longitude range: "
            f"east ({east}) must be greater than "
            f"west ({west})."
        )

    return north, south, east, west

def load_graph(data_dir, bbox=None) -> Graph:
    """
    Build the routing graph from the supplied CSV data.

    Required:
        nodes.csv
        segments.csv

    Optional:
        bbox = (north, south, east, west)

    Only nodes that participate in at least one valid retained
    segment are added to the graph.
    """

    data_dir = Path(data_dir)

    # --------------------------------------------------------
    # 1. Validate BBOX
    # --------------------------------------------------------

    bbox = validate_bbox(bbox)

    nodes_path = data_dir / "nodes.csv"
    segments_path = data_dir / "segments.csv"

    if not nodes_path.exists():
        raise FileNotFoundError(
            f"Missing nodes.csv: {nodes_path}"
        )

    if not segments_path.exists():
        raise FileNotFoundError(
            f"Missing segments.csv: {segments_path}"
        )

    graph = Graph()

    # --------------------------------------------------------
    # 2. Load nodes
    # --------------------------------------------------------

    vertices = load_nodes(nodes_path)

    # --------------------------------------------------------
    # 3. Spatial filtering
    # --------------------------------------------------------

    if bbox is not None:

        north, south, east, west = bbox

        vertices = {
            node_id: vertex
            for node_id, vertex in vertices.items()
            if (
                south <= vertex.y <= north
                and
                west <= vertex.x <= east
            )
        }

        if not vertices:
            raise ValueError(
                "The specified bbox contains no nodes "
                "from nodes.csv."
            )

    # --------------------------------------------------------
    # 4. Load directed segments
    #
    # IMPORTANT:
    # Do NOT add vertices yet.
    #
    # First determine which nodes actually participate
    # in valid road segments.
    # --------------------------------------------------------

    valid_edges = []
    used_nodes = set()

    with segments_path.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            source = int(row["s_node_id"])
            target = int(row["e_node_id"])

            # ------------------------------------------------
            # Endpoints must both be inside the BBOX.
            # ------------------------------------------------

            if (
                source not in vertices
                or target not in vertices
            ):
                continue

            # ------------------------------------------------
            # Ignore self-loops.
            # ------------------------------------------------

            if source == target:
                continue

            # ------------------------------------------------
            # Validate length.
            # ------------------------------------------------

            try:
                length = float(row["length"])
            except (TypeError, ValueError):
                continue

            if not math.isfinite(length) or length <= 0:
                continue

            # ------------------------------------------------
            # Speed limit.
            # ------------------------------------------------

            raw_speed = row.get("max_velocity", "")

            try:
                speed_limit = float(raw_speed)
            except (TypeError, ValueError):
                speed_limit = DEFAULT_SPEED

            if (
                not math.isfinite(speed_limit)
                or speed_limit <= 0
            ):
                speed_limit = DEFAULT_SPEED

            # ------------------------------------------------
            # Road name.
            # ------------------------------------------------

            raw_name = row.get("street_name", "")

            if raw_name is None:
                name = ""
            else:
                name = raw_name.strip()

            if name.lower() in {"", "nan", "none"}:
                name = ""

            # ------------------------------------------------
            # Midpoint
            # midpoint = (latitude, longitude)
            # ------------------------------------------------

            source_vertex = vertices[source]
            target_vertex = vertices[target]

            midpoint = (
                (source_vertex.y + target_vertex.y) / 2.0,
                (source_vertex.x + target_vertex.x) / 2.0
            )

            # ------------------------------------------------
            # Create edge.
            # ------------------------------------------------

            edge = Edge(
                source=source,
                target=target,
                length=length,
                speed_limit=speed_limit,
                name=name,
                midpoint=midpoint
            )

            valid_edges.append(edge)

    # --------------------------------------------------------
    # 5. Keep only the shortest parallel edge per
    #    source -> target pair.
    # --------------------------------------------------------

    shortest_edges = {}

    for edge in valid_edges:

        key = (edge.source, edge.target)

        old = shortest_edges.get(key)

        if old is None or edge.length < old.length:
            shortest_edges[key] = edge

    # --------------------------------------------------------
    # 6. Determine which nodes actually participate in the
    #    final graph.
    # --------------------------------------------------------

    for edge in shortest_edges.values():
        used_nodes.add(edge.source)
        used_nodes.add(edge.target)

    # --------------------------------------------------------
    # 7. Add ONLY nodes used by retained edges.
    # --------------------------------------------------------

    for node_id in used_nodes:
        graph.add_vertex(vertices[node_id])

    # --------------------------------------------------------
    # 8. Add retained edges.
    # --------------------------------------------------------

    for edge in shortest_edges.values():
        graph.add_edge(edge)

    # --------------------------------------------------------
    # 9. Validate resulting graph.
    # --------------------------------------------------------

    edge_count = sum(
        len(targets)
        for targets in graph.adj.values()
    )

    if edge_count == 0:
        raise ValueError(
            "The selected bbox contains nodes but no valid "
            "road segments."
        )

    return graph

# ============================================================
# SCENARIO MODEL
# ============================================================

@dataclass
class Hotspot:
    lat: float
    lon: float
    intensity: float = 1.0
    radius: float = 500.0

def hotspot_intensity(distance, hotspot):
    return hotspot.intensity * math.exp(
        -(distance ** 2) /
        (2 * hotspot.radius ** 2)
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
    max_density: float = 0.9
    density_deviation: float = 0.05
    hotspots: list[Hotspot] = field(default_factory=list)

    block_probability: float = 0.01

    global_weather_factor: float = 1.0
    rain_cells: list[RainCell] = field(default_factory=list)

def roads(graph):
    """
    Return each physical road once.

    If the graph contains both:

        A -> B
        B -> A

    they share the same scenario values.

    If only one direction exists, it is treated as
    a one-way road automatically.
    """

    seen = set()

    for u, targets in graph.adj.items():
        for v, edge in targets.items():

            road_key = frozenset((u, v))

            if road_key in seen:
                continue

            seen.add(road_key)

            twin = graph.adj.get(v, {}).get(u)

            yield edge, twin, edge.midpoint

class ScenarioGenerator:

    def __init__(
        self,
        seed: int,
        config: ScenarioConfig | None = None
    ):
        self.seed = seed
        self.config = config or ScenarioConfig()

    def apply(self, graph: Graph) -> None:
        """
        Generate one traffic/weather/incident snapshot.
        """

        road_list = list(roads(graph))

        self._assign_density(road_list)
        self._assign_incidents(road_list)
        self._assign_weather(road_list)

    def _assign_density(self, roads) -> None:

        rng = random.Random(self.seed)
        cfg = self.config

        for edge, twin, midpoint in roads:

            lat, lon = midpoint

            density = cfg.base_density

            # ----------------------------------------------
            # Congestion hotspot influence
            # ----------------------------------------------

            hotspot_effect = 0.0

            for hotspot in cfg.hotspots:

                distance = haversine_m(
                    lat,
                    lon,
                    hotspot.lat,
                    hotspot.lon
                )

                influence = hotspot_intensity(
                    distance,
                    hotspot
                )

                hotspot_effect = max(
                    hotspot_effect,
                    influence
                )

            density += (
                cfg.max_density - cfg.base_density
            ) * hotspot_effect

            # ----------------------------------------------
            # Small local random variation
            # ----------------------------------------------

            density += rng.uniform(
                -cfg.density_deviation,
                cfg.density_deviation
            )

            density = max(
                0.0,
                min(cfg.max_density, density)
            )

            edge.density = density

            if twin is not None:
                twin.density = density

    def _assign_incidents(self, roads) -> None:

        rng = random.Random(
            f"incidents-{self.seed}"
        )

        for edge, twin, _ in roads:

            blocked = (
                rng.random()
                < self.config.block_probability
            )

            edge.blocked = blocked

            if twin is not None:
                twin.blocked = blocked

    def _assign_weather(self, roads) -> None:

        cfg = self.config

        for edge, twin, midpoint in roads:

            lat, lon = midpoint

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

            if twin is not None:
                twin.weather_factor = weather

# ============================================================
# GEOGRAPHIC UTILITIES
# ============================================================

def haversine_m(
    lat1,
    lon1,
    lat2,
    lon2
) -> float:
    """Great-circle distance in meters."""

    R = 6371000.0

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)

    dp = p2 - p1
    dl = math.radians(lon2 - lon1)

    a = (
        math.sin(dp / 2) ** 2
        +
        math.cos(p1)
        * math.cos(p2)
        * math.sin(dl / 2) ** 2
    )

    return 2 * R * math.asin(
        math.sqrt(a)
    )

def nearest_node(graph: Graph, point):
    """
    Find the graph node geographically closest to
    (latitude, longitude).
    """

    lat, lon = point

    return min(
        graph.vertices,
        key=lambda node: haversine_m(
            lat,
            lon,
            graph.vertices[node].y,
            graph.vertices[node].x
        )
    )