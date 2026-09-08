// This entrypoint runs ONLY inside the disposable job container.
import { createInterface } from 'node:readline';
import { writeFile } from 'node:fs/promises';
import { format } from 'node:util';

const send = message => process.stdout.write(JSON.stringify(message) + '\n');
const finish = async (message, code) => {
  await new Promise(resolve => process.stderr.write('', resolve));
  await new Promise(resolve => process.stdout.write(JSON.stringify(message) + '\n', resolve));
  process.exit(code);
};
console.log = (...args) => process.stderr.write(format(...args) + '\n');
const pending = new Map();
let sequence = 0;
let started = false;
const rpc = request => new Promise((resolve, reject) => {
  const id = ++sequence;
  pending.set(id, { resolve, reject });
  send({ type: 'request', id, request });
});
const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
lines.on('line', async line => {
  try {
    const data = JSON.parse(line);
    if (started) {
      const waiter = pending.get(data.id);
      if (!waiter) throw new Error('Unknown RPC response');
      pending.delete(data.id);
      if (data.error) waiter.reject(new Error(data.error)); else waiter.resolve(data.value);
      return;
    }
    started = true;
    await writeFile('/work/program.mjs', data.code, { flag: 'wx' });
    const module = await import('file:///work/program.mjs');
    if (typeof module.default !== 'function') throw new Error('Export a default async function(input, host)');
    const value = await module.default(data.input, Object.freeze({
      fetch: options => rpc({ ...options, capability: 'public_http' }),
      request: options => rpc(options),
    }));
    if (value === undefined) throw new Error('Program must return a JSON value');
    await finish({ type: 'result', value }, 0);
  } catch (error) {
    await finish({ type: 'error', message: error instanceof Error ? error.message.slice(0, 2000) : 'Program failed' }, 1);
  }
});
