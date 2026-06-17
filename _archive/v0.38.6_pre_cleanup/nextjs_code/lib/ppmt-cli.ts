/**
 * PPMT CLI Helper - Finds and executes the PPMT Python CLI
 *
 * On the user's Mac, `ppmt` is not installed globally.
 * Instead we call `python -m ppmt.cli.main` from the ppmt/ directory
 * within the project, or `python -m ppmt.scripts.bulk_ingest` for bulk.
 *
 * This module auto-detects the correct paths.
 */

import { execSync } from 'child_process';
import path from 'path';
import fs from 'fs';

/**
 * Find the ppmt Python package directory.
 * Looks in several possible locations relative to the project.
 */
function findPpmtDir(): string {
  const candidates = [
    // Standard: ppmt/ dir inside the Next.js project
    path.join(process.cwd(), 'ppmt'),
    // Monorepo: might be one level up
    path.join(process.cwd(), '..', 'ppmt'),
    // Development: might be at home dir
    path.join(require('os').homedir(), 'ppmt'),
  ];

  for (const dir of candidates) {
    // Check if it has the Python source
    const srcDir = path.join(dir, 'src', 'ppmt');
    const scriptsDir = path.join(dir, 'scripts');
    if (fs.existsSync(srcDir) || fs.existsSync(scriptsDir)) {
      return dir;
    }
  }

  // Fallback to cwd/ppmt
  return path.join(process.cwd(), 'ppmt');
}

/**
 * Find the Python executable.
 * Prefers python3, falls back to python.
 */
function findPython(): string {
  for (const cmd of ['python3', 'python']) {
    try {
      execSync(`${cmd} --version`, { timeout: 5000, stdio: 'pipe' });
      return cmd;
    } catch {
      continue;
    }
  }
  return 'python3'; // fallback
}

/**
 * Execute a PPMT CLI command.
 *
 * Instead of calling `ppmt <command>`, we call:
 *   cd <ppmt_dir> && <python> -m ppmt.cli.main <command>
 *
 * This works without pip install and from any project location.
 */
export function execPpmt(command: string, options?: {
  timeout?: number;
  maxBuffer?: number;
}): string {
  const ppmtDir = findPpmtDir();
  const python = findPython();
  const timeout = options?.timeout ?? 120000;
  const maxBuffer = options?.maxBuffer ?? 5 * 1024 * 1024;

  // Ensure ppmt/src is in PYTHONPATH so imports work
  const env = {
    ...process.env,
    PYTHONPATH: path.join(ppmtDir, 'src'),
  };

  const cmd = `cd "${ppmtDir}" && ${python} -m ppmt.cli.main ${command}`;

  return execSync(cmd, {
    timeout,
    encoding: 'utf-8',
    maxBuffer,
    env,
  });
}

/**
 * Execute the bulk ingest Python script.
 */
export function execBulkIngest(args: string, options?: {
  timeout?: number;
  maxBuffer?: number;
}): string {
  const ppmtDir = findPpmtDir();
  const python = findPython();
  const timeout = options?.timeout ?? 300000;
  const maxBuffer = options?.maxBuffer ?? 10 * 1024 * 1024;

  const env = {
    ...process.env,
    PYTHONPATH: path.join(ppmtDir, 'src'),
  };

  const cmd = `cd "${ppmtDir}" && ${python} -m ppmt.scripts.bulk_ingest ${args}`;

  return execSync(cmd, {
    timeout,
    encoding: 'utf-8',
    maxBuffer,
    env,
  });
}
