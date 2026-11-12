/**
 * 在 nestjs-template 容器内运行：读取 /tmp/cases.json，用 nestjs 的 ValidationPipe 逐条校验，结果输出到 stdout。
 * 输入：[{ "id", "dto", "payload" }]；输出：{ id: { "ok": true, "value" } | { "ok": false, "status", "errors" } }，非 HttpException 记为 500
 */
import 'reflect-metadata';
import { readdirSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';
import { HttpException, ValidationPipe } from '@nestjs/common';
import { config as loadEnv } from 'dotenv';

interface Case {
  id: string;
  dto: string;
  payload: unknown;
}

const ROOT = resolve(__dirname, '..');
const SRC = join(ROOT, 'src');
const load = createRequire(__filename);

function loadClasses(): Map<string, Set<new () => object>> {
  loadEnv({ path: join(ROOT, 'env-example-relational'), quiet: true });
  process.env.NODE_ENV = 'development';
  process.env.FILE_DRIVER = 'local';
  load(join(SRC, 'app.module'));
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(path);
      } else if (
        !entry.name.endsWith('.spec.ts') &&
        (entry.name.endsWith('.dto.ts') || (/[\\/]dto[\\/]/.test(path) && entry.name.endsWith('.ts')))
      ) {
        load(path);
      }
    }
  };
  walk(SRC);
  const classes = new Map<string, Set<new () => object>>();
  for (const [file, mod] of Object.entries(load.cache)) {
    if (!mod || !file.startsWith(SRC)) {
      continue;
    }
    for (const value of Object.values(mod.exports as Record<string, unknown>)) {
      if (typeof value === 'function') {
        const candidates = classes.get(value.name) ?? new Set<new () => object>();
        candidates.add(value as new () => object);
        classes.set(value.name, candidates);
      }
    }
  }
  return classes;
}

async function main(): Promise<void> {
  const classes = loadClasses();
  const options = (load(join(SRC, 'common', 'validation-options')) as { default: ConstructorParameters<typeof ValidationPipe>[0] })
    .default;
  const pipe = new ValidationPipe(options);
  const cases = JSON.parse(readFileSync(process.argv[2] ?? '/tmp/cases.json', 'utf8')) as Case[];
  const results: Record<string, unknown> = {};
  for (const item of cases) {
    const candidates = [...(classes.get(item.dto) ?? [])];
    if (candidates.length !== 1) {
      throw new Error(`DTO ${item.dto} matched ${candidates.length} exports`);
    }
    const metatype = candidates[0];
    try {
      const value: unknown = await pipe.transform(item.payload, { type: 'body', metatype, data: '' });
      results[item.id] = { ok: true, value: JSON.parse(JSON.stringify(value)) as unknown };
    } catch (error) {
      if (error instanceof HttpException) {
        const response = error.getResponse() as { errors?: unknown };
        results[item.id] = { ok: false, status: error.getStatus(), errors: response.errors ?? null };
      } else {
        results[item.id] = { ok: false, status: 500, errors: null };
      }
    }
  }
  process.stdout.write(`${JSON.stringify(results, null, 2)}\n`);
}

void main();
