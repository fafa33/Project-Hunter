const net = require('node:net');
const command = process.argv[2] === '-c' && process.argv.length === 4 ? process.argv[3] : null;
if (!command) { process.stderr.write('DENIED: shell invocation\n'); process.exit(126); }
const s = net.createConnection('/run/hunter/broker.sock');
let buf = '';
s.setTimeout(65000);
s.on('connect', () => s.write(JSON.stringify({command})));
s.on('data', d => buf += d.toString());
s.on('end', () => {
  try { const r = JSON.parse(buf); process.stdout.write(r.stdout || ''); process.stderr.write(r.stderr || ''); process.exit(r.exit); }
  catch { process.exit(126); }
});
s.on('error', () => process.exit(126));
s.on('timeout', () => process.exit(126));
