# Traffic Graph and Scenario Generator

## 1. Overview

This module builds a traffic graph from the **Ho Chi Minh City traffic-flow dataset** provided as CSV files and generates simulated traffic, weather, and road-incident conditions for the network.

The dataset is available from Kaggle:

**Traffic Flow Data in Ho Chi Minh City, Viet Nam**

https://www.kaggle.com/datasets/thanhnguyen2612/traffic-flow-data-in-ho-chi-minh-city-viet-nam/data

The module has two main responsibilities:

1. **Build the road network**

   * Load road nodes from `nodes.csv`.
   * Load directed road segments from `segments.csv`.
   * Convert CSV records into `Vertex` and `Edge` objects.
   * Optionally restrict the graph to a geographic bounding box.
   * Remove invalid road segments.
   * Keep only the shortest parallel edge between the same source and target.
   * Store the resulting network in the custom `Graph` structure.

2. **Generate traffic scenarios**

   * Assign baseline traffic density.
   * Increase density around congestion hotspots.
   * Add small random density variations.
   * Randomly block roads to simulate incidents.
   * Apply a global weather factor.
   * Apply localized rain cells.

The resulting graph is used by the A* pathfinding algorithm to estimate travel time.

---

# 2. Overall Data Flow

```text
Kaggle Traffic Dataset
        │
        ▼
    CSV Files
        │
        ├── nodes.csv
        │
        └── segments.csv
        │
        ▼
    load_graph()
        │
        ├── Vertex objects
        │
        └── Edge objects
                │
                ▼
             Graph
                │
                ▼
       ScenarioGenerator
                │
        ┌───────┼────────────┐
        │       │            │
        ▼       ▼            ▼
     Traffic  Incidents   Weather
     density              conditions
        │       │            │
        └───────┼────────────┘
                ▼
          Updated Graph
                │
                ▼
          A* pathfinding
                │
                ▼
       Estimated travel time
```

The module performs all data loading locally from the supplied CSV files. No external road-network API is required at runtime.

---

# 3. Input CSV Files

The graph loader uses two CSV files:

```text
nodes.csv
segments.csv
```

## 3.1 `nodes.csv`

`nodes.csv` contains the geographic nodes of the road network.

The loader expects the following columns:

```text
_id
long
lat
```

These values are converted into `Vertex` objects.

For example:

```text
_id       long        lat
123456    106.7000    10.7800
```

becomes:

```python
Vertex(
    id=123456,
    x=106.7000,
    y=10.7800
)
```

The coordinate convention used by the program is:

```text
x = longitude
y = latitude
```

---

## 3.2 `segments.csv`

`segments.csv` contains the directed road segments connecting the nodes.

The loader uses the following fields:

```text
s_node_id
e_node_id
length
max_velocity
street_name
```

These are mapped to the `Edge` representation:

```text
s_node_id     → source
e_node_id     → target
length        → length
max_velocity  → speed_limit
street_name   → name
```

The source and target define the direction of the edge:

```text
source → target
```

For example:

```text
s_node_id = 100
e_node_id = 200
```

creates:

```text
100 → 200
```

---

# 4. Constants

```python
DEFAULT_SPEED = 40.0
MIN_SPEED_FACTOR = 0.05
```

## `DEFAULT_SPEED`

`DEFAULT_SPEED` is the fallback speed limit in km/h when `max_velocity` in `segments.csv` is missing or invalid.

The loading logic is:

```text
Valid max_velocity
        │
        ▼
   use that value

Missing/invalid max_velocity
        │
        ▼
   use 40 km/h
```

Therefore:

```python
DEFAULT_SPEED = 40.0
```

ensures that every valid road segment has a usable speed limit.

---

## `MIN_SPEED_FACTOR`

`MIN_SPEED_FACTOR` prevents the effective road speed from becoming zero.

```python
MIN_SPEED_FACTOR = 0.05
```

The minimum allowed speed is therefore:

```text
minimum speed
    = speed_limit × 0.05
```

This is important because `travel_time` divides the road length by the current speed.

---

# 5. Vertex

```python
@dataclass
class Vertex:
    id: int
    x: float
    y: float
```

A `Vertex` represents a node in the road network.

| Attribute | Meaning                  |
| --------- | ------------------------ |
| `id`      | Node ID from `nodes.csv` |
| `x`       | Longitude                |
| `y`       | Latitude                 |

Example:

```python
Vertex(
    id=123456,
    x=106.7000,
    y=10.7800
)
```

The geographic coordinate convention is:

```text
Vertex.x → longitude
Vertex.y → latitude
```

---

# 6. Edge

```python
@dataclass
class Edge:
    source: int
    target: int
    length: float
    speed_limit: float
    name: str = ""
    midpoint: tuple[float, float] = (0.0, 0.0)

    density: float = 0.0
    weather_factor: float = 1.0
    blocked: bool = False
```

An `Edge` represents a directed road segment:

```text
source ─────────→ target
```

## Main attributes

| Attribute        |         Unit | Meaning                              |
| ---------------- | -----------: | ------------------------------------ |
| `source`         |      node ID | Starting vertex                      |
| `target`         |      node ID | Ending vertex                        |
| `length`         |       meters | Length of the road segment           |
| `speed_limit`    |         km/h | Static speed limit used by the model |
| `name`           |         text | Road/street name                     |
| `midpoint`       | `(lat, lon)` | Geographic midpoint of the segment   |
| `density`        |          0–1 | Dynamic traffic density              |
| `weather_factor` |         0–1+ | Dynamic weather multiplier           |
| `blocked`        |      boolean | Whether the road is unavailable      |

---

# 7. Edge Midpoint

The midpoint is calculated while loading `segments.csv`.

For an edge connecting:

```text
source = (lat₁, lon₁)
target = (lat₂, lon₂)
```

the midpoint is:

```text
midpoint latitude
    = (lat₁ + lat₂) / 2

midpoint longitude
    = (lon₁ + lon₂) / 2
```

The code stores it as:

```python
midpoint = (
    (source_vertex.y + target_vertex.y) / 2.0,
    (source_vertex.x + target_vertex.x) / 2.0
)
```

Therefore:

```text
edge.midpoint = (latitude, longitude)
```

The midpoint is later used for:

* congestion hotspot calculations
* rain-cell detection
* geographic scenario generation

---

# 8. Traffic Density

Traffic density is represented as a value between `0` and `1`.

```text
0.0 → essentially empty
0.2 → light traffic
0.5 → moderate traffic
0.8 → heavy congestion
1.0 → maximum congestion
```

The scenario generator limits density to:

```python
0.0 ≤ density ≤ max_density
```

with the default:

```python
max_density = 0.9
```

Therefore, the default scenario does not generate a density of exactly `1.0`.

---

# 9. Current Speed

The current speed of an edge is calculated from three factors:

1. Static speed limit
2. Traffic density
3. Weather factor

The implementation is:

```python
speed = self.speed_limit * (1.0 - self.density)
speed *= self.weather_factor
```

Therefore:

```text
current_speed
    = speed_limit
      × (1 - density)
      × weather_factor
```

A minimum speed constraint is then applied:

```python
return max(
    speed,
    self.speed_limit * MIN_SPEED_FACTOR
)
```

Thus:

```text
current_speed
    = max(
        speed_limit × (1 - density) × weather_factor,
        speed_limit × 0.05
      )
```

---

## Example

Suppose:

```text
speed_limit = 40 km/h
density = 0.5
weather_factor = 0.8
```

Then:

```text
speed
= 40 × (1 - 0.5) × 0.8
= 16 km/h
```

Therefore:

```text
current_speed = 16 km/h
```

---

# 10. Travel Time

`travel_time` returns the estimated travel time in seconds.

The implementation is:

```python
return self.length / (self.current_speed / 3.6)
```

The conversion is:

```text
km/h ÷ 3.6 = m/s
```

because edge length is stored in meters.

Therefore:

```text
travel_time
    = length / speed_m_per_s
```

---

## Example

Suppose:

```text
length = 500 m
current_speed = 20 km/h
```

Convert speed:

```text
20 / 3.6
≈ 5.56 m/s
```

Then:

```text
travel_time
= 500 / 5.56
≈ 90 seconds
```

---

## Blocked Edges

If an edge is blocked:

```python
if self.blocked:
    return math.inf
```

Therefore:

```text
blocked edge
    ↓
travel_time = ∞
```

This makes the edge effectively unusable by the routing algorithm.

---

# 11. Graph

The custom `Graph` class stores the complete road network.

```python
class Graph:
    self.vertices
    self.adj
    self.radj
    self.v_max
```

---

## 11.1 `vertices`

```python
id → Vertex
```

Stores the vertices in the graph.

Example:

```text
123 → Vertex(...)
456 → Vertex(...)
789 → Vertex(...)
```

---

## 11.2 `adj`

The forward adjacency structure is:

```text
source → target → Edge
```

For example:

```text
A → B
A → C
B → D
```

allows the routing algorithm to find all outgoing edges from a node.

The method:

```python
neighbors(u)
```

returns the outgoing edges from `u`.

---

## 11.3 `radj`

`radj` is the reverse adjacency structure:

```text
target → source → Edge
```

It stores incoming edges.

For example, if:

```text
A → B
C → B
```

then:

```text
radj[B]
    ├── A → Edge
    └── C → Edge
```

The method:

```python
predecessors(v)
```

returns incoming edges to `v`.

This structure is useful for backward or bidirectional pathfinding algorithms.

---

## 11.4 `get_edge`

The method:

```python
get_edge(u, v)
```

returns the edge from `u` to `v`, if one exists.

Conceptually:

```text
get_edge(A, B)
       ↓
A → B
```

---

# 12. Maximum Graph Speed

The graph stores:

```python
self.v_max
```

which represents the maximum static speed limit among all retained edges.

When an edge is added:

```python
self.v_max = max(
    self.v_max,
    edge.speed_limit
)
```

Therefore:

```text
v_max = maximum edge.speed_limit
```

Importantly, `v_max` is based on the **static speed limits**, not the dynamically reduced current speeds.

---

# 13. A* Heuristic

`v_max` is useful for the time-based A* heuristic.

A basic time heuristic can be expressed as:

```text
h(n)
    = distance(n, goal) / v_max
```

The idea is that `v_max` represents the fastest static speed available in the graph.

Because the heuristic assumes a very fast possible travel speed, it provides a lower-bound estimate of the time required to reach the destination.

The geographic distance is calculated using the Haversine formula described later in this document.

---

# 14. Loading the Graph

The graph is loaded using:

```python
load_graph(data_dir, bbox=None)
```

where:

```text
data_dir
    → directory containing nodes.csv and segments.csv

bbox
    → optional geographic bounding box
```

---

# 15. Graph Loading Process

The complete loading process is:

```text
nodes.csv
    │
    ▼
load_nodes()
    │
    ▼
Vertex objects
    │
    │
segments.csv
    │
    ▼
Validate segments
    │
    ├── BBOX filtering
    ├── self-loop filtering
    ├── length validation
    └── speed validation
    │
    ▼
Edge objects
    │
    ▼
Remove longer parallel edges
    │
    ▼
Determine used nodes
    │
    ▼
Build Graph
```

---

# 16. Loading Nodes

The function:

```python
load_nodes(nodes_path)
```

reads `nodes.csv`.

The expected columns are:

```text
_id
long
lat
```

Each row is converted into:

```python
Vertex(
    id=node_id,
    x=lon,
    y=lat
)
```

The resulting dictionary is:

```text
node_id → Vertex
```

---

# 17. Bounding Box Filtering

`load_graph()` optionally accepts:

```python
bbox=(north, south, east, west)
```

The bounding box format is:

```text
(north, south, east, west)
```

For example:

```python
bbox = (
    10.777,
    10.769,
    106.702,
    106.694
)
```

A node is retained if:

```text
south ≤ latitude ≤ north

west ≤ longitude ≤ east
```

The filtering is performed before processing road segments.

---

# 18. Bounding Box Validation

The function:

```python
validate_bbox(bbox)
```

checks that the bounding box is valid.

It verifies:

1. Exactly four values are provided.
2. Values are numeric.
3. Values are finite.
4. Latitude is between `-90` and `90`.
5. Longitude is between `-180` and `180`.
6. `north > south`.
7. `east > west`.

Invalid input raises a `ValueError`.

If the bounding box contains no nodes from `nodes.csv`, `load_graph()` also raises an error.

---

# 19. Segment Validation

Each row in `segments.csv` is validated before becoming an edge.

## Source and target

Both:

```text
s_node_id
e_node_id
```

must exist in the spatially filtered node set.

Otherwise the segment is skipped.

---

## Self-loops

Segments where:

```text
source == target
```

are ignored.

Therefore:

```text
A → A
```

is not added to the graph.

---

## Length

The `length` field must be:

* numeric
* finite
* greater than zero

Invalid lengths are skipped.

---

# 20. Speed Parsing

The current dataset provides speed through:

```text
max_velocity
```

The loader attempts:

```python
speed_limit = float(raw_speed)
```

If the value is missing or cannot be converted to a valid positive finite number:

```python
speed_limit = DEFAULT_SPEED
```

Therefore:

```text
valid max_velocity
        ↓
use dataset value

invalid/missing max_velocity
        ↓
use 40 km/h
```

---

# 21. Road Names

The segment field:

```text
street_name
```

is stored as:

```python
edge.name
```

Whitespace is removed using:

```python
raw_name.strip()
```

Empty or invalid textual values such as:

```text
""
"nan"
"none"
```

are converted to:

```text
""
```

---

# 22. Creating Edges

For every valid segment, an `Edge` is created:

```python
Edge(
    source=source,
    target=target,
    length=length,
    speed_limit=speed_limit,
    name=name,
    midpoint=midpoint
)
```

The dynamic scenario values initially use their defaults:

```text
density = 0.0
weather_factor = 1.0
blocked = False
```

These values are later modified by `ScenarioGenerator`.

---

# 23. Parallel Edges

The input data may contain multiple segments with the same:

```text
source → target
```

pair.

The loader keeps only the shortest one.

The key is:

```python
key = (edge.source, edge.target)
```

The replacement rule is:

```python
if old is None or edge.length < old.length:
    shortest_edges[key] = edge
```

Therefore:

```text
A → B = 100 m
A → B = 80 m
A → B = 120 m
```

results in:

```text
A → B = 80 m
```

This simplifies the graph to one retained edge for each directed source-target pair.

---

# 24. Used Nodes

The loader does not automatically add every node inside the bounding box.

After parallel edges have been filtered, it determines which nodes actually participate in the final graph.

For every retained edge:

```python
used_nodes.add(edge.source)
used_nodes.add(edge.target)
```

Only these nodes are added to the graph.

Therefore:

```text
nodes.csv
    ↓
BBOX filtering
    ↓
valid segments
    ↓
retained segments
    ↓
only participating nodes
```

This prevents isolated nodes from being unnecessarily stored in the routing graph.

---

# 25. Directed Roads

Edges are directed according to the source and target fields in `segments.csv`.

For example:

```text
A → B
```

is stored as:

```python
Edge(
    source=A,
    target=B,
    ...
)
```

If the dataset also contains:

```text
B → A
```

then the graph contains two directed edges:

```text
A → B
B → A
```

---

# 26. Physical Road Grouping

The function:

```python
roads(graph)
```

groups opposite-direction edges when assigning scenario values.

Suppose the graph contains:

```text
A → B
B → A
```

The two edges are treated as the two directions of the same physical road.

The function uses:

```python
road_key = frozenset((u, v))
```

and tracks already processed road pairs.

Therefore:

```text
A → B
B → A
```

are processed once for scenario generation.

If only:

```text
A → B
```

exists, it is treated as a single-direction road.

---

# 27. Scenario Model

The scenario model consists of three major dynamic conditions:

```text
Traffic density
Weather
Road incidents
```

These are stored directly on each `Edge`.

```text
edge.density
edge.weather_factor
edge.blocked
```

The scenario generator modifies these values without changing the underlying static road network.

---

# 28. Hotspots

A `Hotspot` represents a geographic area with increased traffic congestion.

```python
@dataclass
class Hotspot:
    lat: float
    lon: float
    intensity: float = 1.0
    radius: float = 500.0
```

| Attribute   | Meaning                      |
| ----------- | ---------------------------- |
| `lat`       | Hotspot latitude             |
| `lon`       | Hotspot longitude            |
| `intensity` | Maximum congestion influence |
| `radius`    | Spatial spread in meters     |

Example:

```python
Hotspot(
    lat=10.780,
    lon=106.700,
    intensity=1.0,
    radius=500
)
```

---

# 29. Hotspot Influence

The function:

```python
hotspot_intensity(distance, hotspot)
```

uses a Gaussian-style falloff:

```text
influence
    = intensity
      × exp(
          -distance² / (2 × radius²)
        )
```

The distance is measured using the Haversine formula.

The result is:

```text
close to hotspot
        ↓
high influence

far from hotspot
        ↓
low influence
```

This creates a gradual congestion region instead of a hard boundary.

---

# 30. Multiple Hotspots

When multiple hotspots affect the same road, the implementation uses the **strongest hotspot influence**.

The relevant logic is:

```python
hotspot_effect = max(
    hotspot_effect,
    influence
)
```

For example:

```text
Hotspot A influence = 0.7
Hotspot B influence = 0.4
```

produces:

```text
hotspot_effect = 0.7
```

The two influences are not added together.

---

# 31. ScenarioConfig

Scenario parameters are stored in:

```python
@dataclass
class ScenarioConfig:
    base_density: float = 0.1
    max_density: float = 0.9
    density_deviation: float = 0.05
    hotspots: list[Hotspot] = field(default_factory=list)

    block_probability: float = 0.01

    global_weather_factor: float = 1.0
    rain_cells: list[RainCell] = field(default_factory=list)
```

The configuration controls traffic, incidents, and weather.

---

# 32. Base Density

```python
base_density = 0.1
```

`base_density` is the starting traffic density for every physical road.

For example:

```text
base_density = 0.1
```

means every road starts with:

```text
density = 0.1
```

before hotspot effects and random variation are applied.

This means roads outside congestion hotspots are not assumed to have zero traffic.

---

# 33. Maximum Density

```python
max_density = 0.9
```

The generated density is clamped to:

```text
0.0 ≤ density ≤ 0.9
```

The maximum is applied after hotspot influence and random variation.

---

# 34. Density Deviation

```python
density_deviation = 0.05
```

A small random variation is added to each physical road.

The generated value is:

```python
rng.uniform(
    -cfg.density_deviation,
    cfg.density_deviation
)
```

Therefore, with:

```text
density_deviation = 0.05
```

the variation is between:

```text
-0.05 and +0.05
```

This prevents roads with similar hotspot influence from always having exactly the same density.

---

# 35. Density Generation

For each physical road, density is calculated as:

```text
density
    = base_density
      + hotspot contribution
      + random deviation
```

The hotspot contribution is:

```text
(max_density - base_density)
× hotspot_effect
```

Therefore:

```text
density
    = base_density
      + (max_density - base_density)
        × hotspot_effect
      + random_deviation
```

Finally, the result is clamped:

```python
density = max(
    0.0,
    min(cfg.max_density, density)
)
```

---

# 36. Applying Density to Both Directions

If a physical road has two directions:

```text
A → B
B → A
```

the same density is assigned to both:

```python
edge.density = density

if twin is not None:
    twin.density = density
```

This keeps the simulated traffic condition consistent for both directions of the physical road.

---

# 37. Road Incidents

Road incidents are represented by:

```python
edge.blocked
```

A blocked road is unavailable to the routing algorithm.

The probability is controlled by:

```python
block_probability
```

The default is:

```python
block_probability = 0.01
```

meaning each physical road has a 1% probability of being blocked.

---

# 38. Incident Generation

Incidents use a separate deterministic random generator:

```python
rng = random.Random(
    f"incidents-{self.seed}"
)
```

For every physical road:

```python
blocked = (
    rng.random()
    < self.config.block_probability
)
```

If the road is blocked:

```text
edge.blocked = True
```

If the opposite-direction twin exists, it receives the same value.

Therefore:

```text
A → B = blocked
B → A = blocked
```

for the same physical road.

---

# 39. Random Seeds

The scenario generator is deterministic for a given seed.

Example:

```python
generator = ScenarioGenerator(seed=42)
```

Running the density and incident generation with the same seed produces the same scenario.

Changing the seed:

```python
ScenarioGenerator(seed=43)
```

produces a different random scenario.

This is useful for:

* testing
* benchmarking
* comparing A* variants
* reproducing experiments

---

# 40. Weather Model

Weather is represented using two levels:

```text
Global weather factor
        +
Localized rain cells
```

The corresponding configuration fields are:

```python
global_weather_factor
rain_cells
```

Weather modifies the effective speed of roads but does not change the underlying road network.

---

# 41. RainCell

A `RainCell` represents a rectangular geographic region affected by rain.

```python
@dataclass
class RainCell:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    weather_factor: float = 0.6
```

| Attribute        | Meaning                               |
| ---------------- | ------------------------------------- |
| `min_lat`        | Minimum latitude                      |
| `max_lat`        | Maximum latitude                      |
| `min_lon`        | Minimum longitude                     |
| `max_lon`        | Maximum longitude                     |
| `weather_factor` | Speed multiplier inside the rain cell |

Example:

```python
RainCell(
    min_lat=10.771,
    max_lat=10.775,
    min_lon=106.696,
    max_lon=106.700,
    weather_factor=0.6
)
```

---

# 42. Global Weather Factor

The global weather factor applies to every road.

```python
global_weather_factor = 1.0
```

Examples:

```text
1.0 → no global speed reduction
0.9 → speed multiplied by 0.9
0.8 → speed multiplied by 0.8
```

The initial weather value for every road is:

```python
weather = cfg.global_weather_factor
```

---

# 43. Localized Rain Cells

The road's midpoint is used to determine whether it lies inside a rain cell.

For each edge:

```python
lat, lon = midpoint
```

The implementation checks:

```python
rain_cell.min_lat <= lat <= rain_cell.max_lat
```

and:

```python
rain_cell.min_lon <= lon <= rain_cell.max_lon
```

A road is inside the rain cell only when both conditions are true.

---

# 44. Rain Cell Effects

If an edge midpoint is inside a rain cell:

```python
weather *= rain_cell.weather_factor
```

For example:

```text
global_weather_factor = 1.0
rain_cell.weather_factor = 0.6
```

produces:

```text
weather_factor = 1.0 × 0.6
               = 0.6
```

Therefore, the road's weather-adjusted speed is multiplied by `0.6`.

---

# 45. Multiple Rain Cells

If a road lies inside multiple rain cells, their effects are multiplied.

For example:

```text
global factor = 1.0

rain cell 1 = 0.8
rain cell 2 = 0.7
```

produces:

```text
weather_factor
    = 1.0 × 0.8 × 0.7
    = 0.56
```

Therefore, the final weather factor is:

```text
0.56
```

---

# 46. Applying Weather to Both Directions

As with traffic density and incidents, both directions of the same physical road receive the same weather factor.

For:

```text
A → B
B → A
```

the implementation assigns:

```python
edge.weather_factor = weather

if twin is not None:
    twin.weather_factor = weather
```

This keeps the environmental condition consistent across both directions.

---

# 47. Interaction Between Traffic and Weather

Traffic density and weather both affect current speed.

The complete speed model is:

```text
current_speed
    = speed_limit
      × (1 - density)
      × weather_factor
```

For example:

```text
speed_limit = 50 km/h
density = 0.4
weather_factor = 0.6
```

First apply traffic:

```text
50 × (1 - 0.4)
= 30 km/h
```

Then weather:

```text
30 × 0.6
= 18 km/h
```

Therefore:

```text
current_speed = 18 km/h
```

subject to the minimum speed constraint.

---

# 48. ScenarioGenerator

The main scenario generation class is:

```python
class ScenarioGenerator:
    ...
```

It is initialized with:

```python
ScenarioGenerator(
    seed=42,
    config=config
)
```

If no configuration is supplied:

```python
ScenarioConfig()
```

is used.

---

# 49. Applying a Scenario

The main method is:

```python
generator.apply(graph)
```

The method first obtains the physical roads:

```python
road_list = list(roads(graph))
```

Then it applies the three scenario components:

```text
_assign_density()
        ↓
_assign_incidents()
        ↓
_assign_weather()
```

The complete process is:

```text
Graph
  │
  ▼
Physical roads
  │
  ├── Traffic density
  │
  ├── Road incidents
  │
  └── Weather
  │
  ▼
Updated Graph
```

---

# 50. Scenario Application Order

The scenario is applied in the following order:

```python
self._assign_density(road_list)
self._assign_incidents(road_list)
self._assign_weather(road_list)
```

The three components are stored independently:

```text
density
blocked
weather_factor
```

The final travel time is then determined by the `Edge` properties.

---

# 51. Example Scenario Configuration

A complete scenario can be configured as:

```python
config = ScenarioConfig(
    base_density=0.1,
    max_density=0.9,
    density_deviation=0.05,
    block_probability=0.01,
    global_weather_factor=1.0,
    hotspots=[
        Hotspot(
            lat=10.780,
            lon=106.700,
            intensity=1.0,
            radius=500
        )
    ],
    rain_cells=[
        RainCell(
            min_lat=10.771,
            max_lat=10.775,
            min_lon=106.696,
            max_lon=106.700,
            weather_factor=0.6
        )
    ]
)
```

The generator can then be created with:

```python
generator = ScenarioGenerator(
    seed=42,
    config=config
)
```

and applied using:

```python
generator.apply(graph)
```

---

# 52. Example: Normal Traffic

```python
ScenarioConfig(
    base_density=0.1,
    max_density=0.5,
    density_deviation=0.03,
    block_probability=0.01
)
```

This represents a relatively light traffic scenario with small local variations and occasional road incidents.

---

# 53. Example: Rush Hour

```python
ScenarioConfig(
    base_density=0.4,
    max_density=0.9,
    density_deviation=0.05,
    block_probability=0.02,
    hotspots=[...]
)
```

The higher baseline means that the entire network starts with moderate traffic.

Hotspots then create areas of particularly high congestion.

---

# 54. Example: Heavy Rain

```python
ScenarioConfig(
    base_density=0.3,
    max_density=0.9,
    density_deviation=0.05,
    block_probability=0.01,
    global_weather_factor=0.85,
    rain_cells=[
        RainCell(
            min_lat=10.771,
            max_lat=10.775,
            min_lon=106.696,
            max_lon=106.700,
            weather_factor=0.6
        )
    ]
)
```

In this scenario:

```text
Entire network
    → weather factor 0.85

Inside rain cell
    → 0.85 × 0.6
    → 0.51
```

Thus, roads inside the rain cell experience a much stronger speed reduction.

---

# 55. Geographic Utilities

The module provides two geographic helper functions:

```python
haversine_m(...)
nearest_node(...)
```

---

# 56. Haversine Distance

The function:

```python
haversine_m(
    lat1,
    lon1,
    lat2,
    lon2
)
```

calculates the great-circle distance between two geographic coordinates.

The result is returned in meters.

The implementation uses:

```text
Earth radius = 6,371,000 meters
```

The Haversine formula accounts for the Earth's curvature and is therefore more appropriate for geographic coordinates than a simple Euclidean distance in latitude/longitude space.

---

# 57. Nearest Node

The function:

```python
nearest_node(graph, point)
```

finds the graph node geographically closest to a given point.

The input is:

```python
point = (latitude, longitude)
```

The function compares the point with every graph vertex using the Haversine distance.

Conceptually:

```text
Input point
    │
    ▼
Calculate distance to every Vertex
    │
    ▼
Select minimum distance
    │
    ▼
Nearest node ID
```

This is useful when a user specifies a route endpoint using geographic coordinates rather than a graph node ID.

---

# 58. Final Data Model

After graph loading and scenario generation, each edge contains both static road information and dynamic scenario information.

```text
Edge
│
├── Static road information
│   ├── source
│   ├── target
│   ├── length
│   ├── speed_limit
│   ├── name
│   └── midpoint
│
└── Dynamic scenario information
    ├── density
    ├── weather_factor
    └── blocked
```

---

# 59. Complete Processing Pipeline

The complete system can be summarized as:

```text
                Kaggle Dataset
                      │
                      ▼
                 CSV Files
                      │
              ┌───────┴───────┐
              │               │
          nodes.csv       segments.csv
              │               │
              ▼               ▼
          Vertex data     Edge data
              │               │
              └───────┬───────┘
                      ▼
                 load_graph()
                      │
                      ├── Validate BBOX
                      ├── Filter nodes
                      ├── Validate segments
                      ├── Remove self-loops
                      ├── Parse speed
                      ├── Calculate midpoint
                      ├── Remove longer parallel edges
                      └── Build Graph
                              │
                              ▼
                       ScenarioGenerator
                              │
                ┌─────────────┼─────────────┐
                │             │             │
                ▼             ▼             ▼
             Density       Incidents      Weather
                │             │             │
                │             │             │
                └─────────────┼─────────────┘
                              ▼
                        Updated Graph
                              │
                              ▼
                         A* Search
                              │
                              ▼
                    Estimated Travel Time
```

---

# 60. Important Assumptions

The current module makes the following assumptions:

1. The road network is provided locally through `nodes.csv` and `segments.csv`.
2. `nodes.csv` contains valid node coordinates through `_id`, `long`, and `lat`.
3. `segments.csv` contains directed road segments through `s_node_id` and `e_node_id`.
4. Segment length is measured in meters.
5. `max_velocity` is expressed in km/h.
6. Missing or invalid speed values use `DEFAULT_SPEED = 40 km/h`.
7. Traffic density is simulated rather than treated as a directly measured real-time value by this module.
8. Weather conditions are simulated using a global factor and optional rectangular rain cells.
9. Road incidents are simulated probabilistically using `block_probability`.
10. Opposite-direction edges representing the same physical road receive the same density, incident status, and weather factor.
11. The graph retains only the shortest edge for each directed `(source, target)` pair.
12. The underlying road network is not modified when a scenario is generated; only dynamic edge attributes are changed.

---