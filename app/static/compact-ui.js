/* Presentation only: controls stay enabled and remain in their original forms. */
(function(){
  const labels={evidence:'核对附件',evidence_length_mm:'凭证长度（mm）',evidence_width_mm:'凭证宽度（mm）',evidence_height_mm:'凭证高度（mm）',evidence_note:'凭证 / 记录编号',actual_gross_weight_kg:'实称毛重（kg）',evidence_weight_kg:'秤票核对值（kg）',max_top_load_kg:'顶载限值（kg）',top_load_source_type:'顶载来源',top_load_source_note:'顶载资料说明',top_load_evidence:'顶载凭证附件',evidence_top_load_kg:'凭证顶载（kg）',top_load_evidence_note:'顶载凭证编号',contact_area_source_type:'接触面积来源',contact_area_source_note:'接触面积资料说明',skid_count:'滑木数量',skid_width_mm:'单根滑木接触宽（mm）',skid_contact_length_mm:'单根有效接触长（mm）',custom_contact_area_m2:'自定义有效面积（m²）',contact_area_evidence:'接触面积凭证附件',evidence_contact_area_m2:'凭证有效面积（m²）',contact_area_evidence_note:'接触面积凭证编号',lashing_points:'系固点数量',lashing_evidence:'系固凭证附件',evidence_lashing_points:'凭证系固点数',lashing_evidence_note:'系固凭证编号',handling_note:'叉车 / 吊装 / 装卸说明'};
  document.querySelectorAll('.crate-fold input,.crate-fold select').forEach(input=>{
    if(['checkbox','hidden','submit','button'].includes(input.type)||input.closest('label'))return;
    const label=document.createElement('label'),span=document.createElement('span');label.className='compact-field-label';span.textContent=labels[input.name]||input.placeholder||input.name;input.before(label);label.append(span,input);
  });
  function updateContact(form){
    const mode=form.querySelector('[name=contact_area_mode]')?.value;
    ['skid_count','skid_width_mm','skid_contact_length_mm'].forEach(name=>{const control=form.querySelector(`[name="${name}"]`);if(control?.closest('.compact-field-label'))control.closest('.compact-field-label').hidden=mode!=='SKIDS';});
    const custom=form.querySelector('[name=custom_contact_area_m2]');if(custom?.closest('.compact-field-label'))custom.closest('.compact-field-label').hidden=mode!=='CUSTOM';
  }
  document.querySelectorAll('.physical-constraint-form').forEach(form=>{
    updateContact(form);form.querySelector('[name=contact_area_mode]')?.addEventListener('change',()=>updateContact(form));
  });
  // Browser validation must reveal a folded invalid input before trying to focus it.
  document.addEventListener('invalid',e=>{let node=e.target.parentElement;while(node){if(node.tagName==='DETAILS')node.open=true;node=node.parentElement;}},true);
  const button=document.getElementById('toggleAllEngineering');
  button?.addEventListener('click',()=>{
    const open=button.getAttribute('aria-pressed')!=='true';
    document.querySelectorAll('details.ui-fold').forEach(d=>d.open=open);
    button.setAttribute('aria-pressed',String(open));button.textContent=open?'收起详细参数':'展开详细参数';
  });
  document.querySelectorAll('.workflow-nav a').forEach(a=>a.addEventListener('click',()=>{
    const target=document.getElementById(a.hash.slice(1));let node=target;while(node){if(node.tagName==='DETAILS')node.open=true;node=node.parentElement;}
  }));
})();

// Filters keep the forms in the DOM; submission and validation are unchanged.
(function(){
  const search=document.getElementById('crateSearch'),filter=document.getElementById('crateFilter');
  const cards=[...document.querySelectorAll('.crate-overview-card')];
  function apply(){const query=(search?.value||'').trim().toLocaleLowerCase(),mode=filter?.value||'all';let count=0;
    cards.forEach(card=>{const visible=(!query||(card.dataset.search||'').toLocaleLowerCase().includes(query))&&(mode==='all'||mode==='review'&&card.dataset.review==='1'||mode==='prepacked'&&card.dataset.prepacked==='1');card.hidden=!visible;if(visible)count++;});
    const label=document.getElementById('crateFilterCount');if(label)label.textContent=`显示 ${count} / ${cards.length} 箱`;
    const empty=document.getElementById('crateFilterEmpty');if(empty)empty.hidden=count!==0;
  }
  search?.addEventListener('input',apply);filter?.addEventListener('change',apply);apply();
  const dirty=new Set(),initial=new WeakMap();
  function signature(form){return JSON.stringify([...form.elements].filter(e=>e.name).map(e=>[e.name,e.type==='file'?[...e.files].map(f=>[f.name,f.size,f.lastModified]):e.type==='checkbox'||e.type==='radio'?e.checked:e.value]));}
  const forms=[...document.querySelectorAll('form')];forms.forEach(form=>initial.set(form,signature(form)));
  function update(){const label=document.getElementById('formDirtyCount');if(label)label.textContent=dirty.size?`${dirty.size} 处参数尚未保存`:'';}
  function changed(event){const form=event.target.form;if(!form||!initial.has(form))return;if(signature(form)===initial.get(form))dirty.delete(form);else dirty.add(form);update();}
  document.addEventListener('input',changed);document.addEventListener('change',changed);
  window.addEventListener('beforeunload',event=>{if(dirty.size){event.preventDefault();event.returnValue='';}});
  window.addEventListener('submit',event=>{
    const others=[...dirty].filter(form=>form!==event.target);
    if(others.length&&!confirm(`另有 ${others.length} 处参数尚未保存，保存本表单后页面可能刷新并丢弃这些修改。是否继续？`)){event.preventDefault();event.stopImmediatePropagation();return;}
    // Submission is only an attempt. Keep dirty state until successful navigation;
    // cancellation, server failure and network errors must retain unload protection.
    update();
  },true);
})();
