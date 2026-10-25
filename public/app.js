'use strict';
const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (x) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
const num = (value, digits = 2) => value == null ? '—' : Number(value).toLocaleString('zh-CN', {minimumFractionDigits:digits,maximumFractionDigits:digits});
const pct = (value) => value == null ? '—' : `${value > 0 ? '+' : ''}${num(value)}%`;
const time = (value) => value ? value.slice(0,16).replace('T',' ') : '尚无记录';
const dateLabel = (date, year = true) => `${year ? Number(date.slice(0,4)) + '年' : ''}${Number(date.slice(5,7))}月${Number(date.slice(8,10))}日`;
const weekdays = ['周日','周一','周二','周三','周四','周五','周六'];
let data, month, selected, phase = 'close', mode = 'full', view = 'today', toastTimer, refreshPending = false;

function record(date = selected, slot = phase) { return data.records.find((r) => r.date === date && r.phase === slot); }
function stateFor(score) { return score == null ? 'missing' : score < 40 ? 'defense' : score < 55 ? 'observe' : score < 70 ? 'trial' : 'advance'; }
function shownValue(r) { return mode === 'full' ? r?.score : r?.quantitative?.score; }
function stateName(state) { return (mode === 'full' ? {defense:'防守',observe:'观察',trial:'试仓',advance:'进攻',missing:'待补评'} : {defense:'偏弱',observe:'修复中',trial:'转暖',advance:'偏强',missing:'无数据'})[state]; }
function scoreState(r) { return mode === 'full' ? r?.state || 'missing' : stateFor(shownValue(r)); }
function gaps(r) { return r?.missing || r?.dimensions?.filter(d=>d.score == null) || []; }
function dayAction(r) {
  if(!r) return '未记录';
  if(!r.complete) return `缺${gaps(r).length}项`;
  return r.actions.add === '可分批评估' ? '加仓候选' : r.state === 'defense' ? '减仓检查' : r.state === 'observe' ? '暂缓开仓' : '小仓试探';
}
function sourceLink(source, text) {
  const valid = /^https?:\/\//.test(source.url || '');
  return valid ? `<a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(text || source.title)} ↗</a>` : esc(text || source.title);
}
function months() {
  const start = data.history_start.slice(0,7), end = data.today.slice(0,7), result = [];
  let [y,m] = end.split('-').map(Number);
  while (`${y}-${String(m).padStart(2,'0')}` >= start) {
    result.push(`${y}-${String(m).padStart(2,'0')}`);
    if (--m === 0) {m = 12; y--;}
  }
  return result;
}
function monthDates() {
  const [y,m] = month.split('-').map(Number);
  return Array.from({length:new Date(y,m,0).getDate()}, (_,i) => `${month}-${String(i+1).padStart(2,'0')}`);
}
function latestRecord() { return data.records[data.records.length-1]; }
function goToday() {
  const latest = latestRecord();
  selected = latest?.date || data.today;
  phase = latest?.phase || 'close';
  month = selected.slice(0,7);
  view = 'today'; render();
}
function chooseMonth(value) {
  month = value; view = 'history';
  const candidates = data.records.filter((r) => r.date.startsWith(month) && r.phase === phase);
  selected = candidates.at(-1)?.date || monthDates().find((d) => data.trade_days.includes(d) && d >= data.history_start && d <= data.today) || `${month}-01`;
  render();
}
function renderRail() {
  let year = '';
  $('#months').innerHTML = months().map((m,i,a) => {
    const y = m.slice(0,4), count = new Set(data.records.filter(r=>r.date.startsWith(m)).map(r=>r.date)).size;
    const prefix = y !== year ? `${i ? '</div>' : ''}<div class="year-group"><div class="year-label"><span>⌄</span>${y}年</div>` : '';
    year = y;
    return `${prefix}<button class="month-button ${month === m ? 'active' : ''}" data-month="${m}" data-year-short="${y.slice(2)}/" aria-pressed="${month === m}">${Number(m.slice(5))}月<i>${count}日</i></button>${i === a.length-1 ? '</div>' : ''}`;
  }).join('');
  const closes = data.records.filter(r=>r.phase === 'close'), complete = closes.filter(r=>r.complete).length;
  $('#history-coverage').innerHTML = `<b>${data.coverage.trading_days} 个交易日</b>${complete} 日八维收盘评分<br>${closes.length-complete} 日八维缺项<br>历史从 ${data.history_start.replaceAll('-','.')} 起`;
}
function renderSummary() {
  const r = record(), quant = r?.quantitative;
  const label = r?.complete ? r.title : r?.data_conflicts?.length ? '来源数据冲突，暂停综合判断' : r ? '综合判断待补全' : '该时段尚无记录';
  const weekday = weekdays[new Date(selected + 'T12:00:00').getDay()];
  $('#summary').className = `panel summary ${r?.state || 'missing'}`;
  $('#summary').innerHTML = `<div class="summary-top"><div class="selected-date">${dateLabel(selected)} · ${weekday}<small>${selected === data.today ? '今日' : view === 'today' ? '最近交易日' : '历史'}</small></div><div class="phase-switch"><button data-phase="midday" class="${phase === 'midday' ? 'active' : ''}" aria-pressed="${phase === 'midday'}">午间</button><button data-phase="close" class="${phase === 'close' ? 'active' : ''}" aria-pressed="${phase === 'close'}">收盘</button></div></div>
    <div class="hero-content"><div class="hero-score"><strong>${r?.score ?? '—'}</strong><span>/100</span></div><div class="hero-copy"><h2>${esc(label)}</h2><p>${r?.review_model === 'history-news-v1' ? `历史规则回评 · 当日18:00资料${r.complete ? '' : ' · 缺' + gaps(r).length + '项'}` : r?.complete ? '八维综合评估 · 加仓前逐项确认条件' : r ? '已取得真实行情，缺失维度保留未评估' : phase === 'midday' ? '不以收盘数据补造历史午间判断' : '该日没有可核验的收盘记录'}</p></div></div>
    <div class="hero-bottom"><span>八维综合 · ${r?.coverage ?? 0}/100 权重已有证据</span><b>量价参考 ${quant?.score ?? '—'}/100 ${phase === 'midday' ? '· 仅趋势' : ''}</b></div>
    <div class="compact-actions"><div><span>开仓</span><b>${esc(r?.actions.open || '等待数据')}</b></div><div><span>加仓</span><b>${esc(r?.actions.add || '等待数据')}</b></div><div><span>减仓</span><b>${esc(r?.actions.reduce || '检查条件')}</b></div></div>`;
  $('#market-strip').innerHTML = ['sh','sz','cyb'].map(k=>{
    const q = r?.quotes[k];
    return `<div class="market-stat"><label>${({sh:'上证指数',sz:'深证成指',cyb:'创业板指'})[k]}</label><strong>${num(q?.close)}</strong><small class="${(q?.change_pct ?? 0) >= 0 ? 'rise' : 'fall'}">${pct(q?.change_pct)}</small></div>`;
  }).join('') + `<div class="market-stat"><label>沪深成交额${phase === 'midday' ? ' · 半日' : ''}</label><strong>${quant ? num(quant.turnover / 1e12) + '<small style="display:inline"> 万亿</small>' : '—'}</strong><small class="${(quant?.turnover_change ?? 0) >= 0 ? 'rise' : 'fall'}">${quant?.turnover_change != null ? '较前日 ' + pct(quant.turnover_change) : '不与全天比较'}</small></div>`;
}
function renderCalendar() {
  const all = months(), index = all.indexOf(month);
  $('#month-label').textContent = `${month.slice(0,4)}年${Number(month.slice(5))}月`;
  $('#prev-month').disabled = index === all.length-1;
  $('#next-month').disabled = index <= 0;
  document.querySelectorAll('[data-mode]').forEach(b=>{b.classList.toggle('active',b.dataset.mode === mode);b.setAttribute('aria-pressed',b.dataset.mode === mode);});
  $('#calendar-legend').innerHTML = ['defense','observe','trial','advance','missing'].map(s=>`<span><i class="legend-dot ${s}"></i>${stateName(s)}</span>`).join('');
  const observed = data.records.filter(r=>r.date.startsWith(month) && r.phase === phase);
  const complete = observed.filter(r=>r.complete), missing = observed.filter(r=>!r.complete);
  const expected = data.trade_days.filter(d=>d.startsWith(month) && d>=data.history_start && d<=data.today);
  const unrecorded = expected.filter(d=>!record(d));
  const uncertain = complete.filter(r=>r.conditions.some(c=>c.status==='unknown'));
  const missingCount = missing.length + unrecorded.length;
  $('#month-coverage').innerHTML = `<span><b>${complete.length}/${expected.length}</b> 日八维已评</span><span><b>${complete.filter(r=>['trial','advance'].includes(r.state)).length}</b> 日可条件试仓</span><span><b>${complete.filter(r=>r.actions.add==='可分批评估').length}</b> 日加仓候选</span><span class="${missingCount ? 'has-gap' : ''}"><b>${missingCount}</b> 日资料不全</span>`;
  const dates = monthDates(), leading = (new Date(dates[0] + 'T12:00:00').getDay()+6)%7;
  let html = '<div class="day empty"></div>'.repeat(leading);
  html += dates.map(d=>{
    const r = record(d), value = shownValue(r), future = d > data.today, outside = d < data.history_start;
    const trading = data.trade_days.includes(d);
    const weekend = [0,6].includes(new Date(d + 'T12:00:00').getDay());
    const rest = !trading && (weekend || d < data.calendar_end);
    let state = outside ? 'outside' : rest ? 'rest' : future ? 'future' : scoreState(r);
    let content = outside ? '<small>范围外</small>' : rest ? '<small>休市</small>' : future ? '<small>未开盘</small>' : value != null ? `<strong>${value}</strong><small>${mode === 'full' ? dayAction(r) : stateName(state)}</small>` : `<strong>—</strong><small>${mode === 'full' && r ? dayAction(r) : '未记录'}</small>`;
    const detail = r ? r.complete ? `综合${r.score}分；开仓：${r.actions.open}；加仓：${r.actions.add}；减仓：${r.actions.reduce}` : `缺少：${gaps(r).map(d=>d.label).join('、')}` : '未记录';
    return `<button class="day ${state} ${selected === d ? 'selected' : ''} ${d === data.today ? 'today' : ''}" data-date="${d}" title="${esc(detail)}" ${outside || rest || future ? 'disabled' : ''} aria-label="${dateLabel(d)}，${outside ? '范围外' : rest ? '休市' : future ? '未开盘' : value != null ? (mode === 'full' ? '综合分' : '量价参考分') + value + '，' + (mode === 'full' ? dayAction(r) : stateName(state)) : '未评估'}" aria-pressed="${selected === d}"><span class="day-number">${Number(d.slice(-2))}</span>${content}</button>`;
  }).join('');
  const trailing = (7 - (leading+dates.length)%7)%7;
  $('#calendar').innerHTML = html + '<div class="day empty"></div>'.repeat(trailing);
  $('#calendar-note').textContent = mode === 'full' ? '点击交易日查看八维依据 · “小仓试探”有个股条件 · 缺项不推算总分' : `当前为${phase === 'midday' ? '午间趋势' : '量价'}参考分 · 不等同开仓或加仓许可 · 点击日期查看详情`;
  const gapList = missing.map(r=>`<button data-date="${r.date}">${r.date.slice(5).replace('-','/')}<span>${gaps(r).map(d=>esc(d.label)).join('、')}</span></button>`).join('') + unrecorded.map(d=>`<button data-date="${d}">${d.slice(5).replace('-','/')}<span>${phase==='midday' ? '午间未留档' : '收盘快照缺失'}</span></button>`).join('');
  $('#month-gaps').innerHTML = (missingCount ? `<b>本月资料缺口</b>${gapList}<small>表示目前没有可核验记录；不以收盘数据补造午间观测。</small>` : '') + (uncertain.length ? `<details class="condition-gaps"><summary>${uncertain.length} 日加仓条件仍有待核验项</summary><small>八维评分完整，也不能代替下列条件。缺证据保持“等待确认”，不记为通过。</small>${uncertain.map(r=>`<button data-date="${r.date}">${r.date.slice(5).replace('-','/')}<span>${r.conditions.filter(c=>c.status==='unknown').map(c=>esc(c.label)).join('、')}</span></button>`).join('')}</details>` : '');
}
function renderDailyList() {
  const rows = data.records.filter(r=>r.date.startsWith(month) && r.phase === phase);
  $('#daily-list').innerHTML = rows.length ? `<table><thead><tr><th>交易日</th><th>综合分</th><th>开仓</th><th>加仓</th><th>减仓检查</th></tr></thead><tbody>${rows.map(r=>`<tr class="${r.date===selected ? 'selected-row' : ''}"><td><button data-date="${r.date}">${r.date.slice(5).replace('-','/')}</button></td><td><span class="table-score ${r.state}">${r.score ?? '缺'+gaps(r).length+'项'}</span></td><td>${esc(r.actions.open)}</td><td class="${r.actions.add==='可分批评估' ? 'add-ready' : ''}">${esc(r.actions.add)}</td><td>${esc(r.actions.reduce)}</td></tr>`).join('')}</tbody></table>` : '<p class="empty-month">该月该时段暂无真实观测记录。</p>';
}
function renderChart() {
  const dates = monthDates().filter(d=>data.trade_days.includes(d) && d >= data.history_start && d <= data.today);
  const values = dates.map(d=>({date:d, value:shownValue(record(d))}));
  $('#chart-title').textContent = mode === 'full' ? '本月综合评分变化' : `本月${phase === 'midday' ? '午间趋势' : '量价'}参考变化`;
  const valid = values.filter(v=>v.value != null);
  $('#chart-annotation').textContent = `${valid.length} / ${dates.length} 个交易日有记录`;
  const w = 700, h = 150, left = 30, top = 15, plotH = 105, plotW = 652;
  const x = i => left + (values.length <= 1 ? plotW/2 : i*plotW/(values.length-1));
  const y = v => top + (100-v)*plotH/100;
  let svg = [0,25,50,75,100].map(v=>`<line x1="${left}" y1="${y(v)}" x2="${w-12}" y2="${y(v)}" stroke="#edf1f3" stroke-dasharray="${v === 0 ? '0' : '3 4'}"/><text x="2" y="${y(v)+3}">${v}</text>`).join('');
  let connected = false, path = '';
  values.forEach((v,i)=>{if(v.value == null){connected=false;return;}path += `${connected ? 'L' : 'M'}${x(i)},${y(v.value)} `;connected=true;});
  svg += `<path d="${path}" fill="none" stroke="#4daba4" stroke-width="2"/>`;
  values.forEach((v,i)=>{
    svg += `<text x="${x(i)}" y="141" text-anchor="middle">${Number(v.date.slice(-2))}</text>`;
    if(v.value != null){const isSelected = v.date === selected;svg += `<g class="point" tabindex="0" role="button" data-chart-date="${v.date}" aria-label="${dateLabel(v.date)}，${v.value}分"><title>${v.date} · ${v.value}分</title><circle cx="${x(i)}" cy="${y(v.value)}" r="${isSelected ? 5 : 3.5}" fill="${isSelected ? '#087f86' : '#6cb8ac'}" stroke="white" stroke-width="1.5"/><text x="${x(i)}" y="${y(v.value)-10}" text-anchor="middle" style="fill:${isSelected ? '#087f86' : '#6ba69e'};font-weight:${isSelected ? '700' : '400'}">${v.value}</text><circle cx="${x(i)}" cy="${y(v.value)}" r="11" fill="transparent"/></g>`;}
  });
  if(!valid.length) svg += `<text x="350" y="70" text-anchor="middle">该月该时段尚无${mode === 'full' ? '完整评估' : '真实观测'}记录</text>`;
  $('#chart').innerHTML = `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc($('#chart-title').textContent)}">${svg}</svg>`;
}
function renderDetail() {
  const r = record();
  if(!r){$('#day-detail').innerHTML = `<div class="detail-title"><h2>${dateLabel(selected,false)} · 当日详情</h2><span class="tiny-badge">${phase === 'close' ? '收盘' : '午间'}</span></div><div class="empty-detail">该时段尚无记录<br><small>${phase === 'midday' ? '午间记录从启用定时更新后开始累积。' : '请选择已有行情的交易日。'}</small></div><div class="decision-banner missing"><h3>等待数据核验</h3><p>没有记录时不生成开仓或加仓结论。</p></div>`;return;}
  const cta = r.state === 'defense' ? '暂缓开仓，暂停加仓' : r.state === 'observe' ? '观察修复，等待确认' : r.state === 'trial' ? '可小仓试探，暂缓加仓' : r.state === 'advance' ? (r.actions.add === '可分批评估' ? '条件满足，可评估加仓' : '环境改善，等待确认') : '暂不判断加仓';
  const dimHtml = r.dimensions.map(d=>`<details class="score-dimension"><summary><label>${esc(d.label)}</label><span class="bar ${d.score == null ? 'empty' : ''}"><i class="${d.score != null && d.score/d.max < .4 ? 'low' : ''}" style="width:${d.score == null ? 0 : d.score/d.max*100}%"></i></span><span class="dimension-value"><b>${d.score ?? '—'}</b> / ${d.max}</span></summary><div class="dimension-explanation">${esc(d.reason)}<br>${d.source_ids.map(id=>{const s = r.sources.find(s=>s.id===id);return s ? sourceLink(s,'依据') : '';}).join('')}</div></details>`).join('');
  const metricLabels = {up:'上涨家数',down:'下跌家数',limit_up:'涨停家数',limit_down:'跌停家数',broken:'炸板家数',seal_rate:'封板率',continuation:'连板晋级率'};
  const metrics = Object.entries(metricLabels).filter(([key])=>r.metrics[key]!=null).map(([key,label])=>`<tr><th>${label}</th><td>${num(r.metrics[key],key.endsWith('rate') || key === 'continuation' ? 2 : 0)}${key.endsWith('rate') || key === 'continuation' ? '%' : ' 家'}</td></tr>`).join('');
  $('#day-detail').innerHTML = `<div class="detail-title"><h2>${dateLabel(selected,false)} · 当日详情</h2><span class="tiny-badge">${phase === 'close' ? '收盘' : '午间'}${r.review_kind === 'retrospective' ? ' · 补录' : ''}</span></div>
    <div class="decision-banner ${r.state}"><h3>${cta}</h3><p>${r.risk_gate ? '风险条件触发防守覆盖，优先控制敞口。' : r.complete ? '市场环境提示 · 执行前核对个股条件' : '缺少完整证据，保留判断，不猜分。'}</p></div>
    ${r.risk_status !== 'clear' && !r.risk_gate ? '<div class="risk-pending"><b>极端风险统计待核验</b><p>缺少同口径精确下跌占比或跌停统计，不能把“未触发”理解为风险已经排除。八维评分完整不代表全部风险数据齐全。</p></div>' : ''}
    ${r.research_note ? `<div class="history-method">${esc(r.research_note)}</div>` : ''}
    ${gaps(r).length ? `<div class="missing-evidence"><h3>此日缺少 ${gaps(r).length} 项依据</h3>${gaps(r).map(d=>`<p><b>${esc(d.label)}</b><span>${esc(d.reason)}</span></p>`).join('')}</div>` : ''}
    <div class="action-rows"><div class="action-row"><span class="action-icon">${['trial','advance'].includes(r.state) ? '✓' : '—'}</span><span>开仓</span><b>${esc(r.actions.open)}</b></div><div class="action-row"><span class="action-icon wait">Ⅱ</span><span>加仓</span><b>${esc(r.actions.add)}</b></div><div class="action-row"><span class="action-icon wait">!</span><span>减仓</span><b>${esc(r.actions.reduce)}</b></div></div>
    <div class="detail-section"><h3>当日判断依据</h3><ol class="reasons">${r.reasons.slice(0,3).map(s=>`<li>${esc(s)}</li>`).join('')}</ol></div>
    <div class="detail-section"><h3>加仓还需确认</h3>${r.conditions.map(c=>`<div class="condition ${c.status}"><em>${c.status === 'pass' ? '✓' : c.status === 'fail' ? '×' : '○'}</em><div>${esc(c.label)}<small>${esc(c.detail)} ${(c.source_ids||[]).map(id=>{const s=r.sources.find(s=>s.id===id);return s ? sourceLink(s,'依据') : '';}).join(' ')}</small></div></div>`).join('')}${r.review_model === 'history-news-v1' ? '<p class="score-footnote">历史新闻的涨跌家数多为约数，极端风险阈值未完成同口径精确核验；“候选”仍需检查个股与风险条件。</p>' : ''}</div>
    <div class="detail-section"><h3>八维评分 <small style="font-weight:400;color:#91a0a8">点击查看依据</small></h3>${dimHtml}<p class="score-footnote">${r.complete ? '八项均有证据；分数仍含研判，不代表胜率。' : '未评估项保留空白，不按零分或中性分处理。'}</p></div>
    <details class="detail-accordion"><summary>原始数据与来源</summary><table class="data-table"><tr><th>行情来源</th><td>${esc(r.price_origin || data.price_source.name)}</td></tr><tr><th>行情截至</th><td>${time(r.prices_asof)}</td></tr><tr><th>最近获取</th><td>${time(r.retrieved_at)}</td></tr><tr><th>评估资料截至</th><td>${time(r.asof)}</td></tr><tr><th>评估生成</th><td>${time(r.assessed_at)}</td></tr>${metrics}</table>${r.metrics.scope ? `<p class="score-footnote">${esc(r.metrics.scope)}</p>` : ''}<div class="source-list"><div class="source-item">${sourceLink({url:data.price_source.url,title:data.price_source.name})}<span>真实指数日线；历史仅使用截至当日的行情</span></div><div class="source-item"><a href="https://qt.gtimg.cn/q=sh000001,sz399001,sz399006" target="_blank" rel="noopener noreferrer">腾讯财经：最新报价 ↗</a><span>用于午间观测与收盘时点核验；链接返回最新行情</span></div><div class="source-item"><a href="https://hq.sinajs.cn/list=sh000001,sz399001,sz399006" target="_blank" rel="noopener noreferrer">新浪财经：备用核验报价 ↗</a><span>备用核验源；链接返回最新行情</span></div>${r.sources.map(s=>`<div class="source-item">${sourceLink(s)}<span>${esc(s.published_at || s.date || '')}${s.note ? ' · ' + esc(s.note) : ''}</span></div>`).join('')}</div></details>
    <details class="detail-accordion"><summary>减仓与失效条件</summary><p class="reduce-note">${r.state === 'defense' ? '当日市场处于防守档，优先检查短线弱势持仓的减仓条件。' : r.state === 'observe' ? '当日市场处于观察档，重点检查弱势持仓。' : !r.complete ? '当日资料不足，不能据此给出继续持有或减仓的完整结论。' : '市场层面未触发防守减仓提示，个股自身失效位仍然有效。'}市场低分不等于卖出所有持仓。检查个股是否跌破原定失效位、放量下跌、主线退潮或业绩逻辑改变。出现这些条件，再按自己的仓位与风险预算分批处理；T+1、跳空和跌停可能使止损无法按计划成交。</p></details>`;
}
function renderStatus() {
  const s = data.update, labels = {running:'行情更新中',success:'行情更新完成',partial:'部分数据未更新',failed:'更新失败',never:'尚未更新'};
  $('#status-content').innerHTML = `<dl class="status-grid"><dt>行情状态</dt><dd>${labels[s.state] || esc(s.state)}</dd><dt>行情最新日期</dt><dd>${data.latest_date || '—'}</dd><dt>最近尝试</dt><dd>${time(s.attempted_at || s.started_at)}</dd><dt>最近成功</dt><dd>${time(s.last_success)}</dd><dt>历史覆盖</dt><dd>${data.coverage.trading_days} 个交易日 · ${data.coverage.full_reviews} 份完整评估</dd><dt>定时任务</dt><dd>${data.schedule.enabled ? '已启用 · 每天 12:30 / 16:30' : '待配置'}（北京时间）</dd><dt>运行条件</dt><dd>电脑开机、联网，Codex 保持运行</dd></dl><p>“更新行情”只拉取指数数据。八维综合评估由定时任务核验公开材料后更新，缺少证据不会自动填分。</p>${s.errors?.length ? `<div class="status-errors">${s.errors.map(esc).join('<br>')}</div>` : ''}${s.warnings?.length ? `<div class="status-errors">${s.warnings.map(esc).join('<br>')}</div>` : ''}<p>午间和收盘分别保留；若错过午间，后续收盘更新不会补造午间记录。</p>`;
}
function expectedRecord() {
  const clock = data.server_now?.slice(11,16) || '00:00';
  const currentDue = data.trade_days.includes(data.today) && clock >= '12:30';
  const date = currentDue ? data.today : data.trade_days.filter(d=>d<data.today).at(-1);
  return date ? {date, phase:currentDue && clock < '16:30' ? 'midday' : 'close'} : null;
}
function renderNotice() {
  const status = data.update, warning = ['partial','failed'].includes(status.state);
  const latest = latestRecord(), complete = data.records.filter(r=>r.complete).at(-1);
  const expected = expectedRecord(), due = expected && record(expected.date, expected.phase);
  const stale = expected && !due;
  const incomplete = due && !due.complete;
  const rest = data.today <= data.calendar_end && !data.trade_days.includes(data.today);
  $('#sync-label').textContent = status.state === 'running' ? '行情更新中' : warning ? '更新待检查' : `行情 ${data.latest_date?.slice(5).replace('-','/')} 已存本地`;
  $('#status-button').classList.toggle('warn',Boolean(warning || stale || incomplete));
  $('#refresh').disabled = refreshPending || status.state === 'running';
  $('#notice').className = 'notice' + (warning || stale || incomplete ? ' warning' : '');
  $('#notice').textContent = warning ? `更新未全部完成，正在展示已保存数据（最新 ${data.latest_date}）。点击右上角状态查看原因。` : stale ? `${expected.date} ${expected.phase === 'midday' ? '午间' : '收盘'}数据尚未核验；当前展示最近保存记录 ${latest?.date || '暂无'}。` : incomplete ? `${due.date} ${due.phase === 'midday' ? '午间' : '收盘'}行情已取得，八维综合评估待补全；不沿用旧分数作为本时段判断。` : `${rest ? '今日休市，沿用最近交易日。' : ''}行情截至 ${time(latest?.prices_asof)} · 最近完整评估 ${complete ? time(complete.asof) : '待核验'} · 历史量价参考与八维综合分分别展示。`;
}
function render() {
  if(!data) return;
  $('.schedule').textContent = data.schedule.enabled ? '午间 12:30 · 收盘 16:30 北京时间' : '定时更新未配置 · 可手动更新行情';
  $('#page-title').textContent = view === 'history' ? '历史市场回看' : '今日市场总览';
  $('#page-subtitle').textContent = view === 'history' ? '选择月份，点击交易日，查看当时的行情与依据。' : '先看市场环境，再检查开仓与加仓条件。';
  $('#nav-today').classList.toggle('active',view === 'today'); $('#nav-history').classList.toggle('active',view === 'history');
  renderNotice();
  renderRail();renderSummary();renderCalendar();renderChart();renderDailyList();renderDetail();renderStatus();
}
function toast(message) { clearTimeout(toastTimer);$('#toast').textContent=message;$('#toast').hidden=false;toastTimer=setTimeout(()=>$('#toast').hidden=true,3500); }
async function loadData(initial = false) {
  try {
    const response=await fetch('/api/data');
    if(!response.ok) throw new Error('本地数据暂未准备好');
    const next=await response.json();
    const signature=d=>JSON.stringify([d?.generated_at,d?.today,d?.update,d?.schedule]);
    const changed=signature(data)!==signature(next);
    const previousLatest=latestRecordKey(data);
    data=next;
    if(initial || !selected || (view==='today' && previousLatest!==latestRecordKey(data)))goToday();
    else if(changed)render();
    else renderNotice();
  }
  catch(error){$('#notice').className='notice warning';$('#notice').textContent=`连接中断：${error.message}。已显示的记录继续保留，恢复后自动重试。`;$('#sync-label').textContent='连接中断';$('#status-button').classList.add('warn');}
}
function latestRecordKey(value) { const r=value?.records?.at(-1);return r ? `${r.date}-${r.phase}` : ''; }
async function refresh(force) {
  if(refreshPending)return;
  refreshPending=true;$('#refresh').disabled=true;
  try {const response=await fetch('/api/update',{method:'POST',headers:{'Content-Type':'application/json','X-Local-Request':'position-guide'},body:JSON.stringify({force})});const result=await response.json();if(!response.ok) throw new Error(result.error);if(force)toast(result.message);if(result.started){$('#sync-label').textContent='行情更新中';setTimeout(()=>loadData(),1500);}}
  catch(error){if(force)toast(`更新请求失败：${error.message}`);}
  finally{refreshPending=false;$('#refresh').disabled=data?.update?.state==='running';}
}
document.addEventListener('click',event=>{
  const button=event.target.closest('button');
  if(button?.dataset.month)chooseMonth(button.dataset.month);
  if(button?.dataset.date){selected=button.dataset.date;view=selected===data.today?'today':'history';render();}
  if(button?.dataset.phase){phase=button.dataset.phase;render();}
  if(button?.dataset.mode){mode=button.dataset.mode;render();}
  const point=event.target.closest('[data-chart-date]');
  if(point){selected=point.dataset.chartDate;view=selected===data.today?'today':'history';render();}
  if(button?.classList.contains('close-dialog'))button.closest('dialog').close();
});
$('#chart').addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)&&event.target.dataset.chartDate){event.preventDefault();selected=event.target.dataset.chartDate;view='history';render();}});
$('#prev-month').addEventListener('click',()=>{const a=months(),i=a.indexOf(month);if(a[i+1])chooseMonth(a[i+1]);});
$('#next-month').addEventListener('click',()=>{const a=months(),i=a.indexOf(month);if(a[i-1])chooseMonth(a[i-1]);});
$('#nav-today').addEventListener('click',goToday);$('#return-today').addEventListener('click',goToday);
$('#nav-history').addEventListener('click',()=>{view='history';render();});
$('#nav-rules').addEventListener('click',()=>$('#rules-dialog').showModal());
$('#status-button').addEventListener('click',()=>{$('#status-dialog').showModal();});
$('#refresh').addEventListener('click',()=>refresh(true));
loadData(true).then(()=>refresh(false));
setInterval(()=>{if(!document.hidden)loadData();},15000);
