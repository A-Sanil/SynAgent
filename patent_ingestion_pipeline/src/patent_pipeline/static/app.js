document.addEventListener('DOMContentLoaded', ()=>{
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
