import {heading, panel} from '../components/common/heading.js';
import {entries, records} from '../components/common/records.js';
import {topic} from '../constants/project.js';
import {date, el, esc} from '../utils/dom.js';
import {submit} from '../hooks/submit.js';

function segments(items) {
  if (!Array.isArray(items) || !items.length) return '<span class="note">—</span>';
  return items.map(x=>`<span class="slot"><time>${esc(x.时段)}</time><small>${esc(x.授权)}</small></span>`).join('');
}

function breaks(items) {
  if (!Array.isArray(items) || !items.length) return '<span class="ok">无断点</span>';
  return items.map(x=>`<span class="slot gap"><time>${esc(x.时段)}</time><small>缺 ${esc(x.分钟)} 分钟</small></span>`).join('');
}

function coverageCard(row) {
  return `<article class="coverage" data-state="${row.结论 === '完整覆盖' ? 'whole' : 'gap'}">
    <header><h3>${esc(row.片名)} <small>${esc(row.场次编号)} · ${esc(row.地域)} · ${esc(row.媒介)}</small></h3>
    <span class="status">${esc(row.结论)}</span></header>
    <dl><dt>场次时段</dt><dd>${esc(row.场次时段)}</dd>
    <dt>覆盖分段</dt><dd>${segments(row.覆盖分段)}</dd>
    <dt>断点</dt><dd>${breaks(row.断点)}</dd>
    <dt>未授权分钟</dt><dd>${esc(row.未授权分钟)}</dd>
    <dt>合同版本</dt><dd>${esc(row.合同版本)}</dd>
    <dt>覆盖依据</dt><dd class="chain">${esc(row.覆盖依据)}</dd></dl></article>`;
}

function section(spec) {
  if (spec.kind === 'coverage') {
    return panel(spec.title, `<div class="coverage-grid">${spec.rows.map(coverageCard).join('')}</div>`);
  }
  return panel(spec.title, entries(spec.rows));
}

export function evidencePage(state, identity) {
  const current = state.evidence.find(row=>row.id===identity) || state.evidence[0];
  if (!current) return heading(topic.modules[3]) + '<p class="empty">执行完成后，可查看、复核和导出证据。</p>';
  const {report} = current;
  const metrics = Object.entries(report.metrics).map(([label,value])=>`<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('');
  const sections = report.sections.map(section).join('');
  const review = current.reviewer ? `<p data-testid="reviewed">${esc(current.reviewer)} · ${esc(date(current.reviewed_at))}</p><p>${esc(current.review_note)}</p>`
    : state.session.role==='reviewer'?'<form id="review-form"><label>复核意见<textarea name="note" maxlength="500" required></textarea></label><button>签署复核</button></form>'
    : '<p class="note">待复核员签署</p>';
  const history = state.evidence.map(row=>[`<a href="#evidence/${row.id}">执行 ${row.run_id}</a>`, esc(row.report.summary), esc(row.reviewer || '待复核')]);
  return `${heading(topic.modules[3], report.summary, `<button id="download" data-id="${current.id}">导出证据</button>`)}
    <div class="spread evidence-top"><dl class="metrics">${metrics}</dl><aside class="brief"><h2>复核签署</h2>${review}<details><summary>结果校验码</summary><code class="digest">${esc(current.digest)}</code></details></aside></div>
    ${sections}<details><summary>历史证据</summary>${records(['执行','结论','复核'], history)}</details>`;
}

export function bindEvidence(api, update, fail) {
  const button = document.getElementById('download');
  if (!button) return;
  const identity = button.dataset.id;
  button.addEventListener('click', async()=>{
    try {
      const response = await api(`/evidence/${identity}/download`, {response:true});
      const url = URL.createObjectURL(await response.blob());
      const link = el('a');
      link.href = url;
      link.download = `evidence-${identity}.json`;
      link.click();
      setTimeout(()=>URL.revokeObjectURL(url), 1000);
    } catch (error) { fail(error); }
  });
  submit('review-form', async data=>{
    await api(`/evidence/${identity}/review`, {method:'POST', data:{note:data.get('note')}});
    await update();
  }, fail);
}
