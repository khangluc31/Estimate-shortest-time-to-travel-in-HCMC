# Traffic Graph and Scenario Generator

## 1. Overview

This module builds a road graph from **OpenStreetMap** using OSMnx and assigns a simulated traffic scenario to the roads.

The module has two main responsibilities:

1. **Build the road network**

   * Download a drivable road network from OpenStreetMap.
   * Convert OSM nodes into `Vertex` objects.
   * Convert OSM road segments into `Edge` objects.
   * Store the graph in a custom `Graph` structure.

2. **Generate traffic scenarios**

   * Assign a baseline traffic density to every road.
   * Increase density around predefined congestion hotspots.
   * Add small random variations between roads.
   * Randomly block some roads to simulate incidents.

The resulting graph can then be used by the A* pathfinding algorithm.

---

# 2. Overall Data Flow

```text
OpenStreetMap
     │
     ▼
OSMnx graph_from_bbox()
     │
     ▼
load_graph()
     │
     ├── Vertex objects
     └── Edge objects
              │
              ▼
       ScenarioGenerator
              │
       ┌──────┴─────────┐
       ▼                ▼
  Traffic density   Road incidents
       │                │
       └──────┬─────────┘
              ▼
        Updated Graph
              │
              ▼
        A* pathfinding
```

---

# 3. Constants

```python
DEFAULT_SPEED = 40
MIN_SPEED_FACTOR = 0.05
```

### `DEFAULT_SPEED`

Default road speed in km/h when OpenStreetMap does not provide a `maxspeed` value.

For example:

```text
OSM maxspeed exists → use OSM value
OSM maxspeed missing → use 40 km/h
```

### `MIN_SPEED_FACTOR`

Prevents a road's calculated speed from reaching zero.

The minimum speed is:

```text
minimum speed = speed_limit × 0.05
```

This is important because `travel_time` divides by the current speed.

---

# 4. Vertex

```python
@dataclass
class Vertex:
    id: int
    x: float
    y: float
```

Represents a node/intersection in the road network.

| Attribute | Meaning               |
| --------- | --------------------- |
| `id`      | OpenStreetMap node ID |
| `x`       | Longitude             |
| `y`       | Latitude              |

Example:

```python
Vertex(
    id=123456,
    x=106.7000,
    y=10.7800
)
```

---

# 5. Edge

```python
@dataclass
class Edge:
    source: int
    target: int
    length: float
    speed_limit: float
    oneway: bool = False
    name: str = ""
    geometry: list = field(default_factory=list)
    density: float = 0.0
    weather_factor: float = 1.0
    blocked: bool = False
```

An `Edge` represents a directed road segment from one vertex to another.

## Main attributes

| Attribute        |        Unit | Meaning                               |
| ---------------- | ----------: | ------------------------------------- |
| `source`         |     node ID | Starting vertex                       |
| `target`         |     node ID | Ending vertex                         |
| `length`         |      meters | Road length                           |
| `speed_limit`    |        km/h | Maximum/legal speed used by the model |
| `oneway`         |     boolean | Whether the road is one-way           |
| `name`           |        text | Road name                             |
| `geometry`       | coordinates | Shape used for displaying the road    |
| `density`        |         0–1 | Traffic density                       |
| `weather_factor` |         0–1 | Weather multiplier                    |
| `blocked`        |     boolean | Whether the road is unavailable       |

---

# 6. Traffic Density

Density is represented as a value between `0` and `1`.

```text
0.0 → essentially empty
0.2 → light traffic
0.5 → moderate traffic
0.8 → heavy congestion
1.0 → maximum congestion
```

The current scenario generator intentionally keeps the maximum below `1.0`.

For example:

```python
max_density = 0.9
```

This prevents almost every congested road from immediately reaching the minimum speed floor.

---

# 7. Current Speed

The current speed of an edge is calculated using:

```python
speed = speed_limit * (1 - density)
speed *= weather_factor
```

Then:

```python
max(
    speed,
    speed_limit * MIN_SPEED_FACTOR
)
```

Therefore:

```text
current_speed
    = speed_limit
      × (1 - density)
      × weather_factor
```

subject to the minimum speed constraint.

### Example

Suppose:

```text
speed_limit = 40 km/h
density = 0.5
weather_factor = 0.8
```

Then:

```text
40 × (1 - 0.5) × 0.8
= 16 km/h
```

So the road is currently estimated to travel at `16 km/h`.

---

# 8. Travel Time

`travel_time` returns the estimated travel time in seconds.

```python
travel_time = length / (current_speed / 3.6)
```

The conversion:

```text
km/h ÷ 3.6 = m/s
```

is necessary because `length` is stored in meters.

### Example

For:

```text
length = 500 m
current_speed = 20 km/h
```

we get:

```text
20 / 3.6 = 5.56 m/s

500 / 5.56 ≈ 90 seconds
```

If an edge is blocked:

```python
travel_time = math.inf
```

This makes it unusable by the routing algorithm.

---

# 9. Graph

The custom `Graph` class stores the road network.

```python
class Graph:
    self.vertices
    self.adj
    self.radj
    self.v_max
```

## `vertices`

```python
id -> Vertex
```

Stores all nodes.

## `adj`

```text
source → target → Edge
```

Used for forward graph traversal.

Example:

```text
A → B
A → C
B → D
```

## `radj`

Reverse adjacency structure:

```text
target → source → Edge
```

This is useful when performing a backward search from the destination.

## `v_max`

Maximum speed limit found in the graph.

```python
self.v_max = max(self.v_max, e.speed_limit)
```

This is particularly important for the A* heuristic.

For a time-based heuristic:

```text
h(n) = distance(n, goal) / v_max
```

`v_max` provides the fastest possible speed assumed by the heuristic.

---

# 10. Loading the OSM Graph

```python
load_graph(bbox)
```

This function downloads a drivable road network from OpenStreetMap:

```python
G = ox.graph_from_bbox(
    bbox,
    network_type="drive"
)
```

It then converts the OSMnx graph into the custom `Graph` structure.

## Processing steps

```text
OSMnx graph
    │
    ├── Nodes → Vertex
    │
    └── Edges → Edge
             │
             ├── length
             ├── maxspeed
             ├── name
             └── geometry
```

---

# 11. Speed Parsing

```python
parse_maxspeed(raw)
```

OpenStreetMap speed values are not always stored in one consistent format.

The function handles values such as:

```text
40
40 km/h
40;50
30 mph
```

and converts mph to km/h.

If no usable speed is found:

```python
DEFAULT_SPEED
```

is returned.

For multiple speed values, the smallest value is currently used.

---

# 12. Road Geometry

Each edge stores its geometry as:

```python
[(latitude, longitude), ...]
```

This is used when drawing the calculated route on a map.

If OSM does not provide explicit geometry, the module creates a straight line between the source and target vertices.

---

# 13. Parallel Edges

OpenStreetMap can contain multiple edges between the same pair of nodes.

For example:

```text
A ───────→ B
A ──→ B
```

The current implementation keeps the shortest edge:

```python
if old is None or edge.length < old.length:
    g.add_edge(edge)
```

This simplifies the graph structure to:

```python
adj[source][target] = Edge
```

rather than keeping multiple parallel edges.

---

# 14. One-Way Roads

The graph stores roads as directed edges.

For a two-way road:

```text
A → B
B → A
```

For a one-way road:

```text
A → B
```

The `_roads()` helper identifies physical roads so that scenario values can be assigned consistently.

For a two-way road, both directions receive the same:

```text
density
blocked status
```

This prevents a single physical road from having contradictory traffic conditions in each direction.

---

# 15. Hotspots

A hotspot represents an area with increased traffic congestion.

```python
@dataclass
class Hotspot:
    lat: float
    lon: float
    intensity: float = 1.0
    radius: float = 500.0
```

Example:

```python
Hotspot(
    lat=10.780,
    lon=106.700,
    intensity=1.0,
    radius=500
)
```

The hotspot does not directly assign one density value to every road.

Instead, its influence decreases with distance.

---

# 16. Hotspot Influence

The function:

```python
hotspot_intensity(distance, hotspot)
```

uses a Gaussian-style falloff:

```text
influence =
    intensity × exp(
        -distance² / (2 × radius²)
    )
```

Therefore:

```text
distance from hotspot
        │
        ▼
     closer
        │
        ▼
higher congestion influence
```

and:

```text
distance from hotspot
        │
        ▼
      farther
        │
        ▼
lower congestion influence
```

This creates a gradual congestion zone rather than a hard boundary.

---

# 17. ScenarioConfig

`ScenarioConfig` controls how traffic scenarios are generated.

```python
@dataclass
class ScenarioConfig:
    base_density: float = 0.1
    max_density: float = 0.9
    density_deviation: float = 0.05
    block_probability: float = 0.03
    hotspots: list[Hotspot] = field(default_factory=list)
```

## Parameters

### `base_density`

Baseline traffic affecting **every road**.

Example:

```text
base_density = 0.1
```

means that even roads outside hotspots have approximately 10% baseline density.

This is important because the model does not assume that roads outside congestion hotspots are completely empty.

---

### `max_density`

Maximum density generated by the scenario.

Example:

```python
max_density = 0.9
```

The final density is clamped to:

```text
0.0 ≤ density ≤ 0.9
```

---

### `density_deviation`

Small random variation applied to every physical road.

For:

```python
density_deviation = 0.05
```

the generator adds:

```text
random value between -0.05 and +0.05
```

This prevents roads with identical hotspot influence from always having exactly the same density.

---

### `block_probability`

Probability that a physical road is blocked.

Example:

```python
block_probability = 0.03
```

means each physical road has a 3% probability of being blocked.

Blocked roads have:

```python
travel_time = math.inf
```

---

### `hotspots`

List of congestion hotspots.

Example:

```python
hotspots = [
    Hotspot(
        lat=10.780,
        lon=106.700,
        intensity=1.0,
        radius=500
    ),
    Hotspot(
        lat=10.775,
        lon=106.695,
        intensity=0.7,
        radius=300
    )
]
```

---

# 18. Density Generation

For every physical road, density is calculated approximately as:

```text
density =
    base_density
    + hotspot contribution
    + random local deviation
```

The hotspot contribution is:

```text
(max_density - base_density)
× hotspot influence
```

The final value is clamped:

```text
density = max(
    0,
    min(max_density, density)
)
```

Therefore, the intended model is:

```text
                         hotspot
                            ↓
                     high congestion
                            │
                            │
low baseline ───────────────┼────────────── low baseline
                            │
                      decreasing
                       influence
```

---

# 19. Multiple Hotspots

If several hotspots affect the same road, the current implementation uses the **strongest hotspot**:

```python
hotspot_effect = max(
    hotspot_effect,
    influence
)
```

It does **not** add hotspot influences together.

For example:

```text
Hotspot A influence = 0.7
Hotspot B influence = 0.4

final hotspot influence = 0.7
```

This prevents overlapping hotspots from automatically pushing density beyond the intended range.

---

# 20. Random Seeds

The scenario generator is deterministic for a given seed.

Example:

```python
generator = ScenarioGenerator(seed=42)
```

Running the same scenario with the same seed produces the same random density deviations and incident locations.

Changing the seed:

```python
ScenarioGenerator(seed=43)
```

produces a different scenario.

This is useful for testing because scenarios can be reproduced.

---

# 21. Applying a Scenario

Typical usage:

```python
config = ScenarioConfig(
    base_density=0.1,
    max_density=0.9,
    density_deviation=0.05,
    block_probability=0.03,
    hotspots=[
        Hotspot(
            lat=10.780,
            lon=106.700,
            intensity=1.0,
            radius=500
        )
    ]
)

generator = ScenarioGenerator(
    seed=42,
    config=config
)

generator.apply(graph)
```

After `apply()`:

```python
edge.density
edge.blocked
edge.current_speed
edge.travel_time
```

are ready to be used by the routing algorithm.

---

# 22. Scenario Examples

## Normal traffic

```python
ScenarioConfig(
    base_density=0.1,
    max_density=0.5,
    density_deviation=0.03,
    block_probability=0.01
)
```

Represents relatively light traffic with occasional variation.

---

## Rush hour

```python
ScenarioConfig(
    base_density=0.4,
    max_density=0.9,
    density_deviation=0.05,
    block_probability=0.02,
    hotspots=[...]
)
```

The higher baseline means that roads outside hotspots are still moderately busy.

Hotspots create particularly congested areas.

---

## Holiday / low traffic

```python
ScenarioConfig(
    base_density=0.03,
    max_density=0.5,
    density_deviation=0.02,
    block_probability=0.01
)
```

The entire network has lower baseline traffic, while hotspots can still represent local congestion.

---

# 23. Important Assumptions

The current traffic model is a **simulation**, not real-time traffic data.

The following values are generated or configured artificially:

```text
density
weather_factor
blocked
```

The road network itself comes from OpenStreetMap.

Therefore:

```text
OSM data
    → road geometry
    → road length
    → road names
    → speed limits when available

Scenario generator
    → traffic density
    → congestion hotspots
    → random variation
    → road incidents
```

## 24. Weather Scenario Generation

The weather scenario models the effect of weather conditions on road travel speed. Instead of assigning a completely independent weather value to every road, the scenario uses a **global weather factor** combined with optional **spatial rain cells**. This allows weather conditions to affect large areas while also representing localized regions of heavier rain.

### Weather Configuration

Weather-related parameters are stored in `ScenarioConfig`:

```python
global_weather_factor: float = 1.0
rain_cells: list[RainCell] = field(default_factory=list)
```

A `RainCell` represents a rectangular geographical area affected by rain:

```python
@dataclass
class RainCell:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    weather_factor: float = 0.6
```

The four coordinate values define the boundaries of the rain cell. `weather_factor` represents the effect of the rain on the road's travel speed.

For example:

```text
weather_factor = 1.0   → no reduction
weather_factor = 0.8   → speed reduced to 80%
weather_factor = 0.6   → speed reduced to 60%
```

Therefore, a lower weather factor represents more severe weather conditions.

### Global Weather

The `global_weather_factor` applies to every road in the scenario.

```python
weather = cfg.global_weather_factor
```

A value of `1.0` represents normal weather conditions. A value below `1.0` represents weather conditions that reduce travel speed across the entire map.

For example:

```text
global_weather_factor = 1.0
```

means that weather does not modify the normal road speed.

A value such as:

```text
global_weather_factor = 0.9
```

represents a scenario where weather conditions cause a general reduction in speed throughout the road network.

### Localized Rain Cells

To represent spatially varying weather, the scenario can contain one or more `RainCell` objects.

For each road, the generator calculates the midpoint of its geometry:

```python
lat, lon = road_midpoint(edge.geometry)
```

The midpoint is then tested against every rain cell:

```python
inside = (
    rain_cell.min_lat <= lat <= rain_cell.max_lat
    and
    rain_cell.min_lon <= lon <= rain_cell.max_lon
)
```

If the road midpoint lies inside a rain cell, the road's weather factor is multiplied by that cell's `weather_factor`:

```python
if inside:
    weather *= rain_cell.weather_factor
```

This produces a spatially varying weather scenario. Roads outside the rain cells retain the global weather factor, while roads inside affected regions receive an additional reduction.

For example, consider:

```text
global_weather_factor = 1.0

Rain Cell A:
    weather_factor = 0.6
```

A road outside the rain cell receives:

```text
weather = 1.0
```

while a road inside the rain cell receives:

```text
weather = 1.0 × 0.6 = 0.6
```

If multiple rain cells overlap, their effects are multiplied together. For example:

```text
global_weather_factor = 1.0
rain_cell_1 = 0.8
rain_cell_2 = 0.7

weather = 1.0 × 0.8 × 0.7
        = 0.56
```

This means the affected road operates at 56% of its normal weather-adjusted speed.

### Applying Weather to the Road Network

The complete weather assignment is performed by `_assign_weather()`:

```python
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
```

The `_roads()` helper ensures that a physical two-way road is processed only once. When an edge has an opposite-direction twin, the same weather factor is assigned to both directions:

```python
if twin:
    twin.weather_factor = weather
```

This keeps the weather condition consistent across both directions of the same physical road.

### Interaction with Road Speed

The generated `weather_factor` is later used when calculating the effective road speed. The speed model applies both traffic density and weather:

```python
speed = self.speed_limit * (1 - self.density)
speed *= self.weather_factor
```

Consequently, a road can be affected by both congestion and weather simultaneously.

For example, if:

```text
speed_limit = 50 km/h
density = 0.4
weather_factor = 0.6
```

then:

```text
traffic-adjusted speed
= 50 × (1 - 0.4)
= 30 km/h

weather-adjusted speed
= 30 × 0.6
= 18 km/h
```

Thus, the scenario generator does not directly assign a final travel speed. Instead, it assigns environmental conditions (`density` and `weather_factor`) that are subsequently used by the road speed model.

### Purpose of the Model

The main purpose of the weather model is to introduce **spatial variation** into the road network rather than treating weather as a single constant for the entire map.

A scenario can therefore represent conditions such as:

```text
                 Rain Cell
              ┌─────────────┐
              │  factor 0.6 │
              │             │
      ────────┼─────────────┼────────
              │             │
      ────────┼─────────────┼────────
              └─────────────┘

    Outside → global factor
    Inside  → global factor × 0.6
```

This allows different roads to experience different effective travel speeds depending on their geographic location.

The model is intentionally scenario-based rather than a real-time weather prediction system. Rain cells can be configured manually to create reproducible test cases, making it possible to evaluate how the routing algorithm behaves when adverse weather affects particular regions of the road network.
