from __future__ import annotations
from pathlib import Path
import hashlib, json

FIELDS=('code','inner_l','inner_w','inner_h','door_w','door_h','payload','open_top','floor_load_kg_m2')

class ContainerSpecsError(ValueError):
    pass

def _num(v):
    x=round(float(v),6)
    return int(x) if float(x).is_integer() else x

def canonical_rows(rows):
    if not isinstance(rows,list) or not rows:
        raise ContainerSpecsError('当前集装箱计算参数为空或格式无效。')
    out=[]; seen=set()
    for r in rows:
        if not isinstance(r,dict):
            raise ContainerSpecsError('集装箱计算参数存在非对象记录。')
        code=str(r.get('code') or '').strip().upper()
        if not code:
            raise ContainerSpecsError('集装箱计算参数存在空code。')
        if code in seen:
            raise ContainerSpecsError(f'集装箱参数存在重复code：{code}')
        seen.add(code)
        row={'code':code}
        for k in ('inner_l','inner_w','inner_h','door_w','door_h','payload'):
            if r.get(k) in (None,''):
                raise ContainerSpecsError(f'{code} 缺少 {k}。')
            row[k]=_num(r.get(k))
            if float(row[k])<=0:
                raise ContainerSpecsError(f'{code} 参数 {k} 必须大于0。')
        raw_ot=r.get('open_top', 'OT' in code)
        if isinstance(raw_ot,str):
            row['open_top']=raw_ot.strip().lower() in {'1','true','yes','y','是'}
        else:
            row['open_top']=bool(raw_ot)
        raw_floor=r.get('floor_load_kg_m2')
        if raw_floor in (None,''):
            row['floor_load_kg_m2']=None
        else:
            row['floor_load_kg_m2']=_num(raw_floor)
            if float(row['floor_load_kg_m2'])<=0:
                raise ContainerSpecsError(f'{code} 参数 floor_load_kg_m2 必须大于0或留空。')
        out.append(row)
    out.sort(key=lambda x:x['code'])
    return out

def specs_sha256(rows):
    canon=canonical_rows(rows)
    body=json.dumps(canon,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(body.encode('utf-8')).hexdigest()

def _read_json_strict(path:Path):
    try:
        raw=json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise ContainerSpecsError('实际柜参数文件缺失。')
    except Exception as e:
        raise ContainerSpecsError('实际柜参数文件无法解析：'+str(e))
    return raw

def load_state(spec_path:Path, meta_path:Path, default_rows):
    """Single source of truth for planning, display, hash and release gate.

    - No project spec file/meta -> standard planning mode using built-in templates.
    - Once an actual/project spec exists (or meta says confirmed), it must parse strictly.
      Corrupt/missing project specs never silently fall back to defaults.
    """
    meta={}
    if meta_path.exists():
        try:
            val=json.loads(meta_path.read_text(encoding='utf-8'))
            if isinstance(val,dict): meta=val
        except Exception as e:
            # A corrupt meta is never silently ignored: source selection itself is ambiguous.
            return {'mode':'invalid','rows':[],'sha256':'','meta':{},'error':'集装箱参数meta无法解析：'+str(e)}
    has_project_spec=spec_path.exists() or bool(meta.get('confirmed')) or str(meta.get('mode') or '').lower()=='actual'
    if not has_project_spec:
        rows=canonical_rows([dict(x) for x in default_rows])
        return {'mode':'standard','rows':rows,'sha256':specs_sha256(rows),'meta':meta,'error':''}
    try:
        raw=_read_json_strict(spec_path)
        rows=canonical_rows(raw)
        return {'mode':'actual','rows':rows,'sha256':specs_sha256(rows),'meta':meta,'error':''}
    except Exception as e:
        return {'mode':'invalid','rows':[],'sha256':'','meta':meta,'error':str(e)}

def require_rows(spec_path:Path, meta_path:Path, default_rows):
    st=load_state(spec_path,meta_path,default_rows)
    if st['mode']=='invalid':
        raise ContainerSpecsError(st['error'] or '当前集装箱参数无效。')
    return [dict(x) for x in st['rows']]
