// Pedigree SVG from /api/family/{id}. `hl` is a Map individualId -> [qualifying values].
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function pedigreeSVG(fam, hl = new Map()) {
  const M = fam.members;
  const gens = [...new Set(M.map(m => m.generation))].sort((a, b) => a - b);
  const pos = {}, W = 84, H = 110, R = 16;
  // couples: parents that share children
  const couples = {}; M.forEach(m => { if (m.father || m.mother) { const k = `${m.father || '?'}|${m.mother || '?'}`; (couples[k] ||= {f: m.father, m: m.mother, kids: []}).kids.push(m.id); } });
  function isPartner(a, b) { return Object.values(couples).some(c => (c.f === a.id && c.m === b.id) || (c.m === a.id && c.f === b.id)); }
  gens.forEach(g => {
    const row = M.filter(m => m.generation === g);
    const key = m => {
      const ps = [m.father, m.mother].filter(p => p && pos[p]);
      return ps.length ? ps.reduce((s, p) => s + pos[p].x, 0) / ps.length : null;
    };
    const placed = row.filter(m => key(m) != null).sort((a, b) => key(a) - key(b));
    let rest = row.filter(m => key(m) == null);
    // founders: sit next to their partner if placed, else group couples
    const order = [];
    const pull = m => rest.filter(r => isPartner(r, m)).forEach(r => { order.push(r); rest = rest.filter(x => x !== r); });
    placed.forEach(m => { order.push(m); pull(m); });
    while (rest.length) { const m = rest.shift(); order.push(m); pull(m); }
    order.forEach((m, i) => pos[m.id] = {x: (i + 1) * W, y: (gens.indexOf(g) + 1) * H - 40});
  });
  const width = Math.max(...Object.values(pos).map(p => p.x)) + W, height = gens.length * H + 20;
  let s = `<svg class="ped" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg" font-size="10" font-family="sans-serif">`;
  Object.values(couples).forEach(c => {
    const pf = pos[c.f], pm = pos[c.m];
    let mx, my;
    if (pf && pm) { s += `<line x1="${pf.x}" y1="${pf.y}" x2="${pm.x}" y2="${pm.y}" stroke="#333"/>`; mx = (pf.x + pm.x) / 2; my = pf.y; }
    else { const p = pf || pm; if (!p) return; mx = p.x; my = p.y; }
    const ky = my + H - R - 22;
    const kids = c.kids.map(k => pos[k]).filter(Boolean);
    if (!kids.length) return;
    const xs = kids.map(k => k.x);
    s += `<line x1="${mx}" y1="${my}" x2="${mx}" y2="${ky}" stroke="#333"/><line x1="${Math.min(...xs, mx)}" y1="${ky}" x2="${Math.max(...xs, mx)}" y2="${ky}" stroke="#333"/>`;
    kids.forEach(k => s += `<line x1="${k.x}" y1="${ky}" x2="${k.x}" y2="${k.y - R}" stroke="#333"/>`);
  });
  M.forEach(m => {
    const p = pos[m.id], fill = hl.has(m.id) ? 'var(--hl)' : '#fff', sw = m.is_proband ? 2.5 : 1.2;
    const shape = m.sex === 'MALE' ? `<rect x="${p.x - R}" y="${p.y - R}" width="${2 * R}" height="${2 * R}" fill="${fill}" stroke="#222" stroke-width="${sw}"/>` :
                  m.sex === 'FEMALE' ? `<circle cx="${p.x}" cy="${p.y}" r="${R}" fill="${fill}" stroke="#222" stroke-width="${sw}"/>` :
                  `<polygon points="${p.x},${p.y - R} ${p.x + R},${p.y} ${p.x},${p.y + R} ${p.x - R},${p.y}" fill="${fill}" stroke="#222" stroke-width="${sw}"/>`;
    const rec = [...m.features.map(f => (f.negated ? 'NOT ' : '') + f.label), ...m.diseases.map(d => `${d.label} [${d.evidence}]`), ...m.genes.map(g => 'gene ' + g.symbol)];
    const tip = `${m.relation} · ${m.id}${m.birth_year ? ' · b. ' + m.birth_year : ' · no DOB'}\n${rec.length ? rec.join('\n') : 'no recorded findings'}${hl.has(m.id) ? '\n\nQUALIFYING: ' + hl.get(m.id).join('; ') : ''}`;
    const dots = [m.features.some(f => !f.negated) && '#1a7f37', m.diseases.length && '#bf3989', m.genes.length && '#9a6700'].filter(Boolean)
      .map((c, i) => `<circle cx="${p.x - 8 + i * 8}" cy="${p.y}" r="3" fill="${c}"/>`).join('');
    s += `<g>${shape}${dots}${m.is_proband ? `<text x="${p.x - R - 12}" y="${p.y + 4}" font-size="12">▶</text>` : ''}` +
         `<text x="${p.x}" y="${p.y + R + 12}" text-anchor="middle" font-weight="${hl.has(m.id) ? 700 : 400}">${esc(m.relation.split(' (')[0])}</text>` +
         `<text x="${p.x}" y="${p.y + R + 23}" text-anchor="middle" fill="#656d76">${esc(m.id)}${m.birth_year ? ' · ' + m.birth_year : ''}</text>` +
         `<title>${esc(tip)}</title></g>`;
  });
  return s + '</svg>';
}
const PED_LEGEND = `<div class="legend"><i style="background:var(--hl)"></i>highlighted <i style="background:#fff"></i>other <i style="border-radius:50%"></i>female <i></i>male · ▶ proband · dots: <span style="color:#1a7f37">●</span> phenotype <span style="color:#bf3989">●</span> diagnosis <span style="color:#9a6700">●</span> gene · hover a person for their record</div>`;
