"""
config.py - Carregamento e validacao de configuracoes do bot.
Todas as credenciais sao lidas de variaveis de ambiente (.env).

v3.0 - Melhorias:
  - Trailing stop mais conservador (1.0% ativacao, 0.5% callback)
  - Time stop mais longo (45-120 min)
  - Monitor a cada 10s para SL/TP por software
  - Cooldown de 5 min apos perdas consecutivas
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

# ============================================
# CREDENCIAIS (nunca hardcoded)
# ============================================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
BINANCE_TESTNET_API_KEY = os.getenv("BINANCE_TESTNET_API_KEY", "")
BINANCE_TESTNET_API_SECRET = os.getenv("BINANCE_TESTNET_API_SECRET", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Modo de operacao (testnet ou real)
USE_TESTNET = os.getenv("USE_TESTNET", "true").lower() == "true"

# ============================================
# PARAMETROS DA ESTRATEGIA
# ============================================
# Timeframe dos candles
TIMEFRAME = "5m"
TIMEFRAME_BACKTEST = "1h"

# EMAs
EMA_FAST = 9
EMA_SLOW = 21
EMA_TREND = 200  # EMA do BTC para tendencia geral

# RSI
RSI_PERIOD = 14
RSI_LONG_MIN = 50
RSI_LONG_MAX = 70
RSI_SHORT_MIN = 30
RSI_SHORT_MAX = 50

# Bandas de Bollinger
BB_PERIOD = 20
BB_STD = 2

# ATR para position sizing
ATR_PERIOD = 14
ATR_MULTIPLIER = 2.0

# ============================================
# SELECAO DE ATIVOS
# ============================================
TOP_VOLUME_COUNT = 15          # Top moedas por volume
TOP_GAINERS_COUNT = 5          # Top valorizadoras
PERSISTENCE_CHECKS = 3         # Verificacoes consecutivas
PERSISTENCE_INTERVAL = 20      # Segundos entre verificacoes
CORRELATION_THRESHOLD = 0.85   # Limite de correlacao Pearson
MAX_PORTFOLIO_SIZE = 5         # Maximo de moedas no portfolio

# ============================================
# GESTAO DE RISCO
# ============================================
RISK_PER_TRADE = 0.01          # 1% do capital por trade
MAX_OPEN_POSITIONS = 5
MAX_DAILY_LOSS_PERCENT = 0.03  # 3% perda maxima diaria
MAX_CONSECUTIVE_LOSSES = 3
MAX_DRAWDOWN_PERCENT = 0.10    # 10% drawdown maximo global
LEVERAGE = 10                  # Alavancagem padrao

# Trailing Stop (v3.0 - mais conservador)
TRAILING_ACTIVATION = 0.01     # Ativar trailing com +1.0% de lucro (era 0.5%)
TRAILING_CALLBACK = 0.005      # Trailing de 0.5% (era 0.3%)

# Time Stop (minutos) - mais longo para dar tempo ao trade
TIME_STOP_MIN = 45             # Minimo 45 min (era 30)
TIME_STOP_MAX = 120            # Maximo 120 min (era 90)

# Auto-resume apos perdas consecutivas (segundos)
CONSECUTIVE_LOSS_COOLDOWN = 300  # 5 minutos

# ============================================
# INTERVALOS DE EXECUCAO
# ============================================
ENTRY_INTERVAL_SECONDS = 60    # Intervalo entre buscas de entrada
ENTRY_JITTER_SECONDS = 15      # Jitter aleatorio
MONITOR_INTERVAL_SECONDS = 10  # Intervalo de monitoramento (10s para SL/TP software)
DASHBOARD_REFRESH_SECONDS = 5  # Atualizacao do dashboard
SELECTION_INTERVAL_SECONDS = 300  # Re-selecao de ativos (5 min)

# ============================================
# CIRCUIT BREAKER
# ============================================
CB_FAILURE_THRESHOLD = 10      # Falhas para abrir circuito
CB_RECOVERY_TIMEOUT = 120      # Segundos para tentar recuperar
CB_HALF_OPEN_MAX_CALLS = 3     # Chamadas em half-open

# Retry com backoff
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0         # Delay base em segundos
RETRY_MAX_DELAY = 30.0         # Delay maximo

# ============================================
# BACKTESTING
# ============================================
BACKTEST_DAYS = 180            # 6 meses de dados
BACKTEST_COMMISSION = 0.0004   # 0.04% (taker)
BACKTEST_SLIPPAGE = 0.001      # 0.1% slippage
BACKTEST_INITIAL_CAPITAL = 10000.0

# ============================================
# CAMINHOS
# ============================================
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
DATA_DIR = os.path.join(os.path.dirname(__file__), "backtest_data")
DB_PATH = os.path.join(os.path.dirname(__file__), "trades.db")

# Criar diretorios se nao existirem
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


def get_api_keys(use_testnet: bool = None):
    """Retorna as chaves de API corretas baseado no modo."""
    if use_testnet is None:
        use_testnet = USE_TESTNET
    if use_testnet:
        return BINANCE_TESTNET_API_KEY, BINANCE_TESTNET_API_SECRET
    return BINANCE_API_KEY, BINANCE_API_SECRET


def validate_config(use_testnet: bool = None):
    """Valida se as configuracoes essenciais estao presentes."""
    if use_testnet is None:
        use_testnet = USE_TESTNET
    
    errors = []
    api_key, api_secret = get_api_keys(use_testnet)
    mode = "TESTNET" if use_testnet else "REAL"
    
    if not api_key or api_key.startswith("sua_"):
        errors.append(f"API Key da Binance ({mode}) nao configurada no .env")
    if not api_secret or api_secret.startswith("sua_"):
        errors.append(f"API Secret da Binance ({mode}) nao configurado no .env")
    
    return errors


def print_config_summary(use_testnet: bool = None):
    """Exibe resumo das configuracoes atuais."""
    if use_testnet is None:
        use_testnet = USE_TESTNET
    
    mode = "TESTNET" if use_testnet else "CONTA REAL"
    api_key, _ = get_api_keys(use_testnet)
    key_preview = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "NAO CONFIGURADA"
    
    telegram_status = "Configurado" if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID else "Nao configurado"
    
    summary = {
        "Modo": mode,
        "API Key": key_preview,
        "Telegram": telegram_status,
        "Alavancagem": f"{LEVERAGE}x",
        "Risco/Trade": f"{RISK_PER_TRADE*100:.1f}%",
        "Max Posicoes": MAX_OPEN_POSITIONS,
        "Perda Diaria Max": f"{MAX_DAILY_LOSS_PERCENT*100:.1f}%",
        "Drawdown Max": f"{MAX_DRAWDOWN_PERCENT*100:.1f}%",
        "Trailing Ativacao": f"+{TRAILING_ACTIVATION*100:.1f}%",
        "Trailing Callback": f"{TRAILING_CALLBACK*100:.1f}%",
        "Time Stop": f"{TIME_STOP_MIN}-{TIME_STOP_MAX} min",
        "Timeframe": TIMEFRAME,
        "Monitor Interval": f"{MONITOR_INTERVAL_SECONDS}s",
        "CB Threshold": f"{CB_FAILURE_THRESHOLD} falhas",
    }
    return summary
