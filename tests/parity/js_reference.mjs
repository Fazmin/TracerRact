// Runs the original single-file IP Asset Explorer JS in Node (no DOM) and records reference
// output that the Python port is compared against. tests/test_parity.py runs this automatically
// when it is given the real exports; the output holds that data, so keep it out of the project.
//
//   node tests/parity/js_reference.mjs --bluecat BLUECAT.csv --runzero RUNZERO.csv --out golden.json.gz \
//        [--html "IP Asset Explorer_McMaster_2026.html"]
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import zlib from 'node:zlib';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const args = {html: path.resolve(here, '../..', 'IP Asset Explorer_McMaster_2026.html')};
for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, '')] = process.argv[i + 1];
for (const k of ['bluecat', 'runzero', 'out']) {
  if (!args[k]) { console.error(`Missing --${k}`); process.exit(2); }
}
const html = fs.readFileSync(args.html, 'utf8');
const bcCsv = fs.readFileSync(args.bluecat, 'utf8');
const rzCsv = fs.readFileSync(args.runzero, 'utf8');

// Everything from the CSV parser through the rendering helpers is pure JS; the DOM is only
// touched when render functions are called, which this harness never does.
const code = html.slice(html.indexOf('/* ---------- CSV ---------- */'), html.indexOf('/* ---------- source chips ---------- */'));

const ctx = vm.createContext({BC_CSV: bcCsv, RZ_CSV: rzCsv});
vm.runInContext('var EMB={};var $=function(){return null};function esc(s){return String(s==null?"":s)}function fmt(n){return String(n)}\n' + code, ctx);

const out = vm.runInContext(`(function(){
  var bcTb=parseCSV(BC_CSV),bc=bcFromTable(bcTb).objs;
  var rzTb=parseCSV(RZ_CSV),rzMap=autoMap(rzTb.headers);
  var res=merge(bc,rzTb,rzMap);
  R=res.recs;STATS=res.stats;prep();setExtra(rzTb.headers,rzMap);

  function kpis(){var F=FILTERED,c={Both:0,rz:0,bc:0},rooms=new Set(),hi=0,alive=0,eol=0,rzn=0;
    F.forEach(function(r){if(r.cov==='Both')c.Both++;else if(r.cov==='runZero only')c.rz++;else c.bc++;if(r.room)rooms.add(r.roomL);if(r.risk==='high'||r.risk==='critical')hi++;if(r.alive==='Yes')alive++;if(r.eol)eol++;if(r.hasRz)rzn++});
    return {total:R.length,count:F.length,both:c.Both,rzOnly:c.rz,bcOnly:c.bc,rooms:rooms.size,hi:hi,alive:alive,eol:eol,scanned:rzn}}
  function obj(a){var o={};a.forEach(function(x){o[x[0]]=x[1]});return o}
  function charts(){
    var bmap={};FILTERED.forEach(function(r){var o=bmap[r.bld]||(bmap[r.bld]={});o[r.cov]=(o[r.cov]||0)+1});
    return {cov:obj(tally(function(r){return [r.cov]})),bld:obj(tally(function(r){return [r.bld]})),bldCov:bmap,
      types:obj(tally(function(r){return r.hasRz?[r.type||'(unknown)']:[]})),risk:obj(tally(function(r){return r.hasRz?[r.risk||'(unknown)']:[]})),
      months:obj(tally(function(r){return r.seenM?[r.seenM]:[]})),ports:obj(tally(function(r){return r.tcp}))}}
  function locs(){var g={};FILTERED.forEach(function(r){var b=g[r.bld];if(!b){b=g[r.bld]={n:0,cov:{},hi:0,rooms:{},none:0,code:r.bldC}}b.n++;b.cov[r.cov]=(b.cov[r.cov]||0)+1;if(r.risk==='high'||r.risk==='critical')b.hi++;
    if(r.room)b.rooms[r.roomL]=(b.rooms[r.roomL]||0)+1;else b.none++});return g}
  function counts(){var o={};Object.keys(COUNTS).forEach(function(k){var m={};COUNTS[k].forEach(function(n,v){m[v]=n});o[k]=m});return o}
  function run(c){
    FD.forEach(function(f){SEL[f.k]=new Set((c.sel&&c.sel[f.k])||[])});
    SEARCH=c.q||'';SPASS=SEARCH?compileSearch(SEARCH):null;SORT=c.sort||{k:'n',d:1};
    compute();
    return {ids:FILTERED.map(function(r){return r.id}),counts:counts(),kpis:kpis(),charts:charts(),locations:locs()}}

  // Fixed cases plus generated ones: every facet's top value, its top two values,
  // and each paired with the coverage facet.
  var cases=[{name:'all'},
    {q:'type:printer risk:high'},{q:'room:ITB'},{q:'port:3389'},{q:'-owner:parking'},{q:'ip:130.113.0.0/16'},{q:'cov:"runZero only"'},{q:'seen:>90'},
    {q:'xerox'},{q:'130.113.3.0/24'},{q:'130.113.3.1-130.113.3.60'},{q:'printer -type:printer'},{q:'seen:<30'},{q:'seen:7'},{q:'seen:abc'},{q:'foo:bar'},
    {q:'"john hodgins"'},{q:'ip:999.1.1.1/8'},{q:'location=john'},{q:'tag:location'},{q:'mac:9c:93'},{q:'state:dhcp'},{q:'-state:static -state:dhcp'},
    {q:'sub:"jhe -"'},{q:'owner:'},{q:'-'},{q:'host:mcmaster.ca bldg:jhe'},{q:'vendor:xerox port:443'},{q:'note:printer'},{q:'Room:241'},
    {q:'130.113',sel:{cov:['Both'],risk:['high','medium']}},
    {sort:{k:'risk',d:1}},{sort:{k:'risk',d:-1}},{sort:{k:'last',d:-1}},{sort:{k:'n',d:-1}},{sort:{k:'first',d:1},sel:{cov:['Both']}},{sort:{k:'tcp',d:-1}},{sort:{k:'tagsT',d:-1}}];
  FD.forEach(function(f){SEL[f.k]=new Set()});SPASS=null;compute();
  var allCounts=counts();
  FD.forEach(function(f){var top=Object.keys(allCounts[f.k]).sort(function(a,b){return allCounts[f.k][b]-allCounts[f.k][a]||(a<b?-1:a>b?1:0)});
    if(!top.length)return;var s={};s[f.k]=[top[0]];cases.push({sel:s});
    if(top.length>1){var s2={};s2[f.k]=[top[0],top[1]];cases.push({sel:s2})}
    if(f.k!=='cov'){var s3={cov:['Both']};s3[f.k]=[top[top.length>2?2:0]];cases.push({sel:s3,q:top.length>3?'':'130'})}});
  cases.push({sel:{cov:['runZero only'],bld:['JHE · John Hodgins Engineering'],type:['Printer','(unknown)']},q:'-risk:low'});
  var results=cases.map(run);

  function strip(recs){return recs.map(function(r){var o={};Object.keys(r).forEach(function(k){if(k!=='_h'&&k!=='fv')o[k]=r[k]});return o})}
  var records=strip(R).map(function(o,i){o.fv=R[i].fv;return o});

  // Real exports rarely match on an extra IP or a MAC, so a synthetic merge covers those paths plus
  // duplicate, invalid and missing IPs. Target values are picked from the data. Edits are [row, header, value].
  var edits=null,syn=null,bcObjs=bcFromTable(parseCSV(BC_CSV)).objs,rzIps={},macN={};
  rzTb.rows.forEach(function(r){rzIps[(r[rzMap.ip]||'').trim()]=1});
  bcObjs.forEach(function(b){var m=normMac(b.mac);if(m)macN[m]=(macN[m]||0)+1});
  var free=bcObjs.filter(function(b){return !rzIps[b.ip]}),extraIp=free[0]&&free[0].ip;
  var uniq=free.filter(function(b){var m=normMac(b.mac);return m&&macN[m]===1&&b.ip!==extraIp})[0];
  var dupMac=Object.keys(macN).filter(function(m){return macN[m]>1})[0];
  if(rzTb.rows.length>=10&&extraIp&&uniq&&dupMac&&rzMap.ips2>=0&&rzMap.mac>=0&&rzMap.macs>=0){
    var H=rzTb.headers,dupIp=rzTb.rows.map(function(r){return (r[rzMap.ip]||'').trim()}).filter(function(v,i){return v&&(i<3||i>9)})[0]||'10.200.0.9';
    edits=[[8,H[rzMap.ip],'10.200.0.1'],[8,H[rzMap.ips2],'10.200.0.2 '+extraIp],
      [9,H[rzMap.ip],'10.200.0.3'],[9,H[rzMap.ips2],''],[9,H[rzMap.mac],fmtMac(normMac(uniq.mac))],[9,H[rzMap.macs],fmtMac(normMac(uniq.mac))],
      [3,H[rzMap.ip],dupIp],[4,H[rzMap.ip],'300.1.1.1'],
      [5,H[rzMap.ip],'10.200.0.4'],[5,H[rzMap.ips2],''],[5,H[rzMap.mac],fmtMac(dupMac)],[5,H[rzMap.macs],fmtMac(dupMac)],[6,H[rzMap.ip],'']];
    var synTb={headers:rzTb.headers,rows:rzTb.rows.map(function(r){return r.slice()})};
    edits.forEach(function(e){synTb.rows[e[0]][synTb.headers.indexOf(e[1])]=e[2]});
    var res2=merge(bcFromTable(parseCSV(BC_CSV)).objs,synTb,rzMap);
    syn={edits:edits,stats:res2.stats,records:strip(res2.recs.filter(function(r){return r.hasRz}))};
  }

  return JSON.stringify({stats:STATS,rzMap:rzMap,rzHeaders:rzTb.headers,
    records:records,cases:cases,results:results,
    synthetic:syn});
})()`, ctx);

const parsed = JSON.parse(out);
fs.writeFileSync(args.out, zlib.gzipSync(out));
console.log(`records=${parsed.records.length} cases=${parsed.cases.length} stats=${JSON.stringify(parsed.stats)}`);
