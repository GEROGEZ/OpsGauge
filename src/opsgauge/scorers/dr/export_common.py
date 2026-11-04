"""Complete raw non-log author facts; participant export includes logs too."""
import json


def temporal_projection_source(source, rule):
    """Use the same evidence-linked task projection as the question compiler.

    This is author-side contract input only. Do not trim participant snapshots,
    invent missing entity identities, or attach orphan observations by name.
    """
    from observation_graph import ObservationGraph
    signals = {rule['phase']['desired_signal'], rule['phase']['available_signal'],
               *rule['outputs'].values()}
    graph = ObservationGraph(source)
    view = graph.project(['metric', 'event', 'topology'], lambda row:
                         row['modality'] in ['event', 'topology'] or row['signal'] in signals)
    return view.legacy_source(source['binding'])


def resolve_empty_metric_owners(records, entities):
    """Bind only empty metric owners by unique, explicit Pod+namespace identity."""
    index = {}
    for entity in entities:
        attrs = entity.get('attributes') or {}
        if isinstance(attrs, str): attrs=json.loads(attrs)
        if entity['entity_type']=='k8s.pod' and attrs.get('namespace') and entity['entity_id']:
            index.setdefault((entity['entity_name'],attrs['namespace']),set()).add(entity['entity_id'])
    changes=[]
    for row in records:
        dims=row.get('dimensions') or {}
        if isinstance(dims,str):dims=json.loads(dims)
        ids=row.get('entity_ids') or []
        if isinstance(ids,str):ids=json.loads(ids)
        if row['modality']!='metric' or any(ids):continue
        matches=index.get((dims.get('pod'),dims.get('namespace')),set())
        if len(matches)!=1:continue
        row['entity_ids']=[next(iter(matches))]
        changes.append(dict(record_ref=row['record_ref'],original_entity_ids=ids,resolved_entity_ids=row['entity_ids']))
    return changes

def read_source(db,item,modalities=('metric','trace','event','topology')):
    b=item['binding']
    def rows(sql,params):
        cur=db.execute(sql,params);names=[x[0] for x in cur.description]
        return [dict(zip(names,r)) for r in cur.fetchall()]
    entities=rows('select * from entities where bundle_id=?',[b['bundle_id']])
    assert set(modalities)<={'metric','trace','event','topology'}
    records=rows("select * exclude (time_start,time_end) replace (strftime(observed_at at time zone 'UTC','%Y-%m-%dT%H:%M:%S.%fZ') as observed_at) from records where bundle_id=? and source_kind='raw_telemetry' and not synthetic and modality in (select unnest(?)) and observed_at between ?::timestamptz and ?::timestamptz order by observed_at,record_ref",[b['bundle_id'],list(modalities),b['start'],b['end']])
    for e in entities:
        if isinstance(e['attributes'],str):e['attributes']=json.loads(e['attributes'])
    for r in records:
        for key in ['entity_ids','value_json','dimensions','attributes']:
            if isinstance(r[key],str):r[key]=json.loads(r[key])
    resolve_empty_metric_owners(records,entities)
    return dict(binding=b,entities=entities,records=records)
