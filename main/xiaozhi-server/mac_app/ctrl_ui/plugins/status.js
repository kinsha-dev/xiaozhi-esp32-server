/* Status of every component the app reports (/api/components): one card each, always laid out the same, with its state,
   last error and when that happened, and a Restart button. A new component shows up here without changes. */
(()=>{
const COMP_ICON={server:"◉",whisper:"◎",board:"☰",watchdog:"⚑",tunnel:"⇄",ctrl:"▣",mac:"▣",voice:"◈"};
const iconFor=x=>{const k=String(x.key||x.name||"").toLowerCase();for(const[p,ic]of Object.entries(COMP_ICON))if(k.includes(p))return ic;return x.running?"●":"○"};
const seen=new Set();
const card=x=>{const el=document.createElement("div"),key=String(x.key||x.name||""),fresh=!seen.has(key);seen.add(key);el.className="card"+(fresh?" enter":"");
  el.innerHTML=`<h3><span><span class="dot ${x.running?"on":""}"></span><span class="ico" aria-hidden="true">${iconFor(x)}</span>${esc(x.name)}</span><span class="meta">${x.running?"running":"stopped"}${x.pid?" · pid "+esc(x.pid):""}</span></h3>
  <div class="meta">${esc(x.detail||"—")}</div><div class="${x.error?"err":"meta"}">Last error: ${esc(x.error||"none")}</div><div class="meta">Error time: ${esc(x.error?x.error_at||"unknown":"—")}</div><div class="cardbar"></div>`;
  const bar=el.querySelector(".cardbar"),button=(text,run)=>{const b=document.createElement("button");b.type="button";b.className="act";b.textContent=text;b.onclick=run;bar.append(b);return b};
  const again=button("Restart",async()=>{if(!confirm("Restart "+x.name+"?"))return;const r=await api("/api/restart",{key:x.restart});toast(r.ok?(r.note||x.name+" restarting"):"Failed: "+r.error)});
  if(!x.restart){again.disabled=true;again.title=x.name+" can't be restarted from here"}
  if(x.key=="board")button(x.muted?"Unmute board & Mac":"Mute board & Mac",async()=>{await mute(!x.muted);VibiPlugins.draw()});
  return el};
VibiPlugins.add({id:"components",label:"● Status",title:"Components",every:5000,async render(body){
  const list=await api("/api/components");if(!Array.isArray(list)){body.textContent=list.error||"Status is not available";return}
  const grid=document.createElement("div"),when=document.createElement("div");grid.className="grid";grid.append(...list.map(card));
  when.className="meta";when.textContent="Checked "+new Date().toLocaleTimeString();body.replaceChildren(when,grid);
  const down=list.filter(x=>!x.running).length,b=$("#plug-components");if(b){b.textContent=down?`● Status (${down} down)`:"● Status";b.classList.toggle("bad",down>0)}}});
})();
