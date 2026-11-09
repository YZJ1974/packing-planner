"""Project-specific deterministic rules; external engineering approval is never inferred."""
from __future__ import annotations
import copy,datetime,hashlib,json,math,re
from pathlib import Path
from app.input_validation import number

STANDARD='ASU K-BX 1002 / 1410AAAE / Issue 01'
SOURCE_SHA256='99194810ba9f7e80117633faf4079cef779733c2ee21ec3642a20155698fe6a7'
DEFAULT={'enabled':False,'standard':STANDARD,'source_sha256':SOURCE_SHA256,'transport_modes':['road','sea'],
         'panel_material':'plywood','delivery_date':'','project_reference':'','bottom_basis':'unconfirmed'}
# forward, backward, transverse, vertical. Footnotes are part of the rule, not discarded.
ACCEL={'road':(.8,.5,.5,1),'rail':(1,1,.5,1),'rail_shunting':(4,4,.5,1),
       'sea':(.4,.4,.8,1),'air':(1.5,1.5,1.5,3),'spmt':(.2,.2,.2,.2),'barge':(.3,.3,.6,.2)}
FOOTNOTE='海运/驳船横向系数含重力等分量，竖向系数不含静重。基本适用条件：无限航区、全年航行、航程25天、船长100m、航速15kn、β/GM>13；不适用时应个别计算并由项目批准。仅PPU驱动模块车适用SPMT，牵引模块车应按公路；铁路调车纵向按4.0g单独工况。表中力为惯性需求，不是索力或结构验算结果。'
PACKING={1:['1','2','2A','2B','3'],2:['2','2A','2B','3','3B'],3:['3','3B','3E','4','6','6A'],
         4:['3','3B','5','6'],5:['3D','5','6'],6:['3A','3C','5A','6A'],7:['6B'],8:['7']}
PURPOSES={'equipment':'设备','loose':'设备散件','commissioning':'调试备件','capital':'关键备件','two_year_spare':'两年备件','tools':'临时工具/复出口','restricted':'两用/出口受限','dangerous':'危险品'}
_CACHE={}
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def profile(path):
    path=Path(path)
    if not path.exists():return copy.deepcopy(DEFAULT)
    stat=path.stat();key=(str(path),stat.st_mtime_ns,stat.st_size)
    if key not in _CACHE:
        try: p=validate_profile(json.loads(path.read_text()))
        except Exception as exc:raise ValueError('ASU项目配置无效，请打开 /asu-project 修正：'+str(exc))
        _CACHE.clear();_CACHE[key]=p
    return copy.deepcopy(_CACHE[key])
def validate_profile(value):
    p=copy.deepcopy(DEFAULT);p.update({k:v for k,v in value.items() if k in DEFAULT})
    if not isinstance(p['enabled'],bool):raise ValueError('模式值必须为布尔值')
    if p['standard']!=STANDARD or p['source_sha256']!=SOURCE_SHA256:raise ValueError('标准标识或文件哈希不符')
    if not isinstance(p['transport_modes'],list) or not p['transport_modes'] or any(x not in ACCEL for x in p['transport_modes']):raise ValueError('请选择有效运输工况')
    p['transport_modes']=sorted(set(p['transport_modes']))
    if p['panel_material'] not in ('plywood','solid_wood'):raise ValueError('请选择胶合板或实木')
    if p['bottom_basis'] not in ('unconfirmed','equipment_underface_includes_supports'):raise ValueError('底部净空基准无效')
    if p['delivery_date']:datetime.date.fromisoformat(p['delivery_date'])
    return p
def panel_min(p):
    if p['panel_material']=='solid_wood':return 24.
    # Air exception explicitly includes its road leg. Road-only retains seaworthy
    # default requirements; it must not silently inherit the thinner air exception.
    air_only='air' in p['transport_modes'] and not any(m in p['transport_modes'] for m in ('sea','barge','rail','rail_shunting'))
    return 9.5 if air_only else 12.5
def margin(p,c):
    if c.get('vehicle_type')=='truck':return float(c.get('clearance_mm') or 0)
    return 100. if p['enabled'] and not str(c.get('code') or c.get('container_code') or '').startswith('WOODBOX') else 0.
def clearance_issues(p,box):
    c=box.get('container',box)
    if not p['enabled'] and c.get('vehicle_type')!='truck':return []
    m=margin(p,c)
    if not m:return []
    L=float(c.get('inner_l',0));W=float(c.get('inner_w',0));out=[]
    for q in box.get('placements',[]):
        if float(q['x'])<m-1e-6 or float(q['y'])<m-1e-6 or float(q['x'])+float(q['l'])>L-m+1e-6 or float(q['y'])+float(q['w'])>W-m+1e-6:
            out.append(str(q.get('name') or '货物')+(f'：货台操作间隙不足{m:g}mm' if c.get('vehicle_type')=='truck' else '：ASU前后左右柜壁间隙不足100mm（3.4，第10页）'))
    return out
def runners(width_mm):
    for width,n in ((1000,2),(1800,3),(2750,4),(3250,5)):
        if width_mm<=width+1e-6:return n
    return None
def pipe_requirements(mass_kg,kind):
    mass=number(mass_kg,'管道重量',strict=True)/1000
    if kind not in ('bundle','single','coated'):raise ValueError('管道装载形式无效')
    if kind=='coated':return {'beddings':None,'belts':None,'MSL_kN':None,'note':'涂层管托架按批准结构及系固方案，不套用裸管堆公式'}
    return {'beddings':math.ceil(mass/2),'bedding_width_mm':100,'bedding_height_mm':100,
            'belts':math.ceil(mass/(1.5 if kind=='single' else 1)),
            'belts_per_side':math.ceil(math.ceil(mass)/2) if kind=='bundle' else None,'MSL_kN':25,
            'side_trestles':'待项目澄清（第52页中英文及表2冲突）' if kind=='bundle' else '按批准结构图',
            'lashing_type':'loop' if kind in ('single','coated') else 'approved_plan'}
def forces(mass_kg,p):
    mass=number(mass_kg,'毛重')
    return [{'mode':mode,'g':list(ACCEL[mode]),'inertia_kN':[round(mass*9.80665*x/1000,4) for x in ACCEL[mode]]} for mode in p['transport_modes']]
def longitudinal_demand(pls):
    # A screening indicator only: actual skid reactions and floor beams need a drawing.
    floor=[q for q in pls if float(q.get('z',0))<=1e-6 and float(q.get('l',0))>0]
    edges=sorted(set(v for q in floor for v in (float(q['x']),float(q['x'])+float(q['l']))))
    return max([sum(float(q.get('weight_kg',0))/float(q['l'])*1000 for q in floor if float(q['x'])<b-1e-6 and float(q['x'])+float(q['l'])>a+1e-6) for a,b in zip(edges,edges[1:])] or [0])
def english(value):return bool(re.fullmatch(r"[A-Za-z0-9 .,'()/+&_\-]+",str(value or ''))) and len(re.findall(r'[A-Za-z]{2,}',str(value)))>=2
def points(value,dimensions):
    if value in (None,''):return []
    data=json.loads(value) if isinstance(value,str) else value
    if not isinstance(data,list) or len(data)>24:raise ValueError('吊点必须是最多24个坐标对象的JSON数组')
    out=[]
    for q in data:
        r={k:number(q.get(k),k) for k in ('x','y','z','capacity_kg')}
        if r['capacity_kg']<=0 or any(r[k]>float(dim)+1e-6 for k,dim in zip(('x','y','z'),dimensions)):raise ValueError('吊点坐标须在含吊耳的外包络内，额定能力须大于0')
        r['label']=str(q.get('label') or len(out)+1)[:40];out.append(r)
    return out

# Explicit structured declarations, always paired with a current file and approval reference.
TEXT=('english_description','purpose','goods_category','packing_category','pipe_kind','lashing_type','panel_material',
      'po','batch','origin','storage_category','approval_ref','packing_date','container_no','vgm_method','notes','lifting_points_json')
NUMBERS=('net_kg','panel_mm','ply_layers','runner_count','runner_height_mm','bottom_clearance_mm','top_capacity_kg','pipe_id_mm',
         'bedding_count','bedding_width_mm','bedding_height_mm','belt_count','belts_per_side','belt_msl_kN','storage_months','trestle_count','trestle_required',
         'container_tare_kg','dunnage_kg','securing_kg','vgm_kg','line_limit_kg_m','wall_side_kN','wall_end_kN','hook_height_mm','sling_angle_deg')
FLAGS=('source_confirmed','conflicts_resolved','rfs_review','template_approved','platform_receipt','measurements_actual',
       'structural_review','dynamic_review','lashing_review','lifting_review','preservation_review','wood_treatment',
       'marking_review','not_stackable_approved','line_load_review','wall_load_review','vgm_certificate','coated','prefabricated')
def parse_record(form,dimensions=None):
    d={k:str(form.get(k) or '').strip()[:10000] for k in TEXT}
    for k in NUMBERS:
        v=form.get(k);d[k]=None if v in (None,'') else number(v,k,integer=k in ('ply_layers','runner_count','bedding_count','belt_count','belts_per_side','storage_months','trestle_count','trestle_required'))
    for k in FLAGS:d[k]=str(form.get(k) or '').lower() in ('1','true','on','yes')
    for k in ('packing_date',):
        if d[k]:datetime.date.fromisoformat(d[k])
    if dimensions and d['lifting_points_json']:points(d['lifting_points_json'],dimensions)
    if d.get('sling_angle_deg') is not None and not 0<d['sling_angle_deg']<90:raise ValueError('吊索与水平面角度必须在0至90度之间')
    if d['purpose'] and d['purpose'] not in PURPOSES:raise ValueError('用途分类无效')
    if d['pipe_kind'] and d['pipe_kind'] not in ('none','small','bundle','single','coated'):raise ValueError('管道分类无效')
    if d['panel_material'] and d['panel_material'] not in ('plywood','solid_wood'):raise ValueError('禁用OSB、旧料及未识别板材')
    if d['storage_category'] and d['storage_category'] not in ('1','2','3','4','5'):raise ValueError('储存类别无效')
    if d['goods_category'] and d['goods_category'] not in [str(i) for i in range(1,11)]:raise ValueError('货物类别无效')
    return d
def package_checks(pkg,d,verified,p):
    if not p['enabled']:return []
    out=[];name=str(pkg.get('name') or '')
    def add(text,page,status='待复核'):out.append({'status':status,'entity':name,'page':page,'message':text})
    def require(flag,text,page):
        if not (verified and d.get(flag)):add(text,page)
    if not verified:add('资料未登记、未绑定当前数据或已失效；修改尺寸/重量/项目工况后须重新核对',45)
    if not english(d.get('english_description')):add('填写清晰英文品名，至少两个英文单词，禁止纯代码或中文替代',45)
    for key,label in (('purpose','用途分类'),('po','采购订单号'),('batch','发运批次'),('origin','原产国'),('storage_category','储存类别')):
        if not d.get(key):add('缺少'+label,45 if key in ('po','batch') else 36)
    if d.get('origin') and not re.fullmatch('[A-Za-z .-]+',d['origin']):add('原产国应以英文填入对外交付资料',35)
    try:goods=int(d.get('goods_category') or 0)
    except ValueError:goods=0
    cat=d.get('packing_category') or ''
    if goods not in range(1,11) or not cat:add('确认货物类别和包装类别',15)
    elif goods in PACKING and cat not in PACKING[goods]:add('所选包装类别不在货物类别允许矩阵中',15,'不满足')
    if goods==9 and d.get('purpose') not in ('commissioning','capital','two_year_spare'):add('九类货物必须明确备件类型并分开包装',28,'不满足')
    net=d.get('net_kg');mass=float(pkg['weight_kg']);L,W,H=[float(pkg[k]) for k in ('length_mm','width_mm','height_mm')]
    if net is None:add('缺少实际称重净重，禁止由毛重反推',45)
    elif net>mass+1e-6:add('净重不得大于当前运输单元毛重',45,'不满足')
    require('measurements_actual','净重/毛重须实称、外形须实测，并与当前资料对照',45)
    require('dynamic_review','缺运输工况受力及抗滑/倾覆/固定方案专项复核',12)
    require('lashing_review','缺系固方式、位置、能力和传力路径的批准资料',13)
    require('marking_review','缺英文唛头、标识面、储存及特殊标识核对',34)
    require('preservation_review','缺防护/储存方案及适用的干燥剂计算批准；RH应≤40%，原文歧义须澄清',30)
    months=24 if d.get('purpose') in ('commissioning','capital','two_year_spare') else 12
    if (d.get('storage_months') or 0)<months:add(f'防护保存期限应至少{months}个月',5,'不满足' if d.get('storage_months') is not None else '待复核')
    wood=cat in ('2','3','3A','4','5','5A','6','6A','6B') or pkg.get('type')=='木箱'
    if wood:
        require('wood_treatment','缺木材处理/IPPC标识、垫木挡块及处理证明核对',31)
        require('structural_review','缺底座、框架、顶板和连接结构能力批准资料；估算箱皮不等于强度合格',19)
        mat=d.get('panel_material')
        if mat not in ('plywood','solid_wood'):add('确认板材为合格实木或防水胶合板，禁OSB及旧料',31)
        minimum=24 if mat=='solid_wood' else panel_min(dict(p,panel_material='plywood'))
        if d.get('panel_mm') is None:add('缺实际板厚',31)
        elif d['panel_mm']<minimum:add(f'实际板厚{d["panel_mm"]:g}mm低于{minimum:g}mm',31,'不满足')
        if mat=='plywood' and (d.get('ply_layers') or 0)<5:add('胶合板应至少五层且防水',31,'不满足' if d.get('ply_layers') is not None else '待复核')
        minimum_n=runners(W)
        if minimum_n is None:add('底宽超过图4范围，须专项底座设计',19)
        elif d.get('runner_count') is None:add(f'缺滑木数量；按当前底宽至少{minimum_n}根，方向须按批准图确认',19)
        elif d['runner_count']<minimum_n:add(f'滑木数量不足，当前底宽至少{minimum_n}根',19,'不满足')
        if (d.get('bottom_clearance_mm') or 0)<100:add('设备底面净离地应至少100mm，含已有托架不得重复加高',32,'不满足' if d.get('bottom_clearance_mm') is not None else '待复核')
        if d.get('not_stackable_approved'):
            require('not_stackable_approved','不堆叠豁免须书面批准并在四面及顶面标识',13)
            if pkg.get('stackable'):add('已有不堆叠豁免，但系统仍允许该运输单元作为叠放支撑',34,'不满足')
        elif (d.get('top_capacity_kg') or 0)<1000*L*W/1e6:
            add(f'缺足够的均布顶载能力：当前顶面积要求至少{1000*L*W/1e6:g}kg；或登记书面不堆叠豁免',13,'不满足' if d.get('top_capacity_kg') is not None else '待复核')
    if mass>=500 or H>=1000:
        if not pkg.get('cg_confirmed'):add('重量≥500kg或高度≥1m，需要实际重心和“+ / CENTER OF GRAVITY”标识',34)
    if mass>=500 or max(L,W,H)>3000:
        require('lifting_review','缺真实吊点、允许组合、吊具及运输图专项核对',40)
        actual_points=points(d.get('lifting_points_json'),(L,W,H))
        if len(actual_points)<2:add('至少登记两个真实吊点坐标及额定能力，箱顶假定点不可替代',40)
        elif sum(q['capacity_kg'] for q in actual_points)<mass-1e-6:add('吊点额定能力之和不足自重；满足此下限仍不代表各点反力、索力或强度合格',40,'不满足')
        if d.get('hook_height_mm') is not None and actual_points and d['hook_height_mm']<=max(q['z'] for q in actual_points):add('吊钩高度须高于所用吊点',40,'不满足')
        if not d.get('sling_angle_deg') and not d.get('hook_height_mm'):add('运输图须登记吊索角度（相对水平）或吊钩高度',40)
    if L>15000 or W>3000 or H>3000 or mass>25000:add('触发项目超限定义，需最终运输图、运输许可和物流审批；参照LS180-01',39)
    if mass>=50000:add('达到50t证书边界，原文件中英文不一致，需重量证明及项目解释',46)
    kind=d.get('pipe_kind') or 'none'
    if goods==10 and kind=='none':add('十类管道必须确认内径、涂层、预制状态和装载形式',49)
    if kind!='none':
        dia=d.get('pipe_id_mm')
        if dia is None:add('管道必须填写真实内径，不能仅用DN判断',49)
        elif dia<=76.2+1e-6 and not d.get('prefabricated') and cat not in ('6','6A'):add('内径≤3英寸的直管须6类箱装',49,'不满足')
        if d.get('coated') and kind!='coated':add('涂层管道应采用底托包装及相应防护',49,'不满足')
        if kind=='bundle' and mass>5000 and not (verified and d.get('structural_review')):add('普通型钢/结构件5t上限的具体适用性须在管堆结构方案中确认',28)
        if kind in ('bundle','single'):
            req=pipe_requirements(mass,kind)
            for key,n in (('bedding_count',req['beddings']),('bedding_width_mm',100),('bedding_height_mm',100),('belt_count',req['belts']),('belt_msl_kN',25)):
                if d.get(key) is None:add(f'缺{key}，最低要求{n}',52 if kind=='bundle' else 54)
                elif d[key]<n:add(f'{key}不足，最低要求{n}',52 if kind=='bundle' else 54,'不满足')
            if kind=='bundle':
                if (d.get('belts_per_side') or 0)<req['belts_per_side']:add(f'横向每侧绑带至少{req["belts_per_side"]}；同时满足绑带总数',52)
                if not d.get('trestle_required') or d.get('trestle_count') is None:add('缺项目书面澄清的最低侧挡架数与实际侧挡架数；不得自动采用冲突条款',52)
                elif d['trestle_count']<d['trestle_required']:add('实际侧挡架数量低于项目书面澄清值',52,'不满足')
                if not (verified and d.get('lashing_review')):add('侧挡架数量按第52页中英文与表2的书面澄清执行；不得自动选用冲突数值',52)
            if kind=='single' and d.get('lashing_type')!='loop':add('应采用环形绑扎，不能用tie-down替代',55,'不满足')
    if d.get('purpose') in ('dangerous','restricted'):add('危险品/受限货物须专项文件、隔离与主管项目审批，不能由通用配载放行',26)
    return out
def checklist(delivery):
    rules=[('最终运输/吊装图（适用时）',16),('箱单/运输储存指导',4),('木材处理/重量及其他适用证书',3),('危险品/安全运输资料（适用时）',8)]
    day=datetime.date.fromisoformat(delivery) if delivery else None
    return [{'document':name,'weeks':weeks,'due':str(day-datetime.timedelta(weeks=weeks)) if day else '待填写交货日期'} for name,weeks in rules]
