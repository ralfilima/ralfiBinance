"""
portfolio_manager.py - Gestao de Portfolio com Stop Profit Global.

Logica:
  1. Monitora P&L GLOBAL (soma das 5 moedas)
  2. Quando P&L global >= target, fecha TUDO (stop profit global)
  3. Quando P&L global <= -stop_loss, fecha TUDO (emergencia)
  4. Moedas individuais que ficam positivas podem ser fechadas
     para liberar capital para novas entradas
  5. Ciclos: apos fechar tudo, seleciona novas 5 moedas e recomeça
"""

import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from dca_engine import DCAEngine
from binance_client import BinanceClientWrapper
from config import (
    GLOBAL_TAKE_PROFIT_PCT, GLOBAL_TAKE_PROFIT_USDT,
    GLOBAL_STOP_LOSS_PCT, NUM_COINS,
)
from utils.logger import logger
from utils.helpers import format_pnl, format_pct


class PortfolioManager:
    """Gerencia o portfolio global com stop profit."""

    def __init__(self, dca_engine: DCAEngine, client: BinanceClientWrapper):
        self.dca = dca_engine
        self.client = client
        self.initial_balance = 0.0
        self.current_balance = 0.0
        self.session_start = time.time()
        self.total_realized_pnl = 0.0
        self.total_cycles = 0
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.cycle_start_time = 0.0
        self.cycle_count = 0
        self.history: List[Dict] = []  # Historico de ciclos

    def initialize(self, balance: float):
        """Inicializa com saldo."""
        self.initial_balance = balance
        self.current_balance = balance
        self.cycle_start_time = time.time()
        logger.info(f"Portfolio inicializado com {balance:.2f} USDT")

    def get_take_profit_target(self) -> float:
        """Retorna o target de take profit em USDT."""
        if GLOBAL_TAKE_PROFIT_USDT > 0:
            return GLOBAL_TAKE_PROFIT_USDT
        return self.current_balance * GLOBAL_TAKE_PROFIT_PCT

    def get_stop_loss_limit(self) -> float:
        """Retorna o limite de stop loss em USDT (negativo)."""
        return -(self.current_balance * GLOBAL_STOP_LOSS_PCT)

    def check_global_take_profit(self) -> bool:
        """Verifica se o stop profit global foi atingido."""
        global_pnl = self.dca.get_global_pnl()
        target = self.get_take_profit_target()
        return global_pnl >= target

    def check_global_stop_loss(self) -> bool:
        """Verifica se o stop loss global foi atingido."""
        global_pnl = self.dca.get_global_pnl()
        limit = self.get_stop_loss_limit()
        return global_pnl <= limit

    def execute_global_take_profit(self) -> float:
        """Executa o stop profit global: fecha TUDO."""
        logger.info("=== STOP PROFIT GLOBAL ATINGIDO! Fechando todas as posicoes ===")
        pnl = self.dca.close_all(reason="GLOBAL_TAKE_PROFIT")
        self._record_cycle(pnl, "TAKE_PROFIT")
        return pnl

    def execute_global_stop_loss(self) -> float:
        """Executa o stop loss global: fecha TUDO."""
        logger.info("=== STOP LOSS GLOBAL ATINGIDO! Fechando todas as posicoes ===")
        pnl = self.dca.close_all(reason="GLOBAL_STOP_LOSS")
        self._record_cycle(pnl, "STOP_LOSS")
        return pnl

    def close_profitable_individual(self, min_profit_pct: float = 0.3) -> List[Tuple[str, float]]:
        """
        Fecha moedas individuais que estao no lucro acima de min_profit_pct.
        Retorna lista de (symbol, pnl) fechadas.
        Usado para liberar capital quando uma moeda ja deu lucro individual.
        """
        closed = []
        for symbol, pos in list(self.dca.positions.items()):
            if pos.is_profitable() and pos.unrealized_pnl_pct >= min_profit_pct:
                pnl = self.dca.close_position(symbol, reason="INDIVIDUAL_PROFIT")
                if pnl is not None:
                    closed.append((symbol, pnl))
                    self.total_realized_pnl += pnl
                    self.total_trades += 1
                    if pnl > 0:
                        self.winning_trades += 1
                    else:
                        self.losing_trades += 1
        return closed

    def _record_cycle(self, pnl: float, reason: str):
        """Registra um ciclo completo."""
        self.total_realized_pnl += pnl
        self.cycle_count += 1
        duration = time.time() - self.cycle_start_time

        # Atualizar saldo
        self.current_balance = self.client.get_balance_safe()
        if self.current_balance <= 0:
            self.current_balance = self.initial_balance + self.total_realized_pnl

        self.history.append({
            "cycle": self.cycle_count,
            "time": datetime.now().strftime("%H:%M:%S"),
            "pnl": pnl,
            "reason": reason,
            "duration_min": duration / 60,
            "balance_after": self.current_balance,
        })

        logger.info(
            f"Ciclo #{self.cycle_count} encerrado ({reason}): "
            f"P&L = {format_pnl(pnl)} USDT | "
            f"Duracao: {duration/60:.1f} min | "
            f"Saldo: {self.current_balance:.2f} USDT"
        )

        self.cycle_start_time = time.time()

    def get_session_stats(self) -> Dict:
        """Retorna estatisticas da sessao."""
        global_pnl = self.dca.get_global_pnl()
        target = self.get_take_profit_target()
        stop = self.get_stop_loss_limit()
        duration = time.time() - self.session_start

        return {
            "saldo_inicial": self.initial_balance,
            "saldo_atual": self.current_balance,
            "pnl_realizado": self.total_realized_pnl,
            "pnl_nao_realizado": global_pnl,
            "pnl_total": self.total_realized_pnl + global_pnl,
            "target_tp": target,
            "limite_sl": stop,
            "progresso_tp": (global_pnl / target * 100) if target > 0 else 0,
            "ciclos": self.cycle_count,
            "trades_total": self.total_trades,
            "trades_win": self.winning_trades,
            "trades_loss": self.losing_trades,
            "win_rate": (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0,
            "duracao_min": duration / 60,
            "posicoes_ativas": self.dca.count_active(),
            "posicoes_lucro": self.dca.count_profitable(),
            "posicoes_prejuizo": self.dca.count_negative(),
            "historico": self.history[-10:],  # Ultimos 10 ciclos
        }

    def needs_new_coins(self) -> int:
        """Retorna quantas moedas novas sao necessarias."""
        active = self.dca.count_active()
        return max(0, NUM_COINS - active)

    def get_portfolio_summary(self) -> List[Dict]:
        """Retorna resumo do portfolio para o dashboard."""
        return self.dca.get_all_summaries()
