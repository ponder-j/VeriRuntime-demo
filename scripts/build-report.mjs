// Render the checked-in report model into the HTML-PPT template and editable drawio.
// No network or package install is needed: node scripts/build-report.mjs
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const out = path.join(root,'docs/presentation/veriruntime-report');
const m = JSON.parse(await fs.readFile(path.join(out,'report-source.json'),'utf8'));
const esc = s => String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const vars = {bg:'--bg',soft:'--bg-soft',surface:'--surface',text:'--text-1',dim:'--text-3',accent:'--accent',good:'--good',warn:'--warn',bad:'--bad',border:'--border-strong',onaccent:'--accent-ink'};
const color = key => `var(${vars[key]})`;
function text(t,extra='') {
  return `<div class="report-text ${t.mono?'report-mono':''} ${extra}" style="left:${t.x}px;top:${t.y}px;width:${t.w}px;height:${t.h}px;font-size:${t.size}px;color:${color(t.color)};font-weight:${t.bold?700:400};text-align:${t.align}">${esc(t.text)}</div>`;
}
function rect(t) {
  return `<div class="report-box" style="left:${t.x}px;top:${t.y}px;width:${t.w}px;height:${t.h}px;background:${color(t.fill)};border:1px solid ${color(t.stroke)}"></div>`;
}
function svgLine(t,id) {
  return `<polyline points="${t.points.map(p=>p.join(',')).join(' ')}" fill="none" stroke="${color(t.color??'accent')}" stroke-width="3" ${t.dashed?'stroke-dasharray="10 8"':''} ${t.arrow?`marker-end="url(#arrow-${id})"`:''}/>`;
}
function svg(t,id) {
  const def=`<defs><marker id="arrow-${id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="${color('accent')}"/></marker></defs>`;
  const edges=t.kind==='diagram'?t.edges.map(e=>svgLine({...e,arrow:true},id)).join(''):svgLine(t,id);
  const nodes=t.kind==='diagram'?t.nodes.map(n=>{
    const rows=n.text.split('\n'), lh=n.size*1.35, first=n.y+n.h/2-(rows.length-1)*lh/2+n.size*.34;
    return `<g><rect x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" fill="${color(n.fill)}" stroke="${color('border')}" stroke-width="2"/><text x="${n.x+n.w/2}" y="${first}" text-anchor="middle" style="fill:${color(n.color)};font-family:var(--font-sans);font-size:${n.size}px">${rows.map((row,i)=>`<tspan x="${n.x+n.w/2}" dy="${i?lh:0}">${esc(row)}</tspan>`).join('')}</text></g>`;
  }).join(''):'';
  return `<svg class="report-svg arch" viewBox="0 0 1920 1080" aria-label="${esc(t.id??'执行时间线')}">${def}${edges}${nodes}</svg>`;
}
function nativeTable(t) {
  return `<table class="report-table t" style="left:${t.x}px;top:${t.y}px;width:${t.w}px;height:${t.h}px;font-size:${t.size}px"><colgroup>${t.widths.map(w=>`<col style="width:${w}px">`).join('')}</colgroup><tbody>${t.values.map((r,i)=>`<tr style="height:${t.rowHeight}px">${r.map(c=>`<${i?'td':'th'}>${esc(c).replaceAll('\n','<br>')}</${i?'td':'th'}>`).join('')}</tr>`).join('')}</tbody></table>`;
}
function notes(s) {
  const highlighted=p=>esc(p).replace(/(semantic_key|NEEDS_INPUT|CONTRACT_VALID|VERIFIED|UNKNOWN|CONFLICT|ExecutionSpec|Rocq|SAFE|固定目标|权威 VC|确认要求|内容快照)/g,'<strong>$1</strong>');
  return `<aside class="notes">${s.notes.split('\n\n').map(p=>`<p>${highlighted(p)}</p>`).join('')}<p class="note-sources">来源：${esc(s.sources.join('；'))}</p></aside>`;
}
function section(s,i) {
  const header=[text({text:s.section,x:96,y:52,w:1728,h:40,size:25,color:'accent',bold:false,align:'left'},'kicker'),text({text:s.title,x:96,y:115,w:1728,h:i?87:130,size:i?64:90,color:'text',bold:true,align:'left'},i?'h2':'h1'),text({text:s.subtitle,x:100,y:i?218:250,w:1720,h:58,size:32,color:'dim',bold:false,align:'left'},'lede')].join('');
  const items=s.items.map((t,j)=>t.kind==='text'?text(t):t.kind==='box'?rect(t):t.kind==='table'?nativeTable(t):svg(t,`${i}-${j}`)).join('');
  const footer=text({text:`VeriRuntime  ·  ${s.sources.slice(0,2).join('  /  ')}`,x:100,y:1020,w:1510,h:35,size:19,color:'dim',bold:false,align:'left'},'report-footer')+text({text:`${String(i+1).padStart(2,'0')} / ${m.slides.length}`,x:1620,y:1016,w:200,h:38,size:23,color:'dim',bold:false,mono:true,align:'right'},'report-footer');
  return `<section class="slide" data-title="${esc(s.title)}">${header}${items}${footer}${notes(s)}</section>`;
}
const html=`<!DOCTYPE html>
<html lang="zh-CN" data-themes="academic-paper,corporate-clean,tokyo-night" data-theme="academic-paper" data-theme-base="assets/themes/">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(m.title)}</title>
<link rel="stylesheet" href="assets/base.css"><link rel="stylesheet" id="theme-link" href="assets/themes/academic-paper.css"><link rel="stylesheet" href="assets/animations/animations.css"><link rel="stylesheet" href="style.css"><link rel="stylesheet" href="report.css"></head>
<body class="tpl-presenter-mode-reveal"><div class="deck" data-w="1920" data-h="1080">${m.slides.map(section).join('\n')}</div><script src="assets/runtime.js"></script></body></html>`;
await fs.writeFile(path.join(out,'index.html'),html);
const css=`/* Template extension: fixed canvas, light academic technical report. */
:root { --font-sans:'Microsoft YaHei','Noto Sans SC',Arial,sans-serif; --font-serif:var(--font-sans); --font-display:var(--font-sans); --font-mono:Consolas,'Microsoft YaHei',monospace; --letter-normal:0; --letter-tight:0; }
body { font-family:var(--font-sans); }
.tpl-presenter-mode-reveal .slide { padding:0; display:block; }
.report-text { position:absolute; max-width:none!important; margin:0!important; padding:0!important; white-space:pre-wrap; overflow:visible; line-height:1.30!important; font-family:var(--font-sans)!important; letter-spacing:0!important; }
.report-mono { font-family:var(--font-mono)!important; line-height:1.28!important; }
.tpl-presenter-mode-reveal .kicker { text-transform:none; letter-spacing:0; }
.report-box { position:absolute; border-radius:var(--radius-sm); }
.report-svg { position:absolute; left:0; top:0; width:1920px; height:1080px; pointer-events:none; }
.report-table { position:absolute; table-layout:fixed; border-collapse:collapse; color:var(--text-1); line-height:1.30; margin:0; background:var(--surface); }
.report-table td,.report-table th { padding:12px 20px; text-align:left; vertical-align:middle; border:1px solid var(--border-strong); font-weight:400; }
.report-table th { color:var(--accent-ink); background:var(--accent); font-weight:700; }
@media print { @page { size:20in 11.25in; margin:0; } .slide { width:1920px!important; height:1080px!important; max-height:1080px!important; } .deck { width:1920px!important; } .report-footer { display:block!important; } }
`;
await fs.writeFile(path.join(out,'report.css'),css);

// Same graph models as the slides, with real editable vertices/edges and port positions.
const names={'architecture':'三层架构（当前实现）','physical-plan':'真实物理计划（CPA 后备未执行）','docker-isolation':'Docker 尝试隔离（当前实现）','strong-verification-proposed':'Frama-C→Rocq（设计稿，尚未实现）'};
let drawio='<mxfile host="app.diagrams.net" version="24.7.17" type="device">';
for (const s of m.slides) for (const d of s.items.filter(i=>i.kind==='diagram')) {
  const mx=[`<mxCell id="0"/><mxCell id="1" parent="0"/><mxCell id="heading" value="${esc(names[d.id])}" style="text;html=1;fontSize=48;fontFamily=Microsoft YaHei;fontColor=${m.palette.accent};align=left;" vertex="1" parent="1"><mxGeometry x="96" y="130" width="1728" height="80" as="geometry"/></mxCell>`];
  for (const n of d.nodes) mx.push(`<mxCell id="${n.id}" value="${esc(n.text.replaceAll('\n','<br>'))}" style="rounded=0;whiteSpace=wrap;html=1;fontSize=${n.size};fontFamily=Microsoft YaHei;fontColor=${m.palette[n.color]};fillColor=${m.palette[n.fill]};strokeColor=${m.palette.border};spacing=12;" vertex="1" parent="1"><mxGeometry x="${n.x}" y="${n.y}" width="${n.w}" height="${n.h}" as="geometry"/></mxCell>`);
  d.edges.forEach((e,i)=>{
    const from=d.nodes.find(n=>n.id===e.from),to=d.nodes.find(n=>n.id===e.to),p=e.points[0],q=e.points.at(-1);
    const ports=`exitX=${(p[0]-from.x)/from.w};exitY=${(p[1]-from.y)/from.h};entryX=${(q[0]-to.x)/to.w};entryY=${(q[1]-to.y)/to.h};exitPerimeter=0;entryPerimeter=0;`;
    mx.push(`<mxCell id="edge${i}" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;endFill=1;strokeWidth=3;strokeColor=${m.palette.accent};${e.dashed?'dashed=1;':''}${ports}" edge="1" source="${e.from}" target="${e.to}" parent="1"><mxGeometry relative="1" as="geometry"><Array as="points">${e.points.slice(1,-1).map(p=>`<mxPoint x="${p[0]}" y="${p[1]}"/>`).join('')}</Array></mxGeometry></mxCell>`);
  });
  drawio+=`<diagram id="${d.id}" name="${esc(names[d.id])}"><mxGraphModel dx="1920" dy="1080" grid="1" gridSize="10" page="1" pageScale="1" pageWidth="1920" pageHeight="1080"><root>${mx.join('')}</root></mxGraphModel></diagram>`;
}
drawio+='</mxfile>';
await fs.writeFile(path.join(out,'VeriRuntime-architecture.drawio'),drawio);
console.log(`Rendered ${m.slides.length} slides + 4 editable drawio pages.`);
