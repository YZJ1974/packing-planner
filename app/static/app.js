function escapeHTML(value){return String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));}
async function postForm(url, form) {
  const fd = form instanceof FormData ? form : new FormData(form);
  const r = await fetch(url, {method:"POST", body:fd});
  if(!r.ok){
    let msg="操作失败";
    try{const d=await r.json(); if(d?.error) msg=d.error;}catch(e){}
    alert(msg); return;
  }
  location.reload();
}
document.getElementById("itemForm")?.addEventListener("submit", e => {
  e.preventDefault(); postForm("/items", e.target);
});
document.getElementById("crateForm")?.addEventListener("submit", e => {
  e.preventDefault(); postForm("/crates", e.target);
});
function assignItem(id, crateId){
  const fd=new FormData(); fd.append("item_id",id); fd.append("crate_id",crateId);
  postForm("/assign", fd);
}
function resizeCrate(e,id){
  e.preventDefault(); postForm(`/crates/${id}/resize`, e.target);
}
function splitCrate(id,parts){
  if(!confirm(`确定拆成 ${parts} 个木箱？系统会按重量尽量均衡分配当前物料。`)) return;
  const fd=new FormData(); fd.append("parts",parts); postForm(`/crates/${id}/split`,fd);
}
async function deleteItem(id){
  if(!confirm("删除此材料？")) return;
  await fetch(`/items/${id}`,{method:"DELETE"}); location.reload();
}
async function deleteCrate(id){
  if(!confirm("删除木箱？其中材料会恢复为未分配。")) return;
  await fetch(`/crates/${id}`,{method:"DELETE"}); location.reload();
}

const fileInput = document.getElementById("materialFile");
const fileName = document.getElementById("fileName");
const dropZone = document.getElementById("dropZone");
if(fileInput && fileName){
  fileInput.addEventListener("change", () => {
    fileName.textContent = fileInput.files?.[0]?.name || "尚未选择文件";
  });
}
if(dropZone && fileInput){
  ["dragenter","dragover"].forEach(ev => dropZone.addEventListener(ev, e => {e.preventDefault(); dropZone.classList.add("dragover");}));
  ["dragleave","drop"].forEach(ev => dropZone.addEventListener(ev, e => {e.preventDefault(); dropZone.classList.remove("dragover");}));
  dropZone.addEventListener("drop", e => {
    const files = e.dataTransfer.files;
    if(files && files.length){
      const dt = new DataTransfer(); dt.items.add(files[0]); fileInput.files = dt.files;
      fileName.textContent = files[0].name;
    }
  });
}

function cloneObj(o){return JSON.parse(JSON.stringify(o));}

const ENGINEERING_EVIDENCE_CLOSED=(document.body?.dataset?.evidenceClosed==='1');
const ENGINEERING_EVIDENCE_COUNT=Number(document.body?.dataset?.evidenceCount||0);
function auditStatusClass(st){return st==='通过'?'ok':((st==='不通过'||st==='高风险')?'bad':'warn');}
function evidenceStatusText(){return ENGINEERING_EVIDENCE_CLOSED?'当前已声明确认项已闭环':`未闭环 · NOT FOR RELEASE${ENGINEERING_EVIDENCE_COUNT?`（${ENGINEERING_EVIDENCE_COUNT}项）`:''}`;}
function evidenceStatusCard(){return `<div class="vs-card evidence-${ENGINEERING_EVIDENCE_CLOSED?'ok':'warn'}"><span>工程输入凭证一致性</span><strong>${evidenceStatusText()}</strong><small>${ENGINEERING_EVIDENCE_CLOSED?'附件/哈希/对象绑定/人工核对值一致；不代表正文工程结论已被软件验证。':'可继续 DRAFT 规划；正式装柜/整票资料必须保留 NOT FOR RELEASE 标识。'}</small></div>`;}
function appendEvidenceWatermark(host){if(!host||ENGINEERING_EVIDENCE_CLOSED)return;const w=document.createElement('div');w.className='credential-watermark';w.innerHTML='<b>工程输入凭证未闭环</b><span>NOT FOR RELEASE</span>';host.appendChild(w);}
function isoPoint(x,y,z,s,ox,oy){return{x:ox+x*s+y*s*.55,y:oy-z*s+y*s*.28};}
function pts(a){return a.map(p=>`${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');}
function svgEl(tag,a={}){const e=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(a).forEach(([k,v])=>e.setAttribute(k,v));return e;}
function shade(hex,p){const n=parseInt(hex.slice(1),16),a=Math.round(2.55*p),c=v=>Math.max(0,Math.min(255,v));return'#'+(0x1000000+(c((n>>16)+a)<<16)+(c((n>>8&255)+a)<<8)+c((n&255)+a)).toString(16).slice(1);}
function collide(a,b){return a.x<b.x+b.l&&a.x+a.l>b.x&&a.y<b.y+b.w&&a.y+a.w>b.y&&a.z<b.z+b.h&&a.z+a.h>b.z;}
function overlapXY(a,b){return Math.max(0,Math.min(a.x+a.l,b.x+b.l)-Math.max(a.x,b.x))*Math.max(0,Math.min(a.y+a.w,b.y+b.w)-Math.max(a.y,b.y));}
function containerCg(box){const ps=box.placements||[],tw=ps.reduce((x,q)=>x+Number(q.weight_kg||0),0),L=box.inner_l||1,W=box.inner_w||1,H=box.inner_h||1;if(!tw)return{x:50,y:50,z:0,ox:0,oy:0};const cx=ps.reduce((x,q)=>x+Number(q.weight_kg||0)*(q.x+Number(q.cg_local_x??q.l/2)),0)/tw,cy=ps.reduce((x,q)=>x+Number(q.weight_kg||0)*(q.y+Number(q.cg_local_y??q.w/2)),0)/tw,cz=ps.reduce((x,q)=>x+Number(q.weight_kg||0)*(q.z+Number(q.cg_local_z??q.h/2)),0)/tw;const xp=cx/L*100,yp=cy/W*100;return{x:xp,y:yp,z:cz/H*100,ox:Math.abs(xp-50),oy:Math.abs(yp-50)};}
const CONTAINER_DEG=Math.PI/180;
const CONTAINER_CAMERA_PRESETS={
  perspective:{yaw:-29*CONTAINER_DEG,pitch:-16*CONTAINER_DEG,zoom:1,panX:0,panY:0},
  top:{yaw:0,pitch:-90*CONTAINER_DEG,zoom:1,panX:0,panY:0},
  front:{yaw:0,pitch:0,zoom:1,panX:0,panY:0},
  side:{yaw:-90*CONTAINER_DEG,pitch:0,zoom:1,panX:0,panY:0}
};
const vstate={data:{},original:{},selected:null,view:'perspective',camera:{...CONTAINER_CAMERA_PRESETS.perspective},suppressClickUntil:0,lastSvg:null,dirtyKeys:new Set()};
function currentLayoutKey(){const p=curPlan(),b=curBox();return p&&b?`${p.code}:${b.index}`:'';}
function markLayoutDirty(){const k=currentLayoutKey();if(k)vstate.dirtyKeys.add(k);const p=curPlan(),b=curBox();if(p&&b){fetch('/container-layout/dirty',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scheme:p.code,container_index:b.index})}).catch(()=>{});}}
function clearLayoutDirty(){const k=currentLayoutKey();if(k)vstate.dirtyKeys.delete(k);}
function isCurrentLayoutDirty(){const k=currentLayoutKey();return !!k&&vstate.dirtyKeys.has(k);}
window.containerLayoutHasDirty=function(all=false){return all?vstate.dirtyKeys.size>0:isCurrentLayoutDirty();};
function cgThresholds(){const n=(name,d)=>Number(document.querySelector(`[name="${name}"]`)?.value||d);return{hReview:n('cg_review_pct',10),hHigh:n('cg_high_risk_pct',20),zReview:n('cg_z_review_pct',60),zHigh:n('cg_z_high_risk_pct',75)};}
function setContainerCameraPreset(view){vstate.view=view;vstate.camera={...(CONTAINER_CAMERA_PRESETS[view]||CONTAINER_CAMERA_PRESETS.perspective)};}
function markContainerFreeView(){vstate.view='free';document.querySelectorAll('[data-container-view]').forEach(b=>b.classList.remove('active'));}
function containerProjectFactory(L,W,H,fitH=H){
  const pad=72,vw=1080,vh=600,cx=L/2,cy=W/2,cz=H/2;
  const yaw=vstate.camera.yaw,pitch=vstate.camera.pitch,cyaw=Math.cos(yaw),syaw=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
  function raw(x,y,z){
    const dx=x-cx,dy=y-cy,dz=z-cz;
    const x1=dx*cyaw-dy*syaw;
    const y1=dx*syaw+dy*cyaw;
    const y2=y1*cp-dz*sp;
    const z2=y1*sp+dz*cp;
    return {u:x1,v:-z2,d:y2};
  }
  const corners=[[0,0,0],[L,0,0],[0,W,0],[L,W,0],[0,0,fitH],[L,0,fitH],[0,W,fitH],[L,W,fitH]].map(c=>raw(...c));
  const us=corners.map(p=>p.u),vs=corners.map(p=>p.v),minU=Math.min(...us),maxU=Math.max(...us),minV=Math.min(...vs),maxV=Math.max(...vs);
  const spanU=Math.max(1,maxU-minU),spanV=Math.max(1,maxV-minV),fit=Math.min((vw-2*pad)/spanU,(vh-2*pad)/spanV);
  const scale=fit*Math.max(.35,Math.min(4,vstate.camera.zoom||1)),midU=(minU+maxU)/2,midV=(minV+maxV)/2;
  const ox=vw/2-midU*scale+(vstate.camera.panX||0),oy=vh/2-midV*scale+(vstate.camera.panY||0);
  const P=(x,y,z)=>{const r=raw(x,y,z);return{x:ox+r.u*scale,y:oy+r.v*scale};};
  const pointDepth=(x,y,z)=>raw(x,y,z).d;
  const depth=q=>pointDepth(q.x+q.l/2,q.y+q.w/2,q.z+q.h/2);
  return {P,depth,pointDepth};
}
function validate(plan,box){
  const issues=[];const L=box.inner_l,W=box.inner_w,H=box.inner_h,isOT=String(box.container_code||'').includes('OT');
  box.placements.forEach((a,i)=>{
    if(a.x<0||a.y<0||a.z<0||a.x+a.l>L||a.y+a.w>W||(!isOT&&a.z+a.h>H)||(isOT&&a.z>1e-6&&a.z+a.h>H))issues.push(`${a.name}越界`);
    box.placements.slice(i+1).forEach(b=>{if(collide(a,b))issues.push(`${a.name}与${b.name}碰撞`);});
  });
  return [...new Set(issues),...((box.engineering_audit||{}).hard_fail_notes||[])];
}
function key(code,index){return`packing-v14:${code}:${index}`;}
function curPlan(){return vstate.data[document.getElementById('schemeSelect')?.value];}
function curBox(){const p=curPlan();if(!p)return null;const i=Number(document.getElementById('containerSelect')?.value||1);return p.containers.find(x=>x.index===i)||p.containers[0];}

// V3.1: package-shape renderer for the WHOLE CONTAINER.
// Calculation still uses conservative cuboid envelopes; only the display geometry changes.
function containerPkgKind(q){
  const s=`${q.name||''} ${q.type||''}`;
  if(/裸装扎捆|扎捆|BND-/.test(s)) return 'bundle';
  if(/木箱|BX-PF-|PK-/.test(s)) return 'crate';
  if(['horizontal_cylinder','upright_cylinder'].includes(q.display_shape)) return 'cylinder';
  if(/电缆盘|线盘|卷盘|drum|reel/i.test(s)) return 'drum';
  if(/管道|直管|长料/.test(s)) return 'long';
  return 'box';
}
function parseBundleCount(q){
  const m=String(q.name||'').match(/[｜|]\s*(\d+)\s*根/);
  return m?Math.max(1,Number(m[1])):8;
}
function drawContainerEnvelope(g,q,P,selected){
  const A=P(q.x,q.y,q.z),B=P(q.x+q.l,q.y,q.z),C=P(q.x+q.l,q.y+q.w,q.z),D=P(q.x,q.y+q.w,q.z),
        A2=P(q.x,q.y,q.z+q.h),B2=P(q.x+q.l,q.y,q.z+q.h),C2=P(q.x+q.l,q.y+q.w,q.z+q.h),D2=P(q.x,q.y+q.w,q.z+q.h);
  [[A,B],[B,C],[C,D],[D,A],[A2,B2],[B2,C2],[C2,D2],[D2,A2],[A,A2],[B,B2],[C,C2],[D,D2]].forEach(([a,b])=>{
    g.appendChild(svgEl('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:selected?'#0f172a':'#64748b','stroke-width':selected?2:1,'stroke-dasharray':'5 4','stroke-opacity':'.32'}));
  });
}
function drawBundle3D(g,q,P,color,selected){
  const count=parseBundleCount(q);
  const cols=Math.max(1,Math.ceil(Math.sqrt(count*q.w/Math.max(q.h,1))));
  const rows=Math.max(1,Math.ceil(count/cols));
  const dy=q.w/(cols+1), dz=q.h/(rows+1);
  const scaleLine=Math.max(3,Math.min(12,Math.min(q.w/cols,q.h/rows)/55));
  let n=0;
  for(let r=0;r<rows && n<count;r++){
    for(let c=0;c<cols && n<count;c++,n++){
      const yy=q.y+(c+1)*dy, zz=q.z+(r+1)*dz;
      const a=P(q.x+q.l*.035,yy,zz), b=P(q.x+q.l*.965,yy,zz);
      g.appendChild(svgEl('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'#334155','stroke-width':scaleLine+3,'stroke-linecap':'round'}));
      g.appendChild(svgEl('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:color,'stroke-width':scaleLine,'stroke-linecap':'round'}));
      g.appendChild(svgEl('circle',{cx:a.x,cy:a.y,r:Math.max(2,scaleLine*.46),fill:shade(color,18),stroke:'#334155','stroke-width':'1'}));
    }
  }
  // two steel straps around the bundle
  [0.30,0.70].forEach(t=>{
    const p0=P(q.x+q.l*t,q.y+q.w*.06,q.z+q.h*.05), p1=P(q.x+q.l*t,q.y+q.w*.94,q.z+q.h*.05),
          p2=P(q.x+q.l*t,q.y+q.w*.94,q.z+q.h*.95);
    g.appendChild(svgEl('polyline',{points:`${p0.x},${p0.y} ${p1.x},${p1.y} ${p2.x},${p2.y}`,fill:'none',stroke:'#475569','stroke-width':'3','stroke-opacity':'.9'}));
  });
  // timber skids
  [0.20,0.80].forEach(t=>{
    const a=P(q.x+q.l*t,q.y+q.w*.03,q.z), b=P(q.x+q.l*t,q.y+q.w*.97,q.z);
    g.appendChild(svgEl('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'#92400e','stroke-width':'6','stroke-linecap':'round'}));
  });
}
function drawWoodCrate3D(g,q,P,selected){
  const A=P(q.x,q.y,q.z),B=P(q.x+q.l,q.y,q.z),C=P(q.x+q.l,q.y+q.w,q.z),A2=P(q.x,q.y,q.z+q.h),B2=P(q.x+q.l,q.y,q.z+q.h),C2=P(q.x+q.l,q.y+q.w,q.z+q.h),D2=P(q.x,q.y+q.w,q.z+q.h);
  const edge=selected?'#111827':'#78350f';
  g.appendChild(svgEl('polygon',{points:pts([A,B,B2,A2]),fill:'#d6a56f','fill-opacity':'.88',stroke:edge,'stroke-width':selected?3:1.4}));
  g.appendChild(svgEl('polygon',{points:pts([B,C,C2,B2]),fill:'#b7793f','fill-opacity':'.9',stroke:edge,'stroke-width':selected?3:1.4}));
  g.appendChild(svgEl('polygon',{points:pts([A2,B2,C2,D2]),fill:'#e9c89d','fill-opacity':'.92',stroke:edge,'stroke-width':selected?3:1.4}));
  // slats on front face
  for(let t=.18;t<.95;t+=.18){
    const a=P(q.x+q.l*t,q.y,q.z+q.h*.04),b=P(q.x+q.l*t,q.y,q.z+q.h*.96);
    g.appendChild(svgEl('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'#8b5e34','stroke-width':'1.4','stroke-opacity':'.8'}));
  }
  for(let t=.25;t<.9;t+=.25){
    const a=P(q.x,q.y,q.z+q.h*t),b=P(q.x+q.l,q.y,q.z+q.h*t);
    g.appendChild(svgEl('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'#8b5e34','stroke-width':'1.2','stroke-opacity':'.65'}));
  }
}
function drawCableDrum3D(g,q,P,color,selected){
  const cx=q.x+q.l*.5, cy=q.y+q.w*.5, z=q.z+q.h*.5;
  const left=P(q.x+q.l*.18,cy,z), right=P(q.x+q.l*.82,cy,z);
  const r=Math.max(10,Math.min(34,q.h/70));
  g.appendChild(svgEl('line',{x1:left.x,y1:left.y,x2:right.x,y2:right.y,stroke:'#713f12','stroke-width':Math.max(8,r*.55),'stroke-linecap':'round'}));
  [left,right].forEach(p=>{
    g.appendChild(svgEl('circle',{cx:p.x,cy:p.y,r,fill:'#c08457',stroke:selected?'#111827':'#78350f','stroke-width':selected?3:2}));
    g.appendChild(svgEl('circle',{cx:p.x,cy:p.y,r:r*.24,fill:'#f3e1c7',stroke:'#78350f','stroke-width':'1'}));
  });
}
function lerpScreen(a,b,t){return{x:a.x+(b.x-a.x)*t,y:a.y+(b.y-a.y)*t};}
function drawContainerCuboid(g,q,P,pointDepth,color,selected,wood=false){
  const c=[
    [q.x,q.y,q.z],[q.x+q.l,q.y,q.z],[q.x+q.l,q.y+q.w,q.z],[q.x,q.y+q.w,q.z],
    [q.x,q.y,q.z+q.h],[q.x+q.l,q.y,q.z+q.h],[q.x+q.l,q.y+q.w,q.z+q.h],[q.x,q.y+q.w,q.z+q.h]
  ];
  const pp=c.map(v=>P(...v));
  const faces=[
    {idx:[0,1,2,3],tone:-24,name:'bottom'},{idx:[4,5,6,7],tone:18,name:'top'},
    {idx:[0,1,5,4],tone:-5,name:'front'},{idx:[1,2,6,5],tone:-20,name:'right'},
    {idx:[2,3,7,6],tone:-12,name:'back'},{idx:[3,0,4,7],tone:5,name:'left'}
  ].map(f=>({...f,depth:f.idx.reduce((a,i)=>a+pointDepth(...c[i]),0)/4})).sort((a,b)=>a.depth-b.depth);
  const woodTone={bottom:'#a66f3d',top:'#e9c89d',front:'#d6a56f',right:'#b7793f',back:'#c68c52',left:'#d9aa76'};
  faces.forEach(f=>{
    const fp=f.idx.map(i=>pp[i]),fill=wood?(woodTone[f.name]||'#d6a56f'):shade(color,f.tone);
    g.appendChild(svgEl('polygon',{points:pts(fp),fill,stroke:selected?'#111827':(wood?'#78350f':'#334155'),'stroke-width':selected?3:1.15,'fill-opacity':wood?'.92':'.94'}));
    if(wood && ['front','right','back','left'].includes(f.name)){
      const [a,b,c2,d]=fp;
      [.2,.4,.6,.8].forEach(t=>{const p0=lerpScreen(a,b,t),p1=lerpScreen(d,c2,t);g.appendChild(svgEl('line',{x1:p0.x,y1:p0.y,x2:p1.x,y2:p1.y,stroke:'#8b5e34','stroke-width':'1.1','stroke-opacity':'.72'}));});
      [.33,.66].forEach(t=>{const p0=lerpScreen(a,d,t),p1=lerpScreen(b,c2,t);g.appendChild(svgEl('line',{x1:p0.x,y1:p0.y,x2:p1.x,y2:p1.y,stroke:'#8b5e34','stroke-width':'1','stroke-opacity':'.55'}));});
    }
  });
}
function drawHorizontalCylinder(g,q,P,pointDepth,color,selected){
  const n=48,axis=q.cylinder_axis||'x';
  const rings=[0,1].map(end=>Array.from({length:n},(_,i)=>{
    const a=2*Math.PI*i/n;
    return axis==='x'?[q.x+q.l*end,q.y+q.w/2+q.w/2*Math.cos(a),q.z+q.h/2+q.h/2*Math.sin(a)]:axis==='y'?[q.x+q.l/2+q.l/2*Math.cos(a),q.y+q.w*end,q.z+q.h/2+q.h/2*Math.sin(a)]:[q.x+q.l/2+q.l/2*Math.cos(a),q.y+q.w/2+q.w/2*Math.sin(a),q.z+q.h*end];
  }));
  const faces=[{p:rings[0],tone:12,cap:true},{p:[...rings[1]].reverse(),tone:-12,cap:true}];
  for(let i=0;i<n;i++)faces.push({p:[rings[0][i],rings[0][(i+1)%n],rings[1][(i+1)%n],rings[1][i]],tone:Math.round(14*Math.sin(2*Math.PI*(i+.5)/n)),cap:false});
  faces.map(f=>({...f,depth:f.p.reduce((s,p)=>s+pointDepth(...p),0)/f.p.length})).sort((a,b)=>a.depth-b.depth).forEach(f=>{
    g.appendChild(svgEl('polygon',{points:pts(f.p.map(p=>P(...p))),fill:shade(color,f.tone),stroke:f.cap?(selected?'#111827':'#334155'):shade(color,f.tone),'stroke-width':f.cap?(selected?3:1.2):.4,'data-cylinder-face':f.cap?'end':'shell'}));
  });
}
function drawRepresentativeBag(g,quad,tone){
  const at=(u,v)=>({x:(1-v)*((1-u)*quad[0].x+u*quad[1].x)+v*((1-u)*quad[3].x+u*quad[2].x),y:(1-v)*((1-u)*quad[0].y+u*quad[1].y)+v*((1-u)*quad[3].y+u*quad[2].y)});
  const pairs=args=>args.map(([u,v])=>{const p=at(u,v);return `${p.x},${p.y}`;}).join(' ');
  const d='M '+pairs([[.17,.1]])+' Q '+pairs([[.5,.02],[.83,.1]])+' C '+pairs([[.92,.17],[.99,.32],[.96,.54]])+' Q '+pairs([[.99,.82],[.82,.91]])+' Q '+pairs([[.5,.98],[.18,.91]])+' C '+pairs([[.01,.82],[.04,.66],[.04,.5]])+' Q '+pairs([[.01,.23],[.17,.1]])+' Z';
  g.appendChild(svgEl('path',{d,fill:tone?'#eee4ce':'#fff8e9','fill-opacity':'.66',stroke:'#ae9a77','stroke-width':'.8','data-bag-glyph':'representative-only'}));
  [[[.23,.14],[.77,.14]],[[.17,.2],[.29,.28]],[[.83,.2],[.71,.28]]].forEach(([a,b])=>{const A=at(...a),B=at(...b);g.appendChild(svgEl('line',{x1:A.x,y1:A.y,x2:B.x,y2:B.y,stroke:'#9f8963','stroke-width':'.75'}));});
}
function drawBagVolumeDisplay(svg,box,P,pointDepth){
  (box.flexible_zones||[]).forEach(z=>{const g=svgEl('g',{'data-flexible-zone':'volume-only',opacity:'.30'});drawContainerCuboid(g,z,P,pointDepth,'#e9d8ad',false,false);svg.appendChild(g);});
  const patches=[...(box.bag_display_patches||[])].sort((a,b)=>a.corners.reduce((s,p)=>s+pointDepth(...p),0)-b.corners.reduce((s,p)=>s+pointDepth(...p),0));
  const g=svgEl('g',{'data-bag-stack':'illustrative-not-individual-placement'});
  patches.forEach(p=>{const q=p.corners.map(v=>P(...v));const area=Math.abs(q.reduce((a,v,i)=>a+v.x*q[(i+1)%4].y-v.y*q[(i+1)%4].x,0))/2;if(area>1)drawRepresentativeBag(g,q,p.tone);});svg.appendChild(g);
  (box.flexible_zones||[]).forEach(z=>{if(!z.quantity)return;const c=P(z.x+z.l/2,z.y+z.w/2,z.z+z.h);const label=svgEl('text',{x:c.x,y:c.y-5,'text-anchor':'middle','font-size':11,'font-weight':700,fill:'#713f12',stroke:'#fff','stroke-width':3,'paint-order':'stroke'});label.textContent=`袋区${z.loading_order} · ${z.quantity}包`;svg.appendChild(label);});
}
function drawContainerPackage(svg,q,P,pointDepth,selected){
  const kind=containerPkgKind(q), color=q.color||'#2563eb';
  const g=svgEl('g',{class:`cargo-group real-package ${kind} ${selected?'selected':''}`,'data-seq':q.seq,filter:'url(#softShadow)'});
  if(kind==='bundle') drawBundle3D(g,q,P,color,selected);
  else if(kind==='crate') drawContainerCuboid(g,q,P,pointDepth,color,selected,true);
  else if(kind==='cylinder') drawHorizontalCylinder(g,q,P,pointDepth,color,selected);
  else if(kind==='drum') drawCableDrum3D(g,q,P,color,selected);
  else drawContainerCuboid(g,q,P,pointDepth,color,selected,false);
  drawContainerEnvelope(g,q,P,selected);
  const center=P(q.x+q.l*.5,q.y+q.w*.5,q.z+q.h*.98);
  const t=svgEl('text',{x:center.x,y:center.y-5,'text-anchor':'middle','font-size':'12','font-weight':'900',fill:'#0f172a','paint-order':'stroke',stroke:'#fff','stroke-width':'3'});
  t.textContent=q.seq;g.appendChild(t);
  g.onclick=()=>{if(Date.now()<vstate.suppressClickUntil)return;vstate.selected=q.seq;render();};
  svg.appendChild(g);
}
function render(){
  const plan=curPlan(),box=curBox();if(!plan||!box)return;
  const isTruck=box.vehicle_type==='truck',isFlatbed=isTruck&&box.body_type==='flatbed';
  const wrap=document.getElementById('viewerCanvas'),sum=document.getElementById('viewerSummary'),leg=document.getElementById('legendWrap');
  wrap.innerHTML='';leg.innerHTML='';
  const L=box.inner_l||plan.inner_l,W=box.inner_w||plan.inner_w,H=box.inner_h||plan.inner_h;
  let liftingModel=null;
  try{if(window.LiftingPreview?.enabled())liftingModel=LiftingPreview.buildModel(box,LiftingPreview.settings());}
  catch(e){const status=document.getElementById('liftingInputStatus');if(status)status.textContent=e.message;}
  const {P,depth,pointDepth}=containerProjectFactory(L,W,H,liftingModel?liftingModel.maxZ*1.08:H),svg=svgEl('svg',{viewBox:'0 0 1080 600',id:'containerSvg',class:'svg-3d container-orbit-svg'});
  const p000=P(0,0,0),p100=P(L,0,0),p010=P(0,W,0),p110=P(L,W,0),p001=P(0,0,H),p101=P(L,0,H),p011=P(0,W,H),p111=P(L,W,H);
  svg.appendChild(svgEl('polygon',{points:pts([p000,p100,p110,p010]),fill:'#eff6ff','fill-opacity':'.72',stroke:'#cbd5e1'}));
  const ordered=[...(box.placements||[])].sort((a,b)=>depth(a)-depth(b));
  if(['top','front','side'].includes(vstate.view)&&window.LiftingPreview){
    LiftingPreview.planar(svg,box,P,vstate.view,vstate.selected,seq=>{vstate.selected=seq;render();});
  }else ordered.forEach(q=>drawContainerPackage(svg,q,P,pointDepth,vstate.selected===q.seq));
  if(box.flexible_fill?.length)drawBagVolumeDisplay(svg,box,P,pointDepth);
  if(box.flexible_fill?.length){const label=svgEl('text',{x:24,y:568,'font-size':13,fill:'#854d0e','font-weight':700});label.textContent='珠光砂分阶段可达装袋区 · 实包数见清单 · 袋形为示意';svg.appendChild(label);}
  [[p000,p100],[p100,p110],[p110,p010],[p010,p000],[p001,p101],[p101,p111],[p111,p011],[p011,p001],[p000,p001],[p100,p101],[p010,p011],[p110,p111]].forEach(([a,b],i)=>svg.appendChild(svgEl('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:isFlatbed&&i<4?'#176a7a':'#64748b','stroke-width':isFlatbed&&i<4?'3':'1.35','stroke-opacity':'.9',...(isFlatbed&&i>=4?{'stroke-dasharray':'6 5'}:{})})));
  if(isTruck){const note=svgEl('text',{x:22,y:590,'font-size':12,fill:'#315364'});note.textContent='货车货台配载 · Z=0为货台面；虚线为允许装载包络，非车体实体';svg.appendChild(note);}
  if(liftingModel){LiftingPreview.overlay(svg,P,liftingModel);LiftingPreview.detail(box,liftingModel);}
  else{const lp=document.getElementById('liftingPanel');if(lp)lp.hidden=true;}
  wrap.appendChild(svg);appendEvidenceWatermark(wrap);vstate.lastSvg=svg;
  const pa=box.engineering_audit||{};
  sum.innerHTML=`<div class="vs-card"><span>柜型</span><strong>${escapeHTML(box.container_code||plan.label||plan.code)}</strong></div><div class="vs-card"><span>柜号</span><strong>${box.index}/${plan.count}</strong></div><div class="vs-card"><span>体积利用率</span><strong>${box.volume_utilization}%</strong></div><div class="vs-card"><span>重量利用率</span><strong>${box.weight_utilization}%</strong></div><div class="vs-card"><span>本柜重量</span><strong>${box.total_weight} kg</strong></div><div class="vs-card"><span>柜内尺寸</span><strong>${(L/1000).toFixed(2)}×${(W/1000).toFixed(2)}×${(H/1000).toFixed(2)} m</strong></div><div class="vs-card"><span>布局状态</span><strong>${isCurrentLayoutDirty()?'未保存':(box.manual_saved?'手动已保存':'自动方案')}</strong><small>自动/手动布局状态，不代表工程凭证闭环</small></div><div class="vs-card"><span>静态规划校核</span><strong class="audit-${auditStatusClass(pa.status||'')}">${escapeHTML(pa.status||'—')}</strong><small>仅代表当前静态模型</small></div>${evidenceStatusCard()}<div class="vs-card"><span>静态接触压力</span><strong>${pa.floor_load_checked?(pa.max_floor_pressure_kg_m2+' kg/m²'):'未提供限值'}</strong><small>${pa.floor_load_checked?('项目限值 '+pa.floor_load_limit_kg_m2+'；未确认接触面积 '+(pa.floor_contact_unconfirmed_count||0)+' 件'):'仅规划估算；非局部点载/横梁验算'}</small></div><div class="vs-card"><span>装载路径</span><strong>${pa.maneuver_review_count||0} 件需横移复核</strong><small>直线 ${pa.straight_in_count||0} 件 · 举升/堆叠 ${pa.lift_place_count||0} 件</small></div><div class="vs-card"><span>CG来源</span><strong>${pa.assumed_cg_count||0} 件采用形心</strong><small>其中关键重件 ${pa.critical_assumed_cg_count||0} 件；关键件未知CG进入工程复核</small></div>`;
  if(isTruck){
    const labels={'柜型':'车型','柜号':'车序号','本柜重量':'本车货物重量','柜内尺寸':'货台可用尺寸'};
    sum.querySelectorAll('.vs-card>span').forEach(e=>{if(labels[e.textContent])e.textContent=labels[e.textContent];});
    const ax=pa.truck_axle_loads;
    sum.insertAdjacentHTML('beforeend',`<div class="vs-card"><span>模型范围装车总质量</span><strong>${pa.truck_gross_kg!=null?Number(pa.truck_gross_kg).toFixed(1)+' kg':'缺空载质量，待复核'}</strong><small>上限：${pa.truck_max_gross_kg!=null?Number(pa.truck_max_gross_kg).toFixed(1)+' kg':'未填写'}；不代表组合车辆全车总质量</small></div>`);
    sum.insertAdjacentHTML('beforeend',`<div class="vs-card"><span>装车总高度</span><strong>${pa.truck_loaded_height_mm!=null?Number(pa.truck_loaded_height_mm).toFixed(0)+' mm':'待填写实车高度'}</strong><small>含货台离地及空车自身高度</small></div><div class="vs-card"><span>两支承组静态载荷</span><strong>${ax?'A '+Number(ax.loaded_a_kg).toFixed(1)+' / B '+Number(ax.loaded_b_kg).toFixed(1)+' kg':'参数缺失，待复核'}</strong><small>不等于各实际车轴载荷；纵向重心按支承组模型复核</small></div>`);
  }
  if(box.flexible_fill?.length){sum.insertAdjacentHTML('beforeend',`<div class="vs-card"><span>柔性袋装体积分配</span><strong>${box.flexible_fill.reduce((s,a)=>s+a.quantity,0)} 包 / ${box.flexible_fill.reduce((s,a)=>s+a.volume_m3,0).toFixed(3)} m³</strong><small>袋形/封口为代表性示意，图中袋形数量不等于实包数。按阶段检查工作面与路径；顶部许可来源见本柜确认条件，顶部递袋/辅助工法及整柜重心待现场复核。</small></div>`);sum.querySelectorAll('.vs-card>span').forEach(e=>{if(e.textContent==='CG来源')e.textContent='设备/木箱CG来源（未含柔性袋装）';if(e.textContent==='体积利用率')e.textContent='体积利用率（含柔性体积估算）';});}
  if(box.bag_access_audit){const a=box.bag_access_audit;sum.insertAdjacentHTML('beforeend',`<div class="vs-card"><span>珠光砂分阶段可达区域</span><strong>${a.eligible_volume_m3.toFixed(3)} m³</strong><small>${a.confirmation_source==='USER_PROJECT_DECLARATION'?'本项目用户已确认可在设备顶部放珠光砂（含吸附器、桥架、平台）；按声明密度规划，未改成厂家数值顶载。':'未确认顶载的设备上方仍不自动使用。'} 未计入窄缝或不可达体积 ${a.excluded_void_volume_m3.toFixed(3)} m³；身体作业通道按800mm宽/1800mm高规划。</small></div>`);}
  if(box.bag_access_audit?.loading_steps?.length){const panel=document.createElement('details');panel.className='vs-card bag-loading-steps';const title=document.createElement('summary');title.textContent='珠光砂装载步骤（规划，需现场确认）';panel.appendChild(title);const list=document.createElement('ol');box.bag_access_audit.loading_steps.forEach(s=>{const li=document.createElement('li');const before=s.install_before.length?'设备编号 '+s.install_before.join('、')+' 就位并固定后':'空柜';li.textContent=`第${s.order}步：${before}，填装${s.quantity}包（${s.volume_m3.toFixed(3)}m³），X=${Math.round(s.zone.x)}～${Math.round(s.zone.x+s.zone.l)}mm、Y=${Math.round(s.zone.y)}～${Math.round(s.zone.y+s.zone.w)}mm、底高Z=${Math.round(s.zone.z)}mm；${s.kind==='floor'?'落地堆填':s.kind==='user_confirmed_top'?'用户确认的设备顶部袋区（设备编号 '+(s.support_sequences||[s.support_sequence]).join('、')+'）'+(s.top_budget_kg!=null?'；已知剩余顶载限值'+s.top_budget_kg+'kg':'；无另设数值顶载'):'木箱'+s.support_sequence+'顶区，剩余顶载限值'+s.top_budget_kg+'kg'}。${s.kind==='user_confirmed_top'?'从该阶段开敞工作面递袋/辅助送袋，所需最远送袋距离 '+(s.required_delivery_reach_mm/1000).toFixed(2)+'m、顶部通路净空 '+Math.round(s.delivery_headroom_mm)+'mm'+(s.outside_work_depth_mm>0?'，工作面向柜门外延伸 '+Math.round(s.outside_work_depth_mm)+'mm':'')+'；不要求人员站入最终袋区。':''}保留800mm宽/1800mm高的身体作业路径；${s.install_later.length?'之后再装设备编号 '+s.install_later.join('、'):'无后续设备，按从内向门顺序完成本区填装'}。`;list.appendChild(li);});panel.appendChild(list);leg.appendChild(panel);}
  const grid=document.createElement('div');grid.className='legend-grid';box.placements.forEach(q=>{const r=document.createElement('div');r.className=`legend-item ${vstate.selected===q.seq?'selected':''}`;r.innerHTML=`<span class="legend-color" style="background:${q.color}"></span><div><b>${q.seq}. ${escapeHTML(q.display_name||q.name)}</b><small>${(q.l/1000).toFixed(2)}×${(q.w/1000).toFixed(2)}×${(q.h/1000).toFixed(2)}m · ${q.weight_kg}kg · ${escapeHTML(q.type)}${q.display_shape==='upright_cylinder'?' · 圆桶立放（虚线为计算包络）':q.display_shape==='horizontal_cylinder'?' · 圆柱卧放（虚线为计算包络）':''}</small></div>`;r.onclick=()=>{vstate.selected=q.seq;render();};grid.appendChild(r);});(box.flexible_fill||[]).forEach(a=>{const r=document.createElement('div');r.className='legend-item';const strong=document.createElement('b'),small=document.createElement('small');strong.textContent=a.name;small.textContent=`${a.quantity}包 · ${a.weight_kg}kg · ${a.volume_m3}m³；按原表每m³8包，按装载步骤填装；本项目顶部许可见确认条件；不可达区不计入；参考袋尺寸不作为硬圆柱排布。`;r.append(strong,small);grid.appendChild(r);});leg.appendChild(grid);
  const q=box.placements.find(x=>x.seq===vstate.selected),sel=document.getElementById('selectedCargo'),coord=document.getElementById('coordinateBox'),status=document.getElementById('layoutStatus');
  if(q){sel.innerHTML=`<b>${q.seq}. ${escapeHTML(q.display_name||q.name)}</b><small>${escapeHTML(q.type)} · ${q.weight_kg} kg</small>`;coord.textContent=`X=${q.x} / Y=${q.y} / Z=${q.z} mm`;}else{sel.textContent='点击3D图中的货物或图例';coord.textContent='位置：—';}
  const prob=validate(plan,box),cg=containerCg(box),warn=[],high=[],thr=cgThresholds();
  const hoff=isTruck&&pa.truck_axle_loads?cg.oy:Math.max(cg.ox,cg.oy);
  if(hoff>thr.hHigh)high.push(`水平重心偏移 ${hoff.toFixed(1)}% > ${thr.hHigh}%：高风险`);
  else if(hoff>thr.hReview)warn.push(`水平重心偏移 ΔX ${cg.ox.toFixed(1)}% / ΔY ${cg.oy.toFixed(1)}%，需复核`);
  if(cg.z>thr.zHigh)high.push(`垂直重心高度 ${cg.z.toFixed(1)}% > ${thr.zHigh}%：高风险`);
  else if(cg.z>thr.zReview)warn.push(`垂直重心高度 ${cg.z.toFixed(1)}% > ${thr.zReview}%：需复核`);
  const unknownStack=box.placements.filter(a=>a.z>1e-6).some(a=>box.placements.some(b=>b!==a&&String(b.type||'')==='木箱'&&b.max_top_load_kg==null&&Math.abs((b.z+b.h)-a.z)<=1e-6&&overlapXY(a,b)>0));
  if(unknownStack)warn.push('存在木箱叠放，但下层木箱没有经确认的顶载数据，需结构复核');
  if(String(box.container_code||'').includes('OT')){const oh=Math.max(0,...box.placements.map(x=>x.z+x.h-H));if(oh>0)warn.push(`OT顶部超高 ${oh.toFixed(0)} mm：按OOG确认运输与绑扎`);}
  if(isCurrentLayoutDirty())warn.unshift('当前移动已通过后端校核，坐标尚未保存；正式导出前仍须“保存并后端复核”。');
  if(box.server_dirty_draft&&!isCurrentLayoutDirty())warn.unshift(`服务器记录该柜仍有未保存草稿${box.server_dirty_since?'（'+box.server_dirty_since+'）':''}；请重新保存或恢复自动排布后再导出。`);
  if(box.manual_saved_invalid)high.unshift(`已保存手调方案已失效：${box.manual_invalid_reason||'当前BOM/自动方案已变化'}。系统当前显示自动排布，仅供恢复，不允许静默正式导出。`);
  (pa.engineering_review_notes||[]).forEach(x=>{if(!warn.includes(x))warn.push(`工程复核：${x}`)});
  const info=(pa.information_notes||[]).map(x=>`信息：${x}`);
  const msgs=[...high,...warn,...info];
  status.className='layout-status '+(prob.length?'bad':(high.length?'bad':(warn.length?'warn':'ok')));
  status.innerHTML=prob.length?prob.map(escapeHTML).join('<br>'):(msgs.length?msgs.map(escapeHTML).join('<br>'):`静态规划校核通过：CG X ${cg.x.toFixed(1)}% / Y ${cg.y.toFixed(1)}% / Z ${cg.z.toFixed(1)}%；无碰撞/越界/支撑异常。${box.manual_saved?' 已保存为数据库权威方案。':''}`);
}
let movePending=false;
async function move(axis,dir){
  const p=curPlan(),b=curBox();if(movePending)return;
  if(!p||!b||vstate.selected==null){alert('请先选择货物');return;}
  const q=b.placements.find(x=>x.seq===vstate.selected),old=q[axis],step=Number(document.getElementById('moveStep').value);
  q[axis]+=dir*step;movePending=true;
  const controls=[...document.querySelectorAll('[data-axis],#schemeSelect,#containerSelect,#saveLayoutBtn,#exportSvgBtn,#exportLiftingBtn,#exportLayoutWordBtn')];controls.forEach(e=>e.disabled=true);
  const status=document.getElementById('layoutStatus');status.textContent='后端校核中…';
  try{
    const result=await fetch('/container-layout/validate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scheme:p.code,container_index:b.index,deep:new URLSearchParams(location.search).get('deep')==='1',placements:b.placements.map(v=>({seq:v.seq,name:v.name,x:v.x,y:v.y,z:v.z}))})});
    const response=await result.json();if(!result.ok||!response.ok)throw new Error(response.error||response.audit?.note||'后端静态校核不通过');
    b.engineering_audit=response.audit;b.manual_saved=false;markLayoutDirty();render();
  }catch(error){q[axis]=old;render();status.className='layout-status bad';status.textContent='移动已撤销：'+error.message;}
  finally{movePending=false;controls.forEach(e=>e.disabled=false);}
}
async function downloadDiagram(kind){
  if(movePending||isCurrentLayoutDirty()){alert('请先保存并后端复核当前布局');return;}
  const p=curPlan(),b=curBox();if(!p||!b)return;
  try{await window.packingDownloadPost('/export-container-svg',{scheme:p.code,container_index:b.index,deep:new URLSearchParams(location.search).get('deep')==='1',mode:document.getElementById('exportMode')?.value||'planning',kind,view:vstate.view,lifting:{...(window.LiftingPreview?.settings()||{}),enabled:window.LiftingPreview?.enabled()||false}});}
  catch(error){alert(error.message);}
}
async function downloadLayoutWord(){
  const plan=curPlan(),button=document.getElementById('exportLayoutWordBtn');if(!plan||!button)return;
  if(movePending||[...vstate.dirtyKeys].some(k=>k.startsWith(plan.code+':'))){alert('当前方案有未保存的位置调整，请先保存并后端复核');return;}
  const old=button.textContent;button.disabled=true;button.textContent='正在生成Word…';button.setAttribute('aria-busy','true');
  try{await window.packingDownloadPost('/export-container-layout-word',{scheme:plan.code,deep:new URLSearchParams(location.search).get('deep')==='1',mode:document.getElementById('exportMode')?.value||'planning'});}
  catch(error){alert(error.message);}
  finally{button.disabled=false;button.textContent=old;button.removeAttribute('aria-busy');}
}
(function(){
  let d={};try{d=JSON.parse(document.getElementById('layout-data')?.textContent||'{}')}catch(e){}
  vstate.original=cloneObj(d);vstate.data=cloneObj(d);
  const ss=document.getElementById('schemeSelect'),cs=document.getElementById('containerSelect'),resetBtn=document.getElementById('resetLayoutBtn'),saveBtn=document.getElementById('saveLayoutBtn'),exportBtn=document.getElementById('exportSvgBtn'),wordBtn=document.getElementById('exportLayoutWordBtn'),canvas=document.getElementById('viewerCanvas'),viewResetBtn=document.getElementById('containerViewReset');
  if(!ss||!cs)return;
  function updateViewButtons(view){document.querySelectorAll('[data-container-view]').forEach(b=>b.classList.toggle('active',b.dataset.containerView===view));}
  function resetContainerView(){setContainerCameraPreset('perspective');updateViewButtons('perspective');}
  if(!Object.keys(d).length){ss.disabled=true;cs.disabled=true;[resetBtn,saveBtn,exportBtn,wordBtn,viewResetBtn,...document.querySelectorAll('[data-container-view]')].forEach(b=>{if(b){b.disabled=true;b.title='请先成功导入材料并生成集装箱方案';}});ss.innerHTML='<option>暂无方案</option>';cs.innerHTML='<option>暂无柜</option>';return;}
  ss.innerHTML=Object.keys(d).map(c=>`<option value="${escapeHTML(c)}">${escapeHTML(d[c].label||c)}（${d[c].count}柜）</option>`).join('');
  function refresh(){const p=curPlan();cs.innerHTML=p.containers.map(b=>`<option value="${b.index}">第${b.index}${b.vehicle_type==='truck'?'辆':'柜'} · ${escapeHTML(b.container_code)}${(b.placements.length||b.flexible_fill?.length)?'':(b.vehicle_type==='truck'?'（空车位）':'（空柜）')}</option>`).join('');vstate.selected=null;resetContainerView();render();}
  ss.onchange=refresh;cs.onchange=()=>{vstate.selected=null;resetContainerView();render();};
  document.querySelectorAll('[data-axis]').forEach(x=>x.onclick=()=>move(x.dataset.axis,Number(x.dataset.dir)));
  document.querySelectorAll('[data-container-view]').forEach(b=>b.addEventListener('click',()=>{setContainerCameraPreset(b.dataset.containerView);updateViewButtons(b.dataset.containerView);render();}));
  viewResetBtn?.addEventListener('click',()=>{resetContainerView();render();});
  ['showLifting','liftingAngle','liftingSetback','liftingMinWeight'].forEach(id=>document.getElementById(id)?.addEventListener('change',()=>{
    const status=document.getElementById('liftingInputStatus');if(status)status.textContent='';
    try{LiftingPreview.buildModel(curBox(),LiftingPreview.settings());render();}
    catch(e){if(status)status.textContent=e.message;}
  }));
  document.getElementById('exportLiftingBtn')?.addEventListener('click',()=>downloadDiagram('lifting'));

  let orbitDrag=null,orbitRaf=0;
  function scheduleContainerRender(){if(orbitRaf)return;orbitRaf=requestAnimationFrame(()=>{orbitRaf=0;render();});}
  canvas?.addEventListener('contextmenu',e=>e.preventDefault());
  canvas?.addEventListener('pointerdown',e=>{
    if(e.button!==0&&e.button!==2)return;
    orbitDrag={id:e.pointerId,x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,pan:e.button===2||e.shiftKey,moved:false};
    try{canvas.setPointerCapture(e.pointerId);}catch(_e){}
    canvas.classList.add('orbit-dragging');
  });
  canvas?.addEventListener('pointermove',e=>{
    if(!orbitDrag||orbitDrag.id!==e.pointerId)return;
    const dx=e.clientX-orbitDrag.x,dy=e.clientY-orbitDrag.y;orbitDrag.x=e.clientX;orbitDrag.y=e.clientY;
    if(Math.hypot(e.clientX-orbitDrag.startX,e.clientY-orbitDrag.startY)>3)orbitDrag.moved=true;
    if(orbitDrag.pan){const rx=1080/Math.max(320,canvas.clientWidth),ry=600/Math.max(260,canvas.clientHeight);vstate.camera.panX+=dx*rx;vstate.camera.panY+=dy*ry;}
    else{vstate.camera.yaw+=dx*.008;vstate.camera.pitch=Math.max(-89*CONTAINER_DEG,Math.min(89*CONTAINER_DEG,vstate.camera.pitch+dy*.006));markContainerFreeView();}
    scheduleContainerRender();e.preventDefault();
  });
  function endContainerOrbit(e){if(!orbitDrag||orbitDrag.id!==e.pointerId)return;if(orbitDrag.moved)vstate.suppressClickUntil=Date.now()+180;orbitDrag=null;canvas.classList.remove('orbit-dragging');try{canvas.releasePointerCapture(e.pointerId);}catch(_e){}}
  canvas?.addEventListener('pointerup',endContainerOrbit);canvas?.addEventListener('pointercancel',endContainerOrbit);
  canvas?.addEventListener('wheel',e=>{vstate.camera.zoom=Math.max(.35,Math.min(4,(vstate.camera.zoom||1)*Math.exp(-e.deltaY*.0012)));scheduleContainerRender();e.preventDefault();},{passive:false});
  canvas?.addEventListener('dblclick',()=>{resetContainerView();render();});
  saveBtn.onclick=async()=>{const p=curPlan(),b=curBox();if(!p||!b)return;saveBtn.disabled=true;const oldText=saveBtn.textContent;saveBtn.textContent='后端静态规划校核中…';try{const deep=new URLSearchParams(window.location.search).get('deep')==='1'?1:0;const payload={scheme:p.code,container_index:b.index,deep,placements:(b.placements||[]).map(q=>({seq:q.seq,name:q.name,x:q.x,y:q.y,z:q.z}))};const r=await fetch('/container-layout/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const j=await r.json();if(!r.ok||!j.ok)throw new Error(j.error||'保存失败');const i=p.containers.findIndex(x=>x.index===b.index);p.containers[i]=j.box;clearLayoutDirty();vstate.selected=null;render();alert(`${j.message}\n静态规划校核状态：${j.audit?.status||'—'}${j.audit?.note?'\n'+j.audit.note:''}`);}catch(e){alert(String(e.message||e));}finally{saveBtn.disabled=false;saveBtn.textContent=oldText;}};
  resetBtn.onclick=async()=>{const p=curPlan(),b=curBox();if(!p||!b)return;if(!confirm('恢复该柜的系统自动排布，并删除数据库中已保存的手动微调方案？'))return;try{const deep=new URLSearchParams(window.location.search).get('deep')==='1'?1:0;const r=await fetch('/container-layout/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scheme:p.code,container_index:b.index,deep})});const j=await r.json();if(!r.ok||!j.ok)throw new Error(j.error||'恢复失败');const i=p.containers.findIndex(x=>x.index===b.index);if(j.box){p.containers[i]=j.box;const oi=vstate.original[p.code].containers.findIndex(x=>x.index===b.index);if(oi>=0)vstate.original[p.code].containers[oi]=cloneObj(j.box);}clearLayoutDirty();vstate.selected=null;render();alert(j.message);}catch(e){alert(String(e.message||e));}};
  exportBtn.onclick=()=>downloadDiagram('container');
  if(wordBtn)wordBtn.onclick=downloadLayoutWord;
  refresh();
})();

// V2.4: crate engineering audit + internal 3D verification viewer.
(function initCrate3DViewer(){
  const sel=document.getElementById('crate3dSelect');
  const canvas=document.getElementById('crate3dCanvas');
  const summary=document.getElementById('crate3dSummary');
  const legend=document.getElementById('crate3dLegend');
  const auditDetail=document.getElementById('crateAuditDetail');
  const src=document.getElementById('crate-layout-data');
  if(!sel||!canvas||!summary||!legend||!src) return;
  let data={}; try{data=JSON.parse(src.textContent||'{}')}catch(e){data={}}
  const names=Object.keys(data);
  if(!names.length){sel.innerHTML='<option>暂无木箱3D数据</option>';sel.disabled=true;return;}
  sel.innerHTML=names.map(n=>`<option value="${escapeHTML(n)}">${escapeHTML(n)}</option>`).join('');
  function renderCrate(){
    const plan=data[sel.value]; if(!plan) return;
    const box=plan.containers[0];
    const L=box.inner_l,W=box.inner_w,H=box.inner_h;
    const s=Math.min(720/(L+.55*W),330/(H+.28*W)),ox=90,oy=420;
    const svg=svgEl('svg',{viewBox:'0 0 980 500',class:'svg-3d'}),P=(x,y,z)=>isoPoint(x,y,z,s,ox,oy);
    const p000=P(0,0,0),p100=P(L,0,0),p010=P(0,W,0),p110=P(L,W,0),p001=P(0,0,H),p101=P(L,0,H),p011=P(0,W,H),p111=P(L,W,H);
    svg.appendChild(svgEl('polygon',{points:pts([p000,p100,p110,p010]),fill:'#f8fafc',stroke:'#cbd5e1'}));
    [[p000,p100],[p100,p110],[p110,p010],[p010,p000],[p001,p101],[p101,p111],[p111,p011],[p011,p001],[p000,p001],[p100,p101],[p010,p011],[p110,p111]].forEach(([a,b])=>svg.appendChild(svgEl('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'#64748b','stroke-width':'1.2'})));
    box.placements.forEach(q=>{
      const a=P(q.x,q.y,q.z),b=P(q.x+q.l,q.y,q.z),c=P(q.x+q.l,q.y+q.w,q.z),a2=P(q.x,q.y,q.z+q.h),b2=P(q.x+q.l,q.y,q.z+q.h),c2=P(q.x+q.l,q.y+q.w,q.z+q.h),d2=P(q.x,q.y+q.w,q.z+q.h);
      svg.appendChild(svgEl('polygon',{points:pts([a,b,b2,a2]),fill:shade(q.color,-8),stroke:'#334155'}));
      svg.appendChild(svgEl('polygon',{points:pts([b,c,c2,b2]),fill:shade(q.color,-18),stroke:'#334155'}));
      svg.appendChild(svgEl('polygon',{points:pts([a2,b2,c2,d2]),fill:shade(q.color,14),stroke:'#334155'}));
      const t=svgEl('text',{x:(a2.x+b2.x+c2.x+d2.x)/4,y:(a2.y+b2.y+c2.y+d2.y)/4,'text-anchor':'middle','font-size':'11','font-weight':'700'});t.textContent=q.seq;svg.appendChild(t);
    });
    const ainfo=plan.audit;
    if(ainfo && ainfo.cg_x_pct!=null){
      const cg=P(L*ainfo.cg_x_pct/100,W*ainfo.cg_y_pct/100,H*ainfo.cg_z_pct/100);
      svg.appendChild(svgEl('circle',{cx:cg.x,cy:cg.y,r:8,fill:'#dc2626',stroke:'#ffffff','stroke-width':'3'}));
      const cgt=svgEl('text',{x:cg.x+12,y:cg.y-10,'font-size':'12','font-weight':'800',fill:'#b91c1c'});cgt.textContent='CG';svg.appendChild(cgt);
    }
    canvas.innerHTML='';canvas.appendChild(svg);appendEvidenceWatermark(canvas);
    const a=plan.audit||{};
    summary.innerHTML=`<div class="vs-card"><span>箱号</span><strong>${escapeHTML(plan.code)}</strong></div><div class="vs-card"><span>外形尺寸</span><strong>${(L/1000).toFixed(2)}×${(W/1000).toFixed(2)}×${(H/1000).toFixed(2)} m</strong></div><div class="vs-card"><span>结构净尺寸</span><strong>${a.clear_l??'—'}×${a.clear_w??'—'}×${a.clear_h??'—'} mm</strong></div><div class="vs-card"><span>箱内件数</span><strong>${a.item_count ?? box.placements.length}</strong></div><div class="vs-card"><span>净重 / 毛重</span><strong>${a.cargo_weight ?? '—'} / ${a.gross_weight ?? box.total_weight} kg</strong></div><div class="vs-card"><span>静态规划校核</span><strong class="audit-${a.audit_status==='通过'?'ok':((a.audit_status==='不通过'||a.audit_status==='高风险')?'bad':'warn')}">${escapeHTML(a.audit_status||'无数据')}</strong></div>${evidenceStatusCard()}`;
    if(auditDetail){
      const yn=v=>v===1||v===true?'通过':'不通过';
      const cont=a.can_continue===1||a.can_continue===true?'理论可继续':(a.can_continue===0||a.can_continue===false?'不建议/不能继续':'无下一件候选');
      auditDetail.innerHTML=`
        <div class="audit-grid">
          <div><span>最大单件</span><b>${escapeHTML(a.max_item_name||'—')}</b><small>${escapeHTML(a.max_item_dims||'—')} mm</small></div>
          <div><span>规划占位利用率</span><b>${a.planning_util ?? '—'}%</b><small>含管件间隙后的占位</small></div>
          <div><span>重心位置</span><b>X ${a.cg_x_pct ?? '—'}% / Y ${a.cg_y_pct ?? '—'}%</b><small>Z ${a.cg_z_pct ?? '—'}%；红点 CG</small></div>
          <div><span>水平偏心</span><b>ΔX ${a.offset_x_pct ?? '—'}% / ΔY ${a.offset_y_pct ?? '—'}%</b><small>距箱体中心的比例偏移</small></div>
          <div><span>几何稳定比</span><b>X ${a.stability_x_ratio ?? '—'} / Y ${a.stability_y_ratio ?? '—'}</b><small>重心到支承边缘距离 ÷ 重心高度，仅作规划参考</small></div>
          <div><span>碰撞 / 边界 / 支撑 / 毛重 / 结构空间</span><b>${yn(a.collision_ok)} / ${yn(a.boundary_ok)} / ${yn(a.support_ok)} / ${yn(a.gross_ok)} / ${yn(a.geometry_ok)}</b><small>${escapeHTML(a.structure_class||'—')}结构；五项闭环硬校核</small></div>
          <div><span>还能继续装？</span><b>${cont}</b><small>代表性下一件：${escapeHTML(a.next_candidate||'—')}</small></div>
        </div>
        <div class="audit-reason"><b>停止装箱依据：</b>${escapeHTML(a.stop_reason||'—')}</div>
        <div class="audit-conclusion ${a.audit_status==='通过'?'ok':((a.audit_status==='不通过'||a.audit_status==='高风险')?'bad':'warn')}"><b>静态规划校核结论：</b>${escapeHTML(a.conclusion||'—')}</div>`;
    }
  const grid=document.createElement('div');grid.className='legend-grid';
    box.placements.forEach(q=>{const r=document.createElement('div');r.className='legend-item';r.innerHTML=`<span class="legend-color" style="background:${q.color}"></span><div><b>${q.seq}. ${escapeHTML(q.name)}</b><small>${escapeHTML(q.type)} · 实际包络 ${(q.actual_l/1000).toFixed(2)}×${(q.actual_w/1000).toFixed(2)}×${(q.actual_h/1000).toFixed(2)}m · ${q.weight_kg}kg</small></div>`;grid.appendChild(r);});
    legend.innerHTML='';legend.appendChild(grid);
  }
  sel.onchange=renderCrate;renderCrate();
})();




// V4.0.1: shrink action is isolated from crate-card click/modal events.
(function initShrinkActions(){
  document.querySelectorAll('.crate-shrink-form').forEach(form=>{
    form.addEventListener('click',e=>e.stopPropagation());
    form.addEventListener('submit',async e=>{
      e.preventDefault();
      e.stopPropagation();
      const name=form.dataset.crateName||'当前木箱';
      const dims=form.dataset.recommendedDims||'建议尺寸';
      if(!confirm(`将按当前材料重新3D排布，并把 ${name} 缩到 ${dims} mm。继续吗？`)) return;
      const btn=form.querySelector('.crate-shrink-btn');
      if(btn){btn.disabled=true;btn.textContent='正在缩箱…';}
      try{
        const r=await fetch(form.action,{method:'POST',body:new FormData(form),redirect:'follow'});
        if(!r.ok) throw new Error(`HTTP ${r.status}`);
        // The endpoint redirects to /?message=... or /?error=... . Use final URL so user sees the result.
        window.location.href=r.url || '/';
      }catch(err){
        alert('缩箱执行失败：'+(err?.message||err));
        if(btn){btn.disabled=false;btn.textContent='应用建议缩箱';}
      }
    });
  });
})();
// V2.8: advanced crate visualization — richer SVG 3D, multiple views, selection, export.
(function initCrateModalV28(){
  const modal=document.getElementById('crateModal'); if(!modal) return;
  const title=document.getElementById('crateModalTitle'),sub=document.getElementById('crateModalSub'),listBody=document.getElementById('crateModalListBody'),summary=document.getElementById('crateModalSummary'),canvas=document.getElementById('crateModalCanvas'),auditBox=document.getElementById('crateModalAudit'),legend=document.getElementById('crateModalLegend'),selectedInfo=document.getElementById('crateSelectedInfo');
  const showLabels=document.getElementById('crateShowLabels'),showShell=document.getElementById('crateShowShell');
  let crateData={}; try{crateData=JSON.parse(document.getElementById('crate-layout-data')?.textContent||'{}')}catch(e){}
  const rows=Array.from(document.querySelectorAll('#itemSourceTable tbody tr.item-row'));
  const DEG=Math.PI/180;
  const CAMERA_PRESETS={
    perspective:{yaw:-29*DEG,pitch:-16*DEG,zoom:1,panX:0,panY:0},
    top:{yaw:0,pitch:-90*DEG,zoom:1,panX:0,panY:0},
    front:{yaw:0,pitch:0,zoom:1,panX:0,panY:0},
    side:{yaw:-90*DEG,pitch:0,zoom:1,panX:0,panY:0}
  };
  const state={crate:null,view:'perspective',selected:null,lastSvg:null,camera:{...CAMERA_PRESETS.perspective},suppressClickUntil:0};
  const palette=['#2563eb','#0ea5e9','#14b8a6','#10b981','#84cc16','#f59e0b','#f97316','#ef4444','#ec4899','#a855f7','#7c3aed','#4f46e5'];
  function setCameraPreset(view){state.view=view;state.camera={...(CAMERA_PRESETS[view]||CAMERA_PRESETS.perspective)};}
  function markFreeView(){state.view='perspective';document.querySelectorAll('[data-crate-view]').forEach(b=>b.classList.toggle('active',b.dataset.crateView==='perspective'));}
  function cargoClick(q){if(Date.now()<state.suppressClickUntil)return;state.selected=q.seq;render3d(state.crate);}
  function items(crate){return rows.filter(r=>(r.dataset.crateName||'')===crate).map(r=>({name:r.dataset.itemName||'',material:r.dataset.material||'—',qty:Number(r.dataset.qty||0),size:r.dataset.size||'',sizeGrade:r.dataset.sizeGrade||'C级',sizeSource:r.dataset.sizeSource||'规划尺寸',sizeNote:r.dataset.sizeNote||'',unitWeight:r.dataset.unitWeight||'',grade:r.dataset.weightGrade||'',source:r.dataset.weightSource||'',note:r.dataset.weightNote||''}));}
  function yn(v){return(v===1||v===true)?'通过':'不通过';}
  function colorFor(q,i){return palette[(Number(q.seq||i+1)-1)%palette.length];}
  function projectFactory(view,L,W,H){
    const pad=72,vw=1080,vh=600,cx=L/2,cy=W/2,cz=H/2;
    const yaw=state.camera.yaw,pitch=state.camera.pitch,cyaw=Math.cos(yaw),syaw=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);
    function raw(x,y,z){
      const dx=x-cx,dy=y-cy,dz=z-cz;
      const x1=dx*cyaw-dy*syaw;
      const y1=dx*syaw+dy*cyaw;
      const y2=y1*cp-dz*sp;
      const z2=y1*sp+dz*cp;
      return {u:x1,v:-z2,d:y2};
    }
    const corners=[[0,0,0],[L,0,0],[0,W,0],[L,W,0],[0,0,fitH],[L,0,fitH],[0,W,fitH],[L,W,fitH]].map(c=>raw(...c));
    const us=corners.map(p=>p.u),vs=corners.map(p=>p.v),minU=Math.min(...us),maxU=Math.max(...us),minV=Math.min(...vs),maxV=Math.max(...vs);
    const spanU=Math.max(1,maxU-minU),spanV=Math.max(1,maxV-minV);
    const fit=Math.min((vw-2*pad)/spanU,(vh-2*pad)/spanV);
    const s=fit*Math.max(.35,Math.min(4,state.camera.zoom||1));
    const midU=(minU+maxU)/2,midV=(minV+maxV)/2;
    const ox=vw/2-midU*s+(state.camera.panX||0),oy=vh/2-midV*s+(state.camera.panY||0);
    const P=(x,y,z)=>{const r=raw(x,y,z);return {x:ox+r.u*s,y:oy+r.v*s};};
    const pointDepth=(x,y,z)=>raw(x,y,z).d;
    const depth=q=>pointDepth(q.x+q.l/2,q.y+q.w/2,q.z+q.h/2);
    return {P,s,depth,pointDepth};
  }
  function addDefs(svg){
    const defs=svgEl('defs');
    const shadow=svgEl('filter',{id:'cargoShadow',x:'-30%',y:'-30%',width:'160%',height:'180%'}); shadow.innerHTML='<feDropShadow dx="4" dy="8" stdDeviation="6" flood-color="#0f172a" flood-opacity="0.20"/>';defs.appendChild(shadow);
    const soft=svgEl('filter',{id:'softShadow',x:'-30%',y:'-30%',width:'160%',height:'180%'});soft.innerHTML='<feDropShadow dx="2" dy="5" stdDeviation="4" flood-color="#0f172a" flood-opacity="0.14"/>';defs.appendChild(soft);
    const floor=svgEl('linearGradient',{id:'floorV28',x1:'0%',y1:'0%',x2:'100%',y2:'100%'});floor.innerHTML='<stop offset="0%" stop-color="#f8fbff"/><stop offset="100%" stop-color="#dbeafe"/>';defs.appendChild(floor);
    svg.appendChild(defs);
  }
  function drawGrid(svg,P,L,W,step){
    const g=svgEl('g',{'class':'floor-grid'}), nL=Math.ceil(L/step), nW=Math.ceil(W/step);
    for(let i=0;i<=nL;i++){const x=Math.min(i*step,L),a=P(x,0,0),b=P(x,W,0);g.appendChild(svgEl('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'#94a3b8','stroke-opacity':'.22','stroke-width':'1'}));}
    for(let j=0;j<=nW;j++){const y=Math.min(j*step,W),a=P(0,y,0),b=P(L,y,0);g.appendChild(svgEl('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'#94a3b8','stroke-opacity':'.22','stroke-width':'1'}));}
    svg.appendChild(g);
  }

  function fittingKind(q){
    const n=`${q.name||''} ${q.type||''} ${q.category||''} ${q.fitting_category||''}`;
    const dims=[Number(q.actual_l||q.l||0),Number(q.actual_w||q.w||0),Number(q.actual_h||q.h||0)].sort((a,b)=>b-a);
    const slender=dims[0]>0 && dims[0]/Math.max(1,dims[1])>=4 && Math.max(dims[1],dims[2])<=900;
    if(/裸装扎捆|扎捆|BND-/.test(n)) return 'bundle';
    if(/三通/.test(n)) return 'tee';
    if(/弯头|弯管/.test(n)) return 'elbow';
    if(/异径|大小头|缩径/.test(n)) return 'reducer';
    if(/管帽|封头/.test(n)) return 'cap';
    if(/直管|短节|管段|管道|无缝管|焊管|长料/.test(n) || slender) return 'pipe';
    return 'box';
  }
  function screenBox(q,P){
    const pts8=[P(q.x,q.y,q.z),P(q.x+q.l,q.y,q.z),P(q.x,q.y+q.w,q.z),P(q.x+q.l,q.y+q.w,q.z),P(q.x,q.y,q.z+q.h),P(q.x+q.l,q.y,q.z+q.h),P(q.x,q.y+q.w,q.z+q.h),P(q.x+q.l,q.y+q.w,q.z+q.h)];
    const xs=pts8.map(p=>p.x), ys=pts8.map(p=>p.y); return {x0:Math.min(...xs),x1:Math.max(...xs),y0:Math.min(...ys),y1:Math.max(...ys),cx:(Math.min(...xs)+Math.max(...xs))/2,cy:(Math.min(...ys)+Math.max(...ys))/2,w:Math.max(...xs)-Math.min(...xs),h:Math.max(...ys)-Math.min(...ys)};
  }
  function drawFittingGlyph(svg,q,P,color,selected,labels){
    const k=fittingKind(q); if(k==='box') return false;
    const b=screenBox(q,P), g=svgEl('g',{'class':`v29-fitting ${k}${selected?' selected':''}`,'data-seq':q.seq,filter:'url(#cargoShadow)'});
    const sw=Math.max(7,Math.min(b.w,b.h)*.22), edge=selected?'#0f172a':'#334155';
    // faint computational envelope: keeps relationship to the packing model visible
    g.appendChild(svgEl('rect',{x:b.x0+2,y:b.y0+2,width:Math.max(4,b.w-4),height:Math.max(4,b.h-4),rx:6,fill:'none',stroke:edge,'stroke-width':selected?2.4:1,'stroke-dasharray':'5 4','stroke-opacity':'.28'}));
    const addPipe=(d,width=sw)=>{g.appendChild(svgEl('path',{d,fill:'none',stroke:edge,'stroke-width':width+4,'stroke-linecap':'round','stroke-linejoin':'round'}));g.appendChild(svgEl('path',{d,fill:'none',stroke:color,'stroke-width':width,'stroke-linecap':'round','stroke-linejoin':'round'}));g.appendChild(svgEl('path',{d,fill:'none',stroke:shade(color,28),'stroke-width':Math.max(2,width*.24),'stroke-linecap':'round','stroke-opacity':'.75'}));};
    if(k==='bundle'){
      const x0=b.x0+b.w*.08,x1=b.x1-b.w*.08,y0=b.y0+b.h*.18,y1=b.y1-b.h*.18,rows=3,cols=4,dx=(x1-x0)/Math.max(1,cols-1),dy=(y1-y0)/Math.max(1,rows-1);
      for(let rr=0;rr<rows;rr++)for(let cc=0;cc<cols;cc++){const cx=x0+cc*dx,cy=y0+rr*dy;g.appendChild(svgEl('ellipse',{cx,cy,rx:Math.max(4,sw*.28),ry:Math.max(3,sw*.15),fill:shade(color,10),stroke:edge,'stroke-width':'1'}));if(cc<cols-1)g.appendChild(svgEl('line',{x1:cx,y1:cy,x2:cx+dx*.78,y2:cy,stroke:color,'stroke-width':Math.max(4,sw*.34),'stroke-linecap':'round'}));}
      [b.x0+b.w*.32,b.x0+b.w*.68].forEach(x=>g.appendChild(svgEl('line',{x1:x,y1:b.y0+b.h*.08,x2:x,y2:b.y1-b.h*.08,stroke:'#475569','stroke-width':Math.max(3,sw*.12)})));
      g.appendChild(svgEl('line',{x1:b.x0+b.w*.08,y1:b.y1-b.h*.08,x2:b.x1-b.w*.08,y2:b.y1-b.h*.08,stroke:'#92400e','stroke-width':Math.max(5,sw*.18),'stroke-linecap':'round'}));
    }else if(k==='tee'){
      addPipe(`M ${b.x0+b.w*.13} ${b.cy+b.h*.08} L ${b.x1-b.w*.13} ${b.cy+b.h*.08}`);
      addPipe(`M ${b.cx} ${b.cy+b.h*.08} L ${b.cx} ${b.y0+b.h*.10}`);
      [ [b.x0+b.w*.13,b.cy+b.h*.08],[b.x1-b.w*.13,b.cy+b.h*.08],[b.cx,b.y0+b.h*.10] ].forEach(([cx,cy])=>{g.appendChild(svgEl('ellipse',{cx,cy,rx:sw*.52,ry:sw*.22,fill:shade(color,18),stroke:edge,'stroke-width':'1'}));});
    }else if(k==='elbow'){
      const x0=b.x0+b.w*.16,y0=b.y1-b.h*.18,x1=b.x1-b.w*.17,y1=b.y0+b.h*.18;
      addPipe(`M ${x0} ${y0} Q ${x1} ${y0} ${x1} ${y1}`);
      g.appendChild(svgEl('ellipse',{cx:x0,cy:y0,rx:sw*.5,ry:sw*.22,fill:shade(color,18),stroke:edge}));g.appendChild(svgEl('ellipse',{cx:x1,cy:y1,rx:sw*.5,ry:sw*.22,fill:shade(color,18),stroke:edge}));
    }else if(k==='reducer'){
      const x0=b.x0+b.w*.12,x1=b.x1-b.w*.12,cy=b.cy, r1=Math.max(5,b.h*.34),r2=Math.max(4,b.h*.20);
      g.appendChild(svgEl('path',{d:`M ${x0} ${cy-r1} L ${x1} ${cy-r2} L ${x1} ${cy+r2} L ${x0} ${cy+r1} Z`,fill:color,stroke:edge,'stroke-width':selected?3:1.3}));
      g.appendChild(svgEl('ellipse',{cx:x0,cy,rx:r1*.42,ry:r1,fill:shade(color,16),stroke:edge}));g.appendChild(svgEl('ellipse',{cx:x1,cy,rx:r2*.42,ry:r2,fill:shade(color,-14),stroke:edge}));
    }else if(k==='cap'){
      const x=b.x0+b.w*.18,y=b.y0+b.h*.18,w=b.w*.64,h=b.h*.64;
      g.appendChild(svgEl('path',{d:`M ${x} ${y+h} L ${x} ${y+h*.45} Q ${x+w*.5} ${y-h*.10} ${x+w} ${y+h*.45} L ${x+w} ${y+h} Z`,fill:color,stroke:edge,'stroke-width':selected?3:1.3}));
      g.appendChild(svgEl('ellipse',{cx:x+w/2,cy:y+h,rx:w/2,ry:Math.max(4,h*.12),fill:shade(color,-10),stroke:edge}));
    }else if(k==='pipe'){
      addPipe(`M ${b.x0+b.w*.12} ${b.cy} L ${b.x1-b.w*.12} ${b.cy}`,Math.max(sw,b.h*.45));
    }
    if(labels){const t=svgEl('text',{x:b.cx,y:b.y0+14,'text-anchor':'middle','font-size':'13','font-weight':'900',fill:'#0f172a','paint-order':'stroke',stroke:'#fff','stroke-width':'4'});t.textContent=q.seq;g.appendChild(t);}
    g.onclick=()=>cargoClick(q); svg.appendChild(g); return true;
  }

  function drawPerspectiveBox(svg,q,P,color,selected,labels){
    if(drawFittingGlyph(svg,q,P,color,selected,labels)) return;
    const A=P(q.x,q.y,q.z),B=P(q.x+q.l,q.y,q.z),C=P(q.x+q.l,q.y+q.w,q.z),D=P(q.x,q.y+q.w,q.z),A2=P(q.x,q.y,q.z+q.h),B2=P(q.x+q.l,q.y,q.z+q.h),C2=P(q.x+q.l,q.y+q.w,q.z+q.h),D2=P(q.x,q.y+q.w,q.z+q.h);
    const g=svgEl('g',{'class':`v28-cargo${selected?' selected':''}`,'data-seq':q.seq,filter:'url(#cargoShadow)'});
    g.appendChild(svgEl('polygon',{points:pts([A,B,B2,A2]),fill:shade(color,-5),stroke:selected?'#0f172a':'#334155','stroke-width':selected?'3':'1.2'}));
    g.appendChild(svgEl('polygon',{points:pts([B,C,C2,B2]),fill:shade(color,-20),stroke:selected?'#0f172a':'#334155','stroke-width':selected?'3':'1.2'}));
    g.appendChild(svgEl('polygon',{points:pts([A2,B2,C2,D2]),fill:shade(color,18),stroke:selected?'#0f172a':'#334155','stroke-width':selected?'3':'1.2'}));
    if(labels){const x=(A2.x+B2.x+C2.x+D2.x)/4,y=(A2.y+B2.y+C2.y+D2.y)/4+4,t=svgEl('text',{x,y,'text-anchor':'middle','font-size':'13','font-weight':'800',fill:'#0f172a','paint-order':'stroke',stroke:'#ffffff','stroke-width':'3','stroke-opacity':'.75'});t.textContent=q.seq;g.appendChild(t);}
    g.onclick=()=>cargoClick(q); svg.appendChild(g);
  }
  function drawFreeBox(svg,q,P,pointDepth,color,selected,labels){
    if(drawFittingGlyph(svg,q,P,color,selected,labels)) return;
    const c=[
      [q.x,q.y,q.z],[q.x+q.l,q.y,q.z],[q.x+q.l,q.y+q.w,q.z],[q.x,q.y+q.w,q.z],
      [q.x,q.y,q.z+q.h],[q.x+q.l,q.y,q.z+q.h],[q.x+q.l,q.y+q.w,q.z+q.h],[q.x,q.y+q.w,q.z+q.h]
    ];
    const pp=c.map(v=>P(...v));
    const faces=[
      {idx:[0,1,2,3],tone:-24},{idx:[4,5,6,7],tone:18},{idx:[0,1,5,4],tone:-5},
      {idx:[1,2,6,5],tone:-20},{idx:[2,3,7,6],tone:-12},{idx:[3,0,4,7],tone:5}
    ].map(f=>({...f,depth:f.idx.reduce((a,i)=>a+pointDepth(...c[i]),0)/4})).sort((a,b)=>a.depth-b.depth);
    const g=svgEl('g',{'class':`v28-cargo free-orbit${selected?' selected':''}`,'data-seq':q.seq,filter:'url(#cargoShadow)'});
    faces.forEach(f=>g.appendChild(svgEl('polygon',{points:pts(f.idx.map(i=>pp[i])),fill:shade(color,f.tone),stroke:selected?'#0f172a':'#334155','stroke-width':selected?'3':'1.05','fill-opacity':'.94'})));
    if(labels){
      const top=[4,5,6,7].map(i=>pp[i]),x=top.reduce((a,p)=>a+p.x,0)/4,y=top.reduce((a,p)=>a+p.y,0)/4+4;
      const t=svgEl('text',{x,y,'text-anchor':'middle','font-size':'13','font-weight':'800',fill:'#0f172a','paint-order':'stroke',stroke:'#ffffff','stroke-width':'3','stroke-opacity':'.8'});t.textContent=q.seq;g.appendChild(t);
    }
    g.onclick=()=>cargoClick(q);svg.appendChild(g);
  }
  function drawOrthoBox(svg,q,P,color,selected,labels,view){
    if(drawFittingGlyph(svg,q,P,color,selected,labels)) return;
    let a,b,c,d;
    if(view==='top'){a=P(q.x,q.y,0);b=P(q.x+q.l,q.y,0);c=P(q.x+q.l,q.y+q.w,0);d=P(q.x,q.y+q.w,0);}
    else if(view==='front'){a=P(q.x,0,q.z);b=P(q.x+q.l,0,q.z);c=P(q.x+q.l,0,q.z+q.h);d=P(q.x,0,q.z+q.h);}
    else {a=P(0,q.y,q.z);b=P(0,q.y+q.w,q.z);c=P(0,q.y+q.w,q.z+q.h);d=P(0,q.y,q.z+q.h);}
    const g=svgEl('g',{'class':`v28-cargo${selected?' selected':''}`,'data-seq':q.seq,filter:'url(#softShadow)'});
    g.appendChild(svgEl('polygon',{points:pts([a,b,c,d]),fill:color,'fill-opacity':'.88',stroke:selected?'#0f172a':'#334155','stroke-width':selected?'3':'1.2'}));
    if(labels){const t=svgEl('text',{x:(a.x+b.x+c.x+d.x)/4,y:(a.y+b.y+c.y+d.y)/4+4,'text-anchor':'middle','font-size':'13','font-weight':'800',fill:'#0f172a','paint-order':'stroke',stroke:'#fff','stroke-width':'3'});t.textContent=q.seq;g.appendChild(t);}
    g.onclick=()=>cargoClick(q);svg.appendChild(g);
  }
  function updateSelected(q){
    if(!selectedInfo) return;
    if(!q){selectedInfo.innerHTML='<div class="selected-empty">点击3D货物查看详情</div>';return;}
    selectedInfo.innerHTML=`<div class="selected-kicker">当前选中 #${q.seq}</div><h3>${escapeHTML(q.name)}</h3><div class="selected-spec"><span>实际包络</span><b>${Math.round(q.actual_l||q.l)} × ${Math.round(q.actual_w||q.w)} × ${Math.round(q.actual_h||q.h)} mm</b></div><div class="selected-spec"><span>规划占位</span><b>${Math.round(q.l)} × ${Math.round(q.w)} × ${Math.round(q.h)} mm</b></div><div class="selected-spec"><span>重量</span><b>${q.weight_kg} kg · ${q.weight_grade||'—'}级</b></div><div class="selected-spec"><span>箱内坐标</span><b>X ${Math.round(q.x)} / Y ${Math.round(q.y)} / Z ${Math.round(q.z)} mm</b></div><div class="selected-spec"><span>类别</span><b>${escapeHTML(q.type||'—')}</b></div>`;
  }
  function render3d(crate){
    state.crate=crate; const plan=crateData[crate];
    if(!plan||!plan.containers?.length){summary.innerHTML='';canvas.innerHTML='<div class="empty">暂无3D排布数据，请先执行一键智能规划。</div>';auditBox.innerHTML='';legend.innerHTML='';return;}
    const box=plan.containers[0],a=plan.audit||{},L=box.inner_l,W=box.inner_w,H=box.inner_h,view=state.view;
    const {P,depth,pointDepth}=projectFactory(view,L,W,H),svg=svgEl('svg',{viewBox:'0 0 1080 600',class:'svg-3d v28-svg',id:'crateAdvancedSvg'});addDefs(svg);
    const p000=P(0,0,0),p100=P(L,0,0),p010=P(0,W,0),p110=P(L,W,0),p001=P(0,0,H),p101=P(L,0,H),p011=P(0,W,H),p111=P(L,W,H);
    svg.appendChild(svgEl('polygon',{points:pts([p000,p100,p110,p010]),fill:'url(#floorV28)',stroke:'#94a3b8','stroke-width':'1.2',filter:'url(#softShadow)'}));
    drawGrid(svg,P,L,W,Math.max(250,Math.round(Math.min(L,W)/5/50)*50));
    if(showShell?.checked){
      [[p000,p100,p101,p001],[p100,p110,p111,p101],[p110,p010,p011,p111],[p010,p000,p001,p011],[p001,p101,p111,p011]].forEach((face,i)=>svg.appendChild(svgEl('polygon',{points:pts(face),fill:i===4?'#e0f2fe':'#bfdbfe','fill-opacity':i===4?'.05':'.055',stroke:'none'})));
    }
    const ordered=[...box.placements].sort((u,v)=>depth(u)-depth(v));
    ordered.forEach((q,i)=>{const col=colorFor(q,i),sel=Number(q.seq)===Number(state.selected),labels=!!showLabels?.checked;drawFreeBox(svg,q,P,pointDepth,col,sel,labels);});
    const edges=[[p000,p100],[p100,p110],[p110,p010],[p010,p000],[p001,p101],[p101,p111],[p111,p011],[p011,p001],[p000,p001],[p100,p101],[p010,p011],[p110,p111]];
    if(showShell?.checked)edges.forEach(([x,y])=>svg.appendChild(svgEl('line',{x1:x.x,y1:x.y,x2:y.x,y2:y.y,stroke:'#475569','stroke-width':'1.6','stroke-opacity':'.85'})));
    if(a.cg_x_pct!=null){const cg=P(L*a.cg_x_pct/100,W*a.cg_y_pct/100,H*a.cg_z_pct/100),ring=svgEl('g',{filter:'url(#softShadow)'});ring.appendChild(svgEl('circle',{cx:cg.x,cy:cg.y,r:12,fill:'#fff',stroke:'#ef4444','stroke-width':'4'}));ring.appendChild(svgEl('circle',{cx:cg.x,cy:cg.y,r:5,fill:'#ef4444'}));const t=svgEl('text',{x:cg.x+16,y:cg.y-13,'font-size':'13','font-weight':'900',fill:'#b91c1c','paint-order':'stroke',stroke:'#fff','stroke-width':'4'});t.textContent='CG';ring.appendChild(t);svg.appendChild(ring);}
    canvas.innerHTML='';canvas.appendChild(svg);appendEvidenceWatermark(canvas);state.lastSvg=svg;
    const selected=box.placements.find(q=>Number(q.seq)===Number(state.selected));updateSelected(selected);
    summary.innerHTML=`<div class="vs-card"><span>箱号</span><strong>${escapeHTML(crate)}</strong></div><div class="vs-card"><span>外形尺寸</span><strong>${(L/1000).toFixed(2)}×${(W/1000).toFixed(2)}×${(H/1000).toFixed(2)} m</strong></div><div class="vs-card"><span>件数</span><strong>${a.item_count??box.placements.length}</strong></div><div class="vs-card"><span>净重/毛重</span><strong>${a.cargo_weight??'—'} / ${a.gross_weight??box.total_weight} kg</strong></div><div class="vs-card"><span>利用率</span><strong>${a.actual_util??box.volume_utilization}%</strong></div><div class="vs-card"><span>静态规划校核</span><strong>${escapeHTML(a.audit_status||'—')}</strong></div>${evidenceStatusCard()}`;
    auditBox.innerHTML=`<div class="audit-grid"><div><span>最大单件</span><b>${escapeHTML(a.max_item_name||'—')}</b><small>${escapeHTML(a.max_item_dims||'—')} mm</small></div><div><span>规划占位率</span><b>${a.planning_util??'—'}%</b><small>碰撞/边界规划</small></div><div><span>异形件几何占用率</span><b>${a.shape_util??'—'}%</b><small>按显示形体比例估算</small></div><div><span>重心</span><b>X ${a.cg_x_pct??'—'}% / Y ${a.cg_y_pct??'—'}%</b><small>Z ${a.cg_z_pct??'—'}%</small></div><div><span>水平偏心</span><b>ΔX ${a.offset_x_pct??'—'}% / ΔY ${a.offset_y_pct??'—'}%</b></div><div><span>几何稳定比</span><b>X ${a.stability_x_ratio??'—'} / Y ${a.stability_y_ratio??'—'}</b><small>仅作规划参考</small></div><div><span>碰撞/边界/毛重</span><b>${yn(a.collision_ok)} / ${yn(a.boundary_ok)} / ${yn(a.gross_ok)}</b></div><div><span>还能继续装？</span><b>${a.can_continue===1?'理论可继续':(a.can_continue===0?'不建议/不能继续':'—')}</b></div></div><div class="audit-reason"><b>停止装箱依据：</b>${escapeHTML(a.stop_reason||'—')}</div><div class="audit-conclusion"><b>静态规划校核结论：</b>${escapeHTML(a.conclusion||'—')}</div>`;
    legend.innerHTML='';const g=document.createElement('div');g.className='legend-grid';box.placements.forEach((q,i)=>{const r=document.createElement('button');r.type='button';r.className=`legend-item v28-legend${Number(q.seq)===Number(state.selected)?' selected':''}`;r.innerHTML=`<span class="legend-color" style="background:${colorFor(q,i)}"></span><div><b>${q.seq}. ${escapeHTML(q.name)}</b><small>${(q.actual_l/1000).toFixed(2)}×${(q.actual_w/1000).toFixed(2)}×${(q.actual_h/1000).toFixed(2)}m · ${q.weight_kg}kg</small></div>`;r.onclick=()=>{state.selected=q.seq;render3d(crate)};g.appendChild(r);});legend.appendChild(g);
  }
  function open(crate){const its=items(crate),total=its.reduce((s,r)=>s+r.qty,0);state.crate=crate;state.selected=null;setCameraPreset('perspective');document.querySelectorAll('[data-crate-view]').forEach(x=>x.classList.toggle('active',x.dataset.crateView==='perspective'));title.textContent=`${crate} 木箱详情`;sub.textContent=`${its.length} 行材料，合计 ${total} 件`;listBody.innerHTML=its.length?its.map(r=>`<tr><td>${escapeHTML(r.name)}</td><td><span class="material-badge">${escapeHTML(r.material||'—')}</span></td><td>${r.qty}</td><td>${escapeHTML(r.size)}</td><td><span class="size-grade grade-${(r.sizeGrade||'C级')[0].toLowerCase()}">${escapeHTML(r.sizeGrade)}</span><div class="size-source">${escapeHTML(r.sizeSource)}</div>${r.sizeNote?`<div class="size-note" title="${escapeHTML(r.sizeNote)}">${escapeHTML(r.sizeNote)}</div>`:''}</td><td><b>${escapeHTML(r.unitWeight)}</b></td><td><span class="weight-grade grade-${(r.grade||'C级')[0].toLowerCase()}">${escapeHTML(r.grade)}</span><div class="weight-source">${escapeHTML(r.source)}</div>${r.note?`<div class="weight-note" title="${escapeHTML(r.note)}">${escapeHTML(r.note)}</div>`:''}</td></tr>`).join(''):'<tr><td colspan="7" class="empty-row">该木箱暂无材料。</td></tr>';render3d(crate);modal.classList.add('open');modal.setAttribute('aria-hidden','false');document.body.classList.add('modal-open');document.querySelectorAll('.crate-tab').forEach((b,i)=>b.classList.toggle('active',i===0));document.getElementById('crateTabList').classList.add('active');document.getElementById('crateTab3d').classList.remove('active');}
  function close(){modal.classList.remove('open');modal.setAttribute('aria-hidden','true');document.body.classList.remove('modal-open');}
  document.querySelectorAll('.crate-view-btn').forEach(b=>b.addEventListener('click',e=>{e.stopPropagation();open(b.dataset.crateName);}));
  document.querySelectorAll('.crate-overview-card').forEach(card=>card.addEventListener('click',e=>{if(e.target.closest('form,button'))return;open(card.dataset.crateName);}));
  document.querySelectorAll('[data-close-crate-modal]').forEach(x=>x.addEventListener('click',close));document.addEventListener('keydown',e=>{if(e.key==='Escape'&&modal.classList.contains('open'))close();});
  document.querySelectorAll('.crate-tab').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('.crate-tab').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.getElementById('crateTabList').classList.toggle('active',b.dataset.tab==='list');document.getElementById('crateTab3d').classList.toggle('active',b.dataset.tab==='3d');if(b.dataset.tab==='3d'&&state.crate)render3d(state.crate);}));
  document.querySelectorAll('[data-crate-view]').forEach(b=>b.addEventListener('click',()=>{setCameraPreset(b.dataset.crateView);document.querySelectorAll('[data-crate-view]').forEach(x=>x.classList.toggle('active',x===b));if(state.crate)render3d(state.crate);}));
  document.getElementById('crateViewReset')?.addEventListener('click',()=>{setCameraPreset('perspective');document.querySelectorAll('[data-crate-view]').forEach(x=>x.classList.toggle('active',x.dataset.crateView==='perspective'));if(state.crate)render3d(state.crate);});
  let orbitDrag=null,orbitRaf=0;
  function scheduleOrbitRender(){if(orbitRaf||!state.crate)return;orbitRaf=requestAnimationFrame(()=>{orbitRaf=0;render3d(state.crate);});}
  canvas.addEventListener('contextmenu',e=>e.preventDefault());
  canvas.addEventListener('pointerdown',e=>{
    if(e.button!==0&&e.button!==2)return;
    orbitDrag={id:e.pointerId,x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,pan:e.button===2||e.shiftKey,moved:false};
    try{canvas.setPointerCapture(e.pointerId);}catch(_e){}
    canvas.classList.add('orbit-dragging');e.preventDefault();
  });
  canvas.addEventListener('pointermove',e=>{
    if(!orbitDrag||orbitDrag.id!==e.pointerId)return;
    const dx=e.clientX-orbitDrag.x,dy=e.clientY-orbitDrag.y;orbitDrag.x=e.clientX;orbitDrag.y=e.clientY;
    if(Math.hypot(e.clientX-orbitDrag.startX,e.clientY-orbitDrag.startY)>3)orbitDrag.moved=true;
    if(orbitDrag.pan){const rx=1080/Math.max(320,canvas.clientWidth),ry=600/Math.max(260,canvas.clientHeight);state.camera.panX+=dx*rx;state.camera.panY+=dy*ry;}
    else{state.camera.yaw+=dx*.008;state.camera.pitch=Math.max(-89*DEG,Math.min(89*DEG,state.camera.pitch+dy*.006));markFreeView();}
    scheduleOrbitRender();e.preventDefault();
  });
  function endOrbit(e){if(!orbitDrag||orbitDrag.id!==e.pointerId)return;if(orbitDrag.moved)state.suppressClickUntil=Date.now()+180;orbitDrag=null;canvas.classList.remove('orbit-dragging');try{canvas.releasePointerCapture(e.pointerId);}catch(_e){}}
  canvas.addEventListener('pointerup',endOrbit);canvas.addEventListener('pointercancel',endOrbit);
  canvas.addEventListener('wheel',e=>{state.camera.zoom=Math.max(.35,Math.min(4,(state.camera.zoom||1)*Math.exp(-e.deltaY*.0012)));markFreeView();scheduleOrbitRender();e.preventDefault();},{passive:false});
  canvas.addEventListener('dblclick',()=>{setCameraPreset('perspective');document.querySelectorAll('[data-crate-view]').forEach(x=>x.classList.toggle('active',x.dataset.crateView==='perspective'));state.crate&&render3d(state.crate);});
  showLabels?.addEventListener('change',()=>state.crate&&render3d(state.crate));showShell?.addEventListener('change',()=>state.crate&&render3d(state.crate));
  function exportSvg(){if(!state.lastSvg)return;const cp=state.lastSvg.cloneNode(true);cp.setAttribute('xmlns','http://www.w3.org/2000/svg');const blob=new Blob([new XMLSerializer().serializeToString(cp)],{type:'image/svg+xml;charset=utf-8'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`${state.crate}_${state.view}_3D.svg`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
  function exportPng(){if(!state.lastSvg)return;const xml=new XMLSerializer().serializeToString(state.lastSvg),blob=new Blob([xml],{type:'image/svg+xml;charset=utf-8'}),url=URL.createObjectURL(blob),img=new Image();img.onload=()=>{const c=document.createElement('canvas');c.width=2160;c.height=1200;const ctx=c.getContext('2d');ctx.fillStyle='#f8fafc';ctx.fillRect(0,0,c.width,c.height);ctx.drawImage(img,0,0,c.width,c.height);URL.revokeObjectURL(url);c.toBlob(png=>{const a=document.createElement('a');a.href=URL.createObjectURL(png);a.download=`${state.crate}_${state.view}_3D.png`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)},'image/png');};img.src=url;}
  document.getElementById('crateExportSvg')?.addEventListener('click',exportSvg);document.getElementById('crateExportPng')?.addEventListener('click',exportPng);
})();


// V4.6.1: precision-preserving high-performance smart-fill with dual progress.
(function initSmartFillProgress(){
  function ensureModal(){
    let m=document.getElementById('smartFillProgressModal');
    if(m) return m;
    m=document.createElement('div');m.id='smartFillProgressModal';m.className='sf-progress-modal';
    m.innerHTML=`<div class="sf-progress-backdrop"></div><div class="sf-progress-panel">
      <div class="sf-progress-head"><div><div class="sf-kicker">多候选3D启发式智能填空</div><h3 id="sfStage">准备计算…</h3></div><div class="sf-percent-stack"><span>总任务</span><strong id="sfPercent">0%</strong></div></div>
      <div class="sf-progress-track"><div id="sfBar" class="sf-progress-bar" style="width:0%"></div></div>
      <div class="sf-stage-progress"><div class="sf-stage-label"><span>当前阶段进度</span><b id="sfStagePercent">0%</b></div><div class="sf-stage-track"><div id="sfStageBar" class="sf-stage-bar" style="width:0%"></div></div></div>
      <div class="sf-progress-grid">
        <div><span>当前步骤</span><b id="sfDetail">任务初始化</b></div>
        <div><span>候选/试排进度</span><b id="sfCandidate">—</b></div>
        <div><span>3D试算次数</span><b id="sfTrials">0</b></div>
        <div><span>已用时间</span><b id="sfElapsed">0.0 s</b></div>
      </div>
      <div class="sf-best"><span>当前最佳结果</span><b id="sfBest">尚未找到可行方案</b></div>
      <div class="sf-progress-actions"><button type="button" id="sfCancel" class="danger">取消计算</button><button type="button" id="sfClose" class="ghost-action" style="display:none">关闭</button></div>
      <p class="sf-note">V4.4 不截断候选搜索：增量3D试装成功时直接复用已有合法布局；失败时自动回退原完整重排，并用等价SKU去重、状态缓存和确定性边界剪枝减少重复计算。</p>
    </div>`;
    document.body.appendChild(m);return m;
  }
  function setProgress(data){
    const p=Math.max(0,Math.min(100,Number(data.progress||0)));
    document.getElementById('sfPercent').textContent=`${p}%`;
    document.getElementById('sfBar').style.width=`${p}%`;
    document.getElementById('sfStage').textContent=data.stage||'计算中';
    document.getElementById('sfDetail').textContent=data.detail||'—';
    const sp=Math.max(0,Math.min(100,Number(data.stage_progress ?? 0)));
    document.getElementById('sfStagePercent').textContent=`${sp.toFixed(sp%1?1:0)}%`;
    document.getElementById('sfStageBar').style.width=`${sp}%`;
    const cur=Number(data.candidate_current||0),tot=Number(data.candidate_total||0);
    document.getElementById('sfCandidate').textContent=tot?`${cur} / ${tot}`:'—';
    document.getElementById('sfTrials').textContent=String(data.trials||0);
    document.getElementById('sfElapsed').textContent=`${Number(data.elapsed||0).toFixed(1)} s`;
    document.getElementById('sfBest').textContent=data.best||'尚未找到可行方案';
  }
  document.querySelectorAll('form.js-fast-fill-form').forEach(form=>{
    form.addEventListener('submit',async e=>{
      e.preventDefault();e.stopPropagation();
      if(!confirm('将执行多候选3D启发式搜索，并实时显示候选箱、3D试排和当前最佳结果。确认执行吗？'))return;
      const btn=form.querySelector('button[type="submit"]'),old=btn?.textContent||'';
      if(btn){btn.disabled=true;btn.textContent='正在启动完整搜索…';btn.classList.add('is-loading');}
      const modal=ensureModal();modal.classList.add('open');document.body.classList.add('modal-open');
      let jobId=null,poller=null,finished=false;
      const cancel=document.getElementById('sfCancel'),close=document.getElementById('sfClose');
      cancel.style.display='inline-flex';close.style.display='none';cancel.disabled=false;cancel.textContent='取消计算';
      setProgress({progress:0,stage:'任务启动',detail:'正在提交后台多候选3D启发式任务…',elapsed:0});
      const finishUI=(ok,msg)=>{finished=true;if(poller)clearInterval(poller);cancel.style.display='none';close.style.display='inline-flex';document.getElementById('sfDetail').textContent=msg||'';if(btn){btn.disabled=false;btn.textContent=old;btn.classList.remove('is-loading');}if(ok){close.textContent='查看结果';close.onclick=()=>{window.location.href='/?message='+encodeURIComponent(msg||'多候选3D启发式搜索完成');};}else{close.textContent='关闭';close.onclick=()=>{modal.classList.remove('open');document.body.classList.remove('modal-open');};}};
      try{
        const startUrl=form.action.replace(/\/smart-fill-crate\/?$/,'/smart-fill-crate/start');
        const r=await fetch(startUrl,{method:'POST',body:new FormData(form)});
        const j=await r.json();if(!r.ok||!j.ok)throw new Error(j.error||('服务器返回 '+r.status));jobId=j.job_id;
        const poll=async()=>{
          try{
            const sr=await fetch(`/smart-fill-crate/status/${jobId}`,{cache:'no-store'}),s=await sr.json();
            if(!sr.ok||!s.ok)throw new Error(s.error||'读取进度失败');setProgress(s);
            if(s.status==='done'){setProgress({...s,progress:100});finishUI(true,s.message||'多候选3D启发式搜索完成');}
            else if(s.status==='error'){finishUI(false,'计算失败：'+(s.error||s.detail||'未知错误'));}
            else if(s.status==='cancelled'){finishUI(false,s.detail||'计算已取消');}
          }catch(err){finishUI(false,'读取计算进度失败：'+String(err?.message||err));}
        };
        await poll();if(!finished)poller=setInterval(poll,600);
        cancel.onclick=async()=>{if(!jobId||finished)return;cancel.disabled=true;cancel.textContent='正在取消…';try{await fetch(`/smart-fill-crate/cancel/${jobId}`,{method:'POST'});}catch(e){} };
      }catch(err){finishUI(false,'启动计算失败：'+String(err?.message||err));}
    },true);
  });
})();


// V5.5 — all gated list exports use POST so the server can enforce draft/invalid-layout gates.
(function initContainerLoadListExport(){
  async function downloadPost(url,payload){
    const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(!r.ok){let j={};try{j=await r.json()}catch(_e){}throw new Error(j.error||`导出失败：HTTP ${r.status}`);}
    const blob=await r.blob();let fn='export.xlsx';const cd=r.headers.get('content-disposition')||'';const m=cd.match(/filename\*=UTF-8''([^;]+)/i)||cd.match(/filename=\"?([^\";]+)\"?/i);if(m)try{fn=decodeURIComponent(m[1])}catch(_e){fn=m[1]};
    const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=fn;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1500);
  }
  window.packingDownloadPost=downloadPost;
  async function exportList(all){
    const scheme=document.getElementById('schemeSelect'),container=document.getElementById('containerSelect'),mode=document.getElementById('exportMode')?.value||'planning';
    if(!scheme||!scheme.value){alert('当前没有可导出的装柜方案。');return;}
    if(window.containerLayoutHasDirty?.(all)){alert('存在尚未保存并通过后端规划校核的手动微调。请先保存或恢复自动排布。');return;}
    const idx=all?0:Number(container?.value||1),deep=new URLSearchParams(window.location.search).get('deep')==='1';
    try{await downloadPost('/export-container-load-list',{scheme:scheme.value,container_index:idx,deep,mode});}catch(e){alert(String(e.message||e));}
  }
  document.getElementById('exportContainerListBtn')?.addEventListener('click',()=>exportList(false));
  document.getElementById('exportAllContainerListBtn')?.addEventListener('click',()=>exportList(true));
  document.getElementById('exportUnloadedListBtn')?.addEventListener('click',async()=>{
    const scheme=document.getElementById('schemeSelect'),mode=document.getElementById('exportMode')?.value||'planning';if(!scheme?.value){alert('当前没有可用集装箱方案');return;}if(window.containerLayoutHasDirty?.(true)){alert('存在尚未保存的手动微调，请先保存或恢复自动排布。');return;}
    const deep=new URLSearchParams(window.location.search).get('deep')==='1';try{await downloadPost('/export-unloaded-packing-list',{scheme:scheme.value,deep,mode});}catch(e){alert(String(e.message||e));}
  });
  document.getElementById('exportShipmentPackageBtn')?.addEventListener('click',async()=>{
    const scheme=document.getElementById('schemeSelect');if(!scheme?.value){alert('当前没有可生成整票数据对照归档包的装柜方案。');return;}if(window.containerLayoutHasDirty?.(true)){alert('存在尚未保存的手动微调，请先保存或恢复自动排布。');return;}
    const deep=new URLSearchParams(window.location.search).get('deep')==='1';try{await downloadPost('/export-shipment-package',{scheme:scheme.value,deep});}catch(e){alert(String(e.message||e));}
  });
})();


// V5.5 — non-container documents share the same planning/project-confirmation mode and server gate.
(function initDocumentExportsV48(){
  const mode=()=>document.getElementById('documentExportMode')?.value||'planning';
  const dl=async(url,payload={})=>{
    if(window.packingDownloadPost) return window.packingDownloadPost(url,payload);
    const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(!r.ok){let j={};try{j=await r.json()}catch(_e){}throw new Error(j.error||`导出失败：HTTP ${r.status}`);}
    const blob=await r.blob();let fn='export.bin';const cd=r.headers.get('content-disposition')||'';const m=cd.match(/filename\*=UTF-8''([^;]+)/i)||cd.match(/filename="?([^";]+)"?/i);if(m)try{fn=decodeURIComponent(m[1])}catch(_e){fn=m[1]};
    const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=fn;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1500);
  };
  const run=async(url,payload={})=>{try{await dl(url,{...payload,mode:mode()});}catch(e){alert(String(e.message||e));}};
  document.getElementById('exportPackingListBtn')?.addEventListener('click',()=>run('/export-packing-list'));
  document.getElementById('exportShippingMarksBtn')?.addEventListener('click',()=>run('/export-shipping-marks'));
  document.querySelectorAll('.js-export-crate-mark').forEach(btn=>btn.addEventListener('click',()=>run(`/export-shipping-mark/${btn.dataset.crateId}`)));
  document.getElementById('exportUnloadedPackingTopBtn')?.addEventListener('click',async()=>{
    const scheme=document.getElementById('schemeSelect');
    if(!scheme?.value){alert('当前没有可用集装箱方案。');return;}
    if(window.containerLayoutHasDirty?.(true)){alert('存在尚未保存的手动微调，请先保存或恢复自动排布。');return;}
    const deep=new URLSearchParams(window.location.search).get('deep')==='1';
    await run('/export-unloaded-packing-list',{scheme:scheme.value,deep});
  });
})();
