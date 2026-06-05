#!/usr/bin/env python3
"""
CryptoQuant Terminal - Fase 1: Auditoria de Flujo Completo
Genera PDF profesional con el mapa completo del sistema
"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    Image, PageBreak, KeepTogether, HRFlowable
)
from reportlab.lib import colors

W, H = A4
MARGIN = 20*mm
CONTENT_W = W - 2*MARGIN

# ── Colors ──
C_PRIMARY = HexColor('#1E3A5F')
C_SECONDARY = HexColor('#3B7DD8')
C_ACCENT = HexColor('#E8913A')
C_BG_LIGHT = HexColor('#F5F7FA')
C_BG_CARD = HexColor('#EDF1F7')
C_TEXT = HexColor('#1F2937')
C_TEXT_MUTED = HexColor('#6B7280')
C_BORDER = HexColor('#D1D5DB')
C_SUCCESS = HexColor('#059669')
C_DANGER = HexColor('#DC2626')
C_WARNING = HexColor('#D97706')

# ── Styles ──
styles = getSampleStyleSheet()

s_title = ParagraphStyle('DocTitle', parent=styles['Title'],
    fontSize=28, leading=34, textColor=C_PRIMARY, spaceAfter=6*mm,
    fontName='Helvetica-Bold', alignment=TA_CENTER)

s_subtitle = ParagraphStyle('DocSubtitle', parent=styles['Normal'],
    fontSize=14, leading=18, textColor=C_TEXT_MUTED, spaceAfter=12*mm,
    fontName='Helvetica', alignment=TA_CENTER)

s_h1 = ParagraphStyle('H1', parent=styles['Heading1'],
    fontSize=20, leading=26, textColor=C_PRIMARY, spaceBefore=14*mm, spaceAfter=6*mm,
    fontName='Helvetica-Bold', borderWidth=0, borderPadding=0,
    leftIndent=0)

s_h2 = ParagraphStyle('H2', parent=styles['Heading2'],
    fontSize=15, leading=20, textColor=C_SECONDARY, spaceBefore=8*mm, spaceAfter=4*mm,
    fontName='Helvetica-Bold')

s_h3 = ParagraphStyle('H3', parent=styles['Heading3'],
    fontSize=12, leading=16, textColor=HexColor('#4A5568'), spaceBefore=5*mm, spaceAfter=3*mm,
    fontName='Helvetica-Bold')

s_body = ParagraphStyle('Body', parent=styles['Normal'],
    fontSize=9.5, leading=14, textColor=C_TEXT, spaceAfter=3*mm,
    fontName='Helvetica', alignment=TA_JUSTIFY)

s_body_small = ParagraphStyle('BodySmall', parent=s_body,
    fontSize=8.5, leading=12, spaceAfter=2*mm)

s_bullet = ParagraphStyle('Bullet', parent=s_body,
    leftIndent=12*mm, bulletIndent=6*mm, spaceAfter=1.5*mm)

s_bullet2 = ParagraphStyle('Bullet2', parent=s_body,
    leftIndent=20*mm, bulletIndent=14*mm, fontSize=8.5, leading=12, spaceAfter=1*mm)

s_caption = ParagraphStyle('Caption', parent=styles['Normal'],
    fontSize=8, leading=11, textColor=C_TEXT_MUTED, alignment=TA_CENTER,
    spaceBefore=2*mm, spaceAfter=6*mm, fontName='Helvetica-Oblique')

s_table_header = ParagraphStyle('TableHeader', parent=styles['Normal'],
    fontSize=8, leading=11, textColor=colors.white, fontName='Helvetica-Bold')

s_table_cell = ParagraphStyle('TableCell', parent=styles['Normal'],
    fontSize=8, leading=11, textColor=C_TEXT, fontName='Helvetica')

s_table_cell_small = ParagraphStyle('TableCellSmall', parent=s_table_cell,
    fontSize=7, leading=9.5)

s_note = ParagraphStyle('Note', parent=s_body,
    fontSize=8, leading=11, textColor=C_TEXT_MUTED, fontName='Helvetica-Oblique',
    leftIndent=8*mm, borderWidth=0)

# ── Helpers ──
def heading1(text):
    return Paragraph(text, s_h1)

def heading2(text):
    return Paragraph(text, s_h2)

def heading3(text):
    return Paragraph(text, s_h3)

def body(text):
    return Paragraph(text, s_body)

def body_small(text):
    return Paragraph(text, s_body_small)

def bullet(text):
    return Paragraph(f'<bullet>&bull;</bullet> {text}', s_bullet)

def bullet2(text):
    return Paragraph(f'<bullet>-</bullet> {text}', s_bullet2)

def caption(text):
    return Paragraph(text, s_caption)

def spacer(h=4*mm):
    return Spacer(1, h)

def hr():
    return HRFlowable(width="100%", thickness=0.5, color=C_BORDER, spaceBefore=3*mm, spaceAfter=3*mm)

def note(text):
    return Paragraph(text, s_note)

def img_centered(path, max_w=CONTENT_W, caption_text=None):
    """Insert image scaled to max_w, preserving aspect ratio."""
    from PIL import Image as PILImage
    elems = []
    if os.path.exists(path):
        pil = PILImage.open(path)
        iw, ih = pil.size
        ratio = ih / iw
        display_w = min(max_w, CONTENT_W)
        display_h = display_w * ratio
        # Cap height to avoid single image spanning too many pages
        max_h = 550
        if display_h > max_h:
            display_h = max_h
            display_w = display_h / ratio
        img = Image(path, width=display_w, height=display_h)
        img.hAlign = 'CENTER'
        elems.append(img)
        if caption_text:
            elems.append(caption(caption_text))
    else:
        elems.append(body(f'[Imagen no encontrada: {path}]'))
    return elems

def make_table(headers, rows, col_widths=None):
    """Create a styled table."""
    hdr = [Paragraph(h, s_table_header) for h in headers]
    data = [hdr]
    for row in rows:
        data.append([Paragraph(str(c), s_table_cell) if not isinstance(c, Paragraph) else c for c in row])
    if not col_widths:
        col_widths = [CONTENT_W / len(headers)] * len(headers)
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), C_PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, C_BG_LIGHT]),
        ('GRID', (0, 0), (-1, -1), 0.5, C_BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]))
    return t

# ── Document ──
OUTPUT = '/home/z/my-project/download/Phase1_Auditoria_Flujo_Completo.pdf'
IMG_DIR = '/home/z/my-project/download'

doc = SimpleDocTemplate(OUTPUT, pagesize=A4,
    leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
    title='CryptoQuant Terminal - Fase 1: Auditoria de Flujo Completo',
    author='Z.ai', subject='Auditoria exhaustiva del sistema')

story = []

# ════════════════════════════════════════════════════════════════
# COVER PAGE
# ════════════════════════════════════════════════════════════════
story.append(Spacer(1, 40*mm))
story.append(Paragraph('CryptoQuant Terminal', s_title))
story.append(Spacer(1, 8*mm))
story.append(Paragraph('Fase 1: Auditoria de Flujo Completo', ParagraphStyle('CoverH2',
    parent=s_subtitle, fontSize=18, leading=24, textColor=C_SECONDARY)))
story.append(Spacer(1, 6*mm))
story.append(Paragraph('Mapa completo del sistema desde la entrada de datos hasta la salida final', s_subtitle))
story.append(Spacer(1, 20*mm))

# Metadata table
meta_data = [
    ['Fecha', '2026-06-05'],
    ['Version', '1.0'],
    ['Repo', 'github.com/coverdraft/cryptoquant-terminal'],
    ['Ultimo commit', 'c9b027d'],
    ['Metodologia', '6 Fases (solo mapeo, sin modificaciones)'],
    ['Estado', 'FASE 1 COMPLETADA'],
]
meta_t = Table(meta_data, colWidths=[50*mm, 90*mm])
meta_t.setStyle(TableStyle([
    ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
    ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
    ('FONTSIZE', (0, 0), (-1, -1), 10),
    ('TEXTCOLOR', (0, 0), (0, -1), C_PRIMARY),
    ('TEXTCOLOR', (1, 0), (1, -1), C_TEXT),
    ('TOPPADDING', (0, 0), (-1, -1), 4),
    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ('LINEBELOW', (0, 0), (-1, -2), 0.5, C_BORDER),
    ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
    ('ALIGN', (1, 0), (1, -1), 'LEFT'),
]))
story.append(meta_t)
story.append(PageBreak())

# ════════════════════════════════════════════════════════════════
# TABLE OF CONTENTS
# ════════════════════════════════════════════════════════════════
story.append(heading1('Indice'))
toc_items = [
    '1. Resumen Ejecutivo',
    '2. Arquitectura General del Sistema',
    '3. Flujo de Datos Completo (6 Fases)',
    '4. Catalogo de Modulos (89 archivos, ~40 singletons)',
    '5. Mapa de Rutas API (112 endpoints)',
    '6. Esquema de Base de Datos (38 modelos)',
    '7. Integraciones Externas',
    '8. Pipeline del Strategy Decision Engine (SDE)',
    '9. Kill Switches y Gestion de Riesgo',
    '10. Capital Allocation Pipeline',
    '11. Estado de las Conexiones Criticas',
    '12. Hallazgos y Observaciones',
]
for item in toc_items:
    story.append(Paragraph(item, ParagraphStyle('TOC', parent=s_body,
        fontSize=10, leading=16, spaceAfter=1*mm, fontName='Helvetica')))
story.append(PageBreak())

# ════════════════════════════════════════════════════════════════
# 1. RESUMEN EJECUTIVO
# ════════════════════════════════════════════════════════════════
story.append(heading1('1. Resumen Ejecutivo'))
story.append(body(
    'Este documento presenta el resultado de la <b>Fase 1: Auditoria de Flujo Completo</b> del sistema CryptoQuant Terminal. '
    'Se ha construido un mapa exhaustivo del sistema desde la entrada de datos hasta la salida final, identificando '
    'todos los modulos, dependencias, integraciones, flujos de datos, transformaciones y puntos de decision. '
    '<b>No se han realizado modificaciones al codigo</b> — esta fase es exclusivamente de mapeo y documentacion.'
))
story.append(body(
    'CryptoQuant Terminal es una plataforma de trading cripto semi-autonoma construida con Next.js 16.1.3 (Turbopack), '
    'Prisma 7, SQLite, Tailwind CSS y shadcn/ui. El sistema cuenta con 89 archivos de servicio organizados en 6 directorios, '
    '112 endpoints API, 38 modelos de base de datos, y aproximadamente 40 singletons de servicio activos. '
    'La arquitectura sigue un pipeline de 6 fases: Ingesta de Datos, Analisis e Inteligencia, Estrategia y Validacion, '
    'Decision y Riesgo, Asignacion de Capital, y Ejecucion.'
))
story.append(body(
    'El componente central es el <b>Strategy Decision Engine (SDE)</b>, que actua como el cerebro del sistema. '
    'Implementa un pipeline de 6 pasos: vetos duros, scores compuestos, calidad de senal, estado + accion de capital, '
    'recomendacion de capital, y registro de auditoria. El SDE transforma la plataforma de una herramienta de analisis '
    'que produce informacion a un sistema de trading que produce decisiones accionables.'
))

# Key metrics table
story.append(heading3('Metricas Clave del Sistema'))
metrics = [
    ['Archivos de servicio', '89'],
    ['Singletons activos', '~40'],
    ['Endpoints API', '112'],
    ['Modelos Prisma (DB)', '38'],
    ['Tablas con @@map', '22'],
    ['Indices compuestos', '30+'],
    ['Componentes UI (shadcn)', '50'],
    ['Pestanas activas en UI', '19'],
    ['Integraciones API externas', '13'],
    ['Metodos de capital allocation', '16 (5 activos en v1)'],
    ['Patrones de vela detectados', '36 (5 timeframes)'],
    ['Arquetipos de trader', '8'],
]
story.append(make_table(['Metrica', 'Valor'], metrics, [80*mm, 60*mm]))
story.append(spacer(4*mm))

# ════════════════════════════════════════════════════════════════
# 2. ARQUITECTURA GENERAL
# ════════════════════════════════════════════════════════════════
story.append(heading1('2. Arquitectura General del Sistema'))
story.append(body(
    'El sistema CryptoQuant Terminal sigue una arquitectura modular de 6 capas, donde cada capa depende de las anteriores '
    'pero no de las posteriores. Los datos fluyen de izquierda a derecha: desde las fuentes externas de datos, a traves de '
    'los motores de analisis e inteligencia, hacia los motores de estrategia y validacion, convergiendo en el SDE para la '
    'toma de decisiones, y finalmente hacia la asignacion de capital y la ejecucion (paper trading).'
))

# Insert architecture diagram
story.extend(img_centered(
    os.path.join(IMG_DIR, 'architecture-full-flow.png'),
    max_w=CONTENT_W,
    caption_text='Figura 1: Flujo completo del sistema — Desde la ingesta de datos hasta la ejecucion'
))

story.append(body(
    'La arquitectura se organiza en 6 fases claramente diferenciadas. La Fase 1 (Ingesta) recopila datos de 13+ fuentes '
    'externas a traves de clientes API especializados, con un sistema de cache unificado y cascada (Binance -> CoinGecko -> DexPaprika). '
    'La Fase 2 (Analisis) ejecuta un pipeline de 11 fases por token a traves del Brain Orchestrator, produciendo analisis '
    'de ciclo de vida, modelos comportamentales, patrones de vela, y predicciones. La Fase 3 (Validacion) ejecuta backtests, '
    'simulaciones Monte Carlo, analisis walk-forward, y evolucion de estrategias. La Fase 4 (Decision) es el SDE — el cerebro '
    'central que sintetiza todos los datos en decisiones accionables. La Fase 5 (Asignacion) calcula tamanos de posicion '
    'usando 5 metodos activos. La Fase 6 (Ejecucion) opera el paper trading con controles de riesgo integrados.'
))

story.append(PageBreak())

# ════════════════════════════════════════════════════════════════
# 3. FLUJO DE DATOS COMPLETO
# ════════════════════════════════════════════════════════════════
story.append(heading1('3. Flujo de Datos Completo'))

story.append(heading2('3.1 Fase 1: Ingesta de Datos'))
story.append(body(
    'La ingesta de datos es la puerta de entrada del sistema. Se conecta con 13+ fuentes externas de datos a traves de '
    'clientes API especializados, cada uno con su propio rate limiting, cache, y manejo de errores. El componente central '
    'es el OHLCV Pipeline, que implementa una cascada de fuentes: primero intenta Binance (mayor cobertura y precision), '
    'luego CoinGecko (cobertura amplia), y finalmente DexPaprika (tokens DEX no listados en CEX).'
))
story.append(bullet('<b>OHLCV Pipeline</b>: Cascada Binance -> CoinGecko -> DexPaprika. Agrega timeframes superiores (1m->5m->15m->1h->4h->1d). Backfill historico con deteccion de gaps.'))
story.append(bullet('<b>Data Ingestion Pipeline</b>: Orquesta sync de tokens (DexScreener), market data (CoinGecko), transacciones on-chain (Jupiter/Solana RPC/Ethereum RPC), wallet history, y smart money data.'))
story.append(bullet('<b>Multi-Chain Screener</b>: Screening multi-cadena agregando datos de DexPaprika, CoinGecko y DexScreener.'))
story.append(bullet('<b>Universal Data Extractor</b>: Framework de extraccion que envuelve Moralis, Helius, DefiLlama, EtherscanV2, y CryptoDataDownload.'))
story.append(bullet('<b>Unified Cache</b>: Cache TTL en memoria con deduplicacion de requests, rate-limit awareness, y eviction policies por fuente.'))

story.append(heading3('Clientes API Especializados'))
api_clients = [
    ['CoinGecko Client', 'Tokens paginados, OHLCV, market charts, global data, trending, search', 'TTL: 60s-5min'],
    ['DexScreener Client', 'DEX pairs, token search, trending, liquidez', 'TTL: 30s-2min'],
    ['DexPaprika Client', '20+ chains, pools, swaps, OHLCV, buy/sell pressure, smart money swaps', 'TTL: 60s-5min'],
    ['Binance Client', 'OHLCV candles, symbol info, tickers (intervalos 1m a 1M)', 'TTL: 15s-1min'],
    ['Etherscan Client', 'Token transfers, transacciones, descubrimiento de traders', 'Rate-limited'],
    ['Footprint Client', 'Token prices, OHLCV, protocol TVL, chain overviews', 'TTL: 5min'],
    ['SQD/Subsquid Client', 'Indexer blockchain, eventos historicos, transfers', 'On-demand'],
    ['Helius Client', 'Transacciones Solana, parsed transactions', 'TTL: 2min'],
    ['Moralis Client', 'Multi-chain wallets, balances, NFTs', 'TTL: 5min'],
    ['DefiLlama Client', 'Protocol TVL, yields, chain overview', 'TTL: 10min'],
    ['EtherscanV2 Client', 'Multi-chain EVM data (via Etherscan V2)', 'Rate-limited'],
    ['CryptoDataDownload', 'Historical CSV data', 'On-demand'],
    ['Dune Client', 'SQL queries on blockchain data', 'On-demand'],
]
story.append(make_table(['Cliente', 'Funcionalidad', 'Cache'],
    api_clients, [35*mm, 85*mm, 30*mm]))

story.append(heading2('3.2 Fase 2: Analisis e Inteligencia'))
story.append(body(
    'El Brain Orchestrator ejecuta un pipeline de 11 fases por token: data collection -> market context -> lifecycle -> '
    'behavior -> bot/whale detection -> operability -> predictive signals -> candlestick patterns -> deep analysis -> '
    'cross-correlation -> recommended action. El Brain Scheduler ejecuta 7 tareas periodicas: brain cycle (5min), '
    'market sync (5min), OHLCV backfill (15min), signal validation (15min), evolution check (1hr), capital update (10min), '
    'y data cleanup (6hr). El scheduler persiste su estado en DB para sobrevivir reinicios.'
))

brain_modules = [
    ['Brain Orchestrator', 'Pipeline de 11 fases por token, batch analysis, wallet profiling', 'brain-orchestrator.ts'],
    ['Brain Scheduler', '7 tareas periodicas, persistencia de estado, WS events', 'brain-scheduler.ts'],
    ['Brain Cycle Engine', 'Loop SCAN->FILTER->MATCH->STORE->FEEDBACK->GROWTH 24/7', 'brain-cycle-engine.ts'],
    ['Brain Analysis Pipeline', 'Orquesta todos los engines en secuencia, mantenimiento periodico', 'brain-analysis-pipeline.ts'],
    ['Token Lifecycle Engine', '6 fases: GENESIS->INCIPIENT->GROWTH->FOMO->DECLINE->LEGACY', 'token-lifecycle-engine.ts'],
    ['Behavioral Model Engine', '8 arquetipos, matrices 3D, actualizacion bayesiana', 'behavioral-model-engine.ts'],
    ['Candlestick Pattern Engine', '36 patrones, 5 timeframes, scoring por confluencia', 'candlestick-pattern-engine.ts'],
    ['Deep Analysis Engine', 'Sintesis con LLM (z-ai-sdk) + fallback rule-based', 'deep-analysis-engine.ts'],
    ['Cross-Correlation Engine', 'P(outcome|conditions), tablas de probabilidad historica', 'cross-correlation-engine.ts'],
    ['Smart Money Tracker', 'Deteccion de senales SM, perfiles de wallet, flujo SM', 'smart-money-tracker.ts'],
    ['Bot Detection Engine', '8 tipos de bots, 20+ senales, deteccion batch', 'bot-detection.ts'],
    ['Big Data Predictive', 'Regimenes, anomalias, whale forecasting, mean reversion', 'big-data-engine.ts'],
    ['Pattern Compression', 'Compresion de patrones con decaimiento temporal, limite 500/cat', 'pattern-compression-pipeline.ts'],
    ['Token DNA Recalculator', 'Recalculo de scores DNA desde datos de trader intelligence', 'token-dna-recalculator.ts'],
    ['Brain Capacity Engine', 'Metricas de capacidad en 12 categorias, niveles DORMANT->OPTIMAL', 'brain-capacity-engine.ts'],
]
story.append(make_table(['Modulo', 'Funcion', 'Archivo'],
    brain_modules, [40*mm, 80*mm, 40*mm]))

story.append(heading2('3.3 Fase 3: Estrategia y Validacion'))
story.append(body(
    'La capa de estrategia y validacion transforma los analisis en estrategias probadas. El Backtesting Engine simula '
    'estrategias contra datos historicos con 3 modos (HISTORICAL/PAPER/FORWARD), calculando metricas comprehensivas: '
    'Sharpe, Sortino, Calmar, max drawdown, win rate, profit factor, recovery factor, monthly returns, y phase breakdowns. '
    'El Monte Carlo Simulator genera miles de caminos aleatorios para calcular VaR, CVaR, intervalos de confianza, y '
    'distribuciones de max drawdown. El Walk-Forward Engine divide datos en ventanas de entrenamiento/validacion para '
    'probar robustez fuera de muestra. El Feedback Loop valida senales predictivas contra resultados reales y refina '
    'sistemas suboptimos. El Auto-Evolution Loop ejecuta ciclos continuos de evaluacion, mutacion, backtest y despliegue '
    'de estrategias mejoradas.'
))

strat_modules = [
    ['Backtesting Engine', 'Simulacion historica, metricas completas, stop-loss/take-profit', 'backtesting-engine.ts'],
    ['Monte Carlo Simulator', 'VaR, CVaR, intervalos de confianza, LCG PRNG reproducible', 'monte-carlo-simulator.ts'],
    ['Walk-Forward Engine', 'Training/validation windows, robustez OOS, degradacion', 'walk-forward-engine.ts'],
    ['Feedback Loop Engine', 'Validacion senal->resultado, refinacion, variantes sinteticas', 'feedback-loop-engine.ts'],
    ['Strategy Evolution Engine', 'Mutacion, seleccion, prueba via backtest y WF', 'strategy-evolution-engine.ts'],
    ['Auto-Evolution Loop', 'Ciclo continuo: underperformers -> mutacion -> backtest -> deploy', 'auto-evolution-loop.ts'],
    ['Backtest Loop Engine', 'Loop automatizado por stages (EARLY/MID/STABLE)', 'backtest-loop-engine.ts'],
    ['Statistical Validation', 't-tests, chi-square, correlacion, tamano muestral, decay temporal', 'statistical-validation.ts'],
    ['Trading System Engine', '8 categorias, 20+ templates, CRUD, phase configs', 'trading-system-engine.ts'],
    ['Technical Indicators', 'SMA, EMA, RSI, MACD, Bollinger, ATR, Stochastic, ADX, VWAP, Vol Profile', 'technical-indicators.ts'],
    ['Signal Generators', 'Smart money, rug pull, V-shape, liquidity trap, technical signals', 'signal-generators.ts'],
    ['Parameter Drift Analyzer', 'Comparacion vs baseline, clasificacion STABLE->UNSTABLE', 'parameter-drift-analyzer.ts'],
    ['Strategy State Manager', 'Maquina de estados: IDLE->SCANNING->ANALYZING->...->COOLING', 'strategy-state-manager.ts'],
    ['Strategy Templates', '8 categorias, seeding, templates built-in', 'strategy-templates.ts'],
    ['Backtest Data Bridge', 'Carga datos reales al backtester, normalizacion, gaps', 'backtest-data-bridge.ts'],
]
story.append(make_table(['Modulo', 'Funcion', 'Archivo'],
    strat_modules, [40*mm, 80*mm, 40*mm]))

story.append(heading2('3.4 Fase 4: Decision y Riesgo'))
story.append(body(
    'El Strategy Decision Engine (SDE) es el cerebro central del sistema. Implementa un pipeline de 6 pasos que transforma '
    'datos de backtest, Monte Carlo, walk-forward, operability y paper trading en una decision accionable con 3 dimensiones: '
    'State (ACTIVE/CONDITIONAL/PAUSED/REJECTED), Capital Action (INCREASE/MAINTAIN/REDUCE/EXIT), y Signal Quality '
    '(STRONG/ADEQUATE/WEAK). Cada decision genera un registro de auditoria completo con snapshots de inputs, processing, '
    'decision y configuracion. El Kill Switch Service monitorea 7 niveles de riesgo: portfolio DD>20%, strategy DD>30%, '
    'position loss>50%, token concentration>15%, chain concentration>50%, sector concentration>30%, y emergency global pause.'
))

# Insert SDE diagram
story.extend(img_centered(
    os.path.join(IMG_DIR, 'sde-pipeline-detail.png'),
    max_w=CONTENT_W,
    caption_text='Figura 2: Pipeline detallado del Strategy Decision Engine (SDE) — 6 pasos'
))

risk_modules = [
    ['Strategy Decision Engine', 'Pipeline veto-first: vetos->scores->quality->state->capital->audit', 'strategy-decision-engine.ts'],
    ['Token Decision Engine', 'Decisiones per-token: OPERATE/SKIP/WATCH/EXIT/ADJUST', 'token-decision-engine.ts'],
    ['Kill Switch Service', '7 kill switches, concentracion, auto-pause, manual controls', 'kill-switch-service.ts'],
    ['Risk Controls Verifier', 'Pre-trade: limites de posicion, DD, daily loss, kill switch', 'risk-controls-verifier.ts'],
    ['Operability Score', 'Score 0-100, niveles PREMIUM->UNOPERABLE, fee-aware', 'operability-score.ts'],
    ['Capital Allocation Engine', '16 metodos (5 activos v1), position sizing', 'capital-allocation.ts'],
    ['Capital Strategy Manager', 'Modo ULTRA_CONSERVATIVE->CONCENTRATED, learning state', 'capital-strategy-manager.ts'],
    ['Monte Carlo Simulator', 'Simulacion de riesgo, VaR, CVaR, max DD distribution', 'monte-carlo-simulator.ts'],
    ['Alert Engine', '6 categorias, 3 severidades, distribucion WS', 'alert-engine.ts'],
    ['Strategy Correlation', 'Matrices de correlacion, deteccion sobre-concentracion', 'strategy-correlation-service.ts'],
    ['Data Quality Gate', 'Completitud, frescura, consistencia, precision', 'data-quality-gate.ts'],
    ['Regime Heuristic', '5 regimenes: TRENDING_UP/DOWN, SIDEWAYS, HIGH/LOW_VOL', 'regime-heuristic.ts'],
    ['Data Retention', 'Politicas de retencion por tipo, archival, cleanup', 'data-retention.ts'],
    ['Buy/Sell Pressure', 'Presion desde DEX swaps, SM vs retail, senales', 'buy-sell-pressure.ts'],
]
story.append(make_table(['Modulo', 'Funcion', 'Archivo'],
    risk_modules, [40*mm, 80*mm, 40*mm]))

story.append(heading2('3.5 Fase 5: Asignacion de Capital'))
story.append(body(
    'El Capital Allocation Engine implementa 16 metodos de asignacion, de los cuales 5 estan activos en v1: '
    'Kelly Modified (default single-strategy), Risk Parity (default multi-strategy), Volatility Targeting (regimen alta vol), '
    'Max Drawdown Control (DD>10%), y Equal Weight (fallback). El SDE selecciona automaticamente el metodo basandose '
    'en el estado del portfolio, regimen de mercado, y drawdown actual. Los 11 metodos restantes estan deprecados o '
    'planificados para v2. El Capital Strategy Manager gestiona el modo de operacion (ULTRA_CONSERVATIVE a CONCENTRATED) '
    'y mantiene estado de aprendizaje persistente entre reinicios.'
))

story.append(heading2('3.6 Fase 6: Ejecucion'))
story.append(body(
    'El Paper Trading Engine opera como simulador de trading con posiciones virtuales, tracking de PnL, y estadisticas '
    'completas (win rate, Sharpe, max drawdown). Soporta start/stop/pause y gestion de posiciones. El Trade Execution Engine '
    'proporciona controles pre-trade (risk checks), creacion de ordenes, routing DEX (Jupiter/1inch/ParaSwap), y limites '
    'diarios. El Autonomous Execution Engine procesa senales, crea ordenes, y gestiona posiciones de forma autonoma con '
    'integracion completa con kill switches y capital strategy.'
))

exec_modules = [
    ['Paper Trading Engine', 'Posiciones virtuales, PnL, stats, start/stop/pause', 'paper-trading-engine.ts'],
    ['Trade Execution Engine', 'Pre-trade risk checks, ordenes, DEX routing, limites diarios', 'trade-execution-engine.ts'],
    ['Autonomous Execution', 'Loop autonomo: senal->orden->posicion->portfolio', 'autonomous-execution-engine.ts'],
    ['Trade Executor Arch', 'Interfaces y contratos DEX, tipos de orden', 'trade-executor-arch.ts'],
]
story.append(make_table(['Modulo', 'Funcion', 'Archivo'],
    exec_modules, [40*mm, 80*mm, 40*mm]))

story.append(PageBreak())

# ════════════════════════════════════════════════════════════════
# 4. CATALOGO DE MODULOS
# ════════════════════════════════════════════════════════════════
story.append(heading1('4. Catalogo de Modulos'))
story.append(body(
    'El sistema contiene 89 archivos de servicio organizados en 6 directorios bajo src/lib/services/, mas 10 archivos '
    'de soporte en src/lib/ raiz. Se identifican aproximadamente 40 singletons activos que proporcionan funcionalidad '
    'a traves de toda la aplicacion. A continuacion se presenta el resumen por directorio.'
))

dir_summary = [
    ['services/brain/', '13', 'brainScheduler, brainCycleEngine, brainCapacityEngine, phaseStrategyEngine, candlestickPatternEngine, tokenLifecycleEngine, behavioralModelEngine, patternCompressionPipeline'],
    ['services/execution/', '9', 'tradeExecutionEngine, smartMoneyTracker, autonomousExecutionEngine, paperTradingEngine, botDetection'],
    ['services/strategy/', '16', 'strategyStateManager, deepAnalysisEngine, strategyEvolutionEngine, decisionEngine (TDE), strategyDecisionEngine (SDE), autoEvolutionLoop, regimeHeuristic, tradingSystemEngine'],
    ['services/risk/', '14', 'crossCorrelationEngine, strategyCorrelationService, monteCarloSimulator, killSwitchService, capitalAllocationEngine, capitalStrategyManager, alertEngine, operabilityScore'],
    ['services/data-sources/', '14', 'coinGeckoClient, dexScreenerClient, dexPaprikaClient, binanceClient, etherscanClient, ohlcvPipeline, realDataLoader'],
    ['services/backtesting/', '7', 'backtestingEngine, feedbackLoopEngine, backtestLoopEngine, walkForwardEngine, statisticalValidation'],
    ['services/shared/', '6', 'sharedClients, rateLimiter, requestSemaphore, universalExtractor, userDataFilter'],
    ['lib/ (root)', '10', 'db, wsBridge, unifiedCache, requestQueue, validations, format, startup'],
    ['TOTAL', '89', '~40 singletons activos'],
]
story.append(make_table(['Directorio', 'Archivos', 'Singletons Clave'],
    dir_summary, [35*mm, 18*mm, CONTENT_W - 53*mm]))

# Module dependency diagram
story.append(spacer(4*mm))
story.extend(img_centered(
    os.path.join(IMG_DIR, 'module-dependency-map.png'),
    max_w=CONTENT_W,
    caption_text='Figura 3: Mapa de dependencias entre modulos — Lineas gruesas = dependencias clave, finas = estandar, discontinuas = cross-group'
))

story.append(PageBreak())

# ════════════════════════════════════════════════════════════════
# 5. MAPA DE RUTAS API
# ════════════════════════════════════════════════════════════════
story.append(heading1('5. Mapa de Rutas API'))
story.append(body(
    'El sistema expone 112 endpoints API organizados en 11 categorias funcionales. Todas las rutas siguen el patron '
    'Next.js App Router bajo /src/app/api/. A continuacion se presenta el catalogo completo por categoria.'
))

# Insert API routes diagram
story.extend(img_centered(
    os.path.join(IMG_DIR, 'api-routes-map.png'),
    max_w=CONTENT_W,
    caption_text='Figura 4: Mapa de rutas API organizadas por categoria funcional'
))

api_categories = [
    ['Brain', '12', '/api/brain/{status, scheduler, init, pipeline, analyze, start-all, phase-signals, phase-strategy, loops, growth, capacity, backfill}'],
    ['Strategy Decision', '3', '/api/strategy-decision/{validate, portfolio-review, audit}'],
    ['Market', '11', '/api/market/{tokens, summary, ohlcv, multi-chain, context, stream, buy-sell-pressure, smart-money, pools, search, token/[addr]}'],
    ['Backtesting', '4', '/api/backtest{, /[id], /[id]/run, /walk-forward}'],
    ['Risk', '5', '/api/risk/{overview, controls, monte-carlo} + /api/{kill-switch, risk-budget}'],
    ['Execution', '7', '/api/execution{, /start, /history, /positions, /orders, /auto-exit, /auto-trade}'],
    ['Portfolio', '4', '/api/portfolio/{equity-curve, lifecycle, stats, risk-verification}'],
    ['Capital', '3', '/api/capital-allocation{, /dashboard, /pipeline}'],
    ['Trading Systems', '5', '/api/trading-systems{, /[id], /[id]/activate, /templates}'],
    ['Paper Trading', '3', '/api/paper-trading{, /trades, /positions}'],
    ['Other', '23+', '/api/{signals, predictive, patterns, traders, deep-analysis, alerts, webhooks, auto-sync, auto-evolution, strategy-optimizer, strategy-evolution, strategy-states, export/*, import, data-monitor, data-quality, extractor, tokens, decisions, regime, templates, ohlcv/backfill, cross-correlation, health, seed, user-events}'],
]
story.append(make_table(['Categoria', 'Count', 'Rutas'],
    api_categories, [30*mm, 14*mm, CONTENT_W - 44*mm]))

story.append(PageBreak())

# ════════════════════════════════════════════════════════════════
# 6. ESQUEMA DE BASE DE DATOS
# ════════════════════════════════════════════════════════════════
story.append(heading1('6. Esquema de Base de Datos'))
story.append(body(
    'El sistema utiliza SQLite como base de datos a traves de Prisma 7. El esquema contiene 38 modelos con 22 tablas '
    'mapeadas explicitamente via @@map. No se utilizan enums formales de Prisma — todos los valores tipo-enum son Strings '
    'con valores documentados en comentarios. La base de datos es altamente JSON-heavy, con 40+ campos almacenando JSON '
    'strings (SQLite no tiene tipo nativo JSON). A continuacion se presentan los modelos organizados por dominio funcional.'
))

story.append(heading3('Modelos por Dominio'))
db_domains = [
    ['Usuario y Auth', '2', 'User (8 campos), rol USER/ADMIN'],
    ['Tokens (Multi-chain)', '2', 'Token (20+ campos), PriceCandle (OHLCV + source + unique composite)'],
    ['Trader Intelligence', '5', 'Trader (55+ campos, 8 secciones), TraderTransaction, WalletTokenHolding, TraderBehaviorPattern (22 tipos), CrossChainWallet (7 link types)'],
    ['Senales y Analisis', '5', 'Signal, UserEvent, PatternRule, TokenDNA (1:1 con Token), PredictiveSignal (11 tipos)'],
    ['Trading Systems Lab', '3', 'TradingSystem (5-layer config, self-ref parentSystemId), BacktestRun (anti-overfitting fields), BacktestOperation'],
    ['Brain Cycle (24/7)', '4', 'BrainCycleRun, OperabilitySnapshot (6 sub-scores), CompoundGrowthTracker, TradingCycle'],
    ['Behavioral/Learning', '3', 'TraderBehaviorModel (unique archetype+phase+action), FeedbackMetrics, SystemEvolution'],
    ['Paper Trading', '3', 'PaperTradingSession, PaperTradingPosition, PaperTradingTrade'],
    ['Alerts & Notif', '3', 'AlertRule (6 categorias, 3 severidades), Alert, WebhookConfig'],
    ['Risk Controls', '3', 'RiskBudget (7 limites, 3 perfiles), RiskControlsConfig, DecisionAudit'],
    ['Estrategia/Evolucion', '3', 'StrategyStateHistory (7 estados), EvolutionCycle (6 fases), AIBestStrategy (Hall of Fame)'],
    ['Extraccion/Jobs', '4', 'ExtractionJob, DataRetentionPolicy, ApiRateLimit, DecisionLog'],
    ['Otros', '3', 'SchedulerState, ProtocolData, StrategyTemplate (marketplace)'],
]
story.append(make_table(['Dominio', 'Count', 'Modelos Clave'],
    db_domains, [32*mm, 14*mm, CONTENT_W - 46*mm]))

story.append(heading3('Relaciones Principales'))
story.append(body(
    '<b>User -></b> TradingSystem[], BacktestRun[], PaperTradingSession[], PaperTradingPosition[], AlertRule[], Alert[], WebhookConfig[], StrategyTemplate[]<br/>'
    '<b>Token -></b> TokenDNA? (1:1), Signal[], PriceCandle[], TokenLifecycleState[]<br/>'
    '<b>Trader -></b> TraderTransaction[], WalletTokenHolding[], TraderBehaviorPattern[], CrossChainWallet[] (x2: Primary+Linked), TraderLabelAssignment[]<br/>'
    '<b>TradingSystem -></b> BacktestRun[], BacktestOperation[], TradingSystem[] (self-ref: parentSystemId -> derivedSystems)<br/>'
    '<b>BacktestRun -></b> BacktestOperation[]<br/>'
    '<b>DecisionAudit -></b> strategyId es String plano (NO tiene @relation formal a TradingSystem — discrepancia con el doc de arquitectura)'
))

story.append(heading3('Observaciones del Schema'))
story.append(bullet('<b>No hay Enums Prisma</b>: Todos los valores tipo-enum son Strings. Reduce type safety pero facilita migracion.'))
story.append(bullet('<b>userId nullable</b>: Muchos modelos tienen userId String? "para backward compat" — indica transicion de single-user a multi-user.'))
story.append(bullet('<b>40+ campos JSON</b>: SQLite no tiene tipo nativo JSON. Parsing/validation ocurre en la capa de aplicacion.'))
story.append(bullet('<b>DecisionAudit.strategyId sin FK formal</b>: El doc de arquitectura especifica relacion con TradingSystem, pero el schema Prisma usa String plano.'))
story.append(bullet('<b>Dos modelos de operability</b>: OperabilityScore y OperabilitySnapshot coexisten con solapamiento.'))
story.append(bullet('<b>No hay migraciones formales</b>: Se usa prisma db push (prototyping), no prisma migrate dev.'))

story.append(PageBreak())

# ════════════════════════════════════════════════════════════════
# 7. INTEGRACIONES EXTERNAS
# ════════════════════════════════════════════════════════════════
story.append(heading1('7. Integraciones Externas'))
story.append(body(
    'El sistema se integra con 13+ fuentes de datos externas, cada una con su propio cliente especializado, rate limiting, '
    'y cache. Las integraciones se organizan en 3 categorias: fuentes de datos de mercado (precios, volumenes, OHLCV), '
    'fuentes on-chain (transacciones, wallets, smart money), y fuentes de protocolos DeFi (TVL, yields). Ademas, el sistema '
    'utiliza z-ai-web-dev-sdk para LLM integration en el Deep Analysis Engine, y Socket.IO para comunicacion real-time '
    'entre backend y frontend.'
))

ext_integrations = [
    ['CoinGecko', 'Mercado', 'Gratuita', 'Tokens, OHLCV, global data, trending', 'Si (60s-5min)'],
    ['DexScreener', 'Mercado', 'Gratuita', 'DEX pairs, liquidez, trending', 'Si (30s-2min)'],
    ['DexPaprika', 'Mercado', 'Gratuita', '20+ chains, pools, swaps, OHLCV, SM swaps', 'Si (60s-5min)'],
    ['Binance', 'Mercado', 'Gratuita', 'OHLCV candles, tickers, symbol info', 'Si (15s-1min)'],
    ['Etherscan', 'On-chain', 'API Key', 'Token transfers, trader discovery', 'Si (rate-limited)'],
    ['Footprint', 'Mercado', 'Gratuita', 'Token prices, protocol TVL, chain overview', 'Si (5min)'],
    ['SQD/Subsquid', 'On-chain', 'Gratuita', 'Indexer blockchain, eventos historicos', 'On-demand'],
    ['Helius', 'On-chain', 'Gratuita', 'Transacciones Solana', 'Si (2min)'],
    ['Moralis', 'On-chain', 'Gratuita', 'Multi-chain wallets, balances', 'Si (5min)'],
    ['DefiLlama', 'DeFi', 'Gratuita', 'Protocol TVL, yields, chain overview', 'Si (10min)'],
    ['CryptoDataDownload', 'Historico', 'Gratuita', 'CSV historical data', 'On-demand'],
    ['Dune', 'On-chain', 'Gratuita', 'SQL queries on blockchain data', 'On-demand'],
    ['z-ai-web-dev-sdk', 'LLM', 'SDK interno', 'Chat completions para Deep Analysis', 'N/A'],
    ['Socket.IO', 'Real-time', 'Interno', 'WS bridge backend->frontend (puerto 3010)', 'N/A'],
]
story.append(make_table(['Fuente', 'Tipo', 'Auth', 'Datos', 'Cache'],
    ext_integrations, [28*mm, 16*mm, 18*mm, 65*mm, 22*mm]))

story.append(heading3('Cascada OHLCV'))
story.append(body(
    'El OHLCV Pipeline implementa una cascada de 3 niveles para maximizar la cobertura de datos: '
    '<b>Nivel 1 (Binance)</b>: Mayor cobertura, datos mas precisos, intervalos de 1m a 1M. Se usa como fuente primaria '
    'para tokens listados en Binance. '
    '<b>Nivel 2 (CoinGecko)</b>: Cobertura amplia de tokens CEX, OHLCV con timeframes limitados. Fallback cuando Binance '
    'no tiene el token. '
    '<b>Nivel 3 (DexPaprika)</b>: Tokens DEX no listados en CEX, cobertura de 20+ chains. Ultimo recurso para tokens '
    'esotericos o nuevos. '
    'El pipeline ademas agrega timeframes superiores automaticamente (1m->5m->15m->1h->4h->1d) y maneja backfill '
    'historico con deteccion de gaps.'
))

# ════════════════════════════════════════════════════════════════
# 8. PIPELINE DEL SDE
# ════════════════════════════════════════════════════════════════
story.append(heading1('8. Pipeline del Strategy Decision Engine (SDE)'))
story.append(body(
    'El SDE es el componente mas critico del sistema. Transforma datos de multiples fuentes (backtest, Monte Carlo, '
    'walk-forward, operability, paper trading, regime) en una decision accionable con 3 dimensiones. Implementa el '
    'principio de "vetos antes que scores": un veto duro SIEMPRE tiene prioridad sobre un score alto, sin excepciones. '
    'El pipeline es deterministicamente reproducible — mismos inputs producen siempre el mismo output.'
))

story.append(heading3('Entrada: SDEInput'))
sde_inputs = [
    ['backtest', 'BacktestSnapshot', 'totalTrades, winRate, avgWinPct, avgLossPct, maxDrawdownPct, sharpeRatio, payoffRatio, overfittingScore, parameterStability'],
    ['monteCarlo', 'MonteCarloSnapshot', 'riskOfRuin, probabilityOfProfit, p95MaxDrawdown, meanFinalEquity, simulationsCount'],
    ['walkForward', 'WalkForwardSnapshot', 'aggregateWFE, isRobust, parameterStability, overallDegradation, performanceConsistency, windowCount'],
    ['operability', 'OperabilitySnapshot', 'overallScore, level, isOperable, recommendedPositionUsd, minimumGainPct, feeEstimateTotalCostPct'],
    ['paperTrading', 'PaperTradingSnapshot?', 'totalTrades, winRate, unrealizedPnlPct, currentDrawdownPct, daysActive, sharpeRatio'],
    ['portfolioState', 'Object', 'totalCapitalUsd, currentDrawdownPct, activeStrategies, marketVolatility, marketRegime'],
    ['regimeAssessment', 'Object?', 'regime, confidence, volatilityPercentile, trendDirection, trendStrength'],
]
story.append(make_table(['Campo', 'Tipo', 'Datos Clave'],
    sde_inputs, [25*mm, 30*mm, CONTENT_W - 55*mm]))

story.append(heading3('Salida: StrategyDecision'))
sde_outputs = [
    ['state', 'StrategyState', 'ACTIVE | CONDITIONAL | PAUSED | REJECTED'],
    ['capitalAction', 'CapitalAction', 'INCREASE | MAINTAIN | REDUCE | EXIT'],
    ['signalQuality', 'SignalQuality', 'STRONG | ADEQUATE | WEAK'],
    ['scores', 'CompositeScores', 'robustness (0-100), overfitting (0-100, lower=better), stability (0-100)'],
    ['vetoResults', 'VetoResult[]', '5 vetos con passed/value/threshold/reason'],
    ['capitalRecommendation', 'CapitalRecommendation', 'targetPct, sizeUsd, method, reason'],
    ['recommendations', 'string[]', 'Sugerencias adicionales contextuales'],
    ['auditId', 'string', 'Referencia al registro de auditoria completo'],
]
story.append(make_table(['Campo', 'Tipo', 'Descripcion'],
    sde_outputs, [30*mm, 30*mm, CONTENT_W - 60*mm]))

story.append(heading3('Pesos Fijos v1'))
weights = [
    ['Robustness', 'WFE x 0.35 + probOfProfit x 0.30 + paramStability x 0.35'],
    ['Overfitting', 'degradation x 0.45 + WFE_variance x 0.30 + tradeCountPenalty x 0.25'],
    ['Stability', 'paramStability x 0.40 + OOS_winRate_std x 0.30 + regimeConsistency x 0.30'],
]
story.append(make_table(['Score', 'Formula'], weights, [30*mm, CONTENT_W - 30*mm]))

story.append(heading3('Umbrales de Veto por Perfil de Riesgo'))
veto_thresholds = [
    ['MIN_TRADES', '50', '50', '50'],
    ['MAX_RISK_OF_RUIN', '1%', '3%', '5%'],
    ['MAX_DRAWDOWN', '30%', '40%', '50%'],
    ['MIN_WFE', '40%', '30%', '25%'],
    ['MIN_WIN_RATE (w/ low payoff)', '40%', '35%', '30%'],
]
story.append(make_table(['Veto', 'CONSERVATIVE', 'MODERATE', 'AGGRESSIVE'],
    veto_thresholds, [45*mm, 30*mm, 30*mm, 30*mm]))

story.append(PageBreak())

# ════════════════════════════════════════════════════════════════
# 9. KILL SWITCHES
# ════════════════════════════════════════════════════════════════
story.append(heading1('9. Kill Switches y Gestion de Riesgo'))
story.append(body(
    'El Kill Switch Service implementa 7 niveles de proteccion de capital, cada uno con umbrales configurables cargados '
    'desde la tabla RiskBudget de la base de datos. Cuando un kill switch se activa, se genera automaticamente una alerta '
    'via el Alert Engine y se propaga via WS Bridge al frontend. Los kill switches operan con histeresis del 80% para '
    'evitar activacion/desactivacion rapida — un kill switch activado por DD>20% solo se desactiva cuando DD baja del 16%.'
))

kill_switches = [
    ['Portfolio DD', 'maxPortfolioDrawdownPct', '20%', 'PAUSE_ALL', 'Auto-pause todo el trading'],
    ['Strategy DD', 'maxStrategyDrawdownPct', '30%', 'PAUSE_STRATEGY', 'Auto-pause estrategia especifica'],
    ['Position Loss', 'maxPositionLossPct', '50%', 'CLOSE_POSITION', 'Auto-close posicion con perdida excesiva'],
    ['Token Concentration', 'maxConcentrationPct', '15%', 'REJECT_POSITION', 'Rechazar trade que exceda concentracion'],
    ['Chain Concentration', 'maxChainPct', '50%', 'REJECT_POSITION', 'Rechazar trade por concentracion de cadena'],
    ['Sector Concentration', 'maxSectorPct', '30%', 'REJECT_POSITION', 'Rechazar trade por concentracion sectorial'],
    ['Emergency Global Pause', 'N/A (manual)', 'N/A', 'PAUSE_ALL', 'Pause manual de emergencia'],
]
story.append(make_table(['Kill Switch', 'Campo RiskBudget', 'Default', 'Accion', 'Descripcion'],
    kill_switches, [28*mm, 30*mm, 16*mm, 25*mm, CONTENT_W - 99*mm]))

story.append(heading3('Flujo de canOpenPosition()'))
story.append(body(
    'Antes de abrir cualquier posicion, el sistema ejecuta 7 comprobaciones secuenciales: '
    '(1) Global manual pause activo? -> Rechazar. '
    '(2) Per-strategy manual pause? -> Rechazar. '
    '(3) Portfolio DD kill switch activo? -> Rechazar. '
    '(4) Strategy DD kill switch activo? -> Rechazar. '
    '(5) Token concentration excederia limite? -> Rechazar. '
    '(6) Chain concentration excederia limite? -> Rechazar. '
    '(7) Sector concentration excederia limite? -> Rechazar. '
    'Solo si todas pasan, se permite la apertura. Este flujo garantiza que ninguna posicion se abra sin verificar '
    'todos los limites de riesgo configurados.'
))

# ════════════════════════════════════════════════════════════════
# 10. CAPITAL ALLOCATION PIPELINE
# ════════════════════════════════════════════════════════════════
story.append(heading1('10. Capital Allocation Pipeline'))
story.append(body(
    'El pipeline de asignacion de capital conecta el SDE con el Paper Trading Engine a traves del Capital Allocation Engine. '
    'El flujo es: SDE produce StrategyDecision con capitalRecommendation -> CapitalAllocationEngine calcula tamanos de posicion '
    'usando el metodo seleccionado -> Strategy Correlation Service verifica que no se excedan limites de correlacion -> '
    'Kill Switch Service verifica limites de concentracion -> Paper Trading Engine ejecuta con el tamano aprobado.'
))

story.append(heading3('5 Metodos Activos en v1'))
alloc_methods = [
    ['KELLY_MODIFIED', 'Default single-strategy', 'Half-Kelly: f* = (p*b - q) / b', 'Maximiza crecimiento a largo plazo con riesgo controlado'],
    ['RISK_PARITY', 'Default multi-strategy (2+)', 'w_i proporcional a 1/sigma_i', 'Cada estrategia contribuye igual riesgo'],
    ['VOLATILITY_TARGETING', 'Alta vol (regimen/percentil 75)', 'size = baseSize x (targetVol / realizedVol)', 'Reduce exposicion cuando vol sube'],
    ['MAX_DRAWDOWN_CONTROL', 'DD > 10% o REDUCE action', 'size = baseSize x (1 - currentDD/maxDD)', 'Protege capital durante rachas negativas'],
    ['EQUAL_WEIGHT', 'Fallback/benchmark', 'size = capital / numAssets', 'Simple, predecible, punto de referencia'],
]
story.append(make_table(['Metodo', 'Cuando se usa', 'Formula', 'Rationale'],
    alloc_methods, [30*mm, 30*mm, 50*mm, CONTENT_W - 110*mm]))

story.append(heading3('Seleccion Automatica de Metodo'))
story.append(body(
    'El SDE selecciona automaticamente el metodo de asignacion basandose en el contexto del portfolio: '
    '(1) Si DD > 10% o capitalAction = REDUCE -> MAX_DRAWDOWN_CONTROL. '
    '(2) Si avg pairwise correlation > 0.5 -> RISK_PARITY. '
    '(3) Si regimen = HIGH_VOLATILITY -> VOLATILITY_TARGETING. '
    '(4) Si regimen = TRENDING_DOWN y DD > 5% -> MAX_DRAWDOWN_CONTROL. '
    '(5) Si marketVolatility > percentil 75 -> VOLATILITY_TARGETING. '
    '(6) Si activeStrategies >= 2 -> RISK_PARITY. '
    '(7) Default -> KELLY_MODIFIED (single strategy, buenas condiciones). '
    'Despues del calculo, se aplica un hard constraint de concentracion max 15% por estrategia.'
))

story.append(PageBreak())

# ════════════════════════════════════════════════════════════════
# 11. ESTADO DE LAS CONEXIONES CRITICAS
# ════════════════════════════════════════════════════════════════
story.append(heading1('11. Estado de las Conexiones Criticas'))
story.append(body(
    'Esta seccion documenta el estado real de las conexiones entre modulos, identificando cuales estan operativas, '
    'cuales son parciales, y cuales faltan completamente. Este analisis es fundamental para la Fase 4 (Auditoria de '
    'Integraciones) y la Fase 6 (Correcciones).'
))

connections = [
    ['OHLCV Pipeline -> Brain Orchestrator', 'OPERATIVA', 'El Brain importa y usa ohlcvPipeline directamente'],
    ['Brain Scheduler -> Brain Cycle Engine', 'OPERATIVA', 'Lazy import, loop SCAN->FILTER->MATCH->STORE'],
    ['Brain Cycle -> Feedback Loop Engine', 'PARCIAL', 'Importado pero NUNCA llamado (feedbackLoopEngine no se invoca en brainCycleEngine)'],
    ['Backtest Engine -> SDE', 'OPERATIVA', 'SDE lee BacktestSnapshot de backtest results via buildInputFromStrategyId'],
    ['Monte Carlo -> SDE', 'PARCIAL', 'SDE acepta MonteCarloSnapshot pero buildInputFromStrategyId genera PLACEHOLDER (no corre MC real)'],
    ['Walk-Forward -> SDE', 'PARCIAL', 'Igual que MC: datos placeholder si no se corre WF explicitamente'],
    ['SDE -> Paper Trading', 'NO CONECTADA', 'PTE tiene su propia logica de decision inline, no usa SDE'],
    ['SDE -> Capital Allocation', 'OPERATIVA', 'SDE llama capitalAllocationEngine.calculate() en Step 5'],
    ['Capital Allocation -> Paper Trading', 'NO CONECTADA', 'PTE usa calculatePositionSize() propio (equal-split), no usa CapitalAllocationEngine'],
    ['Kill Switch -> Paper Trading', 'OPERATIVA', 'PTE verifica kill switches antes de abrir posiciones'],
    ['Kill Switch -> Trade Execution', 'OPERATIVA', 'Trade Execution verifica kill switches en pre-trade checks'],
    ['Alert Engine -> WS Bridge', 'OPERATIVA', 'Alert Engine llama wsBridge.pushAlert() para notificaciones real-time'],
    ['Auto-Evolution Loop -> SDE', 'NO CONECTADA', 'Auto-evolution activa estrategias por minSharpeRatio, no pasa por SDE'],
    ['Feedback Loop -> Auto-Evolution', 'OPERATIVA', 'Auto-evolution importa feedbackLoopEngine para evaluacion'],
    ['Strategy Correlation -> SDE', 'OPERATIVA', 'SDE llama strategyCorrelationService para verificacion de correlacion'],
    ['Regime Heuristic -> SDE', 'PARCIAL', 'SDE acepta regimeAssessment opcional, pero no lo genera automaticamente'],
    ['Paper Trading -> SDE (feedback)', 'NO CONECTADA', 'No hay mecanismo automatico para feedear resultados de PTE al SDE'],
    ['TDE <-> SDE', 'NO INTEGRADOS', 'Token Decision Engine y Strategy Decision Engine coexisten sin integracion'],
    ['Risk Controls Verifier -> Paper Trading', 'HARDCODED', 'RiskControlsVerifier usa valores hardcoded, no hace analisis real'],
]
story.append(make_table(['Conexion', 'Estado', 'Detalle'],
    connections, [45*mm, 20*mm, CONTENT_W - 65*mm]))

story.append(heading3('Resumen de Estado de Conexiones'))
conn_summary = [
    ['OPERATIVAS', '9', 'Conexiones funcionando correctamente'],
    ['PARCIALES', '4', 'Conectadas pero con datos placeholder o sin uso completo'],
    ['NO CONECTADAS', '5', 'Modulos que deberian estar conectados pero no lo estan'],
    ['HARDCODED', '1', 'Existe pero usa logica hardcoded en vez de analisis real'],
]
story.append(make_table(['Estado', 'Count', 'Descripcion'],
    conn_summary, [30*mm, 14*mm, CONTENT_W - 44*mm]))

story.append(PageBreak())

# ════════════════════════════════════════════════════════════════
# 12. HALLAZGOS Y OBSERVACIONES
# ════════════════════════════════════════════════════════════════
story.append(heading1('12. Hallazgos y Observaciones'))
story.append(body(
    'A continuacion se presentan los hallazgos mas significativos del mapeo completo del sistema. Estos hallazgos '
    'seran la base para las fases posteriores de la auditoria. <b>No se proponen correcciones en esta fase</b> — '
    'las correcciones se realizaran exclusivamente en la Fase 6.'
))

story.append(heading2('12.1 Gaps Arquitectonicos Criticos (confirmados)'))
story.append(body(
    'Se confirman 6 gaps arquitectonicos previamente identificados que representan las desconexiones mas importantes '
    'entre el diseno del sistema (ARCHITECTURE_FINAL.md) y la implementacion real:'
))

gaps = [
    ['P0', 'Auto-feedback loop (S1.14)', 'feedbackLoopEngine importado pero nunca llamado en brainCycleEngine. El loop SCAN->...->FEEDBACK no ejecuta el paso FEEDBACK.'],
    ['P0', 'SDE->PTE full integration (S1.13)', 'Paper Trading Engine tiene logica de decision inline propia, no usa SDE para decidir que estrategias operar.'],
    ['P1', 'maxDailyVaR en RiskBudget', 'Ausente del schema Prisma y del servicio. El doc de arquitectura lo menciona pero no esta implementado.'],
    ['P1', 'Alert escalation chain (S2.8)', 'No hay cadena INFO->WARNING->CRITICAL->AUTO_PAUSE. Las alertas se generan pero no escalan automaticamente.'],
    ['P1', 'TDE<->SDE integration', 'Token Decision Engine y Strategy Decision Engine coexisten sin integracion. TDE decide por token, SDE por estrategia, sin comunicacion.'],
    ['P2', 'RiskControlsVerifier real analysis', 'RiskControlsVerifier usa valores hardcoded en vez de hacer analisis real de riesgo.'],
]
story.append(make_table(['Prioridad', 'Gap', 'Descripcion'],
    gaps, [14*mm, 40*mm, CONTENT_W - 54*mm]))

story.append(heading2('12.2 Observaciones de Data Flow'))
story.append(bullet('<b>MC/WF datos placeholder</b>: Cuando buildInputFromStrategyId() no encuentra resultados reales de Monte Carlo o Walk-Forward, genera datos placeholder (fabricados). El SDE marca esto como dataQuality=PLACEHOLDER y aplica logica conservadora (max CONDITIONAL + MAINTAIN), pero el usuario podria no ser consciente de que las decisiones se basan en datos fabricados.'))
story.append(bullet('<b>Capital Allocation huerfano</b>: El Capital Allocation Engine tiene 16 metodos implementados pero solo el SDE lo llama (para calcular capitalRecommendation). El Paper Trading Engine usa su propio calculatePositionSize() que divide capital equitativamente entre posiciones, ignorando completamente el motor de allocation.'))
story.append(bullet('<b>Regime no conectado automaticamente</b>: El RegimeHeuristic existe y funciona, pero el SDE solo lo usa si alguien le pasa regimeAssessment en el input. No hay mecanismo automatico que ejecute el regimen heuristic y alimente su resultado al SDE.'))
story.append(bullet('<b>Feedback loop roto</b>: El SDE tiene provideFeedback() para actualizar audit records con resultados, pero nada lo llama automaticamente. Cuando paper trading cierra una posicion con PnL, nadie feedea ese resultado al SDE para reevaluacion.'))

story.append(heading2('12.3 Observaciones de Schema'))
story.append(bullet('<b>DecisionAudit sin FK formal</b>: strategyId es String plano sin @relation a TradingSystem. El doc de arquitectura especifica una relacion formal, pero el schema no la implementa.'))
story.append(bullet('<b>Dos modelos de operability</b>: OperabilityScore y OperabilitySnapshot tienen campos solapados (ambos calculan score, nivel, isOperable). No esta claro cual es la fuente de verdad.'))
story.append(bullet('<b>userId nullable extendido</b>: Muchos modelos tienen userId String? "para backward compat". Esto sugiere que el sistema fue disenado inicialmente como single-user y se esta migrando a multi-user, pero la migracion no esta completa.'))

story.append(heading2('12.4 Observaciones de UI'))
story.append(bullet('<b>19 pestanas activas</b>: La UI tiene 19 tabs organizados en 5 grupos (Market, Intelligence, Strategy, Risk & Portfolio, Tools). Cada tab es un componente complejo con multiples sub-componentes.'))
story.append(bullet('<b>Zustand store con 26 slices</b>: useCryptoStore gestiona 26 slices de estado, desde tokens y senales hasta filtros y configuracion. El store se actualiza via WS y REST polling.'))
story.append(bullet('<b>Socket.IO real-time</b>: WebSocketProvider en el frontend se conecta al servidor Socket.IO en puerto 3010 con fallback a REST polling. SimulationProvider genera datos simulados cuando WS no esta disponible.'))

story.append(heading2('12.5 Observaciones de Configuracion'))
story.append(bullet('<b>Next.js 16 con ignoreBuildErrors</b>: typescript.ignoreBuildErrors=true en next.config.ts. El build compila pero podria ocultar errores de tipos.'))
story.append(bullet('<b>noImplicitAny: false</b>: TypeScript configurado sin verificacion implicita de any. Reduce seguridad de tipos.'))
story.append(bullet('<b>reactStrictMode: false</b>: Modo estricto de React desactivado. Podria ocultar bugs de efectos.'))
story.append(bullet('<b>Auth deshabilitado</b>: AUTH_ENABLED=false por defecto. El sistema usa un demo user automatico via getCurrentUserId().'))

story.append(spacer(10*mm))
story.append(hr())
story.append(Paragraph(
    '<i>Fin de la Fase 1: Auditoria de Flujo Completo. '
    'No se han realizado modificaciones al codigo. '
    'Proxima fase: Fase 2 — Comparacion con el documento de reestructuracion.</i>',
    ParagraphStyle('Footer', parent=s_body, fontSize=9, textColor=C_TEXT_MUTED, alignment=TA_CENTER)
))

# ── Build ──
doc.build(story)
print(f'PDF generado: {OUTPUT}')
print(f'Tamano: {os.path.getsize(OUTPUT) / 1024:.0f} KB')
