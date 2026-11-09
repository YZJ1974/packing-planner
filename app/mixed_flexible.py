"""Combine unchanged closed-container bag planning with separate OT cargo."""
import copy


def candidates(m, packages, definitions):
    bags = [p for p in packages if p.get('flexible_unit_volume_m3')]
    cargo = [p for p in packages if not p.get('flexible_unit_volume_m3')]
    if not bags:
        return []
    results = []
    closed = [c for c in definitions if not c.get('open_top') and 'HQ' in c['code']]
    opened = [c for c in definitions if c.get('open_top')]
    for standard in closed:
        regular = [p for p in cargo if m.fits_container(p, standard)]
        special = [p for p in cargo if not m.fits_container(p, standard)]
        if not regular or not special:
            continue
        for ot in opened:
            if not all(m.fits_container(p, ot) for p in special):
                continue
            base = m._homogeneous_plan_fast(regular + bags, standard)
            extra = m._homogeneous_plan_fast(special, ot)
            if not base or not extra:
                continue
            result = copy.deepcopy(base)
            tail = copy.deepcopy(extra['containers'])
            offset = max((q['seq'] for b in result['containers'] for q in b['placements']), default=0)
            for box in tail:
                for q in box['placements']:
                    q['seq'] += offset
                box['index'] = len(result['containers']) + 1
                result['containers'].append(box)
            result.update(code='MIXED_FLEX_'+standard['code']+'_'+ot['code'],
                          label=f"{standard['code']}×{base['count']} + {ot['code']}×{extra['count']}",
                          count=len(result['containers']),
                          package_count=base['package_count']+extra['package_count'])
            for key, status in [('engineering_fail_count','不通过'),
                                ('engineering_high_risk_count','高风险')]:
                result[key] = sum(b['engineering_audit']['status']==status for b in result['containers'])
            result['engineering_review_count'] = sum(b['engineering_audit']['status']!='通过' for b in result['containers'])
            results.append(result)
    return results
