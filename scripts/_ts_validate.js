// Validate TS syntax of all 5 modified files using the official TypeScript parser
const ts = require('typescript');
const fs = require('fs');

const files = [
    '/tmp/my-project-test/src/app/api/coingecko/markets/route.ts',
    '/tmp/my-project-test/src/app/api/kraken/ticker/route.ts',
    '/tmp/my-project-test/src/lib/live-price-feed.ts',
    '/tmp/my-project-test/src/lib/paper-trading-engine.ts',
    '/tmp/my-project-test/src/stores/trading-store.ts',
];

let allOk = true;
for (const path of files) {
    const src = fs.readFileSync(path, 'utf8');
    const kind = path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
    const sf = ts.createSourceFile(path, src, ts.ScriptTarget.Latest, true, kind);
    const diag = sf.parseDiagnostics || [];
    if (diag.length === 0) {
        console.log(`  OK  ${path} (${src.length} bytes)`);
    } else {
        allOk = false;
        const d = diag[0];
        const pos = sf.getLineAndCharacterOfPosition(d.start);
        console.log(`  FAIL ${path}: ${diag.length} parse errors — first at L${pos.line+1}:${pos.character+1}: ${ts.flattenDiagnosticMessageText(d.messageText, '\n').slice(0, 200)}`);
    }
}
process.exit(allOk ? 0 : 1);
