#!/usr/bin/env python3
"""
CryptoQuant Terminal — Arquitectura Final v4.0
Documento arquitectónico definitivo que reemplaza todos los anteriores.
"""
import sys, os, hashlib
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch, mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether, CondPageBreak, HRFlowable
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.platypus.tableofcontents import TableOfContents

# ─── Palette ───
PAGE_BG       = colors.HexColor('#f5f5f3')
CARD_BG       = colors.HexColor('#e9e8e5')
TABLE_STRIPE  = colors.HexColor('#eeedea')
HEADER_FILL   = colors.HexColor('#655d43')
BORDER        = colors.HexColor('#ccc6b2')
ICON          = colors.HexColor('#a8924f')
ACCENT        = colors.HexColor('#613ad7')
ACCENT_2      = colors.HexColor('#5ec692')
TEXT_PRIMARY   = colors.HexColor('#1a1917')
TEXT_MUTED     = colors.HexColor('#7c7a73')
SEM_SUCCESS   = colors.HexColor('#42995f')
SEM_WARNING   = colors.HexColor('#877144')
SEM_ERROR     = colors.HexColor('#984e47')
SEM_INFO      = colors.HexColor('#49739d')

# ─── Fonts ───

pdfmetrics.registerFont(TTFont('LibSerif', '/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf'))
pdfmetrics.registerFont(TTFont('LibSerifBold', '/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf'))


pdfmetrics.registerFont(TTFont('LibSans', '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'))
pdfmetrics.registerFont(TTFont('LibSansBold', '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf'))

pdfmetrics.registerFont(TTFont('DejaVuMono', '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'))
pdfmetrics.registerFont(TTFont('DejaVuMonoBold', '/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf'))
registerFontFamily('LibSerif', normal='LibSerif', bold='LibSerifBold')
registerFontFamily('LibSans', normal='LibSans', bold='LibSansBold')
registerFontFamily('DejaVuMono', normal='DejaVuMono', bold='DejaVuMono')

# ─── Page Setup ───
PAGE_W, PAGE_H = A4
L_MARGIN = 1.0 * inch
R_MARGIN = 1.0 * inch
T_MARGIN = 0.8 * inch
B_MARGIN = 0.8 * inch
AVAIL_W = PAGE_W - L_MARGIN - R_MARGIN

# ─── Styles ───
styles = {}

styles['H1'] = ParagraphStyle(
    'H1', fontName='LibSerif', fontSize=20, leading=26,
    spaceBefore=18, spaceAfter=10, textColor=ACCENT
)
styles['H2'] = ParagraphStyle(
    'H2', fontName='LibSerif', fontSize=15, leading=20,
    spaceBefore=14, spaceAfter=8, textColor=HEADER_FILL
)
styles['H3'] = ParagraphStyle(
    'H3', fontName='LibSerif', fontSize=12, leading=16,
    spaceBefore=10, spaceAfter=6, textColor=TEXT_PRIMARY
)
styles['Body'] = ParagraphStyle(
    'Body', fontName='LibSerif', fontSize=10.5, leading=17,
    alignment=TA_JUSTIFY, spaceAfter=6, textColor=TEXT_PRIMARY
)
styles['BodyLeft'] = ParagraphStyle(
    'BodyLeft', fontName='LibSerif', fontSize=10.5, leading=17,
    alignment=TA_LEFT, spaceAfter=6, textColor=TEXT_PRIMARY
)
styles['Code'] = ParagraphStyle(
    'Code', fontName='DejaVuMono', fontSize=8.5, leading=13,
    alignment=TA_LEFT, spaceAfter=4, textColor=TEXT_PRIMARY,
    backColor=CARD_BG, leftIndent=12, rightIndent=12,
    spaceBefore=4, borderPadding=(6,6,6,6)
)
styles['Callout'] = ParagraphStyle(
    'Callout', fontName='LibSerif', fontSize=11, leading=17,
    alignment=TA_LEFT, spaceAfter=8, textColor=SEM_INFO,
    leftIndent=18, borderPadding=(8,8,8,8),
    borderColor=SEM_INFO, borderWidth=0, borderRadius=0
)
styles['TableHeader'] = ParagraphStyle(
    'TableHeader', fontName='LibSerif', fontSize=10, leading=14,
    alignment=TA_CENTER, textColor=colors.white
)
styles['TableCell'] = ParagraphStyle(
    'TableCell', fontName='LibSerif', fontSize=9.5, leading=14,
    alignment=TA_LEFT, textColor=TEXT_PRIMARY
)
styles['TableCellCenter'] = ParagraphStyle(
    'TableCellCenter', fontName='LibSerif', fontSize=9.5, leading=14,
    alignment=TA_CENTER, textColor=TEXT_PRIMARY
)
styles['Caption'] = ParagraphStyle(
    'Caption', fontName='LibSerif', fontSize=9, leading=13,
    alignment=TA_CENTER, textColor=TEXT_MUTED, spaceAfter=12
)
styles['Bullet'] = ParagraphStyle(
    'Bullet', fontName='LibSerif', fontSize=10.5, leading=17,
    alignment=TA_LEFT, spaceAfter=4, textColor=TEXT_PRIMARY,
    leftIndent=24, bulletIndent=12
)
styles['Verdict'] = ParagraphStyle(
    'Verdict', fontName='LibSerif', fontSize=11, leading=17,
    alignment=TA_LEFT, spaceAfter=6, textColor=SEM_ERROR,
    leftIndent=18, borderPadding=(6,6,6,6)
)

# ─── TOC Template ───
class TocDocTemplate(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        if hasattr(flowable, 'bookmark_name'):
            level = getattr(flowable, 'bookmark_level', 0)
            text = getattr(flowable, 'bookmark_text', '')
            key = getattr(flowable, 'bookmark_key', '')
            self.notify('TOCEntry', (level, text, self.page, key))

def add_heading(text, style_key, level=0):
    s = styles[style_key]
    key = 'h_%s' % hashlib.md5(text.encode()).hexdigest()[:8]
    p = Paragraph('<a name="%s"/><b>%s</b>' % (key, text), s)
    p.bookmark_name = text
    p.bookmark_level = level
    p.bookmark_text = text
    p.bookmark_key = key
    return p

def P(text, style_key='Body'):
    return Paragraph(text, styles[style_key])

def Code(text):
    return Paragraph(text.replace('<','&lt;').replace('>','&gt;'), styles['Code'])

def make_table(headers, rows, col_ratios=None):
    """Build a styled table with headers and rows."""
    hs = styles['TableHeader']
    cs = styles['TableCell']
    cc = styles['TableCellCenter']
    data = [[Paragraph('<b>%s</b>' % h, hs) for h in headers]]
    for row in rows:
        data.append([Paragraph(str(c), cs) for c in row])
    if col_ratios:
        cw = [r * AVAIL_W for r in col_ratios]
    else:
        cw = [AVAIL_W / len(headers)] * len(headers)
    t = Table(data, colWidths=cw, hAlign='CENTER')
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_FILL),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]
    for i in range(1, len(data)):
        bg = colors.white if i % 2 == 1 else TABLE_STRIPE
        style_cmds.append(('BACKGROUND', (0, i), (-1, i), bg))
    t.setStyle(TableStyle(style_cmds))
    return t

def callout_box(text, color=SEM_INFO):
    """Create a highlighted callout box."""
    s = ParagraphStyle(
        'callout_%s' % hashlib.md5(text.encode()).hexdigest()[:6],
        parent=styles['Body'], textColor=color,
        leftIndent=18, borderColor=color, borderWidth=1.5,
        borderPadding=(8,8,8,8), backColor=colors.HexColor('#f8f7f5')
    )
    return Paragraph(text, s)

# ═══════════════════════════════════════════════════════════
# BUILD DOCUMENT
# ═══════════════════════════════════════════════════════════

OUTPUT = '/home/z/my-project/download/cq-arch/CryptoQuant_Arquitectura_Final_v4.pdf'

doc = TocDocTemplate(
    OUTPUT, pagesize=A4,
    leftMargin=L_MARGIN, rightMargin=R_MARGIN,
    topMargin=T_MARGIN, bottomMargin=B_MARGIN,
    showBoundary=0
)

story = []

# ─── TOC ───
toc = TableOfContents()
toc.levelStyles = [
    ParagraphStyle('TOC1', fontName='LibSerif', fontSize=13, leading=20, leftIndent=20, spaceBefore=6),
    ParagraphStyle('TOC2', fontName='LibSerif', fontSize=11, leading=16, leftIndent=40, spaceBefore=3),
]
story.append(Paragraph('<b>Contenido</b>', ParagraphStyle(
    'TOCTitle', fontName='LibSerif', fontSize=22, leading=28,
    alignment=TA_LEFT, textColor=ACCENT, spaceAfter=18
)))
story.append(toc)
story.append(PageBreak())

# ═══════════════════════════════════════════════════════════
# 1. DECLARACION DE PRINCIPIOS
# ═══════════════════════════════════════════════════════════
story.append(add_heading('1. Declaracion de Principios', 'H1', 0))
story.append(Spacer(1, 6))

story.append(callout_box(
    'Este documento REEMPLAZA todos los documentos anteriores: '
    'STRATEGIC_PLAN.md, STRATEGIC_ROADMAP_V2.md, y los PDFs de analisis critico v1/v2/v3. '
    'A partir de este momento, este es el UNICO documento de referencia arquitectonica del proyecto.'
))

story.append(Spacer(1, 8))
story.append(P('<b>Este documento NO es:</b>'))
story.append(P('Una nueva auditoria completa. No es una lista infinita de ideas. No es un documento teorico. No propone nuevos modulos ni nuevos dashboards.'))
story.append(Spacer(1, 6))
story.append(P('<b>Este documento SI es:</b>'))
story.append(P('Una revision de las decisiones estructurales mas importantes del roadmap existente. Cada decision se evalua desde 4 perspectivas simultaneas: Quant Architect, Portfolio Manager, Risk Manager y CTO de una firma cuantitativa. Si una decision es correcta, se confirma. Si es incorrecta, se sustituye con una alternativa concreta. Si algo critico falta, se anade. Si algo es sobreingenieria, se elimina.'))
story.append(Spacer(1, 6))
story.append(P('<b>El objetivo es cerrar la arquitectura y comenzar a construir.</b>'))
story.append(P('Cada seccion de este documento contiene una decision final, no una propuesta. Las secciones con veredicto son ejecutivas: definen exactamente que se implementa, que se retrasa, y que se elimina.'))

# ═══════════════════════════════════════════════════════════
# 2. REVISION 1: SISTEMA DE PESOS
# ═══════════════════════════════════════════════════════════
story.append(Spacer(1, 24))
story.append(add_heading('2. Revision 1: Sistema de Pesos', 'H1', 0))
story.append(Spacer(1, 6))

story.append(add_heading('2.1 La propuesta actual', 'H2', 1))
story.append(P('El roadmap v2.0 proponia pesos fijos para el Strategy Decision Engine: Monte Carlo 30%, Walk Forward 25%, Backtest 25%, Operability 10%, Regime 10%. La idea intuitiva es atractiva: cada modulo contribuye una fraccion conocida al score final, y se ponderan por importancia relativa.'))
story.append(Spacer(1, 6))

story.append(add_heading('2.2 Por que los pesos fijos son incorrectos', 'H2', 1))
story.append(P('Los pesos fijos tienen tres problemas fundamentales que los hacen inaceptables para un sistema cuantitativo de trading:'))
story.append(Spacer(1, 4))
story.append(P('<b>Problema 1: Ignoran la confianza del modulo emisor.</b> Un Monte Carlo con 50 simulaciones tiene menos confianza estadistica que uno con 10,000 simulaciones. Sin embargo, con pesos fijos, ambos contribuyen el mismo 30% al score final. Esto es equivalente a darle el mismo peso al testimonio de un testigo presencial y al de alguien que escuchó un rumor.'))
story.append(P('<b>Problema 2: Son incompatibles con cambios de regimen.</b> En un mercado lateral de baja volatilidad, el backtest historico es mas predictivo que el Monte Carlo. En un mercado con volatilidad extrema (flash crash, evento de liquidez), el Monte Carlo y los kill switches son mas importantes que el backtest. Pesos fijos no pueden adaptarse a esta realidad.'))
story.append(P('<b>Problema 3: Crean una falsa sensacion de precision.</b> Un score de 73.5/100 sugiere precision que no existe. La diferencia entre 73.5 y 72.8 no es significativa dada la incertidumbre en los inputs. Los pesos fijos animan a tratar este numero como si tuviera mas informacion de la que realmente contiene.'))
story.append(Spacer(1, 6))

story.append(add_heading('2.3 La alternativa: Veto Jerarquico + Confidence-Weighted Scoring', 'H2', 1))
story.append(P('La arquitectura correcta para el SDE es un sistema de dos capas: vetos absolutos primero, scoring ponderado por confianza despues. Esto no es teoria; es la estructura exacta que implementaremos.'))
story.append(Spacer(1, 6))

story.append(P('<b>Capa 1: Vetos Absolutos (hard gates)</b>'))
story.append(P('Los vetos son binarios: pasas o no pasas. No hay pesos, no hay scores parciales. Si cualquier veto falla, la estrategia es REJECT. No importa que los demas modulos den scores perfectos. Esta capa se ejecuta PRIMERO y es obligatoria para TODAS las estrategias, sin excepcion.'))
story.append(Spacer(1, 4))

veto_headers = ['Veto', 'Condicion', 'Perfil Conservador', 'Perfil Moderado', 'Perfil Agresivo']
veto_rows = [
    ['Risk of Ruin', 'RoR > threshold', '< 1%', '< 3%', '< 5%'],
    ['Total Trades', 'Trades < min', '< 50', '< 40', '< 30'],
    ['Max Drawdown', 'DD > limit', '> 40%', '> 50%', '> 60%'],
    ['Walk-Forward Eff.', 'WFE < min', '< 40%', '< 30%', '< 20%'],
    ['Win Rate + Payoff', 'WR < 35% Y Payoff < 2.5:1', 'Ambos', 'Ambos', 'Ambos'],
]
story.append(Spacer(1, 10))
story.append(make_table(veto_headers, veto_rows, [0.18, 0.22, 0.20, 0.20, 0.20]))
story.append(Paragraph('Tabla 1. Vetos absolutos por perfil de riesgo', styles['Caption']))
story.append(Spacer(1, 8))

story.append(P('<b>Capa 2: Confidence-Weighted Scoring</b>'))
story.append(P('Solo si la estrategia pasa TODOS los vetos, se calcula un score compuesto. Pero los pesos no son fijos: cada modulo contribuye en proporcion a su propia confianza en el output que esta produciendo. Esto es fundamentalmente diferente de pesos fijos y resuelve los tres problemas anteriores.'))
story.append(Spacer(1, 4))
story.append(P('La formula es:'))
story.append(Spacer(1, 4))
story.append(Code(
    'CompositeScore = SUM(module_score[i] * module_confidence[i]) / SUM(module_confidence[i])\n\n'
    'Donde:\n'
    '  module_score[i]     = output normalizado del modulo i (0-100)\n'
    '  module_confidence[i] = confianza del modulo i en su propio output (0-1)\n\n'
    'Ejemplos de confidence:\n'
    '  MC con 10000 sims:   confidence = 0.95\n'
    '  MC con 100 sims:     confidence = 0.40\n'
    '  WF con 8 ventanas:   confidence = 0.90\n'
    '  WF con 2 ventanas:   confidence = 0.30\n'
    '  Backtest 2 anios:    confidence = 0.85\n'
    '  Backtest 30 dias:    confidence = 0.20'
))
story.append(Spacer(1, 8))

story.append(P('Esta arquitectura tiene tres propiedades criticas que los pesos fijos no tienen:'))
story.append(P('Primero, se adapta automaticamente a la calidad de los datos disponibles. Si el Monte Carlo se ejecuto con pocas simulaciones, su confianza baja y su influencia en el score compuesto disminuye proporcionalmente. No requiere intervencion manual ni cambios de configuracion.'))
story.append(P('Segundo, es naturalmente robusta frente a modulos con outputs ruidosos. Un modulo que produce scores con alta varianza entre ejecuciones deberia tener baja confianza, y por lo tanto bajo peso efectivo. Esto desincentiva la sobreoptimizacion de un solo modulo.'))
story.append(P('Tercero, es transparente y auditable. Cuando el SDE produce una decision, puede reportar exactamente cuanto peso efectivo tuvo cada modulo y por que. "El Monte Carlo contribuyo 42% del score porque su confianza era 0.95, mientras que el Walk-Forward solo contribuyo 15% porque solo tenia 2 ventanas". Esta transparencia es esencial para la confianza del usuario en el sistema.'))
story.append(Spacer(1, 8))

story.append(add_heading('2.4 Veredicto', 'H2', 1))
story.append(callout_box(
    '<b>VEREDICTO: Pesos fijos ELIMINADOS.</b> Se implementa Veto Jerarquico + Confidence-Weighted Scoring. '
    'Los vetos son absolutos y por perfil de riesgo. Los scores son ponderados por confianza del modulo emisor. '
    'No hay pesos hardcodeados en ningun lugar del sistema.',
    SEM_ERROR
))

# ═══════════════════════════════════════════════════════════
# 3. REVISION 2: VALIDATION GRADE
# ═══════════════════════════════════════════════════════════
story.append(Spacer(1, 24))
story.append(add_heading('3. Revision 2: Validation Grade', 'H1', 0))
story.append(Spacer(1, 6))

story.append(add_heading('3.1 La propuesta actual', 'H2', 1))
story.append(P('El roadmap v2.0 proponia un sistema de grados A+/A/B/C/D/F, similar al sistema academico estadounidense. La intencion era comunicar rapidamente la calidad de una estrategia con una etiqueta simple.'))
story.append(Spacer(1, 6))

story.append(add_heading('3.2 Por que los grados academicos son una simplificacion peligrosa', 'H2', 1))
story.append(P('Los grados tipo A/B/C/D/F tienen tres problemas que los hacen inadecuados para un sistema de trading:'))
story.append(Spacer(1, 4))
story.append(P('<b>Problema 1: Son teatrales, no operativos.</b> Un "Grade B" no te dice que hacer. Significa "bastante bueno" en terminos vagos, pero no especifica: debes asignar capital? Cuanto? Debes paper-tradear primero? La etiqueta comunica una sensacion, no una accion. En un sistema que aspira a ser semi-autonomo, la salida del SDE debe ser directamente accionable.'))
story.append(P('<b>Problema 2: Implican falsa precision.</b> La frontera entre B y C es arbitraria. Un score de 59.9 es C, un score de 60.0 es B. Pero la diferencia real es ruido estadistico. Los grados academicos sugieren que existe una distincion significativa donde solo hay ruido.'))
story.append(P('<b>Problema 3: No capturan la dimension de accion.</b> El sistema necesita distinguir entre "esta estrategia es excelente pero ya tiene suficiente capital asignado" (HOLD) y "esta estrategia es excelente y merece mas capital" (INCREASE). Los grados academicos no tienen esta dimension.'))
story.append(Spacer(1, 6))

story.append(add_heading('3.3 La alternativa: Action-First Classification', 'H2', 1))
story.append(P('El reemplazo es un sistema donde la clasificacion ES la accion. Cada estrategia recibe una Action Tag que define exactamente que hacer con ella, acompañada de un Confidence Score numerico que determina el tamano de posicion dentro de esa accion.'))
story.append(Spacer(1, 6))

action_headers = ['Action Tag', 'Significado', 'Accion de Capital', 'Rango de Confidence']
action_rows = [
    ['TRADE', 'Estrategia validada, lista para capital real', 'Asignar capital segun allocation method', '70-100'],
    ['WATCH', 'Estrategia prometedora, necesita mas datos', 'Paper trading solo, sin capital real', '40-69'],
    ['HOLD', 'Estrategia activa, mantener posicion actual', 'Mantener allocation actual, no aumentar', '50-69'],
    ['RETRAIN', 'Estrategia degradada, necesita ajuste', 'Reducir capital al 50%, reoptimizar', '25-49'],
    ['REJECT', 'Estrategia no valida', 'Cerrar posiciones, no asignar capital', '0-24'],
]
story.append(make_table(action_headers, action_rows, [0.12, 0.28, 0.30, 0.30]))
story.append(Paragraph('Tabla 2. Action-First Classification: cada tag es una accion directa', styles['Caption']))
story.append(Spacer(1, 8))

story.append(P('El Confidence Score (0-100) se calcula a partir del Confidence-Weighted Composite Score definido en la Revision 1. Este score determina el tamano de posicion dentro de la accion:'))
story.append(Spacer(1, 4))
story.append(Code(
    'Si Action = TRADE:\n'
    '  position_multiplier = confidence / 100\n'
    '  kelly_fraction = base_kelly * position_multiplier\n'
    '\n'
    'Si Action = WATCH:\n'
    '  Solo paper trading, no position sizing\n'
    '\n'
    'Si Action = HOLD:\n'
    '  Mantener posicion actual sin cambios\n'
    '\n'
    'Si Action = RETRAIN:\n'
    '  Reducir a 50% de posicion actual\n'
    '\n'
    'Si Action = REJECT:\n'
    '  Cerrar toda posicion'
))
story.append(Spacer(1, 8))

story.append(P('La interfaz TypeScript del SDE output queda asi:'))
story.append(Spacer(1, 4))
story.append(Code(
    'interface SDEDecision {\n'
    '  strategyId: string;\n'
    '  action: "TRADE" | "WATCH" | "HOLD" | "RETRAIN" | "REJECT";\n'
    '  confidence: number;              // 0-100\n'
    '  vetoResults: VetoCheck[];        // que vetos se evaluaron\n'
    '  moduleContributions: {           // transparencia total\n'
    '    moduleId: string;\n'
    '    score: number;                 // 0-100\n'
    '    confidence: number;            // 0-1, peso efectivo\n'
    '    effectiveWeight: number;       // peso real en la decision\n'
    '  }[];\n'
    '  capitalAction: "ALLOCATE" | "HOLD" | "REDUCE" | "EXIT";\n'
    '  targetAllocationPct: number;     // % del portfolio\n'
    '  allocationMethod: AllocationMethod;\n'
    '  reasoning: string;               // explicacion en lenguaje natural\n'
    '  nextReviewDate: Date;\n'
    '}'
))
story.append(Spacer(1, 8))

story.append(add_heading('3.4 Veredicto', 'H2', 1))
story.append(callout_box(
    '<b>VEREDICTO: Grados A+/A/B/C/D/F ELIMINADOS.</b> Se implementa Action-First Classification con 5 tags '
    '(TRADE/WATCH/HOLD/RETRAIN/REJECT) + Confidence Score numerico (0-100). Cada tag es directamente accionable. '
    'El Confidence Score determina position sizing dentro de la accion. No hay etiquetas ambiguas.',
    SEM_ERROR
))

# ═══════════════════════════════════════════════════════════
# 4. REVISION 3: CAPITAL ALLOCATION
# ═══════════════════════════════════════════════════════════
story.append(Spacer(1, 24))
story.append(add_heading('4. Revision 3: Capital Allocation', 'H1', 0))
story.append(Spacer(1, 6))

story.append(add_heading('4.1 Estado actual: 16 metodos, 0 consumidores', 'H2', 1))
story.append(P('El CapitalAllocationEngine actual implementa 16 metodos de allocation. Es codigo impresionante: Risk Parity con Spinu 2013, Markowitz con Gauss-Jordan, Kelly fraccional, volatilidad targeting. Pero tiene un problema critico: el Paper Trading Engine no lo usa. La funcion calculatePositionSize() es un simple availableCapital / remainingSlots que ignora completamente los 16 metodos.'))
story.append(P('Ademas, nadie decide QUE metodo usar y CUANDO. La eleccion es manual y arbitraria. Es como tener un garage lleno de ferraris pero ir caminando al trabajo porque nadie te dijo cual llave usar.'))
story.append(Spacer(1, 6))

story.append(add_heading('4.2 Cuales implementar para V1', 'H2', 1))
story.append(P('Para una primera version autonomo, necesitas exactamente 3 metodos. No 5, no 8, no 16. Tres. Cada uno con un proposito claro y una regla de seleccion definida:'))
story.append(Spacer(1, 6))

method_headers = ['Metodo', 'Funcion', 'Cuando se usa', 'Default']
method_rows = [
    ['Half-Kelly', 'Position sizing optimo para crecimiento a largo plazo', 'Default para estrategias individuales con Action=TRADE', 'SI'],
    ['Risk Parity', 'Allocacion portfolio-level entre estrategias no correlacionadas', 'Cuando 2+ estrategias activas simultaneamente', 'NO'],
    ['Volatility Targeting', 'Reducir exposicion cuando volatilidad sube', 'Cuando volatilidad del regimen > percentil 75', 'NO'],
]
story.append(make_table(method_headers, method_rows, [0.14, 0.30, 0.36, 0.20]))
story.append(Paragraph('Tabla 3. Los 3 metodos de Capital Allocation para V1', styles['Caption']))
story.append(Spacer(1, 8))

story.append(P('<b>Half-Kelly como default</b> es la decision correcta porque: es el unico metodo que maximiza crecimiento a largo plazo con riesgo controlado (demostrado por Kelly 1956 y confirmado por Thorp); usar la mitad del Kelly full es el estandar de la industria porque reduce la varianza del crecimiento a cambio de una pequena reduccion en retorno esperado; y es robusto frente a errores de estimacion en los parametros de entrada (win rate, payoff ratio), algo critico en crypto donde los datos son ruidosos.'))
story.append(Spacer(1, 4))
story.append(P('<b>Risk Parity para portfolio-level</b> porque: es superior a equal-weight ya que ajusta por volatilidad (una estrategia con volatilidad 5% no merece la misma allocation que una con volatilidad 40%); la implementacion Spinu 2013 ya existe en el codigo actual y es correcta; y es la herramienta adecuada cuando el SDE gestiona multiples estrategias simultaneamente.'))
story.append(Spacer(1, 4))
story.append(P('<b>Volatility Targeting como mecanismo de defensa</b> porque: es la respuesta automatica a regimen de alta volatilidad (cuando el mercado se vuelve erratico, reduces exposure); se implementa con una sola formula: target_vol / realized_vol; y es el complemento natural del regime detector (simplificado a volatilidad para V1).'))
story.append(Spacer(1, 6))

story.append(add_heading('4.3 Que eliminar o retrasar', 'H2', 1))

elim_headers = ['Metodo', 'Accion', 'Razon']
elim_rows = [
    ['FIXED_AMOUNT', 'ELIMINAR', 'No escala con capital, inutil para portfolio'],
    ['FIXED_RATIO', 'ELIMINAR', 'Demasiado conservador, no adaptativo'],
    ['FIXED_FRACTIONAL', 'RETRASAR', 'Subconjunto de Kelly, redundante para V1'],
    ['MIN_VARIANCE', 'ELIMINAR', 'Subset de Markowitz, mismos problemas de estimacion'],
    ['RL_ALLOCATION', 'ELIMINAR', 'Q-table es un juguete sin simulacion real'],
    ['META_ALLOCATION', 'ELIMINAR', 'Necesita datos que no tenemos todavia'],
    ['SCORE_BASED', 'RETRASAR', 'Redundante con Confidence Score del SDE'],
    ['REGIME_BASED', 'RETRASAR', 'Se reemplaza por Volatility Targeting para V1'],
    ['ADAPTIVE', 'RETRASAR', 'Complejidad innecesaria para V1'],
    ['CUSTOM_COMPOSITE', 'RETRASAR', 'Solo util cuando hay 4+ metodos activos'],
    ['MEAN_VARIANCE', 'RETRASAR', 'Demasiado sensible a errores de estimacion en crypto'],
    ['EQUAL_WEIGHT', 'RETRASAR', 'Risk Parity es superior, este es fallback'],
    ['MAX_DRAWDOWN_CONTROL', 'RETRASAR', 'Se reemplaza por kill switches para V1'],
]
story.append(make_table(elim_headers, elim_rows, [0.22, 0.14, 0.64]))
story.append(Paragraph('Tabla 4. Metodos de Capital Allocation: eliminar o retrasar', styles['Caption']))
story.append(Spacer(1, 8))

story.append(add_heading('4.4 Veredicto', 'H2', 1))
story.append(callout_box(
    '<b>VEREDICTO: 16 metodos reducidos a 3 para V1.</b> Half-Kelly (default, position sizing), '
    'Risk Parity (portfolio allocation), Volatility Targeting (regimen defensivo). '
    '4 metodos ELIMINADOS del codigo. 9 metodos RETRASADOS a V2+. '
    'El Paper Trading Engine se conecta al CapitalAllocationEngine usando estos 3 metodos.',
    SEM_ERROR
))

# ═══════════════════════════════════════════════════════════
# 5. REVISION 4: DECISION AUDIT ENGINE
# ═══════════════════════════════════════════════════════════
story.append(Spacer(1, 24))
story.append(add_heading('5. Revision 4: Decision Audit Engine', 'H1', 0))
story.append(Spacer(1, 6))

story.append(add_heading('5.1 El concepto', 'H2', 1))
story.append(P('El Decision Audit Engine es un mecanismo que registra las predicciones del SDE y las compara con los resultados reales. Cuando el SDE decide que una estrategia es TRADE con confidence 82%, el audit engine registra esa prediccion. Treinta dias despues, compara: la estrategia realmente rindio como se predijo? Si la estrategia produjo +18% cuando el SDE predijo rendimiento positivo, la decision fue correcta. Si produjo -12%, la decision fue incorrecta. Esta comparacion sistematica es lo que convierte al SDE de un sistema de opinions a un sistema que aprende.'))
story.append(Spacer(1, 6))

story.append(add_heading('5.2 Deberia existir desde el principio?', 'H2', 1))
story.append(P('<b>Si. Absolutamente. Sin excepcion.</b> El Decision Audit Engine no es un modulo separado que se anade despues; es una propiedad fundamental del SDE que debe existir desde la primera linea de codigo. Sin el, el SDE es un sistema de open-loop: produce decisiones pero nunca verifica si fueron correctas. Es como conducir un auto con los ojos vendados, confiando en que el GPS te dijo que giraras a la derecha hace 10 minutos pero sin poder verificar si llegaste al destino.'))
story.append(P('Ademas, el Decision Audit Engine es MAS importante que mejorar Monte Carlo o Walk-Forward. La razon es directa: mejorar MC o WF optimiza los INPUTS del SDE, pero el audit engine optimiza el OUTPUT. Puedes tener los mejores inputs del mundo, pero si nunca verificas si las decisiones finales fueron correctas, no sabes si el sistema funciona. El audit engine es el feedback loop que cierra el circuito y permite que el sistema mejore con el tiempo.'))
story.append(Spacer(1, 6))

story.append(add_heading('5.3 Arquitectura minima viable', 'H2', 1))
story.append(P('La implementacion minima no es un modulo separado. Son dos extensiones al modelo DecisionLog existente en Prisma y un job de reconciliacion:'))
story.append(Spacer(1, 4))
story.append(Code(
    '// Extension 1: Campos de prediccion en DecisionLog\n'
    'model DecisionLog {\n'
    '  // ... campos existentes ...\n'
    '  \n'
    '  // Prediccion al momento de la decision\n'
    '  predictedAction      String    // TRADE/WATCH/HOLD/RETRAIN/REJECT\n'
    '  predictedConfidence  Float     // 0-100\n'
    '  predictedReturnPct   Float?    // retorno esperado (nullable)\n'
    '  predictedMaxDD       Float?    // drawdown maximo esperado\n'
    '  \n'
    '  // Resultado real (llenado por reconciliation job)\n'
    '  actualReturnPct      Float?    // retorno real despues de N dias\n'
    '  actualMaxDD          Float?    // drawdown maximo real\n'
    '  actualOutcome        String?   // PROFIT/LOSS/BREAKEVEN\n'
    '  predictionCorrect    Boolean?  // la prediccion fue correcta?\n'
    '  reconciledAt         DateTime? // cuando se reconcilio\n'
    '  reconciliationDays   Int?      // dias entre decision y reconciliacion\n'
    '}'
))
story.append(Spacer(1, 6))

story.append(Code(
    '// Extension 2: Reconciliation job (se ejecuta diariamente)\n'
    'async function reconcileDecisions() {\n'
    '  // Buscar decisiones de hace >= 30 dias sin reconciliar\n'
    '  const unreconciled = await prisma.decisionLog.findMany({\n'
    '    where: {\n'
    '      reconciledAt: null,\n'
    '      createdAt: { lte: subDays(now(), 30) }\n'
    '    }\n'
    '  });\n'
    '  \n'
    '  for (const decision of unreconciled) {\n'
    '    // Calcular resultado real desde paper trading trades\n'
    '    const actualReturn = calculateActualReturn(decision);\n'
    '    const actualDD = calculateActualMaxDD(decision);\n'
    '    \n'
    '    // Comparar prediccion vs realidad\n'
    '    const predictionCorrect = evaluatePrediction(decision, actualReturn);\n'
    '    \n'
    '    await prisma.decisionLog.update({\n'
    '      where: { id: decision.id },\n'
    '      data: {\n'
    '        actualReturnPct: actualReturn,\n'
    '        actualMaxDD: actualDD,\n'
    '        predictionCorrect,\n'
    '        reconciledAt: new Date(),\n'
    '        reconciliationDays: 30\n'
    '      }\n'
    '    });\n'
    '  }\n'
    '}'
))
story.append(Spacer(1, 8))

story.append(P('Con esta arquitectura minima, el sistema puede responder a la pregunta mas importante que un quant puede hacer: "de las ultimas 100 decisiones del SDE, cuantas fueron correctas?" Si la tasa de acierto es < 50%, el SDE esta funcionando peor que azar y necesita recalibracion. Si es > 70%, el sistema tiene edge real. Sin el audit engine, esta pregunta es imposible de responder.'))
story.append(Spacer(1, 6))

story.append(add_heading('5.4 Posicion en el roadmap', 'H2', 1))
story.append(P('El Decision Audit Engine NO es un sprint separado. Se implementa como parte del Sprint 1 (Strategy Decision Engine), porque es una propiedad intrinseca del SDE, no un addon. Los campos de prediccion se anaden al mismo tiempo que los campos de MC/WF scores. El reconciliation job se implementa en Sprint 1 como un cron job diario.'))
story.append(Spacer(1, 6))

story.append(add_heading('5.5 Veredicto', 'H2', 1))
story.append(callout_box(
    '<b>VEREDICTO: Decision Audit Engine es CORE FEATURE del SDE, no un modulo separado.</b> '
    'Se implementa en Sprint 1 junto con el SDE. Los campos de prediccion se anaden a DecisionLog. '
    'El reconciliation job se ejecuta diariamente. Sin esto, el SDE es open-loop y no puede aprender.',
    SEM_ERROR
))

# ═══════════════════════════════════════════════════════════
# 6. REVISION 5: EVOLUTION TREE
# ═══════════════════════════════════════════════════════════
story.append(Spacer(1, 24))
story.append(add_heading('6. Revision 5: Evolution Tree', 'H1', 0))
story.append(Spacer(1, 6))

story.append(add_heading('6.1 Estado actual: visual sin valor operativo', 'H2', 1))
story.append(P('El Evolution Tree actual es puramente visual. Muestra un arbol de estrategias padre-hija con estadisticas de backtest y porcentaje de mejora, pero no tiene ningun mecanismo de promocion automatica. Si una estrategia hija supera a la padre por 40%, el arbol lo muestra con un badge verde, pero nadie hace nada al respecto. El usuario tiene que manualmente activar la hija y desactivar la padre. Para un sistema semi-autonomo, esto es inaceptable.'))
story.append(Spacer(1, 6))

story.append(add_heading('6.2 Conversion a fuente de candidatos para el SDE', 'H2', 1))
story.append(P('La Evolution Tree debe convertirse en una fuente de candidatos para el SDE. Cuando una estrategia descendiente supera a su ancestro por un umbral definido (ej: improvementPct > 15%), el arbol genera automaticamente un evento que el SDE evalua. El flujo es:'))
story.append(Spacer(1, 4))
story.append(Code(
    '1. Evolution Engine produce estrategia hija\n'
    '2. Si improvementPct > PROMOTION_THRESHOLD (default: 15%):\n'
    '   a. Generar evento "CANDIDATE_PROMOTION"\n'
    '   b. SDE evalua la hija: MC + WF + vetos\n'
    '   c. Si SDE verdict = TRADE:\n'
    '      - Sugerir INCREASE_CAPITAL para la hija\n'
    '      - Sugerir PAUSED para la madre\n'
    '      - Migracion gradual de capital (no swap instantaneo)\n'
    '   d. Si SDE verdict != TRADE:\n'
    '      - Registrar que la hija no paso validacion\n'
    '      - La madre mantiene su status\n'
    '3. Si improvementPct <= PROMOTION_THRESHOLD:\n'
    '   - La hija se queda en el arbol como referencia\n'
    '   - No se envia al SDE'
))
story.append(Spacer(1, 8))

story.append(P('<b>No es demasiado pronto para esto?</b> No. La promocion automatica de descendientes es el mecanismo natural que cierra el loop entre evolucion y deployment de capital. Sin el, la evolucion produce estrategias mejores que nadie usa, lo que es optimizacion sin retorno. El SDE ya debe existir para Sprint 1, y la integracion con Evolution Tree es simplemente conectar dos componentes que ya existen.'))
story.append(Spacer(1, 4))
story.append(P('Sin embargo, la migracion de capital debe ser gradual, no instantanea. Cuando el SDE aprueba una hija para TRADE, no se transfiere todo el capital de la madre a la hija en un solo paso. Se migra en tramos: primero 25% del capital de la madre, luego 50%, y finalmente el 100% restante, con verificacion del SDE en cada paso. Si la hija no rinde como se esperaba durante la migracion, el proceso se detiene automaticamente.'))
story.append(Spacer(1, 6))

story.append(add_heading('6.3 Veredicto', 'H2', 1))
story.append(callout_box(
    '<b>VEREDICTO: Evolution Tree pasa de visual a fuente de candidatos del SDE.</b> '
    'Promocion automatica con umbral de 15% de mejora. El SDE valida antes de promover. '
    'Migracion gradual de capital en tramos de 25%/50%/100%. '
    'Se implementa en Sprint 2 (despues de que el SDE funcione en Sprint 1).',
    SEM_ERROR
))

# ═══════════════════════════════════════════════════════════
# 7. REVISION 6: CUELLO DE BOTELLA REAL
# ═══════════════════════════════════════════════════════════
story.append(Spacer(1, 24))
story.append(add_heading('7. Revision 6: El Cuello de Botella Real', 'H1', 0))
story.append(Spacer(1, 6))

story.append(callout_box(
    '<b>La unica cosa que impide que esta plataforma llegue a operar de forma semi-autonoma es: '
    'el feedback loop roto.</b>',
    SEM_ERROR
))
story.append(Spacer(1, 8))

story.append(P('El sistema puede backtestear, puede simular Monte Carlo, puede hacer Walk-Forward, puede evolucionar estrategias, puede hacer paper trading. Pero no puede OBSERVAR SUS PROPIOS RESULTADOS Y AJUSTARSE.'))
story.append(P('Este es un sistema de open-loop: produce decisiones (o informacion que un humano convierte en decisiones), pero nunca verifica si esas decisiones fueron correctas, ni ajusta su comportamiento en base a los resultados. Es como un termostato que solo puede subir la temperatura pero nunca verificar si la temperatura actual es la correcta.'))
story.append(Spacer(1, 4))
story.append(P('Los tres componentes del feedback loop roto son:'))
story.append(Spacer(1, 4))
story.append(P('<b>1. El Decision Audit Engine no existe.</b> El sistema no registra predicciones ni las compara con resultados reales. No sabe si sus decisiones fueron correctas o incorrectas. Esto ya se trato en la Revision 4 y la solucion es la misma: campos de prediccion en DecisionLog + reconciliation job diario.'))
story.append(P('<b>2. El feedback-loop-engine.ts existe pero esta desconectado.</b> El archivo existe en el codigo, pero no hay mecanismo automatico que alimente los resultados de paper trading de vuelta a la optimizacion de parametros. El sistema evoluciona estrategias con backtests historicos pero no aprende de las operaciones en vivo. Es como estudiar para un examen con apuntes viejos pero nunca verificar las respuestas del examen real.'))
story.append(P('<b>3. Los kill switches no son automaticos.</b> Actualmente, si una estrategia pierde catastroficamente, el usuario tiene que pausarla manualmente. Para un sistema semi-autonomo, esto es inaceptable. Los kill switches deben ser automaticos (portfolio DD > 20% = pausar todo, estrategia DD > 30% = pausar estrategia), como se define en la Revision 7.'))
story.append(Spacer(1, 6))

story.append(P('Todo lo demas es optimizacion de inputs: mejores simulaciones MC, mejores analisis WF, mejor deteccion de regimen. Pero optimizar inputs sin cerrar el feedback loop es como calibrar un telescopio con mas precision pero nunca mirar a traves de el para verificar si las estrellas estan donde predijiste. El feedback loop es lo que convierte un sistema de informacion en un sistema de aprendizaje.'))
story.append(Spacer(1, 6))

story.append(add_heading('7.1 Veredicto', 'H2', 1))
story.append(callout_box(
    '<b>VEREDICTO: El cuello de botella es el feedback loop roto.</b> Todo lo demas es secundario. '
    'El Decision Audit Engine (Revision 4) es la prioridad numero 1 porque es la primera mitad del loop. '
    'Los kill switches automaticos son la segunda mitad. Ambos se implementan en Sprint 1 y Sprint 3 respectivamente.',
    SEM_ERROR
))

# ═══════════════════════════════════════════════════════════
# 8. REVISION 7: QUE NO CONSTRUIR
# ═══════════════════════════════════════════════════════════
story.append(Spacer(1, 24))
story.append(add_heading('8. Revision 7: Que NO Construir', 'H1', 0))
story.append(Spacer(1, 6))

story.append(P('Esta es la revision mas importante. Cada decision de "no construir" libera tiempo y reduce complejidad. Cada pieza de sobreingenieria eliminada reduce la superficie de ataque para bugs y la carga cognitiva para el desarrollador.'))
story.append(Spacer(1, 8))

story.append(add_heading('8.1 Eliminar del codigo', 'H2', 1))
story.append(P('Estos metodos y modulos se eliminan del codigo fuente porque añaden complejidad sin retorno operativo:'))

elim2_headers = ['Componente', 'Razon de eliminacion']
elim2_rows = [
    ['RL_ALLOCATION (Q-table)', 'Juguete sin entorno de simulacion. No hay forma de entrenar el Q-table con datos reales. Produce allocations arbitrarias.'],
    ['META_ALLOCATION', 'Necesita historial de rendimiento de multiples allocation methods, que no existe. Es una meta-optimizacion sin datos.'],
    ['FIXED_AMOUNT', 'No escala con tamano de portfolio. Asignar $100 por trade funciona con $1000 pero no con $100,000.'],
    ['FIXED_RATIO (Ryan Jones)', 'Demasiado conservador para crypto. El delta-based scaling reduce position size cuando deberia aumentarse en tendencias fuertes.'],
    ['MIN_VARIANCE portfolio', 'Subset de Markowitz con los mismos problemas de estimacion. No añade nada que Risk Parity no cubra mejor.'],
]
story.append(make_table(elim2_headers, elim2_rows, [0.25, 0.75]))
story.append(Paragraph('Tabla 5. Componentes a eliminar del codigo', styles['Caption']))
story.append(Spacer(1, 10))

story.append(add_heading('8.2 Retrasar hasta V2+', 'H2', 1))
story.append(P('Estos componentes son validos pero no son necesarios para que el sistema funcione. Se retrasan hasta que V1 demuestre que el SDE produce decisiones correctas:'))

delay_headers = ['Componente', 'Cuando reactivar', 'Condicion para reactivar']
delay_rows = [
    ['Monte Carlo Block Bootstrap', 'V2', 'Cuando el MC actual demuestre que el shuffle destruye estructura temporal significativa'],
    ['MC Stress Scenarios', 'V2', 'Cuando el paper trading tenga 100+ trades y el baseline MC funcione'],
    ['WF Parameter Drift Analysis', 'V2', 'Cuando el WF actual demuestre que parameterStability no es suficiente'],
    ['WF IS vs OOS Equity Curves UI', 'V2', 'Cuando el WF tenga UI y los datos sean utiles para el usuario'],
    ['WF Overfitting Probability', 'V2', 'Como metrica adicional, no como veto'],
    ['Market Regime Detector 3 capas', 'V2', 'V1 usa solo volatilidad; anadir tendencia y liquidez cuando V1 funcione'],
    ['Cross-strategy correlation', 'V2', 'Cuando haya 3+ estrategias activas simultaneamente'],
    ['MEAN_VARIANCE (Markowitz)', 'V2', 'Cuando los datos de covarianza sean estables y significativos'],
    ['ADAPTIVE position sizing', 'V3', 'Complejidad innecesaria hasta que el sistema tenga track record'],
    ['CUSTOM_COMPOSITE allocation', 'V3', 'Solo util cuando haya 4+ metodos de allocation activos'],
    ['Execution Layer (Jupiter)', 'V3', 'DESPUES de que paper trading demuestre Sharpe > 1.0 y kill switches esten probados'],
]
story.append(make_table(delay_headers, delay_rows, [0.25, 0.12, 0.63]))
story.append(Paragraph('Tabla 6. Componentes a retrasar hasta V2/V3', styles['Caption']))
story.append(Spacer(1, 10))

story.append(add_heading('8.3 Simplificar', 'H2', 1))
story.append(P('Estos componentes existen pero necesitan simplificacion para ser utiles en V1:'))

simpl_headers = ['Componente', 'Estado actual', 'Simplificacion V1']
simpl_rows = [
    ['Validation Grade', 'A+/A/B/C/D/F', 'Action-First: TRADE/WATCH/HOLD/RETRAIN/REJECT'],
    ['Sistema de pesos', 'Fijos (MC 30%, WF 25%, etc.)', 'Veto + Confidence-Weighted Scoring'],
    ['Capital Allocation', '16 metodos', '3 metodos: Half-Kelly, Risk Parity, Vol Targeting'],
    ['Regime Detection', '3 capas (vol + trend + liquidity)', '1 capa: volatilidad unica'],
    ['Evolution Tree', 'Visual sin promocion', 'Fuente de candidatos para SDE con umbral 15%'],
    ['Kill Switches', 'Solo manual', 'Automaticos: portfolio 20%, estrategia 30%, posicion 50%'],
    ['DecisionLog schema', 'Sin campos de prediccion', 'Campos de prediccion + reconciliacion'],
    ['parameterStability (WF)', 'Es win rate stability, no drift', 'Renombrar a winRateStability, anadir drift real en V2'],
]
story.append(make_table(simpl_headers, simpl_rows, [0.20, 0.35, 0.45]))
story.append(Paragraph('Tabla 7. Simplificaciones para V1', styles['Caption']))
story.append(Spacer(1, 10))

story.append(add_heading('8.4 Sobreingenieria detectada', 'H2', 1))
story.append(P('Tres patrones de sobreingenieria claros se detectaron en el roadmap v2.0:'))
story.append(Spacer(1, 4))
story.append(P('<b>1. Explosion de metodos de allocation (16 metodos).</b> Es tentador implementar todos los metodos del libro de texto, pero en un sistema que no ha demostrado todavia que puede tomar una sola decision correcta, tener 16 formas de asignar capital es indistinguible de no tener ninguna. La paradoja de la eleccion aplicada al quant trading: mas opciones no significa mejores decisiones, significa mas tiempo decidiendo cual usar. V1 necesita exactamente los 3 metodos que sabemos que funcionan.'))
story.append(P('<b>2. Deteccion de regimen de 3 capas.</b> Volatilidad + tendencia + liquidez es el modelo completo que usan los fondos cuantitativos institucionales. Pero un sistema que no ha cerrado su feedback loop no necesita un modelo institucional de regimen. Necesita saber si el mercado esta tranquilo o agitado. Esa informacion la da la volatilidad sola. Las otras dos capas se anaden cuando haya datos para calibrarlas y el SDE demuestre que la primera capa produce valor.'))
story.append(P('<b>3. Block Bootstrap en Monte Carlo para V1.</b> El Fisher-Yates shuffle actual es estadisticamente inferior al Block Bootstrap porque destruye la estructura temporal de los trades. Pero la pregunta relevante no es "cual es estadisticamente superior?" sino "la diferencia afecta las decisiones del SDE?". Si el shuffle y el block bootstrap producen el mismo verdict (TRADE vs REJECT), la mejora es academica, no operativa. Se implementa en V2 si el SDE muestra que los resultados del shuffle son inconsistentes con el rendimiento real.'))
story.append(Spacer(1, 8))

story.append(add_heading('8.5 Veredicto', 'H2', 1))
story.append(callout_box(
    '<b>VEREDICTO: 5 componentes ELIMINADOS, 11 RETRASADOS, 8 SIMPLIFICADOS.</b> '
    'La regla es: si no cierra el feedback loop o no produce una decision accionable, no se construye en V1. '
    'Sobreingenieria = complejidad sin retorno operativo. Cada linea de codigo que no contribuye '
    'directamente a producir decisiones correctas es deuda tecnica.',
    SEM_ERROR
))

# ═══════════════════════════════════════════════════════════
# 9. ARQUITECTURA FINAL CONSOLIDADA
# ═══════════════════════════════════════════════════════════
story.append(Spacer(1, 24))
story.append(add_heading('9. Arquitectura Final Consolidada', 'H1', 0))
story.append(Spacer(1, 6))

story.append(add_heading('9.1 Pipeline completo', 'H2', 1))
story.append(P('El pipeline de datos del sistema, desde la señal hasta la ejecucion, queda definido de la siguiente manera:'))
story.append(Spacer(1, 6))

pipeline_headers = ['Etapa', 'Componente', 'Input', 'Output', 'Estado']
pipeline_rows = [
    ['1', 'Backtest Engine', 'TradingSystem + OHLCV', 'BacktestResult + metricas', 'EXISTE'],
    ['2', 'Monte Carlo Simulator', 'Trades PnL + config', 'MCResult (RoR, P95 DD, prob. profit)', 'EXISTE'],
    ['3', 'Walk-Forward Engine', 'TradingSystem + OHLCV', 'WFResult (WFE, stability, robustness)', 'EXISTE'],
    ['4', 'Operability Score', 'Token + fees + slippage', 'Score 0-100 + viabilidad', 'EXISTE'],
    ['5', 'Strategy Decision Engine', 'Backtest + MC + WF + Operability', 'SDEDecision (action + confidence + capital)', 'NUEVO'],
    ['6', 'Capital Allocation', 'SDEDecision + portfolio state', 'Position size + method', 'CONECTAR'],
    ['7', 'Paper Trading', 'Signals + position size', 'Trades + PnL + track record', 'EXISTE'],
    ['8', 'Decision Audit', 'SDEDecision + actual results', 'predictionCorrect + reconciliation', 'NUEVO'],
    ['9', 'Kill Switches', 'Portfolio DD + strategy DD', 'Auto-pause + alerts', 'NUEVO'],
    ['10', 'Execution Layer', 'Validated signals + real wallet', 'On-chain transactions', 'V3'],
]
story.append(make_table(pipeline_headers, pipeline_rows, [0.06, 0.18, 0.24, 0.34, 0.18]))
story.append(Paragraph('Tabla 8. Pipeline completo del sistema', styles['Caption']))
story.append(Spacer(1, 10))

story.append(add_heading('9.2 SDE: Logica de decision completa', 'H2', 1))
story.append(P('El Strategy Decision Engine ejecuta el siguiente algoritmo para cada estrategia evaluada:'))
story.append(Spacer(1, 4))
story.append(Code(
    'function evaluateStrategy(strategyId, riskProfile):\n'
    '\n'
    '  // CAPA 1: VETOS ABSOLUTOS\n'
    '  backtest = loadBacktestResults(strategyId)\n'
    '  mc = runMonteCarlo(strategyId)\n'
    '  wf = runWalkForward(strategyId)\n'
    '\n'
    '  if mc.riskOfRuin > ROR_THRESHOLD[riskProfile]: return REJECT\n'
    '  if backtest.totalTrades < MIN_TRADES[riskProfile]: return REJECT\n'
    '  if backtest.maxDrawdownPct > MAX_DD[riskProfile]: return REJECT\n'
    '  if wf.aggregateWFE < MIN_WFE[riskProfile]: return REJECT\n'
    '  if backtest.winRate < 0.35 AND backtest.payoffRatio < 2.5: return REJECT\n'
    '\n'
    '  // CAPA 2: CONFIDENCE-WEIGHTED SCORING\n'
    '  scores = [\n'
    '    { id: "mc",     score: mc.compositeScore,  confidence: mc.simCount / 10000 },\n'
    '    { id: "wf",     score: wf.robustnessScore, confidence: wf.windowCount / 8 },\n'
    '    { id: "bt",     score: backtest.sharpeNormalized, confidence: backtest.dataYears / 2 },\n'
    '    { id: "oper",   score: operabilityScore,   confidence: dataQualityScore / 100 },\n'
    '  ]\n'
    '\n'
    '  composite = SUM(score * confidence) / SUM(confidence)\n'
    '\n'
    '  // CAPA 3: ACTION DETERMINATION\n'
    '  if composite >= 70: action = TRADE\n'
    '  else if composite >= 50: action = WATCH  // paper trading\n'
    '  else if composite >= 40: action = HOLD   // mantener, no aumentar\n'
    '  else if composite >= 25: action = RETRAIN\n'
    '  else: action = REJECT\n'
    '\n'
    '  // CAPA 4: CAPITAL RECOMMENDATION\n'
    '  if action == TRADE:\n'
    '    method = selectAllocationMethod(regime, activeStrategies)\n'
    '    targetPct = calculateAllocation(method, composite, portfolio)\n'
    '  \n'
    '  // CAPA 5: AUDIT REGISTRATION\n'
    '  logDecision(strategyId, action, composite, predictedReturn, predictedDD)\n'
    '\n'
    '  return { action, confidence: composite, capitalAction, method, targetPct }'
))
story.append(Spacer(1, 10))

story.append(add_heading('9.3 Modelo de datos consolidado', 'H2', 1))
story.append(P('Los cambios al schema de Prisma necesarios para soportar la arquitectura final son:'))

schema_headers = ['Modelo', 'Campos nuevos', 'Proposito']
schema_rows = [
    ['DecisionLog', 'predictedAction, predictedConfidence, predictedReturnPct, predictedMaxDD, actualReturnPct, actualMaxDD, actualOutcome, predictionCorrect, reconciledAt, reconciliationDays', 'Decision Audit Engine'],
    ['DecisionLog', 'mcRiskOfRuin, wfe, overfittingScore, robustnessScore, stabilityScore, vetoResults (JSON), moduleContributions (JSON)', 'SDE output tracking'],
    ['TradingSystem', 'sdeAction, sdeConfidence, sdeLastReview, sdeNextReview', 'Estado del SDE por estrategia'],
    ['RiskBudget (NUEVO)', 'maxPortfolioDD, maxStrategyDD, maxDailyVaR, maxConcentrationPct, maxSectorPct, maxChainPct', 'Kill switches + allocation limits'],
    ['PortfolioSnapshot (NUEVO)', 'totalValue, totalDD, activeStrategies, totalExposurePct, equityCurve (JSON), calculatedAt', 'Portfolio-level tracking'],
]
story.append(make_table(schema_headers, schema_rows, [0.16, 0.54, 0.30]))
story.append(Paragraph('Tabla 9. Cambios al schema de Prisma', styles['Caption']))
story.append(Spacer(1, 10))

# ═══════════════════════════════════════════════════════════
# 10. ROADMAP REVISADO
# ═══════════════════════════════════════════════════════════
story.append(Spacer(1, 24))
story.append(add_heading('10. Roadmap Revisado', 'H1', 0))
story.append(Spacer(1, 6))

story.append(P('El roadmap original tenia 6 sprints en 17 semanas. El roadmap revisado tiene 4 sprints en 8-10 semanas. La reduccion viene de eliminar sobreingenieria, retrasar funcionalidades no esenciales, y priorizar el cierre del feedback loop sobre la expansion de capacidades.'))
story.append(Spacer(1, 8))

story.append(add_heading('10.1 Sprint 0: Foundation Fixes [3-4 dias]', 'H2', 1))

s0_headers = ['ID', 'Tarea', 'Detalle', 'Esfuerzo']
s0_rows = [
    ['S0.1', 'Evolution PRNG', 'Math.random() a seeded LCG en mutateParams() (14 llamadas)', '2h'],
    ['S0.2', 'Capital Allocation a Paper Trading', 'Reemplazar calculatePositionSize() con CapitalAllocationEngine (3 metodos)', '3h'],
    ['S0.3', 'PAUSED bloquea Paper Trading', 'Si strategy.status=PAUSED, skip en paper trading scan', '1h'],
    ['S0.4', 'Bug B5', 'saveControlsMutation setTimeout(500) a proper mutation', '1h'],
    ['S0.5', 'Bug B6', 'smart-money-sync/route.ts import incorrecto', '30min'],
    ['S0.6', 'Bug B7', 'phase-strategy-engine.ts token type incompleto', '1h'],
    ['S0.7', 'Bug B8/B9', 'strategy-marketplace.tsx CATEGORY_META missing bg', '1h'],
    ['S0.8', 'Renombrar decision-engine', 'decision-engine.ts a token-decision-engine.ts', '30min'],
]
story.append(make_table(s0_headers, s0_rows, [0.08, 0.22, 0.52, 0.18]))
story.append(Paragraph('Tabla 10. Sprint 0: Foundation Fixes', styles['Caption']))
story.append(Spacer(1, 10))

story.append(add_heading('10.2 Sprint 1: Strategy Decision Engine [2-3 semanas]', 'H2', 1))
story.append(P('<b>Este es el sprint que transforma la plataforma de herramienta de analisis a sistema de trading.</b>'))
story.append(Spacer(1, 4))

s1_headers = ['ID', 'Tarea', 'Prioridad']
s1_rows = [
    ['S1.1', 'Crear strategy-decision-engine.ts con pipeline: vetos a scores a action a capital', 'P0'],
    ['S1.2', 'Extender DecisionLog con campos de SDE + prediccion (audit engine)', 'P0'],
    ['S1.3', 'Implementar Confidence-Weighted Scoring (sin pesos fijos)', 'P0'],
    ['S1.4', 'API: /api/strategy-decision/validate', 'P0'],
    ['S1.5', 'API: /api/strategy-decision/portfolio-review', 'P0'],
    ['S1.6', 'Conectar MC output al SDE (RoR a veto, P95 DD a allocation)', 'P0'],
    ['S1.7', 'Conectar WF output al SDE (WFE a robustness, stability a stability)', 'P0'],
    ['S1.8', 'Reconciliation job diario (Decision Audit Engine)', 'P0'],
    ['S1.9', 'API: /api/strategy-decision/status (dashboard de estado)', 'P1'],
    ['S1.10', 'UI: Decision Dashboard (tabla con action, confidence, capital)', 'P1'],
]
story.append(make_table(s1_headers, s1_rows, [0.08, 0.72, 0.20]))
story.append(Paragraph('Tabla 11. Sprint 1: Strategy Decision Engine', styles['Caption']))
story.append(Spacer(1, 6))

story.append(P('<b>Primer milestone:</b> Ejecutar /api/strategy-decision/validate sobre una estrategia existente y obtener un SDEDecision con action, confidence, vetoResults, moduleContributions, y capitalAction.'))
story.append(Spacer(1, 10))

story.append(add_heading('10.3 Sprint 2: Kill Switches + Risk Budget [1-2 semanas]', 'H2', 1))

s2_headers = ['ID', 'Tarea', 'Prioridad']
s2_rows = [
    ['S2.1', 'Prisma: crear modelo RiskBudget + PortfolioSnapshot', 'P0'],
    ['S2.2', 'Portfolio Kill Switch: auto-pause si portfolio DD > 20%', 'P0'],
    ['S2.3', 'Strategy Kill Switch: auto-pause si estrategia DD > 30%', 'P0'],
    ['S2.4', 'Position Emergency Close: auto-close si posicion pierde > 50%', 'P0'],
    ['S2.5', 'Manual Kill Switch UI: boton global pause + per-strategy pause', 'P0'],
    ['S2.6', 'Concentration Limits: token 15%, sector 30%, chain 50%', 'P0'],
    ['S2.7', 'Proactive Alerts: "Approaching kill switch at X%"', 'P1'],
    ['S2.8', 'Evolution Tree a SDE: promocion de candidatos con umbral 15%', 'P1'],
]
story.append(make_table(s2_headers, s2_rows, [0.08, 0.72, 0.20]))
story.append(Paragraph('Tabla 12. Sprint 2: Kill Switches + Risk Budget', styles['Caption']))
story.append(Spacer(1, 10))

story.append(add_heading('10.4 Sprint 3: Portfolio Monitor + Volatility Regime [1-2 semanas]', 'H2', 1))

s3_headers = ['ID', 'Tarea', 'Prioridad']
s3_rows = [
    ['S3.1', 'Volatility Regime Detector (1 capa: rolling vol percentil)', 'P0'],
    ['S3.2', 'Conectar regime al SDE: vol > P75 a Volatility Targeting', 'P0'],
    ['S3.3', 'Dynamic allocation method selection por regimen + action', 'P0'],
    ['S3.4', 'Portfolio Dashboard: equity curve, DD, Sharpe, allocation breakdown', 'P1'],
    ['S3.5', 'Strategy status en Paper Trading UI (action tag + confidence)', 'P1'],
]
story.append(make_table(s3_headers, s3_rows, [0.08, 0.72, 0.20]))
story.append(Paragraph('Tabla 13. Sprint 3: Portfolio Monitor + Volatility Regime', styles['Caption']))
story.append(Spacer(1, 10))

story.append(P('<b>Milestone de validacion del sistema completo:</b> Al finalizar Sprint 3, el sistema debe poder: (1) evaluar una estrategia con SDE y producir un action tag, (2) asignar capital segun el action tag y el metodo de allocation seleccionado, (3) pausar automaticamente si los kill switches se activan, (4) auditar sus propias predicciones contra resultados reales, y (5) ajustar el metodo de allocation segun regimen de volatilidad.'))
story.append(Spacer(1, 6))

story.append(add_heading('10.5 Sprint 4+ (V2): Mejoras institucionales [post-validacion]', 'H2', 1))
story.append(P('Estos sprints solo se ejecutan DESPUES de que el sistema complete Sprint 3 y demuestre en paper trading que el SDE produce decisiones correctas (tasa de acierto > 55% en 50+ decisiones reconciliadas). Sin esta validacion, las mejoras institucionales son sobreingenieria prematura.'))
story.append(Spacer(1, 4))
story.append(P('Sprint 4 (V2): MC Block Bootstrap + Stress Scenarios, WF Parameter Drift, Regime Detection 3 capas, Markowitz, Adaptive position sizing.'))
story.append(P('Sprint 5 (V3): Execution Layer (Jupiter/Solana), Wallet management, Execution quality metrics, Fee accounting real.'))
story.append(Spacer(1, 8))

# ═══════════════════════════════════════════════════════════
# 11. DECISIONES DE DISENO FUNDAMENTALES
# ═══════════════════════════════════════════════════════════
story.append(Spacer(1, 24))
story.append(add_heading('11. Decisiones de Diseno Fundamentales', 'H1', 0))
story.append(Spacer(1, 6))

story.append(P('Este documento consolida y actualiza las decisiones de diseno fundamentales del proyecto. Estas son las reglas de oro que guian toda implementacion:'))
story.append(Spacer(1, 6))

story.append(P('<b>1. Vetos antes que scores.</b> Un veto duro SIEMPRE tiene prioridad sobre un score alto. Una estrategia con Sharpe 3.0 pero Risk of Ruin 15% es REJECT, no TRADE. Los vetos son binarios y no negociables.'))
story.append(Spacer(1, 4))
story.append(P('<b>2. Accion antes que informacion.</b> Cada output del sistema debe ser directamente accionable. "La estrategia tiene WFE 45%" es informacion. "RETRAIN la estrategia" es una decision. El SDE produce decisiones, no informes.'))
story.append(Spacer(1, 4))
story.append(P('<b>3. Portfolio antes que estrategia.</b> Las decisiones se toman a nivel de portfolio. Una estrategia individual puede ser excelente, pero si esta correlacionada con otras posiciones, el riesgo portfolio-level puede ser inaceptable. Los kill switches operan a nivel de portfolio primero.'))
story.append(Spacer(1, 4))
story.append(P('<b>4. Feedback loop antes que optimizacion.</b> Cerrar el feedback loop (Decision Audit Engine + kill switches) es mas importante que mejorar cualquier modulo individual. Un sistema que aprende de sus errores supera a uno que tiene mejores inputs pero no puede corregirse.'))
story.append(Spacer(1, 4))
story.append(P('<b>5. Conservador por defecto.</b> Cuando hay incertidumbre (datos insuficientes, regimen poco claro, MC percentiles amplios), el sistema debe reducir exposicion, no aumentarla. El default es Half-Kelly (no full Kelly), y Volatility Targeting solo reduce, nunca aumenta allocation.'))
story.append(Spacer(1, 4))
story.append(P('<b>6. Reproducibilidad obligatoria.</b> Toda simulacion debe ser reproducible con su seed. Esto aplica a MC, Walk-Forward, y Evolution. Math.random() esta prohibido en cualquier modulo que produzca outputs que alimenten decisiones.'))
story.append(Spacer(1, 4))
story.append(P('<b>7. Transparencia radical.</b> Cada decision del SDE viene con su razonamiento completo: que vetos se evaluaron, que scores se calcularon, que confianza tuvo cada modulo, y que peso efectivo tuvo en la decision final. El usuario puede auditar cualquier decision.'))
story.append(Spacer(1, 4))
story.append(P('<b>8. Simplicidad antes que cobertura.</b> 3 metodos de allocation bien conectados son superiores a 16 metodos desconectados. 1 capa de regimen que funcione es mejor que 3 capas que nadie calibra. La complejidad sin retorno operativo es deuda tecnica.'))
story.append(Spacer(1, 4))
story.append(P('<b>9. Validacion antes que construccion.</b> No se anade funcionalidad nueva hasta que la funcionalidad existente este validada. El sistema demuestra que produce decisiones correctas ANTES de expandir capacidades. El milestone de validacion (50+ decisiones reconciliadas con > 55% acierto) es obligatorio antes de Sprint 4.'))
story.append(Spacer(1, 4))
story.append(P('<b>10. Migracion gradual, nunca swap.</b> Cuando una estrategia hija reemplaza a la madre, la migracion de capital es gradual (25%/50%/100%) con verificacion del SDE en cada paso. Nunca se transfiere todo el capital de una vez. Esta regla aplica a todos los cambios de allocation que afecten mas del 30% del portfolio.'))

# ═══════════════════════════════════════════════════════════
# BUILD
# ═══════════════════════════════════════════════════════════

doc.multiBuild(story)
print(f"PDF generado: {OUTPUT}")
