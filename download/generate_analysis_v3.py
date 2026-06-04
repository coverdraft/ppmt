#!/usr/bin/env python3
"""
CryptoQuant Terminal — Analisis Critico Profundo v3.0
Genera PDF profesional con analisis desde 3 roles + 30 Q&A + Roadmap Priorizado
"""
import os
import sys
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch, cm, mm
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.lib import colors
from reportlab.platypus import (
    Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether,
    Image, CondPageBreak, Flowable
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily
import hashlib

# ━━ Cascade Palette ━━
PAGE_BG       = colors.HexColor('#f4f4f3')
SECTION_BG    = colors.HexColor('#eeeeec')
CARD_BG       = colors.HexColor('#eeede9')
TABLE_STRIPE  = colors.HexColor('#efeeec')
HEADER_FILL   = colors.HexColor('#534c37')
COVER_BLOCK   = colors.HexColor('#655b3e')
BORDER        = colors.HexColor('#cecabd')
ICON          = colors.HexColor('#a18f5a')
ACCENT        = colors.HexColor('#5a31d5')
ACCENT_2      = colors.HexColor('#4fbd86')
TEXT_PRIMARY   = colors.HexColor('#272623')
TEXT_MUTED     = colors.HexColor('#7d7b73')
SEM_SUCCESS   = colors.HexColor('#3a784f')
SEM_WARNING   = colors.HexColor('#a18244')
SEM_ERROR     = colors.HexColor('#904740')
SEM_INFO      = colors.HexColor('#48719b')

# ━━ Font Registration ━━
pdfmetrics.registerFont(TTFont('LiberationSerif', '/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf'))
pdfmetrics.registerFont(TTFont('LiberationSerifBold', '/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf'))
pdfmetrics.registerFont(TTFont('LiberationSans', '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf'))
pdfmetrics.registerFont(TTFont('WenQuanYi', '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'))
pdfmetrics.registerFont(TTFont('SarasaMono', '/usr/share/fonts/truetype/chinese/SarasaMonoSC-Regular.ttf'))
pdfmetrics.registerFont(TTFont('DejaVuSans', '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'))
registerFontFamily('LiberationSerif', normal='LiberationSerif', bold='LiberationSerifBold')
registerFontFamily('DejaVuSans', normal='DejaVuSans', bold='DejaVuSans')

# ━━ Page Setup ━━
PAGE_W, PAGE_H = A4
LEFT_MARGIN = 1.0 * inch
RIGHT_MARGIN = 1.0 * inch
TOP_MARGIN = 0.8 * inch
BOTTOM_MARGIN = 0.8 * inch
AVAILABLE_W = PAGE_W - LEFT_MARGIN - RIGHT_MARGIN

# ━━ Styles ━━
styles = getSampleStyleSheet()

cover_title_style = ParagraphStyle(
    'CoverTitle', fontName='LiberationSerif', fontSize=32, leading=40,
    textColor=colors.white, alignment=TA_LEFT, spaceAfter=12
)
cover_subtitle_style = ParagraphStyle(
    'CoverSubtitle', fontName='LiberationSerif', fontSize=16, leading=22,
    textColor=colors.HexColor('#cccccc'), alignment=TA_LEFT, spaceAfter=6
)
cover_meta_style = ParagraphStyle(
    'CoverMeta', fontName='LiberationSerif', fontSize=12, leading=18,
    textColor=colors.HexColor('#aaaaaa'), alignment=TA_LEFT
)

h1_style = ParagraphStyle(
    'H1Custom', fontName='LiberationSerif', fontSize=22, leading=28,
    textColor=ACCENT, spaceBefore=18, spaceAfter=12, alignment=TA_LEFT
)
h2_style = ParagraphStyle(
    'H2Custom', fontName='LiberationSerif', fontSize=16, leading=22,
    textColor=HEADER_FILL, spaceBefore=14, spaceAfter=8, alignment=TA_LEFT
)
h3_style = ParagraphStyle(
    'H3Custom', fontName='LiberationSerif', fontSize=13, leading=18,
    textColor=COVER_BLOCK, spaceBefore=10, spaceAfter=6, alignment=TA_LEFT
)
body_style = ParagraphStyle(
    'BodyCustom', fontName='LiberationSerif', fontSize=10.5, leading=16,
    textColor=TEXT_PRIMARY, alignment=TA_JUSTIFY, spaceAfter=6,
    firstLineIndent=0
)
body_indent_style = ParagraphStyle(
    'BodyIndent', fontName='LiberationSerif', fontSize=10.5, leading=16,
    textColor=TEXT_PRIMARY, alignment=TA_JUSTIFY, spaceAfter=6,
    leftIndent=18
)
bullet_style = ParagraphStyle(
    'BulletCustom', fontName='LiberationSerif', fontSize=10.5, leading=16,
    textColor=TEXT_PRIMARY, alignment=TA_LEFT, spaceAfter=4,
    leftIndent=24, bulletIndent=12
)
code_style = ParagraphStyle(
    'CodeCustom', fontName='DejaVuSans', fontSize=8.5, leading=13,
    textColor=colors.HexColor('#333333'), alignment=TA_LEFT,
    spaceAfter=6, spaceBefore=6, leftIndent=12,
    backColor=colors.HexColor('#f0f0ed'), borderPadding=6
)
callout_style = ParagraphStyle(
    'CalloutCustom', fontName='LiberationSerif', fontSize=11, leading=17,
    textColor=ACCENT, alignment=TA_LEFT, spaceAfter=8,
    leftIndent=18, borderWidth=0, borderPadding=0
)
caption_style = ParagraphStyle(
    'CaptionCustom', fontName='LiberationSerif', fontSize=9, leading=13,
    textColor=TEXT_MUTED, alignment=TA_CENTER, spaceBefore=3, spaceAfter=6
)
toc_h1 = ParagraphStyle('TOCH1', fontName='LiberationSerif', fontSize=13, leftIndent=20, leading=20, spaceBefore=4, spaceAfter=2)
toc_h2 = ParagraphStyle('TOCH2', fontName='LiberationSerif', fontSize=11, leftIndent=40, leading=18, spaceBefore=2, spaceAfter=1)
toc_h3 = ParagraphStyle('TOCH3', fontName='LiberationSerif', fontSize=10, leftIndent=60, leading=16, spaceBefore=1, spaceAfter=1)

header_cell_style = ParagraphStyle(
    'HeaderCell', fontName='LiberationSerif', fontSize=10, leading=14,
    textColor=colors.white, alignment=TA_CENTER
)
cell_style = ParagraphStyle(
    'CellCustom', fontName='LiberationSerif', fontSize=9.5, leading=14,
    textColor=TEXT_PRIMARY, alignment=TA_LEFT, wordWrap='CJK'
)
cell_center_style = ParagraphStyle(
    'CellCenter', fontName='LiberationSerif', fontSize=9.5, leading=14,
    textColor=TEXT_PRIMARY, alignment=TA_CENTER
)

# ━━ Helper Functions ━━
def add_heading(text, style, level=0):
    key = 'h_%s' % hashlib.md5(text.encode()).hexdigest()[:8]
    p = Paragraph('<a name="%s"/>%s' % (key, text), style)
    p.bookmark_name = text
    p.bookmark_level = level
    p.bookmark_text = text
    p.bookmark_key = key
    return p

def make_table(data, col_widths=None, has_header=True):
    if col_widths is None:
        col_widths = [AVAILABLE_W / len(data[0])] * len(data[0])
    t = Table(data, colWidths=col_widths, hAlign='CENTER')
    style_cmds = [
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
    ]
    if has_header:
        style_cmds.extend([
            ('BACKGROUND', (0, 0), (-1, 0), HEADER_FILL),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ])
    for i in range(1, len(data)):
        bg = colors.white if i % 2 == 1 else TABLE_STRIPE
        style_cmds.append(('BACKGROUND', (0, i), (-1, i), bg))
    t.setStyle(TableStyle(style_cmds))
    return t

def make_callout(text, accent_color=ACCENT):
    """Create a callout box with left border accent"""
    data = [[Paragraph(text, callout_style)]]
    t = Table(data, colWidths=[AVAILABLE_W - 10], hAlign='CENTER')
    t.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 14),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LINEBEFOREDECOR', (0, 0), (0, -1), 3, accent_color),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8f7f4')),
    ]))
    return t

def verdict_badge(verdict):
    color_map = {
        'ACTIVE': SEM_SUCCESS, 'PAUSED': SEM_WARNING, 'REJECT': SEM_ERROR,
        'RETRAIN': SEM_INFO, 'REDUCE': SEM_WARNING, 'INCREASE': SEM_SUCCESS
    }
    c = color_map.get(verdict, TEXT_MUTED)
    return '<font color="#%s">%s</font>' % (c.hexval()[2:], verdict)

# ━━ TocDocTemplate ━━
from reportlab.platypus import SimpleDocTemplate

class TocDocTemplate(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        if hasattr(flowable, 'bookmark_name'):
            level = getattr(flowable, 'bookmark_level', 0)
            text = getattr(flowable, 'bookmark_text', '')
            key = getattr(flowable, 'bookmark_key', '')
            self.notify('TOCEntry', (level, text, self.page, key))

# ━━ Build Document ━━
OUTPUT_DIR = '/home/z/my-project/download'
BODY_PDF = os.path.join(OUTPUT_DIR, 'cqt_analysis_body.pdf')
FINAL_PDF = os.path.join(OUTPUT_DIR, 'CryptoQuant_Terminal_Analisis_Critico_v3.pdf')

doc = TocDocTemplate(
    BODY_PDF, pagesize=A4,
    leftMargin=LEFT_MARGIN, rightMargin=RIGHT_MARGIN,
    topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN
)

story = []

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TOC
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
story.append(Paragraph('<b>Table of Contents</b>', ParagraphStyle(
    'TOCTitle', fontName='LiberationSerif', fontSize=20, leading=28,
    textColor=ACCENT, alignment=TA_LEFT, spaceAfter=18
)))
toc = TableOfContents()
toc.levelStyles = [toc_h1, toc_h2, toc_h3]
story.append(toc)
story.append(PageBreak())

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PART I: EXECUTIVE SUMMARY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
story.append(add_heading('<b>Part I: Executive Summary</b>', h1_style, level=0))

story.append(Paragraph(
    'CryptoQuant Terminal is an ambitious quantitative crypto trading platform built on Next.js 16 + Prisma 7 + SQLite. '
    'It contains 17+ functional modules, 45+ API endpoints, and 16 capital allocation methods. However, the system has a '
    'fundamental architectural flaw: it produces information but not decisions. A user sitting in front of the dashboard sees '
    'signals, scores, Monte Carlo metrics, Walk-Forward Efficiency, operability grades, lifecycle phases, and system '
    'recommendations. But the question that truly matters — "Should I allocate capital to this strategy NOW, and how much?" — '
    'has no direct answer. The user must mentally synthesize the output of 5-6 modules to reach a conclusion.',
    body_style
))
story.append(Spacer(1, 6))

story.append(Paragraph(
    'This is analogous to having a team of analysts where each presents their report independently but nobody makes the final '
    'decision. The modules operate as islands with weak interconnection, and the most critical piece — a Strategy Decision Engine '
    'that synthesizes all module outputs into actionable portfolio-level decisions — does not exist yet. This document provides a '
    'comprehensive critical analysis from three professional perspectives (Quant Developer, Portfolio Manager, Risk Manager), '
    'answers 30 key questions about architecture and direction, designs the Strategy Decision Engine, and presents a prioritized '
    'roadmap toward semi-autonomous and autonomous operation.',
    body_style
))
story.append(Spacer(1, 8))

# Key metrics callout
story.append(make_callout(
    '<b>Key Finding:</b> The project has exceptional individual modules (Monte Carlo, Walk-Forward, Capital Allocation with '
    '16 methods, Evolution Engine) but zero integration between them. The Strategy Decision Engine is the missing keystone '
    'that transforms information into action. Without it, the platform is a sophisticated analytics dashboard, not a trading system.'
))
story.append(Spacer(1, 12))

# Architecture state table
story.append(Paragraph('<b>Current Architecture State</b>', h3_style))
arch_data = [
    [Paragraph('<b>Module</b>', header_cell_style),
     Paragraph('<b>Lines</b>', header_cell_style),
     Paragraph('<b>Status</b>', header_cell_style),
     Paragraph('<b>Connected?</b>', header_cell_style),
     Paragraph('<b>Critical Issue</b>', header_cell_style)],
    [Paragraph('Decision Engine', cell_style), Paragraph('583', cell_center_style),
     Paragraph('Token-level only', cell_style), Paragraph('Partial', cell_center_style),
     Paragraph('Not strategy-level', cell_style)],
    [Paragraph('Capital Allocation', cell_style), Paragraph('1,097', cell_center_style),
     Paragraph('16 methods complete', cell_style), Paragraph('No', cell_center_style),
     Paragraph('No automated consumer', cell_style)],
    [Paragraph('Monte Carlo', cell_style), Paragraph('727', cell_center_style),
     Paragraph('Production quality', cell_style), Paragraph('No', cell_center_style),
     Paragraph('No Block Bootstrap', cell_style)],
    [Paragraph('Walk-Forward', cell_style), Paragraph('655', cell_center_style),
     Paragraph('Complete WFA', cell_style), Paragraph('No', cell_center_style),
     Paragraph('No parameter optimization', cell_style)],
    [Paragraph('Evolution Engine', cell_style), Paragraph('1,063', cell_center_style),
     Paragraph('GA with mutation', cell_style), Paragraph('No', cell_center_style),
     Paragraph('Math.random() non-reproducible', cell_style)],
    [Paragraph('Paper Trading', cell_style), Paragraph('1,100', cell_center_style),
     Paragraph('Full simulation', cell_style), Paragraph('Partial', cell_center_style),
     Paragraph('Simple position sizing', cell_style)],
    [Paragraph('Brain Orchestrator', cell_style), Paragraph('1,040', cell_center_style),
     Paragraph('11-phase pipeline', cell_style), Paragraph('Partial', cell_center_style),
     Paragraph('Output not consumed by SDE', cell_style)],
    [Paragraph('Strategy State Mgr', cell_style), Paragraph('N/A', cell_center_style),
     Paragraph('6 states tracked', cell_style), Paragraph('No', cell_center_style),
     Paragraph('State does not control execution', cell_style)],
    [Paragraph('Backtest Engine', cell_style), Paragraph('1,200', cell_center_style),
     Paragraph('Direction-aware', cell_style), Paragraph('No', cell_center_style),
     Paragraph('Simple position sizing', cell_style)],
    [Paragraph('Risk Module', cell_style), Paragraph('11 files', cell_center_style),
     Paragraph('Operability + alerts', cell_style), Paragraph('Partial', cell_center_style),
     Paragraph('No VaR, no kill switches', cell_style)],
]
story.append(make_table(arch_data, [AVAILABLE_W*0.20, AVAILABLE_W*0.10, AVAILABLE_W*0.22, AVAILABLE_W*0.13, AVAILABLE_W*0.35]))
story.append(Paragraph('Table 1: Current architecture state of all major modules', caption_style))
story.append(Spacer(1, 12))

# Integration gap map
story.append(Paragraph('<b>Integration Gap Map</b>', h3_style))
story.append(Paragraph(
    'The following diagram illustrates the fundamental problem: modules produce outputs but those outputs are not consumed '
    'by any decision-making layer. Each arrow represents a data flow that should exist but currently does not. The Strategy '
    'Decision Engine (shown in red) is the missing keystone that must be built to unify the system.',
    body_style
))
gap_data = [
    [Paragraph('<b>Source Module</b>', header_cell_style),
     Paragraph('<b>Output</b>', header_cell_style),
     Paragraph('<b>Target Consumer</b>', header_cell_style),
     Paragraph('<b>Currently Connected?</b>', header_cell_style)],
    [Paragraph('Monte Carlo', cell_style), Paragraph('Risk of Ruin, P95 DD, CI', cell_style),
     Paragraph('Strategy Decision Engine', cell_style), Paragraph('NO', cell_center_style)],
    [Paragraph('Walk-Forward', cell_style), Paragraph('WFE, Robustness, Stability', cell_style),
     Paragraph('Strategy Decision Engine', cell_style), Paragraph('NO', cell_center_style)],
    [Paragraph('Backtest Engine', cell_style), Paragraph('Sharpe, PnL, Win Rate, PF', cell_style),
     Paragraph('Strategy Decision Engine', cell_style), Paragraph('NO', cell_center_style)],
    [Paragraph('Evolution Engine', cell_style), Paragraph('Improved strategies', cell_style),
     Paragraph('Capital Deployment', cell_style), Paragraph('NO', cell_center_style)],
    [Paragraph('Capital Allocation', cell_style), Paragraph('Position sizes, methods', cell_style),
     Paragraph('Paper Trading Engine', cell_style), Paragraph('NO', cell_center_style)],
    [Paragraph('Strategy State Mgr', cell_style), Paragraph('State transitions', cell_style),
     Paragraph('Execution Engine', cell_style), Paragraph('NO', cell_center_style)],
    [Paragraph('Brain Orchestrator', cell_style), Paragraph('TokenAnalysis, action', cell_style),
     Paragraph('Decision Engine (token)', cell_style), Paragraph('YES', cell_center_style)],
]
story.append(make_table(gap_data, [AVAILABLE_W*0.22, AVAILABLE_W*0.30, AVAILABLE_W*0.28, AVAILABLE_W*0.20]))
story.append(Paragraph('Table 2: Data flow connections — current state', caption_style))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PART II: QUANT DEVELOPER PERSPECTIVE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
story.append(Spacer(1, 18))
story.append(add_heading('<b>Part II: Quant Developer Perspective</b>', h1_style, level=0))

story.append(Paragraph('<b>2.1 Strengths in the Codebase</b>', h2_style))
story.append(Paragraph(
    'The codebase demonstrates solid quantitative engineering in several key areas. The Monte Carlo Simulator stands out as '
    'the highest-quality module: it uses a seeded PRNG (Linear Congruential Generator with period 2<super>32</super>) for '
    'reproducibility, implements Fisher-Yates shuffle using the seeded PRNG for unbiased permutation, computes confidence '
    'intervals at P5/P25/P50/P75/P95 for equity, drawdown, Sharpe, and win rate, and generates risk-of-ruin and probability-of-profit '
    'metrics. The memory-efficient design stores only aggregate statistics per simulation rather than full equity curves, and the '
    'human-readable report generator with box-drawing characters is a thoughtful touch for debugging.',
    body_style
))
story.append(Paragraph(
    'The Walk-Forward Engine is similarly well-constructed with both Rolling and Anchored modes, weighted average WFE calculation '
    '(weighting by trade count for statistical significance), parameter stability measurement from out-of-sample win rate consistency, '
    'and a four-tier robustness assessment (ROBUST/MARGINAL/OVERFIT/INSUFFICIENT_DATA). The Capital Allocation Engine covers an '
    'impressive 16 methods including Risk Parity with Spinu 2013 iterative algorithm, Markowitz with Gauss-Jordan matrix inversion, '
    'and Kelly Fractional (half-Kelly). These are not toy implementations — the mathematical formulations are correct and production-ready.',
    body_style
))

story.append(Paragraph('<b>2.2 Critical Problems Identified</b>', h2_style))

story.append(Paragraph('<b>Problem 1: Decision Engine is Token-Level, Not Strategy-Level</b>', h3_style))
story.append(Paragraph(
    'The existing Decision Engine at <font face="DejaVuSans" size="8">decision-engine.ts</font> decides whether to operate on an '
    'individual TOKEN based on its lifecycle phase and operability score. It produces verdicts like OPERATE/SKIP/WATCH/EXIT. This is '
    'a token-level decision — "should I trade this token?" The Strategy Decision Engine we need is a fundamentally different layer that '
    'evaluates whether a complete STRATEGY (with its backtest results, Monte Carlo risk profile, Walk-Forward validation, and live track '
    'record) deserves capital allocation. These are two distinct decisions, both necessary, but only the first currently exists. The '
    'Strategy Decision Engine must produce verdicts like ACTIVE/PAUSED/RETRAIN/REJECT/REDUCE CAPITAL/INCREASE CAPITAL with associated '
    'capital recommendations and allocation methods.',
    body_style
))

story.append(Paragraph('<b>Problem 2: Capital Allocation is Disconnected</b>', h3_style))
story.append(Paragraph(
    'The Capital Allocation Engine implements 16 methods with correct mathematical formulations, but no module in the system actually '
    'calls it in an automated pipeline. The Paper Trading Engine uses its own <font face="DejaVuSans" size="8">calculatePositionSize()</font> '
    'which simply divides available capital by remaining position slots — a naive equal-weight approach. The Backtest Engine uses '
    '<font face="DejaVuSans" size="8">allocationMethod</font> from the TradingSystem configuration but never invokes the CapitalAllocationEngine. '
    'The code exists but is orphaned — impressive academically but functionally dead code. This is the most wasteful gap in the project: '
    'thousands of lines of sophisticated allocation logic that nobody uses.',
    body_style
))

story.append(Paragraph('<b>Problem 3: Evolution Engine Uses Math.random()</b>', h3_style))
story.append(Paragraph(
    'The <font face="DejaVuSans" size="8">mutateParams()</font> method in StrategyEvolutionEngine uses <font face="DejaVuSans" size="8">Math.random()</font> '
    'for mutation, making the evolution process non-reproducible — each execution produces different results. In quantitative finance, '
    'reproducibility is mandatory. The Monte Carlo Simulator correctly uses a seeded PRNG for reproducibility, but the Evolution Engine '
    'does not, creating an architectural inconsistency. This must be fixed by replacing Math.random() with the same seeded PRNG pattern '
    'used in the Monte Carlo module, ensuring that given the same seed, the evolution produces identical results.',
    body_style
))

story.append(Paragraph('<b>Problem 4: Feedback Loop is Broken</b>', h3_style))
story.append(Paragraph(
    'The FeedbackLoopEngine exists but there is no automatic mechanism that feeds paper trading results back to parameter optimization. '
    'The system evolves strategies using historical backtests but does not learn from live operations. When a strategy starts underperforming '
    'in paper trading, there is no pipeline that detects this degradation and triggers parameter re-optimization or strategy retirement. '
    'This creates a dangerous situation: a strategy can pass its backtest with flying colors, fail in live conditions, and nobody '
    '(neither human nor system) notices or acts on the divergence. The feedback loop must close: paper trading results must feed back '
    'into the evolution engine for continuous strategy refinement.',
    body_style
))

story.append(Paragraph('<b>Problem 5: Cross-Correlation Uses Static Priors</b>', h3_style))
story.append(Paragraph(
    'The Cross-Correlation Engine calculates P(outcome | trader_behavior x pattern x phase) using Bayesian combination, but the priors '
    '(PHASE_PRIORS, PATTERN_LIKELIHOODS, BEHAVIOR_LIKELIHOODS) are hardcoded constants that never update dynamically. In production, '
    'these priors should be recalculated as observations accumulate — a form of Bayesian updating. With static priors, the cross-correlation '
    'engine cannot adapt to regime changes or evolving market microstructure. This is a partial implementation that works for initial '
    'analysis but fails for continuous operation.',
    body_style
))

story.append(Paragraph('<b>Problem 6: No Real Market Regime Detection</b>', h3_style))
story.append(Paragraph(
    'The <font face="DejaVuSans" size="8">inferRegime()</font> function in the Decision Engine is a simplified heuristic based on lifecycle '
    'phase plus volatility. It produces four states (BULL/BEAR/SIDEWAYS/HIGH_VOL) without quantitative grounding. Real regime detection '
    'requires analysis of market-wide data: trend strength (ADX, Aroon), volatility clustering (GARCH), correlation structure (PCA on '
    'returns), and liquidity conditions. Without proper regime detection, capital allocation cannot adapt dynamically — the system uses '
    'the same allocation approach regardless of whether the market is trending, mean-reverting, or experiencing a liquidity crisis.',
    body_style
))

story.append(Paragraph('<b>2.3 Architectural Design Errors</b>', h2_style))
story.append(Paragraph(
    'Beyond individual module problems, the architecture has systemic design issues that must be addressed. First, excessive weak coupling: '
    'modules are so decoupled they do not communicate. Each is an island. The Brain produces analysis, the Backtest Engine produces results, '
    'the Monte Carlo produces simulations, but nobody unifies them. Second, there is no event bus or pipeline orchestrator. Modules are '
    'called ad-hoc from individual API routes with no structured data flow guaranteeing that each module receives inputs from previous modules. '
    'Third, the DecisionLog Prisma schema is insufficient — it lacks fields for MC risk-of-ruin, WFE, overfitting score, robustness score, '
    'validation grade, and capital recommendation. The schema must be extended significantly to support the Strategy Decision Engine.',
    body_style
))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PART III: PORTFOLIO MANAGER PERSPECTIVE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
story.append(Spacer(1, 18))
story.append(add_heading('<b>Part III: Portfolio Manager Perspective</b>', h1_style, level=0))

story.append(Paragraph('<b>3.1 What Works for Portfolio Management</b>', h2_style))
story.append(Paragraph(
    'The TradingSystem structure with five layers (assetFilter, phaseConfig, entrySignal, executionConfig, exitSignal) is well-designed '
    'and provides the flexibility needed for diverse strategy configurations. The database persistence of paper trading sessions, positions, '
    'and trades is solid, enabling recovery from server restarts and historical analysis. The CompoundGrowthTracker model in the Prisma '
    'schema demonstrates that capital evolution tracking was considered during design. The StrategyStateHistory model provides a complete '
    'audit trail of state transitions with timestamps, metrics, and evolution data — essential for portfolio governance.',
    body_style
))

story.append(Paragraph('<b>3.2 Critical Problems</b>', h2_style))

story.append(Paragraph('<b>Problem 1: No Portfolio Concept Exists</b>', h3_style))
story.append(Paragraph(
    'Each strategy operates independently. There is no mental or data model of "my total strategy portfolio." If Strategy A and Strategy B '
    'are both LONG on a meme token on SOL, the concentration risk is enormous and nobody detects it. The system has no concept of portfolio-level '
    'metrics: no portfolio Sharpe, no portfolio Sortino, no portfolio max drawdown, no rolling correlation between strategies. These metrics '
    'are essential for allocation decisions. Without a portfolio model, the system cannot answer fundamental questions like "How much of my '
    'capital is exposed to SOL meme tokens?" or "What is my portfolio VaR?" or "Which strategies are highly correlated and should not both '
    'be active simultaneously?"',
    body_style
))

story.append(Paragraph('<b>Problem 2: Capital Allocation is Per-Strategy, Not Per-Portfolio</b>', h3_style))
story.append(Paragraph(
    'The <font face="DejaVuSans" size="8">calculatePositionSize()</font> in the Paper Trading Engine simply divides available capital among '
    'remaining position slots. It does not consider: correlation between positions, sector concentration, portfolio drawdown, or any of the '
    '16 allocation methods available in the CapitalAllocationEngine. Even Risk Parity, the most basic portfolio-level method, is never applied. '
    'The system treats each position as independent when in reality, positions in correlated assets act as a single large bet. For a platform '
    'that aspires to manage capital, this is the single most impactful gap to close.',
    body_style
))

story.append(Paragraph('<b>Problem 3: No Kill Switches</b>', h3_style))
story.append(Paragraph(
    'If a strategy starts losing catastrophically (common in crypto), there is no way to stop it automatically. The user must be watching '
    'the dashboard manually. For a system that aspires to semi-autonomous operation, this is unacceptable. Kill switches must exist at three '
    'levels: position-level (individual position hits emergency stop), strategy-level (strategy drawdown exceeds threshold), and portfolio-level '
    '(total portfolio drawdown exceeds threshold). The StrategyStateManager records PAUSED states but does not control execution — a PAUSED '
    'strategy can still be traded if the Paper Trading Engine does not check its state.',
    body_style
))

story.append(Paragraph('<b>Problem 4: Evolution Does Not Feed Capital Decisions</b>', h3_style))
story.append(Paragraph(
    'The Evolution Engine produces improved strategies but there is no mechanism that says "the child strategy outperformed the parent, '
    'migrate capital from parent to child." It is optimization without deployment. The system can evolve a strategy from Sharpe 1.2 to 1.8, '
    'but capital continues to flow to the old strategy unless manually redirected. This gap means the evolution engine produces intellectual '
    'value but not financial value — the improved strategy exists in the database but not in the live portfolio.',
    body_style
))

story.append(Paragraph('<b>Problem 5: Allocation Method Selection is Manual and Arbitrary</b>', h3_style))
story.append(Paragraph(
    'The system has 16 allocation methods (Markowitz, Risk Parity, Kelly, etc.) but who decides WHICH method to use and WHEN? The answer '
    'should be: the Strategy Decision Engine, based on market regime and portfolio track record. Currently, the choice is manual and arbitrary. '
    'In a bull market, Kelly Fractional is appropriate for aggressive growth. In a bear market, Max Drawdown Control is safer. With multiple '
    'uncorrelated strategies, Risk Parity optimizes diversification. The method selection must be automated and regime-aware.',
    body_style
))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PART IV: RISK MANAGER PERSPECTIVE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
story.append(Spacer(1, 18))
story.append(add_heading('<b>Part IV: Risk Manager Perspective</b>', h1_style, level=0))

story.append(Paragraph('<b>4.1 Strengths in Risk Management</b>', h2_style))
story.append(Paragraph(
    'The operability score is a genuine innovation — estimating the impact of fees plus slippage before executing a trade is something '
    'most crypto tools do not do. The bot detection engine with 13 bot types is comprehensive and provides valuable intelligence for '
    'filtering noisy market signals. The Alert Engine with cooldowns, rules, and webhook delivery is well-designed for operational monitoring. '
    'The Data Quality Gate provides a necessary protection layer against garbage-in-garbage-out scenarios. These components form a solid '
    'risk management foundation, but they are individual tools rather than an integrated risk framework.',
    body_style
))

story.append(Paragraph('<b>4.2 Critical Problems</b>', h2_style))

story.append(Paragraph('<b>Problem 1: No Risk Budget</b>', h3_style))
story.append(Paragraph(
    'How much total risk can the portfolio take? What is the daily or weekly Value at Risk? Without an explicit risk budget, any allocation '
    'is arbitrary. A risk budget defines the maximum acceptable loss at portfolio, strategy, and position levels. It should specify: '
    'maximum portfolio drawdown (e.g., 20%), maximum single-strategy drawdown (e.g., 15%), maximum position concentration (e.g., 30% '
    'in single token), maximum sector concentration (e.g., 40% in meme tokens), and maximum chain concentration (e.g., 60% on SOL). '
    'Without these constraints, the system can accidentally concentrate all capital in correlated risky positions.',
    body_style
))

story.append(Paragraph('<b>Problem 2: Risk of Ruin Threshold Too Lax</b>', h3_style))
story.append(Paragraph(
    'The current veto of Risk of Ruin greater than 5% is too lenient for real capital. In an institutional context, risk of ruin should '
    'be less than 1%. The 5% threshold is acceptable only for paper trading. The system should implement risk profiles: Conservative '
    '(Risk of Ruin less than 1%), Moderate (less than 3%), and Aggressive (less than 5%). The profile should be selectable per portfolio '
    'and enforceable by the Strategy Decision Engine as a hard veto.',
    body_style
))

story.append(Paragraph('<b>Problem 3: No Drawdown Limits</b>', h3_style))
story.append(Paragraph(
    'If a strategy loses 30%, it should be paused automatically. If the portfolio loses 20%, everything should pause. This does not exist. '
    'The CapitalStrategyManager defines drawdown thresholds (WARNING 10%, DANGER 15%, CRITICAL 25%) but these are passive labels, not '
    'active circuit breakers. The system should implement escalating responses: at WARNING, reduce position sizes by 50%; at DANGER, pause '
    'new entries; at CRITICAL, close all positions and pause the entire portfolio. These must be automatic, not manual.',
    body_style
))

story.append(Paragraph('<b>Problem 4: Monte Carlo Lacks Block Bootstrap</b>', h3_style))
story.append(Paragraph(
    'The current Fisher-Yates shuffle destroys the temporal structure of trades. In financial markets, returns exhibit autocorrelation '
    'and volatility clustering — consecutive trades are not independent. Block Bootstrap (with block size approximately equal to the square '
    'root of the number of trades) preserves this temporal structure and produces more realistic confidence intervals. Without it, the Monte '
    'Carlo results are optimistic because they assume trade independence, which underestimates the probability of consecutive losses and '
    'overestimates the Sharpe ratio confidence interval.',
    body_style
))

story.append(Paragraph('<b>Problem 5: No Stress Testing</b>', h3_style))
story.append(Paragraph(
    'What happens if Bitcoin drops 30% in a day? How do all strategies behave simultaneously? Without stress testing of extreme scenarios, '
    'the real risk is unknown. The Monte Carlo engine should support stress scenarios: inject artificial shocks (-30%, -50%, -90% portfolio '
    'value) at random points in the simulation, simulate flash crashes with correlated position failures, and model rug pulls with instant '
    '-95% moves on individual tokens. These stress tests should be mandatory for any strategy seeking ACTIVE status.',
    body_style
))

story.append(Paragraph('<b>Problem 6: No Position-Level Emergency Stop</b>', h3_style))
story.append(Paragraph(
    'The paper trading system has trailing stops and stop-loss/take-profit, but there is no emergency kill switch that closes EVERYTHING '
    'if portfolio drawdown exceeds a critical threshold. This is the nuclear option that must exist: when the portfolio has lost more than '
    'X%, close all positions immediately, pause all strategies, and alert the user. This is not about optimal exit — it is about capital '
    'preservation when the system is malfunctioning or market conditions are extreme.',
    body_style
))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PART V: 30 KEY QUESTIONS AND ANSWERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
story.append(Spacer(1, 18))
story.append(add_heading('<b>Part V: 30 Key Questions and Answers</b>', h1_style, level=0))

# Architecture and SDE
story.append(add_heading('<b>5.1 Architecture and Strategy Decision Engine</b>', h2_style))

qa_items = [
    ("Q1: Validation Hub vs Strategy Decision Engine — Which direction?",
     "Strategy Decision Engine (SDE). A Validation Hub sounds like a passive control panel — validating and displaying results. "
     "A Decision Engine sounds like a motor that PRODUCES decisions. The semantic distinction matters because it guides design. "
     "The SDE must receive inputs from all modules (MC, WF, backtest, operability, regime), apply hard vetoes first (any veto = REJECT), "
     "then calculate composite scores (robustness, overfitting, stability), and finally produce an actionable DECISION: ACTIVE/PAUSED/"
     "RETRAIN/REJECT/REDUCE CAPITAL/INCREASE CAPITAL with position size recommendation and allocation method."),

    ("Q2: Where does the SDE live in the architecture?",
     "As a SUPERIOR layer that consumes outputs from all other modules. It replaces nothing. It is an aggregator and decision maker: "
     "Backtest Engine, Monte Carlo, Walk-Forward, Operability, Regime Detector, and Evolution Tree all feed into the SDE, which then "
     "produces DECISION plus Capital Allocation. No existing module is modified — the SDE sits above them all as the orchestration layer."),

    ("Q3: What is the minimum viable output of the SDE?",
     "A StrategyDecision object containing: strategyId, verdict (ACTIVE/PAUSED/RETRAIN/REJECT/REDUCE/INCREASE), validationGrade "
     "(A through F), robustnessScore (0-100), overfittingScore (0-100), stabilityScore (0-100), vetoFailures list, capitalRecommendation "
     "(with action, targetPct, method, reason), and nextReviewDate. This interface is the contract between the SDE and all consumers."),

    ("Q4: Are the hard vetoes from v1.0 correct?",
     "Mostly yes, with adjustments: Risk of Ruin greater than 5% is too lax — use profiles (Conservative less than 1%, Moderate less "
     "than 3%, Aggressive less than 5%). WFE less than 30% is correct as veto. Total Trades less than 30 is correct but consider less "
     "than 50 for more statistical significance. Max Drawdown greater than 50% is correct. Win Rate less than 35% should only be a veto "
     "if avgLoss/avgWin is greater than 2.5 — a system with 30% win rate and 4:1 payoff ratio is profitable."),

    ("Q5: Can the existing Decision Engine be reused?",
     "Yes, but as a component for TOKEN-level decisions. Rename it to token-decision-engine.ts. The new strategy-decision-engine.ts is "
     "a different layer operating at the strategy/portfolio level. Both are needed — the token-level engine decides which tokens to trade, "
     "the strategy-level engine decides which strategies deserve capital. They operate at different abstraction levels and should not be "
     "merged."),

    ("Q6: How should the SDE handle conflicting signals?",
     "Vetoes are absolute — any single veto overrides all positive signals. Beyond vetoes, use a weighted scoring system where each module "
     "contributes to a composite score: Monte Carlo (30% weight on risk-of-ruin and P95 DD), Walk-Forward (25% weight on WFE and stability), "
     "Backtest (25% weight on Sharpe and profit factor), Operability (10% weight), Regime (10% weight). The composite score maps to "
     "validation grades: A (80-100), B (65-79), C (50-64), D (35-49), F (0-34). Grade D or below = REJECT."),
]

for q, a in qa_items:
    story.append(Paragraph('<b>%s</b>' % q, h3_style))
    story.append(Paragraph(a, body_style))
    story.append(Spacer(1, 4))

# Capital Allocation
story.append(add_heading('<b>5.2 Capital Allocation</b>', h2_style))

qa_capital = [
    ("Q7: Which of the 16 methods should be the default?",
     "Kelly Fractional (half-Kelly) as default for position sizing, and Risk Parity for portfolio allocation. Kelly is the only method "
     "that maximizes long-term growth with controlled risk. Risk Parity is superior to equal-weight because it adjusts for volatility. "
     "Markowitz is too sensitive to estimation error in crypto markets where return distributions are fat-tailed and non-stationary."),

    ("Q8: How should the allocation method be chosen dynamically?",
     "The SDE decides based on market regime: Bull market uses Kelly plus trend following for aggressive positioning. Bear market uses "
     "Max Drawdown Control for defensive positioning. High volatility uses Volatility Targeting to reduce exposure. Multiple uncorrelated "
     "strategies use Risk Parity for optimal diversification. Single strategy with high confidence uses Kelly Modified for controlled "
     "concentration. The method selection must be automated and regime-aware, not manual."),

    ("Q9: What about methods that are never used?",
     "Deprecate or remove: FIXED_AMOUNT (does not scale), FIXED_RATIO (too conservative), RL_ALLOCATION (Q-table is a toy without a "
     "real simulation environment), META_ALLOCATION (needs more data than available). This leaves 12 useful methods. The removed methods "
     "should be archived but not deleted — they may become relevant as the system matures."),

    ("Q10: How to connect Capital Allocation to real paper trading?",
     "Replace calculatePositionSize() in PaperTradingEngine with a call to CapitalAllocationEngine. The SDE determines the method, and "
     "CapitalAllocationEngine calculates the size. The code already exists — only the connection is missing. This is the highest-ROI "
     "integration task in the entire project: approximately 50 lines of integration code unlock 2,000+ lines of allocation logic."),

    ("Q11: Should we implement portfolio-level allocation?",
     "Yes, and it is essential. Currently each strategy gets its own capital silo. Portfolio-level allocation considers: cross-strategy "
     "correlation (reduce allocation to correlated strategies), sector/chain concentration limits, portfolio drawdown as a constraint, "
     "and dynamic rebalancing as strategies change performance. This requires a Portfolio model in the database and a portfolio-level "
     "allocation service that sits above the individual strategy allocation."),
]

for q, a in qa_capital:
    story.append(Paragraph('<b>%s</b>' % q, h3_style))
    story.append(Paragraph(a, body_style))
    story.append(Spacer(1, 4))

# Evolution Tree
story.append(add_heading('<b>5.3 Evolution Tree</b>', h2_style))

qa_evo = [
    ("Q12: Is the Evolution Engine well-designed?",
     "The general structure is sound: genetic algorithm with adaptive mutation rate, early stopping, quality filtering, and composite "
     "scoring (Sharpe 30%, PnL 25%, WinRate 20%, PF 10%, DD -10%). However, Math.random() makes it non-reproducible, there is no "
     "crossover (only mutation, which limits parameter space exploration), and no complexity penalty (a strategy with 7 mutated parameters "
     "should be penalized versus one with 2). Fix Math.random() first, then add crossover, then add complexity penalty."),

    ("Q13: How to connect evolution with capital deployment?",
     "When a child strategy outperforms its parent by a margin (e.g., Sharpe improvement greater than 0.3), the SDE should automatically "
     "evaluate the child through full validation (MC + WF + operability). If the child passes, capital migrates from parent to child with "
     "a gradual transition (e.g., 25% per day over 4 days) to avoid abrupt position changes. The parent is marked as SUPERSEDED in the "
     "evolution tree."),

    ("Q14: Should evolution be continuous or scheduled?",
     "Both. Continuous micro-evolution (small parameter tweaks) runs daily during low-activity periods. Macro-evolution (structural changes, "
     "new strategy variants) runs weekly. The evolution loop should be interruptible — if a strategy is actively trading, do not mutate its "
     "parameters mid-session. Wait for the next scheduled review window."),
]

for q, a in qa_evo:
    story.append(Paragraph('<b>%s</b>' % q, h3_style))
    story.append(Paragraph(a, body_style))
    story.append(Spacer(1, 4))

# Monte Carlo
story.append(add_heading('<b>5.4 Monte Carlo Institutional Upgrades</b>', h2_style))

qa_mc = [
    ("Q15: What specific MC upgrades are needed?",
     "Three critical upgrades: (1) Block Bootstrap with block size = sqrt(n) to preserve temporal autocorrelation. (2) Stress Scenario "
     "injection: -30%, -50%, -90% portfolio shocks at random simulation points, plus flash crash (correlated -80% across all positions) "
     "and rug pull (-95% on single token). (3) Consecutive Loss Distribution: calculate P(N consecutive losses) for N = 3, 5, 7, 10, "
     "which is critical for drawdown estimation and psychological risk assessment."),

    ("Q16: Is the current PRNG sufficient?",
     "The LCG with period 2<super>32</super> is adequate for current needs (10,000 simulations x 1,000 trades). For institutional-grade "
     "simulations with 100,000+ paths, consider upgrading to Mersenne Twister (period 2<super>19937</super>). This is a low-priority "
     "upgrade — the current PRNG is sufficient for the foreseeable future."),

    ("Q17: How should MC results feed the SDE?",
     "The MC engine should output a structured MCRiskProfile containing: riskOfRuin, p95MaxDrawdown, probabilityOfProfit, "
     "sharpeConfidenceInterval (P5-P95), expectedShortfall (CVaR at 95%), and stressTestResults. The SDE uses riskOfRuin as a hard "
     "veto and p95MaxDrawdown + probabilityOfProfit as scoring inputs. Stress test failures add penalty points to the composite score."),
]

for q, a in qa_mc:
    story.append(Paragraph('<b>%s</b>' % q, h3_style))
    story.append(Paragraph(a, body_style))
    story.append(Spacer(1, 4))

# Walk-Forward
story.append(add_heading('<b>5.5 Walk-Forward Professional Upgrades</b>', h2_style))

qa_wf = [
    ("Q18: What WF upgrades are needed?",
     "Three upgrades: (1) Parameter optimization within windows — currently the same system template is used for both train and test. "
     "Real WFA requires optimizing parameters in the training window and testing those optimized parameters out-of-sample. (2) Parameter "
     "Drift Analysis — track how optimal parameters change between windows. High drift = low stability = likely overfitting. (3) "
     "Overfitting Probability — calculate P(overfit) from the WFE distribution using the methodology from Bailey, Borwein, Lopez de Prado "
     "(2014) 'Pseudo-Mathematics and Financial Charlatanism'."),

    ("Q19: Is anchored or rolling WFA better for crypto?",
     "Rolling WFA is better for crypto because: market microstructure changes rapidly (new tokens, new DEXs, new MEV strategies), regime "
     "shifts are frequent (bull/bear cycles every 6-18 months), and anchored WFA gives too much weight to ancient history. Rolling WFA "
     "with a 60/40 train/test split and 6-month training window provides the best balance between statistical significance and relevance."),

    ("Q20: How should WF results feed the SDE?",
     "The WF engine should output a WFValidationProfile containing: aggregateWFE, parameterStability, robustnessAssessment, "
     "overfittingProbability, and windowResults array. The SDE uses: WFE less than 30% as hard veto, robustnessAssessment OVERFIT as "
     "hard veto, parameterStability as scoring input, and overfittingProbability as scoring modifier."),
]

for q, a in qa_wf:
    story.append(Paragraph('<b>%s</b>' % q, h3_style))
    story.append(Paragraph(a, body_style))
    story.append(Spacer(1, 4))

# Remaining questions
story.append(add_heading('<b>5.6 Regime Detection, Kill Switches, and Autonomous Operation</b>', h2_style))

qa_remaining = [
    ("Q21: How to implement real regime detection?",
     "Use a multi-factor regime classifier with three components: (1) Trend strength via ADX and Aroon on BTC/ETH daily charts. "
     "(2) Volatility regime via realized volatility percentile rank (compare current 30-day vol to 1-year distribution). "
     "(3) Correlation structure via PCA on top-20 crypto returns — high first-component loading = risk-on correlated market. "
     "Combine the three factors into a 4-state regime: TRENDING, MEAN_REVERTING, HIGH_VOL, CRISIS. Update regime classification daily."),

    ("Q22: Kill switch architecture?",
     "Three levels with escalating severity: Position-level (individual position loss exceeds X% or token drops Y% in 1 hour), "
     "Strategy-level (strategy drawdown exceeds threshold or Sharpe drops below 0 for 30 days), Portfolio-level (total portfolio "
     "drawdown exceeds threshold or daily VaR exceeded by 2x). Each level triggers an automatic response: reduce, pause, or emergency "
     "close. All kill switch activations are logged and require manual acknowledgment before re-enabling."),

    ("Q23: Risk budget implementation?",
     "Define a RiskBudget model with: maxPortfolioDD (20%), maxStrategyDD (15%), maxPositionConcentration (30%), maxSectorConcentration "
     "(40%), maxChainConcentration (60%), maxCorrelatedExposure (50%). The RiskBudget is enforced by the SDE before any capital allocation. "
     "Violations are hard blocks, not warnings. The budget should be configurable per risk profile (Conservative/Moderate/Aggressive)."),

    ("Q24: How to achieve semi-autonomous operation?",
     "Semi-autonomous means the system can operate without constant human supervision but requires human approval for significant decisions. "
     "Implementation: the SDE produces decisions automatically, capital allocation is automated, kill switches are automated, but new strategy "
     "activation, significant allocation changes (greater than 20%), and kill switch resets require human approval via the dashboard. The "
     "system runs autonomously within defined guardrails and escalates decisions that fall outside them."),

    ("Q25: Path to fully autonomous operation?",
     "Fully autonomous requires: (1) Strategy Decision Engine with proven track record (6+ months of semi-autonomous operation with positive "
     "results). (2) Regime detection with validated accuracy (backtested over multiple cycles). (3) Kill switches that have been tested in "
     "production (triggered correctly during real drawdowns). (4) Capital allocation that has been validated through paper trading with real "
     "market data. (5) Human oversight reduced to weekly reviews. This is a 12-18 month journey from semi-autonomous to fully autonomous."),

    ("Q26: Should we use LLM/AI for decision-making?",
     "LLM should be used as an advisory layer, not a decision maker. The SDE must be deterministic and rule-based — every decision must be "
     "auditable and reproducible. LLM can provide: narrative explanations of decisions, regime analysis summaries, and anomaly detection "
     "flagging. But the actual verdict (ACTIVE/PAUSED/REJECT) must come from deterministic scoring. LLM-based decisions are untestable, "
     "unreproducible, and legally problematic for fiduciary responsibility."),

    ("Q27: How to handle strategy correlation in the portfolio?",
     "Implement rolling correlation calculation between strategy equity curves (30-day rolling window). Strategies with correlation greater "
     "than 0.7 should not both be at maximum allocation. The SDE should reduce capital to correlated strategies proportionally. Use "
     "hierarchical clustering to identify strategy groups and enforce concentration limits per cluster."),

    ("Q28: What database schema changes are needed?",
     "Add models: Portfolio (id, name, riskProfile, totalCapital, createdAt), PortfolioAllocation (id, portfolioId, strategyId, targetPct, "
     "currentPct, lastRebalanced), RiskBudget (id, portfolioId, maxPortfolioDD, maxStrategyDD, maxConcentration, maxCorrelated), "
     "KillSwitch (id, level, threshold, action, triggeredAt, acknowledgedAt, acknowledgedBy), StrategyValidation (id, strategyId, "
     "validationGrade, robustnessScore, overfittingScore, stabilityScore, mcRiskProfile, wfValidationProfile, capitalRecommendation, "
     "validUntil, reviewedBy). Extend DecisionLog with: mcRiskOfRuin, wfEfficiency, overfittingScore, robustnessScore, validationGrade, "
     "capitalRecommendation."),

    ("Q29: What is the testing strategy for the SDE?",
     "Three layers: (1) Unit tests for each scoring component with fixed inputs and expected outputs. (2) Integration tests with synthetic "
     "strategy data that exercises all veto and scoring paths. (3) Backtest-of-backtests: run the SDE on historical strategy performance "
     "data and measure whether its decisions would have improved portfolio returns. The SDE must pass all three layers before going live."),

    ("Q30: What is the single most important next step?",
     "Build the Strategy Decision Engine. Everything else is secondary. The SDE is the keystone that transforms this project from an "
     "analytics dashboard into a trading system. Without it, the 16 allocation methods are unused, Monte Carlo results are informational "
     "but not actionable, Walk-Forward validation is academic but not operational, and the evolution engine produces improvements that are "
     "never deployed. The SDE unifies all modules into a decision pipeline and makes the system capable of producing its core output: "
     "capital allocation decisions."),
]

for q, a in qa_remaining:
    story.append(Paragraph('<b>%s</b>' % q, h3_style))
    story.append(Paragraph(a, body_style))
    story.append(Spacer(1, 4))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PART VI: STRATEGY DECISION ENGINE DESIGN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
story.append(Spacer(1, 18))
story.append(add_heading('<b>Part VI: Strategy Decision Engine Design</b>', h1_style, level=0))

story.append(Paragraph('<b>6.1 Architecture Overview</b>', h2_style))
story.append(Paragraph(
    'The Strategy Decision Engine (SDE) operates as the top-level orchestration layer in the system architecture. It receives structured '
    'inputs from all validation modules, applies a deterministic decision pipeline (vetoes, scoring, verdict, capital recommendation), '
    'and produces actionable decisions that downstream systems (Paper Trading, Capital Allocation, Evolution) consume. The SDE is stateless '
    'with respect to decision logic — given the same inputs, it always produces the same output. State (track record, learning) is persisted '
    'to the database and loaded on each evaluation cycle.',
    body_style
))

story.append(Paragraph('<b>6.2 Decision Pipeline</b>', h2_style))
story.append(Paragraph(
    'The decision pipeline has four sequential stages. Stage 1: Hard Veto Check — any single veto failure results in immediate REJECT '
    'without further evaluation. Stage 2: Composite Scoring — weighted score from all modules. Stage 3: Verdict Determination — score '
    'maps to verdict based on thresholds and historical context. Stage 4: Capital Recommendation — verdict plus regime plus portfolio '
    'state determines allocation action, target percentage, and allocation method.',
    body_style
))

# Veto table
story.append(Paragraph('<b>Hard Veto Rules</b>', h3_style))
veto_data = [
    [Paragraph('<b>Veto</b>', header_cell_style),
     Paragraph('<b>Condition</b>', header_cell_style),
     Paragraph('<b>Profile: Conservative</b>', header_cell_style),
     Paragraph('<b>Profile: Moderate</b>', header_cell_style),
     Paragraph('<b>Profile: Aggressive</b>', header_cell_style)],
    [Paragraph('Risk of Ruin', cell_style), Paragraph('MC simulation', cell_style),
     Paragraph('greater than 1%', cell_center_style), Paragraph('greater than 3%', cell_center_style), Paragraph('greater than 5%', cell_center_style)],
    [Paragraph('Walk-Forward Efficiency', cell_style), Paragraph('WFA result', cell_style),
     Paragraph('less than 40%', cell_center_style), Paragraph('less than 30%', cell_center_style), Paragraph('less than 20%', cell_center_style)],
    [Paragraph('Total Trades', cell_style), Paragraph('Backtest count', cell_style),
     Paragraph('less than 50', cell_center_style), Paragraph('less than 30', cell_center_style), Paragraph('less than 20', cell_center_style)],
    [Paragraph('Max Drawdown', cell_style), Paragraph('Backtest metric', cell_style),
     Paragraph('greater than 30%', cell_center_style), Paragraph('greater than 40%', cell_center_style), Paragraph('greater than 50%', cell_center_style)],
    [Paragraph('Robustness', cell_style), Paragraph('WF assessment', cell_style),
     Paragraph('OVERFIT', cell_center_style), Paragraph('OVERFIT', cell_center_style), Paragraph('OVERFIT', cell_center_style)],
    [Paragraph('Data Quality', cell_style), Paragraph('Quality gate', cell_style),
     Paragraph('UNOPERABLE', cell_center_style), Paragraph('UNOPERABLE', cell_center_style), Paragraph('RISKY or worse', cell_center_style)],
]
story.append(make_table(veto_data, [AVAILABLE_W*0.20, AVAILABLE_W*0.18, AVAILABLE_W*0.21, AVAILABLE_W*0.21, AVAILABLE_W*0.20]))
story.append(Paragraph('Table 3: Hard veto rules by risk profile', caption_style))
story.append(Spacer(1, 8))

# Scoring table
story.append(Paragraph('<b>Composite Scoring Weights</b>', h3_style))
score_data = [
    [Paragraph('<b>Module</b>', header_cell_style),
     Paragraph('<b>Weight</b>', header_cell_style),
     Paragraph('<b>Key Metrics</b>', header_cell_style),
     Paragraph('<b>Scoring Logic</b>', header_cell_style)],
    [Paragraph('Monte Carlo', cell_style), Paragraph('30%', cell_center_style),
     Paragraph('Risk of Ruin, P95 DD, Prob. of Profit', cell_style),
     Paragraph('Risk of Ruin mapped to 0-100, P95 DD penalty, PoP bonus', cell_style)],
    [Paragraph('Walk-Forward', cell_style), Paragraph('25%', cell_center_style),
     Paragraph('WFE, Parameter Stability, Robustness', cell_style),
     Paragraph('WFE scaled to 0-100, stability modifier, robustness bonus/penalty', cell_style)],
    [Paragraph('Backtest', cell_style), Paragraph('25%', cell_center_style),
     Paragraph('Sharpe, Profit Factor, Win Rate, Max DD', cell_style),
     Paragraph('Sharpe scaled (0 = 0, 3.0 = 100), PF bonus, DD penalty', cell_style)],
    [Paragraph('Operability', cell_style), Paragraph('10%', cell_center_style),
     Paragraph('Operability Score, Fee Impact', cell_style),
     Paragraph('Score directly used, fee impact penalty', cell_style)],
    [Paragraph('Regime Fit', cell_style), Paragraph('10%', cell_center_style),
     Paragraph('Strategy-Regime alignment', cell_style),
     Paragraph('Trend strategy in trending market = bonus, etc.', cell_style)],
]
story.append(make_table(score_data, [AVAILABLE_W*0.15, AVAILABLE_W*0.10, AVAILABLE_W*0.35, AVAILABLE_W*0.40]))
story.append(Paragraph('Table 4: Composite scoring weights and logic', caption_style))
story.append(Spacer(1, 8))

# Verdict mapping
story.append(Paragraph('<b>Verdict Mapping</b>', h3_style))
verdict_data = [
    [Paragraph('<b>Score Range</b>', header_cell_style),
     Paragraph('<b>Grade</b>', header_cell_style),
     Paragraph('<b>Verdict</b>', header_cell_style),
     Paragraph('<b>Capital Action</b>', header_cell_style)],
    [Paragraph('80-100', cell_center_style), Paragraph('A', cell_center_style),
     Paragraph('ACTIVE', cell_style), Paragraph('INCREASE CAPITAL, full allocation', cell_style)],
    [Paragraph('65-79', cell_center_style), Paragraph('B', cell_center_style),
     Paragraph('ACTIVE', cell_style), Paragraph('ALLOCATE, standard allocation', cell_style)],
    [Paragraph('50-64', cell_center_style), Paragraph('C', cell_center_style),
     Paragraph('ACTIVE (reduced)', cell_style), Paragraph('REDUCE CAPITAL, 50% allocation', cell_style)],
    [Paragraph('35-49', cell_center_style), Paragraph('D', cell_center_style),
     Paragraph('RETRAIN', cell_style), Paragraph('HOLD, no new allocation, evolve parameters', cell_style)],
    [Paragraph('0-34', cell_center_style), Paragraph('F', cell_center_style),
     Paragraph('REJECT', cell_style), Paragraph('EXIT, close positions, archive strategy', cell_style)],
]
story.append(make_table(verdict_data, [AVAILABLE_W*0.15, AVAILABLE_W*0.10, AVAILABLE_W*0.25, AVAILABLE_W*0.50]))
story.append(Paragraph('Table 5: Score-to-verdict mapping', caption_style))
story.append(Spacer(1, 8))

# SDE interface
story.append(Paragraph('<b>6.3 Core Interface</b>', h2_style))
story.append(Paragraph(
    'The StrategyDecision interface defines the contract between the SDE and all consumers. This interface must be stable — downstream '
    'systems depend on it. The StrategyValidation database model stores the full decision context for auditability and learning. The SDE '
    'is designed for testability: every decision can be reproduced by replaying the same inputs through the pipeline.',
    body_style
))

sde_fields = [
    [Paragraph('<b>Field</b>', header_cell_style),
     Paragraph('<b>Type</b>', header_cell_style),
     Paragraph('<b>Description</b>', header_cell_style)],
    [Paragraph('strategyId', cell_style), Paragraph('string', cell_style), Paragraph('The strategy being evaluated', cell_style)],
    [Paragraph('verdict', cell_style), Paragraph('enum', cell_style), Paragraph('ACTIVE / PAUSED / RETRAIN / REJECT / REDUCE / INCREASE', cell_style)],
    [Paragraph('validationGrade', cell_style), Paragraph('A-F', cell_style), Paragraph('Composite validation grade', cell_style)],
    [Paragraph('robustnessScore', cell_style), Paragraph('0-100', cell_style), Paragraph('Weighted robustness from MC + WF', cell_style)],
    [Paragraph('overfittingScore', cell_style), Paragraph('0-100', cell_style), Paragraph('Estimated overfitting probability', cell_style)],
    [Paragraph('stabilityScore', cell_style), Paragraph('0-100', cell_style), Paragraph('Parameter stability across windows', cell_style)],
    [Paragraph('vetoFailures', cell_style), Paragraph('string[]', cell_style), Paragraph('List of failed veto checks', cell_style)],
    [Paragraph('capitalAction', cell_style), Paragraph('enum', cell_style), Paragraph('ALLOCATE / HOLD / REDUCE / EXIT', cell_style)],
    [Paragraph('targetPct', cell_style), Paragraph('number', cell_style), Paragraph('Target portfolio percentage', cell_style)],
    [Paragraph('allocationMethod', cell_style), Paragraph('enum', cell_style), Paragraph('Recommended allocation method', cell_style)],
    [Paragraph('nextReviewDate', cell_style), Paragraph('Date', cell_style), Paragraph('Scheduled re-evaluation date', cell_style)],
]
story.append(make_table(sde_fields, [AVAILABLE_W*0.22, AVAILABLE_W*0.13, AVAILABLE_W*0.65]))
story.append(Paragraph('Table 6: StrategyDecision interface fields', caption_style))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PART VII: PRIORITIZED ROADMAP
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
story.append(Spacer(1, 18))
story.append(add_heading('<b>Part VII: Prioritized Roadmap</b>', h1_style, level=0))

story.append(Paragraph(
    'The roadmap is organized into six sprints, each with a clear deliverable that builds on the previous sprint. The principle is: '
    'every sprint must produce a system that is more capable than before. No sprint should leave the system in a broken or regressed state. '
    'The highest-priority items are those that close integration gaps, because a connected system is worth more than the sum of its disconnected parts.',
    body_style
))

# Sprint 0
story.append(Paragraph('<b>Sprint 0: Foundation Fixes (Week 1-2)</b>', h2_style))
sprint0_data = [
    [Paragraph('<b>Task</b>', header_cell_style),
     Paragraph('<b>Priority</b>', header_cell_style),
     Paragraph('<b>Effort</b>', header_cell_style),
     Paragraph('<b>Impact</b>', header_cell_style),
     Paragraph('<b>Description</b>', header_cell_style)],
    [Paragraph('Fix Math.random() in Evolution', cell_style), Paragraph('P0', cell_center_style),
     Paragraph('2h', cell_center_style), Paragraph('HIGH', cell_center_style),
     Paragraph('Replace with seeded PRNG matching MC pattern', cell_style)],
    [Paragraph('Connect CapitalAllocation to PaperTrading', cell_style), Paragraph('P0', cell_center_style),
     Paragraph('4h', cell_center_style), Paragraph('CRITICAL', cell_center_style),
     Paragraph('Replace calculatePositionSize() with CapitalAllocationEngine call', cell_style)],
    [Paragraph('Rename decision-engine to token-decision-engine', cell_style), Paragraph('P1', cell_center_style),
     Paragraph('1h', cell_center_style), Paragraph('MEDIUM', cell_center_style),
     Paragraph('Clarify scope: token-level vs strategy-level decisions', cell_style)],
    [Paragraph('Fix StrategyStateManager enforcement', cell_style), Paragraph('P1', cell_center_style),
     Paragraph('3h', cell_center_style), Paragraph('HIGH', cell_center_style),
     Paragraph('PAUSED strategies must not execute trades', cell_style)],
    [Paragraph('Add DecisionLog schema fields', cell_style), Paragraph('P1', cell_center_style),
     Paragraph('2h', cell_center_style), Paragraph('MEDIUM', cell_center_style),
     Paragraph('Add mcRiskOfRuin, wfEfficiency, overfittingScore, validationGrade', cell_style)],
    [Paragraph('Close feedback loop', cell_style), Paragraph('P1', cell_center_style),
     Paragraph('4h', cell_center_style), Paragraph('HIGH', cell_center_style),
     Paragraph('Paper trading results feed back to evolution engine', cell_style)],
]
story.append(make_table(sprint0_data, [AVAILABLE_W*0.25, AVAILABLE_W*0.08, AVAILABLE_W*0.08, AVAILABLE_W*0.10, AVAILABLE_W*0.49]))
story.append(Paragraph('Table 7: Sprint 0 tasks', caption_style))
story.append(Spacer(1, 8))

# Sprint 1
story.append(Paragraph('<b>Sprint 1: Strategy Decision Engine (Week 3-5)</b>', h2_style))
story.append(Paragraph(
    'This is the keystone sprint. The SDE is built from scratch as a new service that consumes outputs from all existing modules. '
    'It implements the four-stage decision pipeline (vetoes, scoring, verdict, capital recommendation) described in Part VI. '
    'The deliverable is a working SDE that can evaluate any strategy and produce a StrategyDecision with validation grade, verdict, '
    'and capital recommendation. The SDE must be fully deterministic and testable.',
    body_style
))
sprint1_data = [
    [Paragraph('<b>Task</b>', header_cell_style),
     Paragraph('<b>Effort</b>', header_cell_style),
     Paragraph('<b>Description</b>', header_cell_style)],
    [Paragraph('SDE Core Pipeline', cell_style), Paragraph('8h', cell_center_style),
     Paragraph('Veto check, composite scoring, verdict mapping, capital recommendation', cell_style)],
    [Paragraph('SDE API Route', cell_style), Paragraph('3h', cell_center_style),
     Paragraph('POST /api/strategy-decision/evaluate, GET /api/strategy-decision/history', cell_style)],
    [Paragraph('StrategyValidation DB Model', cell_style), Paragraph('2h', cell_center_style),
     Paragraph('Prisma model for validation results with full decision context', cell_style)],
    [Paragraph('SDE Unit Tests', cell_style), Paragraph('4h', cell_center_style),
     Paragraph('Test all veto paths, scoring calculations, verdict mappings', cell_style)],
    [Paragraph('SDE Integration Tests', cell_style), Paragraph('3h', cell_center_style),
     Paragraph('End-to-end test with real backtest/MC/WF data', cell_style)],
]
story.append(make_table(sprint1_data, [AVAILABLE_W*0.25, AVAILABLE_W*0.10, AVAILABLE_W*0.65]))
story.append(Paragraph('Table 8: Sprint 1 tasks', caption_style))
story.append(Spacer(1, 8))

# Sprint 2
story.append(Paragraph('<b>Sprint 2: Monte Carlo Institutional Upgrades (Week 6-7)</b>', h2_style))
sprint2_data = [
    [Paragraph('<b>Task</b>', header_cell_style),
     Paragraph('<b>Effort</b>', header_cell_style),
     Paragraph('<b>Description</b>', header_cell_style)],
    [Paragraph('Block Bootstrap', cell_style), Paragraph('6h', cell_center_style),
     Paragraph('Implement block resampling with block size = sqrt(n), preserving temporal autocorrelation', cell_style)],
    [Paragraph('Stress Scenarios', cell_style), Paragraph('4h', cell_center_style),
     Paragraph('Inject -30%, -50%, -90% shocks; flash crash; rug pull at random simulation points', cell_style)],
    [Paragraph('Consecutive Loss Distribution', cell_style), Paragraph('3h', cell_center_style),
     Paragraph('Calculate P(N consecutive losses) for N = 3, 5, 7, 10', cell_style)],
    [Paragraph('MC Risk Profile Output', cell_style), Paragraph('2h', cell_center_style),
     Paragraph('Structured MCRiskProfile interface for SDE consumption', cell_style)],
    [Paragraph('Update MC Panel UI', cell_style), Paragraph('4h', cell_center_style),
     Paragraph('Add Block Bootstrap toggle, stress scenario selector, consecutive loss chart', cell_style)],
]
story.append(make_table(sprint2_data, [AVAILABLE_W*0.25, AVAILABLE_W*0.10, AVAILABLE_W*0.65]))
story.append(Paragraph('Table 9: Sprint 2 tasks', caption_style))
story.append(Spacer(1, 8))

# Sprint 3
story.append(Paragraph('<b>Sprint 3: Walk-Forward Professional Upgrades + Regime Detection (Week 8-10)</b>', h2_style))
sprint3_data = [
    [Paragraph('<b>Task</b>', header_cell_style),
     Paragraph('<b>Effort</b>', header_cell_style),
     Paragraph('<b>Description</b>', header_cell_style)],
    [Paragraph('Parameter Optimization in WF Windows', cell_style), Paragraph('8h', cell_center_style),
     Paragraph('Optimize parameters in training window, test with those parameters out-of-sample', cell_style)],
    [Paragraph('Parameter Drift Analysis', cell_style), Paragraph('4h', cell_center_style),
     Paragraph('Track how optimal parameters change between windows, calculate drift score', cell_style)],
    [Paragraph('Overfitting Probability', cell_style), Paragraph('4h', cell_center_style),
     Paragraph('Implement P(overfit) from WFE distribution per Bailey et al. (2014)', cell_style)],
    [Paragraph('Regime Detection Engine', cell_style), Paragraph('8h', cell_center_style),
     Paragraph('Multi-factor classifier: ADX + volatility percentile + PCA correlation structure', cell_style)],
    [Paragraph('Regime API + Daily Update', cell_style), Paragraph('3h', cell_center_style),
     Paragraph('GET /api/market/regime, daily cron job for regime reclassification', cell_style)],
]
story.append(make_table(sprint3_data, [AVAILABLE_W*0.28, AVAILABLE_W*0.10, AVAILABLE_W*0.62]))
story.append(Paragraph('Table 10: Sprint 3 tasks', caption_style))
story.append(Spacer(1, 8))

# Sprint 4
story.append(Paragraph('<b>Sprint 4: Kill Switches + Portfolio Monitor (Week 11-13)</b>', h2_style))
sprint4_data = [
    [Paragraph('<b>Task</b>', header_cell_style),
     Paragraph('<b>Effort</b>', header_cell_style),
     Paragraph('<b>Description</b>', header_cell_style)],
    [Paragraph('Kill Switch Engine', cell_style), Paragraph('6h', cell_center_style),
     Paragraph('3-level kill switches: position, strategy, portfolio with configurable thresholds', cell_style)],
    [Paragraph('Risk Budget Model + Enforcement', cell_style), Paragraph('4h', cell_center_style),
     Paragraph('Portfolio model, risk budget constraints, concentration limits, SDE enforcement', cell_style)],
    [Paragraph('Portfolio VaR Calculation', cell_style), Paragraph('6h', cell_center_style),
     Paragraph('Historical VaR (95%, 99%) at daily and weekly horizons, CVaR (Expected Shortfall)', cell_style)],
    [Paragraph('Strategy Correlation Monitor', cell_style), Paragraph('4h', cell_center_style),
     Paragraph('Rolling 30-day correlation between strategy equity curves, clustering, concentration alerts', cell_style)],
    [Paragraph('Emergency Close Pipeline', cell_style), Paragraph('4h', cell_center_style),
     Paragraph('Portfolio-level emergency position close with alert + logging + acknowledgment', cell_style)],
    [Paragraph('Portfolio Dashboard UI', cell_style), Paragraph('6h', cell_center_style),
     Paragraph('Portfolio overview, risk budget status, correlation heatmap, kill switch controls', cell_style)],
]
story.append(make_table(sprint4_data, [AVAILABLE_W*0.28, AVAILABLE_W*0.10, AVAILABLE_W*0.62]))
story.append(Paragraph('Table 11: Sprint 4 tasks', caption_style))
story.append(Spacer(1, 8))

# Sprint 5
story.append(Paragraph('<b>Sprint 5: Full Integration Pipeline + Autonomous Loop (Week 14-17)</b>', h2_style))
sprint5_data = [
    [Paragraph('<b>Task</b>', header_cell_style),
     Paragraph('<b>Effort</b>', header_cell_style),
     Paragraph('<b>Description</b>', header_cell_style)],
    [Paragraph('Automated SDE Evaluation Cycle', cell_style), Paragraph('4h', cell_center_style),
     Paragraph('Daily cron: evaluate all active strategies through SDE, update verdicts', cell_style)],
    [Paragraph('Capital Allocation Pipeline', cell_style), Paragraph('6h', cell_center_style),
     Paragraph('SDE verdict drives CapitalAllocationEngine, regime-aware method selection', cell_style)],
    [Paragraph('Evolution-to-Deployment Pipeline', cell_style), Paragraph('4h', cell_center_style),
     Paragraph('Evolved strategies auto-evaluated by SDE, capital migration on improvement', cell_style)],
    [Paragraph('Human Approval Gate', cell_style), Paragraph('4h', cell_center_style),
     Paragraph('Approval dashboard for: new strategy activation, allocation changes greater than 20%, kill switch resets', cell_style)],
    [Paragraph('Operational Monitoring', cell_style), Paragraph('4h', cell_center_style),
     Paragraph('Daily report: active strategies, portfolio risk, recent decisions, pending approvals', cell_style)],
    [Paragraph('End-to-End Integration Test', cell_style), Paragraph('6h', cell_center_style),
     Paragraph('Full pipeline test: brain scan, strategy creation, backtest, MC, WF, SDE, capital allocation, paper trading', cell_style)],
]
story.append(make_table(sprint5_data, [AVAILABLE_W*0.28, AVAILABLE_W*0.10, AVAILABLE_W*0.62]))
story.append(Paragraph('Table 12: Sprint 5 tasks', caption_style))
story.append(Spacer(1, 12))

# Timeline summary
story.append(Paragraph('<b>7.2 Timeline Summary</b>', h2_style))
timeline_data = [
    [Paragraph('<b>Sprint</b>', header_cell_style),
     Paragraph('<b>Weeks</b>', header_cell_style),
     Paragraph('<b>Key Deliverable</b>', header_cell_style),
     Paragraph('<b>System Capability After</b>', header_cell_style)],
    [Paragraph('Sprint 0', cell_style), Paragraph('1-2', cell_center_style),
     Paragraph('Foundation fixes', cell_style),
     Paragraph('Reproducible evolution, connected capital allocation, enforced state management', cell_style)],
    [Paragraph('Sprint 1', cell_style), Paragraph('3-5', cell_center_style),
     Paragraph('Strategy Decision Engine', cell_style),
     Paragraph('Automated strategy evaluation with validation grades and capital recommendations', cell_style)],
    [Paragraph('Sprint 2', cell_style), Paragraph('6-7', cell_center_style),
     Paragraph('MC institutional upgrades', cell_style),
     Paragraph('Block Bootstrap, stress testing, institutional-grade risk profiles', cell_style)],
    [Paragraph('Sprint 3', cell_style), Paragraph('8-10', cell_center_style),
     Paragraph('WF professional + regime detection', cell_style),
     Paragraph('Parameter optimization in WFA, overfitting probability, real regime detection', cell_style)],
    [Paragraph('Sprint 4', cell_style), Paragraph('11-13', cell_center_style),
     Paragraph('Kill switches + portfolio monitor', cell_style),
     Paragraph('Automated circuit breakers, risk budget enforcement, VaR, correlation monitoring', cell_style)],
    [Paragraph('Sprint 5', cell_style), Paragraph('14-17', cell_center_style),
     Paragraph('Full integration pipeline', cell_style),
     Paragraph('Semi-autonomous operation with human approval gates, end-to-end automation', cell_style)],
]
story.append(make_table(timeline_data, [AVAILABLE_W*0.10, AVAILABLE_W*0.10, AVAILABLE_W*0.30, AVAILABLE_W*0.50]))
story.append(Paragraph('Table 13: Sprint timeline and system capability progression', caption_style))
story.append(Spacer(1, 12))

# Key callout
story.append(make_callout(
    '<b>Critical Path:</b> Sprint 1 (Strategy Decision Engine) is the keystone. Without it, Sprints 2-5 have no consumer for their '
    'outputs. Prioritize getting the SDE to a working state before investing in MC/WF upgrades. A simple SDE with basic scoring is more '
    'valuable than institutional-grade MC that nobody reads.',
    SEM_INFO
))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PART VIII: KNOWN BUGS TRACKER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
story.append(Spacer(1, 18))
story.append(add_heading('<b>Part VIII: Known Bugs and Pending Items</b>', h1_style, level=0))

bugs_data = [
    [Paragraph('<b>ID</b>', header_cell_style),
     Paragraph('<b>Description</b>', header_cell_style),
     Paragraph('<b>Status</b>', header_cell_style),
     Paragraph('<b>Sprint</b>', header_cell_style)],
    [Paragraph('B5', cell_style), Paragraph('saveControlsMutation uses setTimeout(500) fake delay', cell_style),
     Paragraph('Open', cell_center_style), Paragraph('Sprint 0', cell_center_style)],
    [Paragraph('B6', cell_style), Paragraph('smart-money-sync imports analyticsToMetrics from wrong module', cell_style),
     Paragraph('Open', cell_center_style), Paragraph('Sprint 0', cell_center_style)],
    [Paragraph('B7', cell_style), Paragraph('phase-strategy-engine token type incomplete', cell_style),
     Paragraph('Open', cell_center_style), Paragraph('Sprint 0', cell_center_style)],
    [Paragraph('B8/B9', cell_style), Paragraph('strategy-marketplace CATEGORY_META missing bg, stars typed as never[]', cell_style),
     Paragraph('Open', cell_center_style), Paragraph('Sprint 0', cell_center_style)],
    [Paragraph('B10', cell_style), Paragraph('Candles only fetched for top tokens, not user-selected', cell_style),
     Paragraph('Partial', cell_center_style), Paragraph('Sprint 1', cell_center_style)],
    [Paragraph('B12', cell_style), Paragraph('Monte Carlo UI exists but needs Block Bootstrap/stress controls', cell_style),
     Paragraph('Partial', cell_center_style), Paragraph('Sprint 2', cell_center_style)],
    [Paragraph('B13', cell_style), Paragraph('Walk-Forward UI exists but needs parameter optimization viz', cell_style),
     Paragraph('Partial', cell_center_style), Paragraph('Sprint 3', cell_center_style)],
    [Paragraph('B14', cell_style), Paragraph('Chart data source badge missing', cell_style),
     Paragraph('Open', cell_center_style), Paragraph('Sprint 0', cell_center_style)],
    [Paragraph('B15-B18', cell_style), Paragraph('Cleanup, security, minor UI fixes', cell_style),
     Paragraph('Open', cell_center_style), Paragraph('Sprint 0', cell_center_style)],
]
story.append(make_table(bugs_data, [AVAILABLE_W*0.08, AVAILABLE_W*0.57, AVAILABLE_W*0.12, AVAILABLE_W*0.13]))
story.append(Paragraph('Table 14: Known bugs and sprint assignment', caption_style))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BUILD
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("Building body PDF...")
doc.multiBuild(story)
print(f"Body PDF saved to: {BODY_PDF}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COVER PAGE (HTML/Playwright)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COVER_HTML = os.path.join(OUTPUT_DIR, 'cover_analysis_v3.html')
COVER_PDF = os.path.join(OUTPUT_DIR, 'cover_analysis_v3.pdf')

cover_html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
  @page { size: 794px 1123px; margin: 0; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 794px; height: 1123px; overflow: hidden; font-family: 'Inter', sans-serif; }
  .cover {
    width: 794px; height: 1123px; position: relative;
    background: linear-gradient(145deg, #1a1730 0%, #0f0e1a 40%, #0a0a12 100%);
    color: white; overflow: hidden;
  }
  .accent-line {
    position: absolute; top: 0; left: 60px; width: 3px; height: 100%;
    background: linear-gradient(to bottom, transparent 10%, #5a31d5 30%, #4fbd86 70%, transparent 90%);
  }
  .accent-line-2 {
    position: absolute; top: 0; right: 80px; width: 1px; height: 100%;
    background: linear-gradient(to bottom, transparent 20%, rgba(90,49,213,0.3) 50%, transparent 80%);
  }
  .kicker {
    position: absolute; top: 140px; left: 80px;
    font-size: 14px; letter-spacing: 6px; text-transform: uppercase;
    color: rgba(79,189,134,0.8); font-weight: 600;
  }
  .title {
    position: absolute; top: 180px; left: 80px; right: 80px;
    font-size: 42px; font-weight: 800; line-height: 1.2;
    color: #ffffff;
  }
  .title .highlight { color: #5a31d5; }
  .subtitle {
    position: absolute; top: 340px; left: 80px; right: 120px;
    font-size: 16px; line-height: 1.7; color: rgba(255,255,255,0.6);
    font-weight: 300;
  }
  .meta {
    position: absolute; bottom: 120px; left: 80px;
    font-size: 13px; color: rgba(255,255,255,0.4); line-height: 2;
  }
  .meta .label { color: rgba(79,189,134,0.7); font-weight: 600; margin-right: 8px; }
  .badge {
    position: absolute; top: 140px; right: 80px;
    background: rgba(90,49,213,0.15); border: 1px solid rgba(90,49,213,0.3);
    padding: 8px 16px; border-radius: 4px;
    font-family: 'JetBrains Mono', monospace; font-size: 12px;
    color: rgba(90,49,213,0.9); letter-spacing: 2px;
  }
  .version-watermark {
    position: absolute; bottom: 60px; right: 80px;
    font-family: 'JetBrains Mono', monospace; font-size: 80px; font-weight: 800;
    color: rgba(90,49,213,0.04); letter-spacing: -4px;
  }
  .geometric {
    position: absolute; top: 500px; right: -100px;
    width: 400px; height: 400px; border: 1px solid rgba(90,49,213,0.08);
    border-radius: 50%;
  }
  .geometric-2 {
    position: absolute; top: 550px; right: -50px;
    width: 300px; height: 300px; border: 1px solid rgba(79,189,134,0.06);
    border-radius: 50%;
  }
</style>
</head>
<body>
<div class="cover">
  <div class="accent-line"></div>
  <div class="accent-line-2"></div>
  <div class="badge">v3.0</div>
  <div class="kicker">CRITICAL ANALYSIS</div>
  <div class="title">CryptoQuant<br/>Terminal<br/><span class="highlight">Strategy Decision</span><br/>Engine Design</div>
  <div class="subtitle">
    Deep architectural review from Quant Developer, Portfolio Manager, and Risk Manager perspectives.
    30 key questions answered. Strategy Decision Engine designed. Prioritized 6-sprint roadmap toward
    semi-autonomous operation.
  </div>
  <div class="meta">
    <div><span class="label">DATE</span>2026-06-04</div>
    <div><span class="label">VERSION</span>3.0 — Comprehensive Analysis</div>
    <div><span class="label">SCOPE</span>Architecture, Decision Engine, Capital Allocation, Risk</div>
    <div><span class="label">ROLES</span>Quant Dev / Portfolio Mgr / Risk Mgr</div>
  </div>
  <div class="version-watermark">v3</div>
  <div class="geometric"></div>
  <div class="geometric-2"></div>
</div>
</body>
</html>"""

with open(COVER_HTML, 'w') as f:
    f.write(cover_html)
print(f"Cover HTML saved to: {COVER_HTML}")

# Render cover
import subprocess
try:
    result = subprocess.run([
        'node', '/home/z/my-project/skills/pdf/scripts/html2poster.js',
        COVER_HTML, '--output', COVER_PDF, '--width', '794px'
    ], capture_output=True, text=True, timeout=60)
    print(f"Cover render stdout: {result.stdout}")
    if result.returncode != 0:
        print(f"Cover render stderr: {result.stderr}")
        # Fallback: try direct Playwright
        print("Attempting direct Playwright fallback...")
except Exception as e:
    print(f"Cover render error: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MERGE COVER + BODY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if os.path.exists(COVER_PDF) and os.path.exists(BODY_PDF):
    from pypdf import PdfReader, PdfWriter, Transformation

    A4_W, A4_H = 595.28, 841.89

    def normalize_page(page):
        box = page.mediabox
        w, h = float(box.width), float(box.height)
        if abs(w - A4_W) > 2 or abs(h - A4_H) > 2:
            sx, sy = A4_W / w, A4_H / h
            page.add_transformation(Transformation().scale(sx=sx, sy=sy))
            page.mediabox.lower_left = (0, 0)
            page.mediabox.upper_right = (A4_W, A4_H)
        return page

    writer = PdfWriter()
    # Cover as page 1
    cover_page = PdfReader(COVER_PDF).pages[0]
    writer.add_page(normalize_page(cover_page))
    # Body pages
    for page in PdfReader(BODY_PDF).pages:
        writer.add_page(normalize_page(page))
    writer.add_metadata({
        '/Title': 'CryptoQuant Terminal - Critical Analysis v3.0',
        '/Author': 'Z.ai',
        '/Creator': 'Z.ai',
        '/Subject': 'Strategy Decision Engine Design and Prioritized Roadmap'
    })
    with open(FINAL_PDF, 'wb') as f:
        writer.write(f)
    print(f"\nFinal PDF saved to: {FINAL_PDF}")
    print(f"Total pages: {len(writer.pages)}")
else:
    print("WARNING: Could not merge cover. Using body PDF as final output.")
    import shutil
    shutil.copy(BODY_PDF, FINAL_PDF)
    print(f"Final PDF (body only) saved to: {FINAL_PDF}")

# Cleanup intermediate files
for f in [BODY_PDF, COVER_PDF, COVER_HTML]:
    try:
        if os.path.exists(f):
            os.remove(f)
    except:
        pass

print("\nDone!")
