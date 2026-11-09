/* Chinese lifting geometry preview. No lifting-point strength or sling-force approval. */
(function(root){
  'use strict';
  const FONT='"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif';
  function finite(v,fallback){if(v==null||v==='')return fallback;const n=Number(v);return Number.isFinite(n)?n:fallback;}
  function buildModel(box,options={}){
    const angle=finite(options.angle,60),setback=finite(options.setback,10),minWeight=finite(options.minWeight,500);
    if(angle<20||angle>80||setback<5||setback>40||minWeight<0)throw new Error('吊索角度应为20～80°，吊点内缩应为5～40%，筛选重量不得小于0。');
    const items=(box.placements||[]).filter(q=>finite(q.weight_kg,0)>=minWeight && (options.selected==null||q.seq===options.selected)).map(q=>{
      const x=Number(q.x),y=Number(q.y),z=Number(q.z),l=Number(q.l),w=Number(q.w),h=Number(q.h);
      if(![x,y,z,l,w,h].every(Number.isFinite)||Math.min(l,w,h)<=0)throw new Error('货物尺寸或坐标无效，无法生成吊点图。');
      const inset=setback/100,points=[[x+l*inset,y+w*inset,z+h],[x+l*(1-inset),y+w*inset,z+h],[x+l*(1-inset),y+w*(1-inset),z+h],[x+l*inset,y+w*(1-inset),z+h]];
      const cg=[x+finite(q.cg_local_x,l/2),y+finite(q.cg_local_y,w/2),z+finite(q.cg_local_z,h/2)];
      const radius=Math.max(...points.map(p=>Math.hypot(p[0]-cg[0],p[1]-cg[1])));
      const hook=[cg[0],cg[1],z+h+radius*Math.tan(angle*Math.PI/180)];
      const inside=cg[0]>=points[0][0] && cg[0]<=points[1][0] && cg[1]>=points[0][1] && cg[1]<=points[2][1];
      return {q,points,cg,hook,angle,inside,cgConfirmed:!!q.cg_confirmed,headroom:finite(box.inner_h,0)-hook[2]};
    });
    return {items,angle,setback,maxZ:Math.max(finite(box.inner_h,1),...items.map(i=>i.hook[2]))};
  }
  function settings(){return {angle:document.getElementById('liftingAngle')?.value||60,setback:document.getElementById('liftingSetback')?.value||10,minWeight:document.getElementById('liftingMinWeight')?.value??500};}
  function enabled(){return !!document.getElementById('showLifting')?.checked;}
  function el(tag,attrs={}){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));return n;}
  function text(svg,x,y,content,attrs={}){const n=el('text',{x,y,fill:'#243b53','font-size':13,'font-family':FONT,...attrs});n.textContent=content;svg.appendChild(n);return n;}
  function line(svg,a,b,attrs={}){svg.appendChild(el('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'#db7c19','stroke-width':1.7,...attrs}));}
  function dot(svg,p,color='#facc15',radius=4){svg.appendChild(el('circle',{cx:p.x,cy:p.y,r:radius,fill:color,stroke:'#473c25','stroke-width':1.1}));}
  function star(svg,p){const points=Array.from({length:10},(_,i)=>{const a=-Math.PI/2+i*Math.PI/5,r=i%2?4:10;return `${p.x+Math.cos(a)*r},${p.y+Math.sin(a)*r}`;}).join(' ');svg.appendChild(el('polygon',{points,fill:'#ef4444',stroke:'#7f1d1d','stroke-width':1.5}));}
  function overlay(svg,P,model){
    const g=el('g',{'pointer-events':'none','data-lifting-overlay':'true'});svg.appendChild(g);
    model.items.forEach(item=>{
      const hook=P(...item.hook);
      item.points.forEach((p,i)=>{const a=P(...p);line(g,a,hook);dot(g,a,'#f8fafc',3);text(g,a.x+5,a.y-5,`${item.q.seq}-${i+1}`,{'font-size':10});});
      line(g,P(...item.cg),hook,{'stroke':'#ef4444','stroke-dasharray':'3 4','stroke-opacity':.55});
      dot(g,P(...item.cg));dot(g,hook,'#ef4444',4);
      text(g,hook.x+7,hook.y-7,`吊钩 ${item.q.seq}`,{'font-size':11,'paint-order':'stroke',stroke:'#fff','stroke-width':3});
    });
    text(g,22,30,'吊点/吊索为假定布置 · 未确认 · 非施工依据',{fill:'#b45309','font-weight':700});
  }
  function planar(svg,box,P,view,selected,onSelect){
    const ps=[...(box.placements||[])].sort((a,b)=>view==='top'?a.z-b.z:view==='front'?b.y-a.y:a.x-b.x);
    ps.forEach(q=>{
      const corners=view==='top'?[[q.x,q.y,q.z+q.h],[q.x+q.l,q.y,q.z+q.h],[q.x+q.l,q.y+q.w,q.z+q.h],[q.x,q.y+q.w,q.z+q.h]]:view==='front'?[[q.x,q.y,q.z],[q.x+q.l,q.y,q.z],[q.x+q.l,q.y,q.z+q.h],[q.x,q.y,q.z+q.h]]:[[q.x,q.y,q.z],[q.x,q.y+q.w,q.z],[q.x,q.y+q.w,q.z+q.h],[q.x,q.y,q.z+q.h]];
      const projected=corners.map(p=>P(...p)),g=el('g',{'data-seq':q.seq,class:'cargo-group'}),poly=el('polygon',{points:projected.map(p=>`${p.x},${p.y}`).join(' '),fill:q.color||'#3b82f6','fill-opacity':.84,stroke:selected===q.seq?'#0f172a':'#334155','stroke-width':selected===q.seq?3:1.2});g.appendChild(poly);
      const title=el('title');title.textContent=`${q.seq}. ${q.name} · ${q.weight_kg} kg`;g.appendChild(title);
      const x=projected.reduce((s,p)=>s+p.x,0)/4,y=projected.reduce((s,p)=>s+p.y,0)/4;
      text(g,x,y,String(q.seq),{'text-anchor':'middle','font-weight':800,'paint-order':'stroke',stroke:'#fff','stroke-width':3});
      g.onclick=()=>onSelect(q.seq);svg.appendChild(g);
    });
    const labels={top:'俯视图（X—Y）',front:'侧视图（X—Z）',side:'端视图（Y—Z）'};
    text(svg,22,578,labels[view]||'',{'font-size':14,'font-weight':700});
  }
  function detail(box,model){
    const panel=document.getElementById('liftingPanel'),wrap=document.getElementById('liftingCanvas'),rows=document.getElementById('liftingRows');
    if(!panel||!wrap||!rows)return;
    panel.hidden=!enabled();if(panel.hidden)return;
    const L=Number(box.inner_l),H=Number(box.inner_h),W=Number(box.inner_w),maxZ=model.maxZ*1.10;
    const svg=el('svg',{viewBox:'0 0 1200 560',id:'liftingSvg',role:'img','aria-label':'中文吊点与吊索包络侧视图','font-family':FONT});
    svg.appendChild(el('rect',{width:1200,height:560,fill:'#fff'}));
    const truck=box.vehicle_type==='truck';
    text(svg,60,30,`第${box.index}${truck?'辆货车':'柜'}（${box.container_code}）吊点与吊索包络侧视图`,{'font-size':19,'font-weight':800});
    text(svg,60,54,`四点假定布置 · 吊索与水平面夹角不小于 ${model.angle}° · 吊点向内缩 ${model.setback}%`,{'font-size':12});
    const P=(x,z)=>({x:80+x/L*1060,y:454-z/maxZ*360});
    for(let i=0;i<=6;i++){const x=L*i/6;const a=P(x,0),b=P(x,maxZ);line(svg,a,b,{stroke:'#e2e8f0','stroke-dasharray':'2 4'});text(svg,a.x,478,Math.round(x),{'text-anchor':'middle','font-size':11});}
    for(let i=0;i<=4;i++){const z=maxZ*i/4,a=P(0,z),b=P(L,z);line(svg,a,b,{stroke:'#e2e8f0','stroke-dasharray':'2 4'});text(svg,69,a.y+4,Math.round(z),{'text-anchor':'end','font-size':11});}
    const bottom=P(0,0),top=P(0,H),right=P(L,H);
    svg.appendChild(el('rect',{x:top.x,y:top.y,width:right.x-top.x,height:bottom.y-top.y,fill:'none',stroke:'#334155','stroke-width':2,'stroke-dasharray':'8 5'}));
    line(svg,top,right,{stroke:'#0ea5e9','stroke-dasharray':'8 4'});
    text(svg,1140,83,`${truck?'货台上方限高':'柜内净高参考'} ${H} mm`,{'text-anchor':'end',fill:'#0369a1','font-size':11});
    line(svg,P(L/2,0),P(L/2,maxZ),{stroke:'#94a3b8','stroke-dasharray':'3 4'});
    const selectedSet=new Set(model.items.map(i=>i.q));
    [...(box.placements||[])].sort((a,b)=>b.y-a.y).forEach(q=>{
      const p=P(q.x,q.z+q.h),r=P(q.x+q.l,q.z);
      svg.appendChild(el('rect',{x:p.x,y:p.y,width:r.x-p.x,height:r.y-p.y,fill:q.color||'#3b82f6','fill-opacity':selectedSet.has(q) ? 0.72 : 0.25,stroke:'#475569'}));
      const width=r.x-p.x,limit=Math.max(3,Math.floor(width/9)),name=String(q.name||'货物');
      const caption=`#${q.seq} ${name.length>limit?name.slice(0,limit)+'…':name}`;
      text(svg,(p.x+r.x)/2,(p.y+r.y)/2-5,caption,{'text-anchor':'middle','font-size':11,'font-weight':800,'paint-order':'stroke',stroke:'#fff','stroke-width':3});
      if(r.y-p.y>32)text(svg,(p.x+r.x)/2,(p.y+r.y)/2+13,`${q.weight_kg} kg`,{'text-anchor':'middle','font-size':11,'paint-order':'stroke',stroke:'#fff','stroke-width':3});
    });
    model.items.forEach(i=>{
      const hook=P(i.hook[0],i.hook[2]);
      // Four 3D legs can overlap in side projection; all are retained in the SVG.
      i.points.forEach(p=>{const a=P(p[0],p[2]);line(svg,a,hook);dot(svg,a,'#fff',3);});
      line(svg,P(i.cg[0],0),hook,{stroke:'#ef4444','stroke-dasharray':'3 4','stroke-opacity':.5});
      dot(svg,P(i.cg[0],i.cg[2]));
      svg.appendChild(el('path',{d:`M${hook.x} ${hook.y-5} l-5 9 h10 z`,fill:'#dc2626'}));
      text(svg,hook.x,hook.y-12,`#${i.q.seq} 吊钩`,{'text-anchor':'middle','font-size':11,fill:'#991b1b','paint-order':'stroke',stroke:'#fff','stroke-width':3});
    });
    let weight=0,mx=0,mz=0;(box.placements||[]).forEach(q=>{const m=Number(q.weight_kg)||0;weight+=m;mx+=m*(q.x+finite(q.cg_local_x,q.l/2));mz+=m*(q.z+finite(q.cg_local_z,q.h/2));});
    if(weight>0){star(svg,P(mx/weight,mz/weight));text(svg,60,510,`★ 本${truck?'车':'柜'}货物合成重心：X=${Math.round(mx/weight)} mm，Z=${Math.round(mz/weight)} mm（不含${truck?'车辆':'柜体'}自重）`,{'font-size':12});}
    text(svg,600,498,truck?'货台长度方向 X（mm）　0＝货台前端，L＝货台后端':'柜长方向 X（mm）　0＝前端壁，L＝后门',{'text-anchor':'middle','font-size':12});
    text(svg,60,536,'规划示意 · 吊点未确认 · 索力及吊点强度未验算 · 非吊装施工依据',{fill:'#b45309','font-weight':700,'font-size':13});
    wrap.replaceChildren(svg);rows.replaceChildren();
    model.items.forEach(i=>{const tr=document.createElement('tr');[`${i.q.seq}. ${i.q.name}`,`${i.q.weight_kg} kg`,i.cgConfirmed?'已录入确认值':'几何中心假设',`${Math.round(i.hook[2])} mm`,i.headroom>=0?`余量 ${Math.round(i.headroom)} mm`:`超出 ${Math.round(-i.headroom)} mm`,i.inside?'假定吊点待确认':'重心超出假定吊点区域'].forEach(value=>{const td=document.createElement('td');td.textContent=value;tr.appendChild(td);});rows.appendChild(tr);});
    if(!model.items.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=6;td.textContent='当前筛选条件下没有货物，请降低重量门槛。';tr.appendChild(td);rows.appendChild(tr);}
    const note=document.getElementById('liftingContainerNote');if(note)note.textContent=truck?'货车：Z从货台面起算，限高线为货物装载包络；吊装、侧装与道路总高须分别确认，吊钩包络不是车体尺寸。':String(box.container_code||'').includes('OT')||String(box.container_code||'').includes('FR')?'开顶/框架柜仍需确认顶部构件、吊具、吊车和实际装载顺序。':'闭顶柜：本图仅展示货物在柜外的假定吊装包络，不表示可在柜内起吊；净高线仅用于几何对照。';
  }
  root.LiftingPreview={buildModel,settings,enabled,overlay,planar,detail};
  if(typeof module!=='undefined')module.exports={buildModel};
})(typeof window!=='undefined'?window:globalThis);
