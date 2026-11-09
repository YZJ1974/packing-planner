"""Project-scoped user declaration, never a numerical top-load certificate."""
import hashlib,json,math
from pathlib import Path


def signature(q,stable=False):
    dims=[q.get(k) for k in ('length_mm','width_mm','height_mm')]
    if any(v is None for v in dims):dims=[q.get(k) for k in ('l','w','h')]
    dimensions=[round(float(v),6) for v in dims]
    body=dict(name=q.get('name'),type=q.get('type'),dimensions_mm=sorted(dimensions[:2])+dimensions[2:] if stable else sorted(dimensions),weight_kg=round(float(q.get('weight_kg',0)),6))
    if not stable:body['asu_key']=q.get('asu_key')
    return hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def load(data_dir):
    try:
        p=json.loads((Path(data_dir)/'bag_top_policy.json').read_text())
        if p.get('schema') not in (1,2) or p.get('enabled') is not True or p.get('source')!='USER_PROJECT_DECLARATION':return {}
        if p['schema']==2:
            source=json.loads((Path(data_dir)/'shipment_source.json').read_text())
            if source.get('status')!='verified' or source.get('issues'):return {}
            if not p.get('shipment_sha256') or not p.get('shipment_fingerprint'):return {}
            if p['shipment_sha256']!=source.get('sha256') or p['shipment_fingerprint']!=source.get('fingerprint'):return {}
        if not isinstance(p.get('approved_package_signatures'),list):return {}
        density=float(p['max_bag_density_kg_m3'])
        if not math.isfinite(density) or density<=0:return {}
        return p
    except (OSError,ValueError,TypeError,KeyError):return {}


def applicable(policy,bags):
    if not policy or not policy.get('enabled') or not bags:return {}
    density=float(policy.get('max_bag_density_kg_m3',0))
    for p in bags:
        v=float(p.get('flexible_unit_volume_m3') or 0);w=float(p.get('weight_kg',0))
        if v<=0 or not math.isfinite(v) or w<0 or not math.isfinite(w) or w/v>density+1e-9:return {}
    return policy


def approved(q,policy):
    return bool(policy and policy.get('enabled') and signature(q,stable=policy.get('schema')==2) in policy.get('approved_package_signatures',[]))


def rebind_unchanged_equipment(data_dir, previous_source, previous_items, items):
    """Carry this project's declaration only when bag quantity is the sole physical change."""
    from app.shipment_source import fingerprint
    directory=Path(data_dir);path=directory/'bag_top_policy.json'
    try:
        policy=json.loads(path.read_text());source=json.loads((directory/'shipment_source.json').read_text())
        if policy.get('schema')!=2 or policy.get('source')!='USER_PROJECT_DECLARATION' or not policy.get('enabled'):return False
        if previous_source.get('status')!='verified' or previous_source.get('issues') or previous_source.get('fingerprint')!=fingerprint(previous_items):return False
        if any(policy.get(p)!=previous_source.get(s) for p,s in [('shipment_sha256','sha256'),('shipment_fingerprint','fingerprint')]):return False
        def facts(rows):
            result=[];bags=0
            for row in rows:
                bag='PERLITE_FLEXIBLE_VOLUME' in str(row.get('size_note',''))
                if bag:bags+=1
                result.append([str(row.get('name','')),0 if bag else float(row.get('qty') or 0),*[float(row.get(k) or 0) for k in ('length_mm','width_mm','height_mm','weight_kg')],str(row.get('package_type','')),int(row.get('stackable') or 0),int(row.get('rotatable') or 0),bag])
            return sorted(result),bags
        old,old_bags=facts(previous_items);new,new_bags=facts(items)
        if old_bags!=1 or new_bags!=1 or old!=new:return False
        if source.get('status')!='verified' or source.get('issues') or source.get('fingerprint')!=fingerprint(items):return False
        policy['shipment_sha256']=source['sha256'];policy['shipment_fingerprint']=source['fingerprint']
        policy['rebind_basis']='替换导入前后逐项核对设备数量、外形、重量、包装和姿态一致；袋装物料及密度不变，仅允许袋数变化。'
        temporary=path.with_suffix('.json.tmp');temporary.write_text(json.dumps(policy,ensure_ascii=False,indent=2));temporary.replace(path)
        return True
    except (OSError,ValueError,TypeError,KeyError):return False
