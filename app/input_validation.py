"""Shared finite, physical-domain validation for public input paths."""
import math

def number(value,label,minimum=0,strict=False,integer=False):
    try:n=float(value)
    except (TypeError,ValueError,OverflowError):raise ValueError(label+'必须是有效数字')
    if not math.isfinite(n) or (n<=minimum if strict else n<minimum) or (integer and not n.is_integer()):
        raise ValueError(label+('必须为正整数' if integer and strict else '必须为有限且符合范围的数值'))
    return int(n) if integer else n

def item_values(row):
    number(row.get('qty'),'数量',strict=True,integer=True)
    for key in ('length_mm','width_mm','height_mm'):number(row.get(key),key,strict=True)
    number(row.get('weight_kg'),'重量')
