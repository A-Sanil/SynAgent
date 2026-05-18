async function refreshStorageStatus(){
  const el = document.getElementById('storage-status')
  if(!el) return
  try{
    const res = await fetch('/api/status')
    if(!res.ok){ el.textContent = 'API error: ' + res.status; return }
    const s = await res.json()
    const c = s.counts || {}
    el.textContent = `Connected • DB: ${s.database} • Patents: ${c.patents||0} • Raw: ${c.raw_documents||0} • Queue: ${c.queue_pending||0}` + (s.gemini_configured ? '' : ' • Gemini key missing')
  }catch(e){
    el.textContent = 'Cannot reach API — restart runserver'
  }
}

async function runWorkerTick(){
  const status = document.getElementById('worker-status')
  const btn = document.getElementById('run-worker-btn')
  if(btn) btn.disabled = true
  if(status) status.textContent = 'Processing…'
  try{
    const res = await fetch('/api/worker/tick', {method:'POST'})
    const data = await res.json()
    if(!res.ok){
      alert(data.error || 'Worker failed')
    } else {
      alert(data.message || 'Done')
      if(location.pathname === '/') refreshStorageStatus()
      else location.reload()
    }
  }catch(e){
    alert('Network error')
  }finally{
    if(btn) btn.disabled = false
    if(status) status.textContent = 'Ready.'
  }
}

document.addEventListener('DOMContentLoaded', ()=>{
  refreshStorageStatus()
  document.getElementById('run-worker-btn')?.addEventListener('click', runWorkerTick)

  const backdrop = document.getElementById('modal-backdrop')
  const openBtns = document.querySelectorAll('[data-open-modal]')
  const closeBtn = document.getElementById('modal-close')
  const saveBtn = document.getElementById('modal-save')

  function openModal(data){
    backdrop.style.display='flex'
    // fill fields
    document.getElementById('modal-patent-id').value=data.patent_id||''
    document.getElementById('modal-reaction-id').value=data.reaction_id||''
    document.getElementById('modal-product').value=data.product_smiles||''
    document.getElementById('modal-yield').value=(data.yield_percent!=null)?data.yield_percent:''
    document.getElementById('modal-notes').value=data.notes||''
  }

  openBtns.forEach(b=>b.addEventListener('click', (e)=>{
    const data = JSON.parse(b.getAttribute('data-open-modal'))
    openModal(data)
  }))

  closeBtn?.addEventListener('click', ()=>backdrop.style.display='none')

  saveBtn?.addEventListener('click', async ()=>{
    const payload = {
      patent_id: document.getElementById('modal-patent-id').value,
      reaction_id: document.getElementById('modal-reaction-id').value,
      product_smiles: document.getElementById('modal-product').value,
      yield_percent: document.getElementById('modal-yield').value,
      notes: document.getElementById('modal-notes').value,
    }
    try{
      const res = await fetch('/api/reaction/update', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
      if(res.ok){
        location.reload()
      } else {
        alert('Save failed')
      }
    }catch(err){alert('Network error')}
  })

  // Batch review helpers
  window.openBatchEdit = function(reaction_id, patent_id, oldValue){
    const backdrop = document.getElementById('batch-modal')
    backdrop.style.display = 'flex'
    document.getElementById('batch-reaction-id').value = reaction_id
    document.getElementById('batch-patent-id').value = patent_id
    document.getElementById('batch-old').value = oldValue || ''
    document.getElementById('batch-new').value = oldValue || ''
  }
  document.getElementById('batch-close')?.addEventListener('click', ()=>document.getElementById('batch-modal').style.display='none')
  document.getElementById('batch-save')?.addEventListener('click', async ()=>{
    const payload = {
      reaction_id: document.getElementById('batch-reaction-id').value,
      patent_id: document.getElementById('batch-patent-id').value,
      field: document.getElementById('batch-field').value,
      old_value: document.getElementById('batch-old').value,
      new_value: document.getElementById('batch-new').value,
      user: 'web'
    }
    try{
      const res = await fetch('/api/active_learning', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
      if(res.ok){ location.reload() } else { alert('Save failed') }
    }catch(e){ alert('Network error') }
  })
})
