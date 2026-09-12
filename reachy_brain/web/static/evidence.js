// Display host-validated evidence; fetched image lifetimes follow the live store.
export function evidenceView(api) {
  const root = document.getElementById('evidence');
  let generation = 0, request, current = [];
  let fullRequest, fullVersion=0, fullOwner=null;
  const urls = new Set(), timers = new Set();
  function clear() {
    generation++; request?.abort();
    fullVersion++; fullRequest?.abort(); fullOwner=null;
    for (const url of urls) URL.revokeObjectURL(url);
    for (const timer of timers) clearTimeout(timer);
    urls.clear(); timers.clear(); root.replaceChildren();
  }
  async function render(rows) {
    clear(); current = Array.isArray(rows) ? rows.slice(0,32) : [];
    const own = generation;
    request = new AbortController();
    const signal = request.signal;
    for (const row of current) {
      if (own !== generation) return;
      const card = document.createElement('div'), label = document.createElement('p');
      root.append(card); card.append(label);
      if (row.path) {
        label.textContent = `${row.project} · ${row.path} · ${row.locator_kind||'locator'} ${row.locator} · revision ${row.revision}`;
        if (typeof row.text === 'string') { const quote=document.createElement('blockquote'); quote.textContent=row.text; card.append(quote); }
        continue;
      }
      card.dataset.frame = row.id;
      label.textContent = `${row.source_kind||'image'} · ${row.source_label} · ${new Date(row.captured*1000).toLocaleString()}${row.region?' · inspected crop':''}${row.capture_time_known===false?' · capture time uncertain':''}`;
      const surface=document.createElement('div'); surface.style.cssText='position:relative;width:fit-content;max-width:100%';
      const image=document.createElement('img'); image.alt=label.textContent; image.style.cssText='display:block;max-width:100%';
      surface.append(image); card.append(surface);
      if (Array.isArray(row.region) && row.region.length===4 && row.region.every(Number.isFinite) && row.width>0 && row.height>0) {
        const [x,y,w,h]=row.region;
        if (x>=0 && y>=0 && w>0 && h>0 && x+w<=row.width && y+h<=row.height) {
          const outline=document.createElement('div'); outline.className='evidenceCrop';
          outline.style.cssText=`position:absolute;pointer-events:none;box-sizing:border-box;border:2px solid #dd7a20;left:${100*x/row.width}%;top:${100*y/row.height}%;width:${100*w/row.width}%;height:${100*h/row.height}%`;
          surface.append(outline);
        }
      }
      const full=document.createElement('button'); full.textContent='View full image'; card.append(full);
      let url, thumbnailURL, timer;
      function retire(message) {
        if (timer) {clearTimeout(timer);timers.delete(timer);}
        if (url) {URL.revokeObjectURL(url);urls.delete(url);}
        if (thumbnailURL && thumbnailURL!==url) {URL.revokeObjectURL(thumbnailURL);urls.delete(thumbnailURL);}
        if (fullOwner?.card===card) fullOwner=null;
        delete card.dataset.fullImage;
        surface.replaceChildren(); full.disabled=true;
        const note=document.createElement('p');note.textContent=message;surface.append(note);
      }
      async function load(original=false) {
        full.disabled=true;
        const started=performance.now();
        let fetchSignal=signal, ownFull=null;
        if(original){
          fullRequest?.abort();fullRequest=new AbortController();ownFull=++fullVersion;
          fetchSignal=fullRequest.signal;
        }
        try {
          const response=await api(`/api/frame/${encodeURIComponent(row.id)}${original?'':'?thumbnail=true'}`, {signal:fetchSignal});
          const expiry=response.headers.get('X-Iago-Expires-In');
          if (expiry!=='pinned' && (expiry==null || !Number.isFinite(Number(expiry)))) throw Error('Image expiry unavailable');
          const blob=await response.blob();
          if (own!==generation || (original && ownFull!==fullVersion)) return;
          if(expiry!=='pinned' && Number(expiry)*1000<=performance.now()-started){retire('Image expired; its source citation remains for review.');return;}
          if(original)fullOwner?.restore();
          if (url && url!==thumbnailURL) {URL.revokeObjectURL(url);urls.delete(url);}
          if (timer) {clearTimeout(timer);timers.delete(timer);}
          url=URL.createObjectURL(blob);urls.add(url);image.src=url;
          if(!original)thumbnailURL=url;
          else{
            const originalURL=url;card.dataset.fullImage='true';
            fullOwner={card,restore:()=>{
              URL.revokeObjectURL(originalURL);urls.delete(originalURL);
              url=thumbnailURL;image.src=thumbnailURL;delete card.dataset.fullImage;
            }};
          }
          if (expiry!=='pinned') {
            const delay=Math.max(0,Number(expiry)*1000-(performance.now()-started));
            timer=setTimeout(()=>{if(own===generation)retire('Image expired; its source citation remains for review.');},delay);
            timers.add(timer);
          }
          full.disabled=false;
        } catch(error) {if(own===generation){if(original && error.name==='AbortError')full.disabled=false;else retire(`Image unavailable: ${error.message}`);}}
      }
      full.onclick=()=>load(true);
      await load();
    }
  }
  window.addEventListener('iago-visual-change',()=>{current=[];clear();});
  window.addEventListener('iago-visual-pin-change',()=>render(current));
  window.addEventListener('pagehide',clear);
  document.getElementById('end').addEventListener('click',()=>{current=[];clear();},true);
  return render;
}
