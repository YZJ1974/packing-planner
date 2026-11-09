"""Server-built SVG from authoritative placements; never accepts client markup."""
import math
import xml.etree.ElementTree as ET
from app.cargo_shapes import cylinder_faces
from app.bag_display import svg_sack
from app.input_validation import number

FONT='PingFang SC,Microsoft YaHei,Noto Sans CJK SC,sans-serif'
def render_svg(box,view='perspective',lifting=False,options=None,label='DRAFT / 非签证依据',stamp=''):
    options=options or {};L=float(box['inner_l']);W=float(box['inner_w']);H=float(box['inner_h'])
    truck=box.get('vehicle_type')=='truck';flatbed=truck and box.get('body_type')=='flatbed'
    angle=number(options.get('angle',60),'吊索角度');inset=number(options.get('setback',10),'吊点内缩');threshold=number(options.get('minWeight',500),'重量门槛')
    if not 20<=angle<=80 or not 5<=inset<=40:raise ValueError('吊索角度应为20～80°，吊点内缩应为5～40%')
    overlays=bool(options.get('enabled',False)) or lifting
    rigs=[]
    for q in box.get('placements',[]):
        if not overlays or q['weight_kg']<threshold:continue
        x,y,z,l,w,h=[float(q[k]) for k in ('x','y','z','l','w','h')]
        cx=x+float(q.get('cg_local_x') if q.get('cg_local_x') is not None else l/2)
        cy=y+float(q.get('cg_local_y') if q.get('cg_local_y') is not None else w/2)
        a=inset/100;points=[(x+l*a,y+w*a,z+h),(x+l*(1-a),y+w*a,z+h),(x+l*(1-a),y+w*(1-a),z+h),(x+l*a,y+w*(1-a),z+h)]
        hook=(cx,cy,z+h+max(math.hypot(p[0]-cx,p[1]-cy) for p in points)*math.tan(math.radians(angle)))
        rigs.append((q,points,hook))
    if lifting:view='front'
    presets={'perspective':(-29,-16),'top':(0,-90),'front':(0,0),'side':(-90,0)}
    if view not in presets:raise ValueError('视图无效')
    yaw,pitch=map(math.radians,presets[view]);maxz=max([H]+[r[2][2]*1.08 for r in rigs])
    if options.get('report_fit_all_cargo'):maxz=max([maxz]+[float(q['z'])+float(q['h']) for q in box.get('placements',[])])
    def raw(x,y,z):
        dx=x-L/2;dy=y-W/2;dz=z-H/2
        x1=dx*math.cos(yaw)-dy*math.sin(yaw);y1=dx*math.sin(yaw)+dy*math.cos(yaw)
        return x1,-(y1*math.sin(pitch)+dz*math.cos(pitch)),y1*math.cos(pitch)-dz*math.sin(pitch)
    corners=[raw(x,y,z) for x in (0,L) for y in (0,W) for z in (0,maxz)]
    minx,maxx=min(p[0] for p in corners),max(p[0] for p in corners);miny,maxy=min(p[1] for p in corners),max(p[1] for p in corners)
    scale=min(1000/max(maxx-minx,1),430/max(maxy-miny,1))
    def P(x,y,z):
        u,v,d=raw(x,y,z);return (540+(u-(minx+maxx)/2)*scale,335+(v-(miny+maxy)/2)*scale)
    height=720+len(rigs)*24 if lifting else 690
    svg=ET.Element('svg',{'xmlns':'http://www.w3.org/2000/svg','viewBox':f'0 0 1080 {height}','font-family':FONT})
    def node(tag,**attrs):return ET.SubElement(svg,tag,{k.replace('_','-'):str(v) for k,v in attrs.items()})
    def text(x,y,value,size=13,fill='#263f4c'):
        e=node('text',x=x,y=y,font_size=size,fill=fill);e.text=str(value);return e
    node('rect',width=1080,height=height,fill='white')
    names={'perspective':'三维','top':'俯视','front':'侧视','side':'端视'}
    text(26,30,f"第{box['index']}{'辆货车' if truck else '柜'} · {box['container_code']} · {'吊点规划示意' if lifting else names[view]+('装车图' if truck else '装柜图')}",20)
    text(26,55,label,14,'#b45309');text(26,77,'静态校核：'+str((box.get('engineering_audit') or {}).get('status','未知'))+' · '+stamp,11)
    def line(a,b,color='#64748b',dash=None):
        attrs=dict(x1=a[0],y1=a[1],x2=b[0],y2=b[1],stroke=color,stroke_width=1.4)
        if dash:attrs['stroke_dasharray']=dash
        node('line',**attrs)
    ps=sorted(box.get('placements',[]),key=lambda q:raw(q['x']+q['l']/2,q['y']+q['w']/2,q['z']+q['h']/2)[2])
    for q in ps:
        x,y,z,l,w,h=[float(q[k]) for k in ('x','y','z','l','w','h')]
        c=[(x,y,z),(x+l,y,z),(x+l,y+w,z),(x,y+w,z),(x,y,z+h),(x+l,y,z+h),(x+l,y+w,z+h),(x,y+w,z+h)]
        faces=[[0,3,7,4],[3,2,6,7],[4,5,6,7]] if view=='perspective' else [[4,5,6,7]] if view=='top' else [[0,1,5,4]] if view=='front' else [[0,3,7,4]]
        mesh=cylinder_faces(q)
        if mesh:
            mesh.sort(key=lambda f:sum(raw(*p)[2] for p in f)/len(f))
            group=node('g',data_shape='upright-cylinder' if q.get('cylinder_axis')=='z' or q.get('display_shape')=='upright_cylinder' else 'horizontal-cylinder')
            for face in mesh:
                ET.SubElement(group,'polygon',{'points':' '.join(f'{P(*p)[0]:.2f},{P(*p)[1]:.2f}' for p in face),'fill':q.get('color') or '#60a5fa','stroke':q.get('color') or '#475569','stroke-width':'.4'})
        else:
            for ids in faces:node('polygon',points=' '.join(f'{P(*c[i])[0]:.2f},{P(*c[i])[1]:.2f}' for i in ids),fill=q.get('color') or '#60a5fa',fill_opacity='.78',stroke='#475569',stroke_width=1,data_cargo_seq=q['seq'])
        center=P(x+l/2,y+w/2,z+h/2);text(center[0]-10,center[1],f"#{q['seq']}",12)
    # Every assigned zone is rendered, regardless of motif budget.
    for q in box.get('flexible_zones',[]):
        x,y,z,l,w,h=[float(q[k]) for k in ('x','y','z','l','w','h')]
        c=[(x,y,z),(x+l,y,z),(x+l,y+w,z),(x,y+w,z),(x,y,z+h),(x+l,y,z+h),(x+l,y+w,z+h),(x,y+w,z+h)]
        faces=[[0,3,7,4],[3,2,6,7],[4,5,6,7]] if view=='perspective' else [[4,5,6,7]] if view=='top' else [[0,1,5,4]] if view=='front' else [[0,3,7,4]]
        for ids in faces:node('polygon',points=' '.join(f'{P(*c[i])[0]:.2f},{P(*c[i])[1]:.2f}' for i in ids),fill='#e9d8ad',fill_opacity='.30',stroke='#ae9a77',stroke_width='.6',data_flexible_zone='volume-only')
    # Bag patches are display-only surface motifs; volume/quantity calculations are unchanged.
    patches=sorted(box.get('bag_display_patches',[]),key=lambda p:sum(raw(*v)[2] for v in p['corners']))
    group=node('g',data_bag_stack='illustrative-not-individual-placement')
    for patch in patches:
        corners=[P(*v) for v in patch['corners']]
        area=abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(corners,corners[1:]+corners[:1])))/2
        if area>1:svg_sack(group,corners,lambda parent,tag,attrs:ET.SubElement(parent,tag,attrs),'#eee4ce' if patch['tone'] else '#fff8e9')
    for zone in box.get('flexible_zones',[]):
        if zone.get('quantity'):
            center=P(zone['x']+zone['l']/2,zone['y']+zone['w']/2,zone['z']+zone['h'])
            text(center[0],center[1]-4,f"袋区{zone['loading_order']} · {zone['quantity']}包",10,'#713f12')
    if box.get('flexible_fill'):
        text(26,100,'柔性袋装体积分配：'+str(sum(a['quantity'] for a in box['flexible_fill']))+'包；编织袋堆垛示意，图中袋形数非实包数；非逐包定位/工程放行',12,'#854d0e')
    if box.get('bag_access_audit') and not box.get('flexible_fill'):
        text(26,100,'珠光砂：本柜未找到满足作业路径/支撑条件的分阶段区域，分配0包；未知顶载不计入。',12,'#854d0e')
    c=[P(0,0,0),P(L,0,0),P(L,W,0),P(0,W,0),P(0,0,H),P(L,0,H),P(L,W,H),P(0,W,H)]
    for i,(a,b) in enumerate(((0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7))):line(c[a],c[b],color='#176a7a' if flatbed and i<4 else '#64748b',dash=None if flatbed and i<4 else '7 4')
    for q,points,hook in rigs:
        for i,p in enumerate(points,1):
            a=P(*p);line(a,P(*hook),'#db7c19');node('circle',cx=a[0],cy=a[1],r=3,fill='white',stroke='#92400e')
        a=P(*hook);node('circle',cx=a[0],cy=a[1],r=4,fill='#dc2626');text(a[0]+5,a[1]-7,f"吊钩{q['seq']}",11)
        cg=P(q['x']+float(q.get('cg_local_x') if q.get('cg_local_x') is not None else q['l']/2),q['y']+float(q.get('cg_local_y') if q.get('cg_local_y') is not None else q['w']/2),q['z']+float(q.get('cg_local_z') if q.get('cg_local_z') is not None else q['h']/2))
        node('circle',cx=cg[0],cy=cg[1],r=4,fill='#facc15',stroke='#92400e')
    text(26,578,f"{'货台可用尺寸' if truck else '柜内尺寸'} {L:g} × {W:g} × {H:g} mm；X为长度、Y为宽度、Z为{'货台以上高度' if truck else '高度'}",12)
    text(26,601,'货车虚线为允许装载包络，非车体；Z=0为货台面，索力/轮轴实际分配须专项复核。' if truck else '图形由服务器当前权威坐标生成；货物编号与装载清单对应。',11)
    if overlays:text(26,624,f'吊点未确认 · 非吊装施工依据 · 最小水平夹角{angle:g}°，内缩{inset:g}%，显示≥{threshold:g}kg',12,'#b45309')
    total=sum(float(q.get('weight_kg') or 0) for q in ps)
    if total>0:
        cg=[]
        for axis,dim in (('x','l'),('y','w'),('z','h')):
            cg.append(sum(float(q.get('weight_kg') or 0)*(q[axis]+float(q.get('cg_local_'+axis) if q.get('cg_local_'+axis) is not None else q[dim]/2)) for q in ps)/total)
        point=P(*cg);star=' '.join(f"{point[0]+(9 if k%2==0 else 4)*math.cos(-math.pi/2+k*math.pi/5):.2f},{point[1]+(9 if k%2==0 else 4)*math.sin(-math.pi/2+k*math.pi/5):.2f}" for k in range(10))
        node('polygon',points=star,fill='#ef4444',stroke='#7f1d1d')
        text(26,647,f'{'设备/木箱重心（未含柔性袋装）' if box.get('flexible_fill') else '货物合成重心'}：X={cg[0]:.0f}，Y={cg[1]:.0f}，Z={cg[2]:.0f} mm（不含{"车辆" if truck else "柜体"}；未知单件CG按形心）',11)
    if lifting:
        for i,(q,points,hook) in enumerate(rigs):
            delta=H-hook[2];text(26,675+i*24,f"#{q['seq']} {q['name']} · {q['weight_kg']:g} kg · 吊钩Z={hook[2]:.0f}mm · 净高{'余量' if delta>=0 else '超出'} {abs(delta):.0f}mm · 索力/吊点强度未验算",11)
    elif truck:
        ax=(box.get('engineering_audit') or {}).get('truck_axle_loads')
        if ax:text(26,670,f"二有效支承组静载：A={ax['loaded_a_kg']:.1f}kg，B={ax['loaded_b_kg']:.1f}kg；不是各实际车轴或道路放行验算。",11)
    return ET.tostring(svg,encoding='utf-8',xml_declaration=True)
