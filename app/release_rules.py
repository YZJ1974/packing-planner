from __future__ import annotations

from app.credibility_consistency import evidence_claim_matches_current


def _item_evidence_meta(item, kind):
    item_id=str(item.get('id') or '')
    if kind=='size':
        return {
            'eid':item.get('size_evidence_id'),
            'scope':item.get('size_evidence_scope') or 'item',
            'key':item.get('size_evidence_scope_key') or item_id,
            'etypes':('measured_or_approved_size','approved_dimension'),
            'claim_kind':'item_size',
        }
    return {
        'eid':item.get('weight_evidence_id'),
        'scope':item.get('weight_evidence_scope') or 'item',
        'key':item.get('weight_evidence_scope_key') or item_id,
        'etypes':('weighing_or_approved_weight',),
        'claim_kind':'item_weight',
    }


def item_evidence_ok(item, evidence_matcher, evidence_loader, kind):
    item_id=str(item.get('id') or '')
    meta=_item_evidence_meta(item,kind)
    if meta['scope']=='item':
        linked=evidence_matcher(meta['eid'],scope_type='item',scope_key=item_id,evidence_types=meta['etypes'],entity_types=('item',),entity_ids=(item_id,),require_verified=True)
    else:
        linked=evidence_matcher(meta['eid'],scope_type=meta['scope'],scope_key=meta['key'],evidence_types=meta['etypes'],require_verified=True)
    if not linked:
        return False
    row=evidence_loader(meta['eid']) if meta['eid'] else None
    ok,_,_=evidence_claim_matches_current(row,item,meta['claim_kind'])
    return ok


def item_evidence_problem(item, evidence_matcher, evidence_loader, kind):
    if item_evidence_ok(item,evidence_matcher,evidence_loader,kind):
        return ''
    meta=_item_evidence_meta(item,kind); row=evidence_loader(meta['eid']) if meta['eid'] else None
    if row:
        ok,problem,_=evidence_claim_matches_current(row,item,meta['claim_kind'])
        if not ok and problem:
            return problem
    return '凭证对象/范围/核对状态无效或凭证文件缺失'


def data_quality_release_issues(items, crates, crate_audit_status, evidence_matcher, evidence_loader, release_meta):
    issues=[]
    crate_ids_with_items={i.get('crate_id') for i in items if i.get('crate_id')}
    for c in crates:
        if c.get('id') not in crate_ids_with_items: continue
        cid=str(c.get('id') or '')
        ast=crate_audit_status.get(int(c.get('id')),'未校核')
        if ast!='通过': issues.append(f"{c.get('name')}木箱规划校核状态={ast}")
        if c.get('actual_gross_weight_kg') in (None,''):
            issues.append(f"{c.get('name')}未录入实称毛重")
        else:
            gross_linked=int(c.get('gross_weight_confirmed') or 0) and evidence_matcher(c.get('gross_weight_evidence_id'),scope_type='item',scope_key=cid,evidence_types=('gross_weight_ticket',),entity_types=('crate',),entity_ids=(cid,),require_verified=True)
            gross_row=evidence_loader(c.get('gross_weight_evidence_id')) if c.get('gross_weight_evidence_id') else None
            gross_current_ok=evidence_claim_matches_current(gross_row,c,'crate_gross')[0] if gross_linked else False
            if not gross_linked or not gross_current_ok:
                issues.append(f"{c.get('name')}实称毛重凭证已失效或当前毛重与凭证核对值不一致")
        size_linked=int(c.get('size_confirmed') or 0) and evidence_matcher(c.get('size_evidence_id'),scope_type='item',scope_key=cid,evidence_types=('measured_or_approved_crate_size',),entity_types=('crate',),entity_ids=(cid,),require_verified=True)
        size_row=evidence_loader(c.get('size_evidence_id')) if c.get('size_evidence_id') else None
        size_current_ok=evidence_claim_matches_current(size_row,c,'crate_size')[0] if size_linked else False
        if not size_linked or not size_current_ok:
            issues.append(f"{c.get('name')}木箱外形凭证已失效或当前尺寸与凭证核对值不一致")

    bad_sizes=[]
    for i in items:
        if str(i.get('size_grade') or 'C')!='A' or not item_evidence_ok(i,evidence_matcher,evidence_loader,'size'):
            problem=item_evidence_problem(i,evidence_matcher,evidence_loader,'size')
            bad_sizes.append(f"{i.get('name')}（{problem}）")
    if bad_sizes: issues.append('存在未达到A级人工对照一致性的尺寸：'+','.join(str(x) for x in bad_sizes[:5]))

    bad_weights=[]
    for i in items:
        if str(i.get('weight_grade') or 'C')!='A' or not item_evidence_ok(i,evidence_matcher,evidence_loader,'weight'):
            problem=item_evidence_problem(i,evidence_matcher,evidence_loader,'weight')
            bad_weights.append(f"{i.get('name')}（{problem}）")
    if bad_weights: issues.append('存在未达到A级人工对照一致性的单件重量：'+','.join(str(x) for x in bad_weights[:5]))

    if not str((release_meta or {}).get('project_name') or '').strip(): issues.append('未填写数据对照版项目名称')
    if not str((release_meta or {}).get('approver') or '').strip(): issues.append('未填写单一责任确认人')
    return issues
