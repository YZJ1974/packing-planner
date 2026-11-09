"""Editable all-container Word atlas built only from server-authoritative plans."""
from pathlib import Path
from io import BytesIO
from collections import OrderedDict
import argparse,copy,hashlib,json,re,subprocess,xml.etree.ElementTree as ET
from docx import Document
from docx.shared import Mm,Pt,RGBColor
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT,WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from app.svg_exports import render_svg

ET.register_namespace('', 'http://www.w3.org/2000/svg')

VIEWS=('perspective','top','front','side')
VIEW_NAMES={'perspective':'三维排布','top':'俯视图','front':'侧视图','side':'端视图'}
BLACK='000000';INK='172E40';NAVY='183F57';LIGHT='F1F6FA'


def overheight_warning(box):
    excess=max([0.]+[float(q['z'])+float(q['h'])-float(box['inner_h']) for q in box.get('placements',[])])
    if excess<=1e-6:return ''
    return f'顶部超高 {excess:g} mm  超高运输候选  顶部开口、吊装进柜及超高运输条件尚待核实，未确认可装运。'


def report_svg(box,view):
    # Never forward caller lifting options. All markers and model notes are
    # removed from the plate; the report owns captions, metrics and scope text.
    root=ET.fromstring(render_svg(box,view,lifting=False,options={'enabled':False,'report_fit_all_cargo':True},label='',stamp=''))
    labels=[]
    for node in list(root):
        tag=node.tag.rsplit('}',1)[-1];text=node.text or ''
        if tag=='rect' and node.get('width')=='1080':root.remove(node)
        elif tag=='text':
            if text.startswith('#'):labels.append(node);root.remove(node)
            elif float(node.get('y',0))<115 or float(node.get('y',0))>560 or text.startswith('袋区'):root.remove(node)
        elif tag=='polygon' and node.get('fill')=='#ef4444':root.remove(node)
    root.set('font-family','Arial,Microsoft YaHei,PingFang SC,sans-serif')
    used=[]
    for node in reversed(labels):
        x,y=float(node.get('x')),float(node.get('y'));width=10*len(node.text or '')
        for dx,dy in [(0,0),(0,-18),(0,18),(16,0),(-16,0),(0,-36),(0,36)]:
            rect=(x+dx-3,y+dy-17,x+dx+width,y+dy+4)
            if rect[1]<110 or rect[3]>565:continue
            if any(min(rect[2],r[2])>max(rect[0],r[0]) and min(rect[3],r[3])>max(rect[1],r[1]) for r in used):continue
            node.set('x',str(x+dx));node.set('y',str(y+dy));node.set('font-size','17');node.set('font-weight','700');node.set('fill','#172e40');node.set('stroke','#ffffff');node.set('stroke-width','3');node.set('paint-order','stroke');root.append(node);used.append(rect);break
    raw=ET.tostring(root,encoding='utf-8',xml_declaration=True)
    if any(t in raw.decode() for t in ('吊钩','吊点','吊索','#db7c19')):raise ValueError('装箱排布图片包含非预期叠加图层')
    return raw


def ranges(values):
    values=sorted(set(values));out=[]
    for n in values:
        if not out or n>out[-1][-1]+1:out.append([n])
        else:out[-1].append(n)
    return '、'.join(str(r[0]) if len(r)==1 else f'{r[0]}–{r[-1]}' for r in out)


def cargo_rows(box):
    groups=OrderedDict()
    for q in sorted(box.get('placements',[]),key=lambda q:q['seq']):
        # Only identical units from the same explicit source row are grouped.
        source=q.get('asu_key') or ('single',q['seq'])
        key=(source,re.sub(r'-\d+$','',q['name']),q.get('type'),q['l'],q['w'],q['h'],q['weight_kg'])
        groups.setdefault(key,[]).append(q)
    rows=[]
    for group in groups.values():
        q=group[0];names=[r['name'] for r in group];name=q['name']
        if len(group)>1:
            bases=[re.sub(r'-\d+$','',n) for n in names]
            if len(set(bases))==1:name=bases[0]
        if q.get('package_no'):
            name=str(q.get('cargo_name') or '箱内名称待补充')+'\n箱号：'+str(q['package_no'])
        n=len(group)
        rows.append([ranges([r['seq'] for r in group]),name,str(q.get('type') or ''),str(n),' × '.join(f'{float(q[d]):g}' for d in ('l','w','h')),f"{float(q['weight_kg']):,.1f}",f"{sum(float(r['weight_kg']) for r in group):,.1f}"])
    for a in box.get('flexible_fill',[]):
        n=int(a['quantity']);rows.append(['袋装','珠光砂','编织袋',str(n),f"{float(a['volume_m3']):,.3f} m³",f"{float(a['weight_kg'])/n:,.1f}" if n else '0.0',f"{float(a['weight_kg']):,.1f}"])
    return rows


def font(run,size=10.5,bold=False,color=BLACK):
    run.font.name='Arial';run.font.size=Pt(size);run.font.bold=bold;run.font.color.rgb=RGBColor.from_string(color)
    rpr=run._element.get_or_add_rPr();rf=rpr.rFonts
    if rf is None:rf=OxmlElement('w:rFonts');rpr.insert(0,rf)
    rf.set(qn('w:eastAsia'),'Microsoft YaHei');rf.set(qn('w:ascii'),'Arial');rf.set(qn('w:hAnsi'),'Arial')
    lang=OxmlElement('w:lang');lang.set(qn('w:eastAsia'),'zh-CN');rpr.append(lang)


def text(parent,value,size=10.5,bold=False,style=None,after=3,before=0,align=None):
    p=parent.add_paragraph(style=style);p.paragraph_format.space_after=Pt(after);p.paragraph_format.space_before=Pt(before);p.paragraph_format.line_spacing=1.08
    if align is not None:p.alignment=align
    font(p.add_run(str(value)),size,bold);return p


def cell_text(cell,value,bold=False,color=BLACK,align=None,size=9.5):
    cell.text='';p=cell.paragraphs[0];p.paragraph_format.space_after=Pt(0);p.paragraph_format.space_before=Pt(0);p.paragraph_format.line_spacing=1.05
    if align is not None:p.alignment=align
    font(p.add_run(str(value)),size,bold,color);cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER


def table_style(table,widths,header=True):
    table.alignment=WD_TABLE_ALIGNMENT.CENTER;table.autofit=False
    for col,width in zip(table.columns,widths):col.width=Mm(width)
    pr=table._tbl.tblPr
    borders=OxmlElement('w:tblBorders')
    for name in ('top','left','bottom','right','insideH','insideV'):
        e=OxmlElement('w:'+name);e.set(qn('w:val'),'single');e.set(qn('w:sz'),'4');e.set(qn('w:color'),'D9D9D9');borders.append(e)
    pr.append(borders)
    for i,row in enumerate(table.rows):
        row._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
        if i==0 and header:row._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'))
        for j,cell in enumerate(row.cells):
            cell.width=Mm(widths[j]);cp=cell._tc.get_or_add_tcPr();m=OxmlElement('w:tcMar')
            for side,value in [('top',42),('bottom',42),('left',70),('right',70)]:
                e=OxmlElement('w:'+side);e.set(qn('w:w'),str(value));e.set(qn('w:type'),'dxa');m.append(e)
            cp.append(m);sh=OxmlElement('w:shd');sh.set(qn('w:fill'),NAVY if i==0 and header else (LIGHT if i%2==0 else 'FFFFFF'));cp.append(sh)


def picture(parent,path,width=None,height=None):
    p=parent.add_paragraph();p.alignment=WD_ALIGN_PARAGRAPH.CENTER;p.paragraph_format.space_after=Pt(1);p.paragraph_format.space_before=Pt(0);p.paragraph_format.keep_with_next=False
    # Always inline, preserving aspect ratio in Word and LibreOffice.
    if width and height:
        from docx.image.image import Image
        image=Image.from_file(str(path))
        kwargs={'width':Mm(min(width,height*image.px_width/image.px_height))}
    else:
        kwargs={'height':Mm(height)} if height else {'width':Mm(width)}
    shape=p.add_run().add_picture(str(path),**kwargs)
    shape._inline.docPr.set('descr',path.stem.replace('_',' '));return shape


def heading(doc,value,page_break=False):
    p=text(doc,value,17,True,style='Heading 1',after=5);p.paragraph_format.keep_with_next=True;p.paragraph_format.page_break_before=page_break


def page_setup(doc,label):
    section=doc.sections[0];section.orientation=WD_ORIENT.LANDSCAPE;section.page_width=Mm(297);section.page_height=Mm(210)
    section.top_margin=Mm(13);section.bottom_margin=Mm(13);section.left_margin=Mm(15);section.right_margin=Mm(15);section.header_distance=Mm(5);section.footer_distance=Mm(5)
    normal=doc.styles['Normal'];normal.font.name='Arial';normal.font.size=Pt(10.5);normal._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
    for name in ('Title','Subtitle','Heading 1','Heading 2'):
        style=doc.styles[name];style.font.color.rgb=RGBColor(0,0,0);style.font.name='Arial';style._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
    h=section.header.paragraphs[0];font(h.add_run('装箱排布报告    '+label),8.5)
    f=section.footer.paragraphs[0];f.alignment=WD_ALIGN_PARAGRAPH.RIGHT;font(f.add_run('第 '),8)
    for field,tail in [('PAGE',' 页 / 共 '),('NUMPAGES',' 页')]:
        run=f.add_run();node=OxmlElement('w:fldSimple');node.set(qn('w:instr'),field);run._r.addnext(node);font(f.add_run(tail),8)
    for border in list(doc.styles.element.iter(qn('w:pBdr'))):border.getparent().remove(border)
    for node in doc.styles.element.iter(qn('w:lang')):node.set(qn('w:eastAsia'),'zh-CN')
    update=OxmlElement('w:updateFields');update.set(qn('w:val'),'true');doc.settings.element.append(update)


def render(job,output,work,node,module_path):
    plan=copy.deepcopy(job['plan']);boxes=plan['containers'];work=Path(work);work.mkdir(parents=True,exist_ok=True)
    images=[]
    for b in boxes:
        for view in VIEWS:
            stem=f"container_{int(b['index']):03d}_{view}";svg=work/(stem+'.svg');png=work/(stem+'.png');svg.write_bytes(report_svg(b,view));images.append(dict(svg=str(svg),png=str(png)))
    manifest=work/'image_jobs.json';manifest.write_text(json.dumps(images))
    image_run=subprocess.run([node,str(Path(__file__).with_name('report_images.cjs')),str(manifest),module_path],check=False,timeout=240,capture_output=True)
    if image_run.returncode:raise RuntimeError('Image rendering failed: '+image_run.stderr.decode(errors='replace')[-2000:])
    doc=Document();label=str(job.get('mode_label') or '规划版');page_setup(doc,label)
    doc.core_properties.title='集装箱装箱排布报告';doc.core_properties.subject=str(plan.get('label') or plan.get('code'));doc.core_properties.author='Packing Planner';doc.core_properties.keywords='装箱排布 集装箱 四视图'
    text(doc,'集装箱装箱排布报告',25,True,style='Title',after=7)
    if job.get('project_name'):text(doc,job['project_name'],12,True,after=4)
    equipment=sum(len(b.get('placements',[])) for b in boxes);bags=sum(a['quantity'] for b in boxes for a in b.get('flexible_fill',[]));mass=sum(float(b['total_weight']) for b in boxes)
    text(doc,f"方案 {plan.get('label') or plan.get('code')}    共 {len(boxes)} 柜    设备及包装单元 {equipment} 件    珠光砂 {bags:,} 包    货物总重 {mass:,.1f} kg",11,True,after=4)
    text(doc,f"生成时间 {job['generated_at']}    文件状态 {label}    排布快照 {job['snapshot_id'][:12]}",9,after=5)
    text(doc,'本报告逐柜列示三维排布、俯视、侧视、端视及装载清单。图中编号与清单图号对应，相同来源的同规格单元合并列示。',10,after=4)
    if bags:text(doc,'珠光砂按每立方8包的声明体积分配；仅计入当前有效许可及可达条件允许的袋区。图中的袋形数量不代表实包数，实际数量以各柜清单为准。',9.5,after=5)
    if job.get('planning_only'):text(doc,'本文件为装箱规划资料。设备固定、顶部递袋或辅助送袋工法及运输复核按项目要求落实。',9.5,after=7)
    source=job.get('source') or {}
    if source.get('filename'):text(doc,f"原清单：{source['filename']}　SHA256：{str(source.get('sha256') or '')[:16]}",8.5,after=4)
    overview=doc.add_table(rows=1,cols=7)
    headers=['柜号','柜型','已定位单元','珠光砂 包','货物重量 kg','体积利用率','重量利用率']
    for c,v in zip(overview.rows[0].cells,headers):cell_text(c,v,True,'FFFFFF',WD_ALIGN_PARAGRAPH.CENTER)
    for b in boxes:
        row=overview.add_row();vals=[f"{b['index']:02d}",b['container_code'],len(b.get('placements',[])),sum(a['quantity'] for a in b.get('flexible_fill',[])),f"{b['total_weight']:,.1f}",f"{b['volume_utilization']:.1f}%",f"{b['weight_utilization']:.1f}%"]
        for c,v in zip(row.cells,vals):cell_text(c,v,align=WD_ALIGN_PARAGRAPH.CENTER)
    table_style(overview,[18,30,35,35,50,45,45])
    for b in boxes:
        index=int(b['index']);rows=cargo_rows(b);heading(doc,f"第 {index:02d} 柜 {b['container_code']} 装箱排布",page_break=True)
        bag_count=sum(a['quantity'] for a in b.get('flexible_fill',[]))
        text(doc,f"柜内尺寸 {b['inner_l']:g} × {b['inner_w']:g} × {b['inner_h']:g} mm    额定载荷 {b['payload']:,.0f} kg    本柜货重 {b['total_weight']:,.1f} kg    珠光砂 {bag_count:,} 包",9.5,after=4)
        warning=overheight_warning(b)
        if warning:text(doc,warning,10,bold=True,after=4)
        line_count=sum(max(1,len(r[1].splitlines())) for r in rows)
        separate_list=line_count>20
        figure_height=90 if separate_list else min(max(46,68-max(0,len(rows)-13)*5) if len(rows)>10 else 90, max(40,90-max(0,line_count-9)*5))
        picture(doc,work/f'container_{index:03d}_perspective.png',height=figure_height)
        if separate_list:
            heading(doc,f"第 {index:02d} 柜 {b['container_code']} 装载清单",page_break=True)
            text(doc,'图号对应上一页三维排布；货物名称来自箱内材料记录，箱号保留用于追溯。',9,after=5)
        else:
            text(doc,'三维排布与装载清单',10,True,after=4)
        table=doc.add_table(rows=1,cols=7)
        headers=['图号','货物名称','包装','数量','尺寸 mm 或袋装体积','单件重 kg','合计 kg']
        for c,v in zip(table.rows[0].cells,headers):cell_text(c,v,True,'FFFFFF',WD_ALIGN_PARAGRAPH.CENTER,size=9)
        for values in rows:
            cells=table.add_row().cells
            for col,(c,value) in enumerate(zip(cells,values)):cell_text(c,value,align=WD_ALIGN_PARAGRAPH.LEFT if col==1 else WD_ALIGN_PARAGRAPH.CENTER,size=9)
        table_style(table,[20,82,23,16,62,30,30])
        heading(doc,f"第 {index:02d} 柜 {b['container_code']} 俯视侧视与端视",page_break=True)
        text(doc,'X为柜头至柜门方向  Y为宽度方向  Z为高度方向  图号与本柜清单对应',9,after=5)
        text(doc,'俯视图',10,True,after=1);picture(doc,work/f'container_{index:03d}_top.png',height=50) if b['inner_l']/b['inner_w']<3.5 else picture(doc,work/f'container_{index:03d}_top.png',width=258)
        pair=doc.add_table(rows=1,cols=2);pair.autofit=False;pair.columns[0].width=Mm(188);pair.columns[1].width=Mm(75)
        for cell in pair.rows[0].cells:cell._tc.get_or_add_tcPr().append(OxmlElement('w:hideMark'))
        left,right=pair.rows[0].cells;left.width=Mm(188);right.width=Mm(75)
        cell_text(left,'侧视图',True,size=10);picture(left,work/f'container_{index:03d}_front.png',width=182)
        cell_text(right,'端视图',True,size=10);picture(right,work/f'container_{index:03d}_side.png',width=69,height=78)
    outside=job.get('outside_plan') or []
    if outside:
        heading(doc,'本方案之外的运输单元',page_break=True)
        text(doc,'以下货物仍保留在原清单中，未计入前述集装箱装载重量与图片。需另行安排运输或核实适用载具。',10,after=6)
        t=doc.add_table(rows=1,cols=4)
        for c,v in zip(t.rows[0].cells,['货物名称','尺寸 mm','重量 kg','安排 / 待核实项']):cell_text(c,v,True,'FFFFFF')
        for q in outside:
            vals=[q.get('display_name') or q['name'],' × '.join(f"{q[k]:g}" for k in ('length_mm','width_mm','height_mm')),f"{q['weight_kg']:,.1f}",'原表指定散货船；两段尺寸待提供' if q.get('transport_excluded') else '未满足当前启用柜型与项目净空约束；需另核框架箱或专运']
            for c,v in zip(t.add_row().cells,vals):cell_text(c,v)
        table_style(t,[65,60,35,100])
        text(doc,f"整票重量核对：柜内 {mass:,.1f} kg ＋ 方案外 {sum(float(q['weight_kg']) for q in outside):,.1f} kg ＝ {mass+sum(float(q['weight_kg']) for q in outside):,.1f} kg。",11,True,before=8)
    doc.save(output)
    summary=dict(containers=len(boxes),images=len(images),equipment_units=equipment,bag_quantity=bags,total_weight_kg=mass,views=list(VIEWS),lifting=False,snapshot_id=job['snapshot_id'])
    (work/'report_manifest.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary


def main():
    p=argparse.ArgumentParser();p.add_argument('--input',required=True);p.add_argument('--output',required=True);p.add_argument('--work',required=True);p.add_argument('--node',required=True);p.add_argument('--modules',required=True);a=p.parse_args()
    render(json.loads(Path(a.input).read_text()),a.output,a.work,a.node,a.modules)

if __name__=='__main__':main()
