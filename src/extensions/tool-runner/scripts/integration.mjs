import { spawnSync } from 'node:child_process';
const result = spawnSync(process.execPath, ['node_modules/vitest/vitest.mjs', 'run'], {
  stdio: 'inherit', env: { ...process.env, RUN_TOOL_RUNNER_INTEGRATION: '1' },
});
process.exitCode = result.status ?? 1;
