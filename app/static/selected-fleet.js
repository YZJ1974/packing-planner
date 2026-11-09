(function(){
  const form=document.getElementById('selectedFleetForm'),mode=document.getElementById('selectedFleetMode');
  form?.querySelectorAll('[data-fleet-count]').forEach(input=>input.addEventListener('input',()=>{if(mode)mode.checked=true;}));
})();
