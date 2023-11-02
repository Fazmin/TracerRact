(function(){
'use strict';
var $=function(id){return document.getElementById(id)};
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function fmt(n){return Number(n).toLocaleString()}
function toast(m){var t=$('toast');t.textContent=m;t.classList.add('on');setTimeout(function(){t.classList.remove('on')},2400)}
function isoDay(ms){return ms?new Date(ms).toISOString().slice(0,10):''}

/* ---------- server ---------- */
function api(url,opt){return fetch(url,opt).then(function(r){return r.json().catch(function(){return {}}).then(function(d){if(!r.ok)throw new Error(d.error||('Server error '+r.status));return d})})}
function send(url,method,body){return api(url,{method:method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})})}
var BT;function busy(on){clearTimeout(BT);if(on)BT=setTimeout(function(){$('main').classList.add('loading')},150);else $('main').classList.remove('loading')}
function fail(e){busy(false);toast(e&&e.message&&e.message!=='Failed to fetch'?e.message:'Could not reach the server')}

/* ---------- state ---------- */
var META=null,FD=[],FK={},COLDEFS=[];
var SEL={},SEARCH='',VIEW='table',SORT={k:'n',d:1},PAGE=200,SHOWN=200,OPEN={},FQ={},FMORE={},SHOWCH=true,COLSEL={};
var ROWS=[],COUNT=0,FACETS={},SEQ=0,RENDERING=false,FSEQ={};
var COV_ORDER=['Both','runZero only','BlueCat only'];

function setupMeta(m){
  META=m;FD=m.facets;FK={};
  FD.forEach(function(f){FK[f.k]=f;if(!SEL[f.k])SEL[f.k]=new Set();if(!(f.k in OPEN))OPEN[f.k]=!!f.open;if(!(f.k in FQ))FQ[f.k]='';if(!(f.k in FMORE))FMORE[f.k]=false});
  COLDEFS=m.columns;var keep={};
  COLDEFS.forEach(function(c){keep[c.k]=1;if(!(c.k in COLSEL))COLSEL[c.k]=!!c.def});
  Object.keys(COLSEL).forEach(function(k){if(!keep[k])delete COLSEL[k]});
  if(!COLDEFS.some(function(c){return c.k===SORT.k}))SORT={k:'n',d:1};
}
function visibleCols(){return COLDEFS.filter(function(c){return COLSEL[c.k]})}
function params(extra){
  var p=new URLSearchParams();
  if(SEARCH)p.append('q',SEARCH);
  FD.forEach(function(f){SEL[f.k].forEach(function(v){p.append('f.'+f.k,v)});if(FMORE[f.k])p.append('more',f.k);if(FQ[f.k])p.set('fq.'+f.k,FQ[f.k])});
  p.set('sort',SORT.k);p.set('dir',String(SORT.d));
  visibleCols().forEach(function(c){p.append('cols',c.k)});
  Object.keys(extra||{}).forEach(function(k){p.set(k,extra[k])});
  return p.toString();
}

/* ---------- cells ---------- */
function pillRisk(v){if(!v)return '<span class="dim">–</span>';var c={critical:'--r-crit',high:'--r-high',medium:'--r-med',low:'--r-low'}[v]||'--r-none';return '<span class="pill"><span class="dot" style="background:var('+c+')"></span>'+esc(v)+'</span>'}
function pillCov(v){var c={'Both':'--c-both','runZero only':'--c-rz','BlueCat only':'--c-bc'}[v];return '<span class="pill"><span class="dot" style="background:var('+c+')"></span>'+esc(v)+'</span>'}
function dim(v){return v?esc(v):'<span class="dim">–</span>'}
function mono(v){return v?'<span class="mono">'+esc(v)+'</span>':'<span class="dim">–</span>'}
var CELL={
 n:function(r){return '<span class="mono">'+esc(r.ip||'(none)')+'</span>'},
 bld:function(r){return esc(r.bldC.indexOf('LOC:')===0?r.bld:r.bldC)},
 roomL:function(r){return dim(r.room)},
 hw:function(r){return dim(r.hw||r.os)},
 os:function(r){return dim((r.os+' '+r.osv).trim())},
 risk:function(r){return pillRisk(r.risk)},
 last:function(r){return mono(isoDay(r.last))},
 first:function(r){return mono(isoDay(r.first))},
 cov:function(r){return pillCov(r.cov)},
 mac:function(r){return mono(r.mac)},
 sub:function(r){return mono(r.sub)},
 tcp:function(r){return dim(r.tcp.join(' '))},
 prod:function(r){return dim(r.prod.join(' '))},
 tagsT:function(r){return dim(r.tags.map(function(t){return t[0]+': '+t[1]}).join(', '))}
};
function cell(c,r){
  if(c.extra){var v=r.raw&&r.raw[c.l];return v?esc(v.length>120?v.slice(0,120)+'…':v):'<span class="dim">–</span>'}
  return CELL[c.k]?CELL[c.k](r):dim(r[c.k]);
}

/* ---------- rendering ---------- */
function renderFacetList(f){
  var el=document.querySelector('[data-fl="'+f.k+'"]');if(!el)return;
  var d=FACETS[f.k]||{items:[],total:0,hidden:0};
  el.innerHTML=d.items.map(function(i){var on=SEL[f.k].has(i[0]);return '<button type="button" class="fi'+(i[1]?'':' z')+'" aria-pressed="'+on+'" data-f="'+f.k+'" data-v="'+esc(i[0])+'" title="'+esc(i[0])+'"><span class="bx"></span><span class="t">'+esc(i[0])+'</span><span class="c">'+fmt(i[1])+'</span></button>'}).join('')+
    (d.hidden>0?'<button type="button" class="more" data-more="'+f.k+'">Show '+(FMORE[f.k]?'fewer':fmt(d.hidden)+' more')+'</button>':(FMORE[f.k]&&d.total>10?'<button type="button" class="more" data-more="'+f.k+'">Show fewer</button>':''))+
    (!d.total?'<span class="dim" style="padding:4px">No values</span>':'');
  var h=document.querySelector('[data-fh="'+f.k+'"] .n');if(h){h.hidden=!SEL[f.k].size;h.textContent=SEL[f.k].size}
}
function buildFacets(){
  $('facs').innerHTML=FD.map(function(f){return '<section class="fac'+(OPEN[f.k]?' open':'')+'" data-fac="'+f.k+'"><button class="fh" type="button" data-fh="'+f.k+'" aria-expanded="'+!!OPEN[f.k]+'"><span>'+esc(f.l)+'<span class="n" hidden></span></span><span class="ch">▶</span></button><div class="fb">'+
    (f.search?'<input class="fs" type="search" data-fs="'+f.k+'" placeholder="Find in '+esc(f.l.toLowerCase())+'" aria-label="Find in '+esc(f.l)+'" value="'+esc(FQ[f.k])+'">':'')+'<div class="fl" data-fl="'+f.k+'"></div></div></section>'}).join('')}
function renderChips(){
  var out=[];
  FD.forEach(function(f){SEL[f.k].forEach(function(v){out.push('<span class="chip"><i>'+esc(f.l)+'</i><span>'+esc(v)+'</span><button type="button" aria-label="Remove filter" data-x="'+f.k+'" data-v="'+esc(v)+'">×</button></span>')})});
  if(SEARCH)out.push('<span class="chip"><i>Search</i><span>'+esc(SEARCH)+'</span><button type="button" aria-label="Clear search" data-x="__q">×</button></span>');
  $('chips').innerHTML=out.join('');}
function kpi(v,l,sub,flt){return (flt?'<button type="button" class="kpi" data-kf="'+flt+'">':'<div class="kpi">')+'<div class="v">'+v+(sub?' <small>'+sub+'</small>':'')+'</div><div class="l">'+l+'</div>'+(flt?'</button>':'</div>')}
function renderKpis(k){
  $('kpis').innerHTML=kpi(fmt(k.count),'Assets shown','of '+fmt(k.total))+kpi(fmt(k.both),'In both sources','','cov|Both')+kpi(fmt(k.rzOnly),'runZero only (not in BlueCat)','','cov|runZero only')+kpi(fmt(k.bcOnly),'BlueCat only (not scanned)','','cov|BlueCat only')+
   kpi(fmt(k.rooms),'Rooms')+kpi(fmt(k.hi),'High / critical risk')+kpi(fmt(k.alive),'Alive when scanned','of '+fmt(k.scanned)+' scanned')+kpi(fmt(k.eol),'End-of-life OS')}
function hbar(fk,items,colorFn,stackFn){
  var mx=Math.max.apply(null,items.map(function(i){return i[1]}).concat([1]));
  return '<div class="hb">'+items.map(function(i){var w=i[1]/mx*100,inner;
    if(stackFn){inner=stackFn(i[0]).map(function(s){return '<span style="width:'+(s.n/mx*100)+'%;background:var('+s.c+')"></span>'}).join('')}
    else inner='<span style="width:'+w+'%;background:var('+(colorFn?colorFn(i[0]):'--accent')+')"></span>';
    return '<button type="button" class="hr" aria-pressed="'+SEL[fk].has(i[0])+'" data-f="'+fk+'" data-v="'+esc(i[0])+'" title="'+esc(i[0])+'"><span class="nm">'+esc(i[0])+'</span><span class="ct">'+fmt(i[1])+'</span><span class="tr">'+inner+'</span></button>'}).join('')+'</div>'}
var COVC={'Both':'--c-both','runZero only':'--c-rz','BlueCat only':'--c-bc'},RISKC={critical:'--r-crit',high:'--r-high',medium:'--r-med',low:'--r-low',info:'--r-none',none:'--r-none'};
function stackBar(fk,items,colorOf){
  return '<div class="stk">'+items.map(function(i){return '<button type="button" aria-pressed="'+SEL[fk].has(i[0])+'" data-f="'+fk+'" data-v="'+esc(i[0])+'" title="'+esc(i[0])+': '+i[1]+'" style="background:var('+colorOf(i[0])+');flex:'+i[1]+' 1 0"></button>'}).join('')+'</div>'+
  '<div class="lg">'+items.map(function(i){return '<button type="button" data-f="'+fk+'" data-v="'+esc(i[0])+'"><span class="dot" style="background:var('+colorOf(i[0])+')"></span>'+esc(i[0])+' <b class="mono">'+fmt(i[1])+'</b></button>'}).join('')+'</div>'}
function renderCharts(c){
  $('main').classList.toggle('with-charts',!!(SHOWCH&&c));
  if(!SHOWCH||!c){$('charts').hidden=true;return}$('charts').hidden=false;
  var mx=Math.max.apply(null,c.months.map(function(m){return m[1]}).concat([1]));
  var mn=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  $('charts').innerHTML=
   '<div class="panel"><h2>Coverage <span>BlueCat vs runZero</span></h2>'+stackBar('cov',c.cov,function(v){return COVC[v]})+'</div>'+
   '<div class="panel"><h2>Buildings <span>'+c.bldTotal+' shown, colored by coverage</span></h2>'+hbar('bld',c.bld,null,function(v){var o=c.bldCov[v]||{};return COV_ORDER.map(function(k){return {n:o[k]||0,c:COVC[k]}})})+'</div>'+
   '<div class="panel"><h2>Device types <span>scanned assets</span></h2>'+(c.types.length?hbar('type',c.types):'<div class="dim">No scanned assets in this selection.</div>')+'</div>'+
   '<div class="panel"><h2>Risk <span>runZero rating</span></h2>'+(c.risk.length?stackBar('risk',c.risk,function(v){return RISKC[v]||'--r-none'}):'<div class="dim">No scanned assets in this selection.</div>')+'</div>'+
   '<div class="panel"><h2>Last seen by month <span>click a bar to filter</span></h2>'+(c.months.length?'<div class="cols">'+c.months.map(function(m){var p=m[0].split('-');return '<button type="button" class="col" aria-pressed="'+SEL.seenM.has(m[0])+'" data-f="seenM" data-v="'+esc(m[0])+'" title="'+esc(m[0])+': '+m[1]+'"><span class="v">'+m[1]+'</span><span class="b" style="height:'+Math.max(2,m[1]/mx*92)+'px"></span><span class="xl">'+mn[+p[1]-1]+" '"+p[0].slice(2)+'</span></button>'}).join('')+'</div>':'<div class="dim">No scan dates in this selection.</div>')+'</div>'+
   '<div class="panel"><h2>Open TCP ports <span>top 10</span></h2>'+(c.ports.length?hbar('tcp',c.ports):'<div class="dim">No open ports recorded.</div>')+'</div>';
}
function renderTable(){
  var cols=visibleCols();
  $('tbl').tHead.innerHTML='<tr>'+cols.map(function(c){return '<th data-s="'+esc(c.k)+'" tabindex="0"'+(SORT.k===c.k?' aria-sort="'+(SORT.d>0?'ascending':'descending')+'"':'')+'>'+esc(c.l)+'</th>'}).join('')+'</tr>';
  $('tbl').tBodies[0].innerHTML=ROWS.map(function(r){return '<tr data-id="'+r.id+'">'+cols.map(function(c){return '<td'+(c.w?' class="w"':'')+'>'+cell(c,r)+'</td>'}).join('')+'</tr>'}).join('');
  $('empty').hidden=COUNT>0;
  var left=COUNT-ROWS.length;
  $('pager').innerHTML=left>0?'<span>Showing '+fmt(ROWS.length)+' of '+fmt(COUNT)+'</span><button class="btn sm" type="button" data-pg="1">Show '+fmt(Math.min(PAGE,left))+' more</button><button class="btn sm" type="button" data-pg="all">Show all</button>':(COUNT?'<span>'+fmt(COUNT)+' assets</span>':'');
}
function renderLoc(locs){
  $('vloc').innerHTML=locs.length?'<div class="loc">'+locs.map(function(b){var k=b.bld;
    return '<article class="lc"><div class="lc-h"><button type="button" data-f="bld" data-v="'+esc(k)+'" aria-pressed="'+SEL.bld.has(k)+'">'+esc(k)+'</button><span>'+fmt(b.n)+' assets</span></div>'+
     '<div class="stk">'+COV_ORDER.filter(function(c){return b.cov[c]}).map(function(c){return '<span style="background:var('+COVC[c]+');flex:'+b.cov[c]+' 1 0" title="'+c+': '+b.cov[c]+'"></span>'}).join('')+'</div>'+
     '<div class="lc-m"><span>'+fmt(b.roomCount)+' rooms</span>'+(b.none?'<span>'+fmt(b.none)+' with no room</span>':'')+(b.hi?'<span style="color:var(--r-high)">'+fmt(b.hi)+' high or critical risk</span>':'')+'</div>'+
     '<div class="rc">'+b.rooms.map(function(x){return '<button type="button" class="rm" aria-pressed="'+SEL.room.has(x[0])+'" data-f="room" data-v="'+esc(x[0])+'">'+esc(x[0].replace(/^[^·]*· /,''))+'<b>'+x[1]+'</b></button>'}).join('')+(b.roomCount>b.rooms.length?'<span class="dim" style="padding:2px 6px">+'+(b.roomCount-b.rooms.length)+' more rooms</span>':'')+'</div></article>'}).join('')+'</div>':'<div class="empty">No assets match these filters.</div>'}
function viewChrome(){
  $('vtable').hidden=VIEW!=='table';$('vloc').hidden=VIEW!=='loc';
  document.querySelectorAll('[data-view]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.view===VIEW))});
  $('colbtn').hidden=VIEW!=='table';$('dens').hidden=VIEW!=='table';
}
function render(){
  renderChips();viewChrome();
  var my=++SEQ;RENDERING=true;busy(true);
  return api('/api/explore?'+params({limit:SHOWN,offset:0,view:VIEW,charts:SHOWCH?1:0})).then(function(d){
    if(my!==SEQ)return;
    RENDERING=false;COUNT=d.count;FACETS=d.facets;
    renderKpis(d.kpis);renderCharts(d.charts);FD.forEach(renderFacetList);
    $('vt').textContent=(VIEW==='table'?'Assets':'Locations')+' ('+fmt(COUNT)+')';
    if(VIEW==='table'){ROWS=d.rows;renderTable();$('tw').scrollTop=0}else{renderLoc(d.locations);$('vloc').scrollTop=0}
    busy(false);setTop();
  }).catch(function(e){if(my===SEQ){RENDERING=false;fail(e)}});
}
/* Rows only (sorting, paging, column changes). A full render still in flight already carries them. */
function loadRows(append){
  if(RENDERING)return render();
  var my=++SEQ,off=append?ROWS.length:0;busy(true);
  return api('/api/rows?'+params({offset:off,limit:Math.max(1,SHOWN-off)})).then(function(d){
    if(my!==SEQ)return;
    ROWS=append?ROWS.concat(d.rows):d.rows;COUNT=d.count;renderTable();busy(false);
  }).catch(function(e){if(my===SEQ)fail(e)});
}
function loadFacet(k){
  var my=FSEQ[k]=(FSEQ[k]||0)+1;
  api('/api/facets/'+encodeURIComponent(k)+'?'+params()).then(function(d){if(my!==FSEQ[k])return;FACETS[k]=d;renderFacetList(FK[k])}).catch(fail);
}
function setTop(){
  var r=document.documentElement.style,vb=document.querySelector('.vbar'),cs=getComputedStyle(vb);
  r.setProperty('--topH',$('top').offsetHeight+'px');
  r.setProperty('--fullH',Math.max(300,$('main').clientHeight-vb.offsetHeight-parseFloat(cs.marginTop)-parseFloat(cs.marginBottom))+'px');
}
function toggleSel(k,v){var s=SEL[k];s.has(v)?s.delete(v):s.add(v);SHOWN=PAGE;render()}

/* ---------- source chips ---------- */
function renderSrcs(){
  var rz=META.sources.rz,bc=META.sources.bc,st=META.stats||{};
  $('srcs').innerHTML='<span class="src"><i style="background:var(--c-rz)"></i>runZero <b title="'+esc(rz?rz.name:'none')+'">'+esc(rz?rz.name:'none loaded')+'</b> '+fmt(META.rzCount)+' assets</span>'+
   '<span class="src"><i style="background:var(--c-bc)"></i>BlueCat <b title="'+esc(bc?bc.name:'none')+'">'+esc(bc?bc.name:'none loaded')+'</b> '+fmt(META.bcCount)+' addresses</span>'+
   (st.rzRows?'<span class="src"><i style="background:var(--c-both)"></i>'+fmt(st.byIp+st.byExtra+st.byMac)+' matched</span>':'');
}
function showEmpty(){var e=!!META.empty;$('emptydb').hidden=!e;$('app').hidden=e;$('hint').hidden=e;document.querySelector('.tbar').hidden=e}

/* ---------- drawer ---------- */
function dl(pairs){return '<dl>'+pairs.filter(function(p){return p[1]!==''&&p[1]!=null}).map(function(p){return '<dt>'+esc(p[0])+'</dt><dd>'+(p[2]?p[1]:esc(p[1]))+'</dd>'}).join('')+'</dl>'}
function openDrawer(id){api('/api/assets/'+id).then(function(d){
  var r=d.asset,nb=d.neighbours,nbn=d.neighbourCount,same=d.sameSubnet;
  var all=[];if(r.raw)Object.keys(r.raw).forEach(function(k){all.push([k,r.raw[k]])});
  $('dr').innerHTML='<div class="dr-h"><div><h2>'+esc(r.ip||'No IP address')+'</h2><p>'+esc(r.host||r.an||'Unnamed asset')+(r.type?' · '+esc(r.type):'')+'</p></div><button class="x" type="button" data-close="dr" aria-label="Close detail">×</button></div>'+
   '<div class="tags">'+pillCov(r.cov).replace('class="pill"','class="tag"')+(r.risk?'<span class="tag">Risk '+pillRisk(r.risk)+'</span>':'')+(r.alive?'<span class="tag">Alive: '+esc(r.alive)+'</span>':'')+(r.eol?'<span class="tag" style="color:var(--bad)">OS end-of-life '+esc(r.eol)+'</span>':'')+(r.match?'<span class="tag">Matched by '+esc(r.match)+'</span>':'')+(r.dup?'<span class="tag" style="color:var(--bad)">Duplicate IP in runZero file</span>':'')+'</div>'+
   '<div class="sec"><h3>Location</h3>'+dl([['Building',r.bld],['Building source',r.bldSrc],['Room',r.room],['Owner',r.owner],['Subnet',r.sub?r.sub+(same?' ('+fmt(same)+' records)':''):''],['Subnet name',r.subName],['Address block',r.blk],['BlueCat state',r.state],['BlueCat location',r.loc]])+'</div>'+
   '<div class="sec"><h3>Identity</h3>'+dl([['Hostnames',r.hosts.join(', ')],['BlueCat name',r.an],['MAC',r.macs.length?r.macs.join(', '):r.mac],['MAC vendor',r.vendor],['Other IPs',r.ips2.join(', ')],['Serial numbers',r.serial]])+'</div>'+
   (r.hasRz?'<div class="sec"><h3>runZero</h3>'+dl([['Type',r.type],['Category',r.cat],['OS',(r.os+' '+r.osv).trim()],['Hardware',r.hw],['First seen',isoDay(r.first)],['Last seen',isoDay(r.last)],['Detected by',r.det],['Scanned by',r.agent],['Functions',r.func],['VLAN',r.vlan],['Criticality',r.crit],['Comments',r.notes]])+'</div>'+
    '<div class="sec"><h3>Network exposure</h3>'+dl([['TCP ports',r.tcp.join(' ')],['UDP ports',r.udp.join(' ')],['Protocols',r.proto.join(' ')],['Products',r.prod.join(' ')],['Tags',r.tags.map(function(t){return t[0]+': '+t[1]}).join(', ')]])+'</div>':'<div class="sec"><h3>runZero</h3><span class="dim">This address is in BlueCat but was not found in the runZero file.</span></div>')+
   '<div class="acts">'+(r.room?'<button class="btn sm" type="button" data-q="room" data-v="'+esc(r.roomL)+'">Filter to this room</button>':'')+'<button class="btn sm" type="button" data-q="bld" data-v="'+esc(r.bld)+'">Filter to this building</button>'+(r.sub?'<button class="btn sm" type="button" data-q="sub" data-v="'+esc(r.sub+'  '+r.subName)+'">Filter to this subnet</button>':'')+(r.type?'<button class="btn sm" type="button" data-q="type" data-v="'+esc(r.type)+'">Same device type</button>':'')+(r.owner?'<button class="btn sm" type="button" data-q="owner" data-v="'+esc(r.owner)+'">Same owner</button>':'')+'<button class="btn sm" type="button" data-cp="'+esc(r.ip)+'">Copy IP</button></div>'+
   (nbn?'<div class="sec"><h3>Other assets in '+esc(r.roomL)+' ('+nbn+')</h3><div class="nb">'+nb.map(function(x){return '<button type="button" data-open="'+x.id+'"><span class="mono">'+esc(x.ip)+'</span><span>'+esc(x.host||x.type||x.an||'')+'</span><span class="dim">'+esc(x.type||x.state||'')+'</span></button>'}).join('')+(nbn>nb.length?'<span class="dim" style="padding:3px 4px">+'+(nbn-nb.length)+' more, use the room filter</span>':'')+'</div></div>':'')+
   (all.length?'<div class="sec"><details><summary>All '+all.length+' runZero fields for this asset</summary><div class="allf">'+dl(all)+'</div></details></div>':'');
  $('dr').hidden=false;$('ovl').hidden=false;$('dr').scrollTop=0;$('dr').dataset.open=id;
}).catch(fail)}
function closeAll(){$('dr').hidden=true;$('ovl').hidden=true;$('imp').hidden=true;$('about').hidden=true}

/* ---------- CSV export ---------- */
function copyCsv(){
  fetch('/api/export.csv?'+params()).then(function(r){if(!r.ok)throw new Error('Export failed');return r.text()}).then(function(csv){
    function fb(){var c=$('cp');c.style.display='block';var t=$('cpt');t.value=csv;t.focus();t.select()}
    try{navigator.clipboard.writeText(csv).then(function(){$('cp').style.display='none';toast(fmt(COUNT)+' rows copied')},fb)}catch(e){fb()}
  }).catch(fail)}
function downloadCsv(){location.href='/api/export.csv?'+params({download:1})}

/* ---------- column menu ---------- */
function colMenu(){
  var w=$('colmenu-w'),ex=document.getElementById('colp');if(ex){ex.remove();$('colbtn').setAttribute('aria-expanded','false');return}
  var base=COLDEFS.filter(function(c){return !c.extra}),extra=COLDEFS.filter(function(c){return c.extra});
  var p=document.createElement('div');p.className='menu-p';p.id='colp';
  p.innerHTML='<h3>Core fields</h3>'+base.map(function(c){return '<label><input type="checkbox" data-col="'+esc(c.k)+'"'+(COLSEL[c.k]?' checked':'')+'> '+esc(c.l)+'</label>'}).join('')+(extra.length?'<h3>Other runZero columns ('+extra.length+')</h3>'+extra.map(function(c){return '<label><input type="checkbox" data-col="'+esc(c.k)+'"'+(COLSEL[c.k]?' checked':'')+'> '+esc(c.l)+'</label>'}).join(''):'');
  w.appendChild(p);$('colbtn').setAttribute('aria-expanded','true')}

/* ---------- rebuild after import ---------- */
function afterRebuild(m,msg){
  setupMeta(m);
  Object.keys(SEL).forEach(function(k){SEL[k]=new Set()});SEARCH='';$('q').value='';SHOWN=PAGE;
  closeAll();buildFacets();renderSrcs();showEmpty();if(!m.empty)render();toast(msg);
}

/* ---------- import dialog ---------- */
var IMP={step:1,rz:null,bc:null,res:null,busy:false,msg:'',save:null};
function ok(x){return x&&!x.err}
function renderImp(){
  var st=IMP.step,labels=['Choose files','Map columns','Review and build'];
  $('steps').innerHTML=labels.map(function(l,i){var n=i+1;return '<li class="'+(n===st?'on':n<st?'done':'')+'"><b>'+(n<st?'✓':n)+'</b>'+l+'</li>'}).join('');
  var b=$('imp-b'),next=$('imp-next'),back=$('imp-back');$('imp-msg').textContent=IMP.msg||'';
  back.hidden=st===1;next.disabled=IMP.busy;
  if(IMP.busy){b.innerHTML='<div class="busy"><span class="spin"></span>Processing…</div>';return}
  if(st===1){
    var curRz=META.sources.rz,curBc=META.sources.bc;
    b.innerHTML='<p class="dim" style="margin-top:0">Upload a fresh <b>runZero asset export</b> (CSV) after each scan run. Optionally upload a new <b>BlueCat export</b> too (the CSV from the export script). Files are uploaded to this explorer\'s server and replace the previous file of the same kind.</p><div class="two">'+
     '<div class="card"><h3>runZero asset export</h3><p>Assets CSV with any columns. IP address is the key used to match BlueCat.</p><label class="dz" id="dz-rz" for="f-rz"><span><b>Drop the CSV here</b><br>or click to choose a file</span></label><input class="vh" id="f-rz" type="file" accept=".csv,text/csv,text/plain">'+
       '<div class="cur">'+(IMP.rz?(IMP.rz.err?'<div class="err">'+esc(IMP.rz.name)+': '+esc(IMP.rz.err)+'</div>':'<span class="ok">Ready:</span> '+esc(IMP.rz.name)+' · '+fmt(IMP.rz.rows)+' rows · '+IMP.rz.headers.length+' columns'):'Current: '+(curRz?esc(curRz.name)+' · '+fmt(curRz.rows)+' rows'+(curRz.builtin?' (built-in sample)':''):'none loaded'))+'</div>'+
       (curRz&&curRz.canReset&&!IMP.rz?'<p style="margin-top:8px"><button class="btn sm" type="button" data-reset="rz">Go back to the built-in sample</button></p>':'')+'</div>'+
     '<div class="card"><h3>BlueCat export</h3><p>CSV with IP Address, Room, Owner, State, Subnet columns. Adds rooms, owners and subnets to the scan results.</p><label class="dz" id="dz-bc" for="f-bc"><span><b>Drop the CSV here</b><br>or click to choose a file</span></label><input class="vh" id="f-bc" type="file" accept=".csv,text/csv,text/plain">'+
       '<div class="cur">'+(IMP.bc?(IMP.bc.err?'<div class="err">'+esc(IMP.bc.name)+': '+esc(IMP.bc.err)+'</div>':'<span class="ok">Ready:</span> '+esc(IMP.bc.name)+' · '+fmt(IMP.bc.count)+' addresses'):'Current: '+(curBc?esc(curBc.name)+' · '+fmt(curBc.rows)+' addresses'+(curBc.builtin?' (built-in)':''):'none loaded'))+'</div>'+
       (curBc&&curBc.canReset&&!IMP.bc?'<p style="margin-top:8px"><button class="btn sm" type="button" data-reset="bc">Go back to the built-in export</button></p>':'')+'</div></div>';
    next.textContent=ok(IMP.rz)?'Next: map columns':(ok(IMP.bc)?'Next: review':'Choose a file');next.disabled=!(ok(IMP.rz)||ok(IMP.bc));
  }else if(st===2){
    var r=IMP.rz,mp=r.map,ipOk=mp.ip>=0,s=r.ipStats,RZF=META.rzFields;
    var auto=0;RZF.forEach(function(f){if(mp[f.k]>=0)auto++});
    b.innerHTML='<div class="stats"><div class="kpi"><div class="v">'+fmt(r.rows)+'</div><div class="l">Rows</div></div><div class="kpi"><div class="v">'+r.headers.length+'</div><div class="l">Columns found</div></div><div class="kpi"><div class="v">'+auto+' <small>of '+RZF.length+'</small></div><div class="l">Fields mapped</div></div><div class="kpi"><div class="v">'+fmt(s.valid)+' <small>valid IPv4</small></div><div class="l">'+fmt(s.noIp)+' without IP · '+fmt(s.dup)+' duplicate</div></div></div>'+
     '<p class="dim" style="margin-top:0">Fields were matched by column name. Change any that look wrong. Columns not used here stay available in the table column picker and in each asset\'s detail panel.</p><div class="mapg">'+
     RZF.map(function(f){var c=mp[f.k],sample=c>=0?(r.samples[c]||''):'';
       return '<div class="mrow"><label for="m-'+f.k+'">'+esc(f.l)+(f.req?' *':'')+'<span class="tg '+(c>=0?'a':(f.req?'m':''))+'">'+(c>=0?'mapped':(f.req?'required':'not in file'))+'</span></label><select id="m-'+f.k+'" data-map="'+f.k+'"><option value="-1">Not in file</option>'+r.headers.map(function(h,i){return '<option value="'+i+'"'+(i===c?' selected':'')+'>'+esc(h)+'</option>'}).join('')+'</select><div class="sm">'+(c>=0?'e.g. '+esc(sample.slice(0,80)):'')+'</div></div>'}).join('')+'</div>'+
     (ipOk?'':'<div class="warn err"><b>Pick the column that holds each asset\'s IP address</b> to continue.</div>');
    next.textContent='Next: review';next.disabled=!ipOk;
  }else{
    var t=IMP.res.stats;
    b.innerHTML='<div class="stats">'+[[fmt(t.byIp+t.byExtra+t.byMac),'Matched to BlueCat'],[fmt(t.rzOnly),'runZero only'],[fmt(t.bcOnly),'BlueCat only'],[fmt(IMP.res.total),'Total assets']].map(function(x){return '<div class="kpi"><div class="v">'+x[0]+'</div><div class="l">'+x[1]+'</div></div>'}).join('')+'</div>'+
     '<div class="card"><h3>How matching worked</h3><p style="margin:0">'+fmt(t.byIp)+' assets matched on IP address'+(t.byExtra?', '+fmt(t.byExtra)+' on a secondary IP':'')+(t.byMac?', '+fmt(t.byMac)+' on MAC address':'')+'. Each match brings the BlueCat room, owner, subnet and state onto the runZero asset. Assets only in runZero get their building from the BlueCat subnet they sit in, or from a runZero location tag when no subnet is known.</p></div>'+
     (t.noIp?'<div class="warn"><b>'+fmt(t.noIp)+'</b> runZero rows have no IP address. They are kept but cannot be matched.</div>':'')+
     (t.badIp?'<div class="warn"><b>'+fmt(t.badIp)+'</b> rows have an IP value that is not IPv4.</div>':'')+
     (t.dupIp?'<div class="warn"><b>'+fmt(t.dupIp)+'</b> runZero rows repeat an IP already in the file. Both are shown and flagged.</div>':'')+
     (t.noLoc?'<div class="warn"><b>'+fmt(t.noLoc)+'</b> runZero-only assets sit outside every BlueCat subnet, so their building comes from tags or stays Other / unknown.</div>':'');
    next.textContent='Build explorer';next.disabled=false;
  }
}
function openImp(){IMP={step:1,rz:null,bc:null,res:null,busy:false,msg:'',save:null};renderImp();$('imp').hidden=false;$('ovl').hidden=false}
function impIds(){return {rz:ok(IMP.rz)?IMP.rz.uploadId:null,bc:ok(IMP.bc)?IMP.bc.uploadId:null}}
function impFail(e){IMP.busy=false;IMP.msg=e&&e.message?e.message:'Could not reach the server';renderImp()}
function takeFile(kind,file){
  if(!file)return;IMP.busy=true;IMP.msg='';renderImp();
  var fd=new FormData();fd.append('kind',kind);fd.append('file',file);
  api('/api/import/upload',{method:'POST',body:fd}).then(function(d){IMP[kind]=d.error?{name:d.name||file.name,err:d.error}:d;IMP.busy=false;renderImp()})
   .catch(function(e){IMP.busy=false;IMP[kind]={name:file.name,err:e.message||'Could not read this file.'};renderImp()});
}
function setMap(k,v){
  IMP.rz.map[k]=v;renderImp();
  var id=IMP.rz.uploadId,map=IMP.rz.map;
  IMP.save=send('/api/import/'+id+'/map','PUT',{map:map}).then(function(d){if(IMP.rz&&IMP.rz.uploadId===id&&IMP.rz.map===map){IMP.rz.ipStats=d.ipStats;if(IMP.step===2&&!IMP.busy)renderImp()}});
}
function impNext(){
  if(IMP.step===1){
    if(ok(IMP.rz)){IMP.step=2;renderImp();return}
    if(ok(IMP.bc)){runDry();return}
  }else if(IMP.step===2){runDry();return}
  else if(IMP.step===3){applyImp()}
}
function runDry(){IMP.busy=true;IMP.msg='';renderImp();
  (IMP.save||Promise.resolve()).then(function(){return send('/api/import/preview','POST',impIds())})
   .then(function(d){IMP.res=d;IMP.step=3;IMP.busy=false;renderImp()}).catch(impFail)}
function applyImp(){IMP.busy=true;renderImp();
  send('/api/import/commit','POST',impIds()).then(function(m){IMP.busy=false;afterRebuild(m,'Explorer rebuilt: '+fmt(m.total)+' assets')}).catch(impFail)}
function resetKind(kind){IMP.busy=true;renderImp();
  send('/api/import/reset/'+kind,'POST').then(function(m){IMP.busy=false;afterRebuild(m,'Back to the built-in '+(kind==='rz'?'runZero sample':'BlueCat export'))}).catch(impFail)}

/* ---------- light / dark ---------- */
var DARKQ=window.matchMedia('(prefers-color-scheme: dark)');
var SUN='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
var MOON='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
function theme(){return document.documentElement.getAttribute('data-theme')||(DARKQ.matches?'dark':'light')}
function syncTheme(){var dark=theme()==='dark',b=$('theme'),l=dark?'Switch to light mode':'Switch to dark mode';b.innerHTML=dark?SUN:MOON;b.title=l;b.setAttribute('aria-label',l)}
function toggleTheme(){var next=theme()==='dark'?'light':'dark';document.documentElement.setAttribute('data-theme',next);try{localStorage.setItem('ipx-theme',next)}catch(e){}syncTheme()}

/* ---------- events ---------- */
document.addEventListener('click',function(e){
  var t=e.target;
  var fh=t.closest('[data-fh]');if(fh){var k=fh.dataset.fh;OPEN[k]=!OPEN[k];fh.parentNode.classList.toggle('open',OPEN[k]);fh.setAttribute('aria-expanded',String(OPEN[k]));return}
  var mo=t.closest('[data-more]');if(mo){var k2=mo.dataset.more;FMORE[k2]=!FMORE[k2];loadFacet(k2);return}
  var f=t.closest('[data-f]');if(f){toggleSel(f.dataset.f,f.dataset.v);return}
  var x=t.closest('[data-x]');if(x){if(x.dataset.x==='__q'){SEARCH='';$('q').value='';SHOWN=PAGE;render()}else toggleSel(x.dataset.x,x.dataset.v);return}
  var kf=t.closest('[data-kf]');if(kf){var p=kf.dataset.kf.split('|');SEL[p[0]]=new Set([p[1]]);SHOWN=PAGE;render();return}
  var s=t.closest('th[data-s]');if(s){var key=s.dataset.s;if(SORT.k===key)SORT.d=-SORT.d;else{SORT={k:key,d:1}}SHOWN=PAGE;$('tw').scrollTop=0;loadRows(false);return}
  var tr=t.closest('tbody tr[data-id]');if(tr){openDrawer(+tr.dataset.id);return}
  var op=t.closest('[data-open]');if(op){openDrawer(+op.dataset.open);return}
  var q=t.closest('[data-q]');if(q){SEL[q.dataset.q]=new Set([q.dataset.v]);SHOWN=PAGE;VIEW='table';closeAll();render();return}
  var cp=t.closest('[data-cp]');if(cp){try{navigator.clipboard.writeText(cp.dataset.cp).then(function(){toast('Copied '+cp.dataset.cp)},function(){toast('Select the IP and copy it manually')})}catch(er){toast('Select the IP and copy it manually')}return}
  var pg=t.closest('[data-pg]');if(pg){SHOWN=pg.dataset.pg==='all'?COUNT:SHOWN+PAGE;loadRows(true);return}
  var cl=t.closest('[data-close]');if(cl){closeAll();return}
  var rs=t.closest('[data-reset]');if(rs){resetKind(rs.dataset.reset);return}
  var hc=t.closest('.hint code');if(hc){$('q').value=hc.textContent;SEARCH=hc.textContent;SHOWN=PAGE;render();return}
  var vw=t.closest('[data-view]');if(vw){VIEW=vw.dataset.view;render();return}
  if(t===$('ovl')){closeAll();return}
  if(!t.closest('.menu')){var cpn=document.getElementById('colp');if(cpn){cpn.remove();$('colbtn').setAttribute('aria-expanded','false')}}
});
document.addEventListener('keydown',function(e){
  if(e.key==='Escape'){closeAll();return}
  if((e.key==='Enter'||e.key===' ')&&e.target.matches('th[data-s]')){e.preventDefault();e.target.click()}});
var FT={};
document.addEventListener('input',function(e){
  var t=e.target;
  if(t.matches('[data-fs]')){var k=t.dataset.fs;FQ[k]=t.value;clearTimeout(FT[k]);FT[k]=setTimeout(function(){loadFacet(k)},150)}});
document.addEventListener('change',function(e){
  var t=e.target;
  if(t.matches('[data-col]')){COLSEL[t.dataset.col]=t.checked;loadRows(false);return}
  if(t.matches('[data-map]')){setMap(t.dataset.map,+t.value);return}
  if(t.id==='f-rz'){takeFile('rz',t.files[0]);return}
  if(t.id==='f-bc'){takeFile('bc',t.files[0])}});
['dragover','dragleave','drop'].forEach(function(ev){document.addEventListener(ev,function(e){var z=e.target.closest&&e.target.closest('.dz');if(!z)return;e.preventDefault();
  if(ev==='dragover')z.classList.add('over');else z.classList.remove('over');
  if(ev==='drop'){var fl=e.dataTransfer&&e.dataTransfer.files[0];takeFile(z.id==='dz-rz'?'rz':'bc',fl)}})});
var qt;$('q').addEventListener('input',function(e){clearTimeout(qt);var v=e.target.value;qt=setTimeout(function(){SEARCH=v.trim();SHOWN=PAGE;render()},140)});
$('load').addEventListener('click',openImp);$('load2').addEventListener('click',openImp);$('imp-x').addEventListener('click',closeAll);
$('imp-next').addEventListener('click',impNext);
$('imp-back').addEventListener('click',function(){if(IMP.step>1){IMP.step=IMP.step===3&&!ok(IMP.rz)?1:IMP.step-1;IMP.msg='';renderImp()}});
$('clr').addEventListener('click',function(){Object.keys(SEL).forEach(function(k){SEL[k]=new Set()});SHOWN=PAGE;render()});
$('reset').addEventListener('click',function(){Object.keys(SEL).forEach(function(k){SEL[k]=new Set()});SEARCH='';$('q').value='';SHOWN=PAGE;render()});
$('copy').addEventListener('click',copyCsv);
$('download').addEventListener('click',downloadCsv);
$('colbtn').addEventListener('click',function(e){e.stopPropagation();colMenu()});
$('dens').addEventListener('click',function(){var on=$('dens').getAttribute('aria-pressed')!=='true';$('dens').setAttribute('aria-pressed',String(on));$('tbl').classList.toggle('dense',on)});
$('chtog').addEventListener('click',function(){SHOWCH=!SHOWCH;$('chtog').setAttribute('aria-pressed',String(SHOWCH));try{localStorage.setItem('ipx-charts',SHOWCH?'on':'off')}catch(e){}if(SHOWCH)render();else{renderCharts(null);setTop()}});
$('sidetog').addEventListener('click',function(){$('side').classList.toggle('open')});
$('theme').addEventListener('click',toggleTheme);
$('aboutbtn').addEventListener('click',function(){$('about').hidden=false;$('ovl').hidden=false});
if(DARKQ.addEventListener)DARKQ.addEventListener('change',syncTheme);
syncTheme();
window.addEventListener('resize',setTop);

/* ---------- init ---------- */
try{SHOWCH=localStorage.getItem('ipx-charts')!=='off'}catch(e){}
$('chtog').setAttribute('aria-pressed',String(SHOWCH));
api('/api/meta').then(function(m){setupMeta(m);buildFacets();renderSrcs();showEmpty();if(!m.empty)render();else setTop()}).catch(fail);
})();
