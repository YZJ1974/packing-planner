from __future__ import annotations

import json
from app.evidence_validation import verify_dimension_claim, verify_weight_claim, numeric_close


def _json_dict(value):
    if isinstance(value, dict):
        return value
    try:
        obj = json.loads(value or '{}')
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def evidence_claim_matches_current(evidence_row, current, kind):
    """Re-check a stored human-visible evidence claim against current application data.

    The evidence file itself is not OCR-parsed. This closes the time-of-check/time-of-use gap:
    a previously accepted claim is invalid for release if the current value has changed.
    Returns (ok, problem_text, verification_dict).
    """
    if not evidence_row:
        return False, '凭证记录不存在', {'ok': False, 'problems': ['凭证记录不存在']}
    claim = _json_dict(evidence_row.get('claimed_values_json'))
    if kind in ('item_size', 'crate_size'):
        keys = ('length_mm', 'width_mm', 'height_mm')
        if any(claim.get(k) in (None, '') for k in keys):
            return False, '凭证未保存完整L/W/H核对值', {'ok': False, 'problems': ['凭证未保存完整L/W/H核对值']}
        system = tuple(float(current.get(k) or 0) for k in keys)
        evidence = tuple(float(claim.get(k)) for k in keys)
        ver = verify_dimension_claim(system, evidence)
        if ver.get('ok'):
            return True, '', ver
        return False, '当前尺寸已与凭证核对值不一致：' + '；'.join(ver.get('problems') or []), ver
    if kind == 'item_weight':
        if claim.get('weight_kg') in (None, ''):
            return False, '凭证未保存重量核对值', {'ok': False, 'problems': ['凭证未保存重量核对值']}
        ver = verify_weight_claim(float(current.get('weight_kg') or 0), float(claim.get('weight_kg')))
        if ver.get('ok'):
            return True, '', ver
        return False, '当前单件重量已与凭证核对值不一致：' + '；'.join(ver.get('problems') or []), ver
    if kind == 'crate_gross':
        if claim.get('gross_weight_kg') in (None, ''):
            return False, '秤票凭证未保存毛重核对值', {'ok': False, 'problems': ['秤票凭证未保存毛重核对值']}
        ver = verify_weight_claim(float(current.get('actual_gross_weight_kg') or 0), float(claim.get('gross_weight_kg')))
        if ver.get('ok'):
            return True, '', ver
        return False, '当前实称毛重已与秤票核对值不一致：' + '；'.join(ver.get('problems') or []), ver
    if kind == 'item_cg':
        keys=('cg_x_mm','cg_y_mm','cg_z_mm'); problems=[]
        if any(claim.get(k) in (None,'') for k in keys):
            return False, 'CG凭证未保存完整X/Y/Z核对值', {'ok':False,'problems':['CG凭证未保存完整X/Y/Z核对值']}
        for k,label in zip(keys,('CG-X','CG-Y','CG-Z')):
            ok,_=numeric_close(float(current.get(k) or 0),float(claim.get(k)),abs_tol=1.0,rel_tol=0.001)
            if not ok: problems.append(f'{label}：系统 {float(current.get(k) or 0):g} mm / 凭证核对值 {float(claim.get(k)):g} mm')
        if str(claim.get('source_type') or '') != str(current.get('cg_source_type') or ''):
            problems.append('CG来源类型已与凭证登记时不一致')
        ver={'ok':not problems,'problems':problems}
        return (True,'',ver) if not problems else (False,'当前CG已与凭证核对状态不一致：'+'；'.join(problems),ver)
    if kind == 'crate_top_load':
        if claim.get('max_top_load_kg') in (None,''):
            return False, '顶载凭证未保存核对值', {'ok':False,'problems':['顶载凭证未保存核对值']}
        ok,_=numeric_close(float(current.get('max_top_load_kg') or 0),float(claim.get('max_top_load_kg')),abs_tol=1.0,rel_tol=0.001)
        problems=[] if ok else [f"顶载：系统 {float(current.get('max_top_load_kg') or 0):g} kg / 凭证核对值 {float(claim.get('max_top_load_kg')):g} kg"]
        if str(claim.get('source_type') or '') != str(current.get('top_load_source_type') or ''): problems.append('顶载来源类型已变化')
        ver={'ok':not problems,'problems':problems}
        return (True,'',ver) if not problems else (False,'当前顶载已与凭证核对状态不一致：'+'；'.join(problems),ver)
    if kind == 'crate_contact_area':
        mode=str(current.get('contact_area_mode') or 'UNSPECIFIED').upper()
        if mode=='FULL_BASE': area=float(current.get('length_mm') or 0)*float(current.get('width_mm') or 0)/1e6
        elif mode=='SKIDS': area=float(current.get('skid_count') or 0)*float(current.get('skid_width_mm') or 0)*float(current.get('skid_contact_length_mm') or 0)/1e6
        elif mode=='CUSTOM': area=float(current.get('custom_contact_area_m2') or 0)
        else: area=0.0
        if claim.get('effective_contact_area_m2') in (None,''):
            return False, '接触面积凭证未保存有效面积核对值', {'ok':False,'problems':['接触面积凭证未保存有效面积核对值']}
        ok,_=numeric_close(area,float(claim.get('effective_contact_area_m2')),abs_tol=0.0001,rel_tol=0.001)
        problems=[] if ok else [f"有效接触面积：系统 {area:g} m² / 凭证核对值 {float(claim.get('effective_contact_area_m2')):g} m²"]
        if str(claim.get('mode') or '') != mode: problems.append('接触面积模式已变化')
        if str(claim.get('source_type') or '') != str(current.get('contact_area_source_type') or ''): problems.append('接触面积来源类型已变化')
        ver={'ok':not problems,'problems':problems}
        return (True,'',ver) if not problems else (False,'当前接触面积已与凭证核对状态不一致：'+'；'.join(problems),ver)
    if kind == 'crate_lashing':
        problems=[]
        if int(claim.get('lashing_points') or 0) != int(current.get('lashing_points') or 0): problems.append('系固点数量已变化')
        if bool(claim.get('lashing_required')) != bool(current.get('lashing_required')): problems.append('系固要求已变化')
        ver={'ok':not problems,'problems':problems}
        return (True,'',ver) if not problems else (False,'当前系固登记已与凭证核对状态不一致：'+'；'.join(problems),ver)
    return False, f'未知凭证核对类型：{kind}', {'ok': False, 'problems': [f'未知凭证核对类型：{kind}']}


def invalidate_crate_size(con, crate_id, source='尺寸变化后待重新人工对照'):
    con.execute(
        """UPDATE crates
           SET size_confirmed=0,size_source=?,size_confirmed_at=NULL,size_evidence_id=NULL
           WHERE id=?""",
        (source, crate_id),
    )


def invalidate_crate_gross(con, crate_id, source='箱内内容变化后待重新实称'):
    con.execute(
        """UPDATE crates
           SET actual_gross_weight_kg=NULL,gross_weight_confirmed=0,gross_weight_evidence_id=NULL,tare_weight_source=?
           WHERE id=?""",
        (source, crate_id),
    )


def invalidate_item_size(con, item_id, source='数据变化后待重新人工对照'):
    con.execute(
        """UPDATE items
           SET size_grade='B',size_subgrade='B1',size_source=?,size_note='原A级凭证因当前尺寸变化自动失效',
               size_evidence_id=NULL,size_evidence_scope='',size_evidence_scope_key=''
           WHERE id=?""",
        (source, item_id),
    )


def invalidate_item_weight(con, item_id, source='数据变化后待重新人工对照'):
    con.execute(
        """UPDATE items
           SET weight_grade='B',weight_subgrade='B1',weight_source=?,weight_note='原A级凭证因当前重量变化自动失效',
               weight_evidence_id=NULL,weight_evidence_scope='',weight_evidence_scope_key=''
           WHERE id=?""",
        (source, item_id),
    )
