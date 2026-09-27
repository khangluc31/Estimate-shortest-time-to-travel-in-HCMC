
from collections import defaultdict

import numpy as np
import pandas as pd

node_data = pd.read_csv('nodes.csv')
segments_data = pd.read_csv('segments.csv')
segment_status_data = pd.read_csv('segment_status.csv')
train_data = pd.read_csv('train.csv')

nodes = node_data.dropna(subset=['_id', 'long', 'lat'])
valid_node_ids = set(nodes['_id'])
node_dict = nodes.set_index('_id')[['long', 'lat']].apply(tuple, axis=1).to_dict()
velocity_agg = (
    segment_status_data
    .sort_values('updated_at')
    .groupby('segment_id')['velocity']
    .last()
    .reset_index()
)
edges = segments_data.merge(
    velocity_agg,
    left_on='_id',
    right_on='segment_id',
    how='left'
)
default_speed_by_type = {
    'motorway': 120.0, 'motorway_link': 80.0,
    'trunk': 80.0, 'trunk_link': 60.0,
    'primary': 60.0, 'primary_link': 40.0,
    'secondary': 50.0, 'secondary_link': 40.0,
    'tertiary': 40.0, 'tertiary_link': 30.0,
    'residential': 30.0, 'unclassified': 30.0, 'service': 20.0
}
edges['max_velocity'] = edges['max_velocity'].fillna(edges['street_type'].map(default_speed_by_type)).fillna(40.0)

cols_to_check = ['s_node_id', 'e_node_id', 'length']
edges_clean = edges.dropna(subset=cols_to_check).copy()
edges_clean = edges_clean[
    edges_clean['s_node_id'].isin(valid_node_ids) &
    edges_clean['e_node_id'].isin(valid_node_ids)
]
valid_road_types = {
    'motorway', 'motorway_link', 'trunk', 'trunk_link',
    'primary', 'primary_link', 'secondary', 'secondary_link',
    'tertiary', 'tertiary_link', 'residential', 'unclassified', 'service'
}
edges_clean = edges_clean[edges_clean['street_type'].isin(valid_road_types)]
edges_clean = edges_clean[edges_clean['length'] > 0]
los_summary = train_data.groupby('segment_id')['LOS'].agg(
    lambda x: x.mode()[0] if not x.mode().empty else 'C'
).reset_index()

edges_clean = edges_clean.merge(
    los_summary,
    on='segment_id',
    how='left'
)
edges_clean['LOS'] = edges_clean['LOS'].fillna('A')
los_penalty_map = {'A': 1.0, 'B': 1.1, 'C': 1.2, 'D': 1.3, 'E': 1.4, 'F': 1.5}
edges_clean['los_penalty'] = edges_clean['LOS'].map(los_penalty_map)
type_road_penalty_map = {
    'motorway': 1.0, 'motorway_link': 1.0,
    'trunk': 1.0, 'trunk_link': 1.0,
    'primary': 1.0, 'primary_link': 1.1,
    'secondary': 1.1, 'secondary_link': 1.15,
    'tertiary': 1.2, 'tertiary_link': 1.25,
    'residential': 1.4, 'unclassified': 1.5, 'service': 1.5
}

edges_clean['type_road_penalty'] = edges_clean['street_type'].map(type_road_penalty_map).fillna(1.2)
edges_clean['final_speed'] = edges_clean['velocity'].fillna(edges_clean['max_velocity'])
edges_clean['final_speed'] = np.where(edges_clean['final_speed'] <= 0, 0.001, edges_clean['final_speed'])
edges_clean = edges_clean.dropna(subset=['final_speed'])
edges_clean['base_time'] = edges_clean['length'] / (edges_clean['final_speed'] * 1000 / 3600)
edges_clean['travel_time'] = edges_clean['base_time'] * edges_clean['los_penalty'] * edges_clean['type_road_penalty']
edges_clean['travel_time'] = edges_clean['travel_time'].clip(lower=1e-6)
final_columns = ['segment_id', 's_node_id', 'e_node_id', 'length', 'street_name', 'travel_time', 'LOS']
edges_v1 = edges_clean[final_columns].copy()

graph = defaultdict(list)
for x in edges_v1.itertuples(index=False):
    graph[x.s_node_id].append({
        'target': x.e_node_id,
        'travel_time': x.travel_time,
        'length': x.length,
        'street_name': x.street_name
    })
graph = dict(graph)
v_max = max(edges_clean['final_speed'].max(), edges_clean['max_velocity'].max()) * 1000 / 3600
