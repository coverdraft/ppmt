
const ts = require('typescript');
const fs = require('fs');
const path = process.argv[2];
const src = fs.readFileSync(path, 'utf8');
const kind = path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
const sf = ts.createSourceFile(path, src, ts.ScriptTarget.Latest, true, kind);
const diag = sf.parseDiagnostics || [];
const out = { parse_errors: diag.length, first_error: null };
if (diag.length) {
  const d = diag[0];
  const pos = sf.getLineAndCharacterOfPosition(d.start);
  out.first_error = `L${pos.line+1}:${pos.character+1} ` +
    ts.flattenDiagnosticMessageText(d.messageText, '\n').slice(0, 120);
}
process.stdout.write(JSON.stringify(out));
