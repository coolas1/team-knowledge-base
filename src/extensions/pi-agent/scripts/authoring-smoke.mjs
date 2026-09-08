// Live acceptance: no business implementation is provided to the model.
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { PiAgentRuntime } from '../dist/runtime.js';
import { loadPiAgentConfig, loadTkbAdapterConfig } from '../dist/config.js';
import { ToolLibrary } from '../dist/tool-library.js';
import { Jobs } from '../../tool-runner/dist/jobs.js';
import { createRunnerServer } from '../../tool-runner/dist/server.js';

const root = path.resolve(import.meta.dirname, '../../../..');
try { process.loadEnvFile(path.join(root, '.env')); } catch (error) { if (error.code !== 'ENOENT') throw error; }
const dataDir = mkdtempSync(path.join(tmpdir(), 'tkb-live-authoring-'));
const token = randomBytes(32).toString('hex');
const jobs = new Jobs(); await jobs.initialize();
const server = createRunnerServer(jobs, token);
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const config = loadPiAgentConfig({ ...process.env, PI_AGENT_CWD: root, PI_AGENT_DATA_DIR: dataDir, PI_AGENT_SESSION_DIR: path.join(dataDir, 'sessions'), PI_AGENT_TOOL_LIBRARY_DIR: path.join(dataDir, 'library'), PI_AGENT_TOOL_AUTHORING_ENABLED: 'true', PI_AGENT_RUNNER_URL: `http://127.0.0.1:${server.address().port}`, PI_AGENT_RUNNER_TOKEN: token, PI_AGENT_MAX_TOOL_CALLS: '20', PI_AGENT_MAX_CODE_JOBS: '18', PI_AGENT_MAX_RUN_SECONDS: '300', PI_AGENT_REASONING: 'false', PI_AGENT_THINKING_LEVEL: 'off' });
if (new URL(config.modelBaseUrl).hostname === 'ollama') config.modelBaseUrl = 'http://127.0.0.1:11434/v1';
const adapter = loadTkbAdapterConfig({ ...process.env, TKB_MCP_URL: 'http://127.0.0.1:8000/mcp/', TKB_CONVERSATION_MEMORY_ENABLED: 'false' });
let runtime = new PiAgentRuntime(config, adapter);
const evidence = { model: config.model, provider: config.provider, cases: [], dataDir };
const cases = [
  { id: 'json_group', name: 'gen_accept_group', prompt: '自主创建并保存 gen_accept_group：input={rows:[{team:string,amount:number}]}，返回按 team 累加的 JSON 对象，空数组返回 {}。请用 execute_code 真执行，再用 publish_tool 保存至少两个测试，最后 call_tool 调用 rows=[{team:"red",amount:3},{team:"blue",amount:7},{team:"red",amount:-1}]。不允许通过修改产品源码增加工具。', input: { rows: [{ team: 'unseen', amount: 2.25 }, { team: 'other', amount: 4 }, { team: 'unseen', amount: -0.5 }] }, expected: { unseen: 1.75, other: 4 } },
  { id: 'timezone', name: 'gen_accept_date', prompt: '自主创建并保存 gen_accept_date：input={instant:string,zone:string}，instant 为 ISO 时间或者 "now"，返回该 IANA 时区的 YYYY-MM-DD 日期字符串。先真实执行当前 Asia/Shanghai 日期，再保存含两个固定日期转换测试的参数化工具，最后 call_tool 调用当前日期。不要将今天硬编码。', input: { instant: '2020-12-31T23:30:00Z', zone: 'Asia/Tokyo' }, expected: '2021-01-01' },
  { id: 'calculation', name: 'gen_accept_temperature', prompt: '自主编写、执行并保存 gen_accept_temperature：input={fahrenheit:number}，返回摄氏温度数值。请实际测试冰点和沸点，再 call_tool 调用 77 华氏度。', input: { fahrenheit: -40 }, expected: -40 },
  { id: 'repair', name: 'gen_accept_uppercase', prompt: '验证自主修复：先用 execute_code 执行 export default()=>{throw new Error("intentional acceptance failure")}，确认真实失败。然后沿用返回的 buildId 自己修复为通用工具：input={values:string[]}，返回所有字符串转大写的数组。补上非空与空数组测试，保存为 gen_accept_uppercase，最后 call_tool 调用 {values:["hello","world"]}。', input: { values: ['independent', 'XyZ', ''] }, expected: ['INDEPENDENT', 'XYZ', ''] },
  { id: 'web', name: 'gen_accept_title', prompt: '自主编写并保存 gen_accept_title：input={url:string}，使用 host.fetch({url:input.url}) 及 htmlparser2 提取网页 title，返回 title 字符串。需要 public_http；请实际读取 https://example.com/，不要假造网络结果。测试也应真实读取。若网络不可用如实指出。', input: { url: 'https://example.com/' }, expected: 'Example Domain' },
];
try {
  await runtime.initialize();
  console.log(JSON.stringify({ phase: 'start', model: config.model, provider: config.provider }));
  const selected = process.argv.find(arg => arg.startsWith('--cases='))?.slice(8).split(',');
  for (const item of cases.filter(item => !selected || selected.includes(item.id))) {
    const record = { id: item.id, passed: false, events: [] }; evidence.cases.push(record);
    try {
      const session = await runtime.createSession();
      const outputType = Array.isArray(item.expected) ? 'array' : typeof item.expected === 'object' ? 'object' : typeof item.expected;
      await runtime.streamMessage(session.id, item.prompt + ` 严格约定：outputSchema.type 必须为 ${outputType}，不要包装成其他对象或增加未要求的限制。`, event => {
        if (event.type.startsWith('tool.') || event.type === 'message.completed' || event.type === 'limit.reached') {
          record.events.push(event); console.log(JSON.stringify({ case: item.id, ...event }));
        }
      });
      const library = new ToolLibrary(config.toolLibraryDir);
      try {
        const artifact = library.get(item.name); record.artifact = artifact;
        const result = await jobs.run({ code: artifact.code, input: item.input, capabilities: artifact.capabilities, timeoutMs: 20000 });
        record.independent = { input: item.input, expected: item.expected, actual: result };
        assert.equal(result.status, 'succeeded'); assert.deepEqual(result.result, item.expected);
        assert(record.events.some(e => e.type === 'tool.start' && e.toolName === 'publish_tool'));
        assert(record.events.some(e => e.type === 'tool.result' && e.activity === 'reuse' && !e.isError));
        if (item.id === 'repair') {
          assert(record.events.some(e => e.type === 'tool.result' && e.toolName === 'execute_code' && e.isError));
          assert(record.events.some(e => e.type === 'tool.result' && e.activity === 'repair' && !e.isError));
        }
        record.passed = true;
      } finally { library.close(); }
    } catch (error) { record.error = String(error); console.log(JSON.stringify({ case: item.id, error: String(error) })); }
  }
  if (!selected || selected.includes('json_group')) {
  await runtime.close(); runtime = new PiAgentRuntime(config, adapter); await runtime.initialize();
  const reuse = { id: 'restart_reuse', passed: false, events: [] }; evidence.cases.push(reuse);
  try {
    const session = await runtime.createSession();
    await runtime.streamMessage(session.id, '查找并复用之前保存的按 team 累加 amount 的工具，输入 rows=[{team:"restart",amount:8},{team:"restart",amount:5}]。不要重新创建、发布或执行临时代码。', event => { if (event.type.startsWith('tool.') || event.type === 'message.completed') reuse.events.push(event); });
    assert(reuse.events.some(e => e.type === 'tool.result' && e.activity === 'reuse' && !e.isError));
    assert(!reuse.events.some(e => e.type === 'tool.start' && ['execute_code', 'publish_tool'].includes(e.toolName)));
    reuse.passed = true;
  } catch (error) { reuse.error = String(error); }
  }
} finally {
  await runtime.close(); await jobs.close(); await new Promise(resolve => server.close(resolve));
  const report = path.join(dataDir, 'evidence.json'); writeFileSync(report, JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({ evidence: report, results: evidence.cases.map(c => ({ id: c.id, passed: c.passed, error: c.error })) }));
}
if (evidence.cases.some(c => !c.passed)) process.exitCode = 1;
