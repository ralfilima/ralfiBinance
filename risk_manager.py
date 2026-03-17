"""
risk_manager.py - Gestao de risco avancada.
Position sizing baseado em ATR, limites globais de risco.

v3.0 - Correcoes:
  - ATR minimo obrigatorio (evita posicoes gigantes com ATR=0)
  - Cap de position size em valor nocional (max 20% do saldo)
  - Stop distance minimo de 0.3% do preco (evita SL/TP colados)
  - Log detalhado de cada calculo de position sizing
  - Tracking de pause_time para auto-resume
"""

import time
from typing import Dict, Optional, Tuple
from config import (
    RISK_PER_TRADE, MAX_OPEN_POSITIONS, MAX_DAILY_LOSS_PERCENT,
    MAX_CONSECUTIVE_LOSSES, MAX_DRAWDOWN_PERCENT, ATR_MULTIPLIER,
    LEVERAGE
)
from utils.logger import logger


# Limites de seguranca para position sizing
MIN_STOP_DISTANCE_PCT = 0.003   # 0.3% minimo de distancia do SL
MAX_NOTIONAL_PCT = 0.20         # Max 20% do saldo por posicao (antes da alavancagem)
MIN_ATR_PCT = 0.001             # ATR minimo = 0.1% do preco


class RiskManager:
    """Gerenciador de risco global do bot."""
    
    def __init__(self, initial_balance: float):
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.daily_start_balance = initial_balance
        self.peak_balance = initial_balance
        
        # Contadores
        self.consecutive_losses = 0
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.daily_pnl = 0.0
        self.session_pnl = 0.0
        
        # Estado
        self.is_paused = False
        self.pause_reason = ""
        self.pause_time = 0.0
        self.daily_loss_triggered = False
        self.drawdown_triggered = False
        
        # Historico
        self.trade_history = []
    
    def update_balance(self, new_balance: float):
        """Atualiza saldo atual e metricas relacionadas."""
        self.current_balance = new_balance
        if new_balance > self.peak_balance:
            self.peak_balance = new_balance
    
    def reset_daily(self):
        """Reseta contadores diarios."""
        self.daily_start_balance = self.current_balance
        self.daily_pnl = 0.0
        self.daily_loss_triggered = False
        self.consecutive_losses = 0
        logger.info("Contadores diarios resetados")
    
    # --- Position Sizing ---
    
    def calculate_position_size(self, balance: float, atr: float,
                                 price: float, leverage: int = None) -> Tuple[float, float, float]:
        """
        Calcula tamanho da posicao baseado em ATR com protecoes.
        
        Protecoes:
          1. ATR minimo = 0.1% do preco (evita qty gigante)
          2. Stop distance minimo = 0.3% do preco
          3. Valor nocional max = 20% do saldo (antes de alavancagem)
          4. Margem max = 90% da margem disponivel
        
        Returns:
            (quantidade, stop_distance, capital_arriscado)
        """
        if leverage is None:
            leverage = LEVERAGE
        
        if price <= 0:
            logger.warning("Preco invalido para position sizing")
            return 0.0, 0.0, 0.0
        
        # === PROTECAO 1: ATR minimo ===
        min_atr = price * MIN_ATR_PCT
        if atr <= 0 or atr < min_atr:
            logger.warning(
                f"ATR muito baixo ({atr:.6f}) para preco {price:.4f}. "
                f"Usando ATR minimo: {min_atr:.6f} ({MIN_ATR_PCT*100:.1f}% do preco)"
            )
            atr = min_atr
        
        capital_arriscado = balance * RISK_PER_TRADE
        stop_distance = atr * ATR_MULTIPLIER
        
        # === PROTECAO 2: Stop distance minimo ===
        min_stop = price * MIN_STOP_DISTANCE_PCT
        if stop_distance < min_stop:
            logger.info(
                f"Stop distance ({stop_distance:.6f}) abaixo do minimo. "
                f"Ajustando para {min_stop:.6f} ({MIN_STOP_DISTANCE_PCT*100:.1f}% do preco)"
            )
            stop_distance = min_stop
        
        # Quantidade base
        quantity = capital_arriscado / stop_distance
        
        # === PROTECAO 3: Cap de valor nocional ===
        notional_value = quantity * price
        max_notional = balance * MAX_NOTIONAL_PCT
        
        if notional_value > max_notional:
            old_qty = quantity
            quantity = max_notional / price
            logger.info(
                f"Position size reduzido pelo cap nocional: "
                f"{old_qty:.4f} -> {quantity:.4f} "
                f"(nocional: {notional_value:.2f} -> {max_notional:.2f} USDT)"
            )
        
        # === PROTECAO 4: Limite de margem com alavancagem ===
        margin_required = (quantity * price) / leverage
        max_margin = balance * 0.9  # 90% do saldo
        
        if margin_required > max_margin:
            old_qty = quantity
            quantity = (max_margin * leverage) / price
            logger.info(
                f"Position size reduzido pela margem: "
                f"{old_qty:.4f} -> {quantity:.4f}"
            )
        
        # Garantir quantidade minima
        min_notional = 5.0  # Minimo de 5 USDT
        if quantity * price < min_notional:
            quantity = min_notional / price
        
        # Log detalhado
        final_notional = quantity * price
        final_margin = final_notional / leverage
        sl_pct = (stop_distance / price) * 100
        
        logger.info(
            f"Position sizing: qty={quantity:.4f} | preco={price:.4f} | "
            f"nocional={final_notional:.2f} USDT | margem={final_margin:.2f} USDT | "
            f"ATR={atr:.6f} | stop_dist={stop_distance:.6f} ({sl_pct:.2f}%) | "
            f"risco={capital_arriscado:.2f} USDT"
        )
        
        return quantity, stop_distance, capital_arriscado
    
    def calculate_sl_tp(self, entry_price: float, stop_distance: float,
                         direction: str) -> Tuple[float, float]:
        """
        Calcula precos de Stop Loss e Take Profit.
        
        TP = 2x a distancia do SL (risk/reward de 1:2)
        
        Returns:
            (stop_loss_price, take_profit_price)
        """
        # Garantir stop distance minimo
        min_stop = entry_price * MIN_STOP_DISTANCE_PCT
        if stop_distance < min_stop:
            stop_distance = min_stop
        
        if direction == "LONG":
            sl = entry_price - stop_distance
            tp = entry_price + (stop_distance * 2)  # R:R 1:2
        else:  # SHORT
            sl = entry_price + stop_distance
            tp = entry_price - (stop_distance * 2)
        
        sl_pct = abs(entry_price - sl) / entry_price * 100
        tp_pct = abs(tp - entry_price) / entry_price * 100
        
        logger.info(
            f"SL/TP calculado: entry={entry_price:.4f} | "
            f"SL={sl:.4f} ({sl_pct:.2f}%) | TP={tp:.4f} ({tp_pct:.2f}%) | "
            f"dir={direction}"
        )
        
        return sl, tp
    
    # --- Verificacoes de Risco Global ---
    
    def can_open_position(self, current_positions: int) -> Tuple[bool, str]:
        """
        Verifica se e permitido abrir nova posicao.
        
        Returns:
            (permitido, motivo)
        """
        # Verificar pausa
        if self.is_paused:
            return False, f"Bot pausado: {self.pause_reason}"
        
        # Verificar drawdown maximo
        if self.check_max_drawdown():
            return False, "Drawdown maximo global atingido"
        
        # Verificar perda diaria
        if self.check_daily_loss():
            return False, "Perda diaria maxima atingida"
        
        # Verificar perdas consecutivas
        if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            if not self.is_paused:
                self.is_paused = True
                self.pause_time = time.time()
                self.pause_reason = f"{self.consecutive_losses} perdas consecutivas"
                logger.warning(f"PAUSA: {self.pause_reason}")
            return False, f"Pausa: {self.consecutive_losses} perdas consecutivas"
        
        # Verificar maximo de posicoes
        if current_positions >= MAX_OPEN_POSITIONS:
            return False, f"Maximo de {MAX_OPEN_POSITIONS} posicoes atingido"
        
        return True, "OK"
    
    def check_daily_loss(self) -> bool:
        """Verifica se a perda diaria maxima foi atingida."""
        if self.daily_start_balance <= 0:
            return False
        
        daily_loss_pct = (self.daily_start_balance - self.current_balance) / self.daily_start_balance
        
        if daily_loss_pct >= MAX_DAILY_LOSS_PERCENT:
            if not self.daily_loss_triggered:
                self.daily_loss_triggered = True
                self.is_paused = True
                self.pause_time = time.time()
                self.pause_reason = f"Perda diaria de {daily_loss_pct*100:.2f}% (limite: {MAX_DAILY_LOSS_PERCENT*100:.1f}%)"
                logger.warning(f"ALERTA: {self.pause_reason}")
            return True
        return False
    
    def check_max_drawdown(self) -> bool:
        """Verifica se o drawdown maximo global foi atingido."""
        if self.peak_balance <= 0:
            return False
        
        drawdown = (self.peak_balance - self.current_balance) / self.peak_balance
        
        if drawdown >= MAX_DRAWDOWN_PERCENT:
            if not self.drawdown_triggered:
                self.drawdown_triggered = True
                self.is_paused = True
                self.pause_time = time.time()
                self.pause_reason = f"Drawdown global de {drawdown*100:.2f}% (limite: {MAX_DRAWDOWN_PERCENT*100:.1f}%)"
                logger.warning(f"ALERTA CRITICO: {self.pause_reason}")
            return True
        return False
    
    # --- Registro de Trades ---
    
    def record_trade(self, trade_info: Dict):
        """Registra resultado de um trade."""
        pnl = trade_info.get("pnl", 0)
        
        self.total_trades += 1
        self.daily_pnl += pnl
        self.session_pnl += pnl
        
        if pnl >= 0:
            self.winning_trades += 1
            self.consecutive_losses = 0
        else:
            self.losing_trades += 1
            self.consecutive_losses += 1
        
        self.trade_history.append({
            **trade_info,
            "timestamp": time.time(),
            "balance_after": self.current_balance + pnl,
        })
        
        self.update_balance(self.current_balance + pnl)
        
        logger.info(
            f"Trade registrado: {trade_info.get('symbol', 'N/A')} | "
            f"P&L: {pnl:+.2f} USDT | "
            f"Consecutivas: {self.consecutive_losses} | "
            f"Total: {self.total_trades} | "
            f"Win Rate: {self.get_win_rate():.1f}%"
        )
    
    # --- Metricas ---
    
    def get_current_drawdown(self) -> float:
        """Retorna drawdown atual em percentual."""
        if self.peak_balance <= 0:
            return 0.0
        return ((self.peak_balance - self.current_balance) / self.peak_balance) * 100
    
    def get_daily_loss_percent(self) -> float:
        """Retorna perda diaria em percentual."""
        if self.daily_start_balance <= 0:
            return 0.0
        return ((self.daily_start_balance - self.current_balance) / self.daily_start_balance) * 100
    
    def get_win_rate(self) -> float:
        """Retorna win rate em percentual."""
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100
    
    def get_risk_summary(self) -> Dict:
        """Retorna resumo completo de risco."""
        return {
            "Saldo Atual": f"{self.current_balance:.2f} USDT",
            "P&L Diario": f"{self.daily_pnl:+.2f} USDT",
            "P&L Sessao": f"{self.session_pnl:+.2f} USDT",
            "Drawdown Atual": f"{self.get_current_drawdown():.2f}%",
            "Drawdown Max": f"{MAX_DRAWDOWN_PERCENT*100:.1f}%",
            "Perda Diaria": f"{self.get_daily_loss_percent():.2f}% / {MAX_DAILY_LOSS_PERCENT*100:.1f}%",
            "Perdas Consecutivas": f"{self.consecutive_losses} / {MAX_CONSECUTIVE_LOSSES}",
            "Total Trades": self.total_trades,
            "Win Rate": f"{self.get_win_rate():.1f}%",
            "Status": "PAUSADO" if self.is_paused else "ATIVO",
        }
    
    def force_resume(self):
        """Forca retomada do bot (reset de pausa)."""
        self.is_paused = False
        self.pause_reason = ""
        self.pause_time = 0.0
        self.consecutive_losses = 0
        logger.info("Bot retomado (pausa resetada)")
