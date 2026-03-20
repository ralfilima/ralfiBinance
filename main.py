"""
main.py - DCA Bot Inteligente v1.0
Menu interativo + Dashboard visual em tempo real.

Filosofia:
  - 5 moedas simultaneas, sempre as melhores
  - DCA automatico quando uma moeda cai
  - Stop Profit GLOBAL: quando a soma das 5 da lucro, fecha TUDO
  - Ciclos infinitos: fecha, seleciona novas 5, recomeça
"""

import os
import sys
import time
import threading
from datetime import datetime

from config import (
    get_api_keys, validate_config,
    NUM_COINS, LEVERAGE, CAPITAL_PER_COIN_PCT,
    GLOBAL_TAKE_PROFIT_PCT, GLOBAL_TAKE_PROFIT_USDT, GLOBAL_STOP_LOSS_PCT,
    DCA_LEVELS, MAX_DCA_ORDERS, INITIAL_ENTRY_PCT,
    MONITOR_INTERVAL, DCA_CHECK_INTERVAL, DASHBOARD_INTERVAL,
    TIMEFRAME,
)
from binance_client import BinanceClientWrapper
from dca_engine import DCAEngine
from coin_selector import select_best_coins
from portfolio_manager import PortfolioManager
from utils.logger import logger
from utils.helpers import format_pnl, format_pct, format_price, timestamp_str


# ============================================
# CORES ANSI
# ============================================
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_BLUE = "\033[44m"
    BG_YELLOW = "\033[43m"


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def pnl_color(val):
    if val > 0:
        return C.GREEN
    elif val < 0:
        return C.RED
    return C.WHITE


def bar_chart(value, max_val, width=20, positive_char="█", negative_char="░"):
    """Cria uma barra visual."""
    if max_val == 0:
        return " " * width
    ratio = min(abs(value) / max_val, 1.0)
    filled = int(ratio * width)
    if value >= 0:
        return f"{C.GREEN}{positive_char * filled}{C.DIM}{'─' * (width - filled)}{C.RESET}"
    else:
        return f"{C.RED}{negative_char * filled}{C.DIM}{'─' * (width - filled)}{C.RESET}"


def progress_bar(current, target, width=30):
    """Barra de progresso para o stop profit global."""
    if target == 0:
        return "─" * width
    ratio = max(0, min(current / target, 1.0))
    filled = int(ratio * width)
    pct = ratio * 100

    if ratio >= 1.0:
        color = C.GREEN + C.BOLD
        char = "█"
    elif ratio >= 0.5:
        color = C.YELLOW
        char = "▓"
    else:
        color = C.CYAN
        char = "░"

    bar = f"{color}{char * filled}{C.DIM}{'─' * (width - filled)}{C.RESET}"
    return f"{bar} {pct:.1f}%"


# ============================================
# BANNER
# ============================================
BANNER = f"""
{C.CYAN}{C.BOLD}
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║     ██████╗  ██████╗ █████╗     ██████╗  ██████╗ ████████╗   ║
║     ██╔══██╗██╔════╝██╔══██╗    ██╔══██╗██╔═══██╗╚══██╔══╝   ║
║     ██║  ██║██║     ███████║    ██████╔╝██║   ██║   ██║      ║
║     ██║  ██║██║     ██╔══██║    ██╔══██╗██║   ██║   ██║      ║
║     ██████╔╝╚██████╗██║  ██║    ██████╔╝╚██████╔╝   ██║      ║
║     ╚═════╝  ╚═════╝╚═╝  ╚═╝    ╚═════╝  ╚═════╝    ╚═╝      ║
║                                                              ║
║          Dollar Cost Averaging Inteligente v1.0              ║
║          5 Moedas + DCA Auto + Stop Profit Global            ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
{C.RESET}"""


DISCLAIMER = f"""
{C.YELLOW}{C.BOLD}⚠  AVISO DE RISCO{C.RESET}
{C.YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Trading de criptomoedas envolve risco significativo de perda.
Este bot e uma ferramenta automatizada, NAO uma garantia de lucro.
Use apenas capital que voce pode perder.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{C.RESET}
"""


# ============================================
# MENU DE SELECAO DE MODO
# ============================================
def select_mode() -> bool:
    """Seleciona Testnet ou Conta Real. Retorna True para testnet."""
    print(f"\n{C.BOLD}Selecione o modo de operacao:{C.RESET}\n")
    print(f"  {C.GREEN}[1]{C.RESET} TESTNET (simulacao segura)")
    print(f"  {C.RED}[2]{C.RESET} CONTA REAL (dinheiro real)")
    print()

    while True:
        choice = input(f"{C.CYAN}Escolha (1/2): {C.RESET}").strip()
        if choice == "1":
            print(f"\n{C.GREEN}✓ Modo TESTNET selecionado{C.RESET}")
            return True
        elif choice == "2":
            print(f"\n{C.RED}{C.BOLD}⚠  ATENCAO: Modo CONTA REAL{C.RESET}")
            print(f"{C.RED}Voce esta prestes a operar com dinheiro REAL.{C.RESET}")
            confirm = input(f"\nDigite {C.RED}{C.BOLD}CONFIRMO{C.RESET} para continuar: ").strip()
            if confirm == "CONFIRMO":
                print(f"\n{C.RED}✓ Modo CONTA REAL ativado{C.RESET}")
                return False
            else:
                print(f"{C.YELLOW}Cancelado. Voltando ao menu.{C.RESET}")
        else:
            print(f"{C.YELLOW}Opcao invalida.{C.RESET}")


# ============================================
# DASHBOARD
# ============================================
def render_dashboard(portfolio: PortfolioManager, dca: DCAEngine,
                     status: str = "Operando", last_action: str = ""):
    """Renderiza o dashboard completo no terminal."""
    try:
        clear()
        stats = portfolio.get_session_stats()
        positions = dca.get_all_summaries()
        now = datetime.now().strftime("%H:%M:%S")
        mode = "TESTNET" if portfolio.client.testnet else "REAL"
        mode_color = C.GREEN if portfolio.client.testnet else C.RED

        # === HEADER ===
        print(f"{C.CYAN}{C.BOLD}{'═' * 80}{C.RESET}")
        print(f"{C.CYAN}{C.BOLD}  DCA BOT INTELIGENTE v1.0  {C.RESET}"
              f"│ {mode_color}{C.BOLD}{mode}{C.RESET} "
              f"│ {C.DIM}{now}{C.RESET} "
              f"│ Status: {C.BOLD}{status}{C.RESET}")
        print(f"{C.CYAN}{C.BOLD}{'═' * 80}{C.RESET}")

        # === SALDO E P&L ===
        balance = stats["saldo_atual"]
        pnl_total = stats["pnl_total"]
        pnl_realizado = stats["pnl_realizado"]
        pnl_nao_realizado = stats["pnl_nao_realizado"]
        target = stats["target_tp"]
        progresso = stats["progresso_tp"]

        print(f"\n  {C.BOLD}SALDO{C.RESET}: {C.WHITE}{C.BOLD}{balance:.2f} USDT{C.RESET}"
              f"  │  {C.BOLD}P&L Sessao{C.RESET}: {pnl_color(pnl_total)}{C.BOLD}{format_pnl(pnl_total)} USDT{C.RESET}"
              f"  │  {C.BOLD}Ciclos{C.RESET}: {stats['ciclos']}")

        print(f"  {C.DIM}Realizado: {format_pnl(pnl_realizado)}{C.RESET}"
              f"  │  {C.DIM}Nao Realizado: {format_pnl(pnl_nao_realizado)}{C.RESET}"
              f"  │  {C.DIM}Win Rate: {stats['win_rate']:.0f}%{C.RESET}")

        # === BARRA DE PROGRESSO DO STOP PROFIT ===
        print(f"\n  {C.BOLD}STOP PROFIT GLOBAL{C.RESET} (target: {format_pnl(target)} USDT)")
        print(f"  {progress_bar(pnl_nao_realizado, target, 50)}")

        # === TABELA DE POSICOES ===
        print(f"\n  {C.BOLD}{'─' * 76}{C.RESET}")
        print(f"  {C.BOLD}{'MOEDA':<12} {'DIR':>5} {'QTD':>10} {'PRECO MEDIO':>12} "
              f"{'PRECO ATUAL':>12} {'P&L':>10} {'P&L%':>7} {'DCA':>5} {'STATUS':>8}{C.RESET}")
        print(f"  {C.DIM}{'─' * 76}{C.RESET}")

        if not positions:
            print(f"  {C.DIM}  Nenhuma posicao aberta. Aguardando selecao de moedas...{C.RESET}")
        else:
            # Encontrar max P&L para a barra
            max_pnl = max(abs(p.get("pnl", 0)) for p in positions) if positions else 1

            for p in positions:
                symbol = p["symbol"].replace("USDT", "")
                direction = p["direction"]
                dir_color = C.GREEN if direction == "LONG" else C.RED
                pnl = p.get("pnl", 0)
                pnl_pct = p.get("pnl_pct", 0)
                pc = pnl_color(pnl)
                dca_str = f"{p['dca_count']}/{p['max_dca']}"

                # Icone de status
                if pnl > 0:
                    status_icon = f"{C.GREEN}✓ LUCRO{C.RESET}"
                elif p["dca_count"] >= p["max_dca"]:
                    status_icon = f"{C.RED}MAX DCA{C.RESET}"
                elif p["dca_count"] > 0:
                    status_icon = f"{C.YELLOW}DCA #{p['dca_count']}{C.RESET}"
                else:
                    status_icon = f"{C.CYAN}ENTRY{C.RESET}"

                print(
                    f"  {C.BOLD}{symbol:<12}{C.RESET} "
                    f"{dir_color}{direction:>5}{C.RESET} "
                    f"{p['qty']:>10.4f} "
                    f"{format_price(p['avg_price']):>12} "
                    f"{format_price(p['current_price']):>12} "
                    f"{pc}{format_pnl(pnl):>10}{C.RESET} "
                    f"{pc}{pnl_pct:>6.2f}%{C.RESET} "
                    f"{C.YELLOW}{dca_str:>5}{C.RESET} "
                    f"{status_icon}"
                )

                # Barra visual do P&L
                print(f"  {'':>12} {bar_chart(pnl, max_pnl, 40)}")

        print(f"  {C.BOLD}{'─' * 76}{C.RESET}")

        # === RESUMO DO PORTFOLIO ===
        n_lucro = stats["posicoes_lucro"]
        n_perda = stats["posicoes_prejuizo"]
        n_total = stats["posicoes_ativas"]

        print(f"\n  {C.GREEN}● Lucro: {n_lucro}{C.RESET}"
              f"  {C.RED}● Perda: {n_perda}{C.RESET}"
              f"  {C.WHITE}● Total: {n_total}/{NUM_COINS}{C.RESET}"
              f"  │  {C.DIM}Duracao: {stats['duracao_min']:.0f} min{C.RESET}")

        # === STOP LOSS GLOBAL ===
        sl_limit = stats["limite_sl"]
        sl_pct = (pnl_nao_realizado / sl_limit * 100) if sl_limit != 0 else 0
        if sl_pct > 50:
            sl_color = C.RED + C.BOLD
        elif sl_pct > 25:
            sl_color = C.YELLOW
        else:
            sl_color = C.DIM
        print(f"  {sl_color}Stop Loss Global: {format_pnl(sl_limit)} USDT "
              f"({sl_pct:.0f}% usado){C.RESET}")

        # === ULTIMA ACAO ===
        if last_action:
            print(f"\n  {C.MAGENTA}Ultima acao: {last_action}{C.RESET}")

        # === HISTORICO DE CICLOS ===
        if stats["historico"]:
            print(f"\n  {C.BOLD}Historico de Ciclos:{C.RESET}")
            for h in stats["historico"][-5:]:
                hpc = pnl_color(h["pnl"])
                print(f"  {C.DIM}#{h['cycle']} {h['time']}{C.RESET} "
                      f"│ {hpc}{format_pnl(h['pnl'])} USDT{C.RESET} "
                      f"│ {h['reason']} │ {h['duration_min']:.1f}min")

        # === RODAPE ===
        print(f"\n{C.DIM}  [Ctrl+C] Parar bot  │  Atualizando a cada {DASHBOARD_INTERVAL}s{C.RESET}")
        print(f"{C.CYAN}{'═' * 80}{C.RESET}")

    except Exception as e:
        logger.error(f"Erro no dashboard: {e}")


# ============================================
# MENU PRINCIPAL
# ============================================
def show_menu():
    """Exibe o menu principal."""
    print(f"""
{C.BOLD}  MENU PRINCIPAL{C.RESET}
{C.DIM}  ─────────────────────────────────{C.RESET}
  {C.GREEN}[1]{C.RESET} Iniciar Bot (modo automatico)
  {C.CYAN}[2]{C.RESET} Ver saldo e posicoes
  {C.YELLOW}[3]{C.RESET} Selecionar moedas (preview)
  {C.MAGENTA}[4]{C.RESET} Configuracoes atuais
  {C.RED}[5]{C.RESET} Fechar todas as posicoes
  {C.DIM}[6]{C.RESET} Trocar modo (Testnet/Real)
  {C.DIM}[0]{C.RESET} Sair
""")


def show_balance(client: BinanceClientWrapper):
    """Mostra saldo e posicoes abertas."""
    try:
        balance = client.get_balance()
        positions = client.get_open_positions()

        print(f"\n  {C.BOLD}Saldo USDT:{C.RESET} {C.WHITE}{C.BOLD}{balance:.2f}{C.RESET}")
        print(f"  {C.BOLD}Posicoes abertas:{C.RESET} {len(positions)}")

        if positions:
            print(f"\n  {'Simbolo':<12} {'Lado':>6} {'Qtd':>12} {'Entrada':>12} {'P&L':>12}")
            print(f"  {'─' * 56}")
            for p in positions:
                pc = pnl_color(p["unrealized_pnl"])
                print(f"  {p['symbol']:<12} {p['side']:>6} {p['quantity']:>12.4f} "
                      f"{p['entry_price']:>12.4f} {pc}{p['unrealized_pnl']:>+12.4f}{C.RESET}")
    except Exception as e:
        print(f"\n  {C.RED}Erro: {e}{C.RESET}")

    input(f"\n  {C.DIM}Pressione Enter para voltar...{C.RESET}")


def show_config(use_testnet: bool):
    """Mostra configuracoes atuais."""
    mode = "TESTNET" if use_testnet else "CONTA REAL"
    tp_str = f"{GLOBAL_TAKE_PROFIT_USDT:.2f} USDT" if GLOBAL_TAKE_PROFIT_USDT > 0 else f"{GLOBAL_TAKE_PROFIT_PCT*100:.2f}%"

    print(f"""
  {C.BOLD}CONFIGURACOES ATUAIS{C.RESET}
  {C.DIM}─────────────────────────────────{C.RESET}
  Modo:              {C.BOLD}{mode}{C.RESET}
  Moedas:            {NUM_COINS} simultaneas
  Alavancagem:       {LEVERAGE}x
  Timeframe:         {TIMEFRAME}
  Capital/Moeda:     {CAPITAL_PER_COIN_PCT*100:.0f}% do saldo
  Entrada Inicial:   {INITIAL_ENTRY_PCT*100:.0f}% do capital da moeda

  {C.BOLD}STOP PROFIT GLOBAL{C.RESET}
  Target:            {tp_str}
  Stop Loss:         {GLOBAL_STOP_LOSS_PCT*100:.1f}%

  {C.BOLD}DCA LEVELS{C.RESET}
""")
    for i, (pct, cap) in enumerate(DCA_LEVELS):
        print(f"  Nivel {i+1}: Queda {pct*100:.1f}% -> Adiciona {cap*100:.0f}% do restante")

    print(f"\n  Max DCAs por moeda: {MAX_DCA_ORDERS}")
    input(f"\n  {C.DIM}Pressione Enter para voltar...{C.RESET}")


def preview_coins(client: BinanceClientWrapper):
    """Preview da selecao de moedas."""
    print(f"\n  {C.CYAN}Analisando mercado...{C.RESET}")
    coins = select_best_coins(client)

    if not coins:
        print(f"\n  {C.RED}Nenhuma moeda encontrada com criterios atuais.{C.RESET}")
    else:
        print(f"\n  {C.BOLD}{'#':<3} {'MOEDA':<12} {'DIR':>5} {'SCORE':>6} "
              f"{'RSI':>5} {'ATR%':>6} {'MOM%':>6} {'VOL':>5} {'PRECO':>12}{C.RESET}")
        print(f"  {'─' * 62}")
        for i, c in enumerate(coins, 1):
            dc = C.GREEN if c["direction"] == "LONG" else C.RED
            print(f"  {i:<3} {c['symbol']:<12} {dc}{c['direction']:>5}{C.RESET} "
                  f"{c['score']:>6.3f} {c.get('rsi', 0):>5.1f} "
                  f"{c.get('atr_pct', 0):>5.2f}% {c.get('momentum', 0):>+5.1f}% "
                  f"{c.get('vol_ratio', 0):>5.1f} {format_price(c.get('price', 0)):>12}")

    input(f"\n  {C.DIM}Pressione Enter para voltar...{C.RESET}")


def close_all_menu(client: BinanceClientWrapper):
    """Menu para fechar todas as posicoes."""
    print(f"\n  {C.RED}{C.BOLD}⚠  FECHAR TODAS AS POSICOES{C.RESET}")
    confirm = input(f"  Tem certeza? (s/n): ").strip().lower()
    if confirm == "s":
        closed = client.close_all_positions()
        print(f"\n  {C.GREEN}{closed} posicoes fechadas.{C.RESET}")
    else:
        print(f"  {C.YELLOW}Cancelado.{C.RESET}")
    input(f"\n  {C.DIM}Pressione Enter para voltar...{C.RESET}")


# ============================================
# BOT LOOP PRINCIPAL
# ============================================
def run_bot(client: BinanceClientWrapper, use_testnet: bool):
    """Loop principal do bot DCA."""
    logger.info("=== DCA Bot iniciado ===")

    # Inicializar componentes
    dca = DCAEngine(client)
    portfolio = PortfolioManager(dca, client)

    # Obter saldo
    balance = client.get_balance()
    portfolio.initialize(balance)

    print(f"\n  {C.GREEN}{C.BOLD}Bot iniciado!{C.RESET}")
    print(f"  Saldo: {balance:.2f} USDT")
    print(f"  Target: {format_pnl(portfolio.get_take_profit_target())} USDT")
    print(f"\n  {C.DIM}O dashboard vai aparecer em instantes...{C.RESET}")
    time.sleep(2)

    last_action = "Bot iniciado"
    status = "Selecionando moedas..."
    running = True
    last_dashboard = 0
    last_dca_check = 0
    last_price_update = 0
    last_selection_attempt = 0
    selection_cooldown = 30  # Esperar 30s entre tentativas de selecao

    try:
        while running:
            now = time.time()

            # === 1. SELECIONAR MOEDAS SE NECESSARIO ===
            needed = portfolio.needs_new_coins()
            if needed > 0 and (now - last_selection_attempt) >= selection_cooldown:
                last_selection_attempt = now
                try:
                    status = f"Selecionando {needed} moedas..."
                    render_dashboard(portfolio, dca, status, last_action)

                    coins = select_best_coins(client, needed)

                    if coins:
                        capital_per_coin = balance * CAPITAL_PER_COIN_PCT

                        for coin in coins:
                            symbol = coin["symbol"]
                            direction = coin["direction"]

                            # Pular se ja temos posicao
                            if symbol in dca.positions:
                                continue

                            pos = dca.open_position(symbol, direction, capital_per_coin)
                            if pos:
                                last_action = (
                                    f"ENTRADA {direction} {symbol} @ "
                                    f"{format_price(pos.avg_entry_price)} "
                                    f"(score: {coin['score']:.3f})"
                                )
                                time.sleep(0.5)  # Rate limit

                        # Atualizar saldo
                        portfolio.current_balance = client.get_balance_safe()
                        if portfolio.current_balance <= 0:
                            portfolio.current_balance = balance

                        status = "Operando"
                    else:
                        last_action = f"Nenhuma moeda encontrada. Tentando novamente em {selection_cooldown}s..."
                        status = f"Aguardando ({selection_cooldown}s)"
                        logger.info(last_action)

                except Exception as e:
                    logger.error(f"Erro na selecao: {e}")
                    last_action = f"Erro na selecao: {e}"
                    status = "Aguardando..."

            # === 2. ATUALIZAR PRECOS ===
            if now - last_price_update >= MONITOR_INTERVAL:
                try:
                    dca.update_all_prices()
                    last_price_update = now
                except Exception as e:
                    logger.error(f"Erro ao atualizar precos: {e}")

            # === 3. VERIFICAR DCA ===
            if now - last_dca_check >= DCA_CHECK_INTERVAL:
                try:
                    for symbol in list(dca.positions.keys()):
                        pos = dca.positions.get(symbol)
                        if not pos or pos.status != "ACTIVE":
                            continue

                        executed = dca.check_and_execute_dca(symbol)
                        if executed:
                            last_action = (
                                f"DCA #{pos.dca_count} {symbol} @ "
                                f"{format_price(pos.current_price)} | "
                                f"Novo medio: {format_price(pos.avg_entry_price)}"
                            )
                    last_dca_check = now
                except Exception as e:
                    logger.error(f"Erro no DCA check: {e}")

            # === 4. VERIFICAR STOP PROFIT GLOBAL ===
            if dca.count_active() > 0:
                try:
                    if portfolio.check_global_take_profit():
                        status = "STOP PROFIT!"
                        render_dashboard(portfolio, dca, status, last_action)
                        pnl = portfolio.execute_global_take_profit()
                        last_action = f"STOP PROFIT GLOBAL: {format_pnl(pnl)} USDT"
                        logger.info(last_action)

                        # Atualizar saldo para proximo ciclo
                        balance = client.get_balance_safe()
                        if balance > 0:
                            portfolio.current_balance = balance
                        else:
                            balance = portfolio.current_balance

                        status = "Novo ciclo..."
                        time.sleep(3)
                        continue

                    if portfolio.check_global_stop_loss():
                        status = "STOP LOSS!"
                        render_dashboard(portfolio, dca, status, last_action)
                        pnl = portfolio.execute_global_stop_loss()
                        last_action = f"STOP LOSS GLOBAL: {format_pnl(pnl)} USDT"
                        logger.info(last_action)

                        balance = client.get_balance_safe()
                        if balance > 0:
                            portfolio.current_balance = balance
                        else:
                            balance = portfolio.current_balance

                        status = "Recuperando..."
                        time.sleep(10)
                        continue

                except Exception as e:
                    logger.error(f"Erro no check global: {e}")

            # === 5. FECHAR MOEDAS INDIVIDUAIS COM LUCRO BOM ===
            # Se uma moeda individual tem +1% de lucro, fecha para garantir
            # e abre uma nova no lugar
            try:
                closed = portfolio.close_profitable_individual(min_profit_pct=1.0)
                for sym, pnl in closed:
                    last_action = f"FECHOU {sym} (lucro individual): {format_pnl(pnl)} USDT"
                    logger.info(last_action)
            except Exception as e:
                logger.error(f"Erro ao fechar individuais: {e}")

            # === 6. DASHBOARD ===
            if now - last_dashboard >= DASHBOARD_INTERVAL:
                try:
                    render_dashboard(portfolio, dca, status, last_action)
                    last_dashboard = now
                except Exception as e:
                    logger.error(f"Erro no dashboard: {e}")

            # Sleep curto para nao sobrecarregar
            time.sleep(1)

    except KeyboardInterrupt:
        print(f"\n\n  {C.YELLOW}{C.BOLD}Bot interrompido pelo usuario.{C.RESET}")
        logger.info("Bot interrompido pelo usuario")

        # Perguntar se quer fechar posicoes
        if dca.count_active() > 0:
            print(f"\n  {C.YELLOW}Voce tem {dca.count_active()} posicoes abertas.{C.RESET}")
            choice = input(f"  Fechar todas? (s/n): ").strip().lower()
            if choice == "s":
                pnl = dca.close_all(reason="USER_STOP")
                print(f"  {C.GREEN}Posicoes fechadas. P&L: {format_pnl(pnl)} USDT{C.RESET}")
            else:
                print(f"  {C.YELLOW}Posicoes mantidas abertas na exchange.{C.RESET}")

        # Resumo final
        stats = portfolio.get_session_stats()
        print(f"\n  {C.BOLD}RESUMO DA SESSAO{C.RESET}")
        print(f"  {'─' * 40}")
        print(f"  Duracao:       {stats['duracao_min']:.0f} minutos")
        print(f"  Ciclos:        {stats['ciclos']}")
        print(f"  P&L Total:     {pnl_color(stats['pnl_total'])}{format_pnl(stats['pnl_total'])} USDT{C.RESET}")
        print(f"  P&L Realizado: {pnl_color(stats['pnl_realizado'])}{format_pnl(stats['pnl_realizado'])} USDT{C.RESET}")
        print(f"  Saldo Final:   {stats['saldo_atual']:.2f} USDT")
        print()


# ============================================
# MAIN
# ============================================
def main():
    print(BANNER)
    print(DISCLAIMER)

    # Selecionar modo
    use_testnet = select_mode()

    # Validar config
    errors = validate_config(use_testnet)
    if errors:
        print(f"\n{C.RED}{C.BOLD}Erros de configuracao:{C.RESET}")
        for e in errors:
            print(f"  {C.RED}• {e}{C.RESET}")
        print(f"\n{C.YELLOW}Configure o arquivo .env e tente novamente.{C.RESET}")
        sys.exit(1)

    # Conectar
    api_key, api_secret = get_api_keys(use_testnet)
    print(f"\n  {C.CYAN}Conectando a Binance...{C.RESET}")

    try:
        client = BinanceClientWrapper(api_key, api_secret, testnet=use_testnet)
        balance = client.get_balance()
        print(f"  {C.GREEN}✓ Conectado! Saldo: {balance:.2f} USDT{C.RESET}")
    except Exception as e:
        print(f"\n  {C.RED}Erro ao conectar: {e}{C.RESET}")
        sys.exit(1)

    # Menu principal
    while True:
        clear()
        mode_str = f"{C.GREEN}TESTNET{C.RESET}" if use_testnet else f"{C.RED}CONTA REAL{C.RESET}"
        print(f"\n  {C.BOLD}DCA Bot Inteligente{C.RESET} │ {mode_str} │ Saldo: {balance:.2f} USDT")
        show_menu()

        choice = input(f"  {C.CYAN}Escolha: {C.RESET}").strip()

        if choice == "1":
            run_bot(client, use_testnet)
        elif choice == "2":
            show_balance(client)
        elif choice == "3":
            preview_coins(client)
        elif choice == "4":
            show_config(use_testnet)
        elif choice == "5":
            close_all_menu(client)
        elif choice == "6":
            use_testnet = select_mode()
            api_key, api_secret = get_api_keys(use_testnet)
            try:
                client = BinanceClientWrapper(api_key, api_secret, testnet=use_testnet)
                balance = client.get_balance()
                print(f"  {C.GREEN}✓ Reconectado! Saldo: {balance:.2f} USDT{C.RESET}")
                time.sleep(1)
            except Exception as e:
                print(f"  {C.RED}Erro: {e}{C.RESET}")
                time.sleep(2)
        elif choice == "0":
            print(f"\n  {C.DIM}Ate logo!{C.RESET}\n")
            sys.exit(0)
        else:
            print(f"  {C.YELLOW}Opcao invalida.{C.RESET}")
            time.sleep(1)


if __name__ == "__main__":
    main()