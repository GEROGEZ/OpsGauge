"""Evidence-linked observation graph; never infers causality from co-occurrence."""
from collections import Counter
import copy
import json


class ObservationGraph:
    def __init__(self, source):
        self.binding = copy.deepcopy(source['binding'])
        episode = source.get('original_episode') or {}
        self.parent_episode_id = episode.get('episode_id')
        self.nodes = {}
        self.edges = set()
        self.unresolved_entities = set()
        for entity in source['entities']:
            self._node('entity:' + entity['entity_id'], 'Entity', entity)
        for raw in source['records']:
            if raw.get('synthetic') or raw.get('source_kind', 'raw_telemetry') != 'raw_telemetry':
                continue
            row = copy.deepcopy(raw)
            for key, default in [('attributes', {}), ('dimensions', {}), ('entity_ids', [])]:
                row[key] = row.get(key) or default
            oid = 'observation:' + row['record_ref']
            self._node(oid, 'Observation', row)
            for eid in row['entity_ids']:
                owner = 'entity:' + eid
                if owner in self.nodes:
                    self.edges.add((owner, 'observed', oid))
                else:
                    self.unresolved_entities.add(eid)
            attrs = row['attributes']
            if row['modality'] == 'topology':
                left, right = 'entity:' + str(attrs.get('source_entity_id')), 'entity:' + str(attrs.get('target_entity_id'))
                if left in self.nodes and right in self.nodes:
                    self.edges.add((left, attrs.get('relation_type') or row['signal'], right))
            if row['modality'] == 'trace' and attrs.get('service_name') and attrs.get('operation_name'):
                op = 'operation:' + json.dumps([attrs['service_name'], attrs['operation_name']], ensure_ascii=False)
                self._node(op, 'ObservedOperation', dict(service=attrs['service_name'], operation=attrs['operation_name']))
                self.edges.add((oid, 'callOf', op))
            if row['modality'] == 'event' and attrs.get('source_stream') == 'alert':
                if attrs.get('rule_id') and attrs.get('trans_id'):
                    rule = 'rule:' + attrs['rule_id']
                    transaction = 'transaction:' + json.dumps([attrs['rule_id'], attrs['trans_id']])
                    self._node(rule, 'AlertRule', dict(rule_id=attrs['rule_id']))
                    self._node(transaction, 'AlertTransaction', dict(rule_id=attrs['rule_id'], trans_id=attrs['trans_id']))
                    self.edges.add((oid, 'notificationOf', transaction))
                    self.edges.add((transaction, 'underRule', rule))

    def _node(self, identity, kind, data):
        node = dict(id=identity, type=kind, data=data)
        if identity in self.nodes and self.nodes[identity] != node:
            raise ValueError('conflicting_graph_identity:' + identity)
        self.nodes[identity] = node

    def entities(self):
        return [n['data'] for n in self.nodes.values() if n['type'] == 'Entity']

    def records(self, modalities):
        # Membership is read from current graph edges, never a cached raw list.
        attached = {t for _, relation, t in self.edges if relation == 'observed'}
        rows = []
        for identity in sorted(attached):
            node = self.nodes.get(identity)
            if not node or node['type'] != 'Observation' or node['data']['modality'] not in modalities:
                continue
            row = node['data']; attrs = row['attributes']
            if row['modality'] == 'topology':
                edge = ('entity:' + str(attrs.get('source_entity_id')), attrs.get('relation_type') or row['signal'],
                        'entity:' + str(attrs.get('target_entity_id')))
                if edge not in self.edges:
                    continue
            if row['modality'] == 'trace' and attrs.get('service_name') and attrs.get('operation_name'):
                op = 'operation:' + json.dumps([attrs['service_name'], attrs['operation_name']], ensure_ascii=False)
                if (identity, 'callOf', op) not in self.edges:
                    continue
            if (row['modality'] == 'event' and attrs.get('source_stream') == 'alert'
                    and attrs.get('rule_id') and attrs.get('trans_id')):
                tx = 'transaction:' + json.dumps([attrs.get('rule_id'), attrs.get('trans_id')])
                rule = 'rule:' + str(attrs.get('rule_id'))
                if (identity, 'notificationOf', tx) not in self.edges or (tx, 'underRule', rule) not in self.edges:
                    continue
            # Do not silently restore a removed entity->observation relation.
            ids = [eid for eid in row['entity_ids'] if ('entity:' + eid, 'observed', identity) in self.edges]
            rows.append(dict(row, entity_ids=ids))
        return rows

    def project(self, modalities, predicate=None):
        rows = self.records(modalities)
        if predicate is not None:
            rows = [row for row in rows if predicate(row)]
        selected = {'observation:' + r['record_ref'] for r in rows}
        # Deterministic structural closure, never expand back to observations
        # of unrequested modalities through a shared entity.
        for _ in range(3):
            additions = set()
            for left, _, right in self.edges:
                if left in selected and self.nodes[right]['type'] != 'Observation':
                    additions.add(right)
                if right in selected and self.nodes[left]['type'] != 'Observation':
                    additions.add(left)
            selected.update(additions)
        return ObservationView(self, rows, selected)


class ObservationView:
    def __init__(self, graph, records, selected):
        self.graph, self.records, self.selected = graph, records, selected

    def summary(self):
        edges = [e for e in self.graph.edges if e[0] in self.selected and e[2] in self.selected]
        return dict(schema_version='l2_observation_projection_v1', parent_episode_id=self.graph.parent_episode_id,
                    binding=self.graph.binding, observation_count=len(self.records),
                    modalities=dict(Counter(r['modality'] for r in self.records)),
                    node_types=dict(Counter(self.graph.nodes[n]['type'] for n in self.selected)),
                    edge_types=dict(Counter(e[1] for e in edges)),
                    unresolved_entity_ids=sorted(self.graph.unresolved_entities),
                    relation_semantics='identity/topology/observation; not inferred causal edges',
                    visibility='author_only; participant data is not trimmed to this projection')

    def legacy_source(self, binding):
        return dict(binding=binding, entities=self.graph.entities(), records=self.records,
                    original_episode=dict(episode_id=self.graph.parent_episode_id))
