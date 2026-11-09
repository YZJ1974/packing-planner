from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
from urllib.parse import quote
import sqlite3, math, csv, io, re, json, traceback, logging, uuid, zipfile, threading, time, copy, hashlib, os
from xml.etree import ElementTree as ET
from copy import deepcopy
from collections import OrderedDict
from app.credibility import EvidenceStore, canonical_hash
from app import asu_standard
from app.asu_service import AsuService
from app.selected_fleet import SelectedFleet
from app.cargo_shapes import cylinder_axis,upright_drum
from app import bulk_fill,flexible_fill
from app.bag_top_policy import load as _load_bag_top_policy
from app.trucks import Trucks
from app import trucks as truck_model
import sys
from app.input_validation import number as checked_number, item_values as checked_item_values
from app.evidence_validation import verify_dimension_claim, verify_weight_claim, numeric_close
from app.release_rules import data_quality_release_issues as _release_data_issues
from app.credibility_consistency import invalidate_crate_size, invalidate_crate_gross, evidence_claim_matches_current
from app.routes_credibility import router as credibility_router, configure as configure_credibility_routes
from app.import_security import validate_zip_archive as _stream_validate_zip_archive
from app.import_rules import partition_purchase_tables, purchase_row_classification
from app.container_specs import load_state as _container_specs_load_state, require_rows as _container_specs_require_rows, canonical_rows as _container_specs_canonical_rows, specs_sha256 as _container_specs_hash, ContainerSpecsError
from app.physical_constraints import floor_load_audit, door_maneuver_audit, lashing_audit, top_load_audit, floor_contact_pressure_kg_m2, contact_area_details, validate_contact_geometry, support_load_analysis
from app.physical_inputs import source_verified as _physical_source_verified, source_options as _physical_source_options, source_label as _physical_source_label, normalize_source as _normalize_physical_source
from app.solver_geometry import orientations, boxes_overlap, overlap_area_xy as _overlap_area_xy, occupied_overlap as _occupied_overlap, point_inside_occupied_shape as _point_inside_occupied_shape, shape_anchor_points as _shape_anchor_points
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_BREAK
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PACKING_PLANNER_DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "packing.db"
TEMPLATE_PATH = BASE_DIR / "material_import_template.xlsx"
IMPORT_DIR = DATA_DIR / "import_sessions"
IMPORT_DIR.mkdir(parents=True, exist_ok=True)
STRATEGY_PATH = DATA_DIR / "last_crate_strategy.json"
DIM_OVERRIDE_PATH = DATA_DIR / "dimension_overrides.json"
COST_PATH = DATA_DIR / "cost_settings.json"
DIM_TEMPLATE_PATH = BASE_DIR / "dimension_override_template.csv"
DEFAULT_PACKING_LIST_TEMPLATE_PATH = BASE_DIR / "packing_list_template.xlsx"
CUSTOM_PACKING_LIST_TEMPLATE_PATH = DATA_DIR / "packing_list_custom_template.xlsx"
PACKING_LIST_TEMPLATE_META_PATH = DATA_DIR / "packing_list_template_meta.json"
CONTAINER_LOAD_TEMPLATE_PATH = BASE_DIR / "container_load_template.xlsx"
DEFAULT_SHIPPING_MARK_TEMPLATE_PATH = BASE_DIR / "shipping_mark_template.docx"
CUSTOM_SHIPPING_MARK_TEMPLATE_PATH = DATA_DIR / "shipping_mark_custom_template.docx"
SHIPPING_MARK_TEMPLATE_META_PATH = DATA_DIR / "shipping_mark_template_meta.json"
SHIPPING_MARK_COMMON_PATH = DATA_DIR / "shipping_mark_common.json"
CRATE_WEIGHT_SETTINGS_PATH = DATA_DIR / "crate_weight_settings.json"
CONTAINER_SPECS_PATH = DATA_DIR / "container_specs.json"
CONTAINER_SPECS_META_PATH = DATA_DIR / "container_specs_meta.json"
RELEASE_META_PATH = DATA_DIR / "release_meta.json"
EXPORT_DIR = DATA_DIR / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)
EVIDENCE_DIR = DATA_DIR / "evidence"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Packing Planner V5.8.28 Engineering Screening")

@app.exception_handler(ValueError)
async def invalid_public_value(request,exc):
    return JSONResponse({'ok':False,'error':str(exc)},status_code=422)

LOG_PATH = DATA_DIR / "packing_planner.log"
logging.basicConfig(filename=str(LOG_PATH), level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("packing_planner")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

_LOCAL_HOSTS={'127.0.0.1','localhost','::1','testserver'}

@app.middleware("http")
async def local_origin_guard(request:Request, call_next):
    """Keep the local-only application local and reject obvious cross-site state changes.

    This is not an authentication system; it prevents a normal remote webpage from silently
    POSTing to the user's 127.0.0.1 planner through the browser.
    """
    host=(request.headers.get('host') or '').split(':')[0].strip('[]').lower()
    if host and host not in _LOCAL_HOSTS:
        return JSONResponse({'ok':False,'error':'仅允许本机访问。'},status_code=403)
    if request.method.upper() in {'POST','PUT','PATCH','DELETE'}:
        origin=(request.headers.get('origin') or '').strip()
        if origin:
            try:
                from urllib.parse import urlparse
                oh=(urlparse(origin).hostname or '').lower()
            except Exception: oh=''
            if oh not in _LOCAL_HOSTS:
                return JSONResponse({'ok':False,'error':'拒绝跨站请求。请从本软件页面执行该操作。'},status_code=403)
        fetch_site=(request.headers.get('sec-fetch-site') or '').lower()
        if fetch_site and fetch_site not in {'same-origin','same-site','none'}:
            return JSONResponse({'ok':False,'error':'拒绝跨站请求。'},status_code=403)
    return await call_next(request)

DEFAULT_CONTAINERS = [
    {"code":"20GP","inner_l":5898,"inner_w":2352,"inner_h":2393,"door_w":2340,"door_h":2280,"payload":28200,"open_top":False,"floor_load_kg_m2":None},
    {"code":"40GP","inner_l":12032,"inner_w":2352,"inner_h":2393,"door_w":2340,"door_h":2280,"payload":26700,"open_top":False,"floor_load_kg_m2":None},
    {"code":"40HQ","inner_l":12032,"inner_w":2352,"inner_h":2698,"door_w":2340,"door_h":2585,"payload":26400,"open_top":False,"floor_load_kg_m2":None},
    {"code":"20OT","inner_l":5898,"inner_w":2352,"inner_h":2350,"door_w":2340,"door_h":2350,"payload":28100,"open_top":True,"floor_load_kg_m2":None},
    {"code":"40OT","inner_l":12032,"inner_w":2352,"inner_h":2350,"door_w":2340,"door_h":2350,"payload":26600,"open_top":True,"floor_load_kg_m2":None},
]

def _container_specs_state():
    return _container_specs_load_state(CONTAINER_SPECS_PATH,CONTAINER_SPECS_META_PATH,DEFAULT_CONTAINERS)

def _containers_load():
    return _container_specs_require_rows(CONTAINER_SPECS_PATH,CONTAINER_SPECS_META_PATH,DEFAULT_CONTAINERS)

def _active_containers():
    """Strict single source used by planner, page and release gate. Never silently falls back after project specs exist."""
    if TRUCKS.mode()=="truck":return TRUCKS.rows()
    return _containers_load()

# Compatibility snapshot only. Core solver paths call _active_containers() per calculation.
CONTAINERS = [dict(x) for x in DEFAULT_CONTAINERS]


DEFAULT_COST_SETTINGS={
    "enabled":False,"currency":"CNY","crate_fixed":120.0,"crate_m3":650.0,"crate_labor":100.0,
    "20GP":9000.0,"40GP":12500.0,"40HQ":13500.0,"20OT":16000.0,"40OT":22000.0,"oog_each":5000.0
}

BOX_DATA_LABEL='箱级数据对照版_仅包装单元层级_人工凭证值对照_非运输签证依据'
CONTAINER_DATA_PASS_LABEL='装柜数据对照版_静态规划校核通过_非运输签证依据'
CONTAINER_DATA_REVIEW_LABEL='装柜数据对照版_工程复核未关闭_NOT_FOR_RELEASE_非运输签证依据'
SHIPMENT_PACKAGE_PASS_LABEL='整票数据对照归档包_静态规划校核通过_非结构运输签证依据'
SHIPMENT_PACKAGE_REVIEW_LABEL='整票数据对照归档包_工程复核未关闭_NOT_FOR_RELEASE_非结构运输签证依据'
# compatibility alias for older code paths; formal exports use _container_output_label(plan).
CONTAINER_DATA_LABEL=CONTAINER_DATA_PASS_LABEL
SHIPMENT_PACKAGE_LABEL=SHIPMENT_PACKAGE_PASS_LABEL

DEFAULT_SHIPPING_MARK_COMMON={
    "consignee":"",
    "sc_name":"",
    "sc_no":"",
    "delivery_date":"",
    "packing_cn":"出口木箱",
    "packing_en":"Export wooden box",
    "shipment_cn":"",
    "shipment_en":"",
    "supplier_cn":"",
    "supplier_en":""
}

# V4.4.9 engineering crate-tare model. These are transparent planning assumptions,
# not a substitute for the packing supplier's final structural design or actual weighing.
DEFAULT_CRATE_WEIGHT_SETTINGS={
    "model":"structural",
    "panel_density":500.0,
    "timber_density":480.0,
    "hardware_pct":4.0,
    "light_max_cargo":500.0,
    "medium_max_cargo":2000.0,
    # V4.5.1: classification by cargo weight alone under-specifies panel/frame size for
    # long, slender crates (e.g. a 6m pipe box weighing only a few kg still needs enough
    # panel/rib strength to survive handling over that span). A long max dimension bumps
    # the structural class up by at least one level regardless of how light the cargo is.
    "long_span_upgrade_mm":3000.0,
    # V4.6 geometry: structure has real thickness/intrusion; padding remains cargo handling clearance.
    "geometry_model":"structural_clearance",
    "side_frame_intrusion_factor":1.0,
    "roof_frame_intrusion_factor":1.0,
    "floor_panel_mm":9.0,
    # Planning top-load limits. None = unknown / not structurally certified: stacking is
    # geometrically allowed but the container plan is flagged for engineering review.
    # Set 0 explicitly to prohibit stacking, or a positive value when a supplier-approved limit exists.
    "light_top_load_kg":None,
    "medium_top_load_kg":None,
    "heavy_top_load_kg":None,
    # CG planning thresholds. Horizontal values are offsets from centre; vertical values are CG height percentage.
    "container_cg_review_pct":10.0,
    "container_cg_high_risk_pct":20.0,
    "container_cg_fail_pct":20.0,
    "container_cg_z_review_pct":60.0,
    "container_cg_z_high_risk_pct":75.0,
    "crate_cg_review_pct":10.0,
    "crate_cg_high_risk_pct":20.0,
    "crate_cg_z_review_pct":60.0,
    "crate_cg_z_high_risk_pct":75.0,
    # V5.5: unconfirmed CG becomes an engineering-review finding only for materially important packages.
    "critical_cg_weight_kg":500.0,
    "critical_cg_share_pct":10.0
}

CRATE_STRUCTURE_PROFILES={
    "light":{"label":"轻型","panel_t_mm":5.0,"frame_w_mm":25.0,"frame_h_mm":35.0,"runner_w_mm":50.0,"runner_h_mm":70.0,"rib_spacing_mm":1200.0,"cross_spacing_mm":1500.0,"hardware_min_kg":1.5},
    "medium":{"label":"中型","panel_t_mm":9.0,"frame_w_mm":40.0,"frame_h_mm":60.0,"runner_w_mm":80.0,"runner_h_mm":100.0,"rib_spacing_mm":800.0,"cross_spacing_mm":1200.0,"hardware_min_kg":3.0},
    "heavy":{"label":"重型","panel_t_mm":12.0,"frame_w_mm":50.0,"frame_h_mm":80.0,"runner_w_mm":100.0,"runner_h_mm":120.0,"rib_spacing_mm":600.0,"cross_spacing_mm":1000.0,"hardware_min_kg":5.0}
}

_CRATE_WEIGHT_SETTINGS_CACHE=None

def _json_load(path,default):
    try:
        if path.exists():
            data=json.loads(path.read_text(encoding="utf-8"))
            return data
    except Exception:
        pass
    return json.loads(json.dumps(default,ensure_ascii=False))

def _json_save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")

def _refresh_containers():
    global CONTAINERS
    CONTAINERS=_active_containers()
    return [dict(x) for x in CONTAINERS]

def _cost_settings_load():
    d=DEFAULT_COST_SETTINGS.copy(); d.update(_json_load(COST_PATH,{})); return d

def _cost_settings_save(d):
    base=DEFAULT_COST_SETTINGS.copy(); base.update(d); _json_save(COST_PATH,base)


def _shipping_mark_common_load():
    d=DEFAULT_SHIPPING_MARK_COMMON.copy(); d.update(_json_load(SHIPPING_MARK_COMMON_PATH,{})); return d

def _shipping_mark_common_save(d):
    base=DEFAULT_SHIPPING_MARK_COMMON.copy(); base.update(d); _json_save(SHIPPING_MARK_COMMON_PATH,base)

DEFAULT_RELEASE_META={"project_name":"","approver":""}

def _release_meta_load():
    d=DEFAULT_RELEASE_META.copy(); d.update(_json_load(RELEASE_META_PATH,{})); return d

def _release_meta_save(d):
    base=DEFAULT_RELEASE_META.copy(); base.update(d); _json_save(RELEASE_META_PATH,base)

def _container_specs_meta_load():
    d=_json_load(CONTAINER_SPECS_META_PATH,{})
    return d if isinstance(d,dict) else {}

_CONTAINER_SPEC_FIELDS=('code','inner_l','inner_w','inner_h','door_w','door_h','payload','open_top')

def _container_spec_number(v):
    x=round(float(v),6)
    return int(x) if float(x).is_integer() else x

def _canonical_container_specs_rows(rows):
    return _container_specs_canonical_rows(rows)

def _container_specs_sha256(rows):
    return _container_specs_hash(rows)

def _current_container_specs_sha256():
    st=_container_specs_state()
    if st.get('mode')!='actual':
        if st.get('mode')=='invalid': raise ValueError(st.get('error') or '当前实际柜参数无效。')
        raise ValueError('当前未启用实际柜参数。')
    return str(st.get('sha256') or '')


def _crate_weight_settings_load():
    global _CRATE_WEIGHT_SETTINGS_CACHE
    if _CRATE_WEIGHT_SETTINGS_CACHE is None:
        d=DEFAULT_CRATE_WEIGHT_SETTINGS.copy(); d.update(_json_load(CRATE_WEIGHT_SETTINGS_PATH,{})); _CRATE_WEIGHT_SETTINGS_CACHE=d
    return dict(_CRATE_WEIGHT_SETTINGS_CACHE)

def _crate_weight_settings_save(d):
    global _CRATE_WEIGHT_SETTINGS_CACHE
    base=DEFAULT_CRATE_WEIGHT_SETTINGS.copy(); base.update(d); _json_save(CRATE_WEIGHT_SETTINGS_PATH,base); _CRATE_WEIGHT_SETTINGS_CACHE=base
    if '_REPACK_CACHE' in globals():
        with _REPACK_CACHE_LOCK:
            _REPACK_CACHE.clear(); _REPACK_CACHE_ORDER.clear()
    if '_PERF_CACHE' in globals():
        _PERF_CACHE['fast'].clear(); _PERF_CACHE['deep'].clear()

_CLASS_ORDER=['light','medium','heavy']

def _asu_profile():
    return asu_standard.profile(DATA_DIR/'asu_project.json')

def _planning_specs_state():
    return TRUCKS.state() if TRUCKS.mode()=='truck' else _container_specs_state()

def _needs_door_check(c):
    if c.get('vehicle_type')=='truck':return c.get('body_type')=='box'
    return not (bool(c.get('open_top')) or 'OT' in str(c.get('code','')))

def _asu_margin(c):
    return asu_standard.margin(_asu_profile(),c)

def _crate_structure_profile(cargo_weight,max_span_mm=0):
    s=_crate_weight_settings_load(); c=float(cargo_weight or 0)
    if c <= float(s.get('light_max_cargo',500) or 500): cls='light'
    elif c <= float(s.get('medium_max_cargo',2000) or 2000): cls='medium'
    else: cls='heavy'
    span=float(max_span_mm or 0)
    upgrade_mm=float(s.get('long_span_upgrade_mm',3000) or 3000)
    # Long spans need thicker panels/denser bracing than cargo weight alone implies, but a
    # long-and-light crate (e.g. a slender pipe box) is normally still built as a lighter
    # skeleton/batten crate in practice, not a fully panelled heavy box. So a long span is
    # only allowed to bump the class by ONE tier, however long the item is — never jump
    # straight from light to heavy on span alone — keeping the estimate conservative
    # without grossly overstating tare (and therefore cost) for long, light items.
    idx=_CLASS_ORDER.index(cls)
    if span>=upgrade_mm: idx=min(2,idx+1)
    cls=_CLASS_ORDER[idx]
    prof=dict(CRATE_STRUCTURE_PROFILES[cls])
    project=_asu_profile()
    if project['enabled']:
        prof['panel_t_mm']=max(prof['panel_t_mm'],asu_standard.panel_min(project))
        prof['runner_h_mm']=max(prof['runner_h_mm'],100.0)
    return cls,prof

def _crate_structure_tare(l_mm,w_mm,h_mm,cargo_weight=0):
    s=_crate_weight_settings_load()
    L=max(float(l_mm),1)/1000.0; W=max(float(w_mm),1)/1000.0; H=max(float(h_mm),1)/1000.0
    max_span_mm=max(float(l_mm or 0),float(w_mm or 0),float(h_mm or 0))
    cls,p=_crate_structure_profile(cargo_weight,max_span_mm)
    pd=float(s.get('panel_density',500) or 500); td=float(s.get('timber_density',480) or 480)
    hp=max(0.0,float(s.get('hardware_pct',4) or 4))/100.0
    pt=float(p['panel_t_mm'])/1000.0
    fa=(float(p['frame_w_mm'])/1000.0)*(float(p['frame_h_mm'])/1000.0)
    ra=(float(p['runner_w_mm'])/1000.0)*(float(p['runner_h_mm'])/1000.0)
    # Six enclosed faces. This is a material take-off approximation for an export plywood box.
    panel_area=2.0*(L*W+L*H+W*H)
    panel_mass=panel_area*pt*pd
    # 12 perimeter frame members.
    edge_len=4.0*(L+W+H)
    edge_mass=edge_len*fa*td
    # Intermediate hoop frames along the long axis.
    rib_n=max(0,int(math.ceil((L*1000.0)/float(p['rib_spacing_mm'])))-1)
    rib_len=rib_n*2.0*(W+H)
    rib_mass=rib_len*fa*td
    # Bottom runners/skids.
    runner_n=2 if W<=1.2 else (3 if W<=2.0 else 4)
    if _asu_profile()['enabled']: runner_n=max(runner_n,asu_standard.runners(W*1000) or 5)
    runner_len=runner_n*L
    runner_mass=runner_len*ra*td
    # Cross bearers under the floor.
    cross_n=max(2,int(math.ceil((L*1000.0)/float(p['cross_spacing_mm'])))+1)
    cross_len=cross_n*W
    cross_mass=cross_len*ra*td
    wood=panel_mass+edge_mass+rib_mass+runner_mass+cross_mass
    hardware=max(float(p['hardware_min_kg']),wood*hp)
    total=wood+hardware
    cargo_cls='light' if float(cargo_weight or 0)<=float(s.get('light_max_cargo',500) or 500) else ('medium' if float(cargo_weight or 0)<=float(s.get('medium_max_cargo',2000) or 2000) else 'heavy')
    span_upgraded=_CLASS_ORDER.index(cls)>_CLASS_ORDER.index(cargo_cls)
    note='工程结构估算：板材+框架+滑木+横档+辅材；最终唛头/报关建议以实际称重覆盖。'
    if span_upgraded:
        note+=f' 因箱体最长边达 {max_span_mm:.0f} mm，结构等级已由货重对应的"{CRATE_STRUCTURE_PROFILES[cargo_cls]["label"]}"自动提升为"{p["label"]}"，避免长跨度薄板强度不足。'
    return {
        'model':'structural','class':cls,'class_label':p['label'],'total_kg':round(total,1),
        'panel_kg':round(panel_mass,1),'edge_frame_kg':round(edge_mass,1),'rib_kg':round(rib_mass,1),
        'runner_kg':round(runner_mass,1),'cross_bearer_kg':round(cross_mass,1),'hardware_kg':round(hardware,1),
        'panel_area_m2':round(panel_area,2),'rib_count':rib_n,'runner_count':runner_n,'cross_count':cross_n,
        'panel_t_mm':p['panel_t_mm'],'frame_mm':f"{int(p['frame_w_mm'])}×{int(p['frame_h_mm'])}",
        'runner_mm':f"{int(p['runner_w_mm'])}×{int(p['runner_h_mm'])}",
        'span_upgraded':span_upgraded,'note':note
    }

def _crate_tare_cg(l_mm,w_mm,h_mm,cargo_weight=0,effective_tare=None):
    """Planning CG of the crate structure itself. X/Y are symmetric by model; Z is mass-weighted.
    This is still a planning approximation, not a supplier structural calculation.
    """
    L=float(l_mm or 0); W=float(w_mm or 0); H=float(h_mm or 0)
    br=_crate_structure_tare(L,W,H,cargo_weight)
    total=float(br.get('total_kg') or 0)
    if effective_tare not in (None,''):
        try: total_eff=max(0.0,float(effective_tare))
        except Exception: total_eff=total
    else: total_eff=total
    upper=float(br.get('panel_kg',0))+float(br.get('edge_frame_kg',0))+float(br.get('rib_kg',0))+float(br.get('hardware_kg',0))
    lower=float(br.get('runner_kg',0))+float(br.get('cross_bearer_kg',0))
    cls,prof=_crate_structure_profile(cargo_weight,max(L,W,H))
    lower_z=max(1.0,float(prof.get('runner_h_mm',80))/2.0)
    z=(upper*(H/2.0)+lower*lower_z)/max(upper+lower,1e-9)
    return {'x':L/2.0,'y':W/2.0,'z':max(0.0,min(H,z)),'weight':total_eff}

def _crate_geometry(l_mm,w_mm,h_mm,cargo_weight=0,padding_mm=100):
    """V4.6 structure-aware crate geometry.

    Outer envelope includes panels/frame/base. Packing coordinates live in the USABLE cargo
    volume after subtracting structural intrusion and handling clearance (padding).
    The model is deliberately transparent and conservative; supplier drawings may override it.
    """
    L=max(float(l_mm or 0),1.0); W=max(float(w_mm or 0),1.0); H=max(float(h_mm or 0),1.0)
    pad=max(0.0,float(padding_mm or 0))
    settings=_crate_weight_settings_load()
    cls,prof=_crate_structure_profile(cargo_weight,max(L,W,H))
    pt=float(prof['panel_t_mm'])
    side_factor=max(0.0,float(settings.get('side_frame_intrusion_factor',1.0) or 1.0))
    roof_factor=max(0.0,float(settings.get('roof_frame_intrusion_factor',1.0) or 1.0))
    # Frame width intrudes horizontally; roof frame height intrudes vertically.
    side_struct=pt + float(prof['frame_w_mm'])*side_factor
    end_struct=side_struct
    roof_struct=pt + float(prof['frame_h_mm'])*roof_factor
    floor_panel=max(pt,float(settings.get('floor_panel_mm',pt) or pt))
    # Runners/skids are below the floor deck and therefore consume outer height, not cargo height.
    base_struct=floor_panel + float(prof['runner_h_mm'])
    clear_l=max(0.0,L-2.0*end_struct)
    clear_w=max(0.0,W-2.0*side_struct)
    clear_h=max(0.0,H-roof_struct-base_struct)
    usable_l=max(0.0,clear_l-2.0*pad)
    usable_w=max(0.0,clear_w-2.0*pad)
    usable_h=max(0.0,clear_h-2.0*pad)
    return {
        'class':cls,'class_label':prof['label'],'panel_t_mm':pt,
        'end_struct_mm':round(end_struct,1),'side_struct_mm':round(side_struct,1),
        'roof_struct_mm':round(roof_struct,1),'base_struct_mm':round(base_struct,1),
        'clear_l':clear_l,'clear_w':clear_w,'clear_h':clear_h,
        'usable_l':usable_l,'usable_w':usable_w,'usable_h':usable_h,
        'origin_x':end_struct+pad,'origin_y':side_struct+pad,'origin_z':base_struct+pad,
        'padding_mm':pad
    }

def _crate_required_outer(content_l,content_w,content_h,cargo_weight,padding_mm=100):
    """Smallest 50-mm outer envelope that contains content + structure + handling clearance.
    Iterate because long-span class upgrades can increase structural intrusion.
    """
    pad=max(0.0,float(padding_mm or 0)); cl=max(0.0,float(content_l)); cw=max(0.0,float(content_w)); ch=max(0.0,float(content_h))
    L=_ceil50(cl+2*pad+100); W=_ceil50(cw+2*pad+100); H=_ceil50(ch+2*pad+160)
    for _ in range(5):
        g=_crate_geometry(L,W,H,cargo_weight,pad)
        needL=_ceil50(cl+2*pad+2*g['end_struct_mm'])
        needW=_ceil50(cw+2*pad+2*g['side_struct_mm'])
        needH=_ceil50(ch+2*pad+g['roof_struct_mm']+g['base_struct_mm'])
        if (needL,needW,needH)==(L,W,H): break
        L,W,H=needL,needW,needH
    return L,W,H,_crate_geometry(L,W,H,cargo_weight,pad)

def _crate_top_load_allowance(cargo_weight,l_mm=0,w_mm=0,h_mm=0):
    settings=_crate_weight_settings_load(); cls,_=_crate_structure_profile(cargo_weight,max(float(l_mm or 0),float(w_mm or 0),float(h_mm or 0)))
    raw=settings.get(f'{cls}_top_load_kg',None)
    if raw in (None,''):
        return None,cls
    try: return max(0.0,float(raw)),cls
    except Exception: return None,cls

def _is_prepacked(crate):
    return str(crate.get('package_origin') or '')=='PREPACKED'


def _require_content_crate(crate_id):
    con=db(); row=con.execute('SELECT package_origin FROM crates WHERE id=?',(crate_id,)).fetchone(); con.close()
    if row and row['package_origin']=='PREPACKED':
        raise ValueError('这是已包装运输单元，箱内资料未知；不能自动拆箱、合箱、缩箱或重新分配箱内占位记录。')


def _crate_weight_result(crate,citems):
    if _is_prepacked(crate):
        gross=checked_number(crate.get('actual_gross_weight_kg'),'已包装单元毛重',strict=True)
        return {'cargo_weight':0.0,'estimated_tare':0.0,'effective_tare':0.0,'estimated_gross':gross,
                'gross_weight':gross,'gross_source':'已包装单元毛重（'+('已对照' if crate.get('gross_weight_confirmed') else '待确认')+'）',
                'actual_gross':gross,'actual_gross_warning':None,'internal_metrics_available':False,
                'breakdown':{'model':'prepacked','class':'prepacked','class_label':'内部资料未知','total_kg':0,'note':'净重/箱皮未知，不从毛重反推'}}
    cargo=sum(float(i.get('qty') or 0)*float(i.get('weight_kg') or 0) for i in citems)
    s=_crate_weight_settings_load()
    if str(s.get('model','structural'))=='legacy' and not _asu_profile()['enabled']:
        coeff=float(s.get('legacy_kg_per_m3',45) or 45)
        tare=round(max(0.0,float(crate.get('length_mm') or 0)*float(crate.get('width_mm') or 0)*float(crate.get('height_mm') or 0)/1e9*coeff),1)
        br={'model':'legacy','class':'legacy','class_label':'体积系数','total_kg':tare,'note':f'旧版体积系数法：{coeff:g} kg/m³'}
    else:
        br=_crate_structure_tare(crate.get('length_mm'),crate.get('width_mm'),crate.get('height_mm'),cargo)
        tare=float(br['total_kg'])
    actual=crate.get('actual_gross_weight_kg')
    try: actual=float(actual) if actual not in (None,'') else None
    except Exception: actual=None
    actual_warning=None
    if actual is not None and actual>0 and actual<cargo-1e-6:
        # Invalid input: recorded actual gross cannot be less than the cargo it contains.
        # Do not silently discard it — surface the problem and fall back to the estimate.
        actual_warning=f'录入的实称毛重（{actual:g} kg）小于箱内货物净重（{cargo:.1f} kg），已忽略该数值，箱重改用工程结构估算；请核实实称毛重是否录入有误。'
        actual=None
    if actual is not None and actual>=cargo-1e-6 and actual>0:
        gross=actual
        source='实际称重（已确认）' if int(crate.get('gross_weight_confirmed') or 0) else '导入/历史毛重（待确认）'
        effective_tare=max(0.0,actual-cargo)
    else:
        gross=cargo+tare; source='工程结构估算'; effective_tare=tare
    return {'cargo_weight':round(cargo,1),'estimated_tare':round(tare,1),'effective_tare':round(effective_tare,1),
            'estimated_gross':round(cargo+tare,1),'gross_weight':round(gross,1),'gross_source':source,
            'actual_gross':actual,'actual_gross_warning':actual_warning,'breakdown':br}

def _dim_overrides_load():
    rows=_json_load(DIM_OVERRIDE_PATH,[])
    return rows if isinstance(rows,list) else []

def _dim_overrides_save(rows): _json_save(DIM_OVERRIDE_PATH,rows)

def _norm_material(v):
    s=str(v or '').upper().replace(' ','')
    for k in ('5083','Q235B','Q235A','20#','304L','304','316L','316','321','S30408','S31603'):
        if k in s: return k
    return s or '*'

def _norm_category(v):
    s=str(v or '')
    for k in ('弯头','三通','异径','缩接','管帽','管道'):
        if k in s: return '异径' if k=='缩接' else k
    return s.strip()

def _norm_dn_spec(v):
    vals=[int(x) for x in re.findall(r'DN\s*(\d+)',str(v or ''),re.I)]
    if not vals: vals=[int(x) for x in re.findall(r'(?<!\d)(\d{2,4})(?!\d)',str(v or ''))[:3]]
    return 'x'.join(str(x) for x in vals)

def _dimension_override_lookup(category,dn_text,material,name=''):
    cat=_norm_category(category or name); spec=_norm_dn_spec(dn_text or name); mat=_norm_material(material or name)
    if not cat or not spec: return None
    matches=[]
    for r in _dim_overrides_load():
        rcat=_norm_category(r.get('category')); rspec=_norm_dn_spec(r.get('dn_spec')); rmat=_norm_material(r.get('material'))
        if rcat!=cat or rspec!=spec: continue
        if rmat not in (mat,'*'): continue
        priority=0 if rmat==mat else 1
        matches.append((priority,r))
    if not matches: return None
    r=sorted(matches,key=lambda x:x[0])[0][1]
    try: L=float(r['length_mm']);W=float(r['width_mm']);H=float(r['height_mm'])
    except Exception: return None
    source=str(r.get('source') or '项目/供应商尺寸库')
    verified=str(r.get('verified') or '').strip().lower() in ('1','true','yes','y','已确认','已批准')
    evidence_id=r.get('evidence_id')
    scope_key=f'{_norm_material(r.get("material"))}|{cat}|{spec}'
    verified=bool(verified and evidence_id and _evidence_matches(evidence_id,scope_type='type',scope_key=scope_key,evidence_types=('approved_dimension',),entity_types=('dimension_override',),entity_ids=(scope_key,),require_verified=True))
    if verified:
        evrow=_evidence_summary(evidence_id)
        verified=evidence_claim_matches_current(evrow,{'length_mm':L,'width_mm':W,'height_mm':H},'item_size')[0]
    grade='A' if verified else 'B'; sub='A3' if verified else 'B2'
    return {'length_mm':L,'width_mm':W,'height_mm':H,'size_grade':grade,'size_subgrade':sub,'size_source':source,
            'size_note':f'尺寸来自可编辑项目尺寸库：{source}；条目 {cat} DN{spec} / {mat}。'+(' 已绑定类型/项目凭证并完成数值核对。' if verified else ' 未绑定有效的人工对照凭证，仅作为B2项目类型数据。'),
            'evidence_id':evidence_id,'evidence_scope':'type' if verified else '','evidence_scope_key':scope_key if verified else ''}

# Common engineering-material-list header aliases.
ALIASES = {
    "name": ["物料名称","材料名称","设备名称","货物名称","名称","品名","物品名称","物料","材料"],
    "qty": ["数量","件数","台数","套数","qty","quantity"],
    "length_mm": ["长(mm)","长度(mm)","长mm","长度mm","长","长度","l(mm)","length(mm)"],
    "width_mm": ["宽(mm)","宽度(mm)","宽mm","宽度mm","宽","宽度","w(mm)","width(mm)"],
    "height_mm": ["高(mm)","高度(mm)","高mm","高度mm","高","高度","h(mm)","height(mm)"],
    "size": ["外形尺寸","外形尺寸(mm)","尺寸","尺寸(mm)","包装尺寸","包装尺寸(mm)","长×宽×高","长x宽x高","l×w×h"],
    "weight_kg": ["单件重量(kg)","单重(kg)","单件重量","单重","重量(kg)","weight(kg)","重量"],
    "total_weight_kg": ["总重量(kg)","总重(kg)","总重量","总重"],
    "package_type": ["包装方式","包装类型","包装形式","包装","package type"],
    "stackable": ["可叠放","允许叠放","是否可叠放"],
    "rotatable": ["可旋转","允许旋转","是否可旋转"],
}

def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        con = sqlite3.connect(str(DB_PATH), timeout=15.0)
    except sqlite3.OperationalError as e:
        raise RuntimeError(
            f"无法打开本地数据库：{DB_PATH}。"
            f"请确认程序目录具有写入权限。原始错误：{e}"
        ) from e
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA busy_timeout=15000')
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA synchronous=NORMAL')
    except Exception as e:
        logger.warning('SQLite PRAGMA configuration failed: %s', e)
    return con

def init_db():
    con = db()
    cur = con.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS items(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        qty INTEGER NOT NULL DEFAULT 1,
        length_mm REAL NOT NULL,
        width_mm REAL NOT NULL,
        height_mm REAL NOT NULL,
        weight_kg REAL NOT NULL,
        weight_grade TEXT NOT NULL DEFAULT 'C',
        weight_source TEXT NOT NULL DEFAULT '未标注',
        weight_note TEXT NOT NULL DEFAULT '',
        size_grade TEXT NOT NULL DEFAULT 'C',
        size_source TEXT NOT NULL DEFAULT '未标注',
        size_note TEXT NOT NULL DEFAULT '',
        size_evidence_id INTEGER,
        weight_evidence_id INTEGER,
        size_subgrade TEXT NOT NULL DEFAULT '',
        weight_subgrade TEXT NOT NULL DEFAULT '',
        size_evidence_scope TEXT NOT NULL DEFAULT '',
        size_evidence_scope_key TEXT NOT NULL DEFAULT '',
        weight_evidence_scope TEXT NOT NULL DEFAULT '',
        weight_evidence_scope_key TEXT NOT NULL DEFAULT '',
        package_type TEXT NOT NULL DEFAULT '木箱',
        stackable INTEGER NOT NULL DEFAULT 1,
        rotatable INTEGER NOT NULL DEFAULT 1,
        cg_x_mm REAL, cg_y_mm REAL, cg_z_mm REAL,
        cg_source TEXT NOT NULL DEFAULT '几何中心假设',
        cg_source_type TEXT NOT NULL DEFAULT 'UNCONFIRMED',
        cg_confirmed INTEGER NOT NULL DEFAULT 0,
        cg_evidence_id INTEGER,
        crate_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS crates(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        length_mm REAL NOT NULL,
        width_mm REAL NOT NULL,
        height_mm REAL NOT NULL,
        tare_weight_kg REAL NOT NULL DEFAULT 120,
        actual_gross_weight_kg REAL,
        gross_weight_confirmed INTEGER NOT NULL DEFAULT 0,
        tare_weight_source TEXT NOT NULL DEFAULT '工程结构估算',
        tare_structure_class TEXT NOT NULL DEFAULT '',
        tare_breakdown_json TEXT NOT NULL DEFAULT '',
        size_confirmed INTEGER NOT NULL DEFAULT 0,
        size_source TEXT NOT NULL DEFAULT '规划箱型',
        size_confirmed_at TEXT,
        size_evidence_id INTEGER,
        gross_weight_evidence_id INTEGER,
        max_top_load_kg REAL,
        top_load_confirmed INTEGER NOT NULL DEFAULT 0,
        top_load_source_type TEXT NOT NULL DEFAULT 'UNCONFIRMED',
        top_load_source_note TEXT NOT NULL DEFAULT '',
        lashing_required INTEGER NOT NULL DEFAULT 0,
        lashing_points INTEGER NOT NULL DEFAULT 0,
        lashing_confirmed INTEGER NOT NULL DEFAULT 0,
        handling_note TEXT NOT NULL DEFAULT '',
        contact_area_mode TEXT NOT NULL DEFAULT 'UNSPECIFIED',
        skid_count INTEGER NOT NULL DEFAULT 0,
        skid_width_mm REAL,
        skid_contact_length_mm REAL,
        custom_contact_area_m2 REAL,
        contact_area_source_type TEXT NOT NULL DEFAULT 'UNCONFIRMED',
        contact_area_source_note TEXT NOT NULL DEFAULT '',
        top_load_evidence_id INTEGER,
        contact_area_evidence_id INTEGER,
        lashing_evidence_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS crate_placements(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        crate_id INTEGER NOT NULL,
        seq INTEGER NOT NULL,
        item_name TEXT NOT NULL,
        x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL,
        l REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
        actual_l REAL, actual_w REAL, actual_h REAL,
        weight_kg REAL NOT NULL,
        weight_grade TEXT NOT NULL DEFAULT 'C',
        weight_source TEXT NOT NULL DEFAULT '未标注',
        cg_local_x REAL, cg_local_y REAL, cg_local_z REAL, cg_confirmed INTEGER NOT NULL DEFAULT 0,
        size_class TEXT, material_family TEXT, fitting_category TEXT
    );
    CREATE TABLE IF NOT EXISTS crate_audits(
        crate_id INTEGER PRIMARY KEY,
        padding_mm REAL, max_l REAL, max_w REAL, max_h REAL, max_gross REAL, fill_limit REAL,
        actual_util REAL, planning_util REAL, shape_util REAL, cargo_weight REAL, gross_weight REAL, item_count INTEGER,
        max_item_name TEXT, max_item_dims TEXT,
        cg_x_pct REAL, cg_y_pct REAL, cg_z_pct REAL, offset_x_pct REAL, offset_y_pct REAL,
        stability_x_ratio REAL, stability_y_ratio REAL,
        collision_ok INTEGER, boundary_ok INTEGER, support_ok INTEGER, gross_ok INTEGER, geometry_ok INTEGER,
        clear_l REAL, clear_w REAL, clear_h REAL, structure_class TEXT,
        next_candidate TEXT, can_continue INTEGER, stop_reason TEXT, audit_status TEXT, conclusion TEXT
    );
    CREATE TABLE IF NOT EXISTS pipe_sources(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL, category TEXT NOT NULL, dn_text TEXT, description TEXT, material TEXT,
        total_length_m REAL NOT NULL, od_mm REAL NOT NULL, thickness_mm REAL NOT NULL,
        density_kg_m3 REAL NOT NULL, material_family TEXT NOT NULL DEFAULT 'CS'
    );
    CREATE TABLE IF NOT EXISTS container_manual_layouts(
        scheme_code TEXT NOT NULL,
        container_index INTEGER NOT NULL,
        base_signature TEXT NOT NULL,
        container_code TEXT NOT NULL,
        positions_json TEXT NOT NULL,
        audit_json TEXT NOT NULL,
        saved_at TEXT NOT NULL,
        PRIMARY KEY(scheme_code,container_index)
    );
    CREATE TABLE IF NOT EXISTS container_layout_drafts(
        scheme_code TEXT NOT NULL,
        container_index INTEGER NOT NULL,
        dirty INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(scheme_code,container_index)
    );
    CREATE TABLE IF NOT EXISTS release_audit_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        export_type TEXT NOT NULL,
        file_name TEXT NOT NULL,
        state_fingerprint TEXT NOT NULL,
        project_name TEXT NOT NULL DEFAULT '',
        approver TEXT NOT NULL DEFAULT '',
        file_sha256 TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '',
        prev_hash TEXT NOT NULL DEFAULT '',
        chain_hash TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS credibility_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        entity_type TEXT NOT NULL DEFAULT '',
        entity_id TEXT NOT NULL DEFAULT '',
        entity_name TEXT NOT NULL DEFAULT '',
        project_name TEXT NOT NULL DEFAULT '',
        approver TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        details_json TEXT NOT NULL DEFAULT '',
        prev_hash TEXT NOT NULL DEFAULT '',
        chain_hash TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS evidence_files(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_type TEXT NOT NULL DEFAULT '',
        entity_id TEXT NOT NULL DEFAULT '',
        evidence_type TEXT NOT NULL DEFAULT '',
        file_name TEXT NOT NULL,
        stored_name TEXT NOT NULL,
        file_sha256 TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL DEFAULT '',
        prev_hash TEXT NOT NULL DEFAULT '',
        chain_hash TEXT NOT NULL DEFAULT '',
        format_kind TEXT NOT NULL DEFAULT '',
        scope_type TEXT NOT NULL DEFAULT 'item',
        scope_key TEXT NOT NULL DEFAULT '',
        claimed_values_json TEXT NOT NULL DEFAULT '{}',
        verification_json TEXT NOT NULL DEFAULT '{}',
        chain_version INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS benchmark_records(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_name TEXT NOT NULL,
        actual_crates INTEGER NOT NULL,
        actual_containers INTEGER NOT NULL,
        actual_cbm REAL NOT NULL DEFAULT 0,
        actual_tare_kg REAL NOT NULL DEFAULT 0,
        planner_crates INTEGER NOT NULL,
        planner_containers INTEGER NOT NULL,
        planner_cbm REAL NOT NULL DEFAULT 0,
        planner_tare_kg REAL NOT NULL DEFAULT 0,
        evidence_id INTEGER,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    """)
    # Schema migration for older local databases.
    item_cols={r[1] for r in cur.execute("PRAGMA table_info(items)").fetchall()}
    for col,ddl in (("weight_grade","TEXT NOT NULL DEFAULT 'C'"),("weight_source","TEXT NOT NULL DEFAULT '未标注'"),("weight_note","TEXT NOT NULL DEFAULT ''"),("size_grade","TEXT NOT NULL DEFAULT 'C'"),("size_source","TEXT NOT NULL DEFAULT '未标注'"),("size_note","TEXT NOT NULL DEFAULT ''"),("size_evidence_id","INTEGER"),("weight_evidence_id","INTEGER"),("size_subgrade","TEXT NOT NULL DEFAULT ''"),("weight_subgrade","TEXT NOT NULL DEFAULT ''"),("size_evidence_scope","TEXT NOT NULL DEFAULT ''"),("size_evidence_scope_key","TEXT NOT NULL DEFAULT ''"),("weight_evidence_scope","TEXT NOT NULL DEFAULT ''"),("weight_evidence_scope_key","TEXT NOT NULL DEFAULT ''"),("cg_x_mm","REAL"),("cg_y_mm","REAL"),("cg_z_mm","REAL"),("cg_source","TEXT NOT NULL DEFAULT '几何中心假设'"),("cg_source_type","TEXT NOT NULL DEFAULT 'UNCONFIRMED'"),("cg_confirmed","INTEGER NOT NULL DEFAULT 0"),("cg_evidence_id","INTEGER")):
        if col not in item_cols:
            cur.execute(f"ALTER TABLE items ADD COLUMN {col} {ddl}")
    crate_cols={r[1] for r in cur.execute("PRAGMA table_info(crates)").fetchall()}
    for col,ddl in (("actual_gross_weight_kg","REAL"),("gross_weight_confirmed","INTEGER NOT NULL DEFAULT 0"),("tare_weight_source","TEXT NOT NULL DEFAULT '工程结构估算'"),("tare_structure_class","TEXT NOT NULL DEFAULT ''"),("tare_breakdown_json","TEXT NOT NULL DEFAULT ''"),("size_confirmed","INTEGER NOT NULL DEFAULT 0"),("size_source","TEXT NOT NULL DEFAULT '规划箱型'"),("size_confirmed_at","TEXT"),("size_evidence_id","INTEGER"),("gross_weight_evidence_id","INTEGER"),("max_top_load_kg","REAL"),("top_load_confirmed","INTEGER NOT NULL DEFAULT 0"),("top_load_source_type","TEXT NOT NULL DEFAULT 'UNCONFIRMED'"),("top_load_source_note","TEXT NOT NULL DEFAULT ''"),("lashing_required","INTEGER NOT NULL DEFAULT 0"),("lashing_points","INTEGER NOT NULL DEFAULT 0"),("lashing_confirmed","INTEGER NOT NULL DEFAULT 0"),("handling_note","TEXT NOT NULL DEFAULT ''"),("contact_area_mode","TEXT NOT NULL DEFAULT 'UNSPECIFIED'"),("skid_count","INTEGER NOT NULL DEFAULT 0"),("skid_width_mm","REAL"),("skid_contact_length_mm","REAL"),("custom_contact_area_m2","REAL"),("contact_area_source_type","TEXT NOT NULL DEFAULT 'UNCONFIRMED'"),("contact_area_source_note","TEXT NOT NULL DEFAULT ''"),("top_load_evidence_id","INTEGER"),("contact_area_evidence_id","INTEGER"),("lashing_evidence_id","INTEGER")):
        if col not in crate_cols:
            cur.execute(f"ALTER TABLE crates ADD COLUMN {col} {ddl}")
    if 'package_origin' not in crate_cols:
        cur.execute("ALTER TABLE crates ADD COLUMN package_origin TEXT NOT NULL DEFAULT 'CONTENTS'")
        cur.execute("UPDATE crates SET package_origin='PREPACKED' WHERE (tare_weight_source='原始箱单毛重' OR size_source='上传箱单外形（未确认）') AND (SELECT count(*) FROM items WHERE crate_id=crates.id)=1 AND EXISTS(SELECT 1 FROM items WHERE crate_id=crates.id AND qty=1 AND length_mm=crates.length_mm AND width_mm=crates.width_mm AND height_mm=crates.height_mm)")
    if 'is_package_placeholder' not in item_cols:
        cur.execute("ALTER TABLE items ADD COLUMN is_package_placeholder INTEGER NOT NULL DEFAULT 0")
        cur.execute("UPDATE items SET is_package_placeholder=1 WHERE crate_id IN (SELECT id FROM crates WHERE package_origin='PREPACKED')")
    # Invalid legacy confirmation flags must not survive stricter finite-domain checks.
    cur.execute("UPDATE items SET cg_confirmed=0,cg_evidence_id=NULL WHERE cg_confirmed=1 AND (cg_x_mm IS NULL OR cg_y_mm IS NULL OR cg_z_mm IS NULL OR cg_x_mm<0 OR cg_y_mm<0 OR cg_z_mm<0 OR cg_x_mm>length_mm OR cg_y_mm>width_mm OR cg_z_mm>height_mm)")
    cur.execute("UPDATE crates SET top_load_confirmed=0,top_load_evidence_id=NULL WHERE top_load_confirmed=1 AND (max_top_load_kg IS NULL OR max_top_load_kg<0 OR max_top_load_kg>1e308)")
    audit_cols={r[1] for r in cur.execute("PRAGMA table_info(crate_audits)").fetchall()}
    audit_migrations=(
        ("shape_util","REAL"),("support_ok","INTEGER"),("geometry_ok","INTEGER"),
        ("clear_l","REAL"),("clear_w","REAL"),("clear_h","REAL"),("structure_class","TEXT"),
        ("stability_x_ratio","REAL"),("stability_y_ratio","REAL")
    )
    for col,ddl in audit_migrations:
        if col not in audit_cols:
            cur.execute(f"ALTER TABLE crate_audits ADD COLUMN {col} {ddl}")
    for table in ("credibility_events","release_audit_log"):
        cols={r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}
        for col in ("prev_hash","chain_hash"):
            if col not in cols:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    evidence_cols={r[1] for r in cur.execute("PRAGMA table_info(evidence_files)").fetchall()}
    for col,ddl in (("format_kind","TEXT NOT NULL DEFAULT ''"),("scope_type","TEXT NOT NULL DEFAULT 'item'"),("scope_key","TEXT NOT NULL DEFAULT ''"),("claimed_values_json","TEXT NOT NULL DEFAULT '{}'"),("verification_json","TEXT NOT NULL DEFAULT '{}'"),("chain_version","INTEGER NOT NULL DEFAULT 1")):
        if col not in evidence_cols:
            cur.execute(f"ALTER TABLE evidence_files ADD COLUMN {col} {ddl}")
    plc_cols={r[1] for r in cur.execute("PRAGMA table_info(crate_placements)").fetchall()}
    for col,ddl in (("weight_grade","TEXT NOT NULL DEFAULT 'C'"),("weight_source","TEXT NOT NULL DEFAULT '未标注'"),("cg_local_x","REAL"),("cg_local_y","REAL"),("cg_local_z","REAL"),("cg_confirmed","INTEGER NOT NULL DEFAULT 0")):
        if col not in plc_cols:
            cur.execute(f"ALTER TABLE crate_placements ADD COLUMN {col} {ddl}")
    # V3.3.1: old shipping-list crates sometimes used the raw numeric source box number
    # directly as the crate name (e.g. "10"). Normalize it without losing traceability.
    numeric_rows=cur.execute("SELECT id,name FROM crates").fetchall()
    for rr in numeric_rows:
        raw=str(rr["name"] if isinstance(rr,sqlite3.Row) else rr[1]).strip()
        if re.fullmatch(r"\d+", raw):
            cur.execute("UPDATE crates SET name=? WHERE id=?", (f"BX-SRC-{int(raw):03d}", rr["id"] if isinstance(rr,sqlite3.Row) else rr[0]))
    cur.execute("""CREATE TABLE IF NOT EXISTS item_evidence_links(
        item_id INTEGER NOT NULL, evidence_id INTEGER NOT NULL, source_item_id INTEGER NOT NULL,
        PRIMARY KEY(item_id,evidence_id))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS asu_records(
        entity_key TEXT PRIMARY KEY,record_json TEXT NOT NULL,updated_at TEXT NOT NULL)""")
    con.commit()
    con.close()

init_db()
EVIDENCE_STORE = EvidenceStore(db, DATA_DIR, _release_meta_load)

def _backfill_legacy_hash_chains():
    """One-time migration: make pre-V5.1 local audit rows chain-verifiable without claiming external notarization."""
    con=db()
    try:
        for table in ('credibility_events','release_audit_log'):
            rows=con.execute(f'SELECT * FROM {table} ORDER BY id').fetchall(); prev=''
            for r in rows:
                if table=='credibility_events':
                    try: details=json.loads(r['details_json'] or '{}')
                    except Exception: details={}
                    payload={'event_type':r['event_type'],'entity_type':r['entity_type'],'entity_id':r['entity_id'],'entity_name':r['entity_name'],'project_name':r['project_name'],'approver':r['approver'],'created_at':r['created_at'],'details':details}
                else:
                    try: details=json.loads(r['details_json'] or '{}')
                    except Exception: details={}
                    payload={'export_type':r['export_type'],'file_name':r['file_name'],'state_fingerprint':r['state_fingerprint'],'project_name':r['project_name'],'approver':r['approver'],'file_sha256':r['file_sha256'],'created_at':r['created_at'],'details':details}
                ch=canonical_hash(prev,payload)
                if not r['chain_hash']:
                    con.execute(f'UPDATE {table} SET prev_hash=?,chain_hash=? WHERE id=?',(prev,ch,r['id']))
                prev=str(r['chain_hash'] or ch)
        con.commit()
    finally:
        con.close()

_backfill_legacy_hash_chains()

def _install_append_only_triggers():
    con=db()
    try:
        for table in ('evidence_files','credibility_events','release_audit_log'):
            con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'append-only audit table'); END;")
            con.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'append-only audit table'); END;")
        con.commit()
    finally: con.close()
_install_append_only_triggers()
try:
    _refresh_containers()
except Exception as e:
    logger.error('container specs invalid at startup; planning will stay blocked until repaired/reset: %s', e)


@app.get("/health")
def health():
    return {"status":"ok", "version":"5.8.28"}

def normalize_header(v):
    s = str(v or "").strip().lower()
    s = s.replace("（", "(").replace("）", ")").replace("㎜", "mm").replace("㎏", "kg")
    s = re.sub(r"[\s\n\r\t_/\\\-—:：,.，。·()（）\[\]【】]+", "", s)
    return s

FIELD_LABELS = {
    "name": "物料/货物名称",
    "qty": "数量",
    "unit": "计量单位",
    "shipment_note": "运输备注",
    "length_mm": "长度(mm)",
    "width_mm": "宽度(mm)",
    "height_mm": "高度(mm)",
    "size": "外形/包装尺寸",
    "weight_kg": "单件重量(kg)",
    "total_weight_kg": "总重量(kg)",
    "package_type": "包装方式",
    "stackable": "可叠放",
    "rotatable": "可旋转",
    "box_no": "箱号/装箱单号",
    "material": "材质/材料牌号",
    "cg_x_mm": "重心X(mm)", "cg_y_mm": "重心Y(mm)", "cg_z_mm": "重心Z(mm)",
}

FIELD_PATTERNS = {
    "unit": ["计量单位","单位","unidad"],
    "shipment_note": ["备注","observaciones"],
    "name": ["物料名称","材料名称","设备名称","货物名称","产品名称","品名","物品名称","货品名称","名称","物料","材料","设备","货物","产品"],
    "qty": ["数量","件数","台数","套数","箱数","qty","quantity","pcs","pc"],
    "length_mm": ["长度mm","长mm","长度","包装长","外形长","lengthmm","length","lmm"],
    "width_mm": ["宽度mm","宽mm","宽度","包装宽","外形宽","widthmm","width","wmm"],
    "height_mm": ["高度mm","高mm","高度","包装高","外形高","heightmm","height","hmm"],
    "size": ["外形尺寸","包装尺寸","规格尺寸","外形规格","包装规格","长宽高","尺寸mm","尺寸","规格"],
    "weight_kg": ["单件毛重","单件重量","每件重量","单重kg","单重","unitweight","weightkg","净重kg","净重"],
    "total_weight_kg": ["总毛重","合计重量","总重量","总重kg","总重","毛重kg","毛重","grossweight","grossweigt","totalweight"],
    "package_type": ["包装方式","包装类型","包装形式","包装类别","包装"],
    "stackable": ["可叠放","允许叠放","是否可叠放","叠放"],
    "rotatable": ["可旋转","允许旋转","是否可旋转","旋转"],
    "box_no": ["箱号","boxno","装箱单号","packinglistno"],
    "material": ["材质","材料牌号","牌号","material","materialgrade","grade"],
    "cg_x_mm": ["重心x","cgx","centerofgravityx","重心xmm"],
    "cg_y_mm": ["重心y","cgy","centerofgravityy","重心ymm"],
    "cg_z_mm": ["重心z","cgz","centerofgravityz","重心zmm"],
}

def header_match_score(text, field):
    h = normalize_header(text)
    if not h:
        return 0
    if field == 'name' and any(v in h for v in ('项目名称','厂家','供应商','制造商','制造厂')):return 0
    combined_size=bool(re.search(r'长[×x*]?宽[×x*]?高',str(text or '').lower()))
    if combined_size and field in ('length_mm','width_mm','height_mm'):return 0
    if combined_size and field=='size':return 120
    primary=normalize_header(str(text or '').splitlines()[0])
    if primary and any(primary==normalize_header(p) for p in FIELD_PATTERNS[field]):return 100
    # Two-level engineering headers often become strings such as
    # “包装尺寸 长(mm)” after merged-cell expansion. Recognize the sub-column directly.
    if field == "length_mm" and (h.endswith("长") or "尺寸长" in h or "规格长" in h or "长度" in h):
        return 95
    if field == "width_mm" and (h.endswith("宽") or "尺寸宽" in h or "规格宽" in h or "宽度" in h):
        return 95
    if field == "height_mm" and (h.endswith("高") or "尺寸高" in h or "规格高" in h or "高度" in h):
        return 95
    best = 0
    for raw in FIELD_PATTERNS[field]:
        p = normalize_header(raw)
        if h == p:
            best = max(best, 100)
        elif len(p) >= 2 and p in h:
            best = max(best, 80 if len(p) >= 3 else 60)
        elif len(h) >= 3 and h in p:
            best = max(best, 55)
    # Avoid generic "规格" hijacking explicit L/W/H columns.
    if field == "size" and h in {"长","宽","高","长度","宽度","高度"}:
        return 0
    return best

def identify_columns(headers):
    candidates = []
    for field in FIELD_LABELS:
        for idx, h in enumerate(headers):
            score = header_match_score(h, field)
            if score:
                candidates.append((score, field, idx))
    candidates.sort(reverse=True)
    result = {}
    used_cols = set()
    for score, field, idx in candidates:
        if field not in result and idx not in used_cols:
            result[field] = idx
            used_cols.add(idx)
    return result

def clean_number(v):
    if v is None or v == "": return None
    if isinstance(v, (int, float)): return float(v)
    s = str(v).strip().replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None

def parse_cylinder_size(v):
    """Diameter x axial length, imported lying horizontally; conservative envelope."""
    if v is None:return None
    s=str(v).lower().replace('×','x').replace('*','x').replace('＊','x')
    if any(word in s for word in ('壁厚','wall thickness','管道','钢管','pipe')):return None
    n=r'(\d+(?:\.\d+)?)';u=r'(mm|cm|m|毫米|厘米|米)?'
    match=re.search(r'[φΦϕ⌀øØ]\s*'+n+r'\s*'+u+r'\s*x\s*'+n+r'\s*'+u,s)
    if not match or re.match(r'\s*x\s*\d',s[match.end():]):return None
    d,du,a,au=match.groups();unit=au or du or 'mm'
    scale={'mm':1,'毫米':1,'cm':10,'厘米':10,'m':1000,'米':1000}
    diameter=float(d)*scale[du or unit];axis=float(a)*scale[au or unit]
    if diameter<=0 or axis<=0:return None
    return (axis,diameter,diameter)

def parse_size(v):
    if v is None:return None
    cylinder=parse_cylinder_size(v)
    if cylinder:return cylinder
    s=str(v).lower().replace('×','x').replace('*','x').replace('＊','x')
    s=re.sub(r'x\s*[,，]\s*(?=\d)', 'x', s)
    # A contiguous dimensional triplet is required: trailing weight is never a dimension.
    match=re.search(r'(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*(mm|cm|m|毫米|厘米|米)?',s)
    if not match:return None
    values=match.groups();unit=values[3]
    if unit is None:
        unit='cm' if ('cm' in s or '厘米' in s) else 'mm' if ('mm' in s or '毫米' in s) else 'm' if re.search(r'(?:^|[^a-z])m(?:$|[^a-z])',s) or '米' in s else 'mm'
    scale={'mm':1,'毫米':1,'cm':10,'厘米':10,'m':1000,'米':1000}[unit]
    return tuple(float(x)*scale for x in values[:3])

def truthy(v, default=1):
    if v is None or str(v).strip() == "": return default
    s = str(v).strip().lower()
    if s in {"否","不允许","不可","no","n","0","false","×","✕","不能"}: return 0
    return 1

def normalize_package(v):
    s = str(v or "").strip()
    if not s: return "木箱"
    if "裸" in s or s.lower() in {"bare", "unpacked"}: return "裸装"
    if "木箱" in s: return "木箱"
    # 彩条布、纸箱、编织袋、吨包等已经是运输包装单元，保留原包装名称，
    # 后续直接参与装柜，不再强制二次装入木箱。
    return s

MAX_ZIP_MEMBERS=5000
MAX_ZIP_TOTAL_UNCOMPRESSED=100*1024*1024
MAX_ZIP_MEMBER_UNCOMPRESSED=30*1024*1024
MAX_ZIP_RATIO=250.0

def _validate_zip_archive(content,label="压缩文件"):
    return _stream_validate_zip_archive(content,label)

def _xlsx_col_index(cell_ref):
    m=re.match(r"([A-Z]+)", str(cell_ref or "").upper())
    if not m: return 0
    n=0
    for ch in m.group(1): n=n*26+(ord(ch)-64)
    return n-1

def _xlsx_read_tables(content):
    """Pure-stdlib XLSX reader.
    Avoids openpyxl compatibility differences on different Mac/Python versions.
    Only reads displayed cell values needed by the packing-list importer.
    """
    NS_MAIN="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    NS_REL="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    NS_PKG="http://schemas.openxmlformats.org/package/2006/relationships"
    ns={"m":NS_MAIN,"r":NS_REL,"p":NS_PKG}
    _validate_zip_archive(content,"Excel工作簿")
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names=set(z.namelist())
        if "xl/workbook.xml" not in names:
            raise ValueError("不是有效的 Excel .xlsx 文件。")
        shared=[]
        if "xl/sharedStrings.xml" in names:
            root=ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si",ns):
                shared.append("".join((t.text or "") for t in si.iter(f"{{{NS_MAIN}}}t")))
        wbroot=ET.fromstring(z.read("xl/workbook.xml"))
        relroot=ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        relmap={x.attrib.get("Id"):x.attrib.get("Target") for x in relroot}
        sheet_defs=[]
        sheets_el=wbroot.find("m:sheets",ns)
        if sheets_el is None: return []
        for sh in list(sheets_el):
            title=sh.attrib.get("name","Sheet")
            rid=sh.attrib.get(f"{{{NS_REL}}}id")
            target=relmap.get(rid,"")
            if target.startswith("/"): path=target.lstrip("/")
            elif target.startswith("xl/"): path=target
            else: path="xl/"+target
            # normalize xl/worksheets/../ paths if present
            parts=[]
            for part in path.split("/"):
                if part=="..":
                    if parts: parts.pop()
                elif part not in ("", "."): parts.append(part)
            sheet_defs.append((title,"/".join(parts)))
        tables=[]
        for title,path in sheet_defs:
            if path not in names: continue
            root=ET.fromstring(z.read(path))
            formula_cells=0
            row_map={}
            max_row=0
            sheet_data=root.find("m:sheetData",ns)
            if sheet_data is None: continue
            for row_el in list(sheet_data):
                rno=int(row_el.attrib.get("r",max_row+1)); max_row=max(max_row,rno)
                vals={}
                for c in list(row_el):
                    if not c.tag.endswith("}c"): continue
                    ref=c.attrib.get("r",""); idx=_xlsx_col_index(ref); typ=c.attrib.get("t")
                    val=None
                    if typ=="inlineStr":
                        val="".join((t.text or "") for t in c.iter(f"{{{NS_MAIN}}}t"))
                    else:
                        formula=c.find("m:f",ns)
                        v=c.find("m:v",ns); raw=v.text if v is not None else None
                        if formula is not None:
                            formula_cells += 1
                            if raw is None or str(raw).strip()=="":
                                raise ValueError(f"Excel工作表‘{title}’单元格 {ref or '?'} 含公式但没有缓存计算值。请先用Excel打开并重新计算/保存，再导入。")
                        if raw is not None:
                            if typ=="s":
                                try: val=shared[int(raw)]
                                except Exception: val=raw
                            elif typ=="b": val=(raw=="1")
                            elif typ in {"str","e"}: val=raw
                            else:
                                try:
                                    f=float(raw); val=int(f) if f.is_integer() else f
                                except Exception: val=raw
                    vals[idx]=val
                if vals:
                    arr=[None]*(max(vals)+1)
                    for i,v in vals.items(): arr[i]=v
                    while arr and arr[-1] in (None,""): arr.pop()
                    row_map[rno]=arr
            rows=[row_map.get(r,[]) for r in range(1,max_row+1)]
            if any(any(v not in (None,"") for v in row) for row in rows):
                tables.append({"sheet":title,"rows":rows,"formula_cells":formula_cells})
        return tables

def read_uploaded_tables(filename, content):
    """Read CSV/XLSX robustly.
    Do not trust the filename suffix alone: browsers or renamed files may provide an unexpected name.
    XLSX is detected from the ZIP/OOXML signature and workbook members.
    """
    fname=str(filename or "").strip()
    suffix=Path(fname).suffix.lower()

    # OOXML XLSX/XLSM are ZIP containers. Sniff the actual bytes first so a missing/wrong
    # filename extension does not incorrectly reject a valid Excel workbook.
    is_zip = len(content) >= 4 and content[:2] == b"PK"
    if is_zip:
        try:
            _validate_zip_archive(content,"上传Excel")
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                names=set(z.namelist())
                if "xl/workbook.xml" in names:
                    tables=_xlsx_read_tables(content)
                    if not tables: raise ValueError("Excel工作簿中没有找到有效数据。")
                    return tables
        except zipfile.BadZipFile:
            pass

    # Legacy binary .xls (OLE compound document). Be explicit rather than returning the
    # misleading '.xlsx/.csv only' message when the user did select an Excel file.
    if content[:8] == bytes.fromhex("D0CF11E0A1B11AE1") or suffix == ".xls":
        raise ValueError("检测到旧版 Excel .xls 文件。当前版本不能直接解析 .xls，请在 Excel 中‘另存为’ .xlsx 后再导入。")

    if suffix in {".xlsx", ".xlsm"}:
        # If the suffix says Excel but the content is not a valid OOXML workbook, provide a
        # useful corruption/format error instead of blaming the extension.
        raise ValueError("文件名是 Excel，但内容不是有效的 .xlsx/.xlsm 工作簿。请用 Excel 打开后重新‘另存为 .xlsx’再导入。")

    if suffix == ".csv" or (not suffix and b"," in content[:4096]):
        for enc in ("utf-8-sig", "gb18030", "gbk"):
            try:
                text=content.decode(enc)
                return [{"sheet":"CSV", "rows":list(csv.reader(io.StringIO(text)))}]
            except UnicodeDecodeError:
                continue
        raise ValueError("CSV编码无法识别，请另存为 UTF-8 CSV 后重试。")

    raise ValueError(f"无法识别上传文件格式：{fname or '未提供文件名'}。支持 .xlsx / .xlsm / .csv；旧版 .xls 请先另存为 .xlsx。")

def _combine_header_rows(a, b):
    n=max(len(a),len(b)); out=[]
    for i in range(n):
        x=str(a[i] if i<len(a) and a[i] not in (None,"") else "").strip()
        y=str(b[i] if i<len(b) and b[i] not in (None,"") else "").strip()
        out.append((x + " " + y).strip() if x and y and x != y else (y or x))
    return out

def _mapping_quality(cols):
    score = 0
    if "name" in cols: score += 8
    if "qty" in cols: score += 2
    if "size" in cols: score += 6
    if all(k in cols for k in ("length_mm","width_mm","height_mm")): score += 8
    else: score += sum(1 for k in ("length_mm","width_mm","height_mm") if k in cols)
    if "weight_kg" in cols: score += 6
    if "total_weight_kg" in cols: score += 5
    score += sum(1 for k in ("package_type","stackable","rotatable") if k in cols)
    return score

def _header_candidates(rows, idx):
    out=[(1, rows[idx], idx+1)]
    # Real shipping lists commonly use exactly two header rows:
    # parent Dimension LxWxH + child Length/Width/Height.
    # Do not merge a third row because project/commodity title rows can then be mistaken for field names.
    if idx+1 < len(rows):
        out.append((2, _combine_header_rows(rows[idx],rows[idx+1]), idx+2))
    return out

def find_best_header(tables):
    best=None
    for ti, table in enumerate(tables):
        rows=table["rows"]
        for idx,row in enumerate(rows[:80]):
            for span,headers,data_start in _header_candidates(rows,idx):
                cols=identify_columns(headers)
                score=_mapping_quality(cols)
                rec={"table_index":ti,"sheet":table["sheet"],"header_idx":idx,"header_span":span,
                     "data_start":data_start,"headers":headers,"columns":cols,"score":score}
                specificity=sum(header_match_score(headers[j],f) for f,j in cols.items())
                rank=(score,specificity,-span)
                if best is None or rank>best['_rank']:rec['_rank']=rank;best=rec
    return best

def find_sheet_headers(tables):
    results=[]
    for ti,table in enumerate(tables):
        b=find_best_header([table])
        if b:
            b["table_index"]=ti; b["sheet"]=table["sheet"]; results.append(b)
    return results

def mapping_is_importable(cols):
    dims = "size" in cols or all(k in cols for k in ("length_mm","width_mm","height_mm"))
    weight = "weight_kg" in cols or "total_weight_kg" in cols
    return "name" in cols and dims and weight

def _qty_value(v):
    if v is None or str(v).strip()=="": return 1,False
    s=str(v).strip().lower().replace("³","3")
    if "若干" in s or "m3" in s or "立方" in s:
        return 1,True
    n=clean_number(v)
    if n is None or n<=0: return 1,True
    return max(1,int(n)),False


# -------- Pipe fitting BOM intelligence (GB/T 12459 style material lists) --------
def _dn_values(text):
    return [int(x) for x in re.findall(r"DN\s*(\d+)", str(text or ""), flags=re.I)] or [int(x) for x in re.findall(r"(?<!\d)(\d{2,4})(?!\d)", str(text or ""))[:3]]

# Common process-pipe OD reference (GB/T standard series). Small DN (<25) follow a
# different OD/DN ratio than mid/large sizes (e.g. DN15 OD is ~21.3mm, not ~16mm), so
# they are listed explicitly rather than relying on the linear fallback below, which
# underestimated them badly (~25%+ error) in earlier versions.
_COMMON_OD={15:21.3,20:26.9,25:32,32:38,40:45,50:57,65:76,80:89,100:108,125:133,150:159,
            200:219,250:273,300:325,350:377,400:426,450:480,500:530,600:630,700:720,800:820}

def _nearest_od(dn, od_map):
    if dn in od_map:
        return float(od_map[dn])
    if dn in _COMMON_OD: return float(_COMMON_OD[dn])
    return float(dn*1.06)

def _od_source(dn, od_map):
    """Where the OD used by _nearest_od came from, for transparency in weight_note."""
    if dn in od_map: return 'workbook'
    if dn in _COMMON_OD: return 'table'
    return 'fallback'

def _parse_thickness(desc):
    """Parse wall thickness with provenance/credibility.

    V5.1 deliberately avoids treating every generic A×B or MODEL-T5 token as wall thickness.
    High-confidence forms: explicit thickness keyword, suffix such as 5mm壁厚, or pipe diameter
    notation carrying Φ/OD/DN. Plain 219×6 is accepted only when pipe context is explicit.
    """
    text=str(desc or '').replace('Ｘ','×').replace('ｘ','x').replace('*','×')
    def pick(pattern):
        vals=[]
        for x in re.findall(pattern,text,flags=re.I):
            if isinstance(x,tuple): x=next((y for y in reversed(x) if y not in (None,'')),'')
            try: v=float(x)
            except Exception: continue
            if 1.0 <= v <= 80.0 and v not in vals: vals.append(v)
        return vals
    # Require a sensible token boundary before T/THK so MODEL-T5 is not interpreted as 5 mm.
    vals=pick(r'(?:壁厚|厚度|厚)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:mm)?')
    if vals: return vals,'explicit_keyword_high'
    vals=pick(r'(?:^|[\s,;，；(/])(?:T|THK)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:mm)?')
    if vals: return vals,'explicit_keyword_high'
    vals=pick(r'(?<!\d)(\d+(?:\.\d+)?)\s*mm\s*(?:壁厚|厚度|厚)')
    if vals: return vals,'explicit_keyword_suffix_high'
    vals=pick(r'(?:Φ|φ|OD)\s*\d+(?:\.\d+)?\s*[×xX]\s*(\d+(?:\.\d+)?)\s*(?:mm)?')
    if vals: return vals,'explicit_pipe_dimension_high'
    vals=pick(r'DN\s*\d+(?:\.\d+)?\s*[×xX]\s*(\d+(?:\.\d+)?)\s*(?:mm)?')
    if vals: return vals,'explicit_dn_dimension_high'
    # Plain A×B is only trustworthy when the same description explicitly says it is a pipe/tube.
    if re.search(r'管道|钢管|铝管|管子|PIPE|TUBE',text,re.I):
        vals=pick(r'(?<!\d)\d+(?:\.\d+)?\s*[×xX]\s*(\d+(?:\.\d+)?)\s*(?:mm)?')
        if vals: return vals,'pipe_context_dimension_medium'
    generic=pick(r'(?<!\d)(\d+(?:\.\d+)?)\s*mm\b')
    if len(generic)==1 and re.search(r'壁厚|厚度|\bTHK\b',text,re.I): return generic,'explicit_single_mm_medium'
    return [5.0],'default'

def _thickness_values(desc):
    return _parse_thickness(desc)[0]

def _thickness_is_default(desc):
    return _parse_thickness(desc)[1]=='default'

def _thickness_confidence(desc):
    source=_parse_thickness(desc)[1]
    if source.endswith('_high'): return 'A'
    if source.endswith('_medium'): return 'B'
    return 'C'


def _steel_density(material):
    """Material density for planning mass estimation, kg/m³.
    Explicit aluminium grades must never fall back to steel density.
    """
    text=str(material or '').upper().replace('－','-')
    aluminium={
        '5083':2660.0,'5052':2680.0,'6061':2700.0,'6063':2700.0,'3003':2730.0,
        '1060':2700.0,'1100':2710.0,'5A02':2680.0,'5A03':2670.0,'5A05':2650.0
    }
    for k,v in aluminium.items():
        if k in text: return v
    if 'AL-UNSPEC' in text or '铝' in text or 'ALUMIN' in text or re.search(r'\bAL\b',text): return 2700.0
    if any(k in text for k in ('316','S316','06CR17NI12MO2')): return 7980.0
    if any(k in text for k in ('304','S304','06CR19NI10','321','S321','不锈钢')): return 7930.0
    return 7850.0

def _pipe_shell_area_mm2(od,t):
    idd=max(0.0,od-2*t)
    return math.pi/4.0*(od*od-idd*idd)

# GB/T 12459-2025 current standard reference dimensions (selected common DN range).
# These are dimensional parameters, not packaging L×W×H. The program converts them to a
# conservative rectangular packing envelope and clearly marks that conversion as B-grade.
_GBT2025_LR_ELBOW={
15:(38,16),20:(38,19),25:(38,22),32:(48,25),40:(57,29),50:(76,35),65:(95,44),80:(114,51),90:(133,57),
100:(152,64),125:(190,79),150:(229,95),200:(305,127),250:(381,159),300:(457,190),350:(533,222),400:(610,254),
450:(686,286),500:(762,318),550:(838,343),600:(914,381)}
_GBT2025_SR90={25:25,32:32,40:38,50:51,65:64,80:76,90:89,100:102,125:127,150:152,200:203,250:254,300:305,350:356,400:406,450:457,500:508,550:559,600:610}
_GBT2025_EQUAL_TEE_C={15:25,20:29,25:38,32:48,40:57,50:64,65:76,80:86,90:95,100:105,125:124,150:143,200:178,250:216,300:254,350:279,400:305,450:343,500:381,550:419,600:432,650:495,700:521,750:559,800:597}

# GB/T 12459-2025 Table 10, common reducer end-to-end H (major DN, mm).
# Coverage is intentionally limited to entries verified in the loaded reference range; missing sizes fall back to C-grade estimation.
_GBT2025_REDUCER_H={20:38,25:51,32:51,40:64,50:76,65:89,80:89,90:102,100:102,125:127,150:140,200:152,250:178,300:203,350:330,400:356,450:381,500:508,550:508}
# GB/T 12459-2025 Table 9 cap height E for normal wall-thickness range, common DN.
_GBT2025_CAP_E={15:25,20:25,25:38,32:38,40:38,50:38,65:38,80:51,90:64,100:64,125:76,150:89,200:102,250:127,300:152,350:165,400:178,450:203,500:229,550:254,600:267}
# Selected reducing-tee rows verified from Table 7: (main DN, branch DN) -> (C,M)
_GBT2025_REDUCING_TEE_CM={
 (400,350):(305,305),(400,300):(305,305),(400,250):(305,295),(400,200):(305,283),(400,150):(305,273),
 (450,400):(343,343),(450,350):(343,330),(450,300):(343,330),(450,250):(343,321),(450,200):(343,308),
 (500,450):(381,381),(500,400):(381,368),(500,350):(381,356),(500,300):(381,356),(500,250):(381,346),(500,200):(381,333),
 (550,500):(419,419),(550,450):(419,406),(550,400):(419,394),(550,350):(419,381),(550,300):(419,381),(550,250):(419,371),
 (600,550):(432,432),(600,500):(432,432),(600,450):(432,419),(600,400):(432,406),(600,350):(432,406),(600,300):(432,397),(600,250):(432,384)
}

def _ceil10(v): return int(math.ceil(float(v)/10.0)*10)

def _is_gbt2025_steel_material(material, desc=''):
    s=f"{material or ''} {desc or ''}".upper()
    if '5083' in s or '5052' in s or '铝' in s: return False
    return any(k in s for k in ('Q235','20#','20号','304','304L','316','316L','06CR','不锈钢','碳钢','CS-','SS-'))

def _gbt2025_standard_envelope(category,dn_text,desc,material,od_map):
    if not _is_gbt2025_steel_material(material,desc): return None
    dns=_dn_values(dn_text)
    if not dns: return None
    cat=str(category or ''); ds=str(desc or '')
    if '弯头' in cat:
        dn=dns[0]; od=_nearest_od(dn,od_map)
        ang=45 if '45' in ds else 90
        if ang==45:
            row=_GBT2025_LR_ELBOW.get(dn)
            if not row: return None
            B=row[1]
            L=W=_ceil10(B+od/2.0); H=_ceil10(od)
            return {'length_mm':L,'width_mm':W,'height_mm':H,'size_grade':'B','size_subgrade':'B3','size_source':'GB/T 12459-2025 表2','size_note':f'长半径45°弯头：采用现行标准中心至端面 B={B} mm、端部外径约 {od:g} mm，换算为装箱保守外包络；不是厂家包装尺寸。'}
        # 90°: explicit S => short radius; otherwise L/default => long radius.
        short=bool(re.search(r'90E\s*\(\s*S\s*\)|短半径|R\s*=\s*1(?:\.0)?D',ds,re.I))
        if short:
            A=_GBT2025_SR90.get(dn)
            if A is None: return None
            source='GB/T 12459-2025 表4'; radius='短半径90°'
        else:
            row=_GBT2025_LR_ELBOW.get(dn)
            if not row: return None
            A=row[0]; source='GB/T 12459-2025 表2'; radius='长半径90°'
        L=W=_ceil10(A+od/2.0); H=_ceil10(od)
        return {'length_mm':L,'width_mm':W,'height_mm':H,'size_grade':'B','size_source':source,'size_note':f'{radius}：采用现行标准中心至端面 A={A} mm、端部外径约 {od:g} mm，换算为装箱保守外包络；不是厂家包装尺寸。'}
    if '三通' in cat:
        dn=dns[0]
        od=_nearest_od(dn,od_map)
        if len(dns)>1 and any(x!=dn for x in dns):
            branch=min(dns[1:])
            cm=_GBT2025_REDUCING_TEE_CM.get((dn,branch))
            if cm is None: return None
            C,M=cm; od1=_nearest_od(branch,od_map)
            L=_ceil10(2*C); W=_ceil10(M+od1/2.0); H=_ceil10(max(od,od1))
            return {'length_mm':L,'width_mm':W,'height_mm':H,'size_grade':'B','size_subgrade':'B3','size_source':'GB/T 12459-2025 表7',
                    'size_note':f'异径三通：采用现行标准 C={C} mm、M={M} mm，主管DN{dn}/支管DN{branch}，换算为装箱保守外包络。'}
        C=_GBT2025_EQUAL_TEE_C.get(dn)
        if C is None: return None
        L=_ceil10(2*C); W=_ceil10(C+od/2.0); H=_ceil10(od)
        return {'length_mm':L,'width_mm':W,'height_mm':H,'size_grade':'B','size_subgrade':'B3','size_source':'GB/T 12459-2025 表6','size_note':f'等径三通：采用现行标准中心至端面 C=M={C} mm、端部外径约 {od:g} mm，换算为装箱保守外包络；不是厂家包装尺寸。'}
    if '异径' in cat or '缩接' in cat:
        if len(dns)<2: return None
        big=max(dns[0],dns[1]); small=min(dns[0],dns[1]); H=_GBT2025_REDUCER_H.get(big)
        if H is None: return None
        od=_nearest_od(big,od_map); od1=_nearest_od(small,od_map)
        return {'length_mm':_ceil10(H),'width_mm':_ceil10(max(od,od1)),'height_mm':_ceil10(max(od,od1)),
                'size_grade':'B','size_subgrade':'B3','size_source':'GB/T 12459-2025 表10','size_note':f'异径管：采用现行标准端面至端面 H={H} mm，DN{big}×DN{small}，外径按端部外径换算装箱外包络。'}
    if '管帽' in cat:
        dn=dns[0]; E=_GBT2025_CAP_E.get(dn)
        if E is None: return None
        od=_nearest_od(dn,od_map)
        return {'length_mm':_ceil10(E),'width_mm':_ceil10(od),'height_mm':_ceil10(od),'size_grade':'B','size_subgrade':'B3','size_source':'GB/T 12459-2025 表9',
                'size_note':f'管帽：采用现行标准常规壁厚高度 E={E} mm、端部外径约 {od:g} mm，换算装箱外包络；厚壁E1情形需另核。'}
    return None

def _estimate_fitting_geometry_and_weight(category,dn_text,desc,material,od_map):
    dns=_dn_values(dn_text)
    if not dns: return None
    ods=[_nearest_od(d,od_map) for d in dns]
    ts=_thickness_values(desc)
    rho=_steel_density(material)
    cat=str(category or '').strip(); descs=str(desc or '')
    custom=_dimension_override_lookup(cat,dn_text,material,descs)
    std=custom or _gbt2025_standard_envelope(cat,dn_text,descs,material,od_map)
    size_grade=std.get('size_grade','B') if std else 'C'
    size_source=std.get('size_source') if std else '项目几何估算'
    size_note=std.get('size_note') if std else '缺少厂家实际尺寸或适用标准逐规格尺寸，按DN、端部外径、曲率/形态生成装箱规划外包络；应在正式包装前以厂家图纸复核。'
    if '弯头' in cat:
        dn=dns[0]; od=ods[0]; t=ts[0]; ang=45 if '45' in descs else 90
        if std:
            L,W,H=std['length_mm'],std['width_mm'],std['height_mm']
            # Use a standard-informed centerline radius for theoretical mass only.
            short=bool(re.search(r'90E\s*\(\s*S\s*\)|短半径|R\s*=\s*1(?:\.0)?D',descs,re.I))
            R=(1.0 if short else 1.5)*dn
        else:
            m=re.search(r'R\s*=\s*(\d+(?:\.\d+)?)\s*D',descs,re.I)
            if m: rm=float(m.group(1))
            elif re.search(r'E\s*\(\s*S\s*\)|短半径',descs,re.I): rm=1.0
            else: rm=1.5
            R=rm*dn
            if ang==90:
                A=R; L=W=_ceil10(A+od/2.0)
            else:
                B=R*math.tan(math.radians(22.5)); L=W=_ceil10(B+od/2.0)
            H=_ceil10(od)
        arc=math.radians(ang)*R
        wt=_pipe_shell_area_mm2(od,t)*arc/1e9*rho*1.12
    elif '三通' in cat:
        dn1=dns[0]; dn2=dns[-1] if len(dns)>1 else dns[0]; od1=ods[0]; od2=ods[-1] if len(ods)>1 else ods[0]
        if std:
            L,W,H=std['length_mm'],std['width_mm'],std['height_mm']
        else:
            # Conservative but physically interpretable fallback: center-to-end about 0.9×OD.
            C=max(0.9*od1,0.85*dn1); M=max(0.9*od2,0.85*dn2)
            L=_ceil10(2*C); W=_ceil10(M+od1/2.0); H=_ceil10(max(od1,od2))
        t1=ts[0]; t2=ts[-1] if len(ts)>1 else t1
        wt=(_pipe_shell_area_mm2(od1,t1)*2.0*od1+_pipe_shell_area_mm2(od2,t2)*1.15*od2)/1e9*rho*1.18
    elif '异径' in cat:
        od1=max(ods); od2=min(ods); t=sum(ts)/len(ts)
        if std:
            L,W,H=std['length_mm'],std['width_mm'],std['height_mm']
        else:
            L=_ceil10(max(1.35*od1,od1+150)); W=H=_ceil10(1.05*od1)
        slant=math.sqrt(float(L)*float(L)+((od1-od2)/2)**2); shell_area=math.pi*((od1+od2)/2)*slant
        wt=shell_area*t/1e9*rho*1.08
    elif '管帽' in cat:
        od=ods[0]; t=ts[0]
        if std:
            L,W,H=std['length_mm'],std['width_mm'],std['height_mm']
        else:
            L=_ceil10(0.75*od); W=H=_ceil10(1.05*od)
        wt=(2*math.pi*(od/2)**2*t)/1e9*rho*1.08
    else: return None
    caveats=[]
    if _thickness_is_default(descs):
        caveats.append(f'物料描述中未识别到壁厚标注，壁厚按默认值 {ts[0]:g} mm 估算，建议核实实际壁厚。')
    od_srcs={d:_od_source(d,od_map) for d in dns}
    fallback_dns=[d for d,src in od_srcs.items() if src=='fallback']
    if fallback_dns:
        caveats.append(f'DN{"/".join(str(d) for d in fallback_dns)} 未在管道明细或常用外径表中找到，外径按 DN×1.06 粗略换算，建议核实实际外径。')
    note='重量仍按壁厚、材质密度和简化壳体几何估算；非厂家实测。'
    if caveats:
        note += ' ' + ' '.join(caveats)
    return {'length_mm':max(10,L),'width_mm':max(10,W),'height_mm':max(10,H),
            'weight_kg':round(max(0.2,wt),1),'weight_grade':'C','weight_source':'简化几何估算',
            'weight_note':note,
            'size_grade':size_grade,'size_source':size_source,'size_note':size_note,'size_evidence_id':(custom or {}).get('evidence_id')}


def _purchase_bom_section_kind(section, name=''):
    text=f"{section or ''} {name or ''}"
    if '管帽' in text: return '管帽'
    if '三通' in text: return '三通'
    if '缩接' in text or '异径' in text: return '异径'
    if '弯头' in text: return '弯头'
    if '仪表管' in text: return ''
    if '管' in text and not any(k in text for k in ('管帽','三通','缩接','异径','弯头')): return '管道'
    return ''

def _dn_text_from_purchase_row(code_text, name_text):
    t=f"{code_text or ''} {name_text or ''}"
    vals=[]
    # DN100X50 / DN100x50 / DN100-50
    m=re.search(r'DN\s*(\d+)(?:\s*[Xx×]\s*(\d+))?(?:\s*[Xx×]\s*(\d+))?',t,re.I)
    if m:
        vals=[int(x) for x in m.groups() if x]
    if not vals:
        vals=[int(x) for x in re.findall(r'DN\s*(\d+)',t,re.I)]
    return ' '.join(f'DN{x}' for x in vals)

def _strict_purchase_number(v):
    if isinstance(v,(int,float)):
        return float(v) if float(v)>0 else None
    text=str(v or '').strip()
    # The '需要采购' column in the structured top section contains plain numeric quantities.
    # Do not extract DN numbers from remarks such as '用于DN25管道焊接用'.
    m=re.fullmatch(r'\(?\s*(\d+(?:\.\d+)?)\s*(?:m|米|个|件)?\s*\)?',text,re.I)
    return float(m.group(1)) if m else None

def insert_5083_pipe_crates(pipe_rows, max_crate_weight=4000, max_crate_width=1600, max_crate_height=1600, padding=100, tare_per_m3=45):
    """Group long 5083 pipes into long wood cases instead of creating one oversize crate per pipe.
    Each pipe_row represents N planning lengths derived from the source purchase metres.
    """
    if not pipe_rows: return 0
    con=db(); cur=con.cursor(); created=0
    for r in pipe_rows:
        qty=max(1,int(r.get('qty',1)))
        piece_l=float(r['length_mm']); unit_w=float(r['weight_kg'])
        # Recover approximate OD from the row name; fall back to the planning envelope.
        mo=re.search(r'[Φφ]\s*(\d+(?:\.\d+)?)',str(r.get('name') or ''))
        od=float(mo.group(1)) if mo else max(20.0,min(float(r['width_mm']),float(r['height_mm']))-120.0)
        pitch=max(od+30.0,80.0)
        usable_w=max(100.0,max_crate_width-2*padding); usable_h=max(100.0,max_crate_height-2*padding)
        across=max(1,int(usable_w//pitch)); high=max(1,int(usable_h//pitch))
        by_cross=max(1,across*high); by_weight=max(1,int(max_crate_weight//max(unit_w,0.1)))
        cap=max(1,min(by_cross,by_weight))
        remaining=qty; seq=0
        while remaining>0:
            seq+=1; cnt=min(cap,remaining)
            # Recheck gross weight with the structural tare model because long, narrow boxes can
            # weigh materially more than the old volume-coefficient estimate.
            while cnt>1:
                cols=min(across,cnt); rows=int(math.ceil(cnt/cols))
                crate_l=_ceil50(piece_l+2*padding)
                crate_w=_ceil50(cols*pitch+2*padding)
                crate_h=_ceil50(rows*pitch+2*padding)
                cargo_w=cnt*unit_w; tare=_estimate_tare(crate_l,crate_w,crate_h,tare_per_m3,cargo_w)
                if cargo_w+tare<=max_crate_weight+1e-6: break
                cnt-=1
            cols=min(across,cnt); rows=int(math.ceil(cnt/cols))
            crate_l=_ceil50(piece_l+2*padding)
            crate_w=_ceil50(cols*pitch+2*padding)
            crate_h=_ceil50(rows*pitch+2*padding)
            cargo_w=cnt*unit_w; tare=_estimate_tare(crate_l,crate_w,crate_h,tare_per_m3,cargo_w)
            remaining-=cnt
            created+=1
            cname=f"BX-5083-{created:03d}"
            cur.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,tare_weight_kg) VALUES(?,?,?,?,?)",
                        (cname,crate_l,crate_w,crate_h,round(tare,1)))
            cid=cur.lastrowid
            item_name=str(r['name']).replace('｜强制木箱','')+f"｜本箱{cnt}根"
            cur.execute("""INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,weight_note,size_grade,size_source,size_note,size_evidence_id,weight_evidence_id,package_type,stackable,rotatable,cg_x_mm,cg_y_mm,cg_z_mm,cg_source,cg_source_type,cg_confirmed,crate_id)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (item_name,cnt,piece_l,max(od+30,80),max(od+30,80),unit_w,r.get('weight_grade','B'),r.get('weight_source','5083管道理论重量'),r.get('weight_note',''),r.get('size_grade','C'),r.get('size_source','规划分根外包络'),r.get('size_note',''),r.get('size_evidence_id'),r.get('weight_evidence_id'),"木箱",1,0,r.get('cg_x_mm'),r.get('cg_y_mm'),r.get('cg_z_mm'),r.get('cg_source','几何中心假设'),r.get('cg_source_type','UNCONFIRMED'),r.get('cg_confirmed',0),cid))
    con.commit(); con.close(); return created

def _purchase_bom_specialized_confidence(tables):
    """Conservative routing gate for the KDN-style `需要采购` BOM.

    A generic equipment list can also contain 名称/材料/需要采购.  Only enter the
    specialized pipe/fitting semantics when most purchasable rows actually look
    like pipe/fitting records (DN/Φ or explicit fitting/pipe names).
    """
    for table in tables:
        rows=table.get('rows',[]); header_idx=None; idx={}
        for i,row in enumerate(rows[:30]):
            vals=[re.sub(r'\s+','',str(v or '')) for v in row]
            if all(k in vals for k in ('需要采购','名称','材料')):
                header_idx=i; idx={v:j for j,v in enumerate(vals) if v in {'需要采购','名称','材料','代号','序号'}}; break
        if header_idx is None: continue
        candidates=recognized=0
        for row in rows[header_idx+1:header_idx+301]:
            def cell(k):
                j=idx.get(k); return str(row[j] if j is not None and j<len(row) else '' or '').strip()
            qty=_strict_purchase_number(cell('需要采购'))
            name=cell('名称'); code=cell('代号')
            if qty is None or qty<=0 or not name: continue
            candidates+=1
            text=f'{code} {name}'
            if re.search(r'\bDN\s*\d+',text,re.I) or re.search(r'[Φφ]\s*\d+',text) or any(k in text for k in ('管道','弯头','三通','异径','缩接','管帽')):
                recognized+=1
        if candidates>=2 and recognized>=2 and recognized/max(candidates,1)>=0.60:
            return True
    return False

def parse_need_purchase_bom(tables):
    """Parse the KDN-style cold-box material list whose quantity source is the `需要采购` column.

    Rules requested by the user:
    - 管道: 需要采购 means metres.
    - 弯头/三通/缩接/管帽: 需要采购 means pieces.
    - material 5083: always wood-crated.
    - Q235B carbon-steel pipe may enter the bare-bundle planner.
    """
    all_results=[]; all_pipe_sources=[]; notes=[]; matched=False
    for table in tables:
        rows=table.get('rows',[])
        header_idx=None; col={}
        for i,row in enumerate(rows[:30]):
            vals=[str(v or '').strip() for v in row]
            nvals=[re.sub(r'\s+','',v) for v in vals]
            if '需要采购' in nvals and '名称' in nvals and '材料' in nvals:
                header_idx=i
                for j,v in enumerate(nvals):
                    if v in {'序号','代号','名称','材料','未放余量','需要采购','备注'}:
                        col[v]=j
                break
        if header_idx is None:
            continue
        matched=True
        # Pre-scan OD references from all pipe-looking rows.
        od_map={}
        for row in rows[header_idx+1:]:
            code=str(row[col.get('代号',5)] if col.get('代号',5)<len(row) else '' or '').strip()
            name=str(row[col.get('名称',6)] if col.get('名称',6)<len(row) else '' or '').strip()
            dnvals=_dn_values(code)
            mo=re.search(r'[Φφ]\s*(\d+(?:\.\d+)?)',name)
            if dnvals and mo: od_map[dnvals[0]]=float(mo.group(1))

        section=''; inherited_material=''
        for ridx,row in enumerate(rows[header_idx+1:], start=header_idx+2):
            def get(label,default=None):
                j=col.get(label)
                return row[j] if j is not None and j<len(row) else default
            sec_raw=str(get('序号') or '').strip()
            # Section headers in this workbook are stored in the 序号 column.
            if sec_raw and ('：' in sec_raw or ':' in sec_raw) and not re.fullmatch(r'\d+',sec_raw):
                section=sec_raw
                if '铝' in section: inherited_material='AL-UNSPEC'
                elif '碳钢' in section: inherited_material='Q235B'
                else: inherited_material=''
                continue

            code=str(get('代号') or '').strip()
            name=str(get('名称') or '').strip()
            mat=str(get('材料') or '').strip() or inherited_material
            purchase=get('需要采购')
            qty_num=_strict_purchase_number(purchase)
            if qty_num is None or qty_num<=0 or not name:
                continue
            kind=_purchase_bom_section_kind(section,name)
            if not kind:
                continue
            margin=get('未放余量')
            remark=str(get('备注') or '').strip()

            if kind=='管道':
                total_m=float(qty_num)  # IMPORTANT: 需要采购 means metres for pipe.
                dns=_dn_values(code)
                mo=re.search(r'[Φφ]\s*(\d+(?:\.\d+)?)',name)
                od=float(mo.group(1)) if mo else (_nearest_od(dns[0],od_map) if dns else 0)
                # pipe name normally contains ΦODxT, capture T from the second number
                mt=re.search(r'[Φφ]\s*\d+(?:\.\d+)?\s*[Xx×]\s*(\d+(?:\.\d+)?)',name)
                t=float(mt.group(1)) if mt else (_thickness_values(name)[0] if name else 5.0)
                if od<=0: continue
                is5083='5083' in mat.upper()
                if is5083:
                    # User hard rule: 5083 MUST be wood-crated. Source quantity is metres;
                    # split only for planning, without inventing extra total length.
                    n=max(1,int(math.ceil(total_m/6.0)))
                    piece_len_m=total_m/n
                    rho=_steel_density('5083')
                    unit_w=_pipe_shell_area_mm2(od,t)*piece_len_m*1000.0/1e9*rho
                    all_results.append({
                        'name':f"{code}｜{name}｜5083｜采购{total_m:g}m｜强制木箱"[:180],
                        'qty':n,
                        'length_mm':round(piece_len_m*1000,1),
                        'width_mm':round(max(od+120,200),1),
                        'height_mm':round(max(od+120,200),1),
                        'weight_kg':round(max(unit_w,0.2),1),
                        'weight_grade':'B','weight_source':'5083管道理论重量+采购米数规划分根',
                        'weight_note':f"原表‘需要采购’={total_m:g}m（管道按米解释）；规划为{n}根、每根约{piece_len_m:.2f}m。5083材质按规则强制木箱。未放余量={margin if margin not in (None,'') else '—'}。",
                        'package_type':'木箱','stackable':1,'rotatable':0,'box_no':''
                    })
                else:
                    upmat=mat.upper(); is_aluminium=('AL-UNSPEC' in upmat or '铝' in mat or any(g in upmat for g in ('5052','6061','6063','3003','1060','1100','5A02','5A03','5A05')))
                    if is_aluminium:
                        # V5.1: aluminium without explicit 5083 must NOT inherit 5083 isolation semantics.
                        # Use a protective wood-crate planning assumption and flag grade/package confirmation.
                        n=max(1,int(math.ceil(total_m/6.0))); piece_len_m=total_m/n; rho=_steel_density(mat)
                        unit_w=_pipe_shell_area_mm2(od,t)*piece_len_m*1000.0/1e9*rho; grade=mat or 'AL-UNSPEC'
                        all_results.append({
                            'name':f"{code}｜{name}｜{grade}｜采购{total_m:g}m｜铝管待确认"[:180],
                            'qty':n,'length_mm':round(piece_len_m*1000,1),'width_mm':round(max(od+120,200),1),'height_mm':round(max(od+120,200),1),
                            'weight_kg':round(max(unit_w,0.2),1),'weight_grade':'C' if 'AL-UNSPEC' in upmat else 'B',
                            'weight_source':'铝管理论重量+采购米数规划分根',
                            'weight_note':f"原表‘需要采购’={total_m:g}m；规划为{n}根。材质={grade}，未套用5083隔离规则；暂按保护性木箱规划，正式包装前确认实际牌号、定尺和包装规则。",
                            'package_type':'木箱','stackable':1,'rotatable':0,'box_no':''
                        })
                    else:
                        fam='CS' if ('Q235' in upmat or '碳钢' in section) else ('SS' if any(k in upmat for k in ('304','316','06CR')) else 'OTHER')
                        all_pipe_sources.append({
                            'code':code or f'PIPE-{ridx}','category':'管道','dn_text':code,'description':name,'material':mat,
                            'total_length_m':total_m,'od_mm':od,'thickness_mm':t,'density_kg_m3':_steel_density(mat),'material_family':fam
                        })
                continue

            # fittings: 需要采购 means pieces.
            qty=max(1,int(round(float(qty_num))))
            dn_text=_dn_text_from_purchase_row(code,name) or code
            geom=_estimate_fitting_geometry_and_weight(kind,dn_text,f"{name} {code}",mat,od_map)
            if not geom:
                continue
            force5083='5083' in mat.upper()
            source='简化几何估算'
            note=(f"原表‘需要采购’={qty}个（{kind}按个数解释）；未放余量={margin if margin not in (None,'') else '—'}。"
                  + ("5083材质按规则强制木箱。" if force5083 else "管件按木箱规划。")
                  + f" 尺寸等级={geom.get('size_grade','C')}级；尺寸来源={geom.get('size_source','项目几何估算')}。"
                  + (f" 备注：{remark}" if remark else ""))
            all_results.append({
                'name':f"{kind}｜{dn_text}｜{name}｜{mat or '材质未填'}｜采购{qty}个"[:180],
                'qty':qty,
                'length_mm':geom['length_mm'],'width_mm':geom['width_mm'],'height_mm':geom['height_mm'],
                'weight_kg':geom['weight_kg'],'weight_grade':'C','weight_source':source,'weight_note':note,
                'package_type':'木箱','stackable':1,'rotatable':1,'box_no':''
            })
    if matched:
        pipe_m=sum(float(p['total_length_m']) for p in all_pipe_sources)
        forced=sum(1 for r in all_results if '5083' in r.get('name',''))
        notes.append("本文件按‘需要采购’列取数量：管道按米数，弯头/三通/缩接/管帽按个数。")
        if forced: notes.append(f"5083相关规划记录 {forced} 条，已强制木箱包装。")
        if pipe_m: notes.append(f"非5083管道源数据合计 {pipe_m:g} m，进入裸装扎捆规划。")
    return all_results,all_pipe_sources,notes,matched

def parse_pipe_fitting_bom(tables):
    """Parse fittings and retain pipe source rows for V3.0 bare-bundle planning."""
    results=[]; pipe_sources=[]; warnings=[]
    for table in tables:
        rows=table.get("rows",[])
        od_map={}
        for row in rows:
            if len(row)<6: continue
            code=str(row[0] or "").strip(); cat=str(row[1] or "").strip(); dn=str(row[2] or "").strip(); desc=str(row[3] or "")
            if re.match(r"^(?:CS|SS)-\d+",code,re.I) and "管道" in cat:
                dns=_dn_values(dn); mo=re.search(r"[Φφ]\s*(\d+(?:\.\d+)?)",desc)
                if dns and mo: od_map[dns[0]]=float(mo.group(1))
        for row in rows:
            if len(row)<6: continue
            code=str(row[0] or "").strip(); cat=str(row[1] or "").strip(); dn=str(row[2] or "").strip()
            desc=str(row[3] or "").strip(); material=str(row[4] or "").strip()
            if not re.match(r"^(?:CS|SS)-\d+",code,re.I): continue
            qty_n=clean_number(row[5] if len(row)>5 else None)
            if "管道" in cat:
                total_m=float(qty_n or 0)
                dns=_dn_values(dn); mo=re.search(r"[Φφ]\s*(\d+(?:\.\d+)?)",desc)
                od=float(mo.group(1)) if mo else (_nearest_od(dns[0],od_map) if dns else 0)
                ts=_thickness_values(desc); t=float(ts[0] if ts else 5.0)
                if "5083" in material.upper() or "5083" in desc.upper():
                    # 5083 aluminium pipe must be protected in a wood crate. Create a planning wood-package
                    # record instead of sending it to the carbon-steel bare-bundle generator. If the source
                    # only provides total metres, use a transparent 6m planning piece length assumption.
                    if total_m>0 and od>0:
                        n=max(1,int(math.ceil(total_m/6.0))); piece_m=total_m/n
                        unit_w=_pipe_kg_per_m(od,t,_steel_density(material))*piece_m
                        results.append({"name":f"{code}｜{cat}｜{dn}｜{desc}｜5083强制木箱"[:180],"qty":n,
                            "length_mm":round(piece_m*1000,1),"width_mm":round(max(od+120,200),1),"height_mm":round(max(od+120,200),1),
                            "weight_kg":round(unit_w,1),"weight_grade":"B","weight_source":"5083管道理论重量+规划分根",
                            "weight_note":"5083材质按强制木箱规则处理；原表只有总米数时暂按约6m规划分根，项目确认前应以实际定尺替换。",
                            "size_grade":"C","size_source":"规划分根外包络","size_note":"原表总米数按约6m规划分根，不是实测/批准发运外形。",
                            "package_type":"木箱","stackable":1,"rotatable":0,"box_no":""})
                    continue
                fam="SS" if code.upper().startswith("SS-") else "CS"
                if total_m>0 and od>0:
                    pipe_sources.append({"code":code,"category":cat,"dn_text":dn,"description":desc,"material":material,
                        "total_length_m":total_m,"od_mm":od,"thickness_mm":t,"density_kg_m3":_steel_density(material),"material_family":fam})
                continue
            if not any(k in cat for k in ("弯头","三通","异径","管帽")): continue
            geom=_estimate_fitting_geometry_and_weight(cat,dn,desc,material,od_map)
            if not geom: continue
            qty=max(1,int(qty_n or 1)); src_total_t=clean_number(row[8] if len(row)>8 else None)
            if src_total_t is not None and src_total_t>0:
                unit_weight=src_total_t*1000.0/qty; grade="B"; source="原始材料清单总重（未绑定秤票/批准凭证）"; note=f"原表总重 {src_total_t:g} t，按数量 {qty} 换算单件重量。"
            else:
                unit_weight=geom["weight_kg"]; grade=geom.get("weight_grade","C"); source=geom.get("weight_source","简化几何估算"); note=geom.get("weight_note","")
            results.append({"name":f"{code}｜{cat}｜{dn}｜{desc}"[:180],"qty":qty,"length_mm":geom["length_mm"],"width_mm":geom["width_mm"],"height_mm":geom["height_mm"],
                "weight_kg":round(unit_weight,1),"weight_grade":grade,"weight_source":source,"weight_note":note,
                "size_grade":geom.get("size_grade","C"),"size_source":geom.get("size_source","项目几何估算"),"size_note":geom.get("size_note",""),"size_evidence_id":geom.get("size_evidence_id"),
                "package_type":"木箱","stackable":1,"rotatable":1,"box_no":""})
    if results: warnings.append("管件重量继续采用A/B/C级追溯。")
    forced5083=sum(1 for r in results if "5083强制木箱" in r.get("name", ""))
    if forced5083: warnings.append(f"检测到 {forced5083} 条5083材质管道规划记录，已强制按木箱包装，不进入裸装扎捆。")
    cs=sum(1 for p in pipe_sources if p["material_family"]=="CS"); ss=sum(1 for p in pipe_sources if p["material_family"]=="SS")
    if cs: warnings.append(f"检测到 {cs} 条碳钢管道长料源数据；V3.0 将自动规划裸装扎捆。")
    if ss: warnings.append(f"检测到 {ss} 条不锈钢管道；默认不自动裸装扎捆，避免忽略表面保护要求。")
    return results,pipe_sources,warnings

def insert_pipe_fitting_rows(parsed, mode="append"):
    for row in parsed: checked_item_values(row)
    con=db(); cur=con.cursor()
    if mode=="replace":
        cur.execute("DELETE FROM crate_placements"); cur.execute("DELETE FROM items"); cur.execute("DELETE FROM crates")
    enriched=[]
    for r in parsed:
        rr=dict(r)
        if not rr.get('size_grade'):
            sp=_size_provenance_for_item(rr); rr.update(size_grade=sp['grade'],size_source=sp['source'],size_note=sp['note'],size_evidence_id=sp.get('evidence_id'))
        rr.setdefault('size_evidence_id',None); rr.setdefault('weight_evidence_id',None); rr.setdefault('cg_x_mm',None); rr.setdefault('cg_y_mm',None); rr.setdefault('cg_z_mm',None); rr.setdefault('cg_source','几何中心假设'); rr.setdefault('cg_confirmed',0); enriched.append(rr)
    cur.executemany("""INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,weight_note,size_grade,size_source,size_note,size_evidence_id,weight_evidence_id,package_type,stackable,rotatable,cg_x_mm,cg_y_mm,cg_z_mm,cg_source,cg_confirmed,crate_id)
                       VALUES(:name,:qty,:length_mm,:width_mm,:height_mm,:weight_kg,:weight_grade,:weight_source,:weight_note,:size_grade,:size_source,:size_note,:size_evidence_id,:weight_evidence_id,:package_type,:stackable,:rotatable,:cg_x_mm,:cg_y_mm,:cg_z_mm,:cg_source,:cg_confirmed,NULL)""",enriched)
    con.commit(); con.close()
    return len(parsed)

def replace_pipe_sources(pipe_sources, mode="append"):
    con=db(); cur=con.cursor()
    if mode=="replace":
        cur.execute("DELETE FROM pipe_sources")
        cur.execute("DELETE FROM items WHERE package_type='裸装扎捆'")
    cur.executemany("""INSERT INTO pipe_sources(code,category,dn_text,description,material,total_length_m,od_mm,thickness_mm,density_kg_m3,material_family)
                       VALUES(:code,:category,:dn_text,:description,:material,:total_length_m,:od_mm,:thickness_mm,:density_kg_m3,:material_family)""",pipe_sources)
    con.commit(); con.close()

def _pipe_kg_per_m(od_mm,t_mm,rho):
    return _pipe_shell_area_mm2(float(od_mm),float(t_mm))*1000.0/1e9*float(rho)

def rebuild_pipe_bundles(ship_length_mm=6000,max_bundle_weight=3000,max_bundle_width=1200,max_bundle_height=1200):
    if _asu_profile()['enabled']:
        return ASU.rebuild_pipes(ship_length_mm,max_bundle_weight,max_bundle_width,max_bundle_height)
    if min(ship_length_mm,max_bundle_weight,max_bundle_width,max_bundle_height)<=0: raise ValueError("管道扎捆参数必须大于0。")
    con=db(); cur=con.cursor(); sources=[dict(r) for r in cur.execute("SELECT * FROM pipe_sources WHERE material_family='CS' ORDER BY id").fetchall()]
    cur.execute("DELETE FROM items WHERE package_type='裸装扎捆'")
    bundle_count=0; total_pipes=0
    for s in sources:
        total_mm=float(s["total_length_m"])*1000.0
        if total_mm<=0: continue
        n_pipes=max(1,int(math.ceil(total_mm/float(ship_length_mm)))); piece_len=total_mm/n_pipes
        od=float(s["od_mm"]); t=float(s["thickness_mm"]); rho=float(s["density_kg_m3"]); kg_m=_pipe_kg_per_m(od,t,rho); piece_w=kg_m*(piece_len/1000.0)
        across=max(1,int(max_bundle_width//max(od,1))); high=max(1,int(max_bundle_height//max(od,1))); by_cross=max(1,across*high); by_weight=max(1,int(max_bundle_weight//max(piece_w,0.1))); cap=max(1,min(by_cross,by_weight))
        remaining=n_pipes
        while remaining>0:
            cnt=min(cap,remaining); remaining-=cnt; cols=min(across,cnt); rows=int(math.ceil(cnt/cols)); width=cols*od; height=rows*od; bundle_w=piece_w*cnt; bundle_count+=1; total_pipes+=cnt
            name=f"BND-{bundle_count:03d}｜{s['code']}｜碳钢管道扎捆｜{cnt}根｜L≈{piece_len/1000:.2f}m"
            note=f"原表总长度 {float(s['total_length_m']):g} m；按规划单根长度 {ship_length_mm/1000:g} m 均分，不代表实际下料长度。理论重量按 Φ{od:g}×{t:g} mm 计算。"
            cur.execute("""INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,weight_note,size_grade,size_source,size_note,package_type,stackable,rotatable,crate_id)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",(name,1,round(piece_len,1),round(width,1),round(height,1),round(bundle_w,1),"B","管道理论重量+规划扎捆",note,"C","规划扎捆外形","管束宽高与长度由规划规则生成，正式前需实测/批准确认。","裸装扎捆",1,0))
    con.commit(); con.close(); return bundle_count,total_pipes,len(sources)

def _dimension_scale_from_headers(headers, rows, columns, data_start):
    text=" ".join(str(headers[columns[k]]) for k in ("length_mm","width_mm","height_mm") if k in columns and columns[k] < len(headers)).lower()
    if "cm" not in text and "厘米" not in text: return 1.0,False
    samples=[]
    for row in rows[data_start:data_start+20]:
        for k in ("length_mm","width_mm","height_mm"):
            idx=columns.get(k)
            if idx is not None and idx < len(row):
                x=clean_number(row[idx])
                if x: samples.append(x)
    # A dimension such as 4700 cannot realistically mean 4700 cm for normal container cargo.
    if samples and max(samples)>1000:
        return 1.0,True
    return 10.0,False

def parse_material_rows(rows, columns, data_start_idx, headers=None):
    dims = "size" in columns or all(k in columns for k in ("length_mm","width_mm","height_mm"))
    if "name" not in columns: raise ValueError("必须指定“物料/货物名称”列。")
    if not dims: raise ValueError("必须指定“外形尺寸”列，或分别指定长、宽、高三列。")
    if "weight_kg" not in columns and "total_weight_kg" not in columns:
        raise ValueError("必须指定“单件重量”或“总重量”列。")
    scale, unit_header_suspect = _dimension_scale_from_headers(headers or [], rows, columns, data_start_idx)
    raw_records=[]; skipped=[]; qty_assumed=0; weight_conflicts=0; last_group=None; shipping_skipped=0; shipping_qty_assumed=0
    for excel_row_no,row in enumerate(rows[data_start_idx:], start=data_start_idx+1):
        if not any(v not in (None,"") for v in row): continue
        def get(field):
            idx=columns.get(field); return row[idx] if idx is not None and idx < len(row) else None
        source_name=str(get("name") or "").strip()
        unit_text=str(get('unit') or '')
        explicit_name=source_name.splitlines()[0].strip() if unit_text else source_name
        material_text=str(get("material") or "").strip()
        size=parse_size(get("size")) if "size" in columns else None
        cylinder=bool(parse_cylinder_size(get("size"))) if "size" in columns else False
        packing_raw=get('package_type')
        if not packing_raw and unit_text:
            packing_raw='铁桶' if re.search(r'桶|tambor',unit_text,re.I) else '编织袋' if '珠光砂' in explicit_name else '裸装'
        upright=cylinder and (upright_drum(packing_raw) or bool(re.search(r'桶|tambor',unit_text,re.I)))
        if upright:size=(size[1],size[2],size[0])
        if size: l,w,h=size
        else:
            l=clean_number(get("length_mm")); w=clean_number(get("width_mm")); h=clean_number(get("height_mm"))
            if l: l*=scale
            if w: w*=scale
            if h: h*=scale
        # Header/footer/summary rows without 3D dimensions are not usable for packing geometry.
        if not l or not w or not h:
            if explicit_name and unit_text:shipping_skipped+=1
            if explicit_name: skipped.append((excel_row_no,f"{explicit_name}：尺寸不完整"))
            continue
        if explicit_name:
            last_group=explicit_name
        name=explicit_name or last_group
        if not name:
            skipped.append((excel_row_no,"名称为空")); continue
        if unit_text:
            declared_qty=clean_number(get('qty'))
            if declared_qty is None or declared_qty<=0:raise ValueError(f'第{excel_row_no}行数量缺失或无效，禁止默认1件')
            is_volume=bool(re.search(r'立方|m[³3]',unit_text,re.I))
            if not is_volume and not float(declared_qty).is_integer():raise ValueError(f'第{excel_row_no}行件数不是整数')
            qty=declared_qty if is_volume else int(declared_qty);assumed=False
        else:qty,assumed=_qty_value(get("qty"))
        qty_assumed += int(assumed)
        shipping_qty_assumed+=int(assumed and bool(unit_text))
        extra_note=('SHIPMENT_NON_CONTAINER；原表注明散货运输，分段尺寸待提供。' if re.search(r'散货船|buque de carga general',str(get('shipment_note') or ''),re.I) else '')
        if unit_text and re.search(r'立方|m[³3]',unit_text,re.I):
            if '珠光砂' not in explicit_name or not re.search(r'1\s*立方\s*8\s*包|8\s*袋|8\s*bolsas',str(get('size') or ''),re.I):
                raise ValueError(f'第{excel_row_no}行以体积计量，必须提供每立方的包装数，禁止直接当件数')
            qty=qty*8
            if not float(qty).is_integer():raise ValueError(f'第{excel_row_no}行换算后不是整数包')
            qty=int(qty);extra_note='PERLITE_FLEXIBLE_VOLUME；PERLITE_VOID_FILL；按原表每立方8包换算，单包0.125m³；袋尺寸仅作参考。'
        elif unit_text and re.search(r'套|conjunto',unit_text,re.I) and qty==1:
            pieces=re.search(r'([一二三四五六七八九十0-9]+)\s*件',str(get('size') or ''))
            if pieces:
                value=pieces[1];qty=int(value) if value.isdigit() else {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}.get(value,0)
                if qty<=0:raise ValueError(f'第{excel_row_no}行件数无法识别')
                extra_note=f'原表1套按尺寸说明拆分为{qty}件。'
        if unit_text and not get('package_type') and packing_raw=='裸装':extra_note+='原表未声明包装方式，当前按设备尺寸规划，包装/支座尺寸待核实。'
        wt=clean_number(get("weight_kg")); tw=clean_number(get("total_weight_kg"))
        if unit_text and wt is None:
            embedded=re.search(r'(\d+(?:\.\d+)?)\s*kg',str(get('size') or ''),re.I)
            if embedded and tw is not None:
                declared=float(embedded[1])
                if abs(declared*qty-tw)>max(.5,.01*max(tw,declared*qty,1)):
                    weight_conflicts+=1
                    extra_note+=f'原表重量矛盾：{qty}件×{declared:g}kg={qty*declared:g}kg，总重栏{tw:g}kg。'
        # V4.6: cell semantics come from the mapped field, not from whether another column exists.
        # If total is blank but unit weight exists, total MUST be unit×qty (old code could divide by qty twice).
        if tw is not None:
            line_total=tw; weight_kind='total'
            if wt is not None and abs(tw-wt*qty)>max(0.5,0.01*max(abs(tw),abs(wt*qty),1)):
                weight_conflicts += 1
        elif wt is not None:
            line_total=wt*qty; weight_kind='unit'
        else:
            line_total=None; weight_kind='missing'
        raw_records.append({"row":excel_row_no,"name":name,"explicit_name":bool(explicit_name),"qty":qty,
                            "length_mm":l,"width_mm":w,"height_mm":h,"line_total":line_total,"weight_kind":weight_kind,
                            "cylinder":cylinder,"upright":upright,"package_type":normalize_package(packing_raw),"extra_note":extra_note,"source_row":excel_row_no,"source_name":source_name,"shipping_unit":bool(unit_text),"stackable":0 if (unit_text or cylinder) and normalize_package(packing_raw)!="木箱" else truthy(get("stackable"),1),
                            "rotatable":truthy(get("rotatable"),1),"box_no":str(get("box_no") or "").strip(),
                            "material":material_text,"cg_x_mm":clean_number(get("cg_x_mm")),"cg_y_mm":clean_number(get("cg_y_mm")),"cg_z_mm":clean_number(get("cg_z_mm"))})
    # Split into groups: continuation lines with blank names belong to the preceding named line.
    groups=[]; cur=[]
    for r in raw_records:
        if r["explicit_name"] and cur:
            groups.append(cur); cur=[]
        cur.append(r)
    if cur: groups.append(cur)
    parsed=[]; allocated_weight_rows=0
    for group in groups:
        missing=[r for r in group if r["line_total"] is None]
        known=[r for r in group if r["line_total"] is not None]
        # Only an explicitly mapped TOTAL weight may be treated as a group gross weight.
        # A unit-weight row must never be redistributed to continuation rows.
        group_total_rows=[r for r in known if r.get("weight_kind")=="total"]
        if missing and len(group)>1 and group_total_rows:
            group_total=group_total_rows[0]["line_total"]
            bases=[r["length_mm"]*r["width_mm"]*r["height_mm"]*r["qty"] for r in group]
            denom=sum(bases) or 1
            for r,b in zip(group,bases):
                r["alloc_total"]=group_total*b/denom
                allocated_weight_rows += int(r["line_total"] is None)
        else:
            for r in group: r["alloc_total"]=r["line_total"]
        for r in group:
            total=r.get("alloc_total")
            if total is None or total < 0:
                skipped.append((r["row"],f"{r['name']}：重量无效")); continue
            unit_weight=total/r["qty"]
            derived = (r.get("line_total") is None)
            parsed.append({"name":r["name"],"qty":r["qty"],"length_mm":r["length_mm"],"width_mm":r["width_mm"],
                           "height_mm":r["height_mm"],"weight_kg":unit_weight,
                           "weight_grade":"C" if derived else "B",
                           "weight_source":"组总重按几何体积分摊" if derived else "上传清单声明重量（未确认）",
                           "weight_note":"原始清单只有同组总重，单件重量按外包络体积比例分摊。" if derived else "直接来自上传清单的单件重量或总重量换算；箱级数据对照版前需核实为实称/供应商批准值并升级A级。",
                           "size_grade":"B","size_source":"上传清单声明尺寸（未确认）",
                           "source_row":r["source_row"],"source_name":r["source_name"],"shipping_unit":r["shipping_unit"],"weight_conflict":"重量矛盾" in r["extra_note"],
                           "size_note":r["extra_note"]+(("圆桶立放：长/宽=直径，高=轴向高度；按外包络占位，未加托盘尺寸。原始尺寸：" if r.get("upright") else "圆柱卧放：长=轴向长度，宽/高=直径；按外包络占位，未加支座/包装尺寸。原始尺寸：")+str(rows[r["row"]-1][columns["size"]])+"。" if r.get("cylinder") else "")+"尺寸来自上传清单/字段映射，正式输出前需核实。",
                           "package_type":"木箱" if "5083" in str(r.get("material") or "").upper() or "5083" in str(r.get("name") or "").upper() else r["package_type"],
                           "stackable":r["stackable"],"rotatable":r["rotatable"],"box_no":r["box_no"],
                           "forced_crate_5083": bool("5083" in str(r.get("material") or "").upper() or "5083" in str(r.get("name") or "").upper()),
                           "cg_x_mm":r.get("cg_x_mm"),"cg_y_mm":r.get("cg_y_mm"),"cg_z_mm":r.get("cg_z_mm"),
                           "cg_source":"上传清单声明CG" if all(r.get(k) is not None for k in ("cg_x_mm","cg_y_mm","cg_z_mm")) else "几何中心假设",
                           "cg_confirmed":1 if all(r.get(k) is not None for k in ("cg_x_mm","cg_y_mm","cg_z_mm")) else 0})
    meta={"shipping_skipped":shipping_skipped,"shipping_qty_assumed":shipping_qty_assumed,"qty_assumed":qty_assumed,"allocated_weight_rows":allocated_weight_rows,"unit_header_suspect":unit_header_suspect,"weight_conflicts":weight_conflicts}
    return parsed, skipped, meta

def _cleanup_import_sessions(max_age_hours=24):
    cutoff=time.time()-max(1,float(max_age_hours))*3600
    try:
        for path in IMPORT_DIR.glob('*.json'):
            try:
                if path.stat().st_mtime<cutoff: path.unlink()
            except OSError: pass
    except OSError: pass

def save_import_session(table, best, mode, filename, content=None):
    _cleanup_import_sessions()
    token=uuid.uuid4().hex
    path=IMPORT_DIR/f"{token}.json"
    payload={"filename":filename,"mode":mode,"sheet":table["sheet"],"rows":table["rows"],"best":best,"state_fingerprint":_state_fingerprint(*fetch_state())}
    if content is not None:
        source_path=IMPORT_DIR/f'{token}.raw';source_path.write_bytes(content);payload['source_raw_path']=str(source_path)
    path.write_text(json.dumps(payload,ensure_ascii=False,default=str),encoding="utf-8")
    return token

def load_import_session(token):
    _cleanup_import_sessions()
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""): raise ValueError("导入会话无效。")
    path=IMPORT_DIR/f"{token}.json"
    if not path.exists(): raise ValueError("导入会话已失效，请重新上传文件。")
    return json.loads(path.read_text(encoding="utf-8")), path

def excel_col_name(i):
    n=i+1; s=""
    while n:
        n,rem=divmod(n-1,26); s=chr(65+rem)+s
    return s

def _material_label_from_item_name(name):
    s=str(name or "").upper().replace("＃","#")
    # Specific grades first, then generic carbon/stainless families.
    patterns=[
        (r"\b5083\b","5083"),(r"\bQ235B\b","Q235B"),(r"\bQ235A\b","Q235A"),
        (r"\b20#\b|\b20＃\b|\b20号\b","20#"),(r"\b304L\b","304L"),(r"\b304\b","304"),
        (r"\b316L\b","316L"),(r"\b316\b","316"),(r"\b321\b","321"),(r"\b310S\b","310S"),
        (r"\bS30408\b","S30408"),(r"\bS31603\b","S31603"),(r"\bS32168\b","S32168"),
    ]
    for pat,label in patterns:
        if re.search(pat,s,re.I): return label
    if "不锈钢" in str(name): return "不锈钢"
    if "碳钢" in str(name): return "碳钢"
    if "铝" in str(name): return "铝合金"
    return "—"

def _size_provenance_for_item(item):
    """Conservative fallback for legacy rows only; V5.1 persists provenance in SQLite."""
    name=str(item.get('name') or '')
    mat=_material_label_from_item_name(name)
    if '采购' in name and 'm' in name and ('管' in name or 'PIPE' in name.upper()):
        return {'grade':'C','source':'原表截面 + 规划长度','note':'单根长度为规划拆分，未形成实测/批准外形。','evidence_id':None}
    cat=next((k for k in ('弯头','三通','异径','管帽') if k in name),'')
    if cat:
        dn_text='x'.join('DN'+x for x in re.findall(r'DN\s*(\d+)',name,re.I)) or name
        std=_dimension_override_lookup(cat,dn_text,mat if mat!='—' else name,name) or _gbt2025_standard_envelope(cat,dn_text,name,mat if mat!='—' else name,{})
        if std:
            return {'grade':std.get('size_grade','B'),'source':std.get('size_source','标准/项目尺寸'),'note':std.get('size_note',''),'evidence_id':std.get('evidence_id')}
        return {'grade':'C','source':'项目几何估算','note':'未使用厂家实测/批准尺寸。','evidence_id':None}
    if str(item.get('weight_source') or '').startswith('原始') or str(item.get('weight_source') or '')=='人工输入':
        return {'grade':'B','source':'历史输入尺寸（未确认）','note':'V5.1迁移后保守标记为B级；请绑定实测/供应商批准凭证后升级A级。','evidence_id':None}
    return {'grade':'C','source':'规划尺寸','note':'尺寸来源未被识别为实测/批准数据。','evidence_id':None}

def fetch_state():
    con = db()
    items = [dict(r) for r in con.execute("SELECT * FROM items ORDER BY id DESC").fetchall()]
    crates = [dict(r) for r in con.execute("SELECT * FROM crates ORDER BY id DESC").fetchall()]
    padding_by={r['crate_id']:r['padding_mm'] for r in con.execute('SELECT crate_id,padding_mm FROM crate_audits').fetchall()}
    for c in crates: c['layout_padding_mm']=padding_by.get(c['id'])
    crate_map = {c["id"]: c for c in crates}
    legacy_updates=[]
    for i in items:
        i["crate_name"] = crate_map.get(i["crate_id"], {}).get("name") if i["crate_id"] else None
        i["material_label"] = _material_label_from_item_name(i.get("name"))
        if not str(i.get('size_source') or '').strip() or str(i.get('size_source'))=='未标注':
            sp=_size_provenance_for_item(i)
            i['size_grade']=sp['grade']; i['size_source']=sp['source']; i['size_note']=sp['note']
            legacy_updates.append((sp['grade'],sp['source'],sp['note'],i['id']))
    if legacy_updates:
        con.executemany('UPDATE items SET size_grade=?,size_source=?,size_note=? WHERE id=?',legacy_updates); con.commit()
    sub_updates=[]
    for i in items:
        ss=str(i.get('size_subgrade') or '').strip(); ws=str(i.get('weight_subgrade') or '').strip()
        if not ss:
            ss=_cred_subgrade(i.get('size_grade'),i.get('size_source'),i.get('size_evidence_id'),i.get('size_evidence_scope'))
            i['size_subgrade']=ss
        if not ws:
            ws=_cred_subgrade(i.get('weight_grade'),i.get('weight_source'),i.get('weight_evidence_id'),i.get('weight_evidence_scope'))
            i['weight_subgrade']=ws
        # Recover scope metadata only from V5 evidence rows that actually carry a non-empty scope key.
        scope_updates=[]
        for kind in ('size','weight'):
            eid=i.get(f'{kind}_evidence_id'); scope=str(i.get(f'{kind}_evidence_scope') or ''); key=str(i.get(f'{kind}_evidence_scope_key') or '')
            if eid and (not scope or not key):
                ev=EVIDENCE_STORE.get(eid)
                if ev and str(ev.get('scope_key') or '').strip() and int(ev.get('chain_version') or 1)>=2:
                    scope=str(ev.get('scope_type') or 'item'); key=str(ev.get('scope_key') or '')
                    i[f'{kind}_evidence_scope']=scope; i[f'{kind}_evidence_scope_key']=key
            i[f'{kind}_evidence_valid']=_evidence_matches(eid,scope_type=scope or None,scope_key=key if key else None,
                evidence_types=('measured_or_approved_size','approved_dimension') if kind=='size' else ('weighing_or_approved_weight',),require_verified=True) if eid and scope and key else False
        sub_updates.append((ss,ws,i.get('size_evidence_scope') or '',i.get('size_evidence_scope_key') or '',i.get('weight_evidence_scope') or '',i.get('weight_evidence_scope_key') or '',i['id']))
    if sub_updates:
        con.executemany('UPDATE items SET size_subgrade=?,weight_subgrade=?,size_evidence_scope=?,size_evidence_scope_key=?,weight_evidence_scope=?,weight_evidence_scope_key=? WHERE id=?',sub_updates); con.commit()
    con.close()
    _refresh_stored_support_audits()
    return items, crates

def _refresh_stored_support_audits():
    """Recheck saved layouts under current support rules without moving any cargo."""
    con=db()
    try:
        for row in con.execute('SELECT crate_id,audit_status,conclusion,support_ok FROM crate_audits').fetchall():
            pls=[dict(r) for r in con.execute('SELECT * FROM crate_placements WHERE crate_id=? ORDER BY seq',(row['crate_id'],)).fetchall()]
            if not pls: continue
            result=support_load_analysis(pls)
            hard=result['hard_issues']; review=result['engineering_reviews']
            if not hard and not review: continue
            status='不通过' if hard else ('需复核' if row['audit_status']=='通过' else row['audit_status'])
            conclusion=str(row['conclusion'] or '')
            for note in hard+review:
                if note not in conclusion: conclusion+='；V5.8.28复核：'+note
            if status!=row['audit_status'] or conclusion!=str(row['conclusion'] or ''):
                con.execute('UPDATE crate_audits SET audit_status=?,support_ok=?,conclusion=? WHERE crate_id=?',
                            (status,0 if hard else row['support_ok'],conclusion,row['crate_id']))
        con.commit()
    finally:
        con.close()


def _padding_setting(strategy):
    value=(strategy or {}).get('padding_mm')
    return checked_number(100 if value in (None,'') else value,'装箱间隙')


def crate_stats(crate, items):
    citems = [i for i in items if i["crate_id"] == crate["id"]]
    cargo_vol = sum(i["qty"] * i["length_mm"]*i["width_mm"]*i["height_mm"] for i in citems)
    crate_vol = crate["length_mm"]*crate["width_mm"]*crate["height_mm"]
    wr=_crate_weight_result(crate,citems)
    cargo_weight=wr['cargo_weight']
    strategy=_strategy_load() or {}; pad=crate.get('layout_padding_mm')
    if pad is None: pad=_padding_setting(strategy)
    geom=_crate_geometry(crate['length_mm'],crate['width_mm'],crate['height_mm'],cargo_weight,pad)
    clear_vol=geom['clear_l']*geom['clear_w']*geom['clear_h']; usable_vol=geom['usable_l']*geom['usable_w']*geom['usable_h']
    grades=[str(i.get("weight_grade") or "C") for i in citems]
    worst = "C" if "C" in grades else ("B" if "B" in grades else ("A" if grades else "—"))
    estimated_w=sum(i["qty"]*i["weight_kg"] for i in citems if str(i.get("weight_grade") or "C") == "C")
    return {
        "internal_metrics_available":not _is_prepacked(crate),
        "cargo_weight": cargo_weight,
        "tare_weight":wr['effective_tare'],
        "estimated_tare_weight":wr['estimated_tare'],
        "gross_weight": wr['gross_weight'],
        "gross_weight_source":wr['gross_source'],
        "actual_gross_weight":wr['actual_gross'],
        "actual_gross_weight_warning":wr.get('actual_gross_warning'),
        "tare_breakdown":wr['breakdown'],
        "utilization": 0 if _is_prepacked(crate) else (round((cargo_vol/crate_vol*100),1) if crate_vol else 0),
        "net_utilization": 0 if _is_prepacked(crate) else (round((cargo_vol/usable_vol*100),1) if usable_vol else 0),
        "outer_cbm": round(crate_vol/1e9,3),
        "clear_cbm": round(clear_vol/1e9,3),
        "usable_cbm": round(usable_vol/1e9,3),
        "clear_dims": "内部资料未知" if _is_prepacked(crate) else f"{geom['clear_l']:.0f}×{geom['clear_w']:.0f}×{geom['clear_h']:.0f}",
        "usable_dims": f"{geom['usable_l']:.0f}×{geom['usable_w']:.0f}×{geom['usable_h']:.0f}",
        "structure_class": geom['class_label'],
        "item_count": sum(i["qty"] for i in citems),
        "sku_count": len(citems),
        "weight_grade":worst,
        "estimated_weight_ratio":round(estimated_w/cargo_weight*100,1) if cargo_weight else 0,
        "contents": [f"{i['name']} ×{i['qty']}" for i in citems[:8]],
        "more_contents": max(0, len(citems)-8)
    }


def _support_ok(candidate, placed, analysis=None):
    if candidate["z"] <= 1e-6:
        return True
    supports=[]
    for b in placed:
        if abs((b["z"]+b["h"])-candidate["z"]) <= 1e-6:
            area=_overlap_area_xy(candidate,b)
            if area>0:
                if not b.get("stackable",True):
                    return False
                if str(b.get('type') or '')=='木箱' and not bool(b.get('top_load_verified')):
                    return False
                supports.append((b,area))
    if not supports:
        return False
    footprint=max(1e-9,candidate["l"]*candidate["w"])
    support_area=sum(a for _,a in supports)
    if support_area < footprint*0.75:
        return False
    # V5.5: evaluate cumulative static top-load through the entire support stack, not only the
    # immediate candidate weight.  Unknown or exceeded wood-crate top-load is a hard rejection.
    if top_load_audit(list(placed)+[candidate],analysis).get('hard_issues'):
        return False
    return True

def _point_inside_box(pt,b):
    x,y,z=pt
    return (b["x"]-1e-6 <= x < b["x"]+b["l"]-1e-6 and
            b["y"]-1e-6 <= y < b["y"]+b["w"]-1e-6 and
            b["z"]-1e-6 <= z < b["z"]+b["h"]-1e-6)

def _prune_points(points, placed, L,W,H):
    valid=[]
    seen=set()
    for x,y,z in points:
        x,y,z=round(x,3),round(y,3),round(z,3)
        if x<0 or y<0 or z<0 or x>=L or y>=W or z>=H: continue
        if any(_point_inside_occupied_shape((x,y,z),b) for b in placed): continue
        k=(x,y,z)
        if k not in seen:
            seen.add(k); valid.append(k)
    # Keep candidates closest to origin first; cap prevents pathological growth on large lists.
    valid.sort(key=lambda q:(q[2],q[1],q[0]))
    return valid[:900]

def _safe_local_cg(obj, axis, placed_dim=None):
    """Return a usable package-local CG coordinate.

    Legacy/bare packages may carry cg_local_* as a missing key, None, blank text or
    a non-finite value.  In all such cases the conservative neutral fallback is the
    geometric centre of the placed dimension, not zero and never float(None).
    """
    key=f"cg_local_{axis}"
    dim_key={"x":"length_mm","y":"width_mm","z":"height_mm"}[axis]
    raw=obj.get(key)
    try:
        v=float(raw)
        if math.isfinite(v):
            return v
    except (TypeError,ValueError):
        pass
    dim=placed_dim
    if dim is None:
        try: dim=float(obj.get(dim_key) or 0)
        except (TypeError,ValueError): dim=0.0
    try: dim=float(dim or 0)
    except (TypeError,ValueError): dim=0.0
    return max(0.0,dim/2.0)

_REJECT_REASON_LABELS={
    "payload":"载荷超限",
    "boundary":"柜内边界/高度不满足",
    "door_dimension":"柜门通过尺寸不满足",
    "floor_pressure":"静态平均有效接触压力超限",
    "collision":"3D碰撞",
    "support_topload":"支撑/合成重心平衡或保守顶载条件不满足",
    "physical_audit_error":"物理约束审计异常（按不放行处理）",
}

def _reject_candidate(state, reason):
    stats=state.setdefault("reject_stats",{})
    stats[reason]=int(stats.get(reason,0))+1
    return None

def _candidate_door_review_penalty(cand,c):
    """Cheap point-specific proxy for the final door manoeuvre review.

    0 means the final XY position stays inside the conservative straight door projection;
    1 means the final placement will require lateral/special-sequence review.  This is only
    a tie-breaker: it never turns an infeasible candidate into a feasible one.
    """
    if str(c.get('code','')).startswith('WOODBOX') or not _needs_door_check(c):
        return 0
    W=float(c.get('inner_w') or 0); dw=float(c.get('door_w') or W)
    y0=max(0.0,(W-dw)/2.0); y1=min(W,y0+dw)
    y=float(cand.get('y') or 0); w=float(cand.get('w') or 0)
    return 0 if y>=y0-1e-6 and y+w<=y1+1e-6 else 1

def _projected_centered_cg_penalty(placements,cand,c):
    """Estimate residual horizontal CG offset after the same rigid centring used at finalisation.

    This is a search tie-breaker only.  It does not replace the final container CG audit.
    """
    if str(c.get('code','')).startswith('WOODBOX'):
        return (0.0,0.0)
    pls=list(placements)+[cand]
    total=sum(max(0.0,float(p.get('weight_kg') or 0)) for p in pls)
    if total<=0: return (0.0,0.0)
    L=float(c.get('inner_l') or 0); W=float(c.get('inner_w') or 0)
    if L<=0 or W<=0: return (100.0,100.0)
    cgx=sum(float(p.get('weight_kg') or 0)*(float(p.get('x') or 0)+_safe_local_cg(p,'x',p.get('l'))) for p in pls)/total
    cgy=sum(float(p.get('weight_kg') or 0)*(float(p.get('y') or 0)+_safe_local_cg(p,'y',p.get('w'))) for p in pls)/total
    minx=min(float(p.get('x') or 0) for p in pls); maxx=max(float(p.get('x') or 0)+float(p.get('l') or 0) for p in pls)
    miny=min(float(p.get('y') or 0) for p in pls); maxy=max(float(p.get('y') or 0)+float(p.get('w') or 0) for p in pls)
    margin=_asu_margin(c)
    dx=truck_model.shift(c,pls,margin-minx,L-margin-maxx,L/2-cgx) if c.get('vehicle_type')=='truck' else max(margin-minx,min(L/2-cgx,L-margin-maxx))
    dy_low=margin-miny; dy_high=W-margin-maxy
    if _needs_door_check(c):
        dw=float(c.get('door_w') or W); y0=max(0.0,(W-dw)/2.0); y1=min(W,y0+dw)
        door_safe=all(float(p.get('y') or 0)>=y0-1e-6 and float(p.get('y') or 0)+float(p.get('w') or 0)<=y1+1e-6 for p in pls)
        if door_safe:
            dy_low=max(dy_low,y0-miny); dy_high=min(dy_high,y1-maxy)
    dy=max(dy_low,min(W/2-cgy,dy_high))
    ox=abs((cgx+dx)/L*100.0-50.0); oy=abs((cgy+dy)/W*100.0-50.0)
    return (max(ox,oy),ox+oy)

def _can_place(pkg, dims, pt, state, c):
    l,w,h=dims; x,y,z=pt
    margin=_asu_margin(c)
    if margin and (x<margin-1e-6 or y<margin-1e-6 or x+l>c['inner_l']-margin+1e-6 or y+w>c['inner_w']-margin+1e-6):
        return _reject_candidate(state,'boundary')
    if _asu_profile()['enabled'] and str(c.get('code','')).startswith('WOODBOX') and state['placements']:
        group=pkg.get('asu_group') or 'unclassified:'+str(pkg.get('source_id') or pkg.get('name'))
        if any((q.get('asu_group') or 'unclassified:'+str(q.get('source_id') or q.get('name')))!=group for q in state['placements']):
            return _reject_candidate(state,'support_topload')
    try:
        validate_contact_geometry(pkg)
    except (ValueError,TypeError,OverflowError):
        return _reject_candidate(state,'physical_audit_error')
    if state["weight"] + pkg["weight_kg"] > c["payload"] + 1e-6:
        return _reject_candidate(state,"payload")
    if x+l>c["inner_l"]+1e-6 or y+w>c["inner_w"]+1e-6:
        return _reject_candidate(state,"boundary")
    if z<=1e-6 and c.get('floor_load_kg_m2') not in (None,''):
        # Fail closed: when a configured physical check cannot be evaluated, do not silently accept.
        try:
            probe=dict(pkg); probe.update({'l':l,'w':w,'z':0})
            pressure=floor_contact_pressure_kg_m2(probe,0.0)
            if pressure is not None and pressure>float(c.get('floor_load_kg_m2'))+1e-6:
                return _reject_candidate(state,"floor_pressure")
        except Exception:
            return _reject_candidate(state,"physical_audit_error")
    if bool(c.get('open_top')) or 'OT' in str(c.get('code','')):
        # Open-top may carry over-height cargo as OOG, but an over-height piece must sit on the floor.
        if z>1e-6 and z+h>c["inner_h"]+1e-6:
            return _reject_candidate(state,"boundary")
    elif z+h>c["inner_h"]+1e-6:
        return _reject_candidate(state,"boundary")
    # Closed containers need to pass the door. Open-top containers are exempt from door height.
    if _needs_door_check(c) and (w>c["door_w"]+1e-6 or h>c["door_h"]+1e-6):
        return _reject_candidate(state,"door_dimension")
    pcx=_safe_local_cg(pkg,'x',pkg.get('length_mm',l)); pcy=_safe_local_cg(pkg,'y',pkg.get('width_mm',w)); pcz=_safe_local_cg(pkg,'z',pkg.get('height_mm',h))
    rotated=abs(float(l)-float(pkg.get('width_mm',w)))<1e-6 and abs(float(w)-float(pkg.get('length_mm',l)))<1e-6 and abs(float(pkg.get('length_mm',l))-float(pkg.get('width_mm',w)))>1e-6
    if rotated: pcx,pcy=pcy,pcx
    cand={"x":x,"y":y,"z":z,"l":l,"w":w,"h":h,
          "category":pkg.get("category"),"fitting_category":pkg.get("fitting_category"),"source_name":pkg.get("source_name"),"name":pkg.get("name"),
          "weight_kg":pkg.get("weight_kg",0),"stackable":pkg.get("stackable",True),"max_top_load_kg":pkg.get("max_top_load_kg"),
          "cg_local_x":pcx,"cg_local_y":pcy,"cg_local_z":pcz,"cg_confirmed":pkg.get('cg_confirmed'),"cg_source_type":pkg.get('cg_source_type'),"top_load_verified":pkg.get('top_load_verified'),"top_load_source_type":pkg.get('top_load_source_type'),"crate_audit_status":pkg.get('crate_audit_status'),
          "lashing_required":pkg.get('lashing_required'),"lashing_points":pkg.get('lashing_points'),"lashing_confirmed":pkg.get('lashing_confirmed'),"handling_note":pkg.get('handling_note'),
          "contact_area_mode":pkg.get('contact_area_mode','UNSPECIFIED'),"contact_area_source_type":pkg.get('contact_area_source_type','UNCONFIRMED'),"skid_count":pkg.get('skid_count',0),"skid_width_mm":pkg.get('skid_width_mm'),"skid_contact_length_mm":pkg.get('skid_contact_length_mm'),"custom_contact_area_m2":pkg.get('custom_contact_area_m2')}
    if _asu_profile()['enabled']:
        cand.update({k:pkg.get(k) for k in ('asu_key','asu_group','asu_data','asu_verified')})
    collision_fn=_occupied_overlap if str(c.get('code','')).startswith('WOODBOX') else boxes_overlap
    if any(collision_fn(cand,b) for b in state["placements"]):
        return _reject_candidate(state,"collision")
    if not _support_ok(cand,state["placements"]):
        return _reject_candidate(state,"support_topload")
    # V5.7: cumulative floor pressure is a mandatory search-time physical constraint.
    if c.get("floor_load_kg_m2") not in (None,""):
        try:
            if floor_load_audit(list(state["placements"])+[cand],c).get("hard_issues"):
                return _reject_candidate(state,"floor_pressure")
        except Exception:
            return _reject_candidate(state,"physical_audit_error")
    return cand

def _new_bin_state(c):
    margin=_asu_margin(c)
    points=[(margin,margin,0.0)]
    # Closed containers often have a door narrower than the inner width.  Add the
    # conservative straight-door projection edge as an initial anchor so the first
    # package is not forced to start at y=0 and create an avoidable manoeuvre review.
    if not str(c.get('code','')).startswith('WOODBOX') and _needs_door_check(c):
        W=float(c.get('inner_w') or 0); dw=float(c.get('door_w') or W)
        y0=max(margin,(W-dw)/2.0)
        if y0>1e-6: points.append((margin,round(y0,3),0.0))
    return {"container":c,"placements":[],"points":points,"weight":0.0,"reject_stats":{}}

def _ordered_packages(packages):
    # User-declared void-fill bags follow the main equipment and crates.
    # Large/heavy/non-stackable first. This keeps heavy cargo low and reduces fragmentation.
    return sorted(packages,key=lambda p:(
        not bool(p.get("void_fill")),
        not bool(p.get("stackable",True)),
        p["weight_kg"],
        p["length_mm"]*p["width_mm"]*p["height_mm"]
    ),reverse=True)

def _place_one(pkg, states):
    best=None
    for si,st in enumerate(states):
        c=st["container"]
        for dims in orientations(pkg["length_mm"],pkg["width_mm"],pkg["height_mm"],pkg.get("rotatable",True)):
            for pt in st["points"]:
                cand=_can_place(pkg,dims,pt,st,c)
                if not cand: continue
                maxx=max([b["x"]+b["l"] for b in st["placements"]]+[cand["x"]+cand["l"]])
                maxy=max([b["y"]+b["w"] for b in st["placements"]]+[cand["y"]+cand["w"]])
                maxz=max([b["z"]+b["h"] for b in st["placements"]]+[cand["z"]+cand["h"]])
                # V5.7: preserve the feasible set, but prefer points that avoid an avoidable
                # door-manoeuvre review and leave less residual CG offset after rigid centring.
                # Compactness remains the next objective; final audit is still authoritative.
                door_penalty=_candidate_door_review_penalty(cand,c)
                cg_max,cg_sum=_projected_centered_cg_penalty(st["placements"],cand,c)
                score=(door_penalty, round(cg_max,4), round(cg_sum,4), 0 if st["placements"] else 1,
                       cand["z"], maxz, maxy, maxx, cand["y"], cand["x"], si)
                if best is None or score<best[0]:
                    best=(score,si,cand)
    if best is None: return False
    _,si,cand=best
    st=states[si]
    pl=dict(cand)
    pl.update({k:pkg.get(k) for k in ("display_shape","asu_key","asu_group","asu_data","asu_verified","name","type","weight_kg","stackable","rotatable","source_id","source_name",
                                      "actual_l","actual_w","actual_h","material_family","size_class","category","weight_grade","weight_source","max_top_load_kg","top_load_verified","top_load_source_type","crate_audit_status","cg_confirmed","cg_source_type","lashing_required","lashing_points","lashing_confirmed","handling_note","contact_area_mode","contact_area_source_type","skid_count","skid_width_mm","skid_contact_length_mm","custom_contact_area_m2")})
    st["placements"].append(pl); st["weight"] += pkg["weight_kg"]
    st["points"].extend([(pl["x"]+pl["l"],pl["y"],pl["z"]),(pl["x"],pl["y"]+pl["w"],pl["z"]),(pl["x"],pl["y"],pl["z"]+pl["h"])])
    if str(st["container"].get("code","")).startswith("WOODBOX"):
        st["points"].extend(_shape_anchor_points(pl))
    st["points"]=_prune_points(st["points"],st["placements"],st["container"]["inner_l"],st["container"]["inner_w"],st["container"]["inner_h"])
    return True

def _center_container_state(st):
    """Translate a completed container load as one rigid group toward the container CG centre.

    Relative geometry, collision, support and stack relationships are unchanged.  Translation is
    clamped to the free floor margins, so it cannot create a boundary violation.  WOODBOX states
    are excluded because smart-crate sizing intentionally uses an origin-based compact envelope.
    """
    c=st.get('container',{}); pls=st.get('placements',[])
    if not pls or str(c.get('code','')).startswith('WOODBOX'): return st
    total=sum(float(p.get('weight_kg',0)) for p in pls)
    if total<=0: return st
    L=float(c['inner_l']); W=float(c['inner_w'])
    cgx=sum(float(p.get('weight_kg',0))*(float(p['x'])+_safe_local_cg(p,'x',p.get('l'))) for p in pls)/total
    cgy=sum(float(p.get('weight_kg',0))*(float(p['y'])+_safe_local_cg(p,'y',p.get('w'))) for p in pls)/total
    minx=min(float(p['x']) for p in pls); maxx=max(float(p['x'])+float(p['l']) for p in pls)
    miny=min(float(p['y']) for p in pls); maxy=max(float(p['y'])+float(p['w']) for p in pls)
    want_dx=L/2-cgx; want_dy=W/2-cgy
    margin=_asu_margin(c)
    dx=truck_model.shift(c,pls,margin-minx,L-margin-maxx,want_dx) if c.get('vehicle_type')=='truck' else max(margin-minx,min(want_dx,L-margin-maxx))
    dy_low=margin-miny; dy_high=W-margin-maxy
    # V5.7: if the completed layout is currently inside the conservative straight door
    # projection, final rigid CG centring must not create a new lateral manoeuvre review.
    if _needs_door_check(c):
        dw=float(c.get('door_w') or W); y0=max(0.0,(W-dw)/2.0); y1=min(W,y0+dw)
        door_safe=all(float(p.get('y') or 0)>=y0-1e-6 and float(p.get('y') or 0)+float(p.get('w') or 0)<=y1+1e-6 for p in pls)
        if door_safe:
            dy_low=max(dy_low,y0-miny); dy_high=min(dy_high,y1-maxy)
    dy=max(dy_low,min(want_dy,dy_high))
    if abs(dx)>1e-9 or abs(dy)>1e-9:
        for p in pls:
            p['x']=(float(p['x'])+dx) if c.get('vehicle_type')=='truck' else round(float(p['x'])+dx,3); p['y']=round(float(p['y'])+dy,3)
        # Points are not used after final centring in current flows, but keep state internally coherent.
        st['points']=[(round(float(x)+dx,3),round(float(y)+dy,3),z) for x,y,z in st.get('points',[])]
    return st

def pack_specific_bins(packages, bin_defs):
    states=[_new_bin_state(c) for c in bin_defs]
    for pkg in _ordered_packages(packages):
        if not _place_one(pkg,states):
            return None
    states=[s for s in states if s["placements"]]
    return states

def _cg_stability_metrics(cgx,cgy,cgz,L,W,H):
    """Geometric stability indicators; not certified dynamic tipping limits."""
    z=max(float(cgz or 0),1e-9)
    dx=max(0.0,min(float(cgx or 0),max(float(L or 0)-float(cgx or 0),0.0)))
    dy=max(0.0,min(float(cgy or 0),max(float(W or 0)-float(cgy or 0),0.0)))
    return round(dx/z,3),round(dy/z,3)

def _container_state_audit(st):
    """V5.5 container audit with explicit HARD / REVIEW / INFO semantics.

    HARD FAIL: collision/boundary/payload/top-load/confirmed static-contact-pressure failures.
    HIGH RISK: CG threshold high-risk or inherited high-risk crate.
    ENGINEERING REVIEW: important missing CG, unconfirmed contact area, path manoeuvre,
    lashing coverage and other planning conditions that need a responsible-person review.
    INFORMATION: non-blocking planning notes (lift/stack step, missing floor limit, etc.).
    """
    c=st['container']; pls=st.get('placements',[]); total=sum(float(p.get('weight_kg',0)) for p in pls)
    if total>0:
        cgx=sum(float(p.get('weight_kg',0))*(float(p['x'])+_safe_local_cg(p,'x',p.get('l'))) for p in pls)/total
        cgy=sum(float(p.get('weight_kg',0))*(float(p['y'])+_safe_local_cg(p,'y',p.get('w'))) for p in pls)/total
        cgz=sum(float(p.get('weight_kg',0))*(float(p['z'])+_safe_local_cg(p,'z',p.get('h'))) for p in pls)/total
    else:
        cgx,cgy,cgz=c['inner_l']/2,c['inner_w']/2,0
    x_pct=cgx/max(c['inner_l'],1)*100; y_pct=cgy/max(c['inner_w'],1)*100; z_pct=cgz/max(c['inner_h'],1)*100
    ox=abs(x_pct-50); oy=abs(y_pct-50)
    stability_x,stability_y=_cg_stability_metrics(cgx,cgy,cgz,c['inner_l'],c['inner_w'],c['inner_h'])
    sett=_crate_weight_settings_load()
    review=float(sett.get('container_cg_review_pct',10) or 10)
    high=float(sett.get('container_cg_high_risk_pct',sett.get('container_cg_fail_pct',20)) or 20)
    zreview=float(sett.get('container_cg_z_review_pct',60) or 60)
    zhigh=float(sett.get('container_cg_z_high_risk_pct',75) or 75)
    critical_weight=max(0.0,float(sett.get('critical_cg_weight_kg',500) or 500))
    critical_share=max(0.0,float(sett.get('critical_cg_share_pct',10) or 10))
    overheight=max([max(0.0,float(p['z'])+float(p['h'])-float(c['inner_h'])) for p in pls] or [0.0]) if (bool(c.get('open_top')) or 'OT' in str(c.get('code',''))) else 0.0

    hard_notes=asu_standard.clearance_issues(_asu_profile(),st); high_notes=[]; engineering_reviews=[]; information_notes=[]
    horizontal_offset=oy if c.get('vehicle_type')=='truck' and truck_model.axle_loads(c,pls) is not None else max(ox,oy)
    if horizontal_offset>high:
        high_notes.append(f'水平重心偏移 {horizontal_offset:.1f}% > {high:g}%')
    elif horizontal_offset>review:
        engineering_reviews.append(f'水平重心偏移 {horizontal_offset:.1f}% > {review:g}%')
    if z_pct>zhigh:
        high_notes.append(f'垂直重心高度 {z_pct:.1f}% > {zhigh:g}%')
    elif z_pct>zreview:
        engineering_reviews.append(f'垂直重心高度 {z_pct:.1f}% > {zreview:g}%')

    assumed_cg=[p for p in pls if not bool(p.get('cg_confirmed'))]
    critical_assumed=[]
    for p in assumed_cg:
        w=max(0.0,float(p.get('weight_kg') or 0)); share=(w/total*100.0) if total>0 else 0.0
        if (critical_weight>0 and w>=critical_weight-1e-9) or (critical_share>0 and share>=critical_share-1e-9):
            critical_assumed.append((str(p.get('name') or '货物'),w,share))
    if critical_assumed:
        sample='、'.join(f'{n}({w:.0f}kg/{sh:.1f}%)' for n,w,sh in critical_assumed[:4])
        engineering_reviews.append(f'关键货物CG未确认，当前按几何中心假设：{sample}')
    elif assumed_cg:
        information_notes.append(f'{len(assumed_cg)} 件非关键货物未录入实际CG，当前按几何中心假设参与静态规划')

    risky_crates=[str(p.get('name') or '木箱') for p in pls if str(p.get('type') or '')=='木箱' and str(p.get('crate_audit_status') or '') in ('高风险','不通过')]
    review_crates=[str(p.get('name') or '木箱') for p in pls if str(p.get('type') or '')=='木箱' and str(p.get('crate_audit_status') or '')=='需复核']
    if risky_crates:
        high_notes.append('含箱内规划校核高风险木箱：'+','.join(sorted(set(risky_crates))[:4]))
    elif review_crates:
        engineering_reviews.append('含箱内需复核木箱：'+','.join(sorted(set(review_crates))[:4]))
    if overheight>0:
        engineering_reviews.append(f'OT顶部超高 {overheight:.0f} mm，需人工确认OOG运输/系固方案')

    shared_analysis=support_load_analysis(pls)
    floor_a=floor_load_audit(pls,c,shared_analysis)
    hard_notes.extend(floor_a.get('hard_issues') or [])
    engineering_reviews.extend(floor_a.get('engineering_reviews') or [])
    information_notes.extend(floor_a.get('information_notes') or [])
    top_a=top_load_audit(pls,shared_analysis)
    hard_notes.extend(top_a.get('hard_issues') or [])
    engineering_reviews.extend(top_a.get('engineering_reviews') or [])
    door_a=door_maneuver_audit(pls,c)
    engineering_reviews.extend(door_a.get('engineering_reviews') or [])
    information_notes.extend(door_a.get('information_notes') or [])
    lash_a=lashing_audit(pls)
    engineering_reviews.extend(lash_a.get('engineering_reviews') or [])
    information_notes.extend(lash_a.get('information_notes') or [])

    truck_a=truck_model.audit(c,pls)
    hard_notes.extend(truck_a['hard']);engineering_reviews.extend(truck_a['review']);information_notes.extend(truck_a['info'])
    hard_notes=list(dict.fromkeys(hard_notes)); high_notes=list(dict.fromkeys(high_notes)); engineering_reviews=list(dict.fromkeys(engineering_reviews)); information_notes=list(dict.fromkeys(information_notes))
    status='不通过' if hard_notes else ('高风险' if high_notes else ('需复核' if engineering_reviews else '通过'))
    note_parts=[]
    note_parts += ['硬失败：'+x for x in hard_notes]
    note_parts += ['高风险：'+x for x in high_notes]
    note_parts += ['工程复核：'+x for x in engineering_reviews]
    note_parts += ['信息：'+x for x in information_notes]
    if not note_parts:
        note_parts=['柜级静态规划校核通过；未触发当前模型的硬失败、高风险或工程复核项。']
    return {
        'status':status,'cg_x_pct':round(x_pct,1),'cg_y_pct':round(y_pct,1),'cg_z_pct':round(z_pct,1),
        'offset_x_pct':round(ox,1),'offset_y_pct':round(oy,1),'overheight_mm':round(overheight,1),
        **({'truck_axle_loads':truck_a.get('axle'),'truck_loaded_height_mm':truck_a.get('loaded_height_mm'),'truck_gross_kg':truck_a.get('gross_kg'),'truck_max_gross_kg':(c.get('truck_parameters') or {}).get('max_gross_kg')} if c.get('vehicle_type')=='truck' else {}),
        'stability_x_ratio':stability_x,'stability_y_ratio':stability_y,
        'unknown_topload_stack_count':0,
        'floor_load_checked':bool(floor_a.get('checked')),'floor_load_limit_kg_m2':floor_a.get('limit_kg_m2'),'max_floor_pressure_kg_m2':floor_a.get('max_pressure_kg_m2'),
        'floor_contact_unconfirmed_count':sum(1 for r in (floor_a.get('rows') or []) if not r.get('contact_area_confirmed')),
        'straight_in_count':door_a.get('straight_count',0),'lift_place_count':door_a.get('lift_count',0),'maneuver_review_count':door_a.get('maneuver_count',0),
        'top_load_checked_count':top_a.get('checked_count',0),
        'lashing_required_count':lash_a.get('required_count',0),'lashing_confirmed_count':lash_a.get('confirmed_count',0),
        'assumed_cg_count':len(assumed_cg),'critical_assumed_cg_count':len(critical_assumed),
        'hard_fail_notes':hard_notes,'high_risk_notes':high_notes,'engineering_review_notes':engineering_reviews,'information_notes':information_notes,
        'note':'；'.join(note_parts)
    }

def _container_physical_issues(box):
    c=_container_by_code(str(box.get('container_code') or ''))
    if not c:
        c={'code':str(box.get('container_code') or ''),'inner_l':float(box.get('inner_l') or 0),'inner_w':float(box.get('inner_w') or 0),'inner_h':float(box.get('inner_h') or 0),
           'door_w':float(box.get('inner_w') or 0),'door_h':float(box.get('inner_h') or 0),'payload':float(box.get('payload') or 0)}
    pls=box.get('placements') or []; issues=asu_standard.clearance_issues(_asu_profile(),box); shared_analysis=support_load_analysis(pls)
    total=sum(float(p.get('weight_kg') or 0) for p in pls)
    if total>float(c.get('payload') or 0)+1e-6: issues.append(f'本柜总重 {total:.1f} kg 超过载荷上限 {float(c.get("payload") or 0):.1f} kg')
    is_ot=bool(c.get('open_top')) or 'OT' in str(c.get('code') or '')
    for i,a in enumerate(pls):
        x,y,z,l,w,h=[float(a.get(k) or 0) for k in ('x','y','z','l','w','h')]
        hbad=(z>1e-6 and z+h>float(c['inner_h'])+1e-6) if is_ot else (z+h>float(c['inner_h'])+1e-6)
        if min(x,y,z)<-1e-6 or x+l>float(c['inner_l'])+1e-6 or y+w>float(c['inner_w'])+1e-6 or hbad:
            issues.append(f'{a.get("name") or "货物"} 越界')
        if _needs_door_check(c) and (w>float(c.get('door_w') or c['inner_w'])+1e-6 or h>float(c.get('door_h') or c['inner_h'])+1e-6):
            issues.append(f'{a.get("name") or "货物"} 超过柜门通过尺寸')
        others=[q for j,q in enumerate(pls) if j!=i]
        if not _support_ok(a,others,shared_analysis): issues.append(f'{a.get("name") or "货物"} 支撑不足/顶载不允许')
        for b in pls[i+1:]:
            if boxes_overlap(a,b): issues.append(f'{a.get("name") or "货物"} 与 {b.get("name") or "货物"} 碰撞')
    floor_a=floor_load_audit(pls,c,shared_analysis)
    issues.extend(floor_a.get('hard_issues') or [])
    issues.extend(top_load_audit(pls,shared_analysis).get('hard_issues') or [])
    return list(dict.fromkeys(issues))

def _audit_manual_container_box(box):
    c=_container_by_code(str(box.get('container_code') or '')) or {'code':box.get('container_code'),'inner_l':box.get('inner_l'),'inner_w':box.get('inner_w'),'inner_h':box.get('inner_h'),'door_w':box.get('inner_w'),'door_h':box.get('inner_h'),'payload':box.get('payload')}
    st={'container':c,'placements':box.get('placements') or [],'weight':sum(float(p.get('weight_kg') or 0) for p in (box.get('placements') or []))}
    risk=_container_state_audit(st); issues=_container_physical_issues(box)
    risk=dict(risk); risk['physical_issues']=issues
    if issues:
        risk['status']='不通过'; risk['note']='；'.join(issues+([risk.get('note')] if risk.get('note') else []))
    return risk

def _container_box_signature(box):
    immutable={'asu_profile':_asu_profile(),'container_code':box.get('container_code'),'inner_l':box.get('inner_l'),'inner_w':box.get('inner_w'),'inner_h':box.get('inner_h'),'payload':box.get('payload'),
               'placements':[{'seq':p.get('seq'),'name':p.get('name'),'l':p.get('l'),'w':p.get('w'),'h':p.get('h'),'weight_kg':p.get('weight_kg'),'type':p.get('type'),'cg_local_x':p.get('cg_local_x'),'cg_local_y':p.get('cg_local_y'),'cg_local_z':p.get('cg_local_z'),'cg_confirmed':p.get('cg_confirmed'),'cg_source_type':p.get('cg_source_type'),'max_top_load_kg':p.get('max_top_load_kg'),'top_load_verified':p.get('top_load_verified'),'top_load_source_type':p.get('top_load_source_type'),'lashing_required':p.get('lashing_required'),'lashing_points':p.get('lashing_points'),'lashing_confirmed':p.get('lashing_confirmed'),'contact_area_mode':p.get('contact_area_mode'),'contact_area_source_type':p.get('contact_area_source_type'),'skid_count':p.get('skid_count'),'skid_width_mm':p.get('skid_width_mm'),'skid_contact_length_mm':p.get('skid_contact_length_mm'),'custom_contact_area_m2':p.get('custom_contact_area_m2')} for p in (box.get('placements') or [])]}
    immutable['bag_fill_policy']=_bag_fill_policy()
    if FLEET.config()['enabled']:immutable['selected_fleet']=FLEET.config()
    if box.get('flexible_fill'):immutable['flexible_fill']=box['flexible_fill']
    if box.get('vehicle_type')=='truck':immutable['truck_parameters']=box.get('truck_parameters')
    raw=json.dumps(immutable,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()

def _apply_saved_container_layouts(rec):
    """Overlay valid saved manual layouts and surface every invalidated saved layout.

    V5.1 never silently falls back from an invalid saved manual layout to the automatic layout.
    The automatic geometry remains visible for recovery, but a warning is attached and formal export is blocked.
    """
    rec=copy.deepcopy(rec); plans=rec.get('plans') or {}
    if not plans: return rec
    con=db(); rows=con.execute('SELECT * FROM container_manual_layouts').fetchall(); draft_rows=con.execute('SELECT * FROM container_layout_drafts WHERE dirty=1').fetchall(); con.close()
    saved={(str(r['scheme_code']),int(r['container_index'])):dict(r) for r in rows}
    drafts={(str(r['scheme_code']),int(r['container_index'])):dict(r) for r in draft_rows}
    invalid_all=[]
    for scheme,plan in plans.items():
        plan_invalid=[]
        for bi,box in enumerate(plan.get('containers') or []):
            drow=drafts.get((str(scheme),int(box.get('index') or 0)))
            if drow:
                box['server_dirty_draft']=True; box['server_dirty_since']=drow.get('updated_at')
            row=saved.get((str(scheme),int(box.get('index') or 0)))
            if not row: continue
            reason=''
            if row.get('base_signature')!=_container_box_signature(box):
                reason='已保存手调方案与当前BOM/自动方案签名不一致'
            positions=[]
            if not reason:
                try: positions=json.loads(row.get('positions_json') or '[]')
                except Exception: reason='已保存手调坐标数据损坏/无法解析'
            if not reason:
                try: pos={(int(x.get('seq')),str(x.get('name'))):(float(x.get('x')),float(x.get('y')),float(x.get('z'))) for x in positions if x.get('seq') is not None}
                except Exception: pos={}; reason='已保存手调坐标格式无效'
            if not reason:
                newbox=copy.deepcopy(box); ok=True
                for q in newbox.get('placements') or []:
                    xyz=pos.get((int(q.get('seq')),str(q.get('name'))))
                    if xyz is None: ok=False; break
                    q['x'],q['y'],q['z']=xyz
                if not ok: reason='已保存手调方案货物清单不完整'
            if not reason:
                audit=_audit_manual_container_box(newbox)
                if audit.get('status')=='不通过': reason='已保存手调方案重新规划校核不通过：'+str(audit.get('note') or '')
            if reason:
                info={'scheme':scheme,'container_index':int(box.get('index') or 0),'container_code':box.get('container_code'),'reason':reason,'saved_at':row.get('saved_at')}
                plan_invalid.append(info); invalid_all.append(info)
                box['manual_saved_invalid']=True; box['manual_invalid_reason']=reason; box['manual_saved_at']=row.get('saved_at')
                continue
            newbox['engineering_audit']=audit; newbox['manual_saved']=True; newbox['manual_saved_at']=row.get('saved_at')
            plan['containers'][bi]=newbox
        plan['invalid_manual_layouts']=plan_invalid
        plan['manual_layout_invalid_count']=len(plan_invalid)
        plan['engineering_review_count']=sum(1 for b in plan.get('containers',[]) if (b.get('engineering_audit') or {}).get('status')!='通过')
        plan['engineering_high_risk_count']=sum(1 for b in plan.get('containers',[]) if (b.get('engineering_audit') or {}).get('status')=='高风险')
    rec['invalid_manual_layouts']=invalid_all
    for o in rec.get('options') or []:
        pp=plans.get(o.get('code'))
        if pp:
            o['engineering_review_count']=pp.get('engineering_review_count',o.get('engineering_review_count',0))
            o['engineering_high_risk_count']=pp.get('engineering_high_risk_count',o.get('engineering_high_risk_count',0))
            o['manual_layout_invalid_count']=pp.get('manual_layout_invalid_count',0)
    return rec

def _search_rejection_summary(states):
    totals={}
    for st in states or []:
        for code,count in (st.get('reject_stats') or {}).items():
            totals[code]=totals.get(code,0)+int(count or 0)
    rows=[{'code':code,'label':_REJECT_REASON_LABELS.get(code,code),'count':count} for code,count in totals.items() if count]
    rows.sort(key=lambda r:(-r['count'],r['label']))
    return rows

def _decorate_plan(states, plan_id, label):
    colors=["#2563eb","#059669","#d97706","#dc2626","#7c3aed","#0891b2","#ea580c","#65a30d","#db2777","#0f766e","#475569","#9333ea"]
    seq=1; boxes=[]
    for idx,st in enumerate(states,1):
        st=_center_container_state(st)
        c=st["container"]
        used_vol=0.0
        placements=[]
        for p in st["placements"]:
            q=dict(p);
            if cylinder_axis(q):q.update(display_shape="upright_cylinder" if cylinder_axis(q)=="z" else "horizontal_cylinder",cylinder_axis=cylinder_axis(q))
            q["seq"]=seq; q["color"]=colors[(seq-1)%len(colors)]; seq+=1
            placements.append(q); used_vol+=q["l"]*q["w"]*q["h"]
        cv=c["inner_l"]*c["inner_w"]*c["inner_h"]
        audit=_container_state_audit(st)
        boxes.append({"index":idx,"container_code":c["code"],"inner_l":c["inner_l"],"inner_w":c["inner_w"],"inner_h":c["inner_h"],"payload":c["payload"],
                      "placements":placements,"volume_utilization":round(used_vol/cv*100,1),"weight_utilization":round(st["weight"]/c["payload"]*100,1),"total_weight":round(st["weight"],1),
                      "engineering_audit":audit,"floor_load_kg_m2":c.get("floor_load_kg_m2"),**({k:c.get(k) for k in ("vehicle_type","body_type","truck_parameters","clearance_mm","label")} if c.get("vehicle_type")=="truck" else {})})
    reviews=sum(1 for b in boxes if b['engineering_audit']['status']!='通过')
    highrisks=sum(1 for b in boxes if b['engineering_audit']['status']=='高风险')
    fails=sum(1 for b in boxes if b['engineering_audit']['status']=='不通过')
    return {"code":plan_id,"label":label,"count":len(boxes),"package_count":sum(len(x["placements"]) for x in boxes),"engineering_review_count":reviews,"engineering_high_risk_count":highrisks,"engineering_fail_count":fails,"search_rejection_summary":_search_rejection_summary(states),"containers":boxes}

def fits_container(pkg,c):
    if pkg.get('transport_excluded'):return False
    margin=_asu_margin(c)
    for dims in orientations(pkg["length_mm"],pkg["width_mm"],pkg["height_mm"],pkg.get("rotatable",True)):
        l,w,h=dims
        if l<=c["inner_l"]-2*margin and w<=c["inner_w"]-2*margin and pkg["weight_kg"]<=c["payload"]:
            if c.get('floor_load_kg_m2') not in (None,''):
                try:
                    probe=dict(pkg); probe.update({'l':l,'w':w,'z':0})
                    pr=floor_contact_pressure_kg_m2(probe,0.0)
                    if pr is not None and pr>float(c['floor_load_kg_m2'])+1e-6: continue
                except Exception: continue
            if c.get('vehicle_type')=='truck':
                if h>c['inner_h']:continue
                if not _needs_door_check(c) or (w<=c['door_w'] and h<=c['door_h']):return True
                continue
            if bool(c.get('open_top')) or 'OT' in str(c.get('code','')):
                return True
            if h<=c["inner_h"] and w<=c["door_w"] and h<=c["door_h"]:
                return True
    return False

def get_packages(items,crates):
    packages=[]
    crate_map={c["id"]:c for c in crates}
    con=db(); audit_rows=con.execute('SELECT crate_id,cg_x_pct,cg_y_pct,cg_z_pct,audit_status FROM crate_audits').fetchall(); con.close()
    audit_by={int(r['crate_id']):dict(r) for r in audit_rows}
    # Crated cargo: one shipping package per physical crate. Local crate CG is propagated to container-level CG.
    for c in crates:
        citems=[i for i in items if i.get("crate_id")==c["id"]]
        if not citems: continue
        st=crate_stats(c,items); ca=audit_by.get(int(c['id']),{})
        planning_top_load,struct_cls=_crate_top_load_allowance(st["cargo_weight"],c["length_mm"],c["width_mm"],c["height_mm"])
        top_confirmed=bool(c.get('top_load_confirmed')) and c.get('max_top_load_kg') not in (None,'') and _physical_source_verified(c.get('top_load_source_type'))
        top_load=max(0.0,float(c.get('max_top_load_kg') or 0)) if top_confirmed else None
        cx=float(ca.get('cg_x_pct') if ca.get('cg_x_pct') is not None else 50)/100.0*float(c['length_mm'])
        cy=float(ca.get('cg_y_pct') if ca.get('cg_y_pct') is not None else 50)/100.0*float(c['width_mm'])
        cz=float(ca.get('cg_z_pct') if ca.get('cg_z_pct') is not None else 50)/100.0*float(c['height_mm'])
        if _is_prepacked(c):
            ii=citems[0]
            cx=_safe_local_cg({'cg_local_x':ii.get('cg_x_mm')},'x',c['length_mm'])
            cy=_safe_local_cg({'cg_local_y':ii.get('cg_y_mm')},'y',c['width_mm'])
            cz=_safe_local_cg({'cg_local_z':ii.get('cg_z_mm')},'z',c['height_mm'])
            ca={'audit_status':'需复核'}
        packages.append({"asu_key":"crate:"+str(c["id"]),"name":c["name"],"length_mm":c["length_mm"],"width_mm":c["width_mm"],"height_mm":c["height_mm"],
                         "weight_kg":st["gross_weight"],"type":"木箱","rotatable":True,"stackable":bool(top_confirmed and top_load>0),
                         "max_top_load_kg":top_load,"top_load_verified":top_confirmed,"top_load_source_type":c.get("top_load_source_type") or "UNCONFIRMED","structure_class":struct_cls,"planning_top_load_kg":planning_top_load,
                         "cg_local_x":cx,"cg_local_y":cy,"cg_local_z":cz,"cg_confirmed":all(bool(ii.get("cg_confirmed")) and _physical_source_verified(ii.get("cg_source_type")) for ii in citems),"cg_source_type":"DERIVED_CRATE" if all(bool(ii.get("cg_confirmed")) and _physical_source_verified(ii.get("cg_source_type")) for ii in citems) else "UNCONFIRMED","crate_audit_status":ca.get('audit_status') or '未审计',
                         "lashing_required":bool(c.get("lashing_required")),"lashing_points":int(c.get("lashing_points") or 0),"lashing_confirmed":bool(c.get("lashing_confirmed")),"handling_note":c.get("handling_note") or "",
                         "contact_area_mode":c.get("contact_area_mode") or "UNSPECIFIED","contact_area_source_type":c.get("contact_area_source_type") or "UNCONFIRMED","skid_count":int(c.get("skid_count") or 0),"skid_width_mm":c.get("skid_width_mm"),"skid_contact_length_mm":c.get("skid_contact_length_mm"),"custom_contact_area_m2":c.get("custom_contact_area_m2")})
    # Bare cargo bypasses crates.
    for i in items:
        if i["package_type"]!="木箱":
            for n in range(i["qty"]):
                packages.append({"asu_key":"item:"+str(i["id"]),"name":i["name"]+(f"-{n+1}" if i["qty"]>1 else ""),"length_mm":i["length_mm"],"width_mm":i["width_mm"],"height_mm":i["height_mm"],
                                 "weight_kg":i["weight_kg"],"type":i["package_type"],"transport_excluded":"SHIPMENT_NON_CONTAINER" in str(i.get("size_note") or ""),"void_fill":"PERLITE_VOID_FILL" in str(i.get("size_note") or ""),"flexible_unit_volume_m3":0.125 if "PERLITE_FLEXIBLE_VOLUME" in str(i.get("size_note") or "") else None,"display_shape":"upright_cylinder" if "圆桶立放" in str(i.get("size_note") or "") else "horizontal_cylinder" if "圆柱卧放" in str(i.get("size_note") or "") else None,"rotatable":bool(i.get("rotatable",1)),"stackable":bool(i.get("stackable",1)),
                                 "cg_local_x":i.get("cg_x_mm"),"cg_local_y":i.get("cg_y_mm"),"cg_local_z":i.get("cg_z_mm"),"cg_confirmed":bool(i.get("cg_confirmed")) and _physical_source_verified(i.get("cg_source_type")),"cg_source_type":i.get("cg_source_type") or "UNCONFIRMED",
                                 "contact_area_mode":"UNSPECIFIED","contact_area_source_type":"UNCONFIRMED","skid_count":0,"skid_width_mm":None,"skid_contact_length_mm":None,"custom_contact_area_m2":None})
    packages=ASU.augment(packages)
    if TRUCKS.mode()=="truck":
        for pkg in packages:pkg["lashing_required"]=True
    return packages

def _ordered_variants(packages):
    """Deterministic multi-start orderings for whole-container repacking.
    Different orderings can materially change 3D bin-packing results, so V3.2 tries several
    instead of accepting the first feasible layout.
    """
    variants=[]
    variants.append(_ordered_packages(packages))
    variants.append(sorted(packages,key=lambda p:p["length_mm"]*p["width_mm"]*p["height_mm"],reverse=True))
    variants.append(sorted(packages,key=lambda p:(max(p["length_mm"],p["width_mm"],p["height_mm"]),p["weight_kg"]),reverse=True))
    variants.append(sorted(packages,key=lambda p:(p["height_mm"],p["weight_kg"],p["length_mm"]*p["width_mm"]),reverse=True))
    variants.append(sorted(packages,key=lambda p:(p["weight_kg"],p["length_mm"]*p["width_mm"]*p["height_mm"]),reverse=True))
    variants.append(sorted(packages,key=lambda p:(not bool(p.get("stackable",True)),p["length_mm"],p["width_mm"],p["height_mm"]),reverse=True))
    # de-duplicate by object/name order
    out=[]; seen=set()
    for v in variants:
        v=sorted(v,key=lambda p:bool(p.get("void_fill")))
        key=tuple((p.get("name"),p.get("weight_kg"),p.get("length_mm"),p.get("width_mm"),p.get("height_mm")) for p in v)
        if key not in seen:
            seen.add(key); out.append(v)
    return out

def _audit_severity_counts(audits):
    return (
        sum(1 for a in audits if a.get("status")=="不通过"),
        sum(1 for a in audits if a.get("status")=="高风险"),
        sum(1 for a in audits if a.get("status")=="需复核"),
    )

def pack_specific_bins_multistart(packages, bin_defs):
    """Try several cargo orderings and keep the most compact feasible state set."""
    best=None
    for ordered in _ordered_variants(packages):
        states=[_new_bin_state(c) for c in bin_defs]
        ok=True
        for pkg in ordered:
            if not _place_one(pkg,states):
                ok=False; break
        if not ok: continue
        states=[_center_container_state(x) for x in states if x["placements"]]
        used=sum(len(x["placements"]) for x in states)
        if used!=len(packages): continue
        max_util=max((_state_used_volume(x)/(x["container"]["inner_l"]*x["container"]["inner_w"]*x["container"]["inner_h"]) for x in states),default=0)
        min_util=min((_state_used_volume(x)/(x["container"]["inner_l"]*x["container"]["inner_w"]*x["container"]["inner_h"]) for x in states),default=0)
        audits=[_container_state_audit(x) for x in states]
        hard_count,high_count,review_count=_audit_severity_counts(audits)
        cg_penalty=sum(max(float(a.get("offset_x_pct",0)),float(a.get("offset_y_pct",0))) for a in audits)
        # V5.7: never collapse hard-fail/high-risk/review into one bucket.  Equal-count
        # layouts are ranked by severity first, then CG and utilisation.
        score=(len(states),hard_count,high_count,review_count,round(cg_penalty,3),-min_util,-max_util)
        if best is None or score<best[0]: best=(score,states)
    return best[1] if best else None

def _homogeneous_lower_bound(packages,c):
    total_vol=sum(p["length_mm"]*p["width_mm"]*p["height_mm"] for p in packages)
    total_w=sum(p["weight_kg"] for p in packages)
    cv=c["inner_l"]*c["inner_w"]*c["inner_h"]
    return max(1,math.ceil(total_vol/max(cv,1)),math.ceil(total_w/max(c["payload"],1)))

def _global_compress_same_type(packages,c,baseline_states):
    """Attempt to eliminate the emptiest container(s) by globally repacking ALL cargo.
    Returns best states plus an audit record explaining whether a container was actually removed.
    """
    base_n=len(baseline_states)
    lb=_homogeneous_lower_bound(packages,c)
    best=baseline_states
    attempts=[]
    # Try one fewer first; if successful, keep going until lower bound or failure after two consecutive misses.
    misses=0
    for n in range(base_n-1,lb-1,-1):
        states=pack_specific_bins_multistart(packages,[c]*n)
        attempts.append({"target":n,"ok":bool(states)})
        if states:
            best=states; misses=0
        else:
            misses+=1
            if misses>=2: break
    saved=base_n-len(best)
    return best,{"baseline_count":base_n,"optimized_count":len(best),"saved_count":saved,"lower_bound":lb,"attempts":attempts}

def _targeted_mixed_compression(packages,target_count):
    """Try a bounded set of mixed-type combinations at a desired total container count.
    This is intentionally targeted to avoid combinatorial explosion on large projects.
    """
    if target_count<1: return []
    from itertools import combinations_with_replacement
    # Standard dry boxes first. Add OT only if at least one unit cannot fit any dry box.
    containers=_active_containers()
    dry=[c for c in containers if c["code"] in {"40HQ","40GP","20GP"}]
    needs_ot=any(not any(fits_container(p,c) for c in dry) and any(fits_container(p,c) for c in containers if "OT" in c["code"]) for p in packages)
    types=dry+([c for c in containers if c["code"]=="40OT"] if needs_ot else [])
    plans=[]; checked=0
    for combo in combinations_with_replacement(types,target_count):
        checked+=1
        if checked>220: break
        if any(not any(fits_container(p,c) for c in combo) for p in packages): continue
        if sum(c["payload"] for c in combo)<sum(p["weight_kg"] for p in packages): continue
        if sum(c["inner_l"]*c["inner_w"]*c["inner_h"] for c in combo)<sum(p["length_mm"]*p["width_mm"]*p["height_mm"] for p in packages): continue
        defs=sorted(combo,key=lambda c:(c["inner_l"]*c["inner_w"]*c["inner_h"],c["payload"]),reverse=True)
        states=pack_specific_bins_multistart(packages,defs)
        if not states or len(states)>target_count: continue
        codes=[x["container"]["code"] for x in states]
        counts={k:codes.count(k) for k in sorted(set(codes))}
        label=" + ".join(f"{k}×{v}" for k,v in counts.items())
        pid="CMP_"+"_".join(f"{k}x{v}" for k,v in counts.items())
        plans.append(_decorate_plan(states,pid,label))
    return plans

def _homogeneous_plan(packages,c):
    if any(not fits_container(p,c) for p in packages): return None
    states=[]
    for pkg in _ordered_packages(packages):
        if not states or not _place_one(pkg,states):
            st=_new_bin_state(c); states.append(st)
            if not _place_one(pkg,[st]): return None
    optimized,audit=_global_compress_same_type(packages,c,states)
    plan=_decorate_plan(optimized,c["code"],c["code"])
    plan["compression_audit"]=audit
    return plan

def _candidate_mixed_plans(packages, max_total=4):
    # Search small mixed combinations. This is intentionally bounded for responsiveness.
    from itertools import combinations_with_replacement
    plans=[]
    types=_active_containers()
    for total in range(1,max_total+1):
        for combo in combinations_with_replacement(types,total):
            if any(not any(fits_container(p,c) for c in combo) for p in packages): continue
            if sum(c["payload"] for c in combo) < sum(p["weight_kg"] for p in packages): continue
            if sum(c["inner_l"]*c["inner_w"]*c["inner_h"] for c in combo) < sum(p["length_mm"]*p["width_mm"]*p["height_mm"] for p in packages): continue
            # Larger containers first gives the heuristic a stable placement order.
            defs=sorted(combo,key=lambda c:(c["inner_l"]*c["inner_w"]*c["inner_h"],c["payload"]),reverse=True)
            states=pack_specific_bins(packages,defs)
            if not states: continue
            codes=[s["container"]["code"] for s in states]
            counts={k:codes.count(k) for k in sorted(set(codes))}
            label=" + ".join(f"{k}×{v}" for k,v in counts.items())
            pid="MIX_"+"_".join(f"{k}x{v}" for k,v in counts.items())
            plans.append(_decorate_plan(states,pid,label))
        if plans: break
    # de-duplicate by label
    out={}
    for p in plans: out[p["label"]]=p
    return list(out.values())

def _plan_metrics(plan):
    total_used=sum(b.get("planning_occupied_volume_mm3",sum(p["l"]*p["w"]*p["h"] for p in b["placements"])) for b in plan["containers"])
    total_cap=sum(b["inner_l"]*b["inner_w"]*b["inner_h"] for b in plan["containers"])
    total_weight=sum(b["total_weight"] for b in plan["containers"])
    total_payload=sum(b["payload"] for b in plan["containers"])
    special=sum(1 for b in plan['containers'] if bool((_container_by_code(str(b.get('container_code') or '')) or {}).get('open_top')) or 'OT' in str(b.get('container_code') or ''))
    return {"volume_utilization":round(total_used/total_cap*100,1) if total_cap else 0,
            "weight_utilization":round(total_weight/total_payload*100,1) if total_payload else 0,
            "special_count":special,"total_weight":round(total_weight,1)}


def _crate_packaging_cost(crates,costs):
    return round(sum(float(costs.get('crate_fixed',0))+float(costs.get('crate_labor',0))+float(costs.get('crate_m3',0))*(c['length_mm']*c['width_mm']*c['height_mm']/1e9) for c in crates),2)

def _plan_cost(plan,crates,oversize,costs):
    if any(b.get('vehicle_type')=='truck' or str(b.get('container_code','')).startswith('TRUCK_') for b in plan.get('containers',[])):
        return {'freight_cost':None,'crate_cost':_crate_packaging_cost(crates,costs),'oog_cost':None,'total_cost':None,'currency':costs.get('currency','CNY'),'cost_status':'未配置货车运价，未计算运输总成本'}
    freight=sum(float(costs.get(b.get('container_code'),0) or 0) for b in plan.get('containers',[]))
    crate_cost=_crate_packaging_cost(crates,costs)
    oog=len(oversize)*float(costs.get('oog_each',0) or 0)
    return {'freight_cost':round(freight,2),'crate_cost':round(crate_cost,2),'oog_cost':round(oog,2),'total_cost':round(freight+crate_cost+oog,2),'currency':costs.get('currency','CNY')}

def _homogeneous_plan_fast(packages,c):
    """Fast feasible homogeneous plan without global compression search."""
    if any(not p.get("flexible_unit_volume_m3") and not fits_container(p,c) for p in packages): return None
    if any(p.get("flexible_unit_volume_m3") for p in packages):return flexible_fill.pack(sys.modules[__name__],packages,c)
    if bulk_fill.split(packages)[1]:return bulk_fill.pack(sys.modules[__name__],packages,c)
    states=[]
    for pkg in _ordered_packages(packages):
        if not states or not _place_one(pkg,states):
            st=_new_bin_state(c); states.append(st)
            if not _place_one(pkg,[st]): return None
    plan=_decorate_plan(states,c["code"],c["code"])
    plan["compression_audit"]={"baseline_count":len(states),"optimized_count":len(states),"saved_count":0,"fast_mode":True}
    return plan

def _bag_fill_policy():
    policy=_load_bag_top_policy(DB_PATH.parent)
    if policy.get('schema')==2:
        current=_shipment_source_state(fetch_state()[0])
        if current.get('status')!='verified' or current.get('issues') or current.get('fingerprint')!=policy.get('shipment_fingerprint'):return {}
    return policy


def _bag_permission_status(items,crates):
    from app.bag_top_policy import approved
    packages=get_packages(items,crates)
    if not any(q.get('flexible_unit_volume_m3') for q in packages):return {}
    policy=_bag_fill_policy()
    normal=[q for q in packages if not q.get('flexible_unit_volume_m3') and any(fits_container(q,c) for c in _active_containers())]
    count=sum(approved(q,policy) for q in normal)
    return dict(approved=count,total=len(normal),stale=count==0,
                note='设备顶部放袋许可已对应当前清单；可用空间还须通过柜界、已知顶载及分阶段送袋检查。' if count else '当前设备未匹配到顶部放袋许可，顶部空间未计入珠光砂；请核对本批设备的放袋范围。')


def _state_fingerprint(items,crates):
    payload={
        'items':[(i.get('id'),i.get('name'),i.get('size_note'),i.get('qty'),i.get('crate_id'),float(i.get('length_mm',0)),float(i.get('width_mm',0)),float(i.get('height_mm',0)),float(i.get('weight_kg',0)),i.get('package_type'),i.get('stackable'),i.get('rotatable'),i.get('size_grade'),i.get('size_source'),i.get('weight_grade'),i.get('weight_source'),i.get('cg_x_mm'),i.get('cg_y_mm'),i.get('cg_z_mm'),i.get('cg_source_type'),i.get('cg_confirmed'),i.get('cg_evidence_id')) for i in items],
        'crates':[(c.get('id'),c.get('name'),float(c.get('length_mm',0)),float(c.get('width_mm',0)),float(c.get('height_mm',0)),float(c.get('tare_weight_kg',0)),c.get('actual_gross_weight_kg'),c.get('gross_weight_confirmed'),c.get('size_confirmed'),c.get('size_source'),c.get('max_top_load_kg'),c.get('top_load_confirmed'),c.get('top_load_source_type'),c.get('lashing_required'),c.get('lashing_points'),c.get('lashing_confirmed'),c.get('contact_area_mode'),c.get('contact_area_source_type'),c.get('skid_count'),c.get('skid_width_mm'),c.get('skid_contact_length_mm'),c.get('custom_contact_area_m2'),c.get('top_load_evidence_id'),c.get('contact_area_evidence_id'),c.get('lashing_evidence_id')) for c in crates],
        'selected_fleet':FLEET.config(),'asu_profile':_asu_profile(),'asu_records':ASU.records() if _asu_profile()['enabled'] else {},
        'crate_settings':_crate_weight_settings_load(),'bag_fill_policy':_bag_fill_policy(),
        'containers':_active_containers(),
        'cost':_cost_settings_load()
    }
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()

_PERF_CACHE={"fast":OrderedDict(),"deep":OrderedDict()}
_PERF_CACHE_LIMITS={"fast":64,"deep":16}

def _perf_cache_get(bucket,key):
    if key not in bucket: return None
    value=bucket.pop(key); bucket[key]=value
    return value

def _perf_cache_set(kind,key,value):
    bucket=_PERF_CACHE[kind]
    if key in bucket: bucket.pop(key)
    bucket[key]=value
    limit=int(_PERF_CACHE_LIMITS.get(kind,32))
    while len(bucket)>limit: bucket.popitem(last=False)


def recommend_containers(items,crates,deep=False):
    # Presentation metadata is attached to a copy, outside solver/cache identity.
    from app.cargo_labels import annotate_cargo_names
    return annotate_cargo_names(_recommend_containers_core(items,crates,deep),items,crates)


def _recommend_containers_core(items,crates,deep=False):
    packages=get_packages(items,crates)
    try:
        containers=_active_containers()
        if FLEET.config()["enabled"]:return FLEET.plan(packages,containers,items,crates,deep)
    except Exception as e:
        return {"packages":packages,"options":[],"plans":{},"oversize":[],"container_config_error":str(e)}
    if any(p.get("flexible_unit_volume_m3") for p in packages) and TRUCKS.mode()=="truck":return {"packages":packages,"options":[],"plans":{},"oversize":[],"container_config_error":"柔性袋装体积分配目前仅支持集装箱；货车须另核实车厢、支撑和道路约束。"}
    if not packages: return {"packages":[],"options":[],"plans":{},"oversize":[],"container_config_error":""}
    # Keep clearly OOG / non-containerizable cargo from blocking the plan for all normal cargo.
    # These units are reported separately for FR / breakbulk / special road transport review.
    oversize=[p for p in packages if not any(fits_container(p,c) for c in containers)]
    normal=[p for p in packages if any(fits_container(p,c) for c in containers)]
    if not normal:
        return {"packages":packages,"options":[],"plans":{},"oversize":oversize}
    fp=_state_fingerprint(items,crates)
    cache_kind="deep" if deep else "fast"
    cache_bucket=_PERF_CACHE[cache_kind]
    cached=_perf_cache_get(cache_bucket,fp)
    if cached is not None:
        return cached
    candidates=[]
    for c in containers:
        p=(_homogeneous_plan_fast(normal,c) if any(x.get("flexible_unit_volume_m3") for x in normal) else _homogeneous_plan(normal,c) if deep else _homogeneous_plan_fast(normal,c))
        if p: candidates.append(p)
    if deep and not any(x.get("flexible_unit_volume_m3") for x in normal):
        candidates.extend(_candidate_mixed_plans(normal,max_total=4))
        feasible_counts=[p.get("count",999) for p in candidates if p]
        if feasible_counts:
            target=max(1,min(feasible_counts)-1)
            candidates.extend(_targeted_mixed_compression(normal,target))
    if any(x.get('flexible_unit_volume_m3') for x in normal):
        from app.mixed_flexible import candidates as mixed_bag_candidates
        candidates.extend(mixed_bag_candidates(sys.modules[__name__],normal,containers))
    unique={}; costs=_cost_settings_load(); cost_enabled=bool(costs.get('enabled')) and TRUCKS.mode()!='truck'
    for p in candidates:
        m=_plan_metrics(p); p.update(m); p.update(_plan_cost(p,crates,oversize,costs))
        p["decision_key"]=(p.get("engineering_fail_count",0),p.get("engineering_high_risk_count",0),p["total_cost"],p["count"],p.get("engineering_review_count",0),p["special_count"],-p["volume_utilization"]) if cost_enabled else (p.get("engineering_fail_count",0),p.get("engineering_high_risk_count",0),p["count"],p.get("engineering_review_count",0),p["special_count"],-p["volume_utilization"])
        unique[p["label"]]=p
    plans=sorted(unique.values(),key=lambda p:p["decision_key"])
    if not plans: return {"packages":packages,"options":[],"plans":{},"oversize":oversize}
    for idx,p in enumerate(plans):
        utils=[float(b.get("volume_utilization",0)) for b in p.get("containers",[])]
        low=min(utils) if utils else 0
        ca=p.get("compression_audit") or {}
        compress_txt=""
        if ca:
            if ca.get("saved_count",0)>0:
                compress_txt=f" 全局重排已由 {ca.get('baseline_count')} 柜压缩为 {ca.get('optimized_count')} 柜。"
            elif ca.get("baseline_count"):
                compress_txt=f" 已尝试压到 {max(1,ca.get('baseline_count')-1)} 柜，但当前几何/重量/支撑约束下未找到可行解。"
        if idx==0:
            reason=f"可常规集装箱化货物：当前搜索找到的最少柜数为 {p['count']}；高风险柜 {p.get('engineering_high_risk_count',0)} 个；工程复核柜 {p.get('engineering_review_count',0)} 个；特种箱 {p['special_count']} 个；总体积利用率约 {p['volume_utilization']}%；最空单柜约 {low:.1f}%。"+compress_txt
        else:
            reason=f"备选：{p['count']} 柜，高风险柜 {p.get('engineering_high_risk_count',0)} 个，工程复核柜 {p.get('engineering_review_count',0)} 个，特种箱 {p['special_count']} 个，体积利用率约 {p['volume_utilization']}%；最空单柜约 {low:.1f}%。"+compress_txt
        if p.get("flexible_volume_only"):reason=f"柔性袋装体积分配估算：{p['count']} 柜，体积利用率约 {p['volume_utilization']}%；按原表每立方8包；按装载阶段检查落地区和已确认顶载的木箱顶区，窄缝/未知顶载排除。800mm宽/1800mm高作业路径为规划假设，袋装堆高承压、搬运方法、门口挡护及整柜重心待确认，不能视为精确排布或最少柜数证明。"
        if p.get('void_fill_audit',{}).get('confirmation_source')=='USER_PROJECT_DECLARATION':
            joint=p.get('bag_joint_optimization',{})
            reason=f"按本项目用户确认允许设备顶部放珠光砂；当前联合候选 {joint.get('candidates_evaluated',1)} 个，方案 {p['count']} 柜，设备柜填入 {p['void_fill_audit']['bags_in_existing_voids']} 包。设备固定后按步骤递袋/辅助送袋至顶部，再装后续设备；送袋距离、净空及作业面见预览步骤。每立方8包，体积与重量守恒；仍为规划估算，不保证最少柜数。"
        elif p.get("void_fill_audit") and not p.get("flexible_volume_only"):reason+=" 袋装填隙优先，新增柜使用相同输入的校核布局；未确认袋上叠放，不假定压缩，柜数为当前保守方案。"
        p["reason"]=reason + ((f" 估算总成本约 {p['total_cost']:.0f} {p['currency']}（运费 {p['freight_cost']:.0f} + 木箱 {p['crate_cost']:.0f} + OOG {p['oog_cost']:.0f}）。") if cost_enabled else '')
        p["lowest_bin_utilization"]=round(low,1)
        p["compression_status"]="柔性体积估算" if p.get("flexible_volume_only") else ("已压柜" if ca.get("saved_count",0)>0 else ("已尝试压柜" if ca else "方案比较"))
    result={"packages":packages,"options":plans[:6],"plans":{p["code"]:p for p in plans[:6]},"oversize":oversize,"deep_mode":bool(deep)}
    _perf_cache_set(cache_kind,fp,result)
    return result

def _ceil50(v):
    return int(math.ceil(float(v)/50.0)*50)

def _estimate_tare(l,w,h,kg_per_m3,cargo_weight=0):
    # V4.5: structural material take-off is the default. Legacy volume coefficient remains optional.
    s=_crate_weight_settings_load()
    if str(s.get('model','structural'))=='legacy' and not _asu_profile()['enabled']:
        return round(max(0.0,(float(l)*float(w)*float(h)/1e9)*float(kg_per_m3)),1)
    return float(_crate_structure_tare(l,w,h,cargo_weight)['total_kg'])


def _fitting_profile(item):
    """Compact packing profile.
    User rule:
    - 5083 aluminium: small anti-friction/film allowance only (10 mm default).
    - all other materials: no extra protective spacing; pack tightly.
    """
    name=str(item.get("name") or "")
    up=name.upper()
    family_override=item.get("_merge_family_override") if isinstance(item,dict) else None
    material_text=' | '.join([name,str(item.get('weight_source') or ''),str(item.get('weight_note') or '')]).upper()
    is5083='5083' in material_text
    family=str(family_override) if family_override else ("AL5083" if is5083 else ("SS" if up.startswith("SS-") or "不锈钢" in name else ("CS" if up.startswith("CS-") or "碳钢" in name else "GEN")))
    dims=sorted([float(item["length_mm"]),float(item["width_mm"]),float(item["height_mm"])], reverse=True)
    L,c1,c2=dims[0],dims[1],dims[2]
    max_cross=max(c1,c2); slender_ratio=L/max(max_cross,1.0)
    pipe_like=bool(re.search(r"直管|短节|管段|管道|无缝管|焊管|直缝|长料",name)) or (L>=2500 and max_cross<=900 and slender_ratio>=4.0)
    cat="管道" if pipe_like else "其他"
    if not pipe_like:
        for k in ("三通","弯头","异径","管帽"):
            if k in name: cat=k; break
    dns=[float(x) for x in re.findall(r"DN\s*(\d+(?:\.\d+)?)",name,re.I)]
    dn=max(dns) if dns else 0.0
    wt=float(item["weight_kg"])
    clearance=10.0 if is5083 else 0.0
    if pipe_like:
        size_class="L" if L>=4000 or wt>=80 else "M"; fill_ratio=0.94; force_floor=True
    elif dn>=500 or L>=1000 or wt>=120:
        size_class="L"; fill_ratio=0.90 if is5083 else 0.94; force_floor=True
    elif dn>=250 or L>=500 or wt>=25:
        size_class="M"; fill_ratio=0.92 if is5083 else 0.95; force_floor=False
    else:
        size_class="S"; fill_ratio=0.94 if is5083 else 0.97; force_floor=False
    return {"material_family":family,"category":cat,"dn":dn,"size_class":size_class,
            "clearance":clearance,"fill_ratio":fill_ratio,"force_floor":force_floor,
            "pipe_like":pipe_like,"slender_ratio":round(slender_ratio,2),"is5083":is5083}

def _utilization_target_for_class(size_class,target_min=60,target_max=80):
    hi=max(float(target_min),float(target_max))/100.0
    if size_class=="L": return min(hi,0.68)
    if size_class=="M": return min(hi,0.78)
    return min(hi,0.85)

def _crate_utilization_metrics(crate_defs,target_min=60,target_max=80):
    vals=[]
    for cd in crate_defs:
        a=cd.get("audit") or {}
        u=float(a.get("actual_util") or 0)
        vals.append(u)
    if not vals:
        return {"average":0.0,"low_count":0,"high_count":0,"good_count":0,"penalty":0.0,"min":0.0,"max":0.0}
    low=sum(1 for u in vals if u < target_min)
    high=sum(1 for u in vals if u > target_max)
    good=len(vals)-low-high
    penalty=sum(max(0,target_min-u)+max(0,u-target_max)*0.35 for u in vals)
    return {"average":round(sum(vals)/len(vals),1),"low_count":low,"high_count":high,"good_count":good,
            "penalty":round(penalty,2),"min":round(min(vals),1),"max":round(max(vals),1)}

def _packing_unit_from_item(item, n):
    prof=_fitting_profile(item)
    original_dims=[float(item["length_mm"]),float(item["width_mm"]),float(item["height_mm"])]
    cg_confirmed=bool(item.get("cg_confirmed")) and _physical_source_verified(item.get("cg_source_type"))
    original_cg=[item.get("cg_x_mm"),item.get("cg_y_mm"),item.get("cg_z_mm")]
    if prof.get("pipe_like"):
        # Pipe/fitting planning normalizes the longest physical axis to local X.  Real CG coordinates
        # must follow the same axis permutation; otherwise an uploaded off-centre CG would be attached
        # to the wrong axis after normalization.
        order=sorted(range(3),key=lambda idx:original_dims[idx],reverse=True)
        actual_l,actual_w,actual_h=[original_dims[idx] for idx in order]
        mapped_cg=[original_cg[idx] for idx in order] if cg_confirmed else [None,None,None]
    else:
        actual_l,actual_w,actual_h=original_dims
        mapped_cg=original_cg if cg_confirmed else [None,None,None]
    gap=float(prof.get("clearance",0) or 0)
    eff_l=actual_l+gap; eff_w=actual_w+gap; eff_h=actual_h+gap
    return {"name":item["name"],"source_name":item["name"],"source_id":item["id"],
            "length_mm":eff_l,"width_mm":eff_w,"height_mm":eff_h,
            "actual_l":actual_l,"actual_w":actual_w,"actual_h":actual_h,
            "weight_kg":float(item["weight_kg"]),"weight_grade":item.get("weight_grade","C"),"weight_source":item.get("weight_source","未标注"),
            "cg_local_x":mapped_cg[0],"cg_local_y":mapped_cg[1],"cg_local_z":mapped_cg[2],"cg_confirmed":cg_confirmed,"cg_source_type":item.get("cg_source_type") or "UNCONFIRMED",
            "asu_group":ASU.group(item) if _asu_profile()["enabled"] else "","type":"材料","rotatable":bool(item["rotatable"]),
            "stackable":bool(item["stackable"]) and not prof["force_floor"],"source":item,**prof}

def _state_used_volume(st):
    return sum(p["l"]*p["w"]*p["h"] for p in st["placements"])

def _place_with_fill_cap(unit, states, cap_ratio, max_items_per_crate=0, max_skus_per_crate=0):
    vol=unit["length_mm"]*unit["width_mm"]*unit["height_mm"]
    unit_sku=unit.get("source_id") or unit.get("source_name") or unit.get("name")
    for st in states:
        if int(max_items_per_crate or 0)>0 and len(st["placements"])>=int(max_items_per_crate):
            continue
        if int(max_skus_per_crate or 0)>0:
            existing={p.get("source_id") or p.get("source_name") or p.get("name") for p in st["placements"]}
            if unit_sku not in existing and len(existing)>=int(max_skus_per_crate):
                continue
        cap=st["container"]["inner_l"]*st["container"]["inner_w"]*st["container"]["inner_h"]*cap_ratio
        if _state_used_volume(st)+vol > cap+1e-6:
            continue
        if _place_one(unit,[st]):
            return True
    return False

def _crate_group_key(unit):
    # Separate stainless/carbon steel, and keep very large fittings away from small fittings.
    return (unit["material_family"], unit["size_class"])

def _is_long_pipe_unit(unit):
    cat=str(unit.get("category") or unit.get("source_name") or "")
    dims=sorted([float(unit.get("actual_l",unit.get("length_mm",0))), float(unit.get("actual_w",unit.get("width_mm",0))), float(unit.get("actual_h",unit.get("height_mm",0)))], reverse=True)
    if not dims or dims[0] <= 0:
        return False
    L, c1, c2 = dims[0], dims[1], dims[2]
    return ("管道" in cat or "直管" in cat or "短节" in cat or "长料" in cat or bool(unit.get("pipe_like"))) and L>=2500 and max(c1,c2)<=900

def _long_pipe_pack_key(unit):
    dims=sorted([float(unit.get("actual_l",unit.get("length_mm",0))), float(unit.get("actual_w",unit.get("width_mm",0))), float(unit.get("actual_h",unit.get("height_mm",0)))], reverse=True)
    L, c1, c2 = dims[0], dims[1], dims[2]
    return (unit.get("material_family","GEN"), _ceil50(L), _ceil50(c1), _ceil50(c2), unit.get("size_class","L"))

def _long_pipe_policy(mode="standard"):
    mode=str(mode or "standard").lower()
    if mode=="conservative":
        return {"mode":"conservative","gap_small":45.0,"gap_large":60.0,"gross_ratio":0.70,"section_ratio":0.68,"label":"保守"}
    if mode=="aggressive":
        return {"mode":"aggressive","gap_small":10.0,"gap_large":15.0,"gross_ratio":0.95,"section_ratio":0.95,"label":"激进"}
    return {"mode":"standard","gap_small":20.0,"gap_large":30.0,"gross_ratio":0.85,"section_ratio":0.85,"label":"标准"}

def _pack_long_pipe_units(units, max_l,max_w,max_h,max_gross,padding,tare_per_m3, long_pipe_mode="standard", max_pipes_per_crate=0, max_items_per_crate=30):
    crate_defs=[]
    if not units:
        return crate_defs
    groups={}
    for u in units:
        groups.setdefault(_long_pipe_pack_key(u), []).append(u)
    for _gkey, g_units in groups.items():
        ordered=sorted(g_units, key=lambda u:(u.get("actual_l",u["length_mm"]),u["weight_kg"]), reverse=True)
        while ordered:
            ref=ordered[0]
            dims=sorted([float(ref.get("actual_l",ref["length_mm"])), float(ref.get("actual_w",ref["width_mm"])), float(ref.get("actual_h",ref["height_mm"]))], reverse=True)
            piece_l, piece_w, piece_h = dims[0], dims[1], dims[2]
            piece_l=min(piece_l, max_l-2*padding)
            policy=_long_pipe_policy(long_pipe_mode)
            gap=policy["gap_small"] if max(piece_w,piece_h)<=300 else policy["gap_large"]
            usable_w=max(100.0,max_w-2*padding)
            usable_h=max(100.0,max_h-2*padding)
            pitch_w=max(40.0,piece_w+gap)
            pitch_h=max(40.0,piece_h+gap)
            cols=max(1,int((usable_w+gap)//pitch_w))
            rows=max(1,int((usable_h+gap)//pitch_h))
            section_cap=max(1,int(cols*rows*policy["section_ratio"]))
            tare_guess=_estimate_tare(_ceil50(piece_l+2*padding), max(500,_ceil50(min(max_w, piece_w+2*padding))), max(300,_ceil50(min(max_h, piece_h+2*padding))), tare_per_m3)
            effective_gross=max(100.0,max_gross*policy["gross_ratio"])
            by_weight=max(1,int(max(1.0,effective_gross-tare_guess)//max(0.1,float(ref["weight_kg"]))))
            caps=[section_cap,by_weight,len(ordered)]
            if int(max_pipes_per_crate or 0)>0: caps.append(int(max_pipes_per_crate))
            if int(max_items_per_crate or 0)>0: caps.append(int(max_items_per_crate))
            cap=max(1,min(caps))
            chunk=ordered[:cap]
            ordered=ordered[cap:]
            cols_used=min(cols, max(1, math.ceil(math.sqrt(len(chunk)*max(piece_h,1)/max(piece_w,1)))))
            rows_used=max(1, math.ceil(len(chunk)/cols_used))
            placements=[]
            for idx,u in enumerate(chunk,1):
                c=(idx-1)%cols_used; r=(idx-1)//cols_used
                geometry_unchanged=(abs(piece_l-float(u.get('actual_l',piece_l)))<=1e-6 and abs(piece_w-float(u.get('actual_w',piece_w)))<=1e-6 and abs(piece_h-float(u.get('actual_h',piece_h)))<=1e-6)
                placements.append({
                    "source_id":u["source_id"],"source_name":u["source_name"],"weight_kg":u["weight_kg"],
                    "source":u.get("source"),"x":0.0,"y":c*pitch_w,"z":r*pitch_h,"l":piece_l,"w":piece_w,"h":piece_h,
                    "actual_l":piece_l,"actual_w":piece_w,"actual_h":piece_h,
                    "cg_local_x":u.get('cg_local_x') if geometry_unchanged else None,"cg_local_y":u.get('cg_local_y') if geometry_unchanged else None,"cg_local_z":u.get('cg_local_z') if geometry_unchanged else None,
                    "cg_confirmed":bool(u.get('cg_confirmed')) and geometry_unchanged,
                    "material_family":u.get("material_family"),"size_class":u.get("size_class","L"),
                    "category":"管道","fitting_category":"管道"
                })
            L=min(max_l,_ceil50(piece_l+2*padding))
            W=min(max_w,_ceil50(cols_used*pitch_w-gap+2*padding))
            H=min(max_h,_ceil50(rows_used*pitch_h-gap+2*padding))
            cargo_w=sum(float(p.get('weight_kg',0)) for p in placements)
            tare=_estimate_tare(L,W,H,tare_per_m3,cargo_w)
            crate_defs.append({"length_mm":L,"width_mm":W,"height_mm":H,"tare_weight_kg":tare,
                               "placements":placements,"oversize":False,"material_family":ref.get("material_family","GEN"),
                               "size_class":ref.get("size_class","L"),"fill_ratio_limit":policy["section_ratio"],"utilization_optimized":True,
                               "long_pipe_mode":policy["mode"],"long_pipe_mode_label":policy["label"]})
    return crate_defs

def _build_state_from_placements(container, placements):
    st=_new_bin_state(container)
    st["placements"]=[dict(p) for p in placements]
    st["weight"]=sum(float(p.get("weight_kg",0)) for p in placements)
    pts=[(0.0,0.0,0.0)]
    for p in st["placements"]:
        pts.extend([(p["x"]+p["l"],p["y"],p["z"]),(p["x"],p["y"]+p["w"],p["z"]),(p["x"],p["y"],p["z"]+p["h"])])
        if str(container.get("code","")).startswith("WOODBOX"):
            pts.extend(_shape_anchor_points(p))
    st["points"]=_prune_points(pts,st["placements"],container["inner_l"],container["inner_w"],container["inner_h"])
    return st

def _diagnose_next_candidate(cd, candidate, max_l,max_w,max_h,max_gross,padding,tare_per_m3):
    if not candidate:
        return {"next_candidate":"—","can_continue":None,"stop_reason":"同组材料已全部分配；没有下一件待装材料用于容量关闭验证。"}
    name=candidate.get("source_name") or candidate.get("name") or "下一件材料"
    cargo=sum(float(p.get("weight_kg",0)) for p in cd["placements"])
    projected_cargo=cargo+float(candidate.get('weight_kg',0))
    L,W,H=float(cd['length_mm']),float(cd['width_mm']),float(cd['height_mm'])
    c,projected_tare=_crate_container_def(L,W,H,max_gross,padding,tare_per_m3,projected_cargo)
    if projected_cargo + projected_tare > max_gross + 1e-6:
        return {"next_candidate":name,"can_continue":False,"stop_reason":f"加入代表性下一件“{name}”后预计毛重将超过 {max_gross:.0f} kg 上限。"}
    used=sum(float(p["l"])*float(p["w"])*float(p["h"]) for p in cd["placements"])
    cvol=c["inner_l"]*c["inner_w"]*c["inner_h"]
    candvol=float(candidate["length_mm"])*float(candidate["width_mm"])*float(candidate["height_mm"])
    if cvol<=0:
        return {"next_candidate":name,"can_continue":False,"stop_reason":"当前木箱扣除板材、框架、滑木和装箱间隙后已无有效净空间。"}
    if used+candvol > cvol*float(cd.get("fill_ratio_limit",1.0))+1e-6:
        pct=(used+candvol)/cvol*100
        return {"next_candidate":name,"can_continue":False,"stop_reason":f"加入“{name}”后规划占位率约 {pct:.1f}%，超过本组 {float(cd.get('fill_ratio_limit',1))*100:.0f}% 的保守上限。"}
    # Existing coordinates may have been calculated with a slightly different structural class;
    # a full repack is the authoritative feasibility check.
    existing=[]
    for p in cd['placements']:
        existing.append({"name":p.get('source_name') or p.get('name'),"source_name":p.get('source_name'),"source_id":p.get('source_id'),
                         "length_mm":p['l'],"width_mm":p['w'],"height_mm":p['h'],"actual_l":p.get('actual_l',p['l']),"actual_w":p.get('actual_w',p['w']),"actual_h":p.get('actual_h',p['h']),
                         "weight_kg":p.get('weight_kg',0),"rotatable":p.get('rotatable',True),"stackable":p.get('stackable',True),
                         "cg_local_x":p.get('cg_local_x'),"cg_local_y":p.get('cg_local_y'),"cg_local_z":p.get('cg_local_z'),"cg_confirmed":bool(p.get('cg_confirmed')),
                         "category":p.get('category'),"material_family":p.get('material_family'),"size_class":p.get('size_class')})
    probe={"name":name,"source_name":name,"source_id":candidate.get("source_id"),"length_mm":candidate["length_mm"],"width_mm":candidate["width_mm"],"height_mm":candidate["height_mm"],
           "actual_l":candidate.get('actual_l',candidate['length_mm']),"actual_w":candidate.get('actual_w',candidate['width_mm']),"actual_h":candidate.get('actual_h',candidate['height_mm']),
           "weight_kg":candidate["weight_kg"],"rotatable":candidate.get("rotatable",True),"stackable":candidate.get("stackable",True),
           "cg_local_x":candidate.get('cg_local_x'),"cg_local_y":candidate.get('cg_local_y'),"cg_local_z":candidate.get('cg_local_z'),"cg_confirmed":bool(candidate.get('cg_confirmed')),
           "category":candidate.get('category')}
    st=_new_bin_state(c)
    for u in _ordered_packages(existing+[probe]):
        if not _place_one(u,[st]):
            return {"next_candidate":name,"can_continue":False,"stop_reason":f"代表性下一件“{name}”在当前木箱真实净空间内找不到满足边界、碰撞及≥75%支撑要求的三维位置。"}
    return {"next_candidate":name,"can_continue":True,"stop_reason":f"代表性下一件“{name}”经结构扣除后的三维重排仍可放入；建议仅在不增加运输风险时合箱。"}

def _shape_factor_for_placement(p):
    cat=str(p.get("category") or p.get("fitting_category") or p.get("source_name") or "")
    if "三通" in cat: return 0.42
    if "弯头" in cat: return 0.36
    if "异径" in cat: return 0.52
    if "管帽" in cat: return 0.45
    if "扎捆" in cat or "BND-" in cat: return 0.62
    if "管道" in cat: return 0.78
    return 1.0

def _crate_engineering_audit(cd,padding,max_l,max_w,max_h,max_gross,tare_per_m3,next_candidate=None):
    pls=cd.get("placements",[])
    L,W,H=float(cd["length_mm"]),float(cd["width_mm"]),float(cd["height_mm"])
    cargo=sum(float(p.get("weight_kg",0)) for p in pls)
    geom=_crate_geometry(L,W,H,cargo,padding)
    innerL,innerW,innerH=geom['usable_l'],geom['usable_w'],geom['usable_h']
    geometry_ok=min(innerL,innerW,innerH)>0
    actual_vol=sum(float(p.get("actual_l",p["l"]))*float(p.get("actual_w",p["w"]))*float(p.get("actual_h",p["h"])) for p in pls)
    shape_vol=sum(float(p.get("actual_l",p["l"]))*float(p.get("actual_w",p["w"]))*float(p.get("actual_h",p["h"]))*_shape_factor_for_placement(p) for p in pls)
    planning_vol=sum(float(p["l"])*float(p["w"])*float(p["h"]) for p in pls)
    gross=cargo+float(cd.get("tare_weight_kg",0))
    outer_vol=L*W*H
    actual_util=actual_vol/outer_vol*100 if outer_vol else 0
    shape_util=shape_vol/outer_vol*100 if outer_vol else 0
    planning_util=planning_vol/(innerL*innerW*innerH)*100 if geometry_ok else 0
    # The audit MUST use the same occupied-shape collision rule as the wood-box placer.
    collision_ok=True
    for i,a in enumerate(pls):
        for b in pls[i+1:]:
            if _occupied_overlap(a,b): collision_ok=False; break
        if not collision_ok: break
    boundary_ok=geometry_ok and all(float(p["x"])+float(p["l"])<=innerL+1e-6 and float(p["y"])+float(p["w"])<=innerW+1e-6 and float(p["z"])+float(p["h"])<=innerH+1e-6 and min(float(p['x']),float(p['y']),float(p['z']))>=-1e-6 for p in pls)
    support_ok=True
    shared_analysis=support_load_analysis(pls)
    for idx,p in enumerate(pls):
        others=[q for j,q in enumerate(pls) if j!=idx]
        if not _support_ok(p,others,shared_analysis): support_ok=False; break
    gross_ok=gross<=max_gross+1e-6
    ox,oy,oz=geom['origin_x'],geom['origin_y'],geom['origin_z']
    tare=float(cd.get('tare_weight_kg',0) or 0)
    tare_cg=_crate_tare_cg(L,W,H,cargo,tare)
    total_mass=cargo+tare
    if total_mass>0:
        cargo_mx=sum(float(p["weight_kg"])*(ox+float(p["x"])+_safe_local_cg(p,"x",p.get("l"))) for p in pls)
        cargo_my=sum(float(p["weight_kg"])*(oy+float(p["y"])+_safe_local_cg(p,"y",p.get("w"))) for p in pls)
        cargo_mz=sum(float(p["weight_kg"])*(oz+float(p["z"])+_safe_local_cg(p,"z",p.get("h"))) for p in pls)
        cgx=(cargo_mx+tare*tare_cg['x'])/total_mass
        cgy=(cargo_my+tare*tare_cg['y'])/total_mass
        cgz=(cargo_mz+tare*tare_cg['z'])/total_mass
    else: cgx,cgy,cgz=L/2,W/2,oz
    cg_x_pct=cgx/L*100 if L else 50; cg_y_pct=cgy/W*100 if W else 50; cg_z_pct=cgz/H*100 if H else 0
    offx=abs(cg_x_pct-50); offy=abs(cg_y_pct-50)
    stability_x,stability_y=_cg_stability_metrics(cgx,cgy,cgz,L,W,H)
    maxp=max(pls,key=lambda p: float(p.get("actual_l",p["l"]))*float(p.get("actual_w",p["w"]))*float(p.get("actual_h",p["h"]))) if pls else None
    nextinfo=_diagnose_next_candidate(cd,next_candidate,max_l,max_w,max_h,max_gross,padding,tare_per_m3)
    hard=[]; review=[]
    support_analysis=shared_analysis
    hard.extend(support_analysis['hard_issues'])
    review.extend(support_analysis['engineering_reviews'])
    if not geometry_ok: hard.append("结构扣除后无有效净空间")
    if not collision_ok: hard.append("检测到实际占用几何碰撞")
    if not boundary_ok: hard.append("存在越界")
    if not support_ok: hard.append("存在悬空/支撑不足或压在不可叠放件上")
    if not gross_ok: hard.append("毛重超限")
    sett=_crate_weight_settings_load(); highrisk=[]
    hreview=float(sett.get('crate_cg_review_pct',10) or 10); hhigh=float(sett.get('crate_cg_high_risk_pct',20) or 20)
    zreview=float(sett.get('crate_cg_z_review_pct',60) or 60); zhigh=float(sett.get('crate_cg_z_high_risk_pct',75) or 75)
    if max(offx,offy)>hhigh: highrisk.append(f"水平重心偏移>{hhigh:g}%")
    elif max(offx,offy)>hreview: review.append(f"水平重心偏移>{hreview:g}%")
    if cg_z_pct>zhigh: highrisk.append(f"垂直重心高度>{zhigh:g}%")
    elif cg_z_pct>zreview: review.append(f"垂直重心高度>{zreview:g}%")
    if hard:
        status="不通过"; conclusion="；".join(hard+highrisk+review)
    elif highrisk:
        status="高风险"; conclusion="；".join(highrisk+review)+"；几何与毛重硬约束通过，但需负责人/包装工程确认后发运。"
    elif review:
        status="需复核"; conclusion="；".join(review)+"；其余三维边界、碰撞、支撑、结构净空间与毛重校核通过。"
    else:
        status="通过"; conclusion="结构净空间、三维边界、异形碰撞、≥75%支撑与合成重心静态平衡、毛重与三向重心规划校核通过。"
    return {"padding_mm":padding,"max_l":max_l,"max_w":max_w,"max_h":max_h,"max_gross":max_gross,"fill_limit":float(cd.get("fill_ratio_limit",1.0)),
            "actual_util":round(actual_util,1),"planning_util":round(planning_util,1),"shape_util":round(shape_util,1),"cargo_weight":round(cargo,1),"gross_weight":round(gross,1),"item_count":len(pls),
            "max_item_name":maxp.get("source_name","") if maxp else "—","max_item_dims":f"{maxp.get('actual_l',0):.0f}×{maxp.get('actual_w',0):.0f}×{maxp.get('actual_h',0):.0f}" if maxp else "—",
            "cg_x_pct":round(cg_x_pct,1),"cg_y_pct":round(cg_y_pct,1),"cg_z_pct":round(cg_z_pct,1),"offset_x_pct":round(offx,1),"offset_y_pct":round(offy,1),
            "stability_x_ratio":stability_x,"stability_y_ratio":stability_y,
            "collision_ok":collision_ok,"boundary_ok":boundary_ok,"support_ok":support_ok,"gross_ok":gross_ok,"geometry_ok":geometry_ok,
            "clear_l":round(geom['clear_l'],1),"clear_w":round(geom['clear_w'],1),"clear_h":round(geom['clear_h'],1),"structure_class":geom['class_label'],
            "audit_status":status,"conclusion":conclusion,**nextinfo}

def _order_units_for_countability(group_units):
    """Keep identical material lines together while still packing large units first."""
    groups={}
    for u in group_units:
        key=u.get("source_id") or u.get("source_name") or u.get("name")
        groups.setdefault(key,[]).append(u)
    rank={"L":3,"M":2,"S":1}
    buckets=[]
    for key,vals in groups.items():
        vals=sorted(vals,key=lambda u:(u["weight_kg"],u["length_mm"]*u["width_mm"]*u["height_mm"]),reverse=True)
        h=vals[0]
        buckets.append((rank.get(h.get("size_class"),0),h["length_mm"]*h["width_mm"]*h["height_mm"],h["weight_kg"],str(key),vals))
    buckets.sort(key=lambda x:(x[0],x[1],x[2],x[3]),reverse=True)
    out=[]
    for *_m,vals in buckets: out.extend(vals)
    return out

def _unit_from_placement(p):
    return {
        "name":p.get("source_name") or p.get("name") or "材料",
        "source_name":p.get("source_name") or p.get("name") or "材料",
        "asu_group":p.get("asu_group"),"source_id":p.get("source_id"),
        "length_mm":float(p.get("l",0)),"width_mm":float(p.get("w",0)),"height_mm":float(p.get("h",0)),
        "actual_l":float(p.get("actual_l",p.get("l",0))),"actual_w":float(p.get("actual_w",p.get("w",0))),"actual_h":float(p.get("actual_h",p.get("h",0))),
        "weight_kg":float(p.get("weight_kg",0)),"type":p.get("type","材料"),
        "rotatable":bool(p.get("rotatable",True)),"stackable":bool(p.get("stackable",True)),
        "material_family":p.get("material_family"),"size_class":p.get("size_class"),
        "category":p.get("category") or p.get("fitting_category"),
        "cg_local_x":p.get("cg_local_x"),"cg_local_y":p.get("cg_local_y"),"cg_local_z":p.get("cg_local_z"),"cg_confirmed":bool(p.get("cg_confirmed")),
        "weight_grade":p.get("weight_grade","C"),"weight_source":p.get("weight_source","未标注")
    }

def _balanced_split_units(units):
    """Weight-balanced binary split used only after a full crate fails gross/geometry limits."""
    a=[]; b=[]; wa=wb=0.0
    for u in sorted(units,key=lambda x:(float(x.get('weight_kg',0)),float(x.get('length_mm',0))*float(x.get('width_mm',0))*float(x.get('height_mm',0))),reverse=True):
        if wa<=wb: a.append(u); wa+=float(u.get('weight_kg',0))
        else: b.append(u); wb+=float(u.get('weight_kg',0))
    if not a or not b:
        n=max(1,len(units)//2); return list(units[:n]),list(units[n:])
    return a,b

def _engineering_repack_units(units,seed_placements,max_l,max_w,max_h,max_gross,padding,tare_per_m3,oversize_hint=False):
    """Authoritative V4.6 repacker: structure-aware clear space + gross + full 3D re-audit.
    Candidate sizes are searched by volume, so a poor preliminary layout cannot force a max-size crate.
    """
    if not units: return []
    cargo=sum(float(u.get('weight_kg',0)) for u in units)
    max_piece_l=max(float(u.get('length_mm',0)) for u in units); max_piece_w=max(float(u.get('width_mm',0)) for u in units); max_piece_h=max(float(u.get('height_mm',0)) for u in units)
    baseL,baseW,baseH,_=_crate_required_outer(max_piece_l,max_piece_w,max_piece_h,cargo,padding)
    seed_req=None
    if seed_placements:
        cx=max(float(p.get('x',0))+float(p.get('l',0)) for p in seed_placements)
        cy=max(float(p.get('y',0))+float(p.get('w',0)) for p in seed_placements)
        cz=max(float(p.get('z',0))+float(p.get('h',0)) for p in seed_placements)
        seed_req=_crate_required_outer(cx,cy,cz,cargo,padding)[:3]
    planning_vol=sum(float(u.get('length_mm',0))*float(u.get('width_mm',0))*float(u.get('height_mm',0)) for u in units)
    def vals(lo,hi):
        lo=_ceil50(lo); hi=float(hi)
        if lo>hi+1e-6: return []
        span=hi-lo; step=250.0 if span<=2500 else 500.0
        out={float(lo),float(hi)}; x=float(lo)
        while x<hi:
            out.add(min(hi,_ceil50(x))); x+=step
        return sorted(out)
    candidates=[]
    if not oversize_hint and baseL<=max_l+1e-6 and baseW<=max_w+1e-6 and baseH<=max_h+1e-6:
        ls,ws,hs=vals(baseL,max_l),vals(baseW,max_w),vals(baseH,max_h)
        for L in ls:
            for W in ws:
                for H in hs:
                    g=_crate_geometry(L,W,H,cargo,padding)
                    usable=g['usable_l']*g['usable_w']*g['usable_h']
                    if usable<=0 or usable+1e-6<planning_vol: continue
                    candidates.append((L*W*H,L,W,H))
        if seed_req and seed_req[0]<=max_l and seed_req[1]<=max_w and seed_req[2]<=max_h:
            candidates.append((seed_req[0]*seed_req[1]*seed_req[2],*seed_req))
        candidates.sort(key=lambda x:x[0])
        # Bounded search: smallest engineering-feasible envelopes first.
        candidates=candidates[:220]
    else:
        # Oversize candidate starts from the largest single piece, not from an inefficient seed layout.
        L,W,H=baseL,baseW,baseH
        # For multiple pieces in an explicitly oversize group, seed dimensions can provide a useful lower bound.
        if seed_req:
            L=max(L,seed_req[0]); W=max(W,seed_req[1]); H=max(H,seed_req[2])
        candidates=[(L*W*H,L,W,H),((L+100)*(W+100)*(H+100),L+100,W+100,H+100)]
    seen=set()
    for _vol,L,W,H in candidates:
        key=(round(L,3),round(W,3),round(H,3))
        if key in seen or min(L,W,H)<=0: continue
        seen.add(key)
        cd=_try_repack_one_crate(units,L,W,H,max_gross,padding,tare_per_m3)
        if cd is not None and cd.get('audit',{}).get('audit_status')!='不通过':
            cd['oversize']=bool(oversize_hint or L>max_l+1e-6 or W>max_w+1e-6 or H>max_h+1e-6)
            return [cd]
    # If the group cannot pass gross/geometry inside normal limits, split and REPACK both halves.
    if len(units)>1 and not oversize_hint:
        left,right=_balanced_split_units(units)
        out=[]
        for part in (left,right):
            out.extend(_engineering_repack_units(part,None,max_l,max_w,max_h,max_gross,padding,tare_per_m3,False))
        return out
    # Single oversize/overweight item: retain it as a custom box, but keep the audit honest.
    L,W,H=baseL,baseW,baseH
    total_tare=_estimate_tare(L,W,H,tare_per_m3,cargo)
    c,_=_crate_container_def(L,W,H,max(cargo+total_tare+1,max_gross),padding,tare_per_m3,cargo)
    st=_new_bin_state(c)
    for u in _ordered_packages(units): _place_one(u,[st])
    cd={"length_mm":L,"width_mm":W,"height_mm":H,"tare_weight_kg":total_tare,"placements":st['placements'],
        "oversize":True,"material_family":units[0].get('material_family','GEN'),"size_class":units[0].get('size_class','L'),
        "fill_ratio_limit":1.0,"utilization_optimized":True}
    cd['audit']=_crate_engineering_audit(cd,padding,max_l,max_w,max_h,max_gross,tare_per_m3,None)
    return [cd]

def _engineering_normalize_crates(crate_defs,max_l,max_w,max_h,max_gross,padding,tare_per_m3):
    out=[]
    for cd in crate_defs:
        pls=list(cd.get('placements') or [])
        if not pls: continue
        units=[_unit_from_placement(p) for p in pls]
        parts=_engineering_repack_units(units,pls,max_l,max_w,max_h,max_gross,padding,tare_per_m3,bool(cd.get('oversize')))
        for x in parts:
            x['material_family']=cd.get('material_family',x.get('material_family','GEN'))
            x['size_class']=cd.get('size_class',x.get('size_class','M'))
            x['fill_ratio_limit']=cd.get('fill_ratio_limit',x.get('fill_ratio_limit',1.0))
            x['utilization_optimized']=cd.get('utilization_optimized',False)
            if cd.get('long_pipe_mode'): x['long_pipe_mode']=cd.get('long_pipe_mode'); x['long_pipe_mode_label']=cd.get('long_pipe_mode_label')
            out.append(x)
    return out


def _crate_def_units(cd):
    return [_unit_from_placement(p) for p in (cd.get("placements") or [])]

def _crate_def_mix_class(cd):
    """V5.1 packing compatibility: 5083 remains isolated; all other grades may mix."""
    fam=[]
    for p in cd.get("placements") or []:
        token=" | ".join([str(p.get("material_family") or ""),str(p.get("source_name") or ""),str(p.get("name") or "")]).upper()
        fam.append("5083" if "5083" in token else "OTHER")
    if not fam:
        return "OTHER"
    if "5083" in fam:
        return "5083" if all(x=="5083" for x in fam) else "INVALID-5083-MIX"
    return "OTHER"

def _precision_countability_ok(units,max_items_per_crate=0,max_skus_per_crate=0,max_pipes_per_crate=0):
    if int(max_items_per_crate or 0)>0 and len(units)>int(max_items_per_crate):
        return False
    if int(max_skus_per_crate or 0)>0:
        skus={u.get("source_id") or u.get("source_name") or u.get("name") for u in units}
        if len(skus)>int(max_skus_per_crate):
            return False
    if int(max_pipes_per_crate or 0)>0:
        pipes=sum(1 for u in units if _is_long_pipe_unit(u))
        if pipes>int(max_pipes_per_crate):
            return False
    return True

def _precision_defs_metrics(crate_defs,target_min=60,target_max=80):
    reviews=0; highrisks=0; fails=0; cbm=0.0; tare=0.0
    for cd in crate_defs:
        a=cd.get("audit") or {}
        st=a.get("audit_status") or "通过"
        if st=="不通过": fails+=1
        elif st=="高风险": highrisks+=1
        elif st!="通过": reviews+=1
        cbm+=float(cd.get("length_mm",0))*float(cd.get("width_mm",0))*float(cd.get("height_mm",0))/1e9
        tare+=float(cd.get("tare_weight_kg",0))
    um=_crate_utilization_metrics(crate_defs,target_min,target_max)
    return {"crate_count":len(crate_defs),"review_count":reviews,"high_risk_count":highrisks,"fail_count":fails,
            "cbm":round(cbm,6),"tare":round(tare,3),"util":um}

def _precision_defs_score(crate_defs,target_min=60,target_max=80):
    m=_precision_defs_metrics(crate_defs,target_min,target_max); u=m["util"]
    return (m["fail_count"],m["high_risk_count"],m["crate_count"],m["review_count"],m["cbm"],m["tare"],u["low_count"],u["penalty"],-u["average"])

def _precision_unit_order_variants(units):
    variants=[]
    variants.append(sorted(units,key=lambda u:(float(u.get("length_mm",0))*float(u.get("width_mm",0))*float(u.get("height_mm",0)),float(u.get("weight_kg",0))),reverse=True))
    variants.append(sorted(units,key=lambda u:(max(float(u.get("length_mm",0)),float(u.get("width_mm",0)),float(u.get("height_mm",0))),float(u.get("weight_kg",0))),reverse=True))
    variants.append(sorted(units,key=lambda u:(float(u.get("weight_kg",0)),float(u.get("length_mm",0))*float(u.get("width_mm",0))*float(u.get("height_mm",0))),reverse=True))
    variants.append(sorted(units,key=lambda u:(str(u.get("source_id") or u.get("source_name") or ""),-float(u.get("length_mm",0))*float(u.get("width_mm",0))*float(u.get("height_mm",0)))))
    out=[]; seen=set()
    for v in variants:
        k=tuple(_unit_exact_signature(u) for u in v)
        if k not in seen:
            seen.add(k); out.append(v)
    return out

def _precision_repack_into_existing(target_cd,combined_units,max_gross,padding,tare_per_m3):
    """Full 3D confirmation inside an existing crate envelope, then shrink only if still feasible."""
    L=float(target_cd.get("length_mm",0)); W=float(target_cd.get("width_mm",0)); H=float(target_cd.get("height_mm",0))
    rep=_full_confirm_candidate(combined_units,L,W,H,max_gross,padding,tare_per_m3)
    if rep is None or (rep.get("audit") or {}).get("audit_status")=="不通过":
        return None
    # After a successful absorption, search a smaller engineering-feasible envelope bounded by
    # the original target dimensions. This preserves the V4.6 structure-clearance model.
    shrunk=_engineering_repack_units(combined_units,rep.get("placements"),L,W,H,max_gross,padding,tare_per_m3,False)
    if len(shrunk)==1 and (shrunk[0].get("audit") or {}).get("audit_status")!="不通过":
        rep=shrunk[0]
    rep["material_family"]="AL5083" if any("5083" in str(u.get("material_family") or "").upper() for u in combined_units) else "MIX"
    rep["size_class"]="MIX"; rep["fill_ratio_limit"]=1.0; rep["utilization_optimized"]=True; rep["precision_optimized"]=True
    return rep

def _precision_absorb_source(crate_defs,source_idx,max_gross,padding,tare_per_m3,target_min,target_max,max_pipes_per_crate=0,max_items_per_crate=30,max_skus_per_crate=8):
    """Try to delete one source crate.

    First test whole-donor-to-one-target for every compatible target. If that is impossible,
    run deterministic multi-start material distribution. Every competing placement is accepted
    only after complete 3D repack + V4.6 engineering audit; no stale/incremental layout can win.
    """
    if source_idx<0 or source_idx>=len(crate_defs): return None
    src=crate_defs[source_idx]
    if src.get("oversize") or not src.get("placements"): return None
    src_class=_crate_def_mix_class(src)
    if src_class=="INVALID-5083-MIX": return None
    src_units=_crate_def_units(src)
    if not src_units: return None
    target_indices=[]
    for j,t in enumerate(crate_defs):
        if j==source_idx or t.get("oversize") or not t.get("placements"): continue
        if _crate_def_mix_class(t)!=src_class: continue
        target_indices.append(j)
    if not target_indices: return None

    successful=[]
    # Phase A: complete donor -> one target. This is the strongest/cleanest elimination.
    for j in target_indices:
        t=crate_defs[j]; base=_crate_def_units(t); combined=base+src_units
        if not _precision_countability_ok(combined,max_items_per_crate,max_skus_per_crate,max_pipes_per_crate): continue
        if _donor_impossible_by_bounds(base,src_units,float(t["length_mm"]),float(t["width_mm"]),float(t["height_mm"]),max_gross,padding,tare_per_m3): continue
        rep=_precision_repack_into_existing(t,combined,max_gross,padding,tare_per_m3)
        if not rep: continue
        nd=[copy.deepcopy(x) for x in crate_defs]
        nd[j]=rep; nd.pop(source_idx)
        successful.append(nd)
    if successful:
        successful.sort(key=lambda defs:_precision_defs_score(defs,target_min,target_max))
        return successful[0]

    # Phase B: distribute the whole donor across multiple existing crates. Multiple deterministic
    # unit orders reduce greedy order bias; each individual target choice is FULL-repacked.
    for ordered in _precision_unit_order_variants(src_units):
        target_units={j:_crate_def_units(crate_defs[j]) for j in target_indices}
        target_reps={j:copy.deepcopy(crate_defs[j]) for j in target_indices}
        touched=set(); ok=True
        for u in ordered:
            choices=[]
            for j in target_indices:
                cand_units=target_units[j]+[u]
                if not _precision_countability_ok(cand_units,max_items_per_crate,max_skus_per_crate,max_pipes_per_crate): continue
                t=crate_defs[j]
                if not _candidate_dim_fits_target(u,float(t["length_mm"]),float(t["width_mm"]),float(t["height_mm"]),padding): continue
                rep=_precision_repack_into_existing(t,cand_units,max_gross,padding,tare_per_m3)
                if not rep: continue
                a=rep.get("audit") or {}; util=float(a.get("actual_util") or 0); off=max(float(a.get("offset_x_pct") or 0),float(a.get("offset_y_pct") or 0))
                review=0 if a.get("audit_status")=="通过" else 1
                goal=min(92.0,max(70.0,float(target_max)))
                # Concentrate moves into already-touched targets, but engineering review stays ahead of utilization.
                score=(review,0 if j in touched else 1,abs(goal-util),off,-util,float(rep.get("length_mm",0))*float(rep.get("width_mm",0))*float(rep.get("height_mm",0)))
                choices.append((score,j,rep,cand_units))
            if not choices:
                ok=False; break
            choices.sort(key=lambda x:x[0]); _score,j,rep,cand_units=choices[0]
            target_units[j]=cand_units; target_reps[j]=rep; touched.add(j)
        if ok:
            nd=[]
            for j,cd in enumerate(crate_defs):
                if j==source_idx: continue
                nd.append(copy.deepcopy(target_reps[j] if j in touched else cd))
            successful.append(nd)
    if not successful: return None
    successful.sort(key=lambda defs:_precision_defs_score(defs,target_min,target_max))
    return successful[0]

def _precision_global_consolidate(crate_defs,max_gross,padding,tare_per_m3,target_min=60,target_max=80,max_pipes_per_crate=0,max_items_per_crate=30,max_skus_per_crate=8):
    """V5.1 global elimination pass.

    Every round evaluates every non-oversize source crate as an elimination candidate. A source
    disappears only when ALL its material can be reassigned and every affected target passes the
    same V4.6 structure-clearance, collision, support, gross-weight and CG audit.
    """
    work=[copy.deepcopy(x) for x in crate_defs]
    baseline=len(work); rounds=0; attempts=0; eliminated=[]
    while True:
        candidates=[]
        # Lowest-utilization crates are evaluated first for responsiveness, but no eligible source is truncated.
        order=sorted(range(len(work)),key=lambda i:float((work[i].get("audit") or {}).get("actual_util") or 0))
        for i in order:
            if work[i].get("oversize") or not work[i].get("placements"): continue
            attempts+=1
            nd=_precision_absorb_source(work,i,max_gross,padding,tare_per_m3,target_min,target_max,max_pipes_per_crate,max_items_per_crate,max_skus_per_crate)
            if nd and len(nd)<len(work):
                candidates.append((_precision_defs_score(nd,target_min,target_max),i,nd))
        if not candidates: break
        candidates.sort(key=lambda x:x[0]); _score,src_idx,best=candidates[0]
        eliminated.append(src_idx); work=best; rounds+=1
    # Recompute all final audits against the global limits and record the precision trace.
    for cd in work:
        cd["audit"]=_crate_engineering_audit(cd,padding,max_l=max(float(cd.get("length_mm",0)),1),max_w=max(float(cd.get("width_mm",0)),1),max_h=max(float(cd.get("height_mm",0)),1),max_gross=max_gross,tare_per_m3=tare_per_m3,next_candidate=None)
        cd["precision_optimized"]=True
    summary={"enabled":True,"baseline_crates":baseline,"final_crates":len(work),"eliminated_crates":max(0,baseline-len(work)),"rounds":rounds,"source_attempts":attempts,
             "method":"多候选启发式消箱 + 完整3D重排 + 静态规划校核"}
    if work: work[0]["precision_summary"]=summary
    return work,summary

def _pack_wood_items_to_crates(items,max_l,max_w,max_h,max_gross,padding,tare_per_m3, optimize_util=False, target_min=60, target_max=80, long_pipe_mode="standard", max_pipes_per_crate=0, max_items_per_crate=30, max_skus_per_crate=8, precision_global=False):
    if _asu_profile()['enabled']:
        records=ASU.records();partitions={}
        for item in items:
            if item['package_type']=='木箱' and not item.get('is_package_placeholder'):
                partitions.setdefault(ASU.group(item,records),[]).append(item)
        if len(partitions)>1:
            defs=[];over=0
            for subset in partitions.values():
                part,n=_pack_wood_items_to_crates(subset,max_l,max_w,max_h,max_gross,padding,tare_per_m3,optimize_util,target_min,target_max,long_pipe_mode,max_pipes_per_crate,max_items_per_crate,max_skus_per_crate,precision_global)
                defs.extend(part);over+=n
            if defs:defs[0]['precision_summary']={'enabled':True,'final_crates':len(defs),'isolated_groups':len(partitions),'note':'按用途隔离后，各组维持原搜索深度'}
            return defs,over
    units=[]; oversize=[]
    # Preliminary grouping stays permissive to avoid false oversize classification.
    # V4.6 performs the authoritative structure-aware Split -> Repack -> Re-audit pass afterwards.
    estimated_max_tare=max(40.0,(max_l*max_w*max_h/1e9)*tare_per_m3)
    inner={"code":"WOODBOX","inner_l":max(100,max_l-2*padding),"inner_w":max(100,max_w-2*padding),"inner_h":max(100,max_h-2*padding),"door_w":999999,"door_h":999999,"payload":max(1,max_gross-estimated_max_tare)}
    for i in items:
        if i["package_type"]!="木箱" or i.get("is_package_placeholder"):
            continue
        for _n in range(i["qty"]):
            unit=_packing_unit_from_item(i,_n)
            if unit.get("pipe_like"):
                units.append(unit)
            elif not fits_container(unit,inner):
                oversize.append(unit)
            else:
                units.append(unit)

    crate_defs=[]
    pipe_units=[u for u in units if _is_long_pipe_unit(u)]
    other_units=[u for u in units if not _is_long_pipe_unit(u)]
    crate_defs.extend(_pack_long_pipe_units(pipe_units,max_l,max_w,max_h,max_gross,padding,tare_per_m3,long_pipe_mode,max_pipes_per_crate,max_items_per_crate))

    groups={}
    for u in other_units:
        if optimize_util:
            # V3.6: same-material large fittings form the packing skeleton; small/medium
            # fittings are allowed to use the residual spaces around that skeleton.
            groups.setdefault((u["material_family"],"MIX"),[]).append(u)
        else:
            groups.setdefault(_crate_group_key(u),[]).append(u)

    class_order={"L":0,"MIX":1,"SM":1,"M":1,"S":2}
    for (family,size_class), group_units in sorted(groups.items(), key=lambda kv:(kv[0][0],class_order.get(kv[0][1],9))):
        states=[]
        if optimize_util:
            member_classes={u["size_class"] for u in group_units}
            # Mixed crates can safely target a higher cap than a pure large-item crate,
            # because small pieces are used as fillers after the skeleton is placed.
            if "L" in member_classes:
                fill_ratio=min(max(float(target_min),70.0)/100.0,0.82)
            elif "M" in member_classes:
                fill_ratio=min(max(float(target_min),68.0)/100.0,0.84)
            else:
                fill_ratio=min(max(float(target_min),72.0)/100.0,0.86)
        else:
            fill_ratio=min(u["fill_ratio"] for u in group_units)
        ordered=_order_units_for_countability(group_units)
        for unit in ordered:
            if not states or not _place_with_fill_cap(unit,states,fill_ratio,max_items_per_crate,max_skus_per_crate):
                st=_new_bin_state(inner); states.append(st)
                if not _place_with_fill_cap(unit,[st],fill_ratio,max_items_per_crate,max_skus_per_crate):
                    oversize.append(unit); states.pop()
        for st in states:
            mx=max(p["x"]+p["l"] for p in st["placements"]); my=max(p["y"]+p["w"] for p in st["placements"]); mz=max(p["z"]+p["h"] for p in st["placements"])
            L=min(max_l,_ceil50(mx+2*padding)); W=min(max_w,_ceil50(my+2*padding)); H=min(max_h,_ceil50(mz+2*padding))
            cargo_w=sum(p["weight_kg"] for p in st["placements"])
            tare=_estimate_tare(L,W,H,tare_per_m3,cargo_w)
            # V4.6: never split a coordinate list in half. Keep the preliminary group intact;
            # the engineering normalizer below performs Split -> Repack -> Re-audit if required.
            chunks=[st["placements"]]
            for chunk in chunks:
                if not chunk: continue
                mx=max(p["x"]+p["l"] for p in chunk); my=max(p["y"]+p["w"] for p in chunk); mz=max(p["z"]+p["h"] for p in chunk)
                L=min(max_l,_ceil50(mx+2*padding)); W=min(max_w,_ceil50(my+2*padding)); H=min(max_h,_ceil50(mz+2*padding))
                chunk_cargo=sum(float(p.get('weight_kg',0)) for p in chunk)
                tare=_estimate_tare(L,W,H,tare_per_m3,chunk_cargo)
                crate_defs.append({"length_mm":L,"width_mm":W,"height_mm":H,"tare_weight_kg":tare,
                                   "placements":chunk,"oversize":False,"material_family":family,"size_class":size_class,
                                   "fill_ratio_limit":fill_ratio,"utilization_optimized":bool(optimize_util),
                                   "mixed_filler_strategy":bool(optimize_util and size_class=="MIX")})

    for u in oversize:
        L=_ceil50(u["length_mm"]+2*padding); W=_ceil50(u["width_mm"]+2*padding); H=_ceil50(u["height_mm"]+2*padding)
        tare=_estimate_tare(L,W,H,tare_per_m3,float(u.get('weight_kg',0)))
        crate_defs.append({"length_mm":L,"width_mm":W,"height_mm":H,"tare_weight_kg":tare,
                           "placements":[{"source_id":u["source_id"],"source_name":u["source_name"],"weight_kg":u["weight_kg"],
                                          "source":u["source"],"x":0,"y":0,"z":0,"l":u["length_mm"],"w":u["width_mm"],"h":u["height_mm"],
                                          "actual_l":u.get("actual_l",u["length_mm"]),"actual_w":u.get("actual_w",u["width_mm"]),"actual_h":u.get("actual_h",u["height_mm"]),
                                          "material_family":u.get("material_family"),"size_class":u.get("size_class"),"category":u.get("category") or u.get("fitting_category") or "其他"}],
                           "oversize":True,"material_family":u.get("material_family","GEN"),"size_class":u.get("size_class","L"),"fill_ratio_limit":1.0})
    # V4.6 authoritative pass: all preliminary crates are rebuilt using real structural clear space.
    crate_defs=_engineering_normalize_crates(crate_defs,max_l,max_w,max_h,max_gross,padding,tare_per_m3)
    # V5.1: after every crate is individually valid, attempt GLOBAL crate elimination.
    # Candidate sources are never accepted on volume alone: every affected target is fully repacked
    # and re-audited before the source crate may disappear.
    precision_summary={"enabled":False,"baseline_crates":len(crate_defs),"final_crates":len(crate_defs),"eliminated_crates":0,"rounds":0,"source_attempts":0}
    if precision_global and len(crate_defs)>1:
        crate_defs,precision_summary=_precision_global_consolidate(crate_defs,max_gross,padding,tare_per_m3,target_min,target_max,max_pipes_per_crate,max_items_per_crate,max_skus_per_crate)
    for idx,cd in enumerate(crate_defs):
        next_candidate=None
        if not cd.get("oversize"):
            for later in crate_defs[idx+1:]:
                same_family=later.get("material_family")==cd.get("material_family")
                same_group=(later.get("size_class")==cd.get("size_class")) or (cd.get("size_class")=="MIX") or (later.get("size_class")=="MIX")
                if same_family and same_group and later.get("placements"):
                    p=later["placements"][0]
                    next_candidate={"source_id":p.get("source_id"),"source_name":p.get("source_name"),"length_mm":p.get("l"),"width_mm":p.get("w"),"height_mm":p.get("h"),"weight_kg":p.get("weight_kg",0),"rotatable":p.get("rotatable",True),"stackable":p.get("stackable",True),"max_top_load_kg":p.get("max_top_load_kg")}
                    break
        cd["audit"]=_crate_engineering_audit(cd,padding,max_l,max_w,max_h,max_gross,tare_per_m3,next_candidate)
    if crate_defs:
        crate_defs[0]["precision_summary"]=precision_summary
    return crate_defs,len(oversize)


def _round50_down(v):
    return max(500.0, math.floor(float(v)/50.0)*50.0)

def _container_by_code(code):
    return next((c for c in _active_containers() if c["code"]==code), None)

def _strategy_save(data):
    try:
        STRATEGY_PATH.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    except Exception:
        pass

def _strategy_load():
    try:
        if STRATEGY_PATH.exists(): return json.loads(STRATEGY_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None

def _crate_defs_as_packages(items,crate_defs):
    direct=[i for i in items if i["package_type"]!="木箱"]
    packages=[]
    con=db(); prepacked=[dict(r) for r in con.execute("SELECT * FROM crates WHERE package_origin='PREPACKED'").fetchall()]; con.close()
    packages.extend(get_packages([i for i in items if i.get('is_package_placeholder')],prepacked))
    for idx,cd in enumerate(crate_defs,1):
        cargo=sum(float(p.get("weight_kg",0)) for p in cd.get("placements",[]))
        top_load,struct_cls=_crate_top_load_allowance(cargo,cd["length_mm"],cd["width_mm"],cd["height_mm"])
        packages.append({"name":f"TMP-CRATE-{idx:03d}","length_mm":cd["length_mm"],"width_mm":cd["width_mm"],"height_mm":cd["height_mm"],
                         "weight_kg":cargo+float(cd.get("tare_weight_kg",0)),"type":"木箱","rotatable":True,
                         "stackable":False,"max_top_load_kg":None,"top_load_verified":False,"planning_top_load_kg":top_load,"structure_class":struct_cls})
    for i in direct:
        for n in range(int(i["qty"])):
            packages.append({"name":i["name"]+(f"-{n+1}" if i["qty"]>1 else ""),"length_mm":i["length_mm"],"width_mm":i["width_mm"],"height_mm":i["height_mm"],
                             "weight_kg":i["weight_kg"],"type":i["package_type"],"void_fill":"PERLITE_VOID_FILL" in str(i.get("size_note") or ""),"flexible_unit_volume_m3":0.125 if "PERLITE_FLEXIBLE_VOLUME" in str(i.get("size_note") or "") else None,"display_shape":"upright_cylinder" if "圆桶立放" in str(i.get("size_note") or "") else "horizontal_cylinder" if "圆柱卧放" in str(i.get("size_note") or "") else None,"rotatable":bool(i.get("rotatable",1)),"stackable":bool(i.get("stackable",1)),
                                 "cg_local_x":i.get("cg_x_mm"),"cg_local_y":i.get("cg_y_mm"),"cg_local_z":i.get("cg_z_mm"),"cg_confirmed":bool(i.get("cg_confirmed")),
                                 "contact_area_mode":"UNSPECIFIED","skid_count":0,"skid_width_mm":None,"skid_contact_length_mm":None,"custom_contact_area_m2":None})
    return packages

def _container_aware_candidates(target):
    # Candidate maximum crate envelopes aligned to target container geometry.
    # Each final crate still shrinks to actual cargo occupancy inside this envelope.
    L,W,H=target["inner_l"],target["inner_w"],target["inner_h"]
    # reserve practical clearance for door/handling; candidate width/height based on 1/2 lanes/layers
    ws={_round50_down(W-120), _round50_down((W-160)/2), 1000.0, 1100.0, 1200.0, 1500.0, 1800.0, 2100.0, 2200.0}
    hs={_round50_down(H-120), _round50_down((H-160)/2), 700.0, 900.0, 1100.0, 1200.0, 1500.0, 1800.0, 2000.0, 2200.0}
    ls={2500.0,3000.0,3500.0,4000.0,_round50_down((L-200)/2),_round50_down((L-250)/3),_round50_down((L-300)/4)}
    ws=sorted({x for x in ws if 700<=x<=W-40})
    hs=sorted({x for x in hs if 500<=x<=H-40})
    ls=sorted({x for x in ls if 1500<=x<=min(L-50,5000)})
    # Keep bounded: prioritize container-aligned and common practical crate sizes.
    combos=[]
    target_ws=sorted(ws,key=lambda x:min(abs(x-(W-120)),abs(x-(W-160)/2),abs(x-2200),abs(x-1200)))[:4]
    target_hs=sorted(hs,key=lambda x:min(abs(x-(H-120)),abs(x-(H-160)/2),abs(x-1200),abs(x-2200)))[:4]
    target_ls=sorted(ls,key=lambda x:min(abs(x-3000),abs(x-3500),abs(x-4000),abs(x-(L-250)/3)))[:5]
    for l in target_ls:
        for w in target_ws:
            for h in target_hs:
                combos.append((l,w,h))
    # De-dupe and cap search count.
    out=[]; seen=set()
    for c in combos:
        c=tuple(int(round(x/50)*50) for x in c)
        if c not in seen:
            seen.add(c); out.append(c)
    return out[:48]

def optimize_crates_for_container(target_code,max_gross,padding,tare_per_m3,target_min=60,target_max=80,long_pipe_mode="standard",max_pipes_per_crate=0,max_items_per_crate=30,max_skus_per_crate=8):
    """V5.1 two-stage search.

    Stage 1 scans every configured crate envelope with the V4.6 engineering packer.
    Stage 2 sends the best feasible envelopes through the global heuristic elimination pass,
    then ranks final planning solutions by credibility-first hierarchy:
      high-risk count -> crates -> target containers -> review count -> total crate CBM -> tare -> utilization.
    """
    target=_container_by_code(target_code)
    if not target: raise ValueError(f"未知目标集装箱：{target_code}")
    items,_=fetch_state()
    if not any(i["package_type"]=="木箱" for i in items): raise ValueError("没有需要木箱包装的材料。")

    def evaluate(defs,oversize,l,w,h,phase):
        if not defs: return None
        crate_fail=sum(1 for d in defs if (d.get('audit') or {}).get('audit_status')=='不通过')
        if crate_fail: return None
        packages=_crate_defs_as_packages(items,defs)
        if any(not fits_container(p,target) for p in packages): return None
        plan=_homogeneous_plan(packages,target)
        if not plan: return None
        m=_plan_metrics(plan); um=_crate_utilization_metrics(defs,target_min,target_max)
        crate_vol=sum(float(d["length_mm"])*float(d["width_mm"])*float(d["height_mm"]) for d in defs)/1e9
        total_tare=sum(float(d.get("tare_weight_kg",0)) for d in defs)
        crate_high=sum(1 for d in defs if (d.get('audit') or {}).get('audit_status')=='高风险')
        crate_review=sum(1 for d in defs if (d.get('audit') or {}).get('audit_status')=='需复核')
        container_high=int(plan.get('engineering_high_risk_count') or 0)
        container_review=max(0,int(plan.get('engineering_review_count') or 0)-container_high)
        high_risk_total=crate_high+container_high; engineering_risk=crate_review+container_review
        ps=(defs[0].get('precision_summary') or {}) if defs else {}
        # V5.1 credibility-first ordering: avoid high-risk candidates before chasing fewer crates/containers.
        score=(high_risk_total,len(defs),plan["count"],engineering_risk,round(crate_vol,6),round(total_tare,3),oversize,um["low_count"],um["penalty"],-m["volume_utilization"])
        return {"score":score,"max_l":l,"max_w":w,"max_h":h,"crate_count":len(defs),"oversize":oversize,
                "container_count":plan["count"],"container_util":m["volume_utilization"],"weight_util":m["weight_utilization"],
                "avg_crate_util":um["average"],"low_util_crates":um["low_count"],"good_util_crates":um["good_count"],
                "high_util_crates":um["high_count"],"min_crate_util":um["min"],"max_crate_util":um["max"],
                "engineering_high_risk_count":high_risk_total,"engineering_review_count":engineering_risk,"crate_cbm":round(crate_vol,3),"crate_tare_total":round(total_tare,1),
                "precision_eliminated":int(ps.get('eliminated_crates') or 0),"precision_attempts":int(ps.get('source_attempts') or 0),
                "precision_rounds":int(ps.get('rounds') or 0),"search_phase":phase}

    baseline=[]
    candidates=_container_aware_candidates(target)
    for l,w,h in candidates:
        try:
            defs,oversize=_pack_wood_items_to_crates(items,l,w,h,max_gross,padding,tare_per_m3,True,target_min,target_max,long_pipe_mode,max_pipes_per_crate,max_items_per_crate,max_skus_per_crate,precision_global=False)
            rec=evaluate(defs,oversize,l,w,h,'screen')
            if rec: baseline.append(rec)
        except Exception:
            continue
    if not baseline:
        raise ValueError(f"未找到适合 {target_code} 的可行木箱尺寸组合。可尝试放宽木箱上限或改用其他箱型。")
    baseline.sort(key=lambda x:x["score"])

    # Precision-finalist stage: no incremental/legacy layout may become the winner.
    finalists=[]
    seen=set()
    for base in baseline[:8]:
        dims=(base['max_l'],base['max_w'],base['max_h'])
        if dims in seen: continue
        seen.add(dims)
        l,w,h=dims
        try:
            defs,oversize=_pack_wood_items_to_crates(items,l,w,h,max_gross,padding,tare_per_m3,True,target_min,target_max,long_pipe_mode,max_pipes_per_crate,max_items_per_crate,max_skus_per_crate,precision_global=True)
            rec=evaluate(defs,oversize,l,w,h,'precision-final')
            if rec: finalists.append(rec)
        except Exception:
            continue
    results=finalists or baseline
    results.sort(key=lambda x:x["score"])
    for r in results:
        r['candidate_scanned']=len(candidates); r['precision_finalists']=len(finalists)
    return results[0],results[:5]

def _reboxing_snapshot(con):
    """Read only data that the commit depends on, within the caller's transaction."""
    return {table:[dict(r) for r in con.execute(f'SELECT * FROM {table} ORDER BY rowid')]
            for table in ('items','crates','crate_placements','crate_audits','item_evidence_links','asu_records')}


def apply_smart_crates(max_l,max_w,max_h,max_gross,padding,tare_per_m3, exact_fixed=False, optimize_util=False, target_min=60, target_max=80, long_pipe_mode="standard", max_pipes_per_crate=0, max_items_per_crate=30, max_skus_per_crate=8):
    fetch_state()  # Finish legacy normalization before taking a consistent read snapshot.
    con=db()
    try:
        con.execute('BEGIN')
        snapshot=_reboxing_snapshot(con)
    finally: con.close()
    items=deepcopy(snapshot['items']); original_crates=snapshot['crates']
    crate_names={c['id']:c['name'] for c in original_crates}
    for i in items:
        i['crate_name']=crate_names.get(i.get('crate_id'))
        i['material_label']=_material_label_from_item_name(i.get('name'))
    expected=canonical_hash('',snapshot)
    expected_profile=asu_standard.digest(_asu_profile())
    wood=[i for i in items if i["package_type"]=="木箱" and not i.get("is_package_placeholder")]
    if not wood: raise ValueError("没有需要木箱包装的材料。请先导入材料，或把包装方式设为“木箱”。")
    crate_defs,oversize_count=_pack_wood_items_to_crates(items,max_l,max_w,max_h,max_gross,padding,tare_per_m3,optimize_util,target_min,target_max,long_pipe_mode,max_pipes_per_crate,max_items_per_crate,max_skus_per_crate,precision_global=True)
    precision_summary=(crate_defs[0].get("precision_summary") or {}) if crate_defs else {"enabled":True,"baseline_crates":0,"final_crates":0,"eliminated_crates":0,"rounds":0,"source_attempts":0}
    if exact_fixed:
        fixed_defs=[]
        for cd in crate_defs:
            if cd.get("oversize"):
                fixed_defs.append(cd); continue
            units=[_unit_from_placement(p) for p in cd.get('placements',[])]
            repacked=_try_repack_one_crate(units,float(max_l),float(max_w),float(max_h),max_gross,padding,tare_per_m3)
            if repacked is None or repacked.get('audit',{}).get('audit_status')=='不通过':
                raise ValueError(f"固定箱型 {max_l:.0f}×{max_w:.0f}×{max_h:.0f} mm 扣除板材/框架/滑木和装箱间隙后无法容纳当前材料。")
            repacked['material_family']=cd.get('material_family','MIX'); repacked['size_class']=cd.get('size_class','M'); repacked['fill_ratio_limit']=cd.get('fill_ratio_limit',1.0)
            fixed_defs.append(repacked)
        crate_defs=fixed_defs
        if crate_defs: crate_defs[0]["precision_summary"]=precision_summary
    source={i['id']:i for i in wood}
    # Verify conservation before any write: a partial solver result cannot remove units.
    counts={}
    for cd in crate_defs:
        for pl in cd['placements']: counts[pl['source_id']]=counts.get(pl['source_id'],0)+1
    if counts!={i['id']:i['qty'] for i in wood}:
        raise ValueError('分箱结果件数与源材料不一致，已取消保存。')
    con=db()
    try:
        con.execute('BEGIN IMMEDIATE')
        if canonical_hash('',_reboxing_snapshot(con))!=expected or asu_standard.digest(_asu_profile())!=expected_profile:
            raise ValueError('计算期间材料或木箱数据已发生变化，已取消保存；请重新计算。')
        cur=con.cursor()
        removable=[c['id'] for c in original_crates if not _is_prepacked(c)]
        for cid in removable:
            cur.execute('DELETE FROM crate_placements WHERE crate_id=?',(cid,))
            cur.execute('DELETE FROM crate_audits WHERE crate_id=?',(cid,))
            cur.execute('DELETE FROM crates WHERE id=?',(cid,))
        used_sources=set()
        item_columns=[r[1] for r in con.execute('PRAGMA table_info(items)') if r[1]!='id']
        for idx,cd in enumerate(crate_defs,1):
            name=f"BX-PF-{idx:03d}"
            cur.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,tare_weight_kg) VALUES(?,?,?,?,?)",(name,cd["length_mm"],cd["width_mm"],cd["height_mm"],cd["tare_weight_kg"]))
            cid=cur.lastrowid
            counts={}
            for pl in cd["placements"]:
                sid=pl["source_id"]; counts[sid]=counts.get(sid,0)+1
            for sid,qty in counts.items():
                i=source[sid]
                if sid not in used_sources:
                    cur.execute('UPDATE items SET qty=?,crate_id=? WHERE id=?',(qty,cid,sid))
                    used_sources.add(sid)
                else:
                    # A split keeps every stored field. Evidence remains bound to its original
                    # physical material through an explicit per-evidence lineage link.
                    clone={k:i[k] for k in item_columns}; clone.update(qty=qty,crate_id=cid)
                    cur.execute(f"INSERT INTO items ({','.join(item_columns)}) VALUES ({','.join('?' for _ in item_columns)})",[clone[k] for k in item_columns])
                    child=cur.lastrowid
                    if _asu_profile()['enabled']:
                        declaration=cur.execute('SELECT record_json FROM asu_records WHERE entity_key=?',('item:'+str(sid),)).fetchone()
                        if declaration:
                            draft=json.loads(declaration[0]);draft['basis']='';draft['evidence_id']=None
                            cur.execute('INSERT INTO asu_records(entity_key,record_json,updated_at) VALUES(?,?,?)',('item:'+str(child),json.dumps(draft,ensure_ascii=False),time.strftime('%Y-%m-%dT%H:%M:%S')))
                    for key in ('size_evidence_id','weight_evidence_id','cg_evidence_id'):
                        eid=i.get(key)
                        if not eid: continue
                        link=cur.execute('SELECT source_item_id FROM item_evidence_links WHERE item_id=? AND evidence_id=?',(sid,eid)).fetchone()
                        cur.execute('INSERT OR IGNORE INTO item_evidence_links VALUES(?,?,?)',(child,eid,link[0] if link else sid))
            for seq,pl in enumerate(cd["placements"],1):
                src=source.get(pl["source_id"],{})
                prof=_fitting_profile(src) if src else {"size_class":cd.get("size_class"),"material_family":cd.get("material_family"),"category":""}
                cur.execute("""INSERT INTO crate_placements(crate_id,seq,item_name,x,y,z,l,w,h,actual_l,actual_w,actual_h,weight_kg,weight_grade,weight_source,cg_local_x,cg_local_y,cg_local_z,cg_confirmed,size_class,material_family,fitting_category)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (cid,seq,pl.get("source_name",src.get("name","")),pl.get("x",0),pl.get("y",0),pl.get("z",0),pl.get("l",src.get("length_mm",0)),pl.get("w",src.get("width_mm",0)),pl.get("h",src.get("height_mm",0)),
                             pl.get("actual_l",src.get("length_mm",0)),pl.get("actual_w",src.get("width_mm",0)),pl.get("actual_h",src.get("height_mm",0)),pl.get("weight_kg",src.get("weight_kg",0)),
                             src.get("weight_grade","C"),src.get("weight_source","未标注"),pl.get('cg_local_x',src.get('cg_x_mm')),pl.get('cg_local_y',src.get('cg_y_mm')),pl.get('cg_local_z',src.get('cg_z_mm')),1 if pl.get('cg_confirmed',src.get('cg_confirmed')) else 0,prof.get("size_class"),prof.get("material_family"),prof.get("category")))
            a=cd.get("audit",{})
            cur.execute("""INSERT OR REPLACE INTO crate_audits(crate_id,padding_mm,max_l,max_w,max_h,max_gross,fill_limit,actual_util,planning_util,shape_util,cargo_weight,gross_weight,item_count,max_item_name,max_item_dims,cg_x_pct,cg_y_pct,cg_z_pct,offset_x_pct,offset_y_pct,stability_x_ratio,stability_y_ratio,collision_ok,boundary_ok,support_ok,gross_ok,geometry_ok,clear_l,clear_w,clear_h,structure_class,next_candidate,can_continue,stop_reason,audit_status,conclusion)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (cid,a.get("padding_mm"),a.get("max_l"),a.get("max_w"),a.get("max_h"),a.get("max_gross"),a.get("fill_limit"),a.get("actual_util"),a.get("planning_util"),a.get("shape_util"),a.get("cargo_weight"),a.get("gross_weight"),a.get("item_count"),a.get("max_item_name"),a.get("max_item_dims"),a.get("cg_x_pct"),a.get("cg_y_pct"),a.get("cg_z_pct"),a.get("offset_x_pct"),a.get("offset_y_pct"),a.get("stability_x_ratio"),a.get("stability_y_ratio"),1 if a.get("collision_ok") else 0,1 if a.get("boundary_ok") else 0,1 if a.get("support_ok") else 0,1 if a.get("gross_ok") else 0,1 if a.get("geometry_ok") else 0,a.get("clear_l"),a.get("clear_w"),a.get("clear_h"),a.get("structure_class"),a.get("next_candidate"),None if a.get("can_continue") is None else (1 if a.get("can_continue") else 0),a.get("stop_reason"),a.get("audit_status"),a.get("conclusion")))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally: con.close()
    return len(crate_defs),oversize_count,precision_summary


def _crate_material_family(crate_id):
    con=db()
    rows=con.execute("SELECT DISTINCT material_family FROM crate_placements WHERE crate_id=? AND material_family IS NOT NULL",(crate_id,)).fetchall()
    con.close()
    vals=[str(r[0]) for r in rows if r[0]]
    return vals[0] if len(vals)==1 else ("MIX" if vals else "GEN")

def _target_plan_count_for_packages(packages,target_code):
    c=_container_by_code(target_code)
    if not c: return None
    normal=[p for p in packages if fits_container(p,c)]
    if len(normal)!=len(packages): return None
    p=_homogeneous_plan(normal,c)
    return p["count"] if p else None

def _pair_merge_simulation(ca,cb,items,crates,params,current_target_count):
    if _is_prepacked(ca) or _is_prepacked(cb): return None
    pair_items=[dict(i) for i in items if i.get("crate_id") in {ca["id"],cb["id"]}]
    if not pair_items: return None
    for i in pair_items:
        i["package_type"]="木箱"
    try:
        defs,oversize=_pack_wood_items_to_crates(
            pair_items,
            params["max_l"],params["max_w"],params["max_h"],params["max_gross"],
            params["padding"],params["tare_per_m3"],True,
            params["target_min"],params["target_max"],params["long_pipe_mode"],params["max_pipes"],params["max_items"],params["max_skus"]
        )
    except Exception:
        return None
    if oversize or len(defs)!=1:
        return None
    cd=defs[0]; a=cd.get("audit") or {}
    top_load,struct_cls=_crate_top_load_allowance(float(a.get("cargo_weight") or 0),cd["length_mm"],cd["width_mm"],cd["height_mm"])
    merged_pkg={
        "name":f"MERGE-{ca['name']}-{cb['name']}",
        "length_mm":cd["length_mm"],"width_mm":cd["width_mm"],"height_mm":cd["height_mm"],
        "weight_kg":float(a.get("gross_weight") or 0),"type":"木箱","rotatable":True,"stackable":False,
        "max_top_load_kg":None,"top_load_verified":False,"planning_top_load_kg":top_load,"structure_class":struct_cls
    }
    packages=get_packages(items,crates)
    new_packages=[p for p in packages if p.get("name") not in {ca["name"],cb["name"]}]
    new_packages.append(merged_pkg)
    after_count=_target_plan_count_for_packages(new_packages,params["target_container"])
    return {
        "partner":cb["name"],"partner_id":cb["id"],"target_id":ca["id"],
        "merged_util":round(float(a.get("actual_util") or 0),1),
        "merged_planning_util":round(float(a.get("planning_util") or 0),1),
        "merged_shape_util":round(float(a.get("shape_util") or 0),1),
        "merged_dims":f"{cd['length_mm']:.0f}×{cd['width_mm']:.0f}×{cd['height_mm']:.0f}",
        "merged_gross":round(float(a.get("gross_weight") or 0),1),
        "container_before":current_target_count,"container_after":after_count,
        "container_delta":(None if after_count is None or current_target_count is None else after_count-current_target_count),
        "_cd":cd
    }


def _outer_candidate_values(v):
    vals={float(v)}
    for f in (0.95,0.90,0.85,0.80,0.75,0.70,0.65,0.60,0.55):
        vals.add(max(300.0,_ceil50(float(v)*f)))
    for d in (100,200,300,500,750,1000):
        vals.add(max(300.0,_ceil50(max(300.0,float(v)-d))))
    return sorted(vals,reverse=True)

def _crate_units(crate_id,items):
    units=[]
    for i in items:
        if i.get('crate_id')!=crate_id or i.get('is_package_placeholder'): continue
        for n in range(int(i.get('qty') or 0)):
            units.append(_packing_unit_from_item(i,n))
    return units

_REPACK_CACHE={}
_REPACK_CACHE_ORDER=[]
_REPACK_CACHE_LOCK=threading.Lock()
_REPACK_CACHE_MAX=512

def _unit_exact_signature(u):
    return (
        int(u.get('source_id') or -1), str(u.get('source_name') or u.get('name') or ''),
        round(float(u.get('length_mm',0)),3), round(float(u.get('width_mm',0)),3), round(float(u.get('height_mm',0)),3),
        round(float(u.get('actual_l',u.get('length_mm',0))),3), round(float(u.get('actual_w',u.get('width_mm',0))),3), round(float(u.get('actual_h',u.get('height_mm',0))),3),
        round(float(u.get('weight_kg',0)),3), bool(u.get('rotatable',True)), bool(u.get('stackable',True)),
        None if u.get('cg_local_x') in (None,'') else round(float(u.get('cg_local_x')),3),
        None if u.get('cg_local_y') in (None,'') else round(float(u.get('cg_local_y')),3),
        None if u.get('cg_local_z') in (None,'') else round(float(u.get('cg_local_z')),3), bool(u.get('cg_confirmed')),
        str(u.get('category') or ''), str(u.get('material_family') or '')
    )

def _unit_geom_signature(u):
    """Packing-equivalent signature. Safe for failure de-duplication within the SAME current layout state."""
    return (
        round(float(u.get('length_mm',0)),3),round(float(u.get('width_mm',0)),3),round(float(u.get('height_mm',0)),3),
        round(float(u.get('weight_kg',0)),3),bool(u.get('rotatable',True)),bool(u.get('stackable',True)),
        None if u.get('cg_local_x') in (None,'') else round(float(u.get('cg_local_x')),3),
        None if u.get('cg_local_y') in (None,'') else round(float(u.get('cg_local_y')),3),
        None if u.get('cg_local_z') in (None,'') else round(float(u.get('cg_local_z')),3),bool(u.get('cg_confirmed')),
        str(u.get('category') or ''),str(u.get('material_family') or '')
    )

def _repack_cache_key(units,L,W,H,max_gross,padding,tare_per_m3):
    sig=tuple(sorted((_unit_exact_signature(u) for u in units)))
    s=_crate_weight_settings_load(); wsig=(str(s.get('model')),round(float(s.get('panel_density',500)),2),round(float(s.get('timber_density',480)),2),round(float(s.get('hardware_pct',4)),2))
    return (asu_standard.digest(_asu_profile()),round(float(L),3),round(float(W),3),round(float(H),3),round(float(max_gross),3),round(float(padding),3),round(float(tare_per_m3),3),wsig,sig)

def _repack_cache_get(key):
    with _REPACK_CACHE_LOCK:
        val=_REPACK_CACHE.get(key)
        return copy.deepcopy(val) if val is not None else None

def _repack_cache_put(key,val):
    with _REPACK_CACHE_LOCK:
        if key in _REPACK_CACHE:
            _REPACK_CACHE[key]=copy.deepcopy(val); return
        _REPACK_CACHE[key]=copy.deepcopy(val)
        _REPACK_CACHE_ORDER.append(key)
        if len(_REPACK_CACHE_ORDER)>_REPACK_CACHE_MAX:
            old=_REPACK_CACHE_ORDER.pop(0); _REPACK_CACHE.pop(old,None)

def _crate_container_def(L,W,H,max_gross,padding,tare_per_m3,cargo_weight=0):
    tare=_estimate_tare(L,W,H,tare_per_m3,cargo_weight)
    g=_crate_geometry(L,W,H,cargo_weight,padding)
    c={"code":"WOODBOX-SHRINK","inner_l":g['usable_l'],"inner_w":g['usable_w'],"inner_h":g['usable_h'],
       "door_w":999999,"door_h":999999,"payload":max(1,max_gross-tare),"crate_geometry":g}
    return c,tare

def _state_from_cd(cd,L,W,H,max_gross,padding,tare_per_m3):
    cargo=sum(float(p.get('weight_kg',0)) for p in cd.get('placements',[]))
    c,_tare=_crate_container_def(L,W,H,max_gross,padding,tare_per_m3,cargo)
    return _build_state_from_placements(c,[dict(p) for p in cd.get('placements',[])])

def _cd_from_state(st,L,W,H,max_gross,padding,tare_per_m3):
    cargo=sum(float(p.get('weight_kg',0)) for p in st.get('placements',[]))
    _c,tare=_crate_container_def(L,W,H,max_gross,padding,tare_per_m3,cargo)
    cd={"length_mm":L,"width_mm":W,"height_mm":H,"tare_weight_kg":tare,"placements":[dict(p) for p in st['placements']],
        "oversize":False,"material_family":"MIX","size_class":"M","fill_ratio_limit":1.0,"utilization_optimized":True}
    cd['audit']=_crate_engineering_audit(cd,padding,L,W,H,max_gross,tare_per_m3,None)
    return cd

def _try_extend_crate_cd(base_cd,extras,L,W,H,max_gross,padding,tare_per_m3):
    """Exact-preserving accelerator.
    Try to place ONLY new units into the already-valid layout. If it fails, caller MUST fall back to full repack.
    A success is a fully valid packing, so accepting it cannot reduce correctness.
    """
    if not base_cd: return None
    st=_state_from_cd(base_cd,L,W,H,max_gross,padding,tare_per_m3)
    for u in _ordered_packages(extras):
        if not _place_one(u,[st]):
            return None
    return _cd_from_state(st,L,W,H,max_gross,padding,tare_per_m3)

def _try_extend_or_full(base_cd,base_units,extras,L,W,H,max_gross,padding,tare_per_m3):
    """Incremental fast path + original full repack fallback. Search quality is never reduced."""
    inc=_try_extend_crate_cd(base_cd,extras,L,W,H,max_gross,padding,tare_per_m3)
    if inc is not None:
        return inc,'incremental'
    return _try_repack_one_crate(base_units+list(extras),L,W,H,max_gross,padding,tare_per_m3),'full'

def _full_confirm_candidate(units,L,W,H,max_gross,padding,tare_per_m3):
    """V4.4.1 final-candidate confirmation.
    Incremental placement may prove that a candidate is geometrically feasible, but it can freeze
    the existing layout in a suboptimal state. Any candidate that is about to be accepted is therefore
    normalized through the original complete repacker before it becomes the new search state.
    """
    return _try_repack_one_crate(list(units),L,W,H,max_gross,padding,tare_per_m3)


def _try_repack_one_crate(units,L,W,H,max_gross,padding,tare_per_m3):
    if min(L,W,H)<=2*padding+50: return None
    key=_repack_cache_key(units,L,W,H,max_gross,padding,tare_per_m3)
    cached=_repack_cache_get(key)
    if cached is not None:
        return cached
    total_cargo=sum(float(u.get('weight_kg',0)) for u in units)
    c,tare=_crate_container_def(L,W,H,max_gross,padding,tare_per_m3,total_cargo)
    if min(c['inner_l'],c['inner_w'],c['inner_h'])<=0: return None
    st=_new_bin_state(c)
    for u in _ordered_packages(units):
        if not _place_one(u,[st]):
            return None
    cd={"length_mm":L,"width_mm":W,"height_mm":H,"tare_weight_kg":tare,"placements":st['placements'],
        "oversize":False,"material_family":"MIX","size_class":"M","fill_ratio_limit":1.0,"utilization_optimized":True}
    cd['audit']=_crate_engineering_audit(cd,padding,L,W,H,max_gross,tare_per_m3,None)
    _repack_cache_put(key,cd)
    return copy.deepcopy(cd)

def _recommend_shrink_for_crate(crate,items,crates,stats,strategy,current_target_count=None):
    st=stats.get(crate['id'],{})
    current_util=float(st.get('utilization') or 0)
    target_min=float((strategy or {}).get('target_util_min',60) or 60)
    target_max=float((strategy or {}).get('target_util_max',80) or 80)
    if current_util>=max(55.0,target_min): return None
    padding=_padding_setting(strategy)
    tare_per_m3=float((strategy or {}).get('tare_per_m3',45) or 45)
    max_gross=float((strategy or {}).get('max_crate_gross',5000) or 5000)
    target_container=str((strategy or {}).get('target_container','40HQ') or '40HQ')
    units=_crate_units(crate['id'],items)
    if not units: return None
    cargo_vol=sum(float(i['qty'])*float(i['length_mm'])*float(i['width_mm'])*float(i['height_mm']) for i in items if i.get('crate_id')==crate['id'])
    curL,curW,curH=float(crate['length_mm']),float(crate['width_mm']),float(crate['height_mm'])
    curV=curL*curW*curH
    mid=(target_min+target_max)/2
    candidates=[]
    for L in _outer_candidate_values(curL):
        for W in _outer_candidate_values(curW):
            for H in _outer_candidate_values(curH):
                v=L*W*H
                if v>=curV*0.985: continue
                est_u=cargo_vol/v*100 if v else 999
                if est_u>min(92.0,target_max+12): continue
                save=(1-v/curV)*100
                candidates.append(((0 if target_min<=est_u<=target_max else 1,abs(est_u-mid),-save,v),L,W,H,est_u))
    candidates.sort(key=lambda x:x[0])
    feasible=[]; tested=0; seen=set()
    for _,L,W,H,est_u in candidates:
        if (L,W,H) in seen: continue
        seen.add((L,W,H)); tested+=1
        cd=_try_repack_one_crate(units,L,W,H,max_gross,padding,tare_per_m3)
        if cd:
            a=cd.get('audit') or {}; u=float(a.get('actual_util') or est_u); save=(1-(L*W*H)/curV)*100
            feasible.append((0 if target_min<=u<=target_max else 1,abs(u-mid),-save,L*W*H,cd))
        if tested>=120: break
    if not feasible: return None
    feasible.sort(key=lambda x:x[:4]); cd=feasible[0][4]; a=cd.get('audit') or {}
    newV=cd['length_mm']*cd['width_mm']*cd['height_mm']; save_pct=round((1-newV/curV)*100,1)
    if save_pct<8: return None
    packages=get_packages(items,crates)
    _tl,_sc=_crate_top_load_allowance(float(a.get('cargo_weight') or st.get('cargo_weight') or 0),cd['length_mm'],cd['width_mm'],cd['height_mm'])
    rep={"name":crate['name'],"length_mm":cd['length_mm'],"width_mm":cd['width_mm'],"height_mm":cd['height_mm'],
         "weight_kg":float(a.get('gross_weight') or st.get('gross_weight') or 0),"type":"木箱","rotatable":True,"stackable":False,"max_top_load_kg":None,"top_load_verified":False,"planning_top_load_kg":_tl,"structure_class":_sc}
    new_packages=[p for p in packages if p.get('name')!=crate['name']]+[rep]
    before=current_target_count if current_target_count is not None else _target_plan_count_for_packages(packages,target_container)
    after=_target_plan_count_for_packages(new_packages,target_container)
    return {"current_dims":f"{curL:.0f}×{curW:.0f}×{curH:.0f}","recommended_dims":f"{cd['length_mm']:.0f}×{cd['width_mm']:.0f}×{cd['height_mm']:.0f}",
            "recommended_l":cd['length_mm'],"recommended_w":cd['width_mm'],"recommended_h":cd['height_mm'],
            "current_util":round(current_util,1),"recommended_util":round(float(a.get('actual_util') or 0),1),
            "planning_util":round(float(a.get('planning_util') or 0),1),"volume_saving_pct":save_pct,
            "gross_weight":round(float(a.get('gross_weight') or 0),1),"container_before":before,"container_after":after,
            "target_container":target_container,"_cd":cd}

def _write_crate_layout(crate_id,cd,items):
    source={i['id']:i for i in items if i.get('crate_id')==crate_id}
    con=db(); cur=con.cursor()
    old_row=cur.execute('SELECT length_mm,width_mm,height_mm,size_confirmed,size_source,size_confirmed_at FROM crates WHERE id=?',(crate_id,)).fetchone()
    dims_changed=True
    if old_row:
        dims_changed=any(abs(float(old_row[k] or 0)-float(cd[k] or 0))>0.5 for k in ('length_mm','width_mm','height_mm'))
    cur.execute('UPDATE crates SET length_mm=?,width_mm=?,height_mm=?,tare_weight_kg=? WHERE id=?',(cd['length_mm'],cd['width_mm'],cd['height_mm'],cd['tare_weight_kg'],crate_id))
    if dims_changed:
        invalidate_crate_size(con,crate_id,'规划/重排尺寸变化后待重新人工对照')
    cur.execute('DELETE FROM crate_placements WHERE crate_id=?',(crate_id,)); cur.execute('DELETE FROM crate_audits WHERE crate_id=?',(crate_id,))
    for seq,pl in enumerate(cd['placements'],1):
        src=source.get(pl.get('source_id'),{}); prof=_fitting_profile(src) if src else {"size_class":pl.get('size_class'),"material_family":pl.get('material_family'),"category":pl.get('category','')}
        cur.execute('''INSERT INTO crate_placements(crate_id,seq,item_name,x,y,z,l,w,h,actual_l,actual_w,actual_h,weight_kg,weight_grade,weight_source,cg_local_x,cg_local_y,cg_local_z,cg_confirmed,size_class,material_family,fitting_category)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (crate_id,seq,pl.get('source_name',src.get('name','')),pl.get('x',0),pl.get('y',0),pl.get('z',0),pl.get('l',0),pl.get('w',0),pl.get('h',0),
                     pl.get('actual_l',src.get('length_mm',0)),pl.get('actual_w',src.get('width_mm',0)),pl.get('actual_h',src.get('height_mm',0)),pl.get('weight_kg',src.get('weight_kg',0)),
                     src.get('weight_grade','C'),src.get('weight_source','未标注'),pl.get('cg_local_x',src.get('cg_x_mm')),pl.get('cg_local_y',src.get('cg_y_mm')),pl.get('cg_local_z',src.get('cg_z_mm')),1 if pl.get('cg_confirmed',src.get('cg_confirmed')) else 0,prof.get('size_class'),prof.get('material_family'),prof.get('category')))
    a=cd.get('audit',{})
    cur.execute('''INSERT OR REPLACE INTO crate_audits(crate_id,padding_mm,max_l,max_w,max_h,max_gross,fill_limit,actual_util,planning_util,shape_util,cargo_weight,gross_weight,item_count,max_item_name,max_item_dims,cg_x_pct,cg_y_pct,cg_z_pct,offset_x_pct,offset_y_pct,stability_x_ratio,stability_y_ratio,collision_ok,boundary_ok,support_ok,gross_ok,geometry_ok,clear_l,clear_w,clear_h,structure_class,next_candidate,can_continue,stop_reason,audit_status,conclusion)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (crate_id,a.get('padding_mm'),a.get('max_l'),a.get('max_w'),a.get('max_h'),a.get('max_gross'),a.get('fill_limit'),a.get('actual_util'),a.get('planning_util'),a.get('shape_util'),a.get('cargo_weight'),a.get('gross_weight'),a.get('item_count'),a.get('max_item_name'),a.get('max_item_dims'),a.get('cg_x_pct'),a.get('cg_y_pct'),a.get('cg_z_pct'),a.get('offset_x_pct'),a.get('offset_y_pct'),a.get('stability_x_ratio'),a.get('stability_y_ratio'),1 if a.get('collision_ok') else 0,1 if a.get('boundary_ok') else 0,1 if a.get('support_ok') else 0,1 if a.get('gross_ok') else 0,1 if a.get('geometry_ok') else 0,a.get('clear_l'),a.get('clear_w'),a.get('clear_h'),a.get('structure_class'),a.get('next_candidate'),None if a.get('can_continue') is None else (1 if a.get('can_continue') else 0),a.get('stop_reason'),a.get('audit_status'),a.get('conclusion')))
    con.commit(); con.close()

def _crate_quality_score(stat,audit,target_min,target_max):
    u=float(stat.get("utilization") or 0)
    score=100.0
    if u<target_min: score-=min(34.0,(target_min-u)*0.75)
    elif u>target_max: score-=min(18.0,(u-target_max)*0.8)
    if str(stat.get("weight_grade") or "C")=="C": score-=8
    elif str(stat.get("weight_grade") or "C")=="B": score-=3
    if audit:
        if audit.get("audit_status")=="不通过": score-=28
        elif audit.get("audit_status")=="高风险": score-=20
        elif audit.get("audit_status")=="需复核": score-=12
        score-=min(10.0,max(float(audit.get("offset_x_pct") or 0),float(audit.get("offset_y_pct") or 0))*0.35)
    return max(0,min(100,round(score)))

def build_crate_advice(items,crates,stats,audit_map,rec,strategy,deep=False):
    target_min=float((strategy or {}).get("target_util_min",60) or 60)
    target_max=float((strategy or {}).get("target_util_max",80) or 80)
    params={
        "max_l":float((strategy or {}).get("recommended_l",4000) or 4000),
        "max_w":float((strategy or {}).get("recommended_w",2200) or 2200),
        "max_h":float((strategy or {}).get("recommended_h",2200) or 2200),
        "max_gross":float((strategy or {}).get("max_crate_gross",5000) or 5000),
        "padding":_padding_setting(strategy),
        "tare_per_m3":float((strategy or {}).get("tare_per_m3",45) or 45),
        "target_min":target_min,"target_max":target_max,
        "long_pipe_mode":str((strategy or {}).get("long_pipe_mode","standard") or "standard"),
        "max_pipes":int((strategy or {}).get("max_pipes_per_crate",0) or 0),
        "max_items":int((strategy or {}).get("max_items_per_crate",30) or 0),
        "max_skus":int((strategy or {}).get("max_skus_per_crate",8) or 0),
        "target_container":str((strategy or {}).get("target_container","40HQ") or "40HQ"),
    }
    packages=get_packages(items,crates)
    current_target_count=_target_plan_count_for_packages(packages,params["target_container"])
    family={c["id"]:_crate_material_family(c["id"]) for c in crates}
    advice={}
    low=[c for c in crates if not _is_prepacked(c) and float(stats[c["id"]].get("utilization") or 0)<target_min]
    # Only the emptiest crates need expensive merge simulation. This keeps the page responsive.
    low_sorted=sorted(low,key=lambda c:float(stats[c["id"]].get("utilization") or 0))
    expensive_ids={c["id"] for c in low_sorted[:18]} if deep else set()
    pair_cache={}
    for c in [c for c in crates if not _is_prepacked(c)]:
        st=stats[c["id"]]; audit=audit_map.get(c["id"],{})
        u=float(st.get("utilization") or 0)
        quality=_crate_quality_score(st,audit,target_min,target_max)
        action="保留"
        reason=f"外包络利用率 {u:.1f}% 位于建议区间 {target_min:.0f}%~{target_max:.0f}% 内。"
        merge=None
        shrink=None
        if u<max(55.0,target_min) and c["id"] in expensive_ids:
            shrink=_recommend_shrink_for_crate(c,items,crates,stats,strategy,current_target_count)
        if audit.get("audit_status")=="不通过":
            action="先修正"
            reason=f"静态规划校核未通过：{audit.get('conclusion') or '存在碰撞/边界/重量问题'}。应先修正，暂不以利用率为目标。"
        elif u>target_max:
            action="偏紧复核"
            reason=f"外包络利用率 {u:.1f}% 高于目标上限 {target_max:.0f}%，应复核木方、固定、取货空间和箱体受力。"
        elif u<target_min:
            action="建议合箱/重排"
            reason=f"外包络利用率仅 {u:.1f}%，低于目标下限 {target_min:.0f}%，存在明显空间浪费。"
            if shrink:
                action="建议缩箱"
                impact=""
                if shrink.get("container_before") is not None and shrink.get("container_after") is not None:
                    impact=(f"；目标 {shrink['target_container']} 预计由 {shrink['container_before']} 柜降至 {shrink['container_after']} 柜" if shrink["container_after"]<shrink["container_before"] else f"；目标 {shrink['target_container']} 柜数预计暂不变")
                reason=(f"当前箱体 {shrink['current_dims']} mm 偏大。保持全部材料、重量和清点内容不变重新3D排布后，可缩至约 {shrink['recommended_dims']} mm，"
                        f"外包络利用率预计由 {shrink['current_util']:.1f}% 提升至 {shrink['recommended_util']:.1f}%，箱体体积减少约 {shrink['volume_saving_pct']:.1f}%{impact}。")
            if c["id"] in expensive_ids and not shrink:
                # Prefer same-family, low/medium-utilization neighboring crates.
                candidates=[x for x in crates if x["id"]!=c["id"] and family.get(x["id"])==family.get(c["id"]) and float(stats[x["id"]].get("utilization") or 0)<target_max]
                candidates=sorted(candidates,key=lambda x:(abs(float(stats[x["id"]].get("utilization") or 0)-u),float(stats[x["id"]].get("utilization") or 0)))[:5]
                sims=[]
                for p in candidates:
                    key=tuple(sorted((c["id"],p["id"])))
                    sim=pair_cache.get(key)
                    if key not in pair_cache:
                        sim=_pair_merge_simulation(c,p,items,crates,params,current_target_count)
                        pair_cache[key]=sim
                    if sim: sims.append(sim)
                if sims:
                    mid=(target_min+target_max)/2
                    sims.sort(key=lambda s:(
                        0 if s.get("container_delta") is not None and s["container_delta"]<0 else 1,
                        abs(float(s["merged_util"])-mid),
                        -float(s["merged_util"])
                    ))
                    merge=sims[0]
                    impact=""
                    if merge.get("container_delta") is not None:
                        if merge["container_delta"]<0:
                            impact=f"；目标 {params['target_container']} 预计可由 {merge['container_before']} 柜降至 {merge['container_after']} 柜"
                        elif merge["container_delta"]==0:
                            impact=f"；目标 {params['target_container']} 柜数预计暂不变"
                        else:
                            impact=f"；目标 {params['target_container']} 柜数可能增加，不推荐"
                    reason=(f"当前 {u:.1f}% 偏低；与 {merge['partner']} 合并模拟可形成 1 个约 "
                            f"{merge['merged_dims']} mm 木箱，外包络利用率约 {merge['merged_util']:.1f}%{impact}。")
            if not merge and audit.get("can_continue") in (1,True):
                action="继续填充/全局重排"
                reason=f"当前 {u:.1f}% 偏低，而且静态规划校核判断仍可继续装料；优先做全局重排而不是保留当前分箱。"
            elif not merge and audit.get("can_continue") in (0,False):
                reason += f" 当前箱停止装箱原因：{audit.get('stop_reason') or '几何/重量/支撑约束'}"
        advice[c["id"]]={
            "score":quality,"action":action,"reason":reason,"merge":merge,"shrink":shrink,
            "target_min":target_min,"target_max":target_max,
            "family":family.get(c["id"],"GEN")
        }
    summary={
        "keep":sum(1 for a in advice.values() if a["action"]=="保留"),
        "merge":sum(1 for a in advice.values() if "合箱" in a["action"] or "重排" in a["action"]),
        "review":sum(1 for a in advice.values() if a["action"] in {"偏紧复核","先修正"}),
        "avg_score":round(sum(a["score"] for a in advice.values())/len(advice),1) if advice else 0,
        "target_container":params["target_container"],"target_count":current_target_count,
        "target_min":target_min,"target_max":target_max
    }
    return advice,summary

def analyze_project(items,crates,rec):
    warnings=[]
    if rec.get("container_config_error"):
        warnings.append({"level":"danger","text":"集装箱参数异常："+str(rec.get("container_config_error"))+"。已停止集装箱规划，不会静默退回软件典型柜参数。请修复/重新上传实际柜参数，或明确切换为标准柜规划。"})
        return warnings
    try:
        containers=_active_containers()
    except Exception as e:
        return [{"level":"danger","text":"集装箱参数异常："+str(e)}]
    if rec.get("oversize"):
        names=", ".join(p["name"] for p in rec["oversize"][:5])
        more="" if len(rec["oversize"])<=5 else f" 等共 {len(rec['oversize'])} 个包装单元"
        warnings.append({"level":"danger","text":f"检测到常规 GP/HQ/OT 无法装入的超限货物：{names}{more}。这些货物已从常规集装箱优化中单独剥离，需评估 FR/散杂/专项运输。"})
    unassigned=sum(i["qty"] for i in items if i["package_type"]=="木箱" and not i.get("crate_id"))
    if unassigned: warnings.append({"level":"warn","text":f"还有 {unassigned} 件木箱类材料未分配到木箱；当前集装箱计算不会计入这些未分配材料。"})
    for c in crates:
        st=crate_stats(c,items)
        if st["internal_metrics_available"] and st["item_count"] and st["utilization"]<30:
            warnings.append({"level":"info","text":f"{c['name']} 容积利用率仅 {st['utilization']}%，可考虑合箱或缩小木箱。"})
        pkg={"length_mm":c["length_mm"],"width_mm":c["width_mm"],"height_mm":c["height_mm"],"weight_kg":st["gross_weight"],"rotatable":True}
        if not any(fits_container(pkg,x) for x in containers):
            warnings.append({"level":"danger","text":f"{c['name']} 无法进入当前启用的 GP/HQ/OT 集装箱，需要调整木箱或增加框架箱/散杂运规则。"})
    if rec.get("options"):
        b=rec["options"][0]
        warnings.insert(0,{"level":"ok","text":f"当前系统优先方案：{b['label']}。依据是先减少柜数，再减少特种箱，最后比较空间利用率。"})
    return warnings[:12]


def fetch_crate_layouts(crates):
    con=db()
    out={}
    colors=["#2563eb","#059669","#d97706","#dc2626","#7c3aed","#0891b2","#ea580c","#65a30d","#db2777","#0f766e"]
    for c in crates:
        rows=[dict(r) for r in con.execute("SELECT * FROM crate_placements WHERE crate_id=? ORDER BY seq",(c["id"],)).fetchall()]
        if not rows: continue
        ar=con.execute("SELECT * FROM crate_audits WHERE crate_id=?",(c["id"],)).fetchone()
        audit=dict(ar) if ar else None
        citems=[dict(r) for r in con.execute("SELECT * FROM items WHERE crate_id=?",(c['id'],)).fetchall()]
        cstats=crate_stats(c,citems); pad=float((audit or {}).get('padding_mm') or 100); geom=_crate_geometry(c['length_mm'],c['width_mm'],c['height_mm'],cstats['cargo_weight'],pad)
        placements=[]
        for j,r in enumerate(rows):
            # Stored coordinates are relative to the usable cargo space. Display them inside the true outer box.
            placements.append({"seq":r["seq"],"name":r["item_name"],"x":r["x"]+geom['origin_x'],"y":r["y"]+geom['origin_y'],"z":r["z"]+geom['origin_z'],"l":r["l"],"w":r["w"],"h":r["h"],
                               "actual_l":r["actual_l"],"actual_w":r["actual_w"],"actual_h":r["actual_h"],"weight_kg":r["weight_kg"],
                               "weight_grade":r.get("weight_grade","C"),"weight_source":r.get("weight_source","未标注"),
                               "type":f"{r['material_family'] or ''}-{r['size_class'] or ''} {r['fitting_category'] or ''}".strip(),"color":colors[j%len(colors)]})
        used=sum(p["actual_l"]*p["actual_w"]*p["actual_h"] for p in placements)
        cv=c["length_mm"]*c["width_mm"]*c["height_mm"]
        out[c["name"]]={"code":c["name"],"label":c["name"],"count":1,"audit":audit,"containers":[{"index":1,"container_code":c["name"],"inner_l":c["length_mm"],"inner_w":c["width_mm"],"inner_h":c["height_mm"],
                         "usable_l":geom['usable_l'],"usable_w":geom['usable_w'],"usable_h":geom['usable_h'],"origin_x":geom['origin_x'],"origin_y":geom['origin_y'],"origin_z":geom['origin_z'],
                         "payload":999999,"placements":placements,"volume_utilization":round(used/cv*100,1) if cv else 0,"weight_utilization":0,
                         "total_weight":cstats["gross_weight"]}]}
    con.close(); return out

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    _cleanup_import_sessions()
    try:
        project_mode=_asu_profile()
        TRUCKS.config()
    except ValueError as exc:
        return HTMLResponse('<p>项目或货车配置无效。请打开 <a href="/asu-project">项目设置</a> 或 <a href="/vehicles">货车参数</a> 修正。</p>',status_code=409)
    deep_mode=str(request.query_params.get("deep") or "0").lower() in {"1","true","yes","on"}
    items, crates = fetch_state()
    invalid=[]
    for c in crates:
        if _is_prepacked(c):
            try: checked_number(c.get('actual_gross_weight_kg'),'已包装单元毛重',strict=True)
            except ValueError: invalid.append(c)
    if invalid:
        return templates.TemplateResponse(request,'repair_weights.html',{'crates':invalid,'release_meta':_release_meta_load()})
    stats = {c["id"]:crate_stats(c,items) for c in crates}
    # Attach engineering-audit utilization metrics so the UI can distinguish
    # physical envelope utilization from conservative planning occupancy.
    con_a=db()
    audit_map={r["crate_id"]:dict(r) for r in con_a.execute("SELECT * FROM crate_audits").fetchall()}
    con_a.close()
    for c in crates:
        a=audit_map.get(c["id"],{})
        stats[c["id"]]["planning_util"]=a.get("planning_util")
        stats[c["id"]]["shape_util"]=a.get("shape_util")
        stats[c["id"]]["audit_status"]=a.get("audit_status")
    rec = _apply_saved_container_layouts(recommend_containers(items, crates, deep=deep_mode))
    layout_data = {o["code"]: rec.get("plans", {}).get(o["code"]) for o in rec.get("options", []) if rec.get("plans", {}).get(o["code"])}
    project_warnings = analyze_project(items, crates, rec)
    crate_layouts=fetch_crate_layouts(crates)
    crate_strategy=_strategy_load()
    cost_settings=_cost_settings_load()
    if TRUCKS.mode()=="truck":cost_settings=dict(cost_settings,enabled=False)
    dimension_overrides=_dim_overrides_load()
    for d in dimension_overrides:
        key=f'{_norm_material(d.get("material"))}|{_norm_category(d.get("category"))}|{_norm_dn_spec(d.get("dn_spec"))}'
        d['evidence_valid']=bool(d.get('evidence_id') and _evidence_matches(d.get('evidence_id'),scope_type='type',scope_key=key,evidence_types=('approved_dimension',),require_verified=True))
    packing_template_info=_packing_template_info()
    shipping_mark_template_info=_shipping_mark_template_info()
    shipping_mark_common=_shipping_mark_common_load()
    crate_weight_settings=_crate_weight_settings_load()
    release_meta=_release_meta_load(); container_specs_meta=_container_specs_meta_load(); container_specs_state=_container_specs_state(); release_audits=_recent_release_audits(8); release_issues=_data_quality_release_issues(items,crates); physical_evidence_reviews=_physical_evidence_review_notes(items,crates)
    try:
        con_b=db(); benchmark_records=[dict(r) for r in con_b.execute('SELECT * FROM benchmark_records ORDER BY id DESC LIMIT 8').fetchall()]; evidence_count=int(con_b.execute('SELECT COUNT(*) FROM evidence_files').fetchone()[0]); con_b.close()
    except Exception:
        benchmark_records=[]; evidence_count=0
    audit_chain_problems=EVIDENCE_STORE.verify_chains()[:5]
    size_counts={g:sum(1 for i in items if i.get("size_grade")==g) for g in ("A","B","C")}
    if rec.get('container_config_error') or container_specs_state.get('mode')=='invalid':
        crate_advice={}; crate_advice_summary={'keep':0,'merge':0,'review':0,'avg_score':0,'target_container':'—','target_count':None,'target_min':60,'target_max':80}
    else:
        crate_advice,crate_advice_summary=build_crate_advice(items,crates,stats,audit_map,rec,crate_strategy,deep=deep_mode)
    asu_rows=ASU.package_report()[0] if project_mode["enabled"] else []
    return templates.TemplateResponse(request, "index.html", {
        **TRUCKS.context(),**FLEET.context(),"selection_error":request.query_params.get("selection_error",""),
        "asu_profile":project_mode,"asu_issue_count":len(asu_rows),"asu_pending_entities":len({r['entity'] for r in asu_rows if r['entity']!='项目'}),
        "items":items, "crates":crates, "stats":stats, "rec":rec,
        "crate_layout_data_json":json.dumps(crate_layouts,ensure_ascii=False).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026"),
        "project_warnings": project_warnings,
        "shipment_source":_shipment_source_state(items),
        "bag_permission_status":_bag_permission_status(items,crates),
        "layout_data_json": json.dumps(layout_data, ensure_ascii=False).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026"),
        "crate_strategy": crate_strategy,
        "cost_settings": cost_settings, "dimension_overrides": dimension_overrides, "size_counts": size_counts,
        "packing_template_info": packing_template_info,
        "shipping_mark_template_info": shipping_mark_template_info,
        "shipping_mark_common": shipping_mark_common,
        "crate_weight_settings": crate_weight_settings,
        "release_meta": release_meta, "container_specs_meta": container_specs_meta, "container_specs_state": container_specs_state, "release_audits": release_audits, "release_issues": release_issues, "physical_evidence_reviews": physical_evidence_reviews, "benchmark_records": benchmark_records, "evidence_count": evidence_count, "audit_chain_problems": audit_chain_problems,
        "crate_advice": crate_advice,
        "crate_advice_summary": crate_advice_summary,
        "deep_mode": deep_mode,
        "physical_source_options": _physical_source_options(),
        "imported": request.query_params.get("imported"),
        "skipped": request.query_params.get("skipped"),
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
    })


@app.get('/dimension-override-template.csv')
def dimension_override_template():
    if not DIM_TEMPLATE_PATH.exists():
        DIM_TEMPLATE_PATH.write_text('material,category,dn_spec,length_mm,width_mm,height_mm,source,verified\n5083,弯头,300,0,0,0,厂家图纸REV-A,0\n5083,三通,150,0,0,0,供应商批准图,1\n',encoding='utf-8-sig')
    return FileResponse(DIM_TEMPLATE_PATH,filename='尺寸覆盖库模板.csv')

@app.post('/dimension-overrides')
async def add_dimension_override(material:str=Form(...),category:str=Form(...),dn_spec:str=Form(...),length_mm:float=Form(...),width_mm:float=Form(...),height_mm:float=Form(...),source:str=Form('项目/供应商尺寸'),verified:int=Form(0), evidence:UploadFile=File(None), evidence_note:str=Form(''), evidence_length_mm:float=Form(0), evidence_width_mm:float=Form(0), evidence_height_mm:float=Form(0)):
    try:
        if min(length_mm,width_mm,height_mm)<=0: raise ValueError('尺寸必须大于0。')
        evidence_id=None; evidence_sha=''; evidence_scope_key=''
        norm_key=f'{_norm_material(material)}|{_norm_category(category)}|{_norm_dn_spec(dn_spec)}'
        if verified:
            user=_confirmation_user()
            if not evidence or not getattr(evidence,'filename',None): raise ValueError('A级项目尺寸库必须上传实测记录/供应商批准图纸作为凭证。')
            if min(evidence_length_mm,evidence_width_mm,evidence_height_mm)<=0: raise ValueError('A级项目尺寸库必须填写凭证上核对的L/W/H数值。')
            ver=verify_dimension_claim((length_mm,width_mm,height_mm),(evidence_length_mm,evidence_width_mm,evidence_height_mm))
            if not ver['ok']: raise ValueError('项目尺寸库记录与凭证核对值不一致：'+'；'.join(ver['problems']))
            raw=await evidence.read(); ev=EVIDENCE_STORE.save_evidence(entity_type='dimension_override',entity_id=norm_key,evidence_type='approved_dimension',filename=evidence.filename,content=raw,note=evidence_note or source,created_by=user,
                scope_type='type',scope_key=norm_key,claimed_values={'length_mm':evidence_length_mm,'width_mm':evidence_width_mm,'height_mm':evidence_height_mm},verification=ver)
            evidence_id=ev['id']; evidence_sha=ev['sha256']; evidence_scope_key=norm_key
        rows=_dim_overrides_load(); key=(_norm_material(material),_norm_category(category),_norm_dn_spec(dn_spec))
        rows=[r for r in rows if (_norm_material(r.get('material')),_norm_category(r.get('category')),_norm_dn_spec(r.get('dn_spec'))) != key]
        rows.append({'material':material,'category':category,'dn_spec':dn_spec,'length_mm':length_mm,'width_mm':width_mm,'height_mm':height_mm,'source':source,'verified':1 if verified else 0,'evidence_id':evidence_id,'evidence_sha256':evidence_sha,'evidence_scope':'type' if verified else '','evidence_scope_key':evidence_scope_key})
        _dim_overrides_save(rows); _record_credibility_event('save_dimension_override','dimension_override',norm_key,f'{material} {category} DN{dn_spec}',{'verified':bool(verified),'evidence_id':evidence_id})
        return RedirectResponse(url='/?message='+quote(f'项目尺寸记录已保存：{material} {category} DN{dn_spec}。A级记录已完成类型凭证数值核对。' if verified else f'项目尺寸记录已保存为B2级：{material} {category} DN{dn_spec}。'),status_code=303)
    except Exception as e:
        return RedirectResponse(url='/?error='+quote(str(e)),status_code=303)

@app.post('/dimension-overrides/upload')
async def upload_dimension_overrides(file:UploadFile=File(...)):
    try:
        raw_bytes=await file.read()
        if len(raw_bytes)>2*1024*1024: raise ValueError('尺寸覆盖CSV超过2MB。')
        raw=raw_bytes.decode('utf-8-sig'); rdr=csv.DictReader(io.StringIO(raw)); rows=[]; downgraded=0
        for r in rdr:
            if not r.get('category') or not r.get('dn_spec'): continue
            if min(float(r.get('length_mm') or 0),float(r.get('width_mm') or 0),float(r.get('height_mm') or 0))<=0: continue
            rr=dict(r)
            if str(r.get('verified') or '').strip().lower() in ('1','true','yes','y','已确认','已批准'): downgraded+=1
            rr['verified']=0; rr['evidence_id']=None; rr['evidence_sha256']=''; rows.append(rr)
        _dim_overrides_save(rows)
        msg=f'尺寸覆盖库已导入 {len(rows)} 条，默认均为B级。'
        if downgraded: msg+=f' CSV中 {downgraded} 条 verified 标志未附独立凭证，已自动降为B级。'
        return RedirectResponse(url='/?message='+quote(msg+' 如需A级，请逐条绑定批准/实测凭证。'),status_code=303)
    except Exception as e:
        return RedirectResponse(url='/?error='+quote('尺寸库导入失败：'+str(e)),status_code=303)

@app.post('/apply-dimension-library')
def apply_dimension_library():
    items,_=fetch_state(); changed=0; con=db(); cur=con.cursor()
    for i in items:
        name=str(i.get('name') or ''); cat=next((k for k in ('弯头','三通','异径','缩接','管帽') if k in name),None)
        if not cat: continue
        dn_text='x'.join('DN'+x for x in re.findall(r'DN\s*(\d+)',name,re.I)) or name
        mat=i.get('material_label') or name
        dim=_dimension_override_lookup(cat,dn_text,mat,name)
        if dim:
            target_grade=str(dim.get('size_grade','B')); target_source=str(dim.get('size_source','尺寸库')); target_note=str(dim.get('size_note',''))
            dims_changed=any(abs(float(i[k])-float(dim[k]))>0.5 for k in ('length_mm','width_mm','height_mm'))
            provenance_changed=(str(i.get('size_grade') or '')!=target_grade or str(i.get('size_source') or '')!=target_source or str(i.get('size_note') or '')!=target_note)
            if dims_changed or provenance_changed:
                cur.execute('UPDATE items SET length_mm=?,width_mm=?,height_mm=?,size_grade=?,size_subgrade=?,size_source=?,size_note=?,size_evidence_id=?,size_evidence_scope=?,size_evidence_scope_key=? WHERE id=?',(dim['length_mm'],dim['width_mm'],dim['height_mm'],target_grade,dim.get('size_subgrade') or ('A3' if target_grade=='A' else 'B2'),target_source,target_note,dim.get('evidence_id') if target_grade=='A' else None,dim.get('evidence_scope') if target_grade=='A' else '',dim.get('evidence_scope_key') if target_grade=='A' else '',i['id'])); changed+=1
    if changed:
        cur.execute('DELETE FROM crate_placements');cur.execute('DELETE FROM crate_audits')
    con.commit();con.close()
    return RedirectResponse(url='/?message='+quote(f'尺寸库已应用到 {changed} 条现有材料。请重新执行一键规划以更新木箱和3D。'),status_code=303)

@app.post('/apply-standard-estimates')
def apply_standard_estimates():
    """Use GB/T only as a B-grade estimator and never overwrite existing A/B declared dimensions."""
    items,_=fetch_state(); changed=0; con=db(); cur=con.cursor()
    for i in items:
        if str(i.get('size_grade') or 'C')!='C': continue
        name=str(i.get('name') or ''); cat=next((k for k in ('弯头','三通','异径','缩接','管帽') if k in name),None)
        if not cat: continue
        dn_text='x'.join('DN'+x for x in re.findall(r'DN\s*(\d+)',name,re.I)) or name
        mat=i.get('material_label') or name
        dim=_gbt2025_standard_envelope(cat,dn_text,name,mat,{})
        if not dim: continue
        cur.execute("UPDATE items SET length_mm=?,width_mm=?,height_mm=?,size_grade='B',size_subgrade='B3',size_source=?,size_note=?,size_evidence_id=NULL,size_evidence_scope='',size_evidence_scope_key='' WHERE id=?",(dim['length_mm'],dim['width_mm'],dim['height_mm'],str(dim.get('size_source') or 'GB/T标准估算'),str(dim.get('size_note') or 'GB/T标准外包络估算；未实测。'),i['id'])); changed+=1
    if changed:
        cur.execute('DELETE FROM crate_placements'); cur.execute('DELETE FROM crate_audits')
    con.commit(); con.close()
    _record_credibility_event('apply_standard_estimates','items','','',{'changed':changed})
    return RedirectResponse(url='/?message='+quote(f'GB/T标准估算已填充 {changed} 条C级缺省尺寸；未覆盖任何A级/B级已有尺寸。'),status_code=303)

@app.get('/container-spec-template.csv')
def container_spec_template():
    header='code,inner_l,inner_w,inner_h,door_w,door_h,payload,open_top,floor_load_kg_m2\n'
    body=''.join(f"{c['code']},{c['inner_l']},{c['inner_w']},{c['inner_h']},{c['door_w']},{c['door_h']},{c['payload']},{1 if c.get('open_top') else 0},{c.get('floor_load_kg_m2') or ''}\n" for c in DEFAULT_CONTAINERS)
    p=DATA_DIR/'container_spec_template.csv'; p.write_text(header+body,encoding='utf-8-sig'); return FileResponse(p,filename='集装箱实际参数模板.csv')

@app.post('/container-specs/upload')
async def upload_container_specs(file:UploadFile=File(...), reference_no:str=Form(...), support_evidence:UploadFile=File(...), evidence_note:str=Form(''), evidence_reference_no:str=Form(...)):
    try:
        reference_no=str(reference_no or '').strip()
        if not reference_no: raise ValueError('必须填写箱号/订舱号/箱证参考编号。')
        raw=await file.read()
        if len(raw)>512*1024: raise ValueError('集装箱参数CSV超过512KB。')
        rdr=csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))); rows=[]
        for r in rdr:
            code=str(r.get('code') or '').strip().upper()
            if not code: continue
            row={'code':code,'inner_l':float(r['inner_l']),'inner_w':float(r['inner_w']),'inner_h':float(r['inner_h']),'door_w':float(r['door_w']),'door_h':float(r['door_h']),'payload':float(r['payload']),'open_top':str(r.get('open_top') or '').strip().lower() in ('1','true','yes','y'),'floor_load_kg_m2':(float(r['floor_load_kg_m2']) if str(r.get('floor_load_kg_m2') or '').strip() else None)}
            if min(row['inner_l'],row['inner_w'],row['inner_h'],row['door_w'],row['door_h'],row['payload'])<=0: raise ValueError(f'{code} 参数必须大于0')
            rows.append(row)
        if not rows: raise ValueError('没有识别到有效集装箱参数。')
        user=_confirmation_user()
        ev_ref=str(evidence_reference_no or '').strip()
        if ev_ref != reference_no: raise ValueError('凭证上的箱号/订舱号/箱证编号与系统reference_no不一致。')
        support_raw=await support_evidence.read()
        csv_sha=hashlib.sha256(raw).hexdigest()
        specs_sha=_container_specs_sha256(rows)
        ver={'ok':True,'system_reference_no':reference_no,'evidence_reference_no':ev_ref,'container_csv_sha256':csv_sha,'container_specs_sha256':specs_sha}
        ev=EVIDENCE_STORE.save_evidence(entity_type='container_specs',entity_id=reference_no,evidence_type='container_certificate_or_booking',filename=support_evidence.filename,content=support_raw,note=evidence_note or reference_no,created_by=user,
            scope_type='project',scope_key=reference_no,claimed_values={'reference_no':ev_ref,'container_csv_sha256':csv_sha,'container_specs_sha256':specs_sha},verification=ver)
        saved_at=time.strftime('%Y-%m-%d %H:%M:%S')
        _json_save(CONTAINER_SPECS_PATH,rows); _json_save(CONTAINER_SPECS_META_PATH,{'mode':'actual','confirmed':True,'filename':str(file.filename or ''),'csv_sha256':csv_sha,'specs_sha256':specs_sha,'saved_at':saved_at,'reference_no':reference_no,'evidence_id':ev['id'],'evidence_sha256':ev['sha256']}); _refresh_containers(); _PERF_CACHE['fast'].clear(); _PERF_CACHE['deep'].clear()
        _record_credibility_event('confirm_container_specs','container_specs',reference_no,reference_no,{'filename':str(file.filename or ''),'count':len(rows),'saved_at':saved_at,'evidence_id':ev['id']})
        return RedirectResponse(url='/?message='+quote(f'已载入 {len(rows)} 种实柜/项目参数并绑定凭证：{reference_no}。'),status_code=303)
    except Exception as e:
        return RedirectResponse(url='/?error='+quote('集装箱参数导入失败：'+str(e)),status_code=303)

@app.post('/container-specs/reset')
def reset_container_specs():
    try:
        CONTAINER_SPECS_PATH.unlink(missing_ok=True)
        _json_save(CONTAINER_SPECS_META_PATH,{'mode':'standard','confirmed':False,'saved_at':time.strftime('%Y-%m-%d %H:%M:%S')})
    except Exception: pass
    _refresh_containers(); _PERF_CACHE['fast'].clear(); _PERF_CACHE['deep'].clear()
    return RedirectResponse(url='/?message='+quote('已明确切换为标准柜规划模式。软件将使用内置典型柜参数；装柜数据对照版仍需重新上传并绑定实际柜凭证。'),status_code=303)

@app.post('/cost-settings')
def cost_settings(enabled:str=Form('0'),currency:str=Form('CNY'),crate_fixed:float=Form(120),crate_m3:float=Form(650),crate_labor:float=Form(100),c20gp:float=Form(9000),c40gp:float=Form(12500),c40hq:float=Form(13500),c20ot:float=Form(16000),c40ot:float=Form(22000),oog_each:float=Form(5000)):
    d={'enabled':enabled=='1','currency':currency,'crate_fixed':crate_fixed,'crate_m3':crate_m3,'crate_labor':crate_labor,'20GP':c20gp,'40GP':c40gp,'40HQ':c40hq,'20OT':c20ot,'40OT':c40ot,'oog_each':oog_each}
    _cost_settings_save(d)
    return RedirectResponse(url='/?message='+quote('成本参数已保存；集装箱方案已按新参数重新比较。'),status_code=303)


def _record_credibility_event(event_type,entity_type='',entity_id='',entity_name='',details=None):
    try:
        return EVIDENCE_STORE.append_event(event_type,entity_type,entity_id,entity_name,details or {})
    except Exception:
        logger.exception('record credibility event failed'); return ''

def _recent_release_audits(limit=10):
    try:
        con=db(); rows=con.execute('SELECT * FROM release_audit_log ORDER BY id DESC LIMIT ?',(max(1,min(int(limit),50)),)).fetchall(); con.close()
        return [dict(r) for r in rows]
    except Exception:
        logger.exception('read release audit log failed'); return []

def _record_release_export(export_type,path,items,crates,details=None):
    try:
        pp=Path(path); digest=hashlib.sha256(pp.read_bytes()).hexdigest() if pp.exists() else ''
        return EVIDENCE_STORE.append_release(export_type=export_type,file_name=pp.name,state_fingerprint=_state_fingerprint(items,crates),file_sha256=digest,details=details or {})
    except Exception:
        logger.exception('record release export failed'); return ''

def _evidence_exists(evidence_id):
    if not evidence_id: return False
    row=EVIDENCE_STORE.get(evidence_id)
    return bool(row and EVIDENCE_STORE.path_for(evidence_id))

def _evidence_matches(evidence_id, *, scope_type=None, scope_key=None, evidence_types=None, entity_types=None, entity_ids=None, require_verified=False):
    try:
        kwargs=dict(scope_type=scope_type,scope_key=scope_key,evidence_types=evidence_types,entity_types=entity_types,entity_ids=entity_ids,require_verified=require_verified)
        if EVIDENCE_STORE.matches(evidence_id,**kwargs): return True
        # Only a recorded split of this exact evidence may inherit the source binding.
        # Callers still verify the evidence claim against the child's current values.
        if scope_type=='item' and entity_types==('item',) and entity_ids and len(entity_ids)==1 and str(scope_key)==str(entity_ids[0]):
            con=db()
            try: link=con.execute('SELECT source_item_id FROM item_evidence_links WHERE item_id=? AND evidence_id=?',(entity_ids[0],evidence_id)).fetchone()
            finally: con.close()
            if link:
                source=str(link[0]); kwargs.update(scope_key=source,entity_ids=(source,))
                return EVIDENCE_STORE.matches(evidence_id,**kwargs)
        return False
    except Exception:
        return False

def _confirmation_user():
    user=str((_release_meta_load() or {}).get('approver') or '').strip()
    if not user:
        raise ValueError('请先在“数据确认责任信息”中填写单一责任确认人，再进行A级确认。')
    return user

def _cred_subgrade(grade,source,evidence_id=None,evidence_scope=''):
    g=str(grade or 'C').upper(); src=str(source or '')
    if g=='A':
        if str(evidence_scope or '')=='item': return 'A1'
        if str(evidence_scope or '')=='batch': return 'A2'
        if str(evidence_scope or '') in ('type','project'): return 'A3'
        return 'A1' if evidence_id else 'A?'
    if g=='B':
        if 'GB/T' in src or '标准' in src: return 'B3'
        if '类型' in src or '项目尺寸库' in src or '批准图' in src: return 'B2'
        return 'B1'
    return 'C'

def _evidence_summary(evidence_id):
    row=EVIDENCE_STORE.get(evidence_id) if evidence_id else None
    return row or {}


# ---------------- Packing list / container load list export ----------------
PACKING_EXPORT_ALIASES={
    "po":["purchase order","po","po no","采购订单号"],"batch":["delivery batch","batch","发运批次"],"origin":["country of origin","origin","原产国"],
    "seq":["序号","no","no.","序列"],
    "package_no":["箱号/包装号","箱号","包装号","包装编号","package no","case no"],
    "package_type":["包装方式","包装类型","包装形式","package type"],
    "content":["材料/箱内内容","箱内材料","材料内容","材料","品名","货物名称","内容"],
    "material":["材质","材料牌号","牌号","material"],
    "qty":["数量","件数","qty","quantity"],
    "length_mm":["长(mm)","长度(mm)","长","length"],
    "width_mm":["宽(mm)","宽度(mm)","宽","width"],
    "height_mm":["高(mm)","高度(mm)","高","height"],
    "net_weight":["净重(kg)","净重","net weight","n.w."],
    "gross_weight":["毛重(kg)","毛重","gross weight","g.w."],
    "size_grade":["尺寸等级","尺寸可信度","size grade"],
    "weight_grade":["重量等级","重量可信度","weight grade"],
    "remark":["备注","说明","remark","remarks"],
}
CONTAINER_EXPORT_ALIASES={
    "seq":["序号","no","no."],"scheme":["方案","装柜方案","scheme"],
    "container_type":["柜型","集装箱型","container type"],"container_no":["柜号","集装箱号","container no"],
    "package_no":["箱号/裸装标识","箱号","裸装标识","包装号"],"package_type":["包装方式","包装类型"],
    "content":["材料/箱内内容","材料","箱内内容","品名"],"material":["材质","牌号"],"qty":["数量","件数"],
    "size":["外形尺寸(mm)","外形尺寸","尺寸"],"weight":["重量(kg)","重量","毛重(kg)"],
    "x":["位置x(mm)","x(mm)","位置x"],"y":["位置y(mm)","y(mm)","位置y"],"z":["位置z(mm)","z(mm)","位置z"],
    "remark":["备注","说明"]
}

CONTAINER_EXPORT_ALIASES['container_type']+=['车型']
CONTAINER_EXPORT_ALIASES['container_no']+=['车序号']
CONTAINER_EXPORT_ALIASES['scheme']+=['装车方案']

def _export_alias_reverse(alias_map):
    out={}
    for key,vals in alias_map.items():
        for v in vals:
            out[normalize_header(v)]=key
    return out

def _xlsx_first_sheet_path(content):
    NS_REL="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    NS_PKG="http://schemas.openxmlformats.org/package/2006/relationships"
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        wb=ET.fromstring(z.read('xl/workbook.xml'))
        rel=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        relmap={x.attrib.get('Id'):x.attrib.get('Target') for x in rel}
        sheets=wb.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheets')
        if sheets is None or not list(sheets): raise ValueError('模板没有工作表。')
        sh=list(sheets)[0]; rid=sh.attrib.get(f'{{{NS_REL}}}id'); target=relmap.get(rid,'')
        if target.startswith('/'): path=target.lstrip('/')
        elif target.startswith('xl/'): path=target
        else: path='xl/'+target
        parts=[]
        for p in path.split('/'):
            if p=='..':
                if parts: parts.pop()
            elif p not in ('','.'): parts.append(p)
        return sh.attrib.get('name','Sheet1'), '/'.join(parts)

def _xlsx_export_header_info(content,alias_map):
    tables=_xlsx_read_tables(content)
    if not tables: raise ValueError('模板中没有可读工作表。')
    reverse=_export_alias_reverse(alias_map)
    best=None
    for ti,t in enumerate(tables):
        for ri,row in enumerate(t['rows'][:30]):
            mapped=[]; hits=0
            for v in row:
                key=reverse.get(normalize_header(v)) if v not in (None,'') else None
                mapped.append(key); hits+=int(bool(key))
            if best is None or hits>best[0]: best=(hits,ti,ri,row,mapped)
    if not best or best[0]<3: raise ValueError('模板表头无法识别；请至少包含“箱号/包装号、包装方式、材料/箱内内容”等3个系统字段。')
    return {"hits":best[0],"table_index":best[1],"header_row":best[2]+1,"headers":best[3],"mapped":best[4],"sheet":tables[best[1]]['sheet']}

def _col_letter(idx):
    n=idx+1; s=''
    while n:
        n,r=divmod(n-1,26); s=chr(65+r)+s
    return s

def _xlsx_fill_template(template_bytes,records,alias_map):
    info=_xlsx_export_header_info(template_bytes,alias_map)
    _title,sheet_path=_xlsx_first_sheet_path(template_bytes)
    NS="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ET.register_namespace('',NS)
    with zipfile.ZipFile(io.BytesIO(template_bytes),'r') as zin:
        root=ET.fromstring(zin.read(sheet_path))
        sheet_data=root.find(f'{{{NS}}}sheetData')
        if sheet_data is None: raise ValueError('模板工作表缺少sheetData。')
        header_r=info['header_row']
        style_map={}
        sample_row=None
        for r in list(sheet_data):
            rn=int(r.attrib.get('r','0') or 0)
            if rn>header_r:
                sample_row=r; break
        if sample_row is not None:
            for c in list(sample_row):
                if not c.tag.endswith('}c'): continue
                idx=_xlsx_col_index(c.attrib.get('r','A1'))
                if c.attrib.get('s') is not None: style_map[idx]=c.attrib['s']
        for r in list(sheet_data):
            if int(r.attrib.get('r','0') or 0)>header_r:
                sheet_data.remove(r)
        last_row=header_r
        for ridx,rec in enumerate(records,1):
            rn=header_r+ridx; last_row=rn
            row_el=ET.Element(f'{{{NS}}}row',{'r':str(rn)})
            for ci,key in enumerate(info['mapped']):
                if not key: continue
                val=rec.get(key,'')
                cattrs={'r':f'{_col_letter(ci)}{rn}'}
                if ci in style_map: cattrs['s']=style_map[ci]
                c=ET.SubElement(row_el,f'{{{NS}}}c',cattrs)
                if isinstance(val,(int,float)) and not isinstance(val,bool):
                    v=ET.SubElement(c,f'{{{NS}}}v'); v.text=str(val)
                else:
                    c.set('t','inlineStr'); isel=ET.SubElement(c,f'{{{NS}}}is'); t=ET.SubElement(isel,f'{{{NS}}}t')
                    sval=str(val if val is not None else '')
                    if sval.startswith(' ') or sval.endswith(' ') or '\n' in sval: t.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
                    t.text=sval
            sheet_data.append(row_el)
        dim=root.find(f'{{{NS}}}dimension')
        max_col=max(1,len(info['headers']))
        if dim is not None: dim.set('ref',f'A1:{_col_letter(max_col-1)}{last_row}')
        af=root.find(f'{{{NS}}}autoFilter')
        if af is not None: af.set('ref',f'A{header_r}:{_col_letter(max_col-1)}{last_row}')
        new_sheet=ET.tostring(root,encoding='utf-8',xml_declaration=True)
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as zout:
            for zi in zin.infolist():
                data=new_sheet if zi.filename==sheet_path else zin.read(zi.filename)
                zout.writestr(zi,data)
        return out.getvalue()

def _active_packing_template_path():
    return CUSTOM_PACKING_LIST_TEMPLATE_PATH if CUSTOM_PACKING_LIST_TEMPLATE_PATH.exists() else DEFAULT_PACKING_LIST_TEMPLATE_PATH

def _packing_template_info():
    custom=CUSTOM_PACKING_LIST_TEMPLATE_PATH.exists()
    name='系统默认箱单模板'
    if custom:
        try:
            meta=_json_load(PACKING_LIST_TEMPLATE_META_PATH,{})
            name=meta.get('filename') or '自定义箱单模板.xlsx'
        except Exception: name='自定义箱单模板.xlsx'
    return {'custom':custom,'name':name}


def _active_shipping_mark_template_path():
    return CUSTOM_SHIPPING_MARK_TEMPLATE_PATH if CUSTOM_SHIPPING_MARK_TEMPLATE_PATH.exists() else DEFAULT_SHIPPING_MARK_TEMPLATE_PATH

def _shipping_mark_template_info():
    custom=CUSTOM_SHIPPING_MARK_TEMPLATE_PATH.exists()
    name='BX-5083-014样式_系统默认唛头模板.docx'
    if custom:
        try:
            meta=_json_load(SHIPPING_MARK_TEMPLATE_META_PATH,{})
            name=meta.get('filename') or '自定义唛头模板.docx'
        except Exception:
            name='自定义唛头模板.docx'
    return {'custom':custom,'name':name}

def _validate_shipping_mark_template(path):
    d=Document(path)
    texts=[]
    for p in d.paragraphs: texts.append(p.text)
    for t in d.tables:
        for row in t.rows:
            for cell in row.cells: texts.append(cell.text)
    blob='\n'.join(texts)
    required=['CTN.NO.','箱内货物名称','箱体尺寸','箱体毛重','设备净重']
    missing=[x for x in required if x not in blob]
    if missing: raise ValueError('唛头模板缺少字段：'+', '.join(missing))
    return True

def _mark_set_paragraph(paragraph,text,font_size=12,bold=False):
    paragraph.clear()
    r=paragraph.add_run(str(text or ''))
    r.bold=bold; r.font.size=Pt(font_size)
    r.font.name='Arial'; r._element.rPr.rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
    paragraph.paragraph_format.space_before=Pt(0); paragraph.paragraph_format.space_after=Pt(0); paragraph.paragraph_format.line_spacing=1.0
    return paragraph

def _mark_set_cell(cell,lines,font_size=12,bold=False):
    if not isinstance(lines,(list,tuple)): lines=[str(lines or '')]
    cell.text=''
    for idx,line in enumerate(lines):
        p=cell.paragraphs[0] if idx==0 else cell.add_paragraph()
        _mark_set_paragraph(p,line,font_size,bold)
    cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER

def _short_mark_item(i):
    name=str(i.get('name') or '')
    parts=[x.strip() for x in re.split(r'[｜|]',name) if x and x.strip()]
    cat=parts[0] if parts else '材料'
    # prefer the first explicit DN spec in the source description
    dns=re.findall(r'DN\s*\d+(?:\s*[xX×]\s*\d+)*',name,re.I)
    dn=dns[0].replace(' ','') if dns else ''
    mat=i.get('material_label') or _material_label_from_item_name(name)
    label=' '.join(x for x in (str(mat) if mat and mat!='—' else '',cat,dn) if x)
    return re.sub(r'\s+',' ',label).strip() or cat

def _aggregate_crate_items(citems):
    """Aggregate split placement rows back to material/SKU quantities for documents.
    3D packing may split one source material across several placement rows; shipping marks and
    packing lists must show the material's summed quantity, not individual placement fragments.
    """
    grouped={}
    order=[]
    for i in citems:
        name=str(i.get('name') or '').strip()
        mat=i.get('material_label') or _material_label_from_item_name(name)
        key=(name,str(mat or '—'))
        if key not in grouped:
            grouped[key]=dict(i)
            grouped[key]['qty']=0
            order.append(key)
        grouped[key]['qty']+=int(i.get('qty') or 0)
    return [grouped[k] for k in order]

def _mark_content_lines(citems,max_lines=8):
    grouped={}
    for i in _aggregate_crate_items(citems):
        key=_short_mark_item(i)
        grouped[key]=grouped.get(key,0)+int(i.get('qty') or 0)
    rows=sorted(grouped.items(),key=lambda kv:(-kv[1],kv[0]))
    total=sum(v for _,v in rows)
    out=[f'{k} ×{v}' for k,v in rows[:max_lines]]
    if len(rows)>max_lines:
        out.append(f'另有 {len(rows)-max_lines} 种；共 {len(rows)} 种 / {total} 件，详见箱单')
    else:
        out.append(f'共 {len(rows)} 种 / {total} 件')
    return out

def _mark_lines(value):
    if value is None: return ['']
    rows=[x.strip() for x in str(value).replace('\r','').split('\n') if x.strip()]
    return rows or ['']

def _mark_fill_labeled_value(doc,label_text,lines,font_size=11,bold=False):
    for t in doc.tables:
        for row in t.rows:
            cells=row.cells
            for ci,cell in enumerate(cells):
                if label_text in cell.text and ci+1 < len(cells):
                    _mark_set_cell(cells[ci+1],lines,font_size,bold)
                    return True
    return False

def _fill_compact_mark_layout(doc,crate,citems,common,net,gross,size_cm,contents):
    """Fill the current user-approved compact 8-row shipping-mark layout by position.
    This layout intentionally has no left-side labels for consignee/contract/product cells,
    so label-only replacement would leave stale sample data in the template.
    """
    if not doc.tables:
        return False
    t=doc.tables[0]
    if len(t.rows)<8:
        return False
    try:
        # Row 0: merged CTN.NO.
        _mark_set_cell(t.rows[0].cells[0],[f'CTN.NO. ： {crate["name"]}'],16,True)
        # Row 1: merged consignee
        _mark_set_cell(t.rows[1].cells[0],_mark_lines(common.get('consignee')),10,True)
        # Row 2: merged contract name
        _mark_set_cell(t.rows[2].cells[0],_mark_lines(common.get('sc_name')),13,True)
        # Row 3: contract no | delivery label | delivery date
        _mark_set_cell(t.rows[3].cells[0],_mark_lines(common.get('sc_no')),12,True)
        _mark_set_cell(t.rows[3].cells[1],['发货日期','Delivery date'],11,False)
        _mark_set_cell(t.rows[3].cells[2],_mark_lines(common.get('delivery_date')),12,True)
        # Row 4: product summary | packing label | packing value
        _mark_set_cell(t.rows[4].cells[0],contents,11 if len(contents)<=5 else 9,False)
        _mark_set_cell(t.rows[4].cells[1],['包装形式','Packing'],11,False)
        _mark_set_cell(t.rows[4].cells[2],[common.get('packing_cn','出口木箱'),common.get('packing_en','Export wooden box')],11,False)
        # Row 5: size | gross label | gross value
        _mark_set_cell(t.rows[5].cells[0],[size_cm],12,True)
        _mark_set_cell(t.rows[5].cells[1],['箱体毛重(Kg)','Package G.W.'],11,False)
        _mark_set_cell(t.rows[5].cells[2],[f'{gross:.1f} kg'],12,True)
        # Row 6: shipment | net label | net value
        _mark_set_cell(t.rows[6].cells[0],[common.get('shipment_cn',''),common.get('shipment_en','')],11,False)
        _mark_set_cell(t.rows[6].cells[1],['设备净重(Kg)','Equipment N.W.'],11,False)
        _mark_set_cell(t.rows[6].cells[2],['未知（已包装单元）' if _is_prepacked(crate) else f'{net:.1f} kg'],12,True)
        # Row 7: merged supplier
        _mark_set_cell(t.rows[7].cells[0],[common.get('supplier_cn',''),common.get('supplier_en','')],11,True)
        return True
    except Exception:
        return False

def _fill_shipping_mark_doc(crate,citems,template_path):
    d=Document(template_path)
    common=_shipping_mark_common_load()
    # Always derive quantities/weights from the CURRENT crate state.
    citems=_aggregate_crate_items(citems)
    wr=_crate_weight_result(crate,citems)
    net=wr['cargo_weight']
    gross=wr['gross_weight']
    size_cm=f'{float(crate["length_mm"])/10:g} × {float(crate["width_mm"])/10:g} × {float(crate["height_mm"])/10:g} cm'
    contents=_mark_content_lines(citems,7)
    if _asu_profile()['enabled']:
        key='crate:'+str(crate['id']);record=ASU.records().get(key,{})
        if ASU.verified(key,record,ASU.basis(key)):
            data=record.get('data') or {};contents=[data.get('english_description') or 'DESCRIPTION PENDING']
            if data.get('net_kg') is not None:net=data['net_kg']

    # User-approved compact layout: fill by structural position so stale sample values can never survive.
    if _fill_compact_mark_layout(d,crate,citems,common,net,gross,size_cm,contents):
        for t in d.tables:
            for row in t.rows:
                trPr=row._tr.get_or_add_trPr()
                if not any(x.tag.endswith('}cantSplit') for x in trPr): trPr.append(OxmlElement('w:cantSplit'))
        return d

    # Generic/custom-template fallback: label-based filling.
    ctn_done=False
    for p in d.paragraphs:
        if 'CTN.NO.' in p.text.upper():
            _mark_set_paragraph(p,f'CTN.NO. ： {crate["name"]}',16,True); ctn_done=True; break
    if not ctn_done:
        for t in d.tables:
            for row in t.rows:
                for cell in row.cells:
                    if 'CTN.NO.' in cell.text.upper():
                        _mark_set_cell(cell,[f'CTN.NO. ： {crate["name"]}'],16,True); ctn_done=True; break
                if ctn_done: break
            if ctn_done: break
    _mark_fill_labeled_value(d,'收货单位',_mark_lines(common.get('consignee')),10,True)
    _mark_fill_labeled_value(d,'合同名称',_mark_lines(common.get('sc_name')),13,True)
    _mark_fill_labeled_value(d,'合同号',_mark_lines(common.get('sc_no')),12,True)
    _mark_fill_labeled_value(d,'发货日期',_mark_lines(common.get('delivery_date')),12,True)
    _mark_fill_labeled_value(d,'发货地',[common.get('shipment_cn',''),common.get('shipment_en','')],11,False)
    _mark_fill_labeled_value(d,'供应商',[common.get('supplier_cn',''),common.get('supplier_en','')],11,True)
    for t in d.tables:
        for row in t.rows:
            cells=row.cells
            for ci,cell in enumerate(cells):
                txt=cell.text
                if '箱内货物名称' in txt and ci+1<len(cells): _mark_set_cell(cells[ci+1],contents,11 if len(contents)<=5 else 9,False)
                elif '包装形式' in txt and ci+1<len(cells): _mark_set_cell(cells[ci+1],[common.get('packing_cn','出口木箱'),common.get('packing_en','Export wooden box')],11,False)
                elif '箱体尺寸' in txt and ci+1<len(cells): _mark_set_cell(cells[ci+1],[size_cm],12,True)
                elif '箱体毛重' in txt and ci+1<len(cells): _mark_set_cell(cells[ci+1],[f'{gross:.1f} kg'],12,True)
                elif '设备净重' in txt and ci+1<len(cells): _mark_set_cell(cells[ci+1],['未知（已包装单元）' if _is_prepacked(crate) else f'{net:.1f} kg'],12,True)
    for t in d.tables:
        for row in t.rows:
            trPr=row._tr.get_or_add_trPr()
            if not any(x.tag.endswith('}cantSplit') for x in trPr): trPr.append(OxmlElement('w:cantSplit'))
    return d

def _append_doc_body(dst_doc,src_doc):
    # templates are based on the same document, so image relationship ids are reusable
    dst_doc.add_page_break()
    body=dst_doc.element.body
    for child in list(src_doc.element.body):
        if child.tag.endswith('}sectPr'): continue
        body.insert(len(body)-1,deepcopy(child))

def _write_mark_docx(prefix,doc):
    EXPORT_DIR.mkdir(parents=True,exist_ok=True)
    path=EXPORT_DIR/f'{prefix}_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:6]}.docx'
    doc.save(path); return path

def _worst_grade(vals):
    vals=[str(v or 'C').upper() for v in vals]
    return 'C' if 'C' in vals else ('B' if 'B' in vals else ('A' if vals else '—'))

def _packing_list_records(items,crates):
    rows=[]; seq=1
    for c in sorted(crates,key=lambda x:x['name']):
        citems=[i for i in items if i.get('crate_id')==c['id']]
        if not citems: continue
        qty=sum(int(i.get('qty') or 0) for i in citems)
        wr=_crate_weight_result(c,citems)
        net=wr['cargo_weight']; gross=wr['gross_weight']
        agg_items=_aggregate_crate_items(citems); content='；'.join(f"{i['name']} ×{i['qty']}" for i in agg_items)
        mats=[]
        for i in agg_items:
            m=i.get('material_label') or _material_label_from_item_name(i.get('name'))
            if m not in mats: mats.append(m)
        rows.append({'asu_key':'crate:'+str(c['id']),'seq':seq,'package_no':c['name'],'package_type':'木箱','content':content,'material':'/'.join(mats),'qty':qty,
                     'length_mm':round(float(c['length_mm']),1),'width_mm':round(float(c['width_mm']),1),'height_mm':round(float(c['height_mm']),1),
                     'net_weight':'' if _is_prepacked(c) else round(net,1),'gross_weight':round(gross,1),'size_grade':_worst_grade([i.get('size_grade') for i in citems]),
                     'weight_grade':_worst_grade([i.get('weight_grade') for i in citems]),'remark':('已包装运输单元；设备净重及箱皮未知；箱内校核不适用' if _is_prepacked(c) else f"木箱自重 {wr['effective_tare']:.1f} kg；毛重来源：{wr['gross_source']}")})
        seq+=1
    bare_idx=1
    for i in items:
        if i.get('package_type')=='木箱': continue
        pname=str(i.get('name') or '')
        pno=pname.split('｜')[0].strip() if re.match(r'^(BND-|PK-|BARE-)',pname,re.I) else f'BARE-{bare_idx:03d}'
        bare_idx+=1
        qty=int(i.get('qty') or 1); net=qty*float(i.get('weight_kg') or 0)
        rows.append({'asu_key':'item:'+str(i['id']),'seq':seq,'package_no':pno,'package_type':i.get('package_type') or '裸装','content':pname,
                     'material':i.get('material_label') or _material_label_from_item_name(pname),'qty':qty,
                     'length_mm':round(float(i.get('length_mm') or 0),1),'width_mm':round(float(i.get('width_mm') or 0),1),'height_mm':round(float(i.get('height_mm') or 0),1),
                     'net_weight':round(net,1),'gross_weight':round(net,1),'size_grade':i.get('size_grade') or 'C','weight_grade':i.get('weight_grade') or 'C','remark':'裸装/其它运输包装'})
        seq+=1
    if _asu_profile()['enabled']:
        records=ASU.records();entities=ASU.entities()
        for row in rows:
            key=row['asu_key'];record=records.get(key,{});entity=entities.get(key);data=record.get('data') or {}
            valid=bool(entity and ASU.verified(key,record,ASU.basis(key,entity)))
            for field in ('po','batch','origin','storage_category','packing_date'):row[field]=str(data.get(field) or '')
            if valid:
                row['content']=data.get('english_description') or row['content']
                if data.get('net_kg') is not None:row['net_weight']=data['net_kg']*(row['qty'] if key.startswith('item:') else 1)
                row['remark']='ASU INPUT RECORD; '+('SPARE PARTS; ' if data.get('purpose') in ('capital','commissioning','two_year_spare') else '')+('CONSTRUCTION TOOLS / CONSTRUCTION EQUIPMENT FOR TEMPORARY USE AND RE-EXPORT; ' if data.get('purpose')=='tools' else '')+'PO '+row['po']+'; BATCH '+row['batch']+'; '+str(data.get('approval_ref') or '')
            else:row['remark']='DRAFT / ASU INPUTS PENDING; '+row['remark']
    return rows

def _package_detail_for_name(name,ptype,items,crates):
    crate=next((c for c in crates if c['name']==name),None)
    if crate:
        citems=[i for i in items if i.get('crate_id')==crate['id']]
        return {'content':'；'.join(f"{i['name']} ×{i['qty']}" for i in citems),
                'material':'/'.join(dict.fromkeys((i.get('material_label') or _material_label_from_item_name(i.get('name'))) for i in citems)),
                'qty':sum(int(i.get('qty') or 0) for i in citems)}
    # Bare-package names may get a -1/-2 suffix when quantity is expanded for packing.
    matches=[i for i in items if i.get('package_type')!='木箱' and (name==i.get('name') or str(name).startswith(str(i.get('name'))+'-'))]
    if matches:
        i=max(matches,key=lambda x:len(str(x.get('name') or '')))
        return {'content':i.get('name'),'material':i.get('material_label') or _material_label_from_item_name(i.get('name')),'qty':1}
    return {'content':name,'material':'—','qty':1}

def _container_load_records(plan,container_index,items,crates):
    boxes=plan.get('containers',[])
    selected=boxes if int(container_index)==0 else [b for b in boxes if int(b.get('index',0))==int(container_index)]
    rows=[]; seq=1
    for b in selected:
        for q in b.get('placements',[]):
            d=_package_detail_for_name(q.get('name',''),q.get('type',''),items,crates)
            rows.append({'seq':seq,'scheme':plan.get('label') or plan.get('code'),'container_type':b.get('container_code'),'container_no':b.get('index'),
                         'package_no':q.get('name'),'package_type':q.get('type') or '包装单元','content':d['content'],'material':d['material'],'qty':d['qty'],
                         'size':f"{float(q.get('l',0)):.0f}×{float(q.get('w',0)):.0f}×{float(q.get('h',0)):.0f}",
                         'weight':round(float(q.get('weight_kg') or 0),1),'x':round(float(q.get('x') or 0),1),'y':round(float(q.get('y') or 0),1),'z':round(float(q.get('z') or 0),1),
                         'remark':'木箱箱号' if (q.get('type')=='木箱') else '裸装/其它包装材料'})
            seq+=1
        for a in b.get('flexible_fill',[]):
            rows.append({'seq':seq,'scheme':plan.get('label') or plan.get('code'),'container_type':b.get('container_code'),'container_no':b.get('index'),'package_no':a['name'],'package_type':'柔性袋装体积分配','content':a['name'],'material':'膨胀珍珠岩','qty':a['quantity'],'size':'体积分配 '+str(a['volume_m3'])+' m³','weight':a['weight_kg'],'x':'','y':'','z':'','remark':'按每m³8包；不是逐包定位；细缝可达性、设备顶载/承压、门挡及整柜重心待复核'})
            seq+=1
    return rows

def _cleanup_exports(max_files=200,max_age_days=30):
    try:
        files=sorted([x for x in EXPORT_DIR.glob('*') if x.is_file()],key=lambda x:x.stat().st_mtime,reverse=True)
        cutoff=time.time()-max_age_days*86400
        for i,x in enumerate(files):
            if i>=max_files or x.stat().st_mtime<cutoff:
                try: x.unlink()
                except Exception: pass
    except Exception: pass

def _write_export_file(prefix,data,suffix=".xlsx"):
    EXPORT_DIR.mkdir(parents=True,exist_ok=True); _cleanup_exports()
    p=EXPORT_DIR/f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}{suffix}"
    p.write_bytes(data); return p

@app.get('/packing-list-template/default.xlsx')
def download_default_packing_list_template():
    return FileResponse(DEFAULT_PACKING_LIST_TEMPLATE_PATH,filename='Packing_List_系统默认模板.xlsx')

@app.post('/packing-list-template/upload')
async def upload_packing_list_template(file:UploadFile=File(...)):
    try:
        raw=await file.read()
        if len(raw)>5*1024*1024: raise ValueError('模板超过5MB。')
        if not (len(raw)>4 and raw[:2]==b'PK'): raise ValueError('请上传.xlsx格式箱单模板。')
        info=_xlsx_export_header_info(raw,PACKING_EXPORT_ALIASES)
        if 'package_no' not in info['mapped']:
            raise ValueError('模板至少需要“箱号/包装号”列。')
        CUSTOM_PACKING_LIST_TEMPLATE_PATH.write_bytes(raw)
        _json_save(PACKING_LIST_TEMPLATE_META_PATH,{'filename':file.filename or '自定义箱单模板.xlsx','uploaded_at':time.strftime('%Y-%m-%d %H:%M:%S')})
        return RedirectResponse(url='/?message='+quote(f'箱单模板上传成功：{file.filename}。后续“生成箱单”将使用此模板。'),status_code=303)
    except Exception as e:
        return RedirectResponse(url='/?error='+quote('箱单模板上传失败：'+str(e)),status_code=303)

@app.post('/packing-list-template/reset')
def reset_packing_list_template():
    for p in (CUSTOM_PACKING_LIST_TEMPLATE_PATH,PACKING_LIST_TEMPLATE_META_PATH):
        try:
            if p.exists(): p.unlink()
        except Exception: pass
    return RedirectResponse(url='/?message='+quote('已恢复系统默认箱单模板。'),status_code=303)

@app.get('/export-packing-list')
def export_packing_list_legacy():
    return JSONResponse({'ok':False,'error':'V5.8.28已停用直接URL箱单导出。请从页面选择“规划版”或“箱级数据对照版”，服务器将执行对应门禁。'},status_code=409)

@app.post('/export-packing-list')
async def export_packing_list(request:Request):
    try:
        payload=await request.json()
        mode=str(payload.get('mode') or 'planning').lower()
        if mode not in ('planning','formal'):
            return JSONResponse({'ok':False,'error':'导出模式无效。'},status_code=400)
        items,crates=fetch_state(); records=_packing_list_records(items,crates)
        if not records: return JSONResponse({'ok':False,'error':'当前没有可生成箱单的数据。'},status_code=400)
        quality=_data_quality_release_issues(items,crates)
        if mode=='formal' and quality:
            return JSONResponse({'ok':False,'error':'箱级数据对照版导出被阻止：'+'；'.join(quality),'issues':quality},status_code=409)
        label=BOX_DATA_LABEL if mode=='formal' else 'DRAFT_规划版_非签证依据'
        if mode=='planning' and quality:
            note='；'.join(quality[:8])
            for r in records: r['remark']=(r.get('remark') or '')+'；DRAFT待确认：'+note
        else:
            meta=_release_meta_load(); approval_note=f"项目={meta.get('project_name','')}；单一责任确认人={meta.get('approver','')}"
            for r in records: r['remark']=(r.get('remark') or '')+'；'+label+'；'+approval_note
        tpath=_active_packing_template_path(); raw=tpath.read_bytes()
        xlsx=_xlsx_fill_template(raw,records,PACKING_EXPORT_ALIASES)
        out=_write_export_file('Packing_List',xlsx)
        filename=label+'_装箱单.xlsx'
        if mode=='formal': _record_release_export('packing_list',out,items,crates,{'mode':mode,'filename':filename})
        return FileResponse(out,filename=filename,headers={'X-Packing-Plan-Mode':mode})
    except Exception as e:
        logger.exception('export packing list failed')
        return JSONResponse({'ok':False,'error':'生成箱单失败：'+str(e)},status_code=500)

@app.post('/container-layout/dirty')
async def mark_container_layout_dirty(request:Request):
    try:
        payload=await request.json(); scheme=str(payload.get('scheme') or ''); idx=int(payload.get('container_index') or 0)
        if not scheme or idx<=0: return JSONResponse({'ok':False,'error':'方案或柜号无效。'},status_code=400)
        con=db(); con.execute('INSERT OR REPLACE INTO container_layout_drafts(scheme_code,container_index,dirty,updated_at) VALUES(?,?,1,?)',(scheme,idx,time.strftime('%Y-%m-%d %H:%M:%S'))); con.commit(); con.close()
        return JSONResponse({'ok':True})
    except Exception as e:
        return JSONResponse({'ok':False,'error':str(e)},status_code=500)

def _draft_rows(scheme=None):
    con=db()
    if scheme:
        rows=con.execute('SELECT * FROM container_layout_drafts WHERE dirty=1 AND scheme_code=?',(scheme,)).fetchall()
    else:
        rows=con.execute('SELECT * FROM container_layout_drafts WHERE dirty=1').fetchall()
    con.close(); return [dict(r) for r in rows]

def _data_quality_release_issues(items,crates,asu_split=False):
    """V5.1 single-responsibility data gate with strict evidence-object matching."""
    try:
        con=db(); audit_rows=con.execute('SELECT crate_id,audit_status FROM crate_audits').fetchall(); con.close()
        crate_audit_status={int(r['crate_id']):str(r['audit_status'] or '未校核') for r in audit_rows}
    except Exception:
        crate_audit_status={}
    issues=_release_data_issues(items,crates,crate_audit_status,_evidence_matches,_evidence_summary,_release_meta_load())
    if _asu_profile()["enabled"]:
        issues+=ASU.gate()
        if not asu_split:
            records=ASU.records();keys={((records.get(p.get('asu_key'),{}).get('data') or {}).get('po'),(records.get(p.get('asu_key'),{}).get('data') or {}).get('batch')) for p in get_packages(items,crates)}
            if len(keys)>1:issues.append('ASU不同订单/批次必须分单，请使用项目标准页的按订单批次导出。')
    return issues

def _formal_export_issues(plan,scheme):
    if plan.get('flexible_volume_only'):return ['柔性袋装仅完成体积分配，逐包几何、装载通道、顶载/承压、系固及整柜重心未闭环；仅允许规划草案输出']
    if any(b.get('vehicle_type')=='truck' for b in plan.get('containers',[])):return TRUCKS.formal_issues(plan,scheme)
    issues=[]
    if plan.get('partial') or plan.get('unplaced_count'):issues.append('所选集装箱未全部装入货物，请查看未装清单，增加柜数或调整柜型后重新排箱')
    cstate=_container_specs_state()
    cmeta=cstate.get('meta') or _container_specs_meta_load()
    if cstate.get('mode')!='actual':
        if cstate.get('mode')=='invalid': issues.append('当前实际柜参数无效：'+str(cstate.get('error') or '未知错误'))
        else: issues.append('当前处于标准柜规划模式，未启用实际柜参数')
    container_evidence_ok=bool(cmeta.get('confirmed')) and bool(cmeta.get('evidence_id')) and _evidence_matches(cmeta.get('evidence_id'),scope_type='project',scope_key=str(cmeta.get('reference_no') or ''),evidence_types=('container_certificate_or_booking',),entity_types=('container_specs',),entity_ids=(str(cmeta.get('reference_no') or ''),),require_verified=True)
    if container_evidence_ok:
        ev=_evidence_summary(cmeta.get('evidence_id'))
        try: claim=json.loads(ev.get('claimed_values_json') or '{}')
        except Exception: claim={}
        claim_ref=str(claim.get('reference_no') or '')
        claim_csv=str(claim.get('container_csv_sha256') or '')
        claim_specs=str(claim.get('container_specs_sha256') or '')
        meta_specs=str(cmeta.get('specs_sha256') or '')
        try:
            current_specs=str(cstate.get('sha256') or '') if cstate.get('mode')=='actual' else ''
            if not current_specs: raise ValueError(cstate.get('error') or '当前实际柜参数不是有效的实柜模式')
        except Exception as e:
            current_specs=''
            container_evidence_ok=False
            issues.append('当前实际柜计算参数文件无效：'+str(e))
        if claim_ref != str(cmeta.get('reference_no') or '') or claim_csv != str(cmeta.get('csv_sha256') or ''):
            container_evidence_ok=False
            issues.append('实际柜原始CSV或参考编号已与原凭证登记状态不一致，需重新登记凭证')
        if not claim_specs or not meta_specs:
            container_evidence_ok=False
            issues.append('实际柜参数缺少V5.8.28规范参数哈希；请重新上传并绑定凭证')
        elif claim_specs != meta_specs or current_specs != meta_specs:
            container_evidence_ok=False
            issues.append('当前参与计算的实际柜参数已与确认时参数不一致，需重新上传并绑定凭证')
    if not container_evidence_ok:
        issues.append('装柜数据对照版未绑定当前有效的实际柜参数凭证')
    if not str(cmeta.get('reference_no') or '').strip():
        issues.append('实际柜参数未绑定箱号/订舱/箱证参考编号')
    dirty=_draft_rows(scheme)
    if dirty: issues.append('存在未保存的手动坐标草稿：'+','.join(f"第{r['container_index']}柜" for r in dirty[:6]))
    invalid=plan.get('invalid_manual_layouts') or []
    if invalid: issues.append('存在已失效的已保存手调方案：'+','.join(f"第{x.get('container_index')}柜" for x in invalid[:6]))
    for b in plan.get('containers') or []:
        st=str((b.get('engineering_audit') or {}).get('status') or '未校核')
        # V5.5: HARD FAIL / HIGH RISK remain blocking. ENGINEERING REVIEW may be exported,
        # but the output is explicitly marked so the single responsible person can close the item.
        if st not in ('通过','需复核'):
            issues.append(f"第{b.get('index')}柜规划校核状态={st}")
    if _asu_profile()['enabled']:
        issues += [f"ASU {r['entity']}：{r['message']}（第{r['page']}页）" for r in ASU.container_checks(plan)]
    return issues

def _formal_export_review_notes(plan):
    out=[]
    for b in plan.get('containers') or []:
        a=b.get('engineering_audit') or {}
        if str(a.get('status') or '')!='需复核': continue
        notes=a.get('engineering_review_notes') or []
        text='；'.join(str(x) for x in notes[:12]) or str(a.get('note') or '存在工程复核事项')
        out.append(f"第{b.get('index')}柜：{text}")
    return out

def _physical_evidence_review_notes(items,crates):
    """Formal-output trust layer only. It does not alter solver feasibility or DRAFT planning."""
    out=[]
    for i in items or []:
        if not (bool(i.get('cg_confirmed')) and _physical_source_verified(i.get('cg_source_type'))):
            continue
        iid=str(i.get('id') or '')
        eid=i.get('cg_evidence_id')
        linked=_evidence_matches(eid,scope_type='item',scope_key=iid,evidence_types=('item_cg_record',),entity_types=('item',),entity_ids=(iid,),require_verified=True)
        ok=bool(linked and evidence_claim_matches_current(_evidence_summary(eid),i,'item_cg')[0])
        if not ok: out.append(f"{i.get('name') or '物料'} CG虽标记为{_physical_source_label(i.get('cg_source_type'))}，但未绑定当前有效CG凭证/人工核对值")
    for c in crates or []:
        cid=str(c.get('id') or ''); name=str(c.get('name') or '木箱')
        if bool(c.get('top_load_confirmed')) and _physical_source_verified(c.get('top_load_source_type')) and c.get('max_top_load_kg') not in (None,''):
            eid=c.get('top_load_evidence_id'); linked=_evidence_matches(eid,scope_type='item',scope_key=cid,evidence_types=('crate_top_load_record',),entity_types=('crate',),entity_ids=(cid,),require_verified=True)
            if not (linked and evidence_claim_matches_current(_evidence_summary(eid),c,'crate_top_load')[0]): out.append(f'{name} 顶载已作为确认输入，但未绑定当前有效顶载凭证/人工核对值')
        if str(c.get('contact_area_mode') or 'UNSPECIFIED').upper()!='UNSPECIFIED' and _physical_source_verified(c.get('contact_area_source_type')):
            eid=c.get('contact_area_evidence_id'); linked=_evidence_matches(eid,scope_type='item',scope_key=cid,evidence_types=('crate_contact_area_record',),entity_types=('crate',),entity_ids=(cid,),require_verified=True)
            if not (linked and evidence_claim_matches_current(_evidence_summary(eid),c,'crate_contact_area')[0]): out.append(f'{name} 有效接触面积已作为确认输入，但未绑定当前有效接触面积凭证/人工核对值')
        if bool(c.get('lashing_required')) and bool(c.get('lashing_confirmed')) and int(c.get('lashing_points') or 0)>0:
            eid=c.get('lashing_evidence_id'); linked=_evidence_matches(eid,scope_type='item',scope_key=cid,evidence_types=('crate_lashing_plan',),entity_types=('crate',),entity_ids=(cid,),require_verified=True)
            if not (linked and evidence_claim_matches_current(_evidence_summary(eid),c,'crate_lashing')[0]): out.append(f'{name} 系固点/方案已标记确认，但未绑定当前有效系固图/方案凭证')
    return list(dict.fromkeys(out))

def _all_formal_review_notes(plan,items,crates):
    return list(dict.fromkeys(_formal_export_review_notes(plan)+_physical_evidence_review_notes(items,crates)))

def _container_output_label(plan,items=None,crates=None):
    reviews=_formal_export_review_notes(plan)
    if items is not None and crates is not None: reviews += _physical_evidence_review_notes(items,crates)
    return CONTAINER_DATA_REVIEW_LABEL if reviews else CONTAINER_DATA_PASS_LABEL

def _shipment_output_label(plan,items=None,crates=None):
    reviews=_formal_export_review_notes(plan)
    if items is not None and crates is not None: reviews += _physical_evidence_review_notes(items,crates)
    return SHIPMENT_PACKAGE_REVIEW_LABEL if reviews else SHIPMENT_PACKAGE_PASS_LABEL

def _preview_submitted_layout(payload):
    scheme=str(payload.get('scheme') or '');idx=int(payload.get('container_index') or 0)
    items,crates=fetch_state();rec=recommend_containers(items,crates,deep=bool(payload.get('deep')))
    plan=(rec.get('plans') or {}).get(scheme)
    base=next((b for b in (plan or {}).get('containers',[]) if int(b['index'])==idx),None)
    if not base:raise ValueError('当前方案已变化，请刷新页面')
    if base.get('flexible_fill'):raise ValueError('柔性体积分配的设备坐标须重新规划，不支持独立微调')
    submitted=payload.get('placements') or [];bykey={(int(q['seq']),str(q['name'])):q for q in submitted}
    if len(bykey)!=len(submitted) or len(bykey)!=len(base['placements']):raise ValueError('坐标与当前货物清单不一致')
    box=copy.deepcopy(base)
    for q in box['placements']:
        row=bykey.get((int(q['seq']),str(q['name'])))
        if row is None:raise ValueError('坐标缺少当前货物')
        for axis in ('x','y','z'):q[axis]=round(checked_number(row.get(axis),'坐标'+axis),3)
    audit=_audit_manual_container_box(box);box['engineering_audit']=audit
    return box,audit


@app.post('/container-layout/validate')
async def validate_container_layout(request:Request):
    try:
        box,audit=_preview_submitted_layout(await request.json())
        return JSONResponse({'ok':audit['status']!='不通过','audit':audit,'box':box})
    except (ValueError,KeyError,TypeError) as exc:
        return JSONResponse({'ok':False,'error':str(exc)},status_code=422)


def _build_layout_word_bytes(job):
    import subprocess,tempfile
    from app.report_runtime import paths as report_runtime_paths
    python,node,modules=report_runtime_paths()
    with tempfile.TemporaryDirectory(prefix='layout_word_',dir=DATA_DIR) as temp:
        work=Path(temp);source=work/'job.json';source.write_text(json.dumps(job,ensure_ascii=False))
        output=work/'layout.docx'
        result=subprocess.run([python,'-m','app.word_report','--input',str(source),'--output',str(output),'--work',str(work/'images'),'--node',node,'--modules',modules],cwd=BASE_DIR,env=dict(os.environ,PYTHONPATH=str(BASE_DIR),PYTHONDONTWRITEBYTECODE='1'),check=False,capture_output=True,timeout=300)
        if result.returncode:raise RuntimeError('Word worker failed: '+result.stderr.decode(errors='replace')[-5000:])
        return output.read_bytes()


@app.post('/export-container-layout-word')
async def export_container_layout_word(request:Request):
    import asyncio
    try:
        payload=await request.json()
        if not isinstance(payload,dict):raise ValueError('导出参数无效')
        scheme=str(payload.get('scheme') or '');mode=str(payload.get('mode') or 'planning')
        if mode not in ('planning','formal'):raise ValueError('导出模式无效')
        items,crates=fetch_state();source_issues=_shipment_source_state(items)['issues']
        if source_issues:return JSONResponse({'ok':False,'error':'清单来源核对未通过：'+'；'.join(source_issues)},status_code=409)
        rec=_apply_saved_container_layouts(recommend_containers(items,crates,deep=bool(payload.get('deep'))))
        plan=(rec.get('plans') or {}).get(scheme)
        if not plan or not plan.get('containers'):raise ValueError('所选方案不存在或暂无可导出的集装箱，请刷新')
        issues=[]
        if _draft_rows(scheme):issues.append('当前方案存在未保存的坐标草稿')
        if plan.get('invalid_manual_layouts'):issues.append('已保存布局失效，请重新保存或恢复自动排布')
        if mode=='formal':issues+=_formal_export_issues(plan,scheme)+_data_quality_release_issues(items,crates)
        if issues:return JSONResponse({'ok':False,'error':'Word导出被阻止：'+'；'.join(dict.fromkeys(issues))},status_code=409)
        label=_container_output_label(plan,items,crates) if mode=='formal' else '规划版 DRAFT'
        meta=_release_meta_load() or {};snapshot=hashlib.sha256(json.dumps(plan,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        job={'plan':copy.deepcopy(plan),'mode_label':label,'planning_only':mode=='planning','project_name':str(meta.get('project_name') or ''),'generated_at':time.strftime('%Y-%m-%d %H:%M:%S'),'snapshot_id':snapshot,'source':_shipment_source_state(items),'outside_plan':copy.deepcopy(rec.get('oversize') or [])}
        raw=await asyncio.to_thread(_build_layout_word_bytes,job)
        out=_write_export_file('Container_Layout_Word',raw,suffix='.docx')
        if mode=='formal':_record_release_export('container_layout_word',out,items,crates,{'scheme':scheme,'containers':len(plan['containers']),'snapshot_id':snapshot,'lifting':False})
        name=re.sub(r'[\/\r\n]+','_',str(plan.get('label') or scheme))
        filename=f'{label}_{name}_全部{len(plan["containers"])}柜_装箱排布.docx'
        return FileResponse(out,filename=filename,media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'X-Packing-Plan-Mode':mode,'X-Packing-Plan-Snapshot':snapshot})
    except (ValueError,KeyError,TypeError) as exc:
        return JSONResponse({'ok':False,'error':str(exc)},status_code=422)
    except Exception:
        logger.exception('All-container Word export failed')
        return JSONResponse({'ok':False,'error':'生成Word文件失败，请查看本机日志后重试。'},status_code=500)


@app.post('/export-container-svg')
async def export_container_svg(request:Request):
    from app.svg_exports import render_svg
    try:
        payload=await request.json();scheme=str(payload.get('scheme') or '');idx=int(payload.get('container_index') or 0)
        mode=str(payload.get('mode') or 'planning')
        if mode not in ('planning','formal'):raise ValueError('导出模式无效')
        items,crates=fetch_state();source_issues=_shipment_source_state(items)['issues']
        if source_issues:return JSONResponse({'ok':False,'error':'清单来源核对未通过：'+'；'.join(source_issues)},status_code=409)
        rec=_apply_saved_container_layouts(recommend_containers(items,crates,deep=bool(payload.get('deep'))))
        plan=(rec.get('plans') or {}).get(scheme)
        if not plan:raise ValueError('所选方案不存在，请刷新')
        issues=[]
        if _draft_rows(scheme):issues.append('存在未保存的坐标草稿')
        if plan.get('invalid_manual_layouts'):issues.append('已保存布局失效，需恢复或重新保存')
        if mode=='formal':issues+=_formal_export_issues(plan,scheme)+_data_quality_release_issues(items,crates)
        if issues:return JSONResponse({'ok':False,'error':'图片导出被阻止：'+'；'.join(dict.fromkeys(issues))},status_code=409)
        box=next((b for b in plan['containers'] if b['index']==idx),None)
        if not box:raise ValueError('所选柜号不存在')
        label=_container_output_label(plan,items,crates) if mode=='formal' else 'DRAFT / 规划版 / 非签证依据'
        if _all_formal_review_notes(plan,items,crates):label+=' / 工程复核未关闭 NOT FOR RELEASE'
        lifting=payload.get('kind')=='lifting';view=str(payload.get('view') or 'perspective')
        if view=='free':view='perspective'
        raw=render_svg(box,view,lifting,payload.get('lifting') or {},label,'V5.8.28 · '+time.strftime('%Y-%m-%d %H:%M:%S'))
        out=_write_export_file('Container_Diagram',raw)
        if mode=='formal':_record_release_export('container_svg',out,items,crates,{'scheme':scheme,'container_index':idx,'view':view,'lifting':lifting})
        filename=('DRAFT_规划版' if mode=='planning' else _container_output_label(plan,items,crates))+f'_第{idx}柜_'+('吊点示意_未确认' if lifting else {'perspective':'三维','top':'俯视','front':'侧视','side':'端视'}[view])+'_装柜图.svg'
        return FileResponse(out,filename=filename,media_type='image/svg+xml',headers={'X-Packing-Plan-Mode':mode})
    except (ValueError,KeyError,TypeError) as exc:
        return JSONResponse({'ok':False,'error':str(exc)},status_code=422)


@app.post('/container-layout/save')
async def save_container_layout(request:Request):
    try:
        payload=await request.json(); scheme=str(payload.get('scheme') or ''); idx=int(payload.get('container_index') or 0)
        if not scheme or idx<=0: return JSONResponse({'ok':False,'error':'方案或柜号无效。'},status_code=400)
        items,crates=fetch_state(); rec=recommend_containers(items,crates,deep=bool(payload.get('deep')))
        plan=(rec.get('plans') or {}).get(scheme)
        if not plan: return JSONResponse({'ok':False,'error':'当前自动方案已变化，请刷新页面后重新微调。'},status_code=409)
        base=next((b for b in plan.get('containers',[]) if int(b.get('index') or 0)==idx),None)
        if not base: return JSONResponse({'ok':False,'error':'找不到所选集装箱。'},status_code=404)
        if base.get('flexible_fill'):return JSONResponse({'ok':False,'error':'此柜含柔性袋装体积分配，调整设备须重新计算体积，暂不接受独立坐标微调。'},status_code=409)
        submitted=payload.get('placements') or []; bykey={(int(x.get('seq')),str(x.get('name'))):x for x in submitted if x.get('seq') is not None}
        if len(submitted)!=len(bykey) or len(bykey)!=len(base.get('placements') or []): return JSONResponse({'ok':False,'error':'货物数量与当前自动方案不一致，请刷新后重试。'},status_code=409)
        box=copy.deepcopy(base)
        for q in box.get('placements') or []:
            x=bykey.get((int(q.get('seq')),str(q.get('name'))))
            if not x: return JSONResponse({'ok':False,'error':f'缺少货物坐标：{q.get("name")}'},status_code=400)
            q['x']=round(checked_number(x.get('x'),'坐标X'),3); q['y']=round(checked_number(x.get('y'),'坐标Y'),3); q['z']=round(checked_number(x.get('z'),'坐标Z'),3)
        audit=_audit_manual_container_box(box)
        if audit.get('status')=='不通过': return JSONResponse({'ok':False,'error':'后端规划校核不通过：'+str(audit.get('note') or ''),'audit':audit},status_code=400)
        positions=[{'seq':int(q.get('seq')),'name':str(q.get('name')),'x':q.get('x'),'y':q.get('y'),'z':q.get('z')} for q in box.get('placements') or []]
        now=time.strftime('%Y-%m-%d %H:%M:%S'); con=db()
        sql='INSERT OR REPLACE INTO container_manual_layouts(scheme_code,container_index,base_signature,container_code,positions_json,audit_json,saved_at) VALUES(?,?,?,?,?,?,?)'
        con.execute(sql,(scheme,idx,_container_box_signature(base),str(base.get('container_code') or ''),json.dumps(positions,ensure_ascii=False),json.dumps(audit,ensure_ascii=False),now))
        con.execute('DELETE FROM container_layout_drafts WHERE scheme_code=? AND container_index=?',(scheme,idx))
        con.commit(); con.close()
        box['engineering_audit']=audit; box['manual_saved']=True; box['manual_saved_at']=now
        return JSONResponse({'ok':True,'message':'当前柜方案已保存到数据库并通过后端规划校核。','box':box,'audit':audit})
    except Exception as e:
        logger.exception('save manual container layout failed'); return JSONResponse({'ok':False,'error':'保存失败：'+str(e)},status_code=500)

@app.post('/container-layout/reset')
async def reset_container_layout(request:Request):
    try:
        payload=await request.json(); scheme=str(payload.get('scheme') or ''); idx=int(payload.get('container_index') or 0); deep=bool(payload.get('deep'))
        con=db(); con.execute('DELETE FROM container_manual_layouts WHERE scheme_code=? AND container_index=?',(scheme,idx)); con.execute('DELETE FROM container_layout_drafts WHERE scheme_code=? AND container_index=?',(scheme,idx)); con.commit(); con.close()
        items,crates=fetch_state(); rec=recommend_containers(items,crates,deep=deep); plan=(rec.get('plans') or {}).get(scheme)
        box=next((copy.deepcopy(b) for b in (plan or {}).get('containers',[]) if int(b.get('index') or 0)==idx),None)
        if box: box['manual_saved']=False
        return JSONResponse({'ok':True,'message':'已恢复为自动排布；数据库中的手动方案已删除。','box':box})
    except Exception as e:
        return JSONResponse({'ok':False,'error':'恢复失败：'+str(e)},status_code=500)

@app.get('/export-container-load-list')
def export_container_load_list_legacy():
    return JSONResponse({'ok':False,'error':'V5.8.28已停用直接URL导出。请从页面按钮导出，服务器会重新核对当前方案状态。'},status_code=409)

@app.post('/export-container-load-list')
async def export_container_load_list(request:Request):
    try:
        payload=await request.json(); scheme=str(payload.get('scheme') or ''); container_index=int(payload.get('container_index') or 0); deep=bool(payload.get('deep')); mode=str(payload.get('mode') or 'planning')
        items,crates=fetch_state();source_issues=_shipment_source_state(items)['issues']
        if source_issues:return JSONResponse({'ok':False,'error':'清单来源核对未通过：'+'；'.join(source_issues)},status_code=409)
        rec=_apply_saved_container_layouts(recommend_containers(items,crates,deep=deep)); plans=rec.get('plans') or {}; plan=plans.get(scheme)
        if not plan: return JSONResponse({'ok':False,'error':'没有可导出的集装箱方案。'},status_code=400)
        if mode=='formal':
            issues=_formal_export_issues(plan,scheme)+_data_quality_release_issues(items,crates)
            if issues: return JSONResponse({'ok':False,'error':'装柜数据对照版导出被阻止：'+'；'.join(issues),'issues':issues},status_code=409)
        else:
            # Planning export is still blocked when browser/server already knows a dirty draft exists.
            dirty=_draft_rows(scheme)
            if dirty: return JSONResponse({'ok':False,'error':'存在未保存手调草稿，请先保存/复核或恢复自动排布后再导出。'},status_code=409)
            if plan.get('invalid_manual_layouts'): return JSONResponse({'ok':False,'error':'存在已失效的手调方案，禁止静默回退导出；请恢复自动排布或重新保存。'},status_code=409)
        rows=_container_load_records(plan,container_index,items,crates)
        if not rows: return JSONResponse({'ok':False,'error':'所选集装箱没有装载记录。'},status_code=400)
        label=_container_output_label(plan,items,crates) if mode=='formal' else 'DRAFT_规划版_非签证依据'
        meta=_release_meta_load(); approval_note=f"；项目={meta.get('project_name','')}；单一责任确认人={meta.get('approver','')}" if mode=='formal' else ''
        review_notes=_all_formal_review_notes(plan,items,crates) if mode=='formal' else []
        review_mark=('；含工程复核事项（非硬失败）：'+' | '.join(review_notes[:8])) if review_notes else ''
        for r in rows: r['remark']=(r.get('remark') or '')+'；'+label+approval_note+review_mark
        xlsx=_xlsx_fill_template(truck_model.load_template(CONTAINER_LOAD_TEMPLATE_PATH.read_bytes(),plan),rows,CONTAINER_EXPORT_ALIASES); out=_write_export_file('Container_Load_List',xlsx)
        unit='辆' if any(b.get('vehicle_type')=='truck' for b in plan['containers']) else '柜'
        suffix=('全部货车' if unit=='辆' else '全部柜') if container_index==0 else f'第{container_index}{unit}'; filename=f"{label}_{plan.get('label') or plan.get('code')}_{suffix}_装载清单.xlsx"
        if mode=='formal': _record_release_export('container_load_list',out,items,crates,{'mode':mode,'scheme':scheme,'container_index':container_index,'filename':filename})
        return FileResponse(out,filename=filename,headers={'X-Packing-Plan-Mode':mode})
    except Exception as e:
        logger.exception('export container load list failed'); return JSONResponse({'ok':False,'error':'生成集装箱装载清单失败：'+str(e)},status_code=500)

@app.post('/shipping-mark-common')
def save_shipping_mark_common(consignee:str=Form(''),sc_name:str=Form(''),sc_no:str=Form(''),delivery_date:str=Form(''),packing_cn:str=Form('出口木箱'),packing_en:str=Form('Export wooden box'),shipment_cn:str=Form(''),shipment_en:str=Form(''),supplier_cn:str=Form(''),supplier_en:str=Form('')):
    _shipping_mark_common_save({'consignee':consignee.strip(),'sc_name':sc_name.strip(),'sc_no':sc_no.strip(),'delivery_date':delivery_date.strip(),'packing_cn':packing_cn.strip() or '出口木箱','packing_en':packing_en.strip() or 'Export wooden box','shipment_cn':shipment_cn.strip(),'shipment_en':shipment_en.strip(),'supplier_cn':supplier_cn.strip(),'supplier_en':supplier_en.strip()})
    return RedirectResponse(url='/?message='+quote('唛头公共信息已保存；后续单箱/批量唛头将统一使用这些内容。'),status_code=303)

def _unloaded_packing_records(items,crates,rec,scheme=''):
    plans=rec.get('plans') or {}; plan=plans.get(scheme) if scheme else None
    if plan is None and rec.get('options'): plan=plans.get(rec['options'][0].get('code'))
    loaded=set()
    if plan:
        for b in plan.get('containers',[]):
            for q in b.get('placements',[]): loaded.add(str(q.get('name') or ''))
            for a in b.get('flexible_fill',[]):loaded.update(a['names'])
    from collections import Counter
    loaded_units=Counter((q.get('asu_key'),str(q.get('name') or '')) for b in (plan or {}).get('containers',[]) for q in b.get('placements',[]))
    rows=[]; seq=1
    for p in get_packages(items,crates):
        if (plan or {}).get('code') in ('USER_SELECTED','TRUCK_SELECTED'):
            identity=(p.get('asu_key'),str(p.get('name') or ''))
            if loaded_units[identity]>0:
                loaded_units[identity]-=1;continue
        elif str(p.get('name') or '') in loaded: continue
        d=_package_detail_for_name(p.get('name',''),p.get('type',''),items,crates)
        rows.append({'seq':seq,'package_no':p.get('name'),'package_type':p.get('type') or '包装单元','content':d['content'],'material':d['material'],'qty':d['qty'],'length_mm':round(float(p.get('length_mm') or 0),1),'width_mm':round(float(p.get('width_mm') or 0),1),'height_mm':round(float(p.get('height_mm') or 0),1),'net_weight':round(float(p.get('weight_kg') or 0),1),'gross_weight':round(float(p.get('weight_kg') or 0),1),'size_grade':'—','weight_grade':'—','remark':'未进入当前推荐集装箱方案；请复核OOG/专项运输或重新装柜'}); seq+=1
    for i in items:
        if i.get('package_type')=='木箱' and not i.get('crate_id'):
            qty=int(i.get('qty') or 1); net=qty*float(i.get('weight_kg') or 0)
            rows.append({'seq':seq,'package_no':f'UNASSIGNED-{i.get("id")}','package_type':'待分箱','content':i.get('name'),'material':_material_label_from_item_name(i.get('name')),'qty':qty,'length_mm':round(float(i.get('length_mm') or 0),1),'width_mm':round(float(i.get('width_mm') or 0),1),'height_mm':round(float(i.get('height_mm') or 0),1),'net_weight':round(net,1),'gross_weight':round(net,1),'size_grade':i.get('size_grade') or 'C','weight_grade':i.get('weight_grade') or 'C','remark':'木箱类材料尚未分配到具体木箱，因此未进入集装箱方案'}); seq+=1
    return rows,plan

@app.get('/export-unloaded-packing-list')
def export_unloaded_packing_list_legacy():
    return JSONResponse({'ok':False,'error':'V5.8.28已停用直接URL导出，请从页面按钮导出。'},status_code=409)

@app.post('/export-unloaded-packing-list')
async def export_unloaded_packing_list(request:Request):
    try:
        payload=await request.json(); scheme=str(payload.get('scheme') or ''); deep=bool(payload.get('deep')); mode=str(payload.get('mode') or 'planning')
        items,crates=fetch_state(); rec=_apply_saved_container_layouts(recommend_containers(items,crates,deep=deep)); plan=(rec.get('plans') or {}).get(scheme)
        if not plan: return JSONResponse({'ok':False,'error':'当前没有可用集装箱方案。'},status_code=400)
        if _draft_rows(scheme): return JSONResponse({'ok':False,'error':'存在未保存手调草稿，请先保存/复核或恢复自动排布。'},status_code=409)
        if plan.get('invalid_manual_layouts'): return JSONResponse({'ok':False,'error':'存在已失效的手调方案，禁止静默回退导出。'},status_code=409)
        if mode=='formal':
            issues=_formal_export_issues(plan,scheme)+_data_quality_release_issues(items,crates)
            if issues: return JSONResponse({'ok':False,'error':'装柜数据对照版导出被阻止：'+'；'.join(issues)},status_code=409)
        rows,plan=_unloaded_packing_records(items,crates,rec,scheme)
        if not rows: return JSONResponse({'ok':False,'error':'当前方案没有发现未进集装箱的木箱/裸装件/待分箱材料。'},status_code=400)
        label=_container_output_label(plan,items,crates) if mode=='formal' else 'DRAFT_规划版_非签证依据'
        meta=_release_meta_load(); approval_note=f"；项目={meta.get('project_name','')}；单一责任确认人={meta.get('approver','')}" if mode=='formal' else ''
        review_notes=_all_formal_review_notes(plan,items,crates) if mode=='formal' else []
        review_mark=('；含工程复核事项（非硬失败）：'+' | '.join(review_notes[:8])) if review_notes else ''
        for r in rows: r['remark']=(r.get('remark') or '')+'；'+label+approval_note+review_mark
        xlsx=_xlsx_fill_template(_active_packing_template_path().read_bytes(),rows,PACKING_EXPORT_ALIASES); out=_write_export_file('Unloaded_Packing_List',xlsx)
        filename=f'{label}_{plan.get("label") or plan.get("code")}_未进集装箱箱单.xlsx'
        if mode=='formal': _record_release_export('unloaded_packing_list',out,items,crates,{'mode':mode,'scheme':scheme,'filename':filename})
        return FileResponse(out,filename=filename,headers={'X-Packing-Plan-Mode':mode})
    except Exception as e:
        logger.exception('export unloaded packing list failed'); return JSONResponse({'ok':False,'error':'生成未进集装箱箱单失败：'+str(e)},status_code=500)

@app.get('/shipping-mark-template/default.docx')
def download_default_shipping_mark_template():
    return FileResponse(DEFAULT_SHIPPING_MARK_TEMPLATE_PATH,filename='唛头Word_系统默认模板.docx')

@app.post('/shipping-mark-template/upload')
async def upload_shipping_mark_template(file:UploadFile=File(...)):
    try:
        raw=await file.read()
        if len(raw)>8*1024*1024: raise ValueError('模板超过8MB。')
        tmp=DATA_DIR/f'_mark_tpl_{uuid.uuid4().hex}.docx'; tmp.write_bytes(raw)
        try: _validate_shipping_mark_template(tmp)
        finally:
            pass
        CUSTOM_SHIPPING_MARK_TEMPLATE_PATH.write_bytes(raw)
        try: tmp.unlink()
        except Exception: pass
        _json_save(SHIPPING_MARK_TEMPLATE_META_PATH,{'filename':file.filename or '自定义唛头模板.docx','uploaded_at':time.strftime('%Y-%m-%d %H:%M:%S')})
        return RedirectResponse(url='/?message='+quote(f'唛头Word模板上传成功：{file.filename}。'),status_code=303)
    except Exception as e:
        return RedirectResponse(url='/?error='+quote('唛头模板上传失败：'+str(e)),status_code=303)

@app.post('/shipping-mark-template/reset')
def reset_shipping_mark_template():
    for p in (CUSTOM_SHIPPING_MARK_TEMPLATE_PATH,SHIPPING_MARK_TEMPLATE_META_PATH):
        try:
            if p.exists(): p.unlink()
        except Exception: pass
    return RedirectResponse(url='/?message='+quote('已恢复系统默认唛头Word模板。'),status_code=303)

@app.get('/export-shipping-mark/{crate_id}')
def export_shipping_mark_legacy(crate_id:int):
    return JSONResponse({'ok':False,'error':'V5.8.28已停用直接URL唛头导出。请从页面选择规划版/箱级数据对照版后导出。'},status_code=409)

@app.post('/export-shipping-mark/{crate_id}')
async def export_shipping_mark(crate_id:int, request:Request):
    try:
        payload=await request.json(); mode=str(payload.get('mode') or 'planning').lower()
        if mode not in ('planning','formal'): return JSONResponse({'ok':False,'error':'导出模式无效。'},status_code=400)
        items,crates=fetch_state(); crate=next((c for c in crates if int(c['id'])==int(crate_id)),None)
        if not crate: return JSONResponse({'ok':False,'error':'木箱不存在。'},status_code=404)
        citems=[i for i in items if i.get('crate_id')==crate['id']]
        if not citems: return JSONResponse({'ok':False,'error':'该木箱没有材料。'},status_code=400)
        if mode=='formal':
            issues=_data_quality_release_issues(items,crates)
            if issues: return JSONResponse({'ok':False,'error':'箱级数据对照版唛头导出被阻止：'+'；'.join(issues),'issues':issues},status_code=409)
        doc=_fill_shipping_mark_doc(crate,citems,_active_shipping_mark_template_path())
        out=_write_mark_docx(f'Shipping_Mark_{crate["name"]}',doc)
        label=BOX_DATA_LABEL if mode=='formal' else 'DRAFT_规划版'
        filename=f'{label}_{crate["name"]}_唛头.docx'
        if mode=='formal': _record_release_export('shipping_mark',out,items,crates,{'mode':mode,'crate_id':crate_id,'filename':filename})
        return FileResponse(out,filename=filename,headers={'X-Packing-Plan-Mode':mode})
    except Exception as e:
        logger.exception('export shipping mark failed')
        return JSONResponse({'ok':False,'error':'生成唛头失败：'+str(e)},status_code=500)

@app.get('/export-shipping-marks')
def export_all_shipping_marks_legacy():
    return JSONResponse({'ok':False,'error':'V5.8.28已停用直接URL批量唛头导出。请从页面选择规划版/箱级数据对照版后导出。'},status_code=409)

@app.post('/export-shipping-marks')
async def export_all_shipping_marks(request:Request):
    try:
        payload=await request.json(); mode=str(payload.get('mode') or 'planning').lower()
        if mode not in ('planning','formal'): return JSONResponse({'ok':False,'error':'导出模式无效。'},status_code=400)
        items,crates=fetch_state(); crates=[c for c in sorted(crates,key=lambda x:x['name']) if any(i.get('crate_id')==c['id'] for i in items)]
        if not crates: return JSONResponse({'ok':False,'error':'当前没有可生成唛头的木箱。'},status_code=400)
        if mode=='formal':
            issues=_data_quality_release_issues(items,crates)
            if issues: return JSONResponse({'ok':False,'error':'箱级数据对照版批量唛头导出被阻止：'+'；'.join(issues),'issues':issues},status_code=409)
        tpl=_active_shipping_mark_template_path(); base=None
        for c in crates:
            citems=[i for i in items if i.get('crate_id')==c['id']]
            d=_fill_shipping_mark_doc(c,citems,tpl)
            if base is None: base=d
            else: _append_doc_body(base,d)
        out=_write_mark_docx('Shipping_Marks_ALL',base)
        label=BOX_DATA_LABEL if mode=='formal' else 'DRAFT_规划版'
        filename=f'{label}_全部木箱唛头_每箱一页.docx'
        if mode=='formal': _record_release_export('shipping_marks_all',out,items,crates,{'mode':mode,'filename':filename})
        return FileResponse(out,filename=filename,headers={'X-Packing-Plan-Mode':mode})
    except Exception as e:
        logger.exception('export all shipping marks failed')
        return JSONResponse({'ok':False,'error':'批量生成唛头失败：'+str(e)},status_code=500)

@app.get("/material-template.xlsx")
def download_material_template():
    if not TEMPLATE_PATH.exists():
        return JSONResponse({"ok":False, "error":"模板文件缺失"}, status_code=404)
    return FileResponse(TEMPLATE_PATH, filename="材料清单导入模板.xlsx")

def _shipment_source_state(items):
    from app.shipment_source import state
    return state(DB_PATH.parent,items)


def _record_material_source(filename,content,parsed,mode,previous_items=None):
    from app.shipment_source import record
    previous_source=_shipment_source_state(previous_items) if previous_items is not None else {}
    items=fetch_state()[0]
    record(DB_PATH.parent,filename,content,items,parsed,mode)
    if mode=='replace' and previous_items is not None:
        from app.bag_top_policy import rebind_unchanged_equipment
        rebind_unchanged_equipment(DB_PATH.parent,previous_source,previous_items,items)


def _backup_before_material_replace(con):
    backup_dir=DATA_DIR/'import_backups';backup_dir.mkdir(exist_ok=True)
    path=backup_dir/('before_replace_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex+'.sqlite3')
    saved=sqlite3.connect(path)
    try:con.backup(saved)
    finally:saved.close()
    return path

def insert_imported_shipping_rows(parsed, mode="replace"):
    """Import a shipping/packing list as physical shipping packages.
    Existing 木箱 rows become crate records with tare=0 because the source weight is gross weight.
    Other package types (裸装/彩条布/纸箱/吨包/编织袋...) bypass crates and go directly to 3D loading.
    """
    for row in parsed:
        checked_item_values(row)
        if row.get('package_type')=='木箱': checked_number(row.get('weight_kg'),'已包装单元毛重',strict=True)
    if mode not in ('replace','append'):raise ValueError('导入模式必须为替换或追加')
    if not parsed:raise ValueError('没有有效材料，保留当前数据')
    con=db(); cur=con.cursor()
    try:
        if mode=="replace":
            _backup_before_material_replace(con)
            for table in ('crate_placements','crate_audits','container_manual_layouts','container_layout_drafts','items','crates','pipe_sources'):
                cur.execute('DELETE FROM '+table)
            cur.execute("DELETE FROM asu_records WHERE entity_key!='project'")
        crate_seq=cur.execute("SELECT COUNT(*) FROM crates").fetchone()[0]
        inserted_items=0; inserted_crates=0
        for r in parsed:
            if r["package_type"]=="木箱":
                for n in range(r["qty"]):
                    crate_seq+=1
                    raw_box=str(r.get("box_no") or "").strip()
                    if raw_box:
                        if re.fullmatch(r"\d+", raw_box):
                            base=f"BX-SRC-{int(raw_box):03d}"
                        elif raw_box.upper().startswith(("BX-","PK-")):
                            base=raw_box
                        else:
                            base=f"BX-SRC-{raw_box}"
                    else:
                        base=f"PK-{crate_seq:03d}-{r['name'][:16]}"
                    cname=base if r["qty"]==1 else f"{base}-{n+1}"
                    cur.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,tare_weight_kg,actual_gross_weight_kg,tare_weight_source,size_confirmed,size_source) VALUES(?,?,?,?,0,?,?,0,?)",
                                (cname,r["length_mm"],r["width_mm"],r["height_mm"],r["weight_kg"],"原始箱单毛重","上传箱单外形（未确认）"))
                    cid=cur.lastrowid; inserted_crates+=1
                    cur.execute("UPDATE crates SET package_origin='PREPACKED' WHERE id=?",(cid,))
                    cur.execute("""INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,weight_note,size_grade,size_source,size_note,package_type,stackable,rotatable,cg_x_mm,cg_y_mm,cg_z_mm,cg_source,cg_confirmed,crate_id)
                                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (r["name"],1,r["length_mm"],r["width_mm"],r["height_mm"],r["weight_kg"],r.get("weight_grade","B"),r.get("weight_source","上传清单声明重量（未确认）"),r.get("weight_note",""),r.get("size_grade","B"),r.get("size_source","上传清单声明尺寸（未确认）"),r.get("size_note",""),"木箱",r["stackable"],r["rotatable"],r.get('cg_x_mm'),r.get('cg_y_mm'),r.get('cg_z_mm'),r.get('cg_source','几何中心假设'),r.get('cg_confirmed',0),cid))
                    cur.execute("UPDATE items SET is_package_placeholder=1 WHERE id=?",(cur.lastrowid,))
                    inserted_items+=1
            else:
                cur.execute("""INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,weight_note,size_grade,size_source,size_note,package_type,stackable,rotatable,cg_x_mm,cg_y_mm,cg_z_mm,cg_source,cg_confirmed,crate_id)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                            (r["name"],r["qty"],r["length_mm"],r["width_mm"],r["height_mm"],r["weight_kg"],r.get("weight_grade","B"),r.get("weight_source","上传清单声明重量（未确认）"),r.get("weight_note",""),r.get("size_grade","B"),r.get("size_source","上传清单声明尺寸（未确认）"),r.get("size_note",""),r["package_type"],r["stackable"],r["rotatable"],r.get('cg_x_mm'),r.get('cg_y_mm'),r.get('cg_z_mm'),r.get('cg_source','几何中心假设'),r.get('cg_confirmed',0)))
                inserted_items+=1
        con.commit()
    except Exception:
        con.rollback();raise
    finally:
        con.close()
    return inserted_items,inserted_crates

def _validated_shipping_import(parsed,metas,raw=None):
    """Automatic and mapped imports must preserve identical shipping semantics."""
    if any(meta.get('shipping_skipped') or meta.get('shipping_qty_assumed') for _,meta in metas):
        raise ValueError('发货清单存在未解析的设备行或缺少数量，禁止部分替换；请核对尺寸和数量。')
    if raw is not None:
        from app.shipment_source import resolve_weights
        parsed,metas=resolve_weights(DB_PATH.parent,raw,parsed,metas)
    if any(row.get('shipping_unit') for row in parsed) and any(meta.get('weight_conflicts') for _,meta in metas):
        conflicts=[row.get('size_note','') for row in parsed if row.get('weight_conflict')]
        raise ValueError('发货清单有数量/重量矛盾，尚未替换任何材料，请核对：'+'；'.join(conflicts))
    return parsed,metas


@app.post("/upload-materials", response_class=HTMLResponse)
async def upload_materials(request: Request, file: UploadFile = File(...), mode: str = Form("replace")):
    try:
        content=await file.read()
        original_mode=mode
        previous_items=fetch_state()[0]
        if len(content)>20*1024*1024: raise ValueError("文件超过20MB，请拆分后导入。")
        tables=read_uploaded_tables(file.filename,content)
        if mode not in ('replace','append'):raise ValueError('导入模式必须为替换或追加')
        if mode=='replace':
            snapshot=db()
            try:_backup_before_material_replace(snapshot)
            finally:snapshot.close()
        import_messages=[]; specialized_done=False
        # V5.1: classify mixed `需要采购` sheets row-by-row instead of routing an entire sheet by a 60% vote.
        special_tables,generic_tables,partition_stats=partition_purchase_tables(tables,_strict_purchase_number)
        purchase_rows, purchase_pipes, purchase_notes, purchase_matched = parse_need_purchase_bom(special_tables)
        if purchase_matched and (purchase_rows or purchase_pipes):
            pipe5083=[r for r in purchase_rows if str(r.get('weight_source','')).startswith('5083管道')]
            fitting_purchase_rows=[r for r in purchase_rows if r not in pipe5083]
            if mode=="replace":
                con=db()
                for tbl in ('crate_placements','crate_audits','items','crates','pipe_sources'):
                    con.execute(f"DELETE FROM {tbl}")
                con.commit(); con.close()
            if fitting_purchase_rows: insert_pipe_fitting_rows(fitting_purchase_rows,'append')
            crate_count,oversize,_precision=apply_smart_crates(3500,2200,2200,5000,100,45) if fitting_purchase_rows else (0,0,{})
            crate5083=insert_5083_pipe_crates(pipe5083)
            replace_pipe_sources(purchase_pipes,'append')
            bundle_count,bundle_pipes,_=rebuild_pipe_bundles(6000,3000,1200,1200) if purchase_pipes else (0,0,0)
            msg=f"逐行识别‘需要采购’表：管道/管件行 {partition_stats.get('special_rows',0)} 条进入专用解析，普通设备行 {partition_stats.get('generic_rows',0)} 条保留给通用导入；管件生成 {crate_count} 个木箱，5083管道生成 {crate5083} 个木箱，非5083管道生成 {bundle_count} 捆。"
            if oversize: msg+=f" 其中 {oversize} 个管件采用定制超限木箱。"
            if purchase_notes: msg+="；"+"；".join(purchase_notes)
            import_messages.append(msg); specialized_done=True; tables=generic_tables; mode='append'

        # Specialized pipe-material-list path: this kind of BOM has DN/category/description/quantity,
        # but no packing L/W/H. Estimate fitting envelopes and immediately optimize fittings into wood crates.
        fitting_rows, pipe_sources, fitting_notes = ([],[],[]) if specialized_done else parse_pipe_fitting_bom(tables)
        if fitting_rows or pipe_sources:
            if fitting_rows: insert_pipe_fitting_rows(fitting_rows,mode)
            elif mode=="replace":
                con=db(); con.execute("DELETE FROM items"); con.execute("DELETE FROM crates"); con.execute("DELETE FROM crate_placements"); con.execute("DELETE FROM crate_audits"); con.commit(); con.close()
            replace_pipe_sources(pipe_sources,mode)
            bundle_count,bundle_pipes,_=rebuild_pipe_bundles(6000,3000,1200,1200) if pipe_sources else (0,0,0)
            crate_count,oversize,_precision=apply_smart_crates(3500,2200,2200,5000,100,45) if fitting_rows else (0,0,{})
            msg=f"识别为管道材料表：导入 {len(fitting_rows)} 条管件记录，生成 {crate_count} 个木箱；碳钢管道生成 {bundle_count} 捆裸装扎捆（规划 {bundle_pipes} 根）。"
            if oversize: msg+=f" 其中 {oversize} 个管件采用定制超限木箱。"
            if fitting_notes: msg+="；"+"；".join(fitting_notes)
            return RedirectResponse(url=f"/?message={quote(msg)}",status_code=303)
        sheet_headers=find_sheet_headers(tables)
        all_parsed=[]; all_skipped=[]; imported_sheets=[]; metas=[]
        for best in sheet_headers:
            if not mapping_is_importable(best["columns"]): continue
            table=tables[best["table_index"]]
            parsed,skipped,meta=parse_material_rows(table["rows"],best["columns"],best.get("data_start",best["header_idx"]+1),best.get("headers",[]))
            if parsed:
                all_parsed.extend(parsed); all_skipped.extend([(table["sheet"],)+tuple(x) for x in skipped])
                imported_sheets.append(table["sheet"]); metas.append((table["sheet"],meta))
        if all_parsed:
            all_parsed,metas=_validated_shipping_import(all_parsed,metas,content)
            items_count,crate_count=insert_imported_shipping_rows(all_parsed,mode)
            _record_material_source(file.filename,content,all_parsed,original_mode if not specialized_done else "append",previous_items=previous_items)
            forced_5083=sum(1 for r in all_parsed if r.get("forced_crate_5083"))
            msg=("；".join(import_messages)+"；" if import_messages else "")+f"已读取 {len(imported_sheets)} 个通用工作表区域，导入 {len(all_parsed)} 条有效装运记录；生成 {crate_count} 个现有木箱包装单元。"
            if forced_5083:
                msg+=f" 检测到 {forced_5083} 条 5083 材质记录，已按规则强制采用木箱包装。"
            if all_skipped: msg+=f" 跳过 {len(all_skipped)} 条缺少尺寸/重量的汇总或备注行。"
            extra=[]
            if any(m.get("allocated_weight_rows") for _,m in metas):
                extra.append("续行缺少单独毛重的项目已按同组几何体积比例分摊重量")
            if any(m.get("unit_header_suspect") for _,m in metas):
                extra.append("检测到表头标注 cm 但数值量级明显为 mm，已按 mm 处理")
            conflict_count=sum(int(m.get("weight_conflicts") or 0) for _,m in metas)
            if conflict_count:
                extra.append(f"发现 {conflict_count} 行‘单重×数量’与‘总重’不一致，当前按总重优先导入，请复核原始BOM")
            if extra: msg+="；"+"；".join(extra)+"。"
            return RedirectResponse(url=f"/?message={quote(msg)}",status_code=303)
        if specialized_done and import_messages:
            return RedirectResponse(url=f"/?message={quote('；'.join(import_messages))}",status_code=303)
        # Nothing automatically importable: use best sheet for manual field mapping.
        best=find_best_header(tables)
        if not best: raise ValueError("没有找到任何可识别的数据区域。")
        table=tables[best["table_index"]]
        token=save_import_session(table,best,mode,file.filename,content)
        max_cols=max([len(r) for r in table["rows"][:100]]+[1]); headers=[]
        for i in range(max_cols):
            h=best["headers"][i] if i<len(best["headers"]) else ""
            headers.append({"idx":i,"label":str(h or f"列{excel_col_name(i)}"),"col":excel_col_name(i)})
        preview=[]
        for ri,row in enumerate(table["rows"][max(0,best["header_idx"]-2):best["header_idx"]+12],start=max(0,best["header_idx"]-2)+1):
            preview.append({"row_no":ri,"values":[row[i] if i<len(row) else "" for i in range(max_cols)]})
        return templates.TemplateResponse(request, "import_mapping.html",{
            "token":token,"filename":file.filename,"sheet":table["sheet"],"headers":headers,
            "preview":preview,"detected":best["columns"],"data_start":best.get("data_start",best["header_idx"]+1)+1,
            "field_labels":FIELD_LABELS,"reason":"自动识别不完整，请确认下面的字段对应关系后继续导入。"})
    except Exception as e:
        logger.exception("Material import failed: filename=%s",getattr(file,"filename",""))
        return RedirectResponse(url=f"/?error={quote(str(e))}",status_code=303)

@app.post("/confirm-import")
def confirm_import(
    token: str = Form(...), data_start: int = Form(...),
    name_col: str = Form(...), qty_col: str = Form(""), size_col: str = Form(""),
    length_col: str = Form(""), width_col: str = Form(""), height_col: str = Form(""),
    weight_col: str = Form(""), total_weight_col: str = Form(""), package_col: str = Form(""),
    stackable_col: str = Form(""), rotatable_col: str = Form("")
):
    try:
        session,path=load_import_session(token)
        previous_items,previous_crates=fetch_state()
        if session.get('state_fingerprint')!=_state_fingerprint(previous_items,previous_crates):
            raise ValueError('打开列映射后材料或规划设置已发生变化，请重新上传；当前材料未被替换。')
        def col(v): return int(v) if str(v).strip()!="" else None
        columns={}
        for field,value in {
            "name":name_col,"qty":qty_col,"size":size_col,"length_mm":length_col,"width_mm":width_col,"height_mm":height_col,
            "weight_kg":weight_col,"total_weight_kg":total_weight_col,"package_type":package_col,"stackable":stackable_col,"rotatable":rotatable_col
        }.items():
            x=col(value)
            if x is not None: columns[field]=x
        # These source columns have no manual controls, but determine shipping units
        # and explicit non-container transport; never discard them during mapping.
        detected=(session.get('best') or {}).get('columns') or {}
        for field in ('unit','shipment_note','box_no','material'):
            if field in detected:columns[field]=detected[field]
        headers=(session.get('best') or {}).get('headers') or []
        parsed,skipped,meta=parse_material_rows(session["rows"],columns,max(0,data_start-1),headers)
        raw_path=IMPORT_DIR/f'{token}.raw'
        raw=raw_path.read_bytes() if raw_path.exists() else None
        parsed,metas=_validated_shipping_import(parsed,[(session.get('sheet',''),meta)],raw)
        meta=metas[0][1]
        if not parsed: raise ValueError("按当前字段映射仍没有找到有效材料，请检查数据起始行和字段选择。")
        # V5.1: manual field mapping uses the same physical-package insertion path as automatic generic import.
        import_mode=session.get('mode')
        if import_mode not in ('replace','append'):raise ValueError('导入会话未注明替换或追加模式，请重新上传')
        inserted_items,inserted_crates=insert_imported_shipping_rows(parsed,import_mode)
        if raw is not None:_record_material_source(session.get('filename') or 'uploaded.xlsx',raw,parsed,import_mode,previous_items=previous_items)
        try: path.unlink()
        except OSError: pass
        msg=f"通过字段映射成功导入 {len(parsed)} 条装运记录；生成 {inserted_crates} 个现有木箱包装单元"
        if skipped: msg+=f"，跳过 {len(skipped)} 行"
        if meta.get("weight_conflicts"):
            msg+=f"；发现 {meta['weight_conflicts']} 行‘单重×数量’与‘总重’不一致，当前按总重优先导入，请复核原始BOM"
        return RedirectResponse(url=f"/?message={quote(msg)}", status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/?error={quote(str(e))}", status_code=303)


@app.post("/smart-plan")
def smart_plan(
    crate_mode: str = Form("container_aware"),
    target_container: str = Form("40HQ"),
    max_crate_l: float = Form(4000), max_crate_w: float = Form(2200), max_crate_h: float = Form(2200),
    max_crate_gross: float = Form(5000), padding_mm: float = Form(100), tare_per_m3: float = Form(45),
    target_util_min: float = Form(60), target_util_max: float = Form(80), optimize_utilization: int = Form(1),
    long_pipe_mode: str = Form("standard"), max_pipes_per_crate: int = Form(0),
    max_items_per_crate: int = Form(30), max_skus_per_crate: int = Form(8),
    crate_weight_model: str = Form("structural"), panel_density: float = Form(500),
    timber_density: float = Form(480), hardware_pct: float = Form(4),
    cg_review_pct: float = Form(10), cg_high_risk_pct: float = Form(20),
    cg_z_review_pct: float = Form(60), cg_z_high_risk_pct: float = Form(75),
    critical_cg_weight_kg: float = Form(500), critical_cg_share_pct: float = Form(10)
):
    try:
        for v,n in [(max_crate_l,"木箱长度"),(max_crate_w,"木箱宽度"),(max_crate_h,"木箱高度"),(max_crate_gross,"最大木箱毛重")]:
            if v<=0: raise ValueError(f"{n}必须大于0。")
        if padding_mm<0: raise ValueError("木箱预留量不能为负数。")
        if tare_per_m3<0: raise ValueError("木箱自重估算系数不能为负数。")
        if not (30<=target_util_min<target_util_max<=95): raise ValueError("木箱利用率目标建议设置为 30~95% 且下限小于上限。")
        if long_pipe_mode not in {"conservative","standard","aggressive"}: raise ValueError("长管装箱策略无效。")
        if max_pipes_per_crate < 0: raise ValueError("每箱最多根数不能为负数；0表示自动。")
        if max_items_per_crate < 0: raise ValueError("每箱最大总件数不能为负数；0表示不限制。")
        if max_skus_per_crate < 0: raise ValueError("每箱最大物料种类数不能为负数；0表示不限制。")
        if crate_weight_model not in {"structural","legacy"}: raise ValueError("木箱自重模型无效。")
        if panel_density<=0 or timber_density<=0: raise ValueError("板材/木方密度必须大于0。")
        if hardware_pct<0 or hardware_pct>30: raise ValueError("辅材比例应在0~30%范围。")
        if not (0<=cg_review_pct<cg_high_risk_pct<=50): raise ValueError("水平重心阈值应满足 0≤复核阈值<高风险阈值≤50%。")
        if not (0<cg_z_review_pct<cg_z_high_risk_pct<=100): raise ValueError("垂直重心阈值应满足 0<复核阈值<高风险阈值≤100%。")
        if critical_cg_weight_kg<0 or not (0<=critical_cg_share_pct<=100): raise ValueError("关键重件CG阈值必须为非负重量且占柜重比例0~100%。")
        _crate_weight_settings_save({"model":crate_weight_model,"panel_density":panel_density,"timber_density":timber_density,"hardware_pct":hardware_pct,"legacy_kg_per_m3":tare_per_m3,
                                     "container_cg_review_pct":cg_review_pct,"container_cg_high_risk_pct":cg_high_risk_pct,"container_cg_fail_pct":cg_high_risk_pct,
                                     "container_cg_z_review_pct":cg_z_review_pct,"container_cg_z_high_risk_pct":cg_z_high_risk_pct,
                                     "crate_cg_review_pct":cg_review_pct,"crate_cg_high_risk_pct":cg_high_risk_pct,
                                     "crate_cg_z_review_pct":cg_z_review_pct,"crate_cg_z_high_risk_pct":cg_z_high_risk_pct,
                                     "critical_cg_weight_kg":critical_cg_weight_kg,"critical_cg_share_pct":critical_cg_share_pct})

        if crate_mode=="fixed":
            if padding_mm*2>=min(max_crate_l,max_crate_w,max_crate_h): raise ValueError("固定木箱尺寸与预留量不匹配。")
            count,oversize,precision=apply_smart_crates(max_crate_l,max_crate_w,max_crate_h,max_crate_gross,padding_mm,tare_per_m3,exact_fixed=True,optimize_util=False,target_min=target_util_min,target_max=target_util_max,long_pipe_mode=long_pipe_mode,max_pipes_per_crate=max_pipes_per_crate,max_items_per_crate=max_items_per_crate,max_skus_per_crate=max_skus_per_crate)
            strategy={"mode":"fixed","target_container":target_container,"recommended_l":max_crate_l,"recommended_w":max_crate_w,"recommended_h":max_crate_h,
                      "crate_count":count,"long_pipe_mode":long_pipe_mode,"max_pipes_per_crate":max_pipes_per_crate,"max_items_per_crate":max_items_per_crate,"max_skus_per_crate":max_skus_per_crate,"max_crate_gross":max_crate_gross,"padding_mm":padding_mm,"tare_per_m3":tare_per_m3,"target_util_min":target_util_min,"target_util_max":target_util_max,
                      "precision_eliminated":precision.get("eliminated_crates",0),"precision_attempts":precision.get("source_attempts",0),"precision_rounds":precision.get("rounds",0),
                      "note":"V5.8.28固定箱型仍执行全局精度消箱：先完整3D重排确认所有受影响木箱，再允许删除来源箱；超限件单独定制。长管合箱策略="+_long_pipe_policy(long_pipe_mode)["label"]+"。"}
            _strategy_save(strategy)
            msg=f"固定木箱规划完成：标准箱 {max_crate_l:.0f}×{max_crate_w:.0f}×{max_crate_h:.0f} mm，共生成 {count} 个木箱。"
            if oversize: msg+=f" 另有 {oversize} 个超限定制箱。"
        else:
            best,top=optimize_crates_for_container(target_container,max_crate_gross,padding_mm,tare_per_m3,target_util_min,target_util_max,long_pipe_mode,max_pipes_per_crate,max_items_per_crate,max_skus_per_crate)
            count,oversize,precision=apply_smart_crates(best["max_l"],best["max_w"],best["max_h"],max_crate_gross,padding_mm,tare_per_m3,exact_fixed=False,optimize_util=bool(optimize_utilization),target_min=target_util_min,target_max=target_util_max,long_pipe_mode=long_pipe_mode,max_pipes_per_crate=max_pipes_per_crate,max_items_per_crate=max_items_per_crate,max_skus_per_crate=max_skus_per_crate)
            strategy={"mode":"container_aware","target_container":target_container,"recommended_l":best["max_l"],"recommended_w":best["max_w"],"recommended_h":best["max_h"],
                      "crate_count":count,"container_count":best["container_count"],"container_util":best["container_util"],"weight_util":best["weight_util"],"alternatives":top,
                      "target_util_min":target_util_min,"target_util_max":target_util_max,"avg_crate_util":best.get("avg_crate_util"),
                      "low_util_crates":best.get("low_util_crates"),"good_util_crates":best.get("good_util_crates"),"min_crate_util":best.get("min_crate_util"),
                      "engineering_review_count":best.get("engineering_review_count",0),"crate_cbm":best.get("crate_cbm"),"crate_tare_total":best.get("crate_tare_total"),
                      "precision_eliminated":precision.get("eliminated_crates",best.get("precision_eliminated",0)),"precision_attempts":precision.get("source_attempts",best.get("precision_attempts",0)),"precision_rounds":precision.get("rounds",best.get("precision_rounds",0)),
                      "candidate_scanned":best.get("candidate_scanned",0),"precision_finalists":best.get("precision_finalists",0),
                      "long_pipe_mode":long_pipe_mode,"max_pipes_per_crate":max_pipes_per_crate,"max_items_per_crate":max_items_per_crate,"max_skus_per_crate":max_skus_per_crate,"max_crate_gross":max_crate_gross,"padding_mm":padding_mm,"tare_per_m3":tare_per_m3,
                      "note":f"V5.8.28沿用V5.7两阶段精度搜索：先扫描候选木箱包络，再让最终候选进入全局消箱 + 完整3D重排 + 静态规划校核。最终排序：木箱数 → 目标柜数 → 工程复核风险 → 木箱总CBM → 箱皮总重 → 利用率。长管策略={_long_pipe_policy(long_pipe_mode)['label']}。"}
            _strategy_save(strategy)
            msg=(f"V5.8.28精度规划完成：目标 {target_container}，推荐木箱最大包络约 "
                 f"{best['max_l']:.0f}×{best['max_w']:.0f}×{best['max_h']:.0f} mm；生成 {count} 个木箱，预计 {best['container_count']} 柜，"
                 f"总体积利用率约 {best['container_util']:.1f}%；全局精度消箱减少 {precision.get('eliminated_crates',0)} 个木箱，"
                 f"工程复核项 {best.get('engineering_review_count',0)}。")
            if oversize: msg+=f" 其中 {oversize} 个超限定制箱。"
        return RedirectResponse(url=f"/?message={quote(msg)}",status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/?error={quote(str(e))}",status_code=303)



def _exact_material_key(text):
    s=str(text or '').upper().replace('＃','#')
    patterns=[
        (r'(?<!\d)5083(?!\d)','5083'),
        (r'Q235B','Q235B'),
        (r'(?<!\d)20\s*#','20#'),
        (r'S31603|316L','316L'),
        (r'S30408|304L','304L'),
        (r'(?<!\d)316(?!\d)','316'),
        (r'(?<!\d)304(?!\d)','304'),
        (r'(?<!\d)321(?!\d)','321'),
    ]
    for pat,key in patterns:
        if re.search(pat,s,re.I): return key
    # fall back to explicit CS/SS only when no grade is present
    if '不锈钢' in s or re.search(r'(^|[^A-Z])SS[-\s]',s): return 'SS-UNSPEC'
    if '碳钢' in s or re.search(r'(^|[^A-Z])CS[-\s]',s): return 'CS-UNSPEC'
    return 'UNKNOWN'

def _crate_exact_material_set(crate_id,items):
    vals=set()
    for i in items:
        if i.get('crate_id')!=crate_id or i.get('is_package_placeholder'): continue
        key=_exact_material_key(' | '.join([str(i.get('name') or ''),str(i.get('weight_source') or ''),str(i.get('weight_note') or '')]))
        vals.add(key)
    return vals

def _crate_mix_class(crate_id,items):
    """Only 5083 is an isolation class. All other grades may mix with each other."""
    mats=_crate_exact_material_set(crate_id,items)
    if not mats:
        return 'OTHER'
    if '5083' in mats:
        # A crate that already mixes 5083 with anything else is invalid for further focused merging.
        return 'INVALID-5083-MIX' if len(mats)>1 else '5083'
    return 'OTHER'

def _item_mix_class(item):
    key=_exact_material_key(' | '.join([str(item.get('name') or ''),str(item.get('weight_source') or ''),str(item.get('weight_note') or '')]))
    return '5083' if key=='5083' else 'OTHER'

def _target_fill_padding(mix_class,strategy):
    configured=_padding_setting(strategy)
    return min(configured,30.0 if mix_class=='5083' else 10.0)

def _compatible_donor_crates(target_id,items,crates,mix_class):
    crates=[c for c in crates if not _is_prepacked(c)]
    out=[]
    for c in crates:
        cid=c['id']
        if cid==target_id or str(c.get('name') or '').startswith('BX-SRC-'):
            continue
        citems=[i for i in items if i.get('crate_id')==cid and i.get('package_type')=='木箱']
        if not citems:
            continue
        # 5083 stays isolated. All non-5083 materials may mix with each other.
        cclass=_crate_mix_class(cid,items)
        if cclass!=mix_class:
            continue
        units=[]
        for i in citems:
            for n in range(int(i.get('qty') or 0)):
                u=_packing_unit_from_item(i,n)
                u['_donor_crate_id']=cid
                u['_donor_item_id']=i['id']
                u['_actual_volume']=float(u.get('actual_l',0))*float(u.get('actual_w',0))*float(u.get('actual_h',0))
                units.append(u)
        if units:
            out.append({'crate':c,'items':citems,'units':units,
                        'unit_count':len(units),
                        'actual_volume':sum(u['_actual_volume'] for u in units),
                        'weight':sum(float(u.get('weight_kg',0)) for u in units)})
    return out

def _candidate_dim_fits_target(u,L,W,H,padding):
    inner_l=max(1.0,L-2*padding); inner_w=max(1.0,W-2*padding); inner_h=max(1.0,H-2*padding)
    for ol,ow,oh in orientations(u['length_mm'],u['width_mm'],u['height_mm'],u.get('rotatable',True)):
        if ol<=inner_l+1e-6 and ow<=inner_w+1e-6 and oh<=inner_h+1e-6:
            return True
    return False

def _donor_impossible_by_bounds(work, donor_units, L, W, H, max_gross, padding, tare_per_m3):
    """Safe pruning only: returns True only when a donor is provably impossible.
    It does NOT discard candidates merely because they look less promising.
    """
    if not donor_units:
        return True
    # Weight is a hard necessary condition.
    tare=_estimate_tare(L,W,H,tare_per_m3)
    total_weight=sum(float(u.get('weight_kg',0)) for u in work)+sum(float(u.get('weight_kg',0)) for u in donor_units)+tare
    if total_weight > max_gross + 1e-6:
        return True
    # Sum of conservative planning envelopes cannot exceed internal volume in any feasible cuboid packing.
    inner_l=max(1.0,L-2*padding); inner_w=max(1.0,W-2*padding); inner_h=max(1.0,H-2*padding)
    capacity=inner_l*inner_w*inner_h
    plan_vol=sum(float(u.get('length_mm',0))*float(u.get('width_mm',0))*float(u.get('height_mm',0)) for u in work)
    plan_vol+=sum(float(u.get('length_mm',0))*float(u.get('width_mm',0))*float(u.get('height_mm',0)) for u in donor_units)
    if plan_vol > capacity + 1e-6:
        return True
    return False

def _confirmed_candidate_rank(cd,target_goal,extra_volume=0.0,extra_count=0):
    """Rank ONLY fully-repacked candidates.
    Lower tuple is better.  The engineering layout in ``cd`` is already the authoritative
    complete 3D repack, so incremental screening never determines the final ordering.
    """
    a=cd.get('audit') or {}
    util=float(a.get('actual_util') or 0)
    planning=float(a.get('planning_util') or 0)
    offset=max(float(a.get('offset_x_pct') or 0),float(a.get('offset_y_pct') or 0))
    # Prefer a useful fill level without forcing irregular cargo past the target band.
    band_penalty=0 if util<=target_goal else 1
    gap=abs(target_goal-util)
    return (band_penalty,round(gap,4),round(offset,4),-round(util,4),-round(planning,4),-round(float(extra_volume),3),-int(extra_count))


def _unique_unit_options(donors):
    """Return one geometric representative per packing-equivalent SKU plus all donor owners.
    This is exact symmetry reduction: equivalent units have identical 3D feasibility in the
    same current layout, but donor metadata is preserved for elimination ranking.
    """
    by_sig={}
    for d in donors:
        for u in d.get('units',[]):
            sig=_unit_geom_signature(u)
            entry=by_sig.setdefault(sig,{'unit':u,'owners':[]})
            entry['owners'].append((d,u))
    return list(by_sig.values())

def _simulate_material_fill(crate_id,items,crates,stats,strategy,max_trials=100000,fast=False,progress=None,cancel_check=None,time_budget_s=20.0):
    """V5.1 precision solver: every candidate that competes for selection is FULL-3D-repacked
    before final ranking. Incremental placement may still exist elsewhere as a quick feasibility
    aid, but it is never used as the ranking authority in this solver.

    Priority remains:
      1) eliminate complete donor crates;
      2) minimize the number of donor crates touched;
      3) improve target-crate utilization without exceeding engineering limits.
    """
    max_trials=min(int(max_trials or 0),200000)
    started_at=time.monotonic(); deadline=started_at+max(2.0,float(time_budget_s or 20.0))
    def budget_exhausted(): return trials>=max_trials or time.monotonic()>=deadline
    target=next((c for c in crates if c['id']==crate_id),None)
    if not target: return None
    mix_class=_crate_mix_class(crate_id,items)
    if mix_class=='INVALID-5083-MIX': return None
    target_units=_crate_units(crate_id,items)
    if not target_units: return None

    max_gross=float((strategy or {}).get('max_crate_gross',5000) or 5000)
    tare=float((strategy or {}).get('tare_per_m3',45) or 45)
    padding=_target_fill_padding(mix_class,strategy)
    L=float(target['length_mm']); W=float(target['width_mm']); H=float(target['height_mm'])
    current=float(stats[crate_id].get('utilization') or 0)
    target_max=float((strategy or {}).get('target_util_max',80) or 80)
    target_goal=min(92.0,max(70.0,target_max))

    # Initial state itself is established by the complete repacker.
    last_cd=_full_confirm_candidate(target_units,L,W,H,max_gross,padding,tare)
    if not last_cd: return None
    work=list(target_units)
    accepted=[]
    fully_absorbed=[]
    partial_donor_ids=[]
    trials=1

    donors=_compatible_donor_crates(crate_id,items,crates,mix_class)
    if progress: progress(5,'候选筛选',f'扫描到 {len(donors)} 个兼容来源箱；仅做确定性尺寸/重量/体积排除。',candidate_current=0,candidate_total=len(donors),trials=trials,best='尚未找到可行方案',stage_progress=0)
    donors=[d for d in donors if all(_candidate_dim_fits_target(u,L,W,H,padding) for u in d['units'])]
    donor_initial=len(donors)
    full_est=max(1,donor_initial*(donor_initial+1)//2)
    partial_est=max(1,sum(int(d.get('unit_count') or 0) for d in donors))
    full_done=0; partial_done=0; best_text='尚未找到可行方案'
    if progress: progress(8,'整箱候选完整重排排名',f'尺寸筛选后剩余 {donor_initial} 个来源箱；所有竞争候选均执行完整3D重排后排名。',candidate_current=0,candidate_total=full_est,trials=trials,best=best_text,stage_progress=0)

    # PHASE 1 — complete-crate elimination. ALL feasible donor competitors are fully repacked
    # before ranking, rather than ranking on an incremental layout and only confirming the first.
    while donors and not budget_exhausted():
        confirmed_candidates=[]
        round_total=max(1,len(donors)); round_done=0
        for d in donors:
            if cancel_check and cancel_check(): raise RuntimeError('__CANCELLED__')
            if budget_exhausted(): break
            round_done+=1; full_done+=1
            pct=8+min(47,int(47*full_done/max(full_est,1)))
            if progress: progress(pct,'整箱候选完整重排排名',f"完整重排 {d['crate']['name']}（本轮 {round_done}/{round_total}）",candidate_current=full_done,candidate_total=full_est,trials=trials,best=best_text,stage_progress=round(round_done/round_total*100,1))
            if _donor_impossible_by_bounds(work,d['units'],L,W,H,max_gross,padding,tare):
                continue
            confirmed=_full_confirm_candidate(work+d['units'],L,W,H,max_gross,padding,tare)
            trials+=1
            if not confirmed: continue
            a=confirmed.get('audit') or {}
            ua=float(a.get('actual_util') or 0)
            if ua>96.0: continue
            score=_confirmed_candidate_rank(confirmed,target_goal,d.get('actual_volume',0),d.get('unit_count',0))
            confirmed_candidates.append((score,d,confirmed,ua))
        if not confirmed_candidates:
            break
        confirmed_candidates.sort(key=lambda x:x[0])
        _score,chosen,chosen_cd,ua=confirmed_candidates[0]
        best_text=f"当前最佳：整体吸收 {chosen['crate']['name']}；所有整箱候选完整重排后排名第一，利用率约 {ua:.1f}%"
        if progress: progress(max(15,8+min(47,int(47*full_done/max(full_est,1)))),'整箱完整排名完成',f"本轮 {len(confirmed_candidates)} 个可行整箱候选全部完整重排，选中 {chosen['crate']['name']}",candidate_current=full_done,candidate_total=full_est,trials=trials,best=best_text,stage_progress=100)
        accepted.extend(chosen['units']); work.extend(chosen['units'])
        fully_absorbed.append(int(chosen['crate']['id']))
        last_cd=chosen_cd
        donors=[d for d in donors if int(d['crate']['id'])!=int(chosen['crate']['id'])]
        if ua>=target_goal: break

    # PHASE 2 — material-level filling. At EVERY decision point, every unique packing-equivalent
    # SKU that can compete is fully repacked. Equivalent SKU copies are symmetry-reduced, but all
    # donor ownership choices remain in the final ranking.
    if progress: progress(58,'材料候选完整重排排名',f'整箱消箱阶段结束；开始对剩余材料做全候选完整3D排名。候选件约 {partial_est}。',candidate_current=0,candidate_total=partial_est,trials=trials,best=best_text,stage_progress=0)
    donor_remaining={int(d['crate']['id']):list(d['units']) for d in donors}
    donor_meta={int(d['crate']['id']):d for d in donors}
    touched_partial=set()

    while donor_remaining and not budget_exhausted():
        now=float((last_cd.get('audit') or {}).get('actual_util') or current)
        if now>=target_goal: break
        active=[]
        for cid,us in donor_remaining.items():
            if us:
                d=donor_meta[cid]
                active.append({'crate':d['crate'],'units':us,'unit_count':len(us),'actual_volume':sum(float(u.get('_actual_volume',0)) for u in us),'weight':sum(float(u.get('weight_kg',0)) for u in us)})
        if not active: break

        # Exact SKU symmetry reduction: one full repack per geometry signature at this state.
        options=_unique_unit_options(active)
        if not options: break
        step_candidates=[]
        step_total=len(options); step_done=0
        for opt in options:
            if cancel_check and cancel_check(): raise RuntimeError('__CANCELLED__')
            if budget_exhausted(): break
            step_done+=1; partial_done+=1
            rep=opt['unit']
            pct=58+min(31,int(31*partial_done/max(partial_est,1)))
            if progress: progress(pct,'材料候选完整重排排名',f"完整重排候选SKU：{rep.get('source_name') or rep.get('name') or '材料'}（{step_done}/{step_total}）",candidate_current=partial_done,candidate_total=partial_est,trials=trials,best=best_text,stage_progress=round(step_done/step_total*100,1))
            # Safe hard bounds for a single candidate.
            if not _candidate_dim_fits_target(rep,L,W,H,padding):
                continue
            confirmed=_full_confirm_candidate(work+[rep],L,W,H,max_gross,padding,tare)
            trials+=1
            if not confirmed: continue
            a=confirmed.get('audit') or {}; ua=float(a.get('actual_util') or 0)
            if ua>96.0: continue
            # Same confirmed geometry applies to every packing-equivalent owner. Preserve donor
            # metadata so elimination priority is still exact.
            for d,u in opt['owners']:
                cid=int(d['crate']['id']); remain_after=max(0,len(donor_remaining[cid])-1)
                eliminates_now=0 if remain_after==0 else 1
                donor_touch=0 if cid in touched_partial else 1
                geom_rank=_confirmed_candidate_rank(confirmed,target_goal,float(u.get('_actual_volume',0)),1)
                # Primary: immediate crate elimination, then keep sourcing concentrated in already-touched
                # donor, then leave as few units as possible in donor; only after that compare geometry rank.
                score=(eliminates_now,donor_touch,remain_after)+geom_rank
                step_candidates.append((score,cid,u,confirmed,ua))
        if not step_candidates:
            break
        step_candidates.sort(key=lambda x:x[0])
        _score,cid,chosen_u,chosen_cd,ua=step_candidates[0]
        accepted.append(chosen_u); work.append(chosen_u); last_cd=chosen_cd
        touched_partial.add(cid); partial_donor_ids.append(cid)
        # Remove exactly one matching unit from that donor.
        removed=False; sig=_unit_geom_signature(chosen_u)
        new_us=[]
        for u in donor_remaining[cid]:
            if not removed and _unit_geom_signature(u)==sig and int(u.get('_donor_item_id',-1))==int(chosen_u.get('_donor_item_id',-2)):
                removed=True; continue
            new_us.append(u)
        donor_remaining[cid]=new_us
        if not new_us:
            fully_absorbed.append(cid); donor_remaining.pop(cid,None)
        best_text=f"当前最佳：从 {donor_meta[cid]['crate']['name']} 搬入 1 件；本轮所有竞争SKU完整重排后排名第一，利用率约 {ua:.1f}%"
        if progress: progress(max(62,58+min(31,int(31*partial_done/max(partial_est,1)))),'材料完整排名完成',f"选中 {chosen_u.get('source_name') or chosen_u.get('name') or '材料'}",candidate_current=partial_done,candidate_total=partial_est,trials=trials,best=best_text,stage_progress=100)

    if not accepted:
        if progress: progress(90,'搜索结束','所有竞争候选均已按完整3D重排评估，没有找到可行搬运方案。',candidate_current=partial_done,candidate_total=partial_est,trials=trials,best=best_text,stage_progress=100)
        return None

    # FINAL AUTHORITY — full repack once more before any database mutation.
    if progress: progress(92,'最终方案完整3D终审','正在对最终材料集合重新执行完整3D重排终审。',candidate_current=partial_done,candidate_total=partial_est,trials=trials,best=best_text,stage_progress=25)
    final_cd=_full_confirm_candidate(work,L,W,H,max_gross,padding,tare)
    trials+=1
    if not final_cd:
        if progress: progress(94,'最终方案否决','候选排名已完成，但最终完整3D终审未通过，本次不执行搬运。',candidate_current=partial_done,candidate_total=partial_est,trials=trials,best='最终完整重排未通过',stage_progress=100)
        return None
    last_cd=final_cd
    after=float((last_cd.get('audit') or {}).get('actual_util') or 0)
    if progress: progress(96,'最终完整3D终审通过',f'完整重排终审通过：准备搬运 {len(accepted)} 件材料。',candidate_current=partial_done,candidate_total=partial_est,trials=trials,best=f'最终确认利用率 {after:.1f}%',stage_progress=100)

    moved_by_item={}; donors_used=set()
    for u in accepted:
        iid=int(u['_donor_item_id']); moved_by_item[iid]=moved_by_item.get(iid,0)+1
        donors_used.add(int(u['_donor_crate_id']))
    return {
        'target_id':crate_id,'mix_class':mix_class,'before_util':round(current,1),'after_util':round(after,1),
        'moved_count':len(accepted),'moved_by_item':moved_by_item,'donor_crates':sorted(donors_used),
        'fully_absorbed_donors':sorted(set(fully_absorbed)),
        'partial_donors':sorted(set(partial_donor_ids)-set(fully_absorbed)),
        'padding_used':padding,'_cd':last_cd,'solver_mode':'full_candidate_repack_ranking','trials':trials
    }

def _transfer_item_units_to_target(target_id,moved_by_item):
    con=db(); cur=con.cursor(); affected=set()
    for item_id,moved in moved_by_item.items():
        row=cur.execute('SELECT * FROM items WHERE id=?',(item_id,)).fetchone()
        if not row: continue
        r=dict(row); moved=min(int(moved),int(r.get('qty') or 0))
        if moved<=0: continue
        affected.add(int(r['crate_id']))
        existing=cur.execute('SELECT id,qty FROM items WHERE crate_id=? AND name=? AND length_mm=? AND width_mm=? AND height_mm=? AND weight_kg=? AND weight_grade=? AND cg_x_mm IS ? AND cg_y_mm IS ? AND cg_z_mm IS ? AND cg_confirmed=? LIMIT 1',
                             (target_id,r['name'],r['length_mm'],r['width_mm'],r['height_mm'],r['weight_kg'],r.get('weight_grade','C'),r.get('cg_x_mm'),r.get('cg_y_mm'),r.get('cg_z_mm'),int(r.get('cg_confirmed') or 0))).fetchone()
        if existing:
            cur.execute('UPDATE items SET qty=qty+?,size_evidence_id=COALESCE(size_evidence_id,?),weight_evidence_id=COALESCE(weight_evidence_id,?) WHERE id=?',(moved,r.get('size_evidence_id'),r.get('weight_evidence_id'),existing['id']))
        else:
            cur.execute('INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,weight_note,size_grade,size_source,size_note,size_evidence_id,weight_evidence_id,package_type,stackable,rotatable,cg_x_mm,cg_y_mm,cg_z_mm,cg_source,cg_confirmed,crate_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (r['name'],moved,r['length_mm'],r['width_mm'],r['height_mm'],r['weight_kg'],r.get('weight_grade','C'),r.get('weight_source','未标注'),r.get('weight_note',''),r.get('size_grade','C'),r.get('size_source','未标注'),r.get('size_note',''),r.get('size_evidence_id'),r.get('weight_evidence_id'),
                         '木箱',r.get('stackable',1),r.get('rotatable',1),r.get('cg_x_mm'),r.get('cg_y_mm'),r.get('cg_z_mm'),r.get('cg_source','几何中心假设'),r.get('cg_confirmed',0),target_id))
        remain=int(r['qty'])-moved
        if remain>0: cur.execute('UPDATE items SET qty=? WHERE id=?',(remain,item_id))
        else: cur.execute('DELETE FROM items WHERE id=?',(item_id,))
    con.commit(); con.close(); return affected

def _cleanup_and_repack_donors(donor_ids,strategy):
    deleted=[]; repacked=[]
    for cid in donor_ids:
        items,crates=fetch_state(); c=next((x for x in crates if x['id']==cid),None)
        if not c: continue
        citems=[i for i in items if i.get('crate_id')==cid]
        if not citems:
            con=db(); cur=con.cursor(); cur.execute('DELETE FROM crate_placements WHERE crate_id=?',(cid,));cur.execute('DELETE FROM crate_audits WHERE crate_id=?',(cid,));cur.execute('DELETE FROM crates WHERE id=?',(cid,));con.commit();con.close();deleted.append(c['name']);continue
        mix=_crate_mix_class(cid,items); padding=_target_fill_padding(mix,strategy)
        units=_crate_units(cid,items)
        max_gross=float((strategy or {}).get('max_crate_gross',5000) or 5000); tare=float((strategy or {}).get('tare_per_m3',45) or 45)
        cd=_try_repack_one_crate(units,float(c['length_mm']),float(c['width_mm']),float(c['height_mm']),max_gross,padding,tare)
        if cd:
            _write_crate_layout(cid,cd,items); repacked.append(c['name'])
    return deleted,repacked

_SMART_FILL_JOBS={}
_SMART_FILL_JOBS_LOCK=threading.Lock()
_SMART_FILL_EXEC_LOCK=threading.Lock()

def _smart_job_update(job_id,progress=None,stage=None,detail=None,**extra):
    with _SMART_FILL_JOBS_LOCK:
        job=_SMART_FILL_JOBS.get(job_id)
        if not job: return
        if progress is not None: job['progress']=max(0,min(100,int(progress)))
        if stage is not None: job['stage']=stage
        if detail is not None: job['detail']=detail
        job.update(extra)
        job['updated_at']=time.time()

def _smart_job_cancelled(job_id):
    with _SMART_FILL_JOBS_LOCK:
        return bool((_SMART_FILL_JOBS.get(job_id) or {}).get('cancel_requested'))

def _execute_smart_fill(crate_id,progress=None,cancel_check=None):
    _require_content_crate(crate_id)
    items,crates=fetch_state(); stats={c['id']:crate_stats(c,items) for c in crates}
    before_crate_count=len(crates)
    target=next((c for c in crates if c['id']==crate_id),None)
    if not target: raise ValueError('未找到指定木箱。')
    strategy=_strategy_load() or {}
    if progress: progress(3,'初始化',f"目标箱 {target['name']}：正在读取材料、木箱和工程约束…",candidate_current=0,candidate_total=0,trials=0,best='尚未找到可行方案',stage_progress=5)
    sim=_simulate_material_fill(crate_id,items,crates,stats,strategy,max_trials=200000,fast=False,progress=progress,cancel_check=cancel_check,time_budget_s=20.0)
    if cancel_check and cancel_check(): raise RuntimeError('__CANCELLED__')
    if not sim:
        rule='5083目标箱只搜索5083材料；非5083目标箱搜索所有非5083材料。'
        raise ValueError('多候选3D启发式精度搜索后，没有找到可整体消箱或可通过3D尺寸、毛重、边界和支撑校核的填充材料。'+rule)
    if progress: progress(92,'执行搬运',f"搜索完成，正在把 {sim['moved_count']} 件材料写入目标箱…",best=f"利用率预计 {sim['before_util']}% → {sim['after_util']}%",stage_progress=35)
    affected=_transfer_item_units_to_target(crate_id,sim['moved_by_item'])
    if cancel_check and cancel_check(): raise RuntimeError('__CANCELLED_AFTER_WRITE__')
    items2,crates2=fetch_state(); units=_crate_units(crate_id,items2)
    mix=_crate_mix_class(crate_id,items2); padding=_target_fill_padding(mix,strategy)
    max_gross=float((strategy or {}).get('max_crate_gross',5000) or 5000); tare=float((strategy or {}).get('tare_per_m3',45) or 45)
    target2=next(c for c in crates2 if c['id']==crate_id)
    if progress: progress(95,'目标箱复排','材料已转移，正在重新生成目标箱3D布局并校核边界/支撑/毛重…',best=f"已搬入 {sim['moved_count']} 件",stage_progress=60)
    cd=_try_repack_one_crate(units,float(target2['length_mm']),float(target2['width_mm']),float(target2['height_mm']),max_gross,padding,tare)
    if not cd: raise RuntimeError('材料转移后目标箱复排失败，已停止。')
    _write_crate_layout(crate_id,cd,items2)
    if progress: progress(97,'来源箱清理',f'正在重排 {len(affected)} 个受影响来源箱，并删除被搬空的木箱…',best=f"已搬入 {sim['moved_count']} 件",stage_progress=80)
    deleted,repacked=_cleanup_and_repack_donors(affected,strategy)
    items3,crates3=fetch_state(); after_crate_count=len(crates3)
    st3=crate_stats(next(c for c in crates3 if c['id']==crate_id),items3)
    eliminated=before_crate_count-after_crate_count
    if eliminated>0:
        elimination_txt=f'；成功消箱 {eliminated} 个，木箱总数 {before_crate_count}→{after_crate_count}'
        if deleted: elimination_txt+='（删除：'+','.join(deleted)+'）'
    else:
        elimination_txt=f'；本次未能完整搬空来源箱，木箱总数仍为 {after_crate_count}'
    donor_txt=''
    if sim.get('fully_absorbed_donors'):
        donor_txt=f"；算法优先选择了 {len(sim['fully_absorbed_donors'])} 个可整体搬空的来源箱"
    elif sim.get('partial_donors'):
        donor_txt='；没有整箱可完全吸收，才执行了单一来源箱的局部填空'
    cabinet='；本次材料级填空使用完整候选搜索，集装箱方案将在页面刷新后更新'
    msg=(f"消箱优先智能填空完成：{target['name']} 搬入 {sim['moved_count']} 件材料；"
         f"实际外包络利用率约 {sim['before_util']}%→{st3['utilization']}%；"
         f"间隙规则：{'5083防摩擦10mm' if mix=='5083' else '非5083额外保护间隙0mm'}"
         f"{donor_txt}{elimination_txt}{cabinet}。")
    if progress: progress(100,'完成',msg,best=f"木箱总数 {before_crate_count} → {after_crate_count}；目标箱利用率 {st3['utilization']}%",stage_progress=100)
    return msg

def _smart_fill_worker(job_id,crate_id):
    started=time.time()
    def cb(p,stage,detail,**extra):
        _smart_job_update(job_id,p,stage,detail,elapsed=round(time.time()-started,1),**extra)
    try:
        with _SMART_FILL_EXEC_LOCK:
            if _smart_job_cancelled(job_id):
                _smart_job_update(job_id,0,'已取消','任务在启动前已取消。',status='cancelled',elapsed=round(time.time()-started,1)); return
            _smart_job_update(job_id,1,'初始化','多候选3D启发式任务已启动。',status='running',elapsed=0)
            msg=_execute_smart_fill(crate_id,progress=cb,cancel_check=lambda:_smart_job_cancelled(job_id))
            _smart_job_update(job_id,100,'完成',msg,status='done',message=msg,elapsed=round(time.time()-started,1))
    except RuntimeError as e:
        if str(e) in {'__CANCELLED__','__CANCELLED_AFTER_WRITE__'}:
            detail='计算已取消。' if str(e)=='__CANCELLED__' else '取消请求发生在材料写入之后；系统已停止后续搜索，请刷新页面检查当前状态。'
            _smart_job_update(job_id,None,'已取消',detail,status='cancelled',elapsed=round(time.time()-started,1))
        else:
            _smart_job_update(job_id,None,'失败',str(e),status='error',error=str(e),elapsed=round(time.time()-started,1))
    except Exception as e:
        logger.exception('smart fill background failed')
        _smart_job_update(job_id,None,'失败',str(e),status='error',error=str(e),elapsed=round(time.time()-started,1))

@app.post('/smart-fill-crate/start')
def smart_fill_crate_start(crate_id:int=Form(...)):
    job_id=uuid.uuid4().hex
    now=time.time()
    with _SMART_FILL_JOBS_LOCK:
        # prune old completed jobs
        for k in list(_SMART_FILL_JOBS):
            j=_SMART_FILL_JOBS[k]
            if j.get('status') in {'done','error','cancelled'} and now-float(j.get('updated_at') or now)>3600:
                _SMART_FILL_JOBS.pop(k,None)
        _SMART_FILL_JOBS[job_id]={
            'job_id':job_id,'crate_id':crate_id,'status':'queued','progress':0,'stage':'排队中',
            'detail':'任务已提交，等待计算资源…','candidate_current':0,'candidate_total':0,'trials':0,
            'best':'尚未找到可行方案','cancel_requested':False,'created_at':now,'updated_at':now,'elapsed':0
        }
    threading.Thread(target=_smart_fill_worker,args=(job_id,crate_id),daemon=True).start()
    return JSONResponse({'ok':True,'job_id':job_id})

@app.get('/smart-fill-crate/status/{job_id}')
def smart_fill_crate_status(job_id:str):
    with _SMART_FILL_JOBS_LOCK:
        job=dict(_SMART_FILL_JOBS.get(job_id) or {})
    if not job: return JSONResponse({'ok':False,'error':'任务不存在或已过期。'},status_code=404)
    return JSONResponse({'ok':True,**job})

@app.post('/smart-fill-crate/cancel/{job_id}')
def smart_fill_crate_cancel(job_id:str):
    with _SMART_FILL_JOBS_LOCK:
        job=_SMART_FILL_JOBS.get(job_id)
        if not job: return JSONResponse({'ok':False,'error':'任务不存在或已过期。'},status_code=404)
        if job.get('status') in {'done','error','cancelled'}: return JSONResponse({'ok':True,'status':job.get('status')})
        job['cancel_requested']=True; job['detail']='已请求取消，正在等待当前3D试排步骤结束…'; job['updated_at']=time.time()
    return JSONResponse({'ok':True,'status':'cancelling'})

@app.post('/smart-fill-crate')
def smart_fill_crate(crate_id:int=Form(...)):
    # Compatibility fallback for old pages/bookmarks.
    try:
        msg=_execute_smart_fill(crate_id)
        return RedirectResponse(url=f'/?message={quote(msg)}',status_code=303)
    except Exception as e:
        return RedirectResponse(url=f'/?error={quote(str(e))}',status_code=303)

def _focused_pair_merge_simulation(ca,cb,items,crates,params,current_target_count,mix_class='OTHER'):
    if _is_prepacked(ca) or _is_prepacked(cb): return None
    """Focused merge ignores count/SKU limits.
    5083 is isolated; all non-5083 grades are treated as one compatible merge family.
    Hard engineering constraints remain: dimensions, gross weight, 3D collision/boundary/support.
    """
    local=dict(params)
    local['max_items']=0
    local['max_skus']=0
    local['target_min']=0
    local['target_max']=92

    # _pair_merge_simulation re-packs the two crates using the normal crate packer.
    # Give those temporary item dicts a merge-family override so Q235B/20#/304/316L etc.
    # can share a crate, while 5083 still remains isolated from all others.
    patched=[]
    ids={ca['id'],cb['id']}
    for i in items:
        d=dict(i)
        if d.get('crate_id') in ids:
            d['_merge_family_override']='AL5083' if mix_class=='5083' else 'OTHER'
        patched.append(d)
    return _pair_merge_simulation(ca,cb,patched,crates,local,current_target_count)

def _focused_merge_best(crate_id,items,crates,stats,strategy):
    target=next((c for c in crates if c["id"]==crate_id),None)
    if not target: return None
    target_min=float((strategy or {}).get("target_util_min",60) or 60)
    target_max=float((strategy or {}).get("target_util_max",80) or 80)
    params={
        "max_l":float((strategy or {}).get("recommended_l",4000) or 4000),
        "max_w":float((strategy or {}).get("recommended_w",2200) or 2200),
        "max_h":float((strategy or {}).get("recommended_h",2200) or 2200),
        "max_gross":float((strategy or {}).get("max_crate_gross",5000) or 5000),
        "padding":_padding_setting(strategy),
        "tare_per_m3":float((strategy or {}).get("tare_per_m3",45) or 45),
        "target_min":target_min,"target_max":target_max,
        "long_pipe_mode":str((strategy or {}).get("long_pipe_mode","standard") or "standard"),
        "max_pipes":int((strategy or {}).get("max_pipes_per_crate",0) or 0),
        "max_items":0,"max_skus":0,
        "target_container":str((strategy or {}).get("target_container","40HQ") or "40HQ")
    }
    mix_class=_crate_mix_class(target["id"],items)
    if mix_class=='INVALID-5083-MIX':
        return None

    packages=get_packages(items,crates)
    before=_target_plan_count_for_packages(packages,params["target_container"])
    current_u=float(stats[target["id"]].get("utilization") or 0)

    # Only compatibility filter:
    # - 5083 may merge only with pure 5083 crates.
    # - every non-5083 crate may merge with any other non-5083 crate.
    candidates=[]
    for c in crates:
        if c["id"]==target["id"]: continue
        cc=_crate_mix_class(c["id"],items)
        if cc!=mix_class: continue
        cu=float(stats[c["id"]].get("utilization") or 0)
        candidates.append((cu,c))

    sims=[]
    for _,c in candidates:
        sim=_focused_pair_merge_simulation(target,c,items,crates,params,before,mix_class)
        if not sim: continue
        improvement=float(sim["merged_util"])-current_u
        sim["improvement"]=round(improvement,1)
        sim["mix_class"]=mix_class
        if improvement>0.1 or (sim.get("container_delta") is not None and sim["container_delta"]<0):
            sims.append(sim)
    if not sims: return None

    sims.sort(key=lambda s:(
        0 if s.get("container_delta") is not None and s["container_delta"]<0 else 1,
        -float(s.get("merged_util",0)),
        -float(s.get("improvement",0)),
        float(s.get("merged_gross",0))
    ))
    return sims[0]

def _execute_focused_merge(target_id,sim):
    _require_content_crate(target_id)
    _require_content_crate(int(sim['partner_id']))
    partner_id=int(sim["partner_id"]); cd=sim["_cd"]
    con=db(); cur=con.cursor()
    # Reassign all partner material rows to the target crate before rewriting the 3D layout.
    cur.execute("UPDATE items SET crate_id=? WHERE crate_id=?",(target_id,partner_id))
    # The target crate contents have changed; any previous scale ticket no longer proves its current gross weight.
    invalidate_crate_gross(con,target_id,'合箱后箱内材料变化，待重新实称')
    cur.execute("DELETE FROM crate_placements WHERE crate_id=?",(partner_id,))
    cur.execute("DELETE FROM crate_audits WHERE crate_id=?",(partner_id,))
    cur.execute("DELETE FROM crates WHERE id=?",(partner_id,))
    con.commit(); con.close()
    items,_=fetch_state()
    _write_crate_layout(target_id,cd,items)

def _repack_existing_crate(crate_id):
    con_origin=db(); origin=con_origin.execute("SELECT package_origin FROM crates WHERE id=?",(crate_id,)).fetchone(); con_origin.close()
    if origin and origin["package_origin"]=="PREPACKED": return None
    """Rebuild one existing crate after any manual data mutation. Raises on invalid geometry."""
    items,crates=fetch_state(); crate=next((c for c in crates if c['id']==crate_id),None)
    if not crate: return None
    units=_crate_units(crate_id,items)
    if not units:
        con=db(); con.execute('DELETE FROM crate_placements WHERE crate_id=?',(crate_id,)); con.execute('DELETE FROM crate_audits WHERE crate_id=?',(crate_id,)); con.commit(); con.close(); return None
    strategy=_strategy_load() or {}
    padding=_padding_setting(strategy); tare_per_m3=float(strategy.get('tare_per_m3',45) or 45); max_gross=float(strategy.get('max_crate_gross',5000) or 5000)
    cd=_try_repack_one_crate(units,float(crate['length_mm']),float(crate['width_mm']),float(crate['height_mm']),max_gross,padding,tare_per_m3)
    if cd is None or cd.get('audit',{}).get('audit_status')=='不通过':
        raise ValueError(f"{crate['name']} 在当前外形尺寸下无法通过V5.8.28静态规划的结构净空间/三维碰撞/支撑/毛重校核。")
    _write_crate_layout(crate_id,cd,items)
    return cd

@app.post("/focused-crate-merge")
def focused_crate_merge(crate_id: int = Form(...)):
    try:
        items,crates=fetch_state(); stats={c["id"]:crate_stats(c,items) for c in crates}
        target=next((c for c in crates if c["id"]==crate_id),None)
        if not target: raise ValueError("未找到指定木箱。")
        strategy=_strategy_load() or {}
        sim=_focused_merge_best(crate_id,items,crates,stats,strategy)
        if not sim:
            raise ValueError("没有找到满足5083隔离规则且通过尺寸、毛重和3D工程约束的有效合箱对象。除5083外，其它材质允许互相混装。")
        before_u=float(stats[crate_id].get("utilization") or 0)
        partner=sim["partner"]
        _execute_focused_merge(crate_id,sim)
        after_count=sim.get("container_after"); before_count=sim.get("container_before")
        impact=""
        if before_count is not None and after_count is not None:
            impact=f"；目标集装箱柜数估算 {before_count}→{after_count}"
        msg=(f"定向合箱完成：{target['name']} 按‘5083隔离、其它材质可混装’规则自动选择 {partner} 合并；"
             f"外包络利用率约 {before_u:.1f}%→{sim['merged_util']:.1f}%，新箱约 {sim['merged_dims']} mm，"
             f"毛重约 {sim['merged_gross']} kg{impact}；已删除多余木箱 {partner}。")
        return RedirectResponse(url=f"/?message={quote(msg)}",status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/?error={quote(str(e))}",status_code=303)

@app.post("/apply-crate-shrink")
def apply_crate_shrink(crate_id: int = Form(...)):
    try:
        items,crates=fetch_state(); stats={c["id"]:crate_stats(c,items) for c in crates}; crate=next((c for c in crates if c["id"]==crate_id),None)
        if not crate: raise ValueError("未找到指定木箱。")
        strategy=_strategy_load() or {}; target=str(strategy.get("target_container","40HQ") or "40HQ"); before=_target_plan_count_for_packages(get_packages(items,crates),target)
        rec=_recommend_shrink_for_crate(crate,items,crates,stats,strategy,before)
        if not rec: raise ValueError("该木箱当前没有通过3D复排验证的缩箱建议。")
        _write_crate_layout(crate_id,rec["_cd"],items)
        msg=f"{crate['name']} 已由 {rec['current_dims']} mm 缩至 {rec['recommended_dims']} mm；利用率 {rec['current_util']}% → {rec['recommended_util']}%，箱体体积减少约 {rec['volume_saving_pct']}%。"
        return RedirectResponse(url=f"/?message={quote(msg)}",status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/?error={quote(str(e))}",status_code=303)

@app.post("/reoptimize-low-util")
def reoptimize_low_util(crate_name: str = Form("")):
    try:
        strategy=_strategy_load() or {}
        target=strategy.get("target_container","40HQ")
        l=float(strategy.get("recommended_l",4000)); w=float(strategy.get("recommended_w",2200)); h=float(strategy.get("recommended_h",2200))
        tmin=float(strategy.get("target_util_min",60)); tmax=float(strategy.get("target_util_max",80))
        lpm=strategy.get("long_pipe_mode","standard"); maxpipes=int(strategy.get("max_pipes_per_crate",0) or 0)
        maxitems=int(strategy.get("max_items_per_crate",30) or 0); maxskus=int(strategy.get("max_skus_per_crate",8) or 0)
        # Low-util trigger uses global reassignment because true merging can affect neighboring crates.
        count,oversize,_precision=apply_smart_crates(l,w,h,5000,100,45,exact_fixed=False,optimize_util=True,target_min=tmin,target_max=tmax,long_pipe_mode=lpm,max_pipes_per_crate=maxpipes,max_items_per_crate=maxitems,max_skus_per_crate=maxskus)
        msg=f"已以 {crate_name or '低利用率木箱'} 为触发点执行全局合箱/重排；重新生成 {count} 个木箱。"
        if oversize: msg+=f" 其中 {oversize} 个为超限定制箱。"
        return RedirectResponse(url=f"/?message={quote(msg)}",status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/?error={quote(str(e))}",status_code=303)

@app.post("/pipe-bundles")
def pipe_bundles(ship_length_mm: float = Form(6000), max_bundle_weight: float = Form(3000), max_bundle_width: float = Form(1200), max_bundle_height: float = Form(1200)):
    try:
        count,pipes,sources=rebuild_pipe_bundles(ship_length_mm,max_bundle_weight,max_bundle_width,max_bundle_height)
        return RedirectResponse(url=f"/?message={quote(f'碳钢管道扎捆已重算：{sources} 条源记录，规划 {pipes} 根，生成 {count} 个裸装管束。')}",status_code=303)
    except Exception as e:
        return RedirectResponse(url=f"/?error={quote(str(e))}",status_code=303)

@app.post("/items")
def add_item(
    name: str = Form(...), qty: int = Form(...), length_mm: float = Form(...),
    width_mm: float = Form(...), height_mm: float = Form(...), weight_kg: float = Form(...),
    package_type: str = Form(...), stackable: int = Form(1), rotatable: int = Form(1),
    cg_x_mm: str = Form(''), cg_y_mm: str = Form(''), cg_z_mm: str = Form(''), cg_source_type: str = Form('UNCONFIRMED')
):
    try: checked_item_values({'qty':qty,'length_mm':length_mm,'width_mm':width_mm,'height_mm':height_mm,'weight_kg':weight_kg})
    except ValueError as e: raise HTTPException(422,str(e))
    vals=[]
    for raw,lim,label in ((cg_x_mm,length_mm,'CG-X'),(cg_y_mm,width_mm,'CG-Y'),(cg_z_mm,height_mm,'CG-Z')):
        if str(raw).strip()=='': vals.append(None); continue
        v=checked_number(raw,label)
        if v>float(lim): raise ValueError(f'{label} 必须位于物料外形范围内。')
        vals.append(v)
    src=_normalize_physical_source(cg_source_type); cg_ok=all(v is not None for v in vals) and _physical_source_verified(src)
    con=db()
    con.execute("""INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,weight_note,size_grade,size_source,size_note,package_type,stackable,rotatable,cg_x_mm,cg_y_mm,cg_z_mm,cg_source,cg_source_type,cg_confirmed)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (name,qty,length_mm,width_mm,height_mm,weight_kg,"B","人工输入重量（未确认）","由用户手工输入的单件重量；箱级数据对照版前需明确确认。","B","人工输入尺寸（未确认）","请确认实测/供应商批准后升级A级。",package_type,stackable,rotatable,*vals,_physical_source_label(src) if cg_ok else '几何中心假设',src,1 if cg_ok else 0))
    con.commit(); con.close()
    return JSONResponse({"ok":True})

@app.post("/crates")
def add_crate(name: str = Form(...), length_mm: float = Form(...), width_mm: float = Form(...),
              height_mm: float = Form(...), tare_weight_kg: float = Form(120)):
    try:
        for value,label in ((length_mm,'长度'),(width_mm,'宽度'),(height_mm,'高度')): checked_number(value,label,strict=True)
        checked_number(tare_weight_kg,'箱皮重量')
    except ValueError as e: raise HTTPException(422,str(e))
    con=db(); con.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,tare_weight_kg) VALUES(?,?,?,?,?)",
                (name,length_mm,width_mm,height_mm,tare_weight_kg)); con.commit(); con.close()
    return JSONResponse({"ok":True})

# V5.1 credibility confirmation routes are registered from app.routes_credibility near the end of this module.

@app.post('/items/{item_id}/cg')
async def set_item_cg(item_id:int, cg_x_mm:str=Form(''), cg_y_mm:str=Form(''), cg_z_mm:str=Form(''), cg_source_type:str=Form('UNCONFIRMED'), cg_source:str=Form(''),
                      evidence:UploadFile=File(None), evidence_cg_x_mm:str=Form(''), evidence_cg_y_mm:str=Form(''), evidence_cg_z_mm:str=Form(''), evidence_note:str=Form('')):
    con=db(); rr=con.execute('SELECT * FROM items WHERE id=?',(item_id,)).fetchone()
    if not rr: con.close(); return RedirectResponse(url='/?error='+quote('未找到材料。'),status_code=303)
    row=dict(rr); vals=[]
    try:
        for raw,lim,label in ((cg_x_mm,row['length_mm'],'CG-X'),(cg_y_mm,row['width_mm'],'CG-Y'),(cg_z_mm,row['height_mm'],'CG-Z')):
            if str(raw).strip()=='': vals.append(None); continue
            v=checked_number(raw,label)
            if v>float(lim): raise ValueError(f'{label} 必须位于物料外形范围内。')
            vals.append(v)
        src=_normalize_physical_source(cg_source_type); planning_confirmed=all(v is not None for v in vals) and _physical_source_verified(src)
        note=(cg_source.strip() or _physical_source_label(src)) if planning_confirmed else '几何中心假设/来源未确认'
        proposed=dict(row); proposed.update(cg_x_mm=vals[0],cg_y_mm=vals[1],cg_z_mm=vals[2],cg_source_type=src,cg_confirmed=1 if planning_confirmed else 0)
        evidence_id=row.get('cg_evidence_id')
        if evidence_id:
            linked=_evidence_matches(evidence_id,scope_type='item',scope_key=str(item_id),evidence_types=('item_cg_record',),entity_types=('item',),entity_ids=(str(item_id),),require_verified=True)
            if not linked or not evidence_claim_matches_current(_evidence_summary(evidence_id),proposed,'item_cg')[0]: evidence_id=None
        if evidence and getattr(evidence,'filename',None):
            if not planning_confirmed: raise ValueError('登记CG凭证前，必须填写完整三轴CG并选择明确来源。')
            ev_vals=[]
            for raw,label in ((evidence_cg_x_mm,'凭证CG-X'),(evidence_cg_y_mm,'凭证CG-Y'),(evidence_cg_z_mm,'凭证CG-Z')):
                if str(raw).strip()=='': raise ValueError(f'{label}不能为空。请由责任人查看附件后手工录入可见数值。')
                ev_vals.append(float(raw))
            problems=[]
            for a,b,label in zip(vals,ev_vals,('CG-X','CG-Y','CG-Z')):
                ok,_=numeric_close(a,b,abs_tol=1.0,rel_tol=0.001)
                if not ok: problems.append(f'{label}：系统 {a:g} mm / 凭证核对值 {b:g} mm')
            if problems: raise ValueError('人工读取的CG凭证值与当前CG不一致：'+'；'.join(problems))
            user=_confirmation_user(); raw=await evidence.read(); ver={'ok':True,'method':'人工读取CG凭证可见数值后录入对照；软件未OCR读取正文'}
            ev=EVIDENCE_STORE.save_evidence(entity_type='item',entity_id=item_id,evidence_type='item_cg_record',filename=evidence.filename,content=raw,note=evidence_note or note,created_by=user,
                scope_type='item',scope_key=str(item_id),claimed_values={'cg_x_mm':ev_vals[0],'cg_y_mm':ev_vals[1],'cg_z_mm':ev_vals[2],'source_type':src},verification=ver)
            evidence_id=ev['id']
            _record_credibility_event('confirm_item_cg','item',item_id,row.get('name') or '',{'evidence_id':evidence_id,'sha256':ev['sha256'],'source_type':src,'method':ver['method']})
        crate_id=row.get('crate_id')
        con.execute('UPDATE items SET cg_x_mm=?,cg_y_mm=?,cg_z_mm=?,cg_source=?,cg_source_type=?,cg_confirmed=?,cg_evidence_id=? WHERE id=?',(*vals,note,src,1 if planning_confirmed else 0,evidence_id,item_id)); con.commit(); con.close()
        _PERF_CACHE['fast'].clear(); _PERF_CACHE['deep'].clear()
        if crate_id: _repack_existing_crate(int(crate_id))
        return RedirectResponse(url='/?message='+quote('物料CG已保存。来源确认可用于DRAFT规划；只有绑定有效CG附件并完成人工数值对照，正式对照输出才视为证据闭环。'),status_code=303)
    except Exception as e:
        con.close(); return RedirectResponse(url='/?error='+quote(str(e)),status_code=303)

@app.post('/crates/{crate_id}/physical')
async def set_crate_physical(crate_id:int, max_top_load_kg:str=Form(''), top_load_confirmed:int=Form(0), top_load_source_type:str=Form('UNCONFIRMED'), top_load_source_note:str=Form(''), lashing_required:int=Form(0), lashing_points:int=Form(0), lashing_confirmed:int=Form(0), handling_note:str=Form(''),
                       contact_area_mode:str=Form('UNSPECIFIED'), contact_area_source_type:str=Form('UNCONFIRMED'), contact_area_source_note:str=Form(''), skid_count:int=Form(0), skid_width_mm:str=Form(''), skid_contact_length_mm:str=Form(''), custom_contact_area_m2:str=Form(''),
                       top_load_evidence:UploadFile=File(None), evidence_top_load_kg:str=Form(''), top_load_evidence_note:str=Form(''),
                       contact_area_evidence:UploadFile=File(None), evidence_contact_area_m2:str=Form(''), contact_area_evidence_note:str=Form(''),
                       lashing_evidence:UploadFile=File(None), evidence_lashing_points:str=Form(''), lashing_evidence_note:str=Form('')):
    con=db(); rr=con.execute('SELECT * FROM crates WHERE id=?',(crate_id,)).fetchone()
    if not rr: con.close(); return RedirectResponse(url='/?error='+quote('未找到木箱。'),status_code=303)
    row=dict(rr)
    try:
        top=None if str(max_top_load_kg).strip()=='' else checked_number(max_top_load_kg,'顶载')
        top_src=_normalize_physical_source(top_load_source_type)
        if int(top_load_confirmed or 0) and top is not None and not _physical_source_verified(top_src): raise ValueError('确认顶载必须选择明确来源：厂家资料/现场实测/包装供应商批准/工程计算。')
        tc=1 if int(top_load_confirmed or 0) and top is not None and _physical_source_verified(top_src) else 0
        lp=checked_number(lashing_points,'系固点数',integer=True); lr=1 if int(lashing_required or 0) else 0; lc=1 if int(lashing_confirmed or 0) and lp>0 else 0
        mode=str(contact_area_mode or 'UNSPECIFIED').upper().strip(); contact_src=_normalize_physical_source(contact_area_source_type)
        if mode not in {'UNSPECIFIED','FULL_BASE','SKIDS','CUSTOM'}: raise ValueError('接触面积模式无效。')
        sc=checked_number(skid_count,'滑木数量',integer=True); sw=None if str(skid_width_mm).strip()=='' else checked_number(skid_width_mm,'滑木宽度'); sl=None if str(skid_contact_length_mm).strip()=='' else checked_number(skid_contact_length_mm,'滑木长度'); ca=None if str(custom_contact_area_m2).strip()=='' else checked_number(custom_contact_area_m2,'接触面积')
        if mode=='SKIDS' and not (sc>0 and sw and sl): raise ValueError('滑木接触面积模式需要填写滑木数量、宽度和有效接触长度。')
        if mode=='CUSTOM' and not (ca and ca>0): raise ValueError('自定义接触面积模式需要填写有效接触面积 m²。')
        proposed=dict(row); proposed.update(max_top_load_kg=top,top_load_confirmed=tc,top_load_source_type=top_src,lashing_required=lr,lashing_points=lp,lashing_confirmed=lc,contact_area_mode=mode,contact_area_source_type=contact_src,skid_count=sc,skid_width_mm=sw,skid_contact_length_mm=sl,custom_contact_area_m2=ca)
        validate_contact_geometry(proposed)
        if mode=='FULL_BASE': effective_area=float(row.get('length_mm') or 0)*float(row.get('width_mm') or 0)/1e6
        elif mode=='SKIDS': effective_area=sc*float(sw or 0)*float(sl or 0)/1e6
        elif mode=='CUSTOM': effective_area=float(ca or 0)
        else: effective_area=0.0
        def keep_existing(eid,etype,kind):
            if not eid: return None
            linked=_evidence_matches(eid,scope_type='item',scope_key=str(crate_id),evidence_types=(etype,),entity_types=('crate',),entity_ids=(str(crate_id),),require_verified=True)
            return eid if linked and evidence_claim_matches_current(_evidence_summary(eid),proposed,kind)[0] else None
        top_eid=keep_existing(row.get('top_load_evidence_id'),'crate_top_load_record','crate_top_load')
        contact_eid=keep_existing(row.get('contact_area_evidence_id'),'crate_contact_area_record','crate_contact_area')
        lashing_eid=keep_existing(row.get('lashing_evidence_id'),'crate_lashing_plan','crate_lashing')
        # If the current planning state no longer claims confirmation, the old formal binding
        # must not survive merely because its previous numeric claim happened to match.
        if not tc: top_eid=None
        if mode=='UNSPECIFIED' or not _physical_source_verified(contact_src): contact_eid=None
        if not (lr and lc and lp>0): lashing_eid=None

        upload_top=bool(top_load_evidence and getattr(top_load_evidence,'filename',None))
        upload_contact=bool(contact_area_evidence and getattr(contact_area_evidence,'filename',None))
        upload_lashing=bool(lashing_evidence and getattr(lashing_evidence,'filename',None))
        pending=[]
        user=_confirmation_user() if (upload_top or upload_contact or upload_lashing) else ''

        # Phase 1: validate *all* human-entered comparison values.  Nothing is registered yet.
        if upload_top:
            if not tc: raise ValueError('登记顶载凭证前，必须填写顶载、勾选顶载值已确认并选择明确来源。')
            if str(evidence_top_load_kg).strip()=='': raise ValueError('请输入人工读取的凭证顶载核对值 kg。')
            evv=float(evidence_top_load_kg); ok,_=numeric_close(top,evv,abs_tol=1.0,rel_tol=0.001)
            if not ok: raise ValueError(f'顶载凭证核对值 {evv:g} kg 与当前顶载 {top:g} kg 不一致。')
            raw=await top_load_evidence.read(); ver={'ok':True,'method':'人工读取顶载凭证可见数值后录入对照；软件未OCR读取正文'}
            pending.append(('top',{'entity_type':'crate','entity_id':crate_id,'evidence_type':'crate_top_load_record','filename':top_load_evidence.filename,'content':raw,
                'note':top_load_evidence_note or top_load_source_note,'created_by':user,'scope_type':'item','scope_key':str(crate_id),
                'claimed_values':{'max_top_load_kg':evv,'source_type':top_src},'verification':ver}))
        if upload_contact:
            if mode=='UNSPECIFIED' or not _physical_source_verified(contact_src): raise ValueError('登记接触面积凭证前，必须选择明确接触模式和来源。')
            if str(evidence_contact_area_m2).strip()=='': raise ValueError('请输入人工读取/计算书中的有效接触面积核对值 m²。')
            evv=float(evidence_contact_area_m2); ok,_=numeric_close(effective_area,evv,abs_tol=0.0001,rel_tol=0.001)
            if not ok: raise ValueError(f'接触面积凭证核对值 {evv:g} m² 与当前有效接触面积 {effective_area:g} m² 不一致。')
            raw=await contact_area_evidence.read(); ver={'ok':True,'method':'人工读取接触面积凭证可见数值后录入对照；软件未OCR读取正文'}
            pending.append(('contact',{'entity_type':'crate','entity_id':crate_id,'evidence_type':'crate_contact_area_record','filename':contact_area_evidence.filename,'content':raw,
                'note':contact_area_evidence_note or contact_area_source_note,'created_by':user,'scope_type':'item','scope_key':str(crate_id),
                'claimed_values':{'effective_contact_area_m2':evv,'mode':mode,'source_type':contact_src},'verification':ver}))
        if upload_lashing:
            if not (lr and lc and lp>0): raise ValueError('登记系固凭证前，必须勾选要求系固、填写系固点数量并确认方案。')
            if str(evidence_lashing_points).strip()=='': raise ValueError('请输入凭证可见的系固点数量。')
            ep=int(float(evidence_lashing_points))
            if ep!=lp: raise ValueError(f'系固凭证点数 {ep} 与当前登记 {lp} 不一致。')
            raw=await lashing_evidence.read(); ver={'ok':True,'method':'人工查看系固方案/图纸后录入点数对照；软件未自动判断绑扎力'}
            pending.append(('lashing',{'entity_type':'crate','entity_id':crate_id,'evidence_type':'crate_lashing_plan','filename':lashing_evidence.filename,'content':raw,
                'note':lashing_evidence_note or handling_note,'created_by':user,'scope_type':'item','scope_key':str(crate_id),
                'claimed_values':{'lashing_required':True,'lashing_points':ep},'verification':ver}))

        # Phase 2: the EvidenceStore validates every file format first, then writes the batch
        # in one DB transaction.  A later bad attachment cannot leave earlier orphan records.
        if pending:
            saved=EVIDENCE_STORE.save_evidence_batch([entry for _,entry in pending])
            for (kind,_),ev in zip(pending,saved):
                if kind=='top': top_eid=ev['id']
                elif kind=='contact': contact_eid=ev['id']
                elif kind=='lashing': lashing_eid=ev['id']
        con.execute('UPDATE crates SET max_top_load_kg=?,top_load_confirmed=?,top_load_source_type=?,top_load_source_note=?,lashing_required=?,lashing_points=?,lashing_confirmed=?,handling_note=?,contact_area_mode=?,contact_area_source_type=?,contact_area_source_note=?,skid_count=?,skid_width_mm=?,skid_contact_length_mm=?,custom_contact_area_m2=?,top_load_evidence_id=?,contact_area_evidence_id=?,lashing_evidence_id=? WHERE id=?',
                    (top,tc,top_src,top_load_source_note.strip(),lr,lp,lc,handling_note.strip(),mode,contact_src,contact_area_source_note.strip(),sc,sw,sl,ca,top_eid,contact_eid,lashing_eid,crate_id)); con.commit(); con.close()
        _PERF_CACHE['fast'].clear(); _PERF_CACHE['deep'].clear()
        _record_credibility_event('save_crate_physical','crate',crate_id,row.get('name') or '',{'top_load_evidence_id':top_eid,'contact_area_evidence_id':contact_eid,'lashing_evidence_id':lashing_eid})
        return RedirectResponse(url='/?message='+quote('木箱静态物理输入已保存。来源/数值继续用于DRAFT；附件人工对照决定正式对照资料是否完成工程输入证据闭环。'),status_code=303)
    except Exception as e:
        con.close(); return RedirectResponse(url='/?error='+quote(str(e)),status_code=303)

@app.get('/benchmark-template.csv')
def benchmark_template():
    p=DATA_DIR/'benchmark_template.csv'
    p.write_text('project_name,actual_crates,actual_containers,actual_cbm,actual_tare_kg,note\n示例项目,10,2,36.5,1250,请填写已完成项目的真实包装结果\n',encoding='utf-8-sig')
    return FileResponse(p,filename='历史项目对照模板.csv')

@app.post('/benchmark/save')
async def save_benchmark(project_name:str=Form(...), actual_crates:int=Form(...), actual_containers:int=Form(...), actual_cbm:float=Form(0), actual_tare_kg:float=Form(0), evidence:UploadFile=File(...), note:str=Form('')):
    try:
        if actual_crates<0 or actual_containers<0 or actual_cbm<0 or actual_tare_kg<0: raise ValueError('历史项目实际数据不能为负数。')
        raw=await evidence.read()
        ev=EVIDENCE_STORE.save_evidence(entity_type='benchmark',entity_id=project_name,evidence_type='actual_completed_packing_record',filename=evidence.filename,content=raw,note=note,created_by=str((_release_meta_load() or {}).get('approver') or ''),scope_type='project',scope_key=project_name,claimed_values={'actual_crates':actual_crates,'actual_containers':actual_containers,'actual_cbm':actual_cbm,'actual_tare_kg':actual_tare_kg},verification={'ok':True,'method':'人工历史对照录入；软件未从附件自动提取数值'})
        items,crates=fetch_state(); rec=recommend_containers(items,crates,deep=False); top=(rec.get('options') or [{}])[0]
        active=[c for c in crates if any(i.get('crate_id')==c.get('id') for i in items)]
        pcbm=sum(float(c.get('length_mm') or 0)*float(c.get('width_mm') or 0)*float(c.get('height_mm') or 0)/1e9 for c in active)
        ptare=sum(float(crate_stats(c,items).get('tare_weight') or 0) for c in active)
        planner_containers=int(top.get('count') or 0)
        con=db()
        con.execute("INSERT INTO benchmark_records(project_name,actual_crates,actual_containers,actual_cbm,actual_tare_kg,planner_crates,planner_containers,planner_cbm,planner_tare_kg,evidence_id,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(project_name.strip(),actual_crates,actual_containers,actual_cbm,actual_tare_kg,len(active),planner_containers,pcbm,ptare,ev['id'],note,time.strftime('%Y-%m-%d %H:%M:%S')))
        con.commit(); con.close()
        _record_credibility_event('save_benchmark','benchmark','',project_name,{'evidence_id':ev['id'],'actual_crates':actual_crates,'planner_crates':len(active),'actual_containers':actual_containers,'planner_containers':planner_containers})
        return RedirectResponse(url='/?message='+quote('历史项目对照已保存。当前仅用于人工比较偏差，不会自动校准求解器。'),status_code=303)
    except Exception as e:
        return RedirectResponse(url='/?error='+quote('历史项目对照保存失败：'+str(e)),status_code=303)


def _build_audit_bundle_path(prefix='V5.8.28_本地凭证审计包'):
    EXPORT_DIR.mkdir(parents=True,exist_ok=True)
    ts=time.strftime('%Y%m%d_%H%M%S'); out=EXPORT_DIR/f'{prefix}_{ts}.zip'
    con=db()
    try:
        evidence=[dict(r) for r in con.execute('SELECT * FROM evidence_files ORDER BY id').fetchall()]
        events=[dict(r) for r in con.execute('SELECT * FROM credibility_events ORDER BY id').fetchall()]
        releases=[dict(r) for r in con.execute('SELECT * FROM release_audit_log ORDER BY id').fetchall()]
        benchmarks=[dict(r) for r in con.execute('SELECT * FROM benchmark_records ORDER BY id').fetchall()]
        item_evidence_links=[dict(r) for r in con.execute('SELECT * FROM item_evidence_links ORDER BY item_id,evidence_id')]
    finally: con.close()
    problems=EVIDENCE_STORE.verify_chains()
    cstate=_container_specs_state()
    manifest={'version':'5.8.28','generated_at':time.strftime('%Y-%m-%d %H:%M:%S'),'project':_release_meta_load(),
              'container_specs_state':{'mode':cstate.get('mode'),'sha256':cstate.get('sha256'),'error':cstate.get('error')},
              'chain_ok':not problems,'chain_problems':problems,'evidence_files':evidence,
              'credibility_events':events,'release_audit_log':releases,'benchmark_records':benchmarks,'item_evidence_links':item_evidence_links,'asu_profile':_asu_profile(),'asu_records':ASU.records(),
              'disclaimer':'本审计包用于本地记录完整性复核，不是第三方数字签名、公证、结构/绑扎/吊装或运输签证。'}
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True))
        for row in evidence:
            ep=EVIDENCE_STORE.path_for(row['id'])
            if ep and ep.exists(): z.write(ep,arcname=f"evidence/{int(row['id']):05d}_{Path(row['file_name']).name}")
    return out

@app.post('/export-shipment-package')
async def export_shipment_package(request:Request):
    """Full-ticket archive. Unlike box-level documents, this requires the container formal gate."""
    try:
        payload=await request.json(); scheme=str(payload.get('scheme') or ''); deep=bool(payload.get('deep'))
        items,crates=fetch_state(); rec=_apply_saved_container_layouts(recommend_containers(items,crates,deep=deep)); plan=(rec.get('plans') or {}).get(scheme)
        if not plan: return JSONResponse({'ok':False,'error':'当前没有可生成整票资料包的装柜方案。'},status_code=400)
        issues=_data_quality_release_issues(items,crates)+_formal_export_issues(plan,scheme)
        if issues: return JSONResponse({'ok':False,'error':'整票数据对照归档包门禁未通过：'+'；'.join(issues),'issues':issues},status_code=409)
        records=_packing_list_records(items,crates)
        if not records: return JSONResponse({'ok':False,'error':'当前没有箱级记录。'},status_code=400)
        meta=_release_meta_load(); approval=f"项目={meta.get('project_name','')}；单一责任确认人={meta.get('approver','')}"
        shipment_label=_shipment_output_label(plan,items,crates)
        for r in records: r['remark']=(r.get('remark') or '')+'；'+shipment_label+'；'+approval
        packing_bytes=_xlsx_fill_template(_active_packing_template_path().read_bytes(),records,PACKING_EXPORT_ALIASES)
        load_rows=_container_load_records(plan,0,items,crates)
        review_notes=_all_formal_review_notes(plan,items,crates)
        review_mark=('；含工程复核事项（非硬失败）：'+' | '.join(review_notes[:8])) if review_notes else ''
        for r in load_rows: r['remark']=(r.get('remark') or '')+'；'+shipment_label+'；'+approval+review_mark
        load_bytes=_xlsx_fill_template(truck_model.load_template(CONTAINER_LOAD_TEMPLATE_PATH.read_bytes(),plan),load_rows,CONTAINER_EXPORT_ALIASES)
        active_crates=[c for c in sorted(crates,key=lambda x:x['name']) if any(i.get('crate_id')==c['id'] for i in items)]
        marks_doc=None
        for c in active_crates:
            citems=[i for i in items if i.get('crate_id')==c['id']]
            d=_fill_shipping_mark_doc(c,citems,_active_shipping_mark_template_path())
            if marks_doc is None: marks_doc=d
            else: _append_doc_body(marks_doc,d)
        marks_io=io.BytesIO(); marks_doc.save(marks_io) if marks_doc is not None else None
        audit_path=_build_audit_bundle_path('V5.8.28_随票凭证审计包')
        ts=time.strftime('%Y%m%d_%H%M%S'); state_tag='工程复核未关闭_NOT_FOR_RELEASE' if review_notes else '静态规划校核通过'
        out=EXPORT_DIR/f'V5.8.28_整票数据对照归档包_{state_tag}_非运输签证依据_{ts}.zip'
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('01_箱级数据对照版_装箱单.xlsx',packing_bytes)
            z.writestr('02_装柜数据对照版_全部柜装载清单.xlsx',load_bytes)
            if marks_doc is not None: z.writestr('03_箱级数据对照版_全部木箱唛头.docx',marks_io.getvalue())
            z.write(audit_path,arcname='04_离机凭证审计包.zip')
            review_notes=_all_formal_review_notes(plan,items,crates)
            z.writestr('README_资料包边界.txt',('V5.8.28整票数据对照归档包（非结构/运输签证依据）\n'+('状态：工程复核未关闭 / NOT FOR RELEASE\n' if review_notes else '状态：当前无工程复核项，静态规划校核通过\n')+
                '本资料包表示：箱级尺寸/重量等数据已完成对应门禁；装柜计算使用的实际柜参数与确认时规范参数一致；硬失败/高风险项已被门禁阻止。CG/顶载/接触面积/系固若凭证未闭环，会作为工程复核事项明确列出并标记NOT FOR RELEASE。\n'+
                (('存在工程复核事项（允许带标归档，仍需责任人继续处理）：'+' | '.join(review_notes)+'\n') if review_notes else '当前无工程复核项。\n')+
                '信息提示不作为硬门禁；软件未自动读取/理解凭证正文。本资料包不是结构、绑扎、吊装、动态运输、适航、保险或法律签证依据。\n'))
        _record_release_export('shipment_package',out,items,crates,{'mode':'formal','scheme':scheme,'filename':out.name})
        return FileResponse(out,filename=out.name,headers={'X-Packing-Plan-Mode':'formal'})
    except Exception as e:
        logger.exception('export shipment package failed'); return JSONResponse({'ok':False,'error':'生成整票数据对照归档包失败：'+str(e)},status_code=500)

@app.get('/evidence/{evidence_id}')
def download_evidence(evidence_id:int):
    row=EVIDENCE_STORE.get(evidence_id); path=EVIDENCE_STORE.path_for(evidence_id)
    if not row or not path: return JSONResponse({'ok':False,'error':'凭证不存在或文件已缺失。'},status_code=404)
    return FileResponse(path,filename=row['file_name'])

@app.get('/audit-chain/verify')
def verify_audit_chain():
    problems=EVIDENCE_STORE.verify_chains()
    return {'ok':not problems,'problems':problems,'note':'本地哈希链用于检测应用内记录被修改；不是第三方数字签名或公证。'}

@app.get('/audit-bundle/export')
def export_audit_bundle():
    out=_build_audit_bundle_path()
    return FileResponse(out,filename=out.name)

@app.post("/assign")
def assign(item_id: int = Form(...), crate_id: int = Form(0)):
    if crate_id: _require_content_crate(crate_id)
    con_check=db(); item_check=con_check.execute('SELECT is_package_placeholder FROM items WHERE id=?',(item_id,)).fetchone(); con_check.close()
    if item_check and item_check['is_package_placeholder']: raise HTTPException(400,'已包装单元的占位记录不能拆分或移动')
    con=db(); row=con.execute("SELECT crate_id FROM items WHERE id=?",(item_id,)).fetchone()
    if not row: con.close(); return JSONResponse({"ok":False,"error":"材料不存在"},status_code=404)
    old_id=row['crate_id']; new_id=crate_id if crate_id else None
    con.execute("UPDATE items SET crate_id=? WHERE id=?", (new_id,item_id)); con.commit(); con.close()
    try:
        affected=list(dict.fromkeys([x for x in (old_id,new_id) if x]))
        for cid in affected: _repack_existing_crate(cid)
        # A previous scale ticket belongs to the old crate contents; moving material invalidates it.
        con=db()
        for cid in affected: invalidate_crate_gross(con,cid,'箱内材料变化后待重新实称')
        con.commit(); con.close()
        return JSONResponse({"ok":True,"engineering_revalidated":True,"gross_evidence_invalidated":bool(affected)})
    except Exception as e:
        # Revert the assignment rather than leaving a geometrically impossible green state.
        con=db(); con.execute("UPDATE items SET crate_id=? WHERE id=?",(old_id,item_id)); con.commit(); con.close()
        for cid in dict.fromkeys([x for x in (old_id,new_id) if x]):
            try: _repack_existing_crate(cid)
            except Exception: pass
        return JSONResponse({"ok":False,"error":str(e)},status_code=400)

@app.post("/crates/{crate_id}/resize")
def resize_crate(crate_id: int, length_mm: float = Form(...), width_mm: float = Form(...), height_mm: float = Form(...)):
    _require_content_crate(crate_id)
    for value,label in ((length_mm,'长度'),(width_mm,'宽度'),(height_mm,'高度')): checked_number(value,label,strict=True)
    items,crates=fetch_state(); crate=next((c for c in crates if c['id']==crate_id),None)
    if not crate: return JSONResponse({"ok":False,"error":"木箱不存在"},status_code=404)
    units=_crate_units(crate_id,items); strategy=_strategy_load() or {}
    padding=_padding_setting(strategy); tare_per_m3=float(strategy.get('tare_per_m3',45) or 45); max_gross=float(strategy.get('max_crate_gross',5000) or 5000)
    cd=_try_repack_one_crate(units,length_mm,width_mm,height_mm,max_gross,padding,tare_per_m3) if units else None
    if units and (cd is None or cd.get('audit',{}).get('audit_status')=='不通过'):
        return JSONResponse({"ok":False,"error":"新尺寸扣除板材、框架、滑木和装箱间隙后无法容纳现有材料，尺寸未修改。"},status_code=400)
    if units:
        _write_crate_layout(crate_id,cd,items)
    else:
        con=db(); old=con.execute('SELECT length_mm,width_mm,height_mm FROM crates WHERE id=?',(crate_id,)).fetchone()
        changed=bool(old and any(abs(float(old[k] or 0)-float(v))>0.5 for k,v in (('length_mm',length_mm),('width_mm',width_mm),('height_mm',height_mm))))
        con.execute("UPDATE crates SET length_mm=?,width_mm=?,height_mm=? WHERE id=?",(length_mm,width_mm,height_mm,crate_id))
        if changed: invalidate_crate_size(con,crate_id,'空箱外形修改后待重新人工对照')
        con.commit(); con.close()
    return JSONResponse({"ok":True,"engineering_revalidated":bool(units),"size_evidence_invalidated":True})

@app.post("/crates/{crate_id}/split")
def split_crate(crate_id: int, parts: int = Form(2)):
    _require_content_crate(crate_id)
    parts=max(2,min(parts,6)); con=db(); c=con.execute("SELECT * FROM crates WHERE id=?",(crate_id,)).fetchone()
    if not c: con.close(); return JSONResponse({"ok":False,"error":"木箱不存在"},status_code=404)
    assigned=[dict(r) for r in con.execute("SELECT * FROM items WHERE crate_id=? ORDER BY id",(crate_id,)).fetchall()]; con.close()
    if not assigned: return JSONResponse({"ok":False,"error":"木箱内没有材料"},status_code=400)
    units=[]
    for i in assigned:
        for n in range(int(i.get('qty') or 0)): units.append(_packing_unit_from_item(i,n))
    groups=[[] for _ in range(parts)]; loads=[0.0]*parts
    for u in sorted(units,key=lambda x:float(x.get('weight_kg',0)),reverse=True):
        k=min(range(parts),key=lambda j:loads[j]); groups[k].append(u); loads[k]+=float(u.get('weight_kg',0))
    groups=[g for g in groups if g]
    strategy=_strategy_load() or {}; padding=_padding_setting(strategy); tare_per_m3=float(strategy.get('tare_per_m3',45) or 45); max_gross=float(strategy.get('max_crate_gross',5000) or 5000)
    tests=[]
    for g in groups:
        cd=_try_repack_one_crate(g,float(c['length_mm']),float(c['width_mm']),float(c['height_mm']),max_gross,padding,tare_per_m3)
        if cd is None or cd.get('audit',{}).get('audit_status')=='不通过':
            return JSONResponse({"ok":False,"error":"按当前箱体尺寸拆分后仍有子箱无法通过静态规划校核；未执行拆箱。"},status_code=400)
        tests.append(cd)
    # Mutate only after every child has a proven feasible layout.
    con=db(); new_ids=[]
    for idx,g in enumerate(groups):
        br=_crate_structure_tare(c['length_mm'],c['width_mm'],c['height_mm'],sum(float(u.get('weight_kg',0)) for u in g)); tare=float(br['total_kg'])
        cur=con.execute("INSERT INTO crates(name,length_mm,width_mm,height_mm,tare_weight_kg,tare_weight_source,tare_structure_class,tare_breakdown_json) VALUES(?,?,?,?,?,?,?,?)",
                        (f"{c['name']}-{chr(65+idx)}",c['length_mm'],c['width_mm'],c['height_mm'],tare,'工程结构估算',br.get('class_label',''),json.dumps(br,ensure_ascii=False)))
        new_ids.append(cur.lastrowid)
    # Split material quantities by source item according to unit assignment.
    group_counts=[]
    for g in groups:
        d={}
        for u in g: d[u['source_id']]=d.get(u['source_id'],0)+1
        group_counts.append(d)
    original={i['id']:i for i in assigned}
    for i in assigned: con.execute("DELETE FROM items WHERE id=?",(i['id'],))
    new_item_ids_by_group=[{} for _ in groups]
    for gi,countmap in enumerate(group_counts):
        for sid,qty in countmap.items():
            i=original[sid]
            cur=con.execute("""INSERT INTO items(name,qty,length_mm,width_mm,height_mm,weight_kg,weight_grade,weight_source,weight_note,size_grade,size_source,size_note,size_evidence_id,weight_evidence_id,size_subgrade,weight_subgrade,size_evidence_scope,size_evidence_scope_key,weight_evidence_scope,weight_evidence_scope_key,package_type,stackable,rotatable,cg_x_mm,cg_y_mm,cg_z_mm,cg_source,cg_confirmed,crate_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (i['name'],qty,i['length_mm'],i['width_mm'],i['height_mm'],i['weight_kg'],i.get('weight_grade','C'),i.get('weight_source','未标注'),i.get('weight_note',''),i.get('size_grade','C'),i.get('size_source','未标注'),i.get('size_note',''),i.get('size_evidence_id'),i.get('weight_evidence_id'),i.get('size_subgrade',''),i.get('weight_subgrade',''),i.get('size_evidence_scope',''),i.get('size_evidence_scope_key',''),i.get('weight_evidence_scope',''),i.get('weight_evidence_scope_key',''),i['package_type'],i['stackable'],i['rotatable'],i.get('cg_x_mm'),i.get('cg_y_mm'),i.get('cg_z_mm'),i.get('cg_source','几何中心假设'),i.get('cg_confirmed',0),new_ids[gi]))
            new_item_ids_by_group[gi][sid]=cur.lastrowid
    con.execute("DELETE FROM crate_placements WHERE crate_id=?",(crate_id,)); con.execute("DELETE FROM crate_audits WHERE crate_id=?",(crate_id,)); con.execute("DELETE FROM crates WHERE id=?",(crate_id,)); con.commit(); con.close()
    for nid in new_ids: _repack_existing_crate(nid)
    return JSONResponse({"ok":True,"new_crates":new_ids,"engineering_revalidated":True})

@app.delete("/items/{item_id}")
def del_item(item_id:int):
    con=db(); row=con.execute("SELECT crate_id FROM items WHERE id=?",(item_id,)).fetchone(); cid=row['crate_id'] if row else None
    placeholder=con.execute('SELECT is_package_placeholder FROM items WHERE id=?',(item_id,)).fetchone()
    if placeholder and placeholder['is_package_placeholder']:
        con.close();return JSONResponse({'ok':False,'error':'这是已包装单元的占位记录，请从木箱卡片整包删除'},status_code=409)
    con.execute("DELETE FROM items WHERE id=?",(item_id,)); con.commit(); con.close()
    if cid:
        try:
            _repack_existing_crate(cid)
            con=db(); invalidate_crate_gross(con,cid,'删除箱内材料后待重新实称'); con.commit(); con.close()
        except Exception as e: return JSONResponse({"ok":False,"error":str(e)},status_code=400)
    return JSONResponse({"ok":True,"engineering_revalidated":bool(cid),"gross_evidence_invalidated":bool(cid)})

@app.delete("/crates/{crate_id}")
def del_crate(crate_id:int):
    con=db(); con.execute("UPDATE items SET crate_id=NULL WHERE crate_id=?", (crate_id,)); con.execute("DELETE FROM crate_placements WHERE crate_id=?", (crate_id,)); con.execute("DELETE FROM crates WHERE id=?", (crate_id,)); con.commit(); con.close(); return JSONResponse({"ok":True})

# V5.1: credibility confirmation routes are isolated from the solver/packing routes.
configure_credibility_routes(
    db=db,
    release_meta_load=_release_meta_load,
    release_meta_save=_release_meta_save,
    confirmation_user=_confirmation_user,
    evidence_store_getter=lambda: EVIDENCE_STORE,
    record_event=_record_credibility_event,
)
app.include_router(credibility_router)

ASU=AsuService(sys.modules[__name__])
ASU.install_routes()

FLEET=SelectedFleet(sys.modules[__name__])
FLEET.install_routes()

TRUCKS=Trucks(sys.modules[__name__])
TRUCKS.install_routes()
