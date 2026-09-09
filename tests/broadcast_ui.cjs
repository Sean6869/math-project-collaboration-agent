const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('assets/app.js', 'utf8');
const text = {textContent: ''}, kind = {textContent: ''}, actions = {innerHTML: ''};
const banner = {hidden: true, querySelector: selector => selector.includes('text') ? text : selector.includes('actions') ? actions : kind};
const input = {value: '正在输入的作业内容'};
const elements = {
  '#studentBroadcast': banner, '#bcText': input, '#bcType': {value: '阶段提醒'},
  '#sendBc': {}, '#clearBc': {}, '#bcPublished': {innerHTML: '旧广播'}
};
let state = {broadcast: '', groupCount: 4}, response, fail = false;
const notices = [], timers = [];
const context = {
  document: {hidden: false, querySelector: selector => elements[selector], addEventListener() {}},
  setInterval: (fn, ms) => timers.push(ms), _session: {role: 'student'},
  load: () => state, api: async () => {if(fail) throw Error('网络失败'); return response;},
  esc: value => String(value).replaceAll('<', '&lt;').replaceAll('>', '&gt;'),
  setRoot() {}, shell() {}, head() {}, btn() {}, toast: message => notices.push(message)
};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function commonIssues('), source.indexOf('function renderReport(')), context);
(async () => {
  response = {broadcast: '<img onerror=bad>', broadcastType: '阶段提醒'};
  await context.syncBroadcast();
  assert.equal(banner.hidden, false);
  assert.equal(text.textContent, response.broadcast);
  assert.equal(input.value, '正在输入的作业内容');
  assert.ok(context.broadcastBanner(response).includes('&lt;img'));
  assert.ok(timers.includes(3000));
  response = {broadcast: '', broadcastType: '教师广播'};
  await context.syncBroadcast();
  assert.equal(banner.hidden, true);
  context.renderBroadcast();
  fail = true;
  await elements['#sendBc'].onclick();
  assert.equal(state.broadcast, '');
  assert.equal(elements['#bcPublished'].innerHTML, '旧广播');
  assert.ok(notices.at(-1).startsWith('广播操作失败'));
  assert.equal(elements['#sendBc'].disabled, false);
  fail = false;
  response = {broadcast: '已发布内容', broadcastType: '阶段提醒'};
  await elements['#sendBc'].onclick();
  assert.equal(state.broadcast, '已发布内容');
  assert.ok(elements['#bcPublished'].innerHTML.includes('已发布内容'));
  console.log('Broadcast UI checks passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
