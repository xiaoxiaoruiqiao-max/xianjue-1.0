const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

function setup() {
  const elements=new Map();
  const document={hidden:false,addEventListener(){},querySelectorAll(){return [];},querySelector(key){
    if(!elements.has(key))elements.set(key,{textContent:'',className:'',disabled:false,
      classList:{add(){},toggle(){}},addEventListener(){}});
    return elements.get(key);
  }};
  const context=vm.createContext({document,fetch:()=>new Promise(()=>{}),setTimeout(){},clearTimeout(){},setInterval(){}});
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../public/app.js'),'utf8'),context);
  context.fixture={today:'2026-10-26',server_now:'2026-10-26T16:30:00+08:00',calendar_end:'2026-12-31',
    generated_at:'v1',latest_date:'2026-10-26',history_start:'2025-10-26',schedule:{},update:{state:'success'},
    trade_days:['2026-10-23','2026-10-26'],records:[{date:'2026-10-23',phase:'close',complete:true,asof:'2026-10-23T17:00:00+08:00'},
      {date:'2026-10-26',phase:'midday',complete:true,prices_asof:'2026-10-26T11:30:00+08:00'}]};
  vm.runInContext('data=fixture;selected="2026-10-26";month="2026-10";',context);
  return {context,elements,run:code=>vm.runInContext(code,context)};
}

test('收盘漏更不能被当天午间记录掩盖',()=>{
  const {run,elements}=setup();run('renderNotice()');
  assert.match(elements.get('#notice').textContent,/2026-10-26 收盘数据尚未核验/);
});
test('午间开始前不报午间漏更',()=>{
  const {run}=setup();run('data.server_now="2026-10-26T12:29:00+08:00"');
  assert.equal(run('expectedRecord().date'),'2026-10-23');
});
test('到午间更新时间即检查午间记录',()=>{
  const {run}=setup();run('data.server_now="2026-10-26T12:30:00+08:00"');
  assert.equal(run('expectedRecord().phase'),'midday');
});
test('周末缺少上一交易日收盘仍然提示',()=>{
  const {run,elements}=setup();run('data.today="2026-10-25";data.records=[];renderNotice()');
  assert.match(elements.get('#notice').textContent,/2026-10-23 收盘数据尚未核验/);
});
test('跨月返回最新日使月历与选中日期一致',()=>{
  const {run}=setup();run('render=()=>{};data.today="2026-11-01";goToday()');
  assert.equal(run('month'),'2026-10');
});
test('断网恢复时相同数据也清除错误提示',async()=>{
  const {context,run,elements}=setup();
  context.fetch=async()=>{throw Error('offline');};await run('loadData()');
  assert.match(elements.get('#notice').textContent,/连接中断/);
  context.fetch=async()=>({ok:true,json:async()=>context.fixture});await run('loadData()');
  assert.doesNotMatch(elements.get('#notice').textContent,/连接中断/);
});
test('今日总览跟随新记录，历史回看保持选择',async()=>{
  for(const currentView of ['today','history']){
    const {context,run}=setup();run(`view='${currentView}';phase='midday';selected='2026-10-23';render=()=>{};`);
    const next=JSON.parse(JSON.stringify(context.fixture));
    next.generated_at='v2';next.records.push({date:'2026-10-26',phase:'close',complete:true});
    context.fetch=async()=>({ok:true,json:async()=>next});await run('loadData()');
    assert.equal(run('phase'),currentView==='today'?'close':'midday');
    assert.equal(run('view'),currentView);
    // Historical selection can intentionally stay on an earlier date/phase.
    assert.equal(run('selected'),currentView==='today'?'2026-10-26':'2026-10-23');
  }
});
test('更新请求进行中避免重复提交',async()=>{
  const {context,run}=setup();let count=0,resolve;
  context.fetch=()=>{count++;return new Promise(r=>resolve=r);};
  const first=run('refresh(true)');await run('refresh(true)');assert.equal(count,1);
  resolve({ok:true,json:async()=>({started:false,message:'已有数据'})});await first;
});
test('新时段行情已更新但综合缺项时提示补评',()=>{
  const {run,elements}=setup();run('data.records.push({date:data.today,phase:"close",complete:false});renderNotice()');
  assert.match(elements.get('#notice').textContent,/八维综合评估待补全/);
});
test('缺失分数与零分区分，来源文本及危险协议不能注入',()=>{
  const {run}=setup();assert.equal(run('stateFor(0)'),'defense');assert.equal(run('stateFor(null)'),'missing');
  assert.equal(run('sourceLink({url:"javascript:alert(1)",title:"<img>"})'),'&lt;img&gt;');
});
