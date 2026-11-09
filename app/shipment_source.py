"""Source identity for the current material set; independent of packing geometry."""
import hashlib
import json
import os
import copy
from pathlib import Path


def fingerprint(items):
    fields = ('name', 'qty', 'length_mm', 'width_mm', 'height_mm', 'weight_kg')
    rows = [[str(i.get(k) or '') if k == 'name' else float(i.get(k) or 0)
             for k in fields] for i in items]
    raw = json.dumps(sorted(rows), ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def source_row_digest(row):
    """Bind a confirmation to all parsed source facts, before corrections."""
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def resolve_weights(data_dir, raw, parsed, metas):
    path = Path(data_dir) / 'import_resolutions.json'
    if not path.exists():
        return parsed, metas
    resolution = json.loads(path.read_text())
    exact_file = resolution.get('sha256') == hashlib.sha256(raw).hexdigest()
    parsed, metas = copy.deepcopy(parsed), copy.deepcopy(metas)
    for fix in resolution.get('rows', []):
        matches = [r for r in parsed if r.get('source_row') == fix['row']
                   and r['name'] == fix['name'] and r['qty'] == fix['qty']]
        if not exact_file and (len(matches) != 1 or not fix.get('source_row_sha256') or source_row_digest(matches[0]) != fix['source_row_sha256']):
            continue
        if len(matches) != 1:
            raise ValueError('已确认的清单修正无法唯一对应原表行，禁止猜测套用。')
        row = matches[0]
        row['weight_kg'] = float(fix['weight_kg'])
        row['weight_conflict'] = False
        row['weight_source'] = '用户核对原表修正（未实称）'
        row['weight_note'] = resolution['authorization']
        row['size_note'] += '；上述重量矛盾已由用户确认修正，采用核对记录中的单重。'
    for fix in resolution.get('handling_confirmations', []):
        if not fix.get('planning_dimensions_mm'):
            continue
        matches=[r for r in parsed if r.get('source_row')==fix['row'] and r['name']==fix['name'] and r['qty']==fix['qty']]
        if not exact_file and (len(matches)!=1 or not fix.get('source_row_sha256') or source_row_digest(matches[0])!=fix['source_row_sha256']):
            continue
        if len(matches)!=1:
            raise ValueError('运输姿态确认无法唯一对应原表行，禁止猜测套用。')
        row=matches[0]
        original=[float(row[k]) for k in ('length_mm','width_mm','height_mm')]
        dims=[float(v) for v in fix['planning_dimensions_mm']]
        if len(dims)!=3 or sorted(dims)!=sorted(original) or min(dims)<=0:
            raise ValueError('放倒姿态只能调整原尺寸方向，不得缩小或改变外形尺寸。')
        if original!=[float(v) for v in fix['source_dimensions_mm']]:
            raise ValueError('原表尺寸与姿态确认记录不一致，禁止套用。')
        row.update(dict(zip(('length_mm','width_mm','height_mm'),dims)))
        row['size_note']+='；原表外形尺寸 '+ '×'.join(f'{v:g}' for v in original)+' mm；'+fix['authorization']+'；规划放倒后长宽高 '+ '×'.join(f'{v:g}' for v in dims)+' mm。'
    remaining = sum(bool(r.get('weight_conflict')) for r in parsed)
    for _, meta in metas:
        meta['weight_conflicts'] = remaining
    return parsed, metas


def state(data_dir, items):
    path = Path(data_dir) / 'shipment_source.json'
    if not path.exists():
        return {'status': 'untracked', 'issues': [], 'filename': '',
                'note': '历史材料未登记原清单来源，导出前请核对项目。'}
    try:
        data = json.loads(path.read_text())
        issues = list(data.get('issues') or [])
        if data.get('fingerprint') and data['fingerprint'] != fingerprint(items):
            issues.append('当前材料的名称、数量、尺寸或重量与已登记清单不一致，请重新核对来源。')
        data['issues'] = list(dict.fromkeys(issues))
        data['status'] = 'mismatch' if issues else data.get('status', 'verified')
        return data
    except (ValueError, OSError):
        return {'status': 'mismatch', 'filename': '', 'issues': ['清单来源记录损坏，禁止导出。']}


def record(data_dir, filename, raw, items, parsed, mode):
    directory = Path(data_dir)
    if mode != 'replace':
        # An append must never clear a known mismatch or claim the new file is
        # the sole source of the combined cargo set.
        return
    digest = hashlib.sha256(raw).hexdigest()
    sources = directory / 'source_files'
    sources.mkdir(exist_ok=True)
    (sources / (digest + Path(filename).suffix.lower())).write_bytes(raw)
    data = dict(status='verified', filename=Path(filename).name, sha256=digest,
                fingerprint=fingerprint(items), source_rows=len(parsed),
                item_records=len(items), issues=[], mode='replace',
                rows=[dict(name=r['name'], qty=r['qty'], row=r.get('source_row'),
                           weight_kg=r['weight_kg']) for r in parsed])
    resolution_path=directory/'import_resolutions.json'
    if resolution_path.exists():
        resolution=json.loads(resolution_path.read_text())
        data['handling_confirmations']=[fix for fix in resolution.get('handling_confirmations',[]) if any(r.get('source_row')==fix.get('row') and r.get('name')==fix.get('name') and fix.get('authorization','__absent__') in r.get('size_note','') for r in parsed)]
    path = directory / 'shipment_source.json'
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(temporary, path)
