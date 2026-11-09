from __future__ import annotations

import time
from app.input_validation import number as checked_number
from urllib.parse import quote
from fastapi import APIRouter, Form, UploadFile, File
from fastapi.responses import RedirectResponse
from app.evidence_validation import verify_dimension_claim, verify_weight_claim

router = APIRouter()
_CTX = {}


def configure(**kwargs):
    _CTX.clear(); _CTX.update(kwargs)


def _need(name):
    if name not in _CTX:
        raise RuntimeError(f'credibility route dependency not configured: {name}')
    return _CTX[name]


@router.post('/items/{item_id}/confirm-size')
async def confirm_item_size(item_id:int, evidence:UploadFile=File(...), evidence_note:str=Form(''),
                            evidence_length_mm:float=Form(...), evidence_width_mm:float=Form(...), evidence_height_mm:float=Form(...),
                            evidence_scope:str=Form('item'), evidence_scope_key:str=Form('')):
    db=_need('db'); release_meta_load=_need('release_meta_load'); confirmation_user=_need('confirmation_user'); store=_need('evidence_store_getter')(); record=_need('record_event')
    con=db(); row=con.execute('SELECT * FROM items WHERE id=?',(item_id,)).fetchone(); con.close()
    if not row: return RedirectResponse(url='/?error='+quote('材料不存在。'),status_code=303)
    try:
        user=confirmation_user(); scope=str(evidence_scope or 'item').strip().lower()
        if scope not in {'item','batch','type','project'}: raise ValueError('凭证作用范围无效。')
        key=str(item_id) if scope=='item' else str(evidence_scope_key or '').strip()
        if scope=='project': key=str((release_meta_load() or {}).get('project_name') or key).strip()
        if not key: raise ValueError('批次/类型/项目凭证必须填写作用范围标识。')
        ver=verify_dimension_claim((row['length_mm'],row['width_mm'],row['height_mm']),(evidence_length_mm,evidence_width_mm,evidence_height_mm))
        if not ver['ok']: raise ValueError('人工读取的凭证尺寸与系统尺寸不一致：'+'；'.join(ver['problems']))
        raw=await evidence.read(); ev=store.save_evidence(entity_type='item',entity_id=item_id,evidence_type='measured_or_approved_size',filename=evidence.filename,content=raw,note=evidence_note,created_by=user,
            scope_type=scope,scope_key=key,claimed_values={'length_mm':evidence_length_mm,'width_mm':evidence_width_mm,'height_mm':evidence_height_mm},verification=ver)
        sub={'item':'A1','batch':'A2','type':'A3','project':'A3'}[scope]
        con=db(); con.execute("UPDATE items SET size_grade='A',size_subgrade=?,size_source='凭证登记+人工数值对照',size_note=?,size_evidence_id=?,size_evidence_scope=?,size_evidence_scope_key=? WHERE id=?",
            (sub,f"凭证#{ev['id']} 已登记；责任人手工录入凭证可见值并与系统值对照一致，SHA256={ev['sha256'][:16]}…",ev['id'],scope,key,item_id)); con.commit(); con.close()
        record('confirm_item_size','item',item_id,row['name'],{'evidence_id':ev['id'],'sha256':ev['sha256'],'scope':scope,'scope_key':key,'verification':ver,'method':'人工读取凭证值后录入对照；软件未OCR读取正文'})
        return RedirectResponse(url='/?message='+quote(f'材料尺寸凭证已登记，人工核对值一致，记录为{sub}。'),status_code=303)
    except Exception as e: return RedirectResponse(url='/?error='+quote(str(e)),status_code=303)


@router.post('/items/{item_id}/confirm-weight')
async def confirm_item_weight(item_id:int, evidence:UploadFile=File(...), evidence_note:str=Form(''), evidence_weight_kg:float=Form(...),
                              evidence_scope:str=Form('item'), evidence_scope_key:str=Form('')):
    db=_need('db'); release_meta_load=_need('release_meta_load'); confirmation_user=_need('confirmation_user'); store=_need('evidence_store_getter')(); record=_need('record_event')
    con=db(); row=con.execute('SELECT * FROM items WHERE id=?',(item_id,)).fetchone(); con.close()
    if not row: return RedirectResponse(url='/?error='+quote('材料不存在。'),status_code=303)
    try:
        user=confirmation_user(); scope=str(evidence_scope or 'item').strip().lower()
        if scope not in {'item','batch','type','project'}: raise ValueError('凭证作用范围无效。')
        key=str(item_id) if scope=='item' else str(evidence_scope_key or '').strip()
        if scope=='project': key=str((release_meta_load() or {}).get('project_name') or key).strip()
        if not key: raise ValueError('批次/类型/项目凭证必须填写作用范围标识。')
        ver=verify_weight_claim(row['weight_kg'],evidence_weight_kg)
        if not ver['ok']: raise ValueError('人工读取的凭证重量与系统重量不一致：'+'；'.join(ver['problems']))
        raw=await evidence.read(); ev=store.save_evidence(entity_type='item',entity_id=item_id,evidence_type='weighing_or_approved_weight',filename=evidence.filename,content=raw,note=evidence_note,created_by=user,
            scope_type=scope,scope_key=key,claimed_values={'weight_kg':evidence_weight_kg},verification=ver)
        sub={'item':'A1','batch':'A2','type':'A3','project':'A3'}[scope]
        con=db(); con.execute("UPDATE items SET weight_grade='A',weight_subgrade=?,weight_source='凭证登记+人工数值对照',weight_note=?,weight_evidence_id=?,weight_evidence_scope=?,weight_evidence_scope_key=? WHERE id=?",
            (sub,f"凭证#{ev['id']} 已登记；责任人手工录入凭证可见值并与系统值对照一致，SHA256={ev['sha256'][:16]}…",ev['id'],scope,key,item_id)); con.commit(); con.close()
        record('confirm_item_weight','item',item_id,row['name'],{'evidence_id':ev['id'],'sha256':ev['sha256'],'scope':scope,'scope_key':key,'verification':ver,'method':'人工读取凭证值后录入对照；软件未OCR读取正文'})
        return RedirectResponse(url='/?message='+quote(f'材料重量凭证已登记，人工核对值一致，记录为{sub}。'),status_code=303)
    except Exception as e: return RedirectResponse(url='/?error='+quote(str(e)),status_code=303)


@router.post('/crates/{crate_id}/confirm-size')
async def confirm_crate_size(crate_id:int, evidence:UploadFile=File(...), evidence_note:str=Form(''),
                             evidence_length_mm:float=Form(...), evidence_width_mm:float=Form(...), evidence_height_mm:float=Form(...)):
    db=_need('db'); confirmation_user=_need('confirmation_user'); store=_need('evidence_store_getter')(); record=_need('record_event')
    con=db(); row=con.execute('SELECT * FROM crates WHERE id=?',(crate_id,)).fetchone(); con.close()
    if not row: return RedirectResponse(url='/?error='+quote('木箱不存在。'),status_code=303)
    try:
        user=confirmation_user(); ver=verify_dimension_claim((row['length_mm'],row['width_mm'],row['height_mm']),(evidence_length_mm,evidence_width_mm,evidence_height_mm))
        if not ver['ok']: raise ValueError('人工读取的木箱凭证尺寸与当前系统外形不一致：'+'；'.join(ver['problems']))
        raw=await evidence.read(); ev=store.save_evidence(entity_type='crate',entity_id=crate_id,evidence_type='measured_or_approved_crate_size',filename=evidence.filename,content=raw,note=evidence_note,created_by=user,
            scope_type='item',scope_key=str(crate_id),claimed_values={'length_mm':evidence_length_mm,'width_mm':evidence_width_mm,'height_mm':evidence_height_mm},verification=ver)
        now=time.strftime('%Y-%m-%d %H:%M:%S'); con=db(); con.execute("UPDATE crates SET size_confirmed=1,size_source='凭证登记+人工数值对照',size_confirmed_at=?,size_evidence_id=? WHERE id=?",(now,ev['id'],crate_id)); con.commit(); con.close()
        record('confirm_crate_size','crate',crate_id,row['name'],{'confirmed_at':now,'evidence_id':ev['id'],'sha256':ev['sha256'],'verification':ver,'method':'人工读取凭证值后录入对照；软件未OCR读取正文'})
        return RedirectResponse(url='/?message='+quote('木箱外形凭证已登记，人工核对值一致。'),status_code=303)
    except Exception as e: return RedirectResponse(url='/?error='+quote(str(e)),status_code=303)


@router.post('/release-meta')
def save_release_meta(project_name:str=Form(''),approver:str=Form('')):
    save=_need('release_meta_save'); record=_need('record_event')
    save({'project_name':project_name.strip(),'approver':approver.strip()})
    record('save_release_meta','project','','',{'project_name':project_name.strip(),'approver':approver.strip()})
    return RedirectResponse(url='/?message='+quote('数据对照确认责任信息已保存。'),status_code=303)


@router.post('/crates/{crate_id}/actual-gross')
async def set_actual_gross(crate_id:int, actual_gross_weight_kg:float=Form(0), evidence:UploadFile=File(None), evidence_note:str=Form(''), evidence_weight_kg:float=Form(0)):
    db=_need('db'); confirmation_user=_need('confirmation_user'); store=_need('evidence_store_getter')(); record=_need('record_event')
    con=db(); c=con.execute('SELECT * FROM crates WHERE id=?',(crate_id,)).fetchone()
    if not c: con.close(); return RedirectResponse(url='/?error='+quote('木箱不存在。'),status_code=303)
    items=[dict(r) for r in con.execute('SELECT * FROM items WHERE crate_id=?',(crate_id,)).fetchall()]; con.close()
    cargo=0.0 if dict(c).get('package_origin')=='PREPACKED' else sum(float(i.get('qty') or 0)*float(i.get('weight_kg') or 0) for i in items)
    try: v=checked_number(actual_gross_weight_kg,'实称毛重')
    except ValueError as e: return RedirectResponse(url='/?error='+quote(str(e)),status_code=303)
    if v<=0 and dict(c).get('package_origin')=='PREPACKED': return RedirectResponse(url='/?error='+quote('已包装单元必须保留有效毛重，不能恢复未知箱皮估算'),status_code=303)
    if v<=0:
        con=db(); con.execute('UPDATE crates SET actual_gross_weight_kg=NULL,gross_weight_confirmed=0,gross_weight_evidence_id=NULL WHERE id=?',(crate_id,)); con.commit(); con.close()
        record('clear_crate_gross','crate',crate_id,str(c['name']),{})
        return RedirectResponse(url='/?message='+quote(f"{c['name']} 已清除实称毛重，恢复工程估算。"),status_code=303)
    if v<cargo-1e-6: return RedirectResponse(url='/?error='+quote(f'实称毛重 {v:.1f} kg 小于箱内设备净重 {cargo:.1f} kg，请核对。'),status_code=303)
    if not evidence or not getattr(evidence,'filename',None): return RedirectResponse(url='/?error='+quote('确认实称毛重必须上传秤票/称重记录凭证。'),status_code=303)
    try:
        user=confirmation_user(); ver=verify_weight_claim(v,evidence_weight_kg)
        if not ver['ok']: raise ValueError('人工读取的秤票值与录入实称毛重不一致：'+'；'.join(ver['problems']))
        raw=await evidence.read(); ev=store.save_evidence(entity_type='crate',entity_id=crate_id,evidence_type='gross_weight_ticket',filename=evidence.filename,content=raw,note=evidence_note,created_by=user,
            scope_type='item',scope_key=str(crate_id),claimed_values={'gross_weight_kg':evidence_weight_kg},verification=ver)
        con=db(); con.execute("UPDATE crates SET actual_gross_weight_kg=?,gross_weight_confirmed=1,gross_weight_evidence_id=?,tare_weight_source='实称/凭证人工对照' WHERE id=?",(v,ev['id'],crate_id)); con.commit(); con.close()
        record('confirm_crate_gross','crate',crate_id,str(c['name']),{'gross_weight_kg':v,'evidence_id':ev['id'],'sha256':ev['sha256'],'verification':ver,'method':'人工读取秤票值后录入对照；软件未OCR读取正文'})
        return RedirectResponse(url='/?message='+quote(f"{c['name']} 实称毛重 {v:.1f} kg 已登记秤票并完成人工数值对照。"),status_code=303)
    except Exception as e: return RedirectResponse(url='/?error='+quote(str(e)),status_code=303)
