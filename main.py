#!/usr/bin/env python3
"""
main.py - Ponto de entrada do Bot de Trading Binance Futures.
Menu interativo com opcao de Testnet e Conta Real.

v3.0 - Correcoes:
  - Dashboard 100% a prova de erros (try/except em cada secao)
  - KeyError 'P&L Diario' corrigido (acesso seguro a dicts)
  - Loop principal separado: monitor, entradas e dashboard independentes
  - Auto-resume apos cooldown de perdas consecutivas
  - Cooldown por simbolo apos fechamento (evita re-entrada imediata)

DISCLAIMER: Este bot e uma ferramenta de auxilio a decisao.
O usuario final e o unico responsavel por qualquer perda financeira.
Opere por sua conta e risco.
"""

import os
import sys
import time
import signal
import threading
from datetime import datetime

from colorama import Fore, Back, Style, init

init(autoreset=True)

# Adicionar diretorio ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    get_api_keys, validate_config, print_config_summary,
    LEVERAGE, ENTRY_INTERVAL_SECONDS, ENTRY_JITTER_SECONDS,
    MONITOR_INTERVAL_SECONDS, DASHBOARD_REFRESH_SECONDS,
    MAX_OPEN_POSITIONS, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    MAX_CONSECUTIVE_LOSSES, CONSECUTIVE_LOSS_COOLDOWN,
    SELECTION_INTERVAL_SECONDS
)
from binance_client import BinanceClientWrapper
from strategy_engine import StrategyEngine
from risk_manager import RiskManager
from position_manager import PositionManager, Position
from telegram_notifier import TelegramNotifier
from backtest_engine import BacktestEngine
from utils.logger import logger
from utils.helpers import (
    clear_screen, print_header, print_separator, print_success,
    print_error, print_warning, print_info, format_pnl, format_percent,
    get_jitter, safe_float
)


# ============================================
# VARIAVEIS GLOBAIS
# ============================================
bot_running = False
shutdown_event = threading.Event()

# Cooldown por simbolo apos fechamento (segundos)
SYMBOL_COOLDOWN = 300  # 5 minutos


# ============================================
# BANNER E MENU
# ============================================
def print_banner():
    """Exibe banner do bot."""
    clear_screen()
    print(f"""
{Fore.CYAN}╔══════════════════════════════════════════════════════════╗
║                                                          ║
║   {Fore.YELLOW}██████╗  ██╗███╗   ██╗ █████╗ ███╗   ██╗ ██████╗███████╗{Fore.CYAN}║
║   {Fore.YELLOW}██╔══██╗ ██║████╗  ██║██╔══██╗████╗  ██║██╔════╝██╔════╝{Fore.CYAN}║
║   {Fore.YELLOW}██████╔╝ ██║██╔██╗ ██║███████║██╔██╗ ██║██║     █████╗  {Fore.CYAN}║
║   {Fore.YELLOW}██╔══██╗ ██║██║╚██╗██║██╔══██║██║╚██╗██║██║     ██╔══╝  {Fore.CYAN}║
║   {Fore.YELLOW}██████╔╝ ██║██║ ╚████║██║  ██║██║ ╚████║╚██████╗███████╗{Fore.CYAN}║
║   {Fore.YELLOW}╚═════╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝╚══════╝{Fore.CYAN}║
║                                                          ║
║   {Fore.WHITE}Bot de Trading Automatizado - Futuros Scalping{Fore.CYAN}          ║
║   {Fore.WHITE}Versao 3.0.0{Fore.CYAN}                                            ║
║                                                          ║
╚══════════════════════════════════════════════════════════╝{Style.RESET_ALL}
""")


def print_disclaimer():
    """Exibe disclaimer obrigatorio."""
    print(f"""
{Fore.RED}{'='*60}
  AVISO IMPORTANTE - LEIA COM ATENCAO
{'='*60}{Style.RESET_ALL}
{Fore.YELLOW}
  Este bot e uma ferramenta de AUXILIO a decisao de trading.
  
  - NAO ha garantia de lucros.
  - Operar futuros com alavancagem envolve ALTO RISCO.
  - Voce pode perder TODO o seu capital investido.
  - O usuario e o UNICO responsavel por perdas financeiras.
  - Use SEMPRE a Testnet antes de operar com dinheiro real.
  - Nunca invista mais do que pode perder.
{Style.RESET_ALL}
{Fore.RED}{'='*60}{Style.RESET_ALL}
""")


def select_mode() -> bool:
    """Menu de selecao de modo: Testnet ou Conta Real."""
    print(f"""
{Fore.CYAN}┌──────────────────────────────────────────┐
│       SELECIONE O MODO DE OPERACAO       │
├──────────────────────────────────────────┤
│                                          │
│   {Fore.GREEN}[1]{Fore.CYAN} TESTNET (Simulacao)                 │
│       Ambiente seguro para testes        │
│       Sem risco financeiro real          │
│                                          │
│   {Fore.RED}[2]{Fore.CYAN} CONTA REAL (Mainnet)                │
│       Operacoes com dinheiro real        │
│       USE COM EXTREMA CAUTELA            │
│                                          │
│   {Fore.WHITE}[0]{Fore.CYAN} Sair                                │
│                                          │
└──────────────────────────────────────────┘{Style.RESET_ALL}
""")
    
    while True:
        choice = input(f"{Fore.WHITE}  Escolha [0/1/2]: {Style.RESET_ALL}").strip()
        
        if choice == "0":
            print_info("Saindo...")
            sys.exit(0)
        elif choice == "1":
            return True  # Testnet
        elif choice == "2":
            # Confirmacao extra para conta real
            print(f"\n{Fore.RED}  ATENCAO: Voce esta prestes a operar com DINHEIRO REAL!")
            print(f"  Todas as operacoes terao impacto financeiro real.{Style.RESET_ALL}\n")
            confirm = input(f"  {Fore.YELLOW}Digite 'CONFIRMO' para continuar: {Style.RESET_ALL}").strip()
            if confirm == "CONFIRMO":
                return False  # Conta Real
            else:
                print_warning("Operacao cancelada. Voltando ao menu...")
                continue
        else:
            print_error("Opcao invalida. Tente novamente.")


def main_menu(use_testnet: bool) -> str:
    """Menu principal do bot."""
    mode_text = f"{Fore.GREEN}TESTNET" if use_testnet else f"{Fore.RED}CONTA REAL"
    mode_icon = "[T]" if use_testnet else "[$]"
    
    print(f"""
{Fore.CYAN}┌──────────────────────────────────────────┐
│          MENU PRINCIPAL                  │
│          Modo: {mode_icon} {mode_text}{Fore.CYAN}                   │
├──────────────────────────────────────────┤
│                                          │
│   {Fore.GREEN}[1]{Fore.CYAN} Iniciar Bot Automatico              │
│   {Fore.GREEN}[2]{Fore.CYAN} Ver Saldo e Posicoes                │
│   {Fore.GREEN}[3]{Fore.CYAN} Analise de Mercado                  │
│   {Fore.GREEN}[4]{Fore.CYAN} Executar Backtest                   │
│   {Fore.GREEN}[5]{Fore.CYAN} Configuracoes                       │
│   {Fore.GREEN}[6]{Fore.CYAN} Testar Telegram                     │
│   {Fore.GREEN}[7]{Fore.CYAN} Trocar Modo (Testnet/Real)          │
│   {Fore.GREEN}[8]{Fore.CYAN} Fechar Todas as Posicoes            │
│                                          │
│   {Fore.WHITE}[0]{Fore.CYAN} Sair                                │
│                                          │
└──────────────────────────────────────────┘{Style.RESET_ALL}
""")
    
    choice = input(f"{Fore.WHITE}  Escolha [0-8]: {Style.RESET_ALL}").strip()
    return choice


# ============================================
# FUNCOES DO MENU
# ============================================
def show_balance_and_positions(client: BinanceClientWrapper):
    """Exibe saldo e posicoes abertas."""
    print_header("SALDO E POSICOES")
    
    try:
        balance = client.get_futures_balance()
        print(f"\n  {Fore.WHITE}Saldo USDT: {Fore.GREEN}{balance:,.2f} USDT{Style.RESET_ALL}")
        
        positions = client.get_open_positions()
        
        if positions:
            print(f"\n  {Fore.YELLOW}Posicoes Abertas ({len(positions)}):{Style.RESET_ALL}\n")
            
            print(f"  {'Simbolo':<12} {'Direcao':<8} {'Qtd':<12} {'Entrada':<12} {'P&L':>12}")
            print_separator("-", 60)
            
            total_pnl = 0
            for pos in positions:
                pnl = pos['unrealized_pnl']
                total_pnl += pnl
                pnl_color = Fore.GREEN if pnl >= 0 else Fore.RED
                side_color = Fore.GREEN if pos['side'] == 'LONG' else Fore.RED
                
                print(
                    f"  {Fore.WHITE}{pos['symbol']:<12}"
                    f"{side_color}{pos['side']:<8}"
                    f"{Fore.WHITE}{pos['quantity']:<12.4f}"
                    f"{pos['entry_price']:<12.4f}"
                    f"{pnl_color}{pnl:>+12.2f}{Style.RESET_ALL}"
                )
            
            print_separator("-", 60)
            total_color = Fore.GREEN if total_pnl >= 0 else Fore.RED
            print(f"  {'P&L Total:':<44}{total_color}{total_pnl:>+12.2f} USDT{Style.RESET_ALL}")
        else:
            print(f"\n  {Fore.YELLOW}Nenhuma posicao aberta.{Style.RESET_ALL}")
        
        # Status do circuit breaker
        cb_status = client.get_circuit_status()
        print(f"\n  {Fore.WHITE}Circuit Breaker: {cb_status.get('estado', 'N/A')} "
              f"(Falhas: {cb_status.get('falhas', 0)}){Style.RESET_ALL}")
        
        # Modo SL/TP
        sl_mode = "SOFTWARE" if not client.exchange_sl_tp_supported else "EXCHANGE"
        print(f"  {Fore.WHITE}Modo SL/TP: {sl_mode}{Style.RESET_ALL}")
        
    except Exception as e:
        print_error(f"Erro ao consultar saldo: {e}")
    
    input(f"\n  {Fore.WHITE}Pressione Enter para voltar...{Style.RESET_ALL}")


def show_market_analysis(client: BinanceClientWrapper):
    """Executa e exibe analise de mercado."""
    print_header("ANALISE DE MERCADO")
    
    try:
        engine = StrategyEngine(client)
        
        print_info("Selecionando ativos (pode levar ~1 minuto)...")
        assets = engine.select_assets()
        
        if not assets:
            print_warning("Nenhum ativo selecionado. Tente novamente mais tarde.")
            input(f"\n  Pressione Enter para voltar...")
            return
        
        # Tendencia BTC
        btc_trend = engine.analyze_btc_trend()
        print(f"\n  {Fore.WHITE}Tendencia BTC: ", end="")
        if btc_trend == "ALTA":
            print(f"{Fore.GREEN}ALTA{Style.RESET_ALL}")
        elif btc_trend == "BAIXA":
            print(f"{Fore.RED}BAIXA{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}LATERAL{Style.RESET_ALL}")
        
        # Analise individual
        print(f"\n  {Fore.YELLOW}Analise dos Ativos Selecionados:{Style.RESET_ALL}\n")
        
        summaries = engine.get_analysis_summary()
        
        if summaries:
            print(f"  {'Simbolo':<12} {'Preco':<12} {'EMA9':<12} {'EMA21':<12} "
                  f"{'RSI':<8} {'ATR':<10} {'Sinal':<8}")
            print_separator("-", 80)
            
            for s in summaries:
                signal = s.get("Sinal", "NONE")
                signal_color = Fore.GREEN if signal == "LONG" else (Fore.RED if signal == "SHORT" else Fore.WHITE)
                
                print(
                    f"  {Fore.WHITE}{s.get('Simbolo', 'N/A'):<12}"
                    f"{s.get('Preco', 'N/A'):<12}"
                    f"{s.get('EMA9', 'N/A'):<12}"
                    f"{s.get('EMA21', 'N/A'):<12}"
                    f"{s.get('RSI', 'N/A'):<8}"
                    f"{s.get('ATR', 'N/A'):<10}"
                    f"{signal_color}{signal:<8}{Style.RESET_ALL}"
                )
        else:
            print_warning("Nenhum dado de analise disponivel.")
    
    except Exception as e:
        print_error(f"Erro na analise: {e}")
    
    input(f"\n  {Fore.WHITE}Pressione Enter para voltar...{Style.RESET_ALL}")


def run_backtest_menu(client: BinanceClientWrapper):
    """Menu de backtesting."""
    print_header("BACKTESTING")
    
    try:
        engine = BacktestEngine(client)
        
        print(f"\n  {Fore.WHITE}Simbolo para backtest (ex: BTCUSDT): ", end="")
        symbol = input().strip().upper()
        
        if not symbol:
            symbol = "BTCUSDT"
        
        print_info(f"Executando backtest para {symbol}...")
        print_info("Isso pode levar alguns minutos...")
        
        results = engine.run(symbol)
        
        if results:
            print(f"\n  {Fore.YELLOW}--- RESULTADOS DO BACKTEST ---{Style.RESET_ALL}\n")
            for key, val in results.items():
                print(f"  {Fore.CYAN}{key:<30}{Fore.WHITE}{val}{Style.RESET_ALL}")
        else:
            print_warning("Backtest nao retornou resultados.")
    
    except Exception as e:
        print_error(f"Erro no backtest: {e}")
    
    input(f"\n  {Fore.WHITE}Pressione Enter para voltar...{Style.RESET_ALL}")


def show_config(use_testnet: bool):
    """Exibe configuracoes atuais."""
    print_header("CONFIGURACOES ATUAIS")
    
    summary = print_config_summary(use_testnet)
    print()
    for key, val in summary.items():
        print(f"  {Fore.CYAN}{key:<25}{Fore.WHITE}{val}{Style.RESET_ALL}")
    
    input(f"\n  {Fore.WHITE}Pressione Enter para voltar...{Style.RESET_ALL}")


def test_telegram():
    """Testa conexao com Telegram."""
    print_header("TESTE DE TELEGRAM")
    
    notifier = TelegramNotifier()
    
    if not notifier.enabled:
        print_error("Telegram nao configurado. Verifique TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env")
        input(f"\n  Pressione Enter para voltar...")
        return
    
    print_info("Testando conexao...")
    
    if notifier.test_connection():
        print_success("Conexao com Telegram OK!")
        
        print_info("Enviando mensagem de teste...")
        if notifier.send_message("Teste de conexao do Bot de Trading - OK!"):
            print_success("Mensagem enviada com sucesso!")
        else:
            print_error("Falha ao enviar mensagem.")
    else:
        print_error("Falha na conexao com Telegram.")
    
    input(f"\n  {Fore.WHITE}Pressione Enter para voltar...{Style.RESET_ALL}")


def close_all_positions_menu(client: BinanceClientWrapper):
    """Menu para fechar todas as posicoes."""
    print_header("FECHAR TODAS AS POSICOES")
    
    try:
        positions = client.get_open_positions()
        
        if not positions:
            print_info("Nenhuma posicao aberta.")
            input(f"\n  Pressione Enter para voltar...")
            return
        
        print(f"\n  {Fore.YELLOW}Posicoes que serao fechadas:{Style.RESET_ALL}\n")
        for pos in positions:
            side_color = Fore.GREEN if pos['side'] == 'LONG' else Fore.RED
            print(f"  {pos['symbol']} | {side_color}{pos['side']}{Style.RESET_ALL} | "
                  f"Qtd: {pos['quantity']:.4f}")
        
        confirm = input(f"\n  {Fore.RED}Confirma fechamento? (s/n): {Style.RESET_ALL}").strip().lower()
        
        if confirm == "s":
            print_info("Fechando posicoes...")
            closed = client.close_all_positions()
            print_success(f"{closed} posicao(oes) fechada(s).")
        else:
            print_info("Operacao cancelada.")
    
    except Exception as e:
        print_error(f"Erro: {e}")
    
    input(f"\n  {Fore.WHITE}Pressione Enter para voltar...{Style.RESET_ALL}")


# ============================================
# DASHBOARD EM TEMPO REAL (v3.0 - a prova de erros)
# ============================================
def print_dashboard(client, risk_manager, position_manager,
                     strategy, use_testnet, last_action="", symbol_cooldowns=None):
    """Imprime dashboard atualizado no terminal. Nunca levanta excecao."""
    try:
        clear_screen()
    except Exception:
        pass
    
    mode = f"{Fore.GREEN}TESTNET" if use_testnet else f"{Fore.RED}CONTA REAL"
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    
    # Modo SL/TP
    try:
        sl_mode = "SOFTWARE" if not client.exchange_sl_tp_supported else "EXCHANGE"
    except Exception:
        sl_mode = "N/A"
    
    print(f"""
{Fore.CYAN}╔══════════════════════════════════════════════════════════╗
║  {Fore.YELLOW}BINANCE FUTURES SCALPING BOT v3.0{Fore.CYAN}                        ║
║  Modo: {mode}{Fore.CYAN} | SL/TP: {Fore.WHITE}{sl_mode}{Fore.CYAN} | {Fore.WHITE}{now_str}{Fore.CYAN}       ║
╚══════════════════════════════════════════════════════════╝{Style.RESET_ALL}
""")
    
    # === RESUMO DE RISCO (acesso seguro a todas as chaves) ===
    try:
        risk = risk_manager.get_risk_summary()
        saldo = risk.get("Saldo Atual", "N/A")
        pnl_diario = risk.get("P&L Diário", risk.get("P&L Diario", "N/A"))
        pnl_sessao = risk.get("P&L Sessão", risk.get("P&L Sessao", "N/A"))
        drawdown = risk.get("Drawdown Atual", "N/A")
        win_rate = risk.get("Win Rate", "N/A")
        status = risk.get("Status", "N/A")
        total_trades = risk.get("Total Trades", 0)
        perdas_consec = risk.get("Perdas Consecutivas", "N/A")
        
        status_color = Fore.GREEN if status == "ATIVO" else Fore.RED
        
        print(f"  {Fore.YELLOW}--- RESUMO DE RISCO ---{Style.RESET_ALL}")
        print(f"  Saldo: {Fore.WHITE}{saldo}{Style.RESET_ALL}  |  "
              f"P&L Diario: {Fore.WHITE}{pnl_diario}{Style.RESET_ALL}  |  "
              f"P&L Sessao: {Fore.WHITE}{pnl_sessao}{Style.RESET_ALL}")
        print(f"  Drawdown: {Fore.WHITE}{drawdown}{Style.RESET_ALL}  |  "
              f"Win Rate: {Fore.WHITE}{win_rate}{Style.RESET_ALL}  |  "
              f"Trades: {Fore.WHITE}{total_trades}{Style.RESET_ALL}  |  "
              f"Status: {status_color}{status}{Style.RESET_ALL}")
        print(f"  Perdas Consec: {Fore.WHITE}{perdas_consec}{Style.RESET_ALL}")
        
        if risk_manager.is_paused:
            elapsed = time.time() - risk_manager.pause_time if risk_manager.pause_time > 0 else 0
            remaining = max(0, CONSECUTIVE_LOSS_COOLDOWN - elapsed)
            print(f"  {Fore.RED}PAUSADO: {risk_manager.pause_reason} "
                  f"(retoma em {remaining:.0f}s){Style.RESET_ALL}")
    except Exception as e:
        print(f"  {Fore.RED}Erro ao carregar risco: {e}{Style.RESET_ALL}")
    
    # === CIRCUIT BREAKER ===
    try:
        cb = client.get_circuit_status()
        cb_estado = cb.get("estado", "N/A")
        cb_falhas = cb.get("falhas", 0)
        cb_color = Fore.GREEN if cb_estado == "FECHADO" else Fore.RED
        print(f"  Circuit Breaker: {cb_color}{cb_estado}{Style.RESET_ALL} (Falhas: {cb_falhas})")
    except Exception:
        print(f"  Circuit Breaker: {Fore.YELLOW}N/A{Style.RESET_ALL}")
    
    # === POSICOES ABERTAS ===
    try:
        positions = position_manager.get_positions_summary()
        pos_count = len(positions)
        print(f"\n  {Fore.YELLOW}--- POSICOES ABERTAS ({pos_count}/{MAX_OPEN_POSITIONS}) ---{Style.RESET_ALL}")
        
        if positions:
            print(f"  {'Simbolo':<10} {'Dir':<6} {'Entrada':<10} {'Atual':<10} "
                  f"{'P&L':>8} {'P&L%':>8} {'Trail':>6} {'SL/TP':>6} {'Tempo':>8}")
            print(f"  {'-'*76}")
            
            for p in positions:
                try:
                    pnl_str = p.get('P&L', '0')
                    pnl_val = safe_float(pnl_str.replace('+', '').replace(' ', ''), 0)
                    pnl_color = Fore.GREEN if pnl_val >= 0 else Fore.RED
                    direcao = p.get('Direcao', p.get('Direção', 'N/A'))
                    side_color = Fore.GREEN if direcao == 'LONG' else Fore.RED
                    
                    print(
                        f"  {Fore.WHITE}{p.get('Simbolo', p.get('Símbolo', 'N/A')):<10}"
                        f"{side_color}{direcao:<6}{Style.RESET_ALL}"
                        f"{p.get('Entrada', 'N/A'):<10}"
                        f"{p.get('Atual', 'N/A'):<10}"
                        f"{pnl_color}{pnl_str:>8}{Style.RESET_ALL}"
                        f"{pnl_color}{p.get('P&L%', 'N/A'):>8}{Style.RESET_ALL}"
                        f"{'  ' + p.get('Trailing', 'N/A'):>6}"
                        f"{'  ' + p.get('SL/TP', 'N/A'):>6}"
                        f"{'  ' + p.get('Tempo', 'N/A'):>8}"
                    )
                except Exception:
                    print(f"  {Fore.RED}Erro ao exibir posicao{Style.RESET_ALL}")
            
            try:
                total_pnl = position_manager.get_total_unrealized_pnl()
                total_color = Fore.GREEN if total_pnl >= 0 else Fore.RED
                print(f"  {'-'*76}")
                print(f"  {'P&L Total Nao Realizado:':<46}"
                      f"{total_color}{total_pnl:>+10.2f} USDT{Style.RESET_ALL}")
            except Exception:
                pass
        else:
            print(f"  {Fore.WHITE}Nenhuma posicao aberta{Style.RESET_ALL}")
    except Exception as e:
        print(f"  {Fore.RED}Erro ao listar posicoes: {e}{Style.RESET_ALL}")
    
    # === ATIVOS MONITORADOS ===
    try:
        print(f"\n  {Fore.YELLOW}--- ATIVOS MONITORADOS ---{Style.RESET_ALL}")
        if strategy.selected_assets:
            print(f"  {', '.join(strategy.selected_assets)}")
            print(f"  Tendencia BTC: {strategy.last_btc_trend}")
        else:
            print(f"  {Fore.WHITE}Aguardando selecao...{Style.RESET_ALL}")
    except Exception:
        print(f"  {Fore.WHITE}N/A{Style.RESET_ALL}")
    
    # === COOLDOWNS POR SIMBOLO ===
    if symbol_cooldowns:
        try:
            active_cooldowns = []
            now = time.time()
            for sym, cd_time in list(symbol_cooldowns.items()):
                remaining = cd_time - now
                if remaining > 0:
                    active_cooldowns.append(f"{sym}({remaining:.0f}s)")
            if active_cooldowns:
                print(f"\n  {Fore.YELLOW}--- COOLDOWNS ---{Style.RESET_ALL}")
                print(f"  {', '.join(active_cooldowns)}")
        except Exception:
            pass
    
    # === ULTIMA ACAO ===
    if last_action:
        print(f"\n  {Fore.CYAN}Ultima acao: {Fore.WHITE}{last_action}{Style.RESET_ALL}")
    
    # === CONTROLES ===
    print(f"\n  {Fore.CYAN}--- CONTROLES ---{Style.RESET_ALL}")
    print(f"  {Fore.WHITE}Ctrl+C = Parar bot (fecha posicoes ordenadamente){Style.RESET_ALL}")


# ============================================
# LOOP PRINCIPAL DO BOT (v3.0)
# ============================================
def run_bot(client: BinanceClientWrapper, use_testnet: bool):
    """Executa o loop principal do bot."""
    global bot_running
    
    print_header("INICIANDO BOT AUTOMATICO")
    
    mode_text = "TESTNET" if use_testnet else "CONTA REAL"
    
    try:
        # Verificar saldo
        balance = client.get_futures_balance()
        print_success(f"Saldo: {balance:,.2f} USDT")
        
        if balance <= 0:
            print_error("Saldo insuficiente para operar!")
            input(f"\n  Pressione Enter para voltar...")
            return
        
        # Inicializar componentes
        risk_manager = RiskManager(balance)
        position_manager = PositionManager(client, risk_manager)
        strategy = StrategyEngine(client)
        notifier = TelegramNotifier()
        
        # Cooldowns por simbolo (evita re-entrada imediata)
        symbol_cooldowns = {}
        
        # Fechar posicoes previas
        print_info("Verificando posicoes previas...")
        prev_positions = client.get_open_positions()
        if prev_positions:
            print_warning(f"Encontradas {len(prev_positions)} posicoes previas. Fechando...")
            client.close_all_positions()
            print_success("Posicoes previas fechadas.")
        
        # Notificar inicio
        notifier.notify_bot_start(mode_text, balance)
        
        # Registrar comandos do Telegram (acesso seguro a dicts)
        def cmd_status(text):
            try:
                risk = risk_manager.get_risk_summary()
                pos_count = position_manager.get_open_count()
                sl_mode = "SOFTWARE" if not client.exchange_sl_tp_supported else "EXCHANGE"
                return (
                    f"<b>Status do Bot</b>\n"
                    f"Saldo: {risk.get('Saldo Atual', 'N/A')}\n"
                    f"Posicoes: {pos_count}/{MAX_OPEN_POSITIONS}\n"
                    f"P&L Diario: {risk.get('P&L Diário', risk.get('P&L Diario', 'N/A'))}\n"
                    f"SL/TP: {sl_mode}\n"
                    f"Status: {risk.get('Status', 'N/A')}"
                )
            except Exception as e:
                return f"Erro: {e}"
        
        def cmd_positions(text):
            try:
                positions = position_manager.get_positions_summary()
                if not positions:
                    return "Nenhuma posicao aberta"
                lines = ["<b>Posicoes Abertas</b>\n"]
                for p in positions:
                    sym = p.get('Simbolo', p.get('Símbolo', 'N/A'))
                    direcao = p.get('Direcao', p.get('Direção', 'N/A'))
                    lines.append(f"{sym} | {direcao} | P&L: {p.get('P&L', 'N/A')} | {p.get('SL/TP', 'N/A')}")
                return "\n".join(lines)
            except Exception as e:
                return f"Erro: {e}"
        
        def cmd_pause(text):
            risk_manager.is_paused = True
            risk_manager.pause_reason = "Comando Telegram"
            risk_manager.pause_time = time.time()
            return "Bot pausado via Telegram"
        
        def cmd_resume(text):
            risk_manager.force_resume()
            return "Bot retomado via Telegram"
        
        def cmd_balance(text):
            try:
                bal = client.get_futures_balance()
                return f"Saldo: {bal:,.2f} USDT"
            except Exception:
                return "Erro ao consultar saldo"
        
        notifier.register_command("status", cmd_status)
        notifier.register_command("posicoes", cmd_positions)
        notifier.register_command("pausar", cmd_pause)
        notifier.register_command("retomar", cmd_resume)
        notifier.register_command("saldo", cmd_balance)
        notifier.start_polling()
        
        # Configurar handler de shutdown
        bot_running = True
        shutdown_event.clear()
        
        def signal_handler(sig, frame):
            global bot_running
            bot_running = False
            shutdown_event.set()
            print(f"\n\n{Fore.YELLOW}  Recebido sinal de parada. Encerrando...{Style.RESET_ALL}")
        
        signal.signal(signal.SIGINT, signal_handler)
        
        print_success("Bot iniciado! Pressione Ctrl+C para parar.\n")
        
        last_entry_time = 0
        last_monitor_time = 0
        last_dashboard_time = 0
        last_selection_time = 0
        last_action = "Inicializando..."
        
        # Loop principal
        while bot_running and not shutdown_event.is_set():
            now = time.time()
            
            # === 1. AUTO-RESUME: Verificar se pode retomar apos pausa ===
            try:
                if risk_manager.is_paused and risk_manager.pause_time > 0:
                    elapsed_pause = now - risk_manager.pause_time
                    if elapsed_pause >= CONSECUTIVE_LOSS_COOLDOWN:
                        # Retomar apenas se pausa foi por perdas consecutivas
                        if "consecutiv" in risk_manager.pause_reason.lower():
                            logger.info(
                                f"Auto-resume: cooldown de {CONSECUTIVE_LOSS_COOLDOWN}s expirado. "
                                f"Retomando operacoes."
                            )
                            risk_manager.force_resume()
                            last_action = f"Auto-resume apos {CONSECUTIVE_LOSS_COOLDOWN//60}min de cooldown"
                            try:
                                notifier.send_message(
                                    f"Bot retomado automaticamente apos cooldown de "
                                    f"{CONSECUTIVE_LOSS_COOLDOWN//60}min"
                                )
                            except Exception:
                                pass
            except Exception as e:
                logger.warning(f"Erro no auto-resume: {e}")
            
            # === 2. MONITORAR POSICOES (prioridade maxima) ===
            try:
                if now - last_monitor_time >= MONITOR_INTERVAL_SECONDS:
                    # Atualizar saldo
                    try:
                        balance = client.get_futures_balance()
                        risk_manager.update_balance(balance)
                    except Exception:
                        pass
                    
                    # Monitorar posicoes (SL/TP/trailing/time stop)
                    if position_manager.get_open_count() > 0:
                        closed = position_manager.monitor_cycle()
                        for trade in closed:
                            try:
                                last_action = (
                                    f"Fechou {trade['symbol']} ({trade['reason']}): "
                                    f"P&L {trade['pnl']:+.2f}"
                                )
                                
                                # Adicionar cooldown para o simbolo
                                symbol_cooldowns[trade['symbol']] = now + SYMBOL_COOLDOWN
                                
                                notifier.notify_position_close(
                                    trade["symbol"], trade["side"], trade["reason"],
                                    trade["pnl"], trade["pnl_percent"], trade["duration_min"]
                                )
                            except Exception as e:
                                logger.warning(f"Erro ao processar trade fechado: {e}")
                            
                            # Verificar alertas de risco
                            try:
                                if risk_manager.daily_loss_triggered:
                                    notifier.notify_risk_alert(
                                        "Perda Diaria Maxima",
                                        f"Perda diaria de {risk_manager.get_daily_loss_percent():.2f}%"
                                    )
                                if risk_manager.drawdown_triggered:
                                    notifier.notify_risk_alert(
                                        "Drawdown Maximo",
                                        f"Drawdown de {risk_manager.get_current_drawdown():.2f}%"
                                    )
                            except Exception:
                                pass
                    
                    last_monitor_time = now
            except Exception as e:
                logger.warning(f"Erro no monitoramento: {e}")
            
            # === 3. RE-SELECIONAR ATIVOS ===
            try:
                if now - last_selection_time >= SELECTION_INTERVAL_SECONDS:
                    try:
                        strategy.select_assets()
                        if strategy.selected_assets:
                            last_action = f"Ativos: {', '.join(strategy.selected_assets)}"
                    except Exception as e:
                        logger.warning(f"Erro na selecao de ativos: {e}")
                    last_selection_time = now
            except Exception as e:
                logger.warning(f"Erro no bloco de selecao: {e}")
            
            # === 4. BUSCAR NOVAS ENTRADAS ===
            try:
                entry_interval = get_jitter(ENTRY_INTERVAL_SECONDS, ENTRY_JITTER_SECONDS)
                if now - last_entry_time >= entry_interval:
                    can_open, reason = risk_manager.can_open_position(
                        position_manager.get_open_count()
                    )
                    
                    if can_open and strategy.selected_assets:
                        # Excluir simbolos com posicao aberta E em cooldown
                        exclude = set(position_manager.get_open_symbols())
                        for sym, cd_time in list(symbol_cooldowns.items()):
                            if cd_time > now:
                                exclude.add(sym)
                            else:
                                del symbol_cooldowns[sym]
                        
                        try:
                            opportunities = strategy.find_opportunities(list(exclude))
                            
                            if opportunities:
                                last_action = f"Encontradas {len(opportunities)} oportunidades"
                            else:
                                last_action = f"Sem oportunidades ({len(strategy.selected_assets)} ativos)"
                            
                            for opp in opportunities:
                                # Verificar novamente antes de cada entrada
                                can_open, reason = risk_manager.can_open_position(
                                    position_manager.get_open_count()
                                )
                                if not can_open:
                                    last_action = f"Entrada bloqueada: {reason}"
                                    break
                                
                                try:
                                    symbol = opp["symbol"]
                                    direction = opp["signal"]
                                    atr = opp["atr"]
                                    price = opp["price"]
                                    
                                    # Position sizing
                                    quantity, stop_dist, capital_risk = risk_manager.calculate_position_size(
                                        balance, atr, price
                                    )
                                    
                                    if quantity <= 0:
                                        logger.info(f"Quantidade zero para {symbol}, pulando")
                                        continue
                                    
                                    # Validar quantidade maxima via exchange info
                                    try:
                                        filters = client.get_symbol_filters(symbol)
                                        max_qty_filter = filters.get("MARKET_LOT_SIZE", filters.get("LOT_SIZE", {}))
                                        max_qty = float(max_qty_filter.get("maxQty", 999999999))
                                        if quantity > max_qty:
                                            logger.warning(
                                                f"{symbol}: qty {quantity:.4f} > maxQty {max_qty}. "
                                                f"Reduzindo para maxQty."
                                            )
                                            quantity = max_qty * 0.95  # 95% do max
                                    except Exception:
                                        pass
                                    
                                    # Calcular SL/TP
                                    sl, tp = risk_manager.calculate_sl_tp(price, stop_dist, direction)
                                    
                                    # Configurar simbolo
                                    client.set_leverage(symbol, LEVERAGE)
                                    client.set_margin_type(symbol, "CROSSED")
                                    
                                    # Abrir posicao
                                    side = "BUY" if direction == "LONG" else "SELL"
                                    order = client.place_market_order(symbol, side, quantity)
                                    
                                    if order:
                                        # Tentar colocar SL e TP na exchange
                                        sl_side = "SELL" if direction == "LONG" else "BUY"
                                        
                                        sl_order = client.place_stop_loss(
                                            symbol, sl_side, sl,
                                            close_position=True, quantity=quantity
                                        )
                                        tp_order = client.place_take_profit(
                                            symbol, sl_side, tp,
                                            close_position=True, quantity=quantity
                                        )
                                        
                                        has_exchange_sl = sl_order is not None
                                        has_exchange_tp = tp_order is not None
                                        
                                        if not has_exchange_sl:
                                            logger.info(
                                                f"SL/TP de {symbol} sera gerenciado por SOFTWARE "
                                                f"(SL: {sl:.4f}, TP: {tp:.4f})"
                                            )
                                        
                                        # Registrar posicao
                                        entry_price = float(order.get("avgPrice", price))
                                        if entry_price == 0:
                                            entry_price = price
                                        
                                        exec_qty = float(order.get("executedQty", quantity))
                                        if exec_qty > 0:
                                            quantity = exec_qty
                                        
                                        pos = Position(
                                            symbol=symbol,
                                            side=direction,
                                            quantity=quantity,
                                            entry_price=entry_price,
                                            stop_loss=sl,
                                            take_profit=tp,
                                            sl_order_id=str(sl_order.get("orderId", "")) if sl_order else None,
                                            tp_order_id=str(tp_order.get("orderId", "")) if tp_order else None,
                                            has_exchange_sl=has_exchange_sl,
                                            has_exchange_tp=has_exchange_tp,
                                        )
                                        position_manager.add_position(pos)
                                        
                                        # Notificar
                                        notifier.notify_position_open(
                                            symbol, direction, quantity,
                                            entry_price, sl, tp
                                        )
                                        
                                        sl_mode_str = "EXCHANGE" if has_exchange_sl else "SOFTWARE"
                                        last_action = (
                                            f"ENTRADA: {direction} {quantity:.4f} {symbol} "
                                            f"@ {entry_price:.4f} (SL/TP: {sl_mode_str})"
                                        )
                                        
                                        logger.info(
                                            f"ENTRADA: {direction} {quantity:.4f} {symbol} "
                                            f"@ {entry_price:.4f} | SL: {sl:.4f} | TP: {tp:.4f} "
                                            f"| Modo: {sl_mode_str}"
                                        )
                                
                                except Exception as e:
                                    logger.error(f"Erro ao abrir posicao em {opp['symbol']}: {e}")
                                    last_action = f"Erro ao abrir {opp['symbol']}: {e}"
                        
                        except Exception as e:
                            logger.error(f"Erro ao buscar oportunidades: {e}")
                            last_action = f"Erro na busca: {e}"
                    
                    elif not can_open:
                        last_action = f"Entradas bloqueadas: {reason}"
                    elif not strategy.selected_assets:
                        last_action = "Aguardando selecao de ativos..."
                    
                    last_entry_time = now
            except Exception as e:
                logger.warning(f"Erro no bloco de entradas: {e}")
            
            # === 5. ATUALIZAR DASHBOARD (nunca pode crashar o loop) ===
            try:
                if now - last_dashboard_time >= DASHBOARD_REFRESH_SECONDS:
                    print_dashboard(
                        client, risk_manager, position_manager,
                        strategy, use_testnet, last_action, symbol_cooldowns
                    )
                    last_dashboard_time = now
            except Exception as e:
                # Dashboard NUNCA pode parar o bot
                logger.debug(f"Erro no dashboard: {e}")
            
            # Sleep curto para nao sobrecarregar
            time.sleep(1)
        
        # Shutdown ordenado
        print(f"\n{Fore.YELLOW}  Encerrando bot...{Style.RESET_ALL}")
        
        # Fechar posicoes
        open_count = position_manager.get_open_count()
        if open_count > 0:
            print_info(f"Fechando {open_count} posicao(oes)...")
            closed = position_manager.close_all()
            print_success(f"{closed} posicao(oes) fechada(s).")
        
        # Parar Telegram
        notifier.stop_polling()
        notifier.notify_bot_stop("Encerramento manual (Ctrl+C)")
        
        # Resumo da sessao
        print_header("RESUMO DA SESSAO")
        try:
            risk = risk_manager.get_risk_summary()
            for key, val in risk.items():
                print(f"  {Fore.CYAN}{key:<25}{Fore.WHITE}{val}{Style.RESET_ALL}")
        except Exception:
            print(f"  {Fore.RED}Erro ao gerar resumo{Style.RESET_ALL}")
        
        bot_running = False
        
    except Exception as e:
        logger.error(f"Erro fatal: {e}")
        print_error(f"Erro fatal: {e}")
        import traceback
        traceback.print_exc()
        bot_running = False
    
    input(f"\n  {Fore.WHITE}Pressione Enter para voltar ao menu...{Style.RESET_ALL}")


# ============================================
# PONTO DE ENTRADA
# ============================================
def main():
    """Funcao principal."""
    print_banner()
    print_disclaimer()
    
    input(f"  {Fore.WHITE}Pressione Enter para continuar...{Style.RESET_ALL}")
    
    # Selecionar modo
    use_testnet = select_mode()
    
    # Validar configuracao
    errors = validate_config(use_testnet)
    if errors:
        print_header("ERROS DE CONFIGURACAO")
        for err in errors:
            print_error(err)
        print(f"\n  {Fore.WHITE}Configure o arquivo .env com suas credenciais.")
        print(f"  Use .env.example como referencia.{Style.RESET_ALL}")
        input(f"\n  Pressione Enter para sair...")
        sys.exit(1)
    
    # Conectar a Binance
    mode_text = "TESTNET" if use_testnet else "CONTA REAL"
    print_info(f"Conectando a Binance ({mode_text})...")
    
    try:
        api_key, api_secret = get_api_keys(use_testnet)
        client = BinanceClientWrapper(api_key, api_secret, testnet=use_testnet)
        
        # Testar conexao
        balance = client.get_futures_balance()
        print_success(f"Conectado! Saldo: {balance:,.2f} USDT")
    except Exception as e:
        print_error(f"Falha ao conectar: {e}")
        print_info("Verifique suas credenciais no arquivo .env")
        input(f"\n  Pressione Enter para sair...")
        sys.exit(1)
    
    # Loop do menu principal
    while True:
        print_banner()
        choice = main_menu(use_testnet)
        
        if choice == "0":
            print_info("Encerrando...")
            break
        
        elif choice == "1":
            run_bot(client, use_testnet)
        
        elif choice == "2":
            show_balance_and_positions(client)
        
        elif choice == "3":
            show_market_analysis(client)
        
        elif choice == "4":
            run_backtest_menu(client)
        
        elif choice == "5":
            show_config(use_testnet)
        
        elif choice == "6":
            test_telegram()
        
        elif choice == "7":
            # Trocar modo
            use_testnet = not use_testnet
            mode_text = "TESTNET" if use_testnet else "CONTA REAL"
            
            errors = validate_config(use_testnet)
            if errors:
                print_error(f"Credenciais para {mode_text} nao configuradas!")
                use_testnet = not use_testnet  # Reverter
                input(f"\n  Pressione Enter para voltar...")
                continue
            
            if not use_testnet:
                print(f"\n{Fore.RED}  ATENCAO: Trocando para CONTA REAL!")
                confirm = input(f"  {Fore.YELLOW}Digite 'CONFIRMO': {Style.RESET_ALL}").strip()
                if confirm != "CONFIRMO":
                    use_testnet = not use_testnet
                    print_warning("Operacao cancelada.")
                    input(f"\n  Pressione Enter para voltar...")
                    continue
            
            try:
                api_key, api_secret = get_api_keys(use_testnet)
                client = BinanceClientWrapper(api_key, api_secret, testnet=use_testnet)
                balance = client.get_futures_balance()
                print_success(f"Modo alterado para {mode_text}! Saldo: {balance:,.2f} USDT")
            except Exception as e:
                print_error(f"Falha ao conectar em {mode_text}: {e}")
                use_testnet = not use_testnet  # Reverter
            
            input(f"\n  Pressione Enter para continuar...")
        
        elif choice == "8":
            close_all_positions_menu(client)
        
        else:
            print_error("Opcao invalida!")
            time.sleep(1)
    
    print(f"\n{Fore.CYAN}  Ate logo!{Style.RESET_ALL}\n")


if __name__ == "__main__":
    main()
