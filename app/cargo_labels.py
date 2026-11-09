"""Resolve displayed contents from explicit crate/item links, preserving unit IDs."""
import copy


def annotate_cargo_names(rec, items, crates):
    result = copy.deepcopy(rec)
    labels = {}
    for crate in crates:
        linked = [i for i in items if i.get('crate_id') == crate['id']]
        names = [str(i.get('name') or '').strip() for i in linked]
        if len(linked) == 1 and int(linked[0].get('qty') or 0) == 1:
            content = names[0]
        else:
            content = '；'.join(f"{n} ×{i.get('qty', 0)}" for n, i in zip(names, linked) if n)
        number = str(crate.get('name') or '')
        content = content or '箱内名称待补充'
        labels['crate:' + str(crate['id'])] = dict(
            cargo_name=content, package_no=number,
            display_name=f'{content}（箱号：{number}）')
    units = list(result.get('packages') or []) + list(result.get('oversize') or [])
    for plan in (result.get('plans') or {}).values():
        for box in plan.get('containers') or []:
            units.extend(box.get('placements') or [])
    for unit in units:
        label = labels.get(unit.get('asu_key'))
        if label:
            unit.update(label)
    return result
