# Archive — v0.38.6 pre-cleanup

**Fecha**: 2026-06-18
**Creado por**: Fase 0 — Higiene del repo (commit inicial)

## Qué es esto

Carpeta aislada con todo el código/configs/docs que **NO forman parte del motor PPMT
actual** (v0.38.6+ FastAPI). Se mueve aquí **en vez de borrar** para:

- Mantener un safety net reversible durante unos días.
- Permitir `git checkout` de cualquier archivo si lo necesitamos.
- Si en ~7 días nada de esto fue necesario, se hace `git rm -r _archive/` en un
  único commit final.

## Verificación previa (antes de mover nada)

Se corrieron 4 `rg` que confirmaron:

1. `localhost:3000|next dev|next start` → solo en `package.json` y dentro del
   propio `src/app/` + `src/components/` (auto-referencia, sin callers vivos).
2. `from.*src/lib/services|from.*src/components` en `src/app/` → vacío.
3. `npm|node|supervisor` en `src/ppmt/ scripts/` → solo "node" como
   "child_node" del MoneyManager / trie, no Node.js.
4. `src/lib|src/components|src/hooks|src/core|src/app` en `src/ppmt/` → vacío.

El motor PPMT vive 100% aislado del Next.js obsoleto.

## Estructura

```
_archive/v0.38.6_pre_cleanup/
├── nextjs_code/              # src/app, src/components, src/hooks, src/core,
│                             # src/lib, src/store, src/tests, src/index.ts,
│                             # src/proxy.ts
├── nextjs_configs/           # package.json, tsconfig.json, tailwind.config.ts,
│                             # postcss.config.mjs, eslint.config.mjs,
│                             # vitest.config.ts, components.json,
│                             # package-lock.json, bun.lock, supervisor.js,
│                             # tsconfig.tsbuildinfo, next.config.ts
├── obsolete_root_scripts/    # predict_live.py, run_papertrader.py,
│                             # signal_daemon.py, signal_loop.sh, start.sh
├── debug_artifacts/          # signals/, public/, examples/
├── ts_tests/                 # tests/*.test.ts (duplican los .py)
└── redundant_docs/           # ANALISIS_CRITICO_v0.34.0.md, ARCHITECTURE.md,
                              # CHANGELOG.md, PPMT_TERMINAL_PLAN.md,
                              # TRACEABILITY.md, TRACEABILITY_v0.31.md,
                              # worklog-new.md, worklogs/
```

## Conservado (en raíz, NO archivado)

- `src/ppmt/` — motor completo.
- `config/` — `default.env`, `default.yaml` (configs vivos).
- `docs/` — 2 PDFs técnicos.
- `scripts/` — Python scripts vivos.
- `tests/*.py` — tests Python (cubren lo mismo que los .test.ts).
- `prisma/`, `skills/`, `mini-services/`, `agent-ctx/` (por decisión del usuario).
- `groups_config.json`, `oos_validation_results.json` (referenciados vivos).
- `setup_fresh.sh`, `pyproject.toml`, `HANDOFF.md`, `TRAZABILIDAD.md`,
  `README.md`, `worklog.md`, `Caddyfile`, `.zscripts/`.

## Cómo revertir

Cada grupo se movió en su propio commit. Para restaurar uno:

```bash
git revert <commit-hash-del-movimiento>
```

O para restaurar todo el archive a raíz:

```bash
git mv _archive/v0.38.6_pre_cleanup/<grupo>/* <destino-original>/
```

## Próximo paso (en ~7 días)

Si nada de esto fue necesario:

```bash
git rm -r _archive/
git commit -m "chore: drop _archive/ — v0.38.6 pre-cleanup confirmed unused"
```
