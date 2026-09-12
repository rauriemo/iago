// Historical inspection is separate from the live preview and current-source choice.
export function installHistory(api, post) {
  const $ = id => document.getElementById(id);
  let epoch = 0, request, next = null, end = null, filters = null;
  let expiryTimer, inspection = 0;
  const urls = new Set();
  function reset() {
    epoch++;
    inspection++;
    clearTimeout(expiryTimer);
    request?.abort();
    for (const url of urls) URL.revokeObjectURL(url);
    urls.clear();
    $('historyPages').replaceChildren();
    $('historyCoverage').replaceChildren();
    $('historyImage').replaceChildren();
    $('historyOlder').disabled = true;
    $('historyMessage').textContent = 'History changed or expired. Refresh to browse.';
    next = null;
    window.dispatchEvent(new Event('iago-history-unselected'));
  }
  window.addEventListener('iago-visual-change', reset);
  window.addEventListener('pagehide', reset);
  for (const id of ['clear', 'end']) $(id).addEventListener('click', reset, true);
  async function load(older = false) {
    const anchor = older ? next : null;
    if (!older) {
      filters = {source: $('historySource').value, query: $('historyQuery').value};
      for (const [name, id] of [['start','historyStart'], ['end','historyEnd']]) {
        const value = $(id).value;
        if (value) filters[name] = String(new Date(value).getTime()/1000);
      }
      end = null;
    }
    reset();
    const own = epoch;
    request = new AbortController();
    const signal = request.signal;
    const started = performance.now();
    const params = new URLSearchParams(filters);
    if (anchor) params.set('before', anchor);
    if (end != null) params.set('end', end);
    $('historyMessage').textContent = 'Loading retained images…';
    try {
      const page = await (await api(`/api/history?${params}`, {signal})).json();
      if (own !== epoch) return;
      end = page.end;
      for (const source of page.sources) {
        const retained = source.retained;
        if (!retained) continue;
        const row = document.createElement('p');
        const range = retained.earliest_capture == null ? 'No rolling images' :
          `${new Date(retained.earliest_capture*1000).toLocaleTimeString()}–${new Date(retained.latest_capture*1000).toLocaleTimeString()}`;
        row.textContent = `${source.kind}: ${source.label} · ${range} · ${retained.rolling_frames} rolling images (${(retained.rolling_bytes/1048576).toFixed(2)} MiB) · ${retained.pin_frames} pins (${(retained.pin_bytes/1048576).toFixed(2)} MiB) · PC upload requests: ${source.upload_accepted||0} accepted, ${source.upload_rejected||0} rejected of ${source.upload_attempts||0} attempted. Snapshot at refresh.`;
        $('historyCoverage').append(row);
      }

      function expirePage() {
        clearTimeout(expiryTimer);
        const expires = page.frames.filter(f => f.expires != null).map(f => f.expires);
        if (expires.length) expiryTimer = setTimeout(() => { if (own === epoch) reset(); },
          Math.max(0, (Math.min(...expires)-page.server_time)*1000-(performance.now()-started)));
      }
      expirePage();
      const selection = $('historySource').value;
      $('historySource').replaceChildren(new Option('All sources', ''));
      for (const source of page.sources) $('historySource').add(new Option(`${source.kind}: ${source.label}`, source.id));
      if ([...$('historySource').options].some(o => o.value === selection)) $('historySource').value = selection;
      for (const frame of page.frames) {
        const blob = await (await api(`/api/frame/${frame.id}?thumbnail=true`, {signal})).blob();
        if (own !== epoch) return;
        const tile = document.createElement('div'); tile.className = 'frame';
        const label = `${frame.source_label} · ${new Date(frame.captured*1000).toLocaleString()}`;
        const image = document.createElement('img'); image.alt = label;
        const url = URL.createObjectURL(blob); urls.add(url); image.src = url;
        const inspect = document.createElement('button'); inspect.textContent = 'Inspect image';
        inspect.onclick = async () => {
          const selected = ++inspection;
          try {
            const original = await (await api(`/api/frame/${frame.id}`, {signal})).blob();
            if (own !== epoch || selected !== inspection) return;
            const full = document.createElement('img'); full.alt = label; full.style.maxWidth = '100%';
            const previous = $('historyImage').dataset.url;
            if (previous) { URL.revokeObjectURL(previous); urls.delete(previous); }
            const fullURL = URL.createObjectURL(original); urls.add(fullURL); full.src = fullURL;
            $('historyImage').dataset.url = fullURL;
            const surface = document.createElement('div');
            surface.style.cssText = 'position:relative;width:fit-content;max-width:100%';
            full.style.display = 'block'; full.style.touchAction = 'none'; full.draggable = false;
            const outline = document.createElement('div');
            outline.style.cssText = 'position:absolute;border:2px solid #dd7a20;pointer-events:none;box-sizing:border-box;display:none';
            surface.append(full, outline);
            const crop = document.createElement('button'); crop.id = 'historyUseCrop';
            crop.textContent = 'Use crop for typed question'; crop.disabled = true;
            let start = null, region = null;
            function point(event) {
              const box = full.getBoundingClientRect();
              if (!box.width || !box.height) return null;
              return [Math.max(0,Math.min(frame.width,Math.floor((event.clientX-box.left)*frame.width/box.width))),
                      Math.max(0,Math.min(frame.height,Math.floor((event.clientY-box.top)*frame.height/box.height)))];
            }
            function draw(event) {
              const end = point(event);
              if (!start || !end) return;
              const x = Math.min(start[0],end[0]), y = Math.min(start[1],end[1]);
              const w = Math.abs(start[0]-end[0]), h = Math.abs(start[1]-end[1]);
              region = w && h ? [x,y,w,h] : null; crop.disabled = !region;
              spokenCrop.disabled = !region;
              Object.assign(outline.style, {display:region?'block':'none', left:`${100*x/frame.width}%`,
                top:`${100*y/frame.height}%`,width:`${100*w/frame.width}%`,height:`${100*h/frame.height}%`});
            }
            full.onpointerdown = event => { if (event.button !== 0) return; start=point(event); region=null; crop.disabled=true; full.setPointerCapture(event.pointerId); draw(event); };
            full.onpointermove = event => { if (full.hasPointerCapture(event.pointerId)) draw(event); };
            full.onpointerup = event => { draw(event); start=null; if(full.hasPointerCapture(event.pointerId))full.releasePointerCapture(event.pointerId); };
            full.onpointercancel = () => { start=null; region=null; crop.disabled=true; spokenCrop.disabled=true; outline.style.display='none'; };
            crop.onclick = () => {
              if (own !== epoch || selected !== inspection || !region) return;
              window.dispatchEvent(new CustomEvent('iago-history-selected', {detail:{id:frame.id,label:`${label} · selected crop`,region:[...region]}}));
            };
            const spokenCrop = document.createElement('button'); spokenCrop.textContent = 'Use crop for spoken question';
            spokenCrop.disabled = true;
            spokenCrop.onclick = () => {
              if (own !== epoch || selected !== inspection || !region) return;
              window.dispatchEvent(new CustomEvent('iago-history-spoken', {detail:{id:frame.id,label:`${label} · selected crop`,region:[...region]}}));
            };
            const hint = document.createElement('p'); hint.textContent = 'Drag across the image to select detail. The full image is included for context.';
            const save = document.createElement('a'); save.textContent = 'Save image';
            save.className = 'button'; save.href = fullURL;
            const extension = frame.transformation?.stored_format === 'PNG' ? 'png' : 'jpg';
            save.download = /^[0-9a-f]{64}$/.test(frame.image_sha256||'') ? `${frame.image_sha256}.${extension}` : `iago-image.${extension}`;
            save.onclick = event => { if (own !== epoch || selected !== inspection) event.preventDefault(); };
            const exportHint = document.createElement('p');
            exportHint.textContent = 'Saved image files remain on your device after Clear or End. Manage those files separately.';
            $('historyImage').replaceChildren(document.createTextNode(label), surface, hint, crop, spokenCrop, save, exportHint);
          } catch (error) { if (own === epoch) $('historyMessage').textContent = error.message; }
        };
        const pin = document.createElement('button'); pin.textContent = frame.pin ? 'Unpin image' : 'Keep image';
        const pinLabel = document.createElement('input');
        pinLabel.type = 'text'; pinLabel.maxLength = 100;
        pinLabel.setAttribute('aria-label', 'Pinned image label');
        pinLabel.value = frame.pin || label.slice(0, 100);
        pinLabel.disabled = !!frame.pin;
        pin.onclick = async () => {
          const keeping = !frame.pin;
          const requestedLabel = pinLabel.value.trim() || label.slice(0, 100);
          pin.disabled = true;
          try {
            await post('/api/visual', {action: keeping ? 'pin' : 'unpin', frame: frame.id, label: requestedLabel});
            if (own !== epoch) return;
            frame.pin = keeping ? requestedLabel : '';
            pinLabel.value = requestedLabel; pinLabel.disabled = keeping;
            frame.expires = frame.pin ? null : frame.captured + page.retention;
            expirePage();
            pin.textContent = frame.pin ? 'Unpin image' : 'Keep image';
          } catch (error) { if (own === epoch) $('historyMessage').textContent = error.message; }
          finally { if (own === epoch) pin.disabled = false; }
        };
        const use = document.createElement('button'); use.textContent = 'Use for typed question';
        use.onclick = () => {
          if (own !== epoch) return;
          window.dispatchEvent(new CustomEvent('iago-history-selected', {detail: {id: frame.id, label}}));
        };
        const spoken = document.createElement('button'); spoken.textContent = 'Use for spoken question';
        spoken.onclick = () => {
          if (own !== epoch) return;
          window.dispatchEvent(new CustomEvent('iago-history-spoken', {detail:{id:frame.id,label}}));
        };
        tile.append(image, document.createTextNode(label), inspect, pinLabel, pin, use, spoken);
        $('historyPages').append(tile);
      }
      next = page.next_before;
      $('historyOlder').disabled = !next;
      $('historyMessage').textContent = page.frames.length ? `${page.frames.length} retained images. Labels search covers saved labels and pins, not image contents.` : 'No retained images match.';
    } catch (error) {
      if (own === epoch) $('historyMessage').textContent = `${error.message}. Refresh history if an image expired.`;
    }
  }
  $('historyFilter').onsubmit = event => { event.preventDefault(); load(); };
  $('historyOlder').onclick = () => load(true);
}
