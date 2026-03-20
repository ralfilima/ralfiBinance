"""
config.py - Configuracoes do DCA Bot Inteligente.

Filosofia: Entrar em 5 moedas simultaneamente com posicoes pequenas.
Quando uma cai, fazer DCA (Dollar Cost Averaging) para baixar o preco medio.
Stop Profit GLOBAL: quando a soma das 5 moedas atinge o target, fecha TUDO.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ============================================
# CREDENCIAIS
# ============================================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
BINANCE_TESTNET_API_KEY = os.getenv("BINANCE_TESTNET_API_KEY", "")
BINANCE_TESTNET_API_SECRET = os.getenv("BINANCE_TESTNET_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
USE_TESTNET = os.getenv("USE_TESTNET", "true").lower() == "true"

# ============================================
# PORTFOLIO
# ============================================
NUM_COINS = 5                     # Sempre 5 moedas simultaneas
LEVERAGE = 10                     # Alavancagem
TIMEFRAME = "5m"                  # Timeframe para analise (5m para DCA rapido)
TIMEFRAME_TREND = "1h"            # Timeframe para tendencia

# ============================================
# DCA (Dollar Cost Averaging)
# ============================================
# Capital alocado por moeda (% do saldo total)
CAPITAL_PER_COIN_PCT = 0.15       # 15% do saldo por moeda (5 x 15% = 75%, 25% reserva)

# Entrada inicial: % do capital alocado para a moeda
INITIAL_ENTRY_PCT = 0.25          # 25% do capital da moeda na 1a entrada

# DCA Levels: quando o preco cai X%, adicionar Y% do capital restante
# (pct_queda, pct_capital_restante)
DCA_LEVELS = [
    (0.005, 0.20),   # Nivel 1: -0.5% -> adiciona 20% do restante
    (0.010, 0.25),   # Nivel 2: -1.0% -> adiciona 25% do restante
    (0.020, 0.30),   # Nivel 3: -2.0% -> adiciona 30% do restante
    (0.035, 0.50),   # Nivel 4: -3.5% -> adiciona 50% do restante
    (0.050, 1.00),   # Nivel 5: -5.0% -> usa TODO o restante (all-in)
]

MAX_DCA_ORDERS = 5                # Maximo de DCAs por moeda

# ============================================
# STOP PROFIT GLOBAL
# ============================================
# O bot fecha TODAS as posicoes quando o lucro GLOBAL atinge o target
GLOBAL_TAKE_PROFIT_PCT = 0.005    # +0.5% do capital total = fecha tudo
GLOBAL_TAKE_PROFIT_USDT = 0.0     # Ou valor fixo em USDT (0 = usar %)

# Stop Loss de emergencia GLOBAL
GLOBAL_STOP_LOSS_PCT = 0.03       # -3% do capital = fecha tudo (emergencia)

# ============================================
# SELECAO INTELIGENTE DE MOEDAS
# ============================================
MIN_VOLUME_24H = 10_000_000      # Volume minimo 24h em USDT
MIN_PRICE = 0.01                  # Preco minimo em USDT
TOP_CANDIDATES = 30               # Quantas moedas analisar
RSI_PERIOD = 14
RSI_OVERSOLD = 35                 # RSI < 35 = sobrevendido (bom para LONG DCA)
RSI_OVERBOUGHT = 65               # RSI > 65 = sobrecomprado (bom para SHORT DCA)
EMA_FAST = 9
EMA_SLOW = 21
BB_PERIOD = 20
BB_STD = 2.0
ATR_PERIOD = 14

# ============================================
# FILTROS DE QUALIDADE
# ============================================
MIN_ATR_PCT = 0.002               # ATR minimo 0.2% (precisa de volatilidade para DCA funcionar)
MAX_ATR_PCT = 0.08                # ATR maximo 8% (muito volatil = perigoso)
MAX_SPREAD_PCT = 0.002            # Spread maximo 0.2%

# ============================================
# INTERVALOS
# ============================================
MONITOR_INTERVAL = 5              # Monitorar posicoes a cada 5s
DCA_CHECK_INTERVAL = 10           # Verificar DCA a cada 10s
DASHBOARD_INTERVAL = 3            # Atualizar dashboard a cada 3s
SELECTION_INTERVAL = 300          # Re-selecionar moedas a cada 5 min (quando livre)

# ============================================
# CIRCUIT BREAKER
# ============================================
CB_FAILURE_THRESHOLD = 10
CB_RECOVERY_TIMEOUT = 120
CB_HALF_OPEN_MAX_CALLS = 3
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY = 30.0

# ============================================
# CAMINHOS
# ============================================
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def get_api_keys(use_testnet=None):
    if use_testnet is None:
        use_testnet = USE_TESTNET
    if use_testnet:
        return BINANCE_TESTNET_API_KEY, BINANCE_TESTNET_API_SECRET
    return BINANCE_API_KEY, BINANCE_API_SECRET


def validate_config(use_testnet=None):
    if use_testnet is None:
        use_testnet = USE_TESTNET
    errors = []
    key, secret = get_api_keys(use_testnet)
    mode = "TESTNET" if use_testnet else "REAL"
    if not key or key.startswith("sua_"):
        errors.append(f"API Key ({mode}) nao configurada no .env")
    if not secret or secret.startswith("sua_"):
        errors.append(f"API Secret ({mode}) nao configurado no .env")
    return errors
