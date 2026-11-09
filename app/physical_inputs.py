from __future__ import annotations

SOURCE_LABELS={
    'UNCONFIRMED':'未确认',
    'MANUFACTURER_DATA':'厂家资料',
    'FIELD_MEASURED':'现场实测',
    'PACKING_SUPPLIER_APPROVED':'包装供应商批准',
    'ENGINEERING_CALC':'工程计算',
}
VERIFIED_SOURCES=frozenset(k for k in SOURCE_LABELS if k!='UNCONFIRMED')


def normalize_source(value):
    v=str(value or 'UNCONFIRMED').strip().upper()
    return v if v in SOURCE_LABELS else 'UNCONFIRMED'


def source_verified(value):
    return normalize_source(value) in VERIFIED_SOURCES


def source_label(value):
    return SOURCE_LABELS[normalize_source(value)]


def source_options():
    return [{'value':k,'label':v} for k,v in SOURCE_LABELS.items()]
