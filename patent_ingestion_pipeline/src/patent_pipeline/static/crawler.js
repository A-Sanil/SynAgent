async function refreshCrawler(){
  try{
    const res = await fetch('/api/crawler/status')
    const data = await res.json()
    const c = data.crawler || {}
    document.getElementById('crawler-message').textContent =
      c.active ? `Running (${c.profile}) — ${c.current_query || ''}` : `Idle — last: ${c.message || 'none'}`
    document.getElementById('crawler-detail').textContent =
      `Found ${c.urls_found||0} · Collected ${c.collected||0} · Skipped ${c.skipped||0} · Errors ${c.errors||0}` +
      (c.fetcher_used ? ` · Fetcher: ${c.fetcher_used}` : '')
    const counts = data.counts || {}
    document.getElementById('db-counts').textContent =
      `Patents ${counts.patents||0} · Reactions ${counts.reactions||0} · Raw ${counts.raw_documents||0} · Queue ${counts.queue_pending||0}`
    const tbody = document.querySelector('#runs-table tbody')
    tbody.innerHTML = ''
    for(const r of (data.runs || [])){
      const tr = document.createElement('tr')
      tr.innerHTML = `<td>${r.started_at||''}</td><td>${r.profile||''}</td><td>${r.query||''}</td>
        <td>${r.urls_found||0}</td><td>${r.collected||0}</td><td>${r.skipped||0}</td>
        <td>${r.errors||0}</td><td>${r.status||''}</td>`
      tbody.appendChild(tr)
    }
  }catch(e){
    document.getElementById('crawler-message').textContent = 'Cannot reach API'
  }
}
document.addEventListener('DOMContentLoaded', ()=>{
  refreshCrawler()
  setInterval(refreshCrawler, 5000)
})
