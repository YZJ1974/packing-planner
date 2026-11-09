import re

# Chinese domain terms can be substring-matched reasonably; English terms must be token matched
# so that "tee" never matches "steel".
PIPE_CN_INCLUDE=('管道','弯头','三通','异径','缩接','管帽','法兰')
PIPE_EN_INCLUDE=('pipe','elbow','tee','reducer','cap','flange')
PIPE_CN_HARD_EXCLUDE=('支架','吊架','托架','垫片','垫圈','电机','泵','仪表','电缆','底座','撬块')
PIPE_EN_HARD_EXCLUDE=('support','bracket','gasket','washer','motor','pump','instrument','cable','skid')
# Packaging words are annotations, not domain exclusions. A row such as "DN300弯头（木箱包装）"
# remains a fitting row.
PACKAGING_WORDS=('盒','箱','包装','box','package','packing')


def _en_tokens(text):
    return set(re.findall(r'[A-Za-z]+', str(text or '').lower()))


def purchase_row_classification(code, name):
    text=f'{code or ""} {name or ""}'.strip()
    low=text.lower()
    tokens=_en_tokens(text)
    cn_hard=[k for k in PIPE_CN_HARD_EXCLUDE if k in text]
    en_hard=[k for k in PIPE_EN_HARD_EXCLUDE if k in tokens]
    cn_inc=[k for k in PIPE_CN_INCLUDE if k in text]
    en_inc=[k for k in PIPE_EN_INCLUDE if k in tokens]
    dn=bool(re.search(r'\bDN\s*\d+',text,re.I))
    phi=bool(re.search(r'[Φφ]\s*\d+',text))

    # A clear fitting noun beats packaging annotations, but not a true device/accessory noun.
    if cn_hard or en_hard:
        return {'kind':'generic','confidence':0.97,'reason':'命中设备/附件主体排除词'}
    inc=cn_inc+en_inc
    if (dn or phi) and inc:
        return {'kind':'pipe','confidence':0.99,'reason':'规格标记+管件主体词'}
    if inc:
        return {'kind':'pipe','confidence':0.90,'reason':'管件主体词'}
    if dn or phi:
        # A bare DN/Φ is useful but not sufficient for strong routing; keep generic unless a pipe noun exists.
        return {'kind':'generic','confidence':0.65,'reason':'仅有DN/Φ规格，缺少管件主体词'}
    if any(k in low for k in PACKAGING_WORDS):
        return {'kind':'generic','confidence':0.78,'reason':'仅命中包装描述词'}
    return {'kind':'generic','confidence':0.75,'reason':'未命中管件主体规则'}


def purchase_row_kind(code, name):
    return purchase_row_classification(code,name)['kind']


def partition_purchase_tables(tables, strict_number):
    """Partition purchase sheets row-by-row with token-aware bilingual classification."""
    special=[]; generic=[]; stats={'special_rows':0,'generic_rows':0,'tables':0,'low_confidence_rows':0}
    for table in tables:
        rows=[list(r) for r in table.get('rows',[])]; hidx=None; idx={}
        for i,row in enumerate(rows[:30]):
            vals=[re.sub(r'\s+','',str(v or '')) for v in row]
            if all(k in vals for k in ('需要采购','名称','材料')):
                hidx=i; idx={v:j for j,v in enumerate(vals) if v in {'需要采购','名称','材料','代号','序号'}}; break
        if hidx is None:
            special.append(table); generic.append(table); continue
        stats['tables']+=1
        srows=[list(r) for r in rows]; grows=[list(r) for r in rows]
        qj=idx.get('需要采购'); nj=idx.get('名称'); cj=idx.get('代号')
        for ri in range(hidx+1,len(rows)):
            row=rows[ri]
            purchase = strict_number(row[qj] if qj is not None and qj<len(row) else None)
            if purchase is None or purchase<=0:
                if ri<len(srows): srows[ri]=['']*len(srows[ri])
                continue
            name=row[nj] if nj is not None and nj<len(row) else ''
            code=row[cj] if cj is not None and cj<len(row) else ''
            cls=purchase_row_classification(code,name)
            if cls['confidence']<0.8: stats['low_confidence_rows']+=1
            if cls['kind']=='pipe':
                stats['special_rows']+=1
                if ri<len(grows): grows[ri]=['']*len(grows[ri])
            else:
                stats['generic_rows']+=1
                if ri<len(srows): srows[ri]=['']*len(srows[ri])
        st=dict(table); st['rows']=srows
        gt=dict(table); gt['rows']=grows
        special.append(st); generic.append(gt)
    return special,generic,stats
