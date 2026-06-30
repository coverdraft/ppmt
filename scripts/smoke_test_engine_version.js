#!/usr/bin/env node
// Smoke test: spawn the PaperTradingEngine, snapshot once, verify engine_version is set.
// Usage: PPMT_GIT_SHORT=test123 node scripts/smoke_test_engine_version.js

const path = require('path')
process.chdir(__dirname + '/..')

// Load ts via tsx if available, otherwise ts-node
let tsx
try { tsx = require('tsx') }
catch { tsx = require('ts-node') }

// Register TS loader
if (tsx.register) tsx.register({ transpileOnly: true })

const { PaperTradingEngine } = require('../src/lib/paper-trading-engine.ts')
const ENGINE_VERSION = require('../src/lib/paper-trading-engine.ts').ENGINE_VERSION

console.log('─'.repeat(60))
console.log('ENGINE_VERSION exported constant:')
console.log(JSON.stringify(ENGINE_VERSION, null, 2))
console.log('─'.repeat(60))

// Sanity checks
const checks = []
const check = (name, cond) => checks.push({ name, ok: !!cond })

check('ENGINE_VERSION has strategy_stack',
  typeof ENGINE_VERSION.strategy_stack === 'string' && ENGINE_VERSION.strategy_stack.length > 0)
check('ENGINE_VERSION has pkg_version',
  typeof ENGINE_VERSION.pkg_version === 'string' && ENGINE_VERSION.pkg_version.length > 0)
check('ENGINE_VERSION has git_short',
  typeof ENGINE_VERSION.git_short === 'string' && ENGINE_VERSION.git_short.length > 0)
check('ENGINE_VERSION has built_at',
  typeof ENGINE_VERSION.built_at === 'string' && !isNaN(Date.parse(ENGINE_VERSION.built_at)))
check('ENGINE_VERSION has strategies',
  typeof ENGINE_VERSION.strategies === 'object' && Object.keys(ENGINE_VERSION.strategies).length >= 4)
check('ENGINE_VERSION has flags',
  typeof ENGINE_VERSION.flags === 'object' && Object.keys(ENGINE_VERSION.flags).length >= 4)
check('ENGINE_VERSION has summary',
  typeof ENGINE_VERSION.summary === 'string' && ENGINE_VERSION.summary.includes('@'))
check('v82j exit stack on A is true',
  ENGINE_VERSION.flags.v82j_exit_stack_on_A === true)
check('v82j exit stack on D is true',
  ENGINE_VERSION.flags.v82j_exit_stack_on_D === true)
check('strategy C is disabled',
  ENGINE_VERSION.flags.strategy_C_enabled === false)
check('strategy F is enabled',
  ENGINE_VERSION.flags.strategy_F_enabled === true)

const gitShort = process.env.PPMT_GIT_SHORT
if (gitShort) {
  check(`PPMT_GIT_SHORT env var (${gitShort}) propagated to ENGINE_VERSION.git_short`,
    ENGINE_VERSION.git_short === gitShort)
} else {
  check('PPMT_GIT_SHORT falls back to "dev"',
    ENGINE_VERSION.git_short === 'dev')
}

console.log('Checks:')
checks.forEach(c => {
  console.log(`  ${c.ok ? '✓' : '✗'} ${c.name}`)
})
console.log('─'.repeat(60))

const failures = checks.filter(c => !c.ok)
if (failures.length > 0) {
  console.error(`FAILED: ${failures.length} check(s) failed`)
  process.exit(1)
} else {
  console.log(`All ${checks.length} checks passed ✓`)
  process.exit(0)
}
