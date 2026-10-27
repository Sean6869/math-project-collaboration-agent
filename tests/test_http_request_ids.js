const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const source = fs.readFileSync(require('node:path').join(__dirname, '../assets/app.js'), 'utf8');
const requestIdSource = source.match(/^function createRequestId\(\).*$/m)[0];
function environment(crypto) {
  const context = vm.createContext({crypto});
  vm.runInContext(requestIdSource, context);
  return context;
}
// HTTP origins expose getRandomValues but may omit randomUUID.
const http = environment({getRandomValues: bytes => webcrypto.getRandomValues(bytes)});
const identifiers = new Set(Array.from({length: 1000}, () => http.createRequestId()));
assert.equal(identifiers.size, 1000);
for (const id of identifiers) assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
assert.equal(environment({randomUUID: () => 'native-id'}).createRequestId(), 'native-id');
assert.throws(() => environment(undefined).createRequestId(), /安全随机数/);
console.log('Request identifiers work on HTTP and HTTPS.');
