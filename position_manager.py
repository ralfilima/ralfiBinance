"""
position_manager.py - Acompanhamento e fechamento de posicoes.
Inclui trailing stop, time stop e monitoramento de P&L.

v3.0 - Correcoes:
  - Chaves do dicionario SEM acentos (evita KeyError no dashboard)
  - Trailing stop mais conservador: ativacao +1.0%, callback 0.5%
  - Protecao contra current_price=0 (nao verifica SL/TP sem preco valido)
  - SL/TP por software e o modo primario (exchange e bonus)
  - Fallback para mark_price_critical quando circuit breaker esta aberto
  - Contador de falhas de update por posicao
  - Fechamento forcado apos N falhas consecutivas de update
"""

import time
import random
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from binance_client import BinanceClientWrapper
from risk_manager import RiskManager
from config import (
    TRAILING_ACTIVATION, TRAILING_CALLBACK,
    TIME_STOP_MIN, TIME_STOP_MAX, LEVERAGE
)
from utils.logger import logger
from utils.helpers import format_pnl, format_percent


# Maximo de falhas de update antes de forcar fechamento
MAX_UPDATE_FAILURES = 10


@dataclass
class Position:
    """Representa uma posicao aberta gerenciada pelo bot."""
    symbol: str
    side: str  # "LONG" ou "SHORT"
    quantity: float
    entry_price: float
    stop_loss: float
    take_profit: float
    open_time: float = field(default_factory=time.time)
    time_stop_seconds: float = 0.0
    
    # Trailing stop
    trailing_active: bool = False
    highest_pnl_pct: float = 0.0
    trailing_stop_price: float = 0.0
    
    # Ordens na exchange (podem ser None se SL/TP por software)
    sl_order_id: Optional[str] = None
    tp_order_id: Optional[str] = None
    has_exchange_sl: bool = False
    has_exchange_tp: bool = False
    
    # Status
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    pnl_percent: float = 0.0
    close_reason: str = ""
    
    # Controle de falhas de update
    update_failures: int = 0
    last_valid_price: float = 0.0
    price_updated: bool = False
    
    def __post_init__(self):
        if self.time_stop_seconds == 0:
            self.time_stop_seconds = random.uniform(
                TIME_STOP_MIN * 60, TIME_STOP_MAX * 60
            )
        # Inicializar last_valid_price com entry_price
        if self.last_valid_price == 0.0:
            self.last_valid_price = self.entry_price


class PositionManager:
    """Gerenciador de posicoes abertas."""
    
    def __init__(self, client: BinanceClientWrapper, risk_manager: RiskManager):
        self.client = client
        self.risk_manager = risk_manager
        self.positions: Dict[str, Position] = {}  # {symbol: Position}
        self.closed_positions: List[Dict] = []
    
    def add_position(self, position: Position):
        """Adiciona posicao ao gerenciamento."""
        self.positions[position.symbol] = position
        logger.info(
            f"Posicao adicionada: {position.side} {position.quantity} "
            f"{position.symbol} @ {position.entry_price:.4f} | "
            f"SL: {position.stop_loss:.4f} | TP: {position.take_profit:.4f} | "
            f"Time Stop: {position.time_stop_seconds/60:.0f}min | "
            f"SL/TP Exchange: {'SIM' if position.has_exchange_sl else 'SOFTWARE'}"
        )
    
    def remove_position(self, symbol: str) -> Optional[Position]:
        """Remove posicao do gerenciamento."""
        return self.positions.pop(symbol, None)
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """Retorna posicao pelo simbolo."""
        return self.positions.get(symbol)
    
    def get_open_count(self) -> int:
        """Retorna numero de posicoes abertas."""
        return len(self.positions)
    
    def get_open_symbols(self) -> List[str]:
        """Retorna lista de simbolos com posicao aberta."""
        return list(self.positions.keys())
    
    # --- Atualizacao de Posicoes ---
    
    def update_positions(self):
        """Atualiza precos e P&L de todas as posicoes."""
        for symbol, pos in list(self.positions.items()):
            try:
                # Tentar obter preco normal
                current_price = self.client.get_mark_price(symbol)
                pos.current_price = current_price
                pos.last_valid_price = current_price
                pos.price_updated = True
                pos.update_failures = 0  # Reset contador de falhas
                
                # Calcular P&L
                if pos.side == "LONG":
                    pos.unrealized_pnl = (current_price - pos.entry_price) * pos.quantity
                    pos.pnl_percent = ((current_price - pos.entry_price) / pos.entry_price) * 100
                else:  # SHORT
                    pos.unrealized_pnl = (pos.entry_price - current_price) * pos.quantity
                    pos.pnl_percent = ((pos.entry_price - current_price) / pos.entry_price) * 100
                
                # P&L com alavancagem
                pos.pnl_percent *= LEVERAGE
                
            except Exception as e:
                pos.price_updated = False
                pos.update_failures += 1
                
                # Tentar com bypass do circuit breaker
                try:
                    critical_price = self.client.get_mark_price_critical(symbol)
                    if critical_price > 0:
                        pos.current_price = critical_price
                        pos.last_valid_price = critical_price
                        pos.price_updated = True
                        pos.update_failures = 0
                        
                        if pos.side == "LONG":
                            pos.unrealized_pnl = (critical_price - pos.entry_price) * pos.quantity
                            pos.pnl_percent = ((critical_price - pos.entry_price) / pos.entry_price) * 100
                        else:
                            pos.unrealized_pnl = (pos.entry_price - critical_price) * pos.quantity
                            pos.pnl_percent = ((pos.entry_price - critical_price) / pos.entry_price) * 100
                        pos.pnl_percent *= LEVERAGE
                        
                        logger.info(f"Preco de {symbol} obtido via fallback critico: {critical_price}")
                        continue
                except Exception:
                    pass
                
                logger.warning(
                    f"Erro ao atualizar {symbol} (falha {pos.update_failures}/{MAX_UPDATE_FAILURES}): {e}"
                )
                
                # Se muitas falhas consecutivas, forcar fechamento
                if pos.update_failures >= MAX_UPDATE_FAILURES:
                    logger.error(
                        f"ALERTA: {symbol} sem update ha {pos.update_failures} ciclos! "
                        f"Forcando fechamento por seguranca."
                    )
                    pos.close_reason = "UPDATE_FAILURE"
    
    # --- Verificacoes de Fechamento ---
    
    def check_trailing_stop(self, pos: Position) -> bool:
        """
        Verifica e atualiza trailing stop.
        Retorna True se deve fechar a posicao.
        """
        # NAO verificar sem preco valido
        if not pos.price_updated or pos.current_price <= 0:
            return False
        
        pnl_pct = pos.pnl_percent / 100  # Converter para decimal
        
        # Ativar trailing se lucro minimo atingido
        if not pos.trailing_active and pnl_pct >= TRAILING_ACTIVATION:
            pos.trailing_active = True
            pos.highest_pnl_pct = pnl_pct
            
            # Calcular stop price do trailing
            if pos.side == "LONG":
                pos.trailing_stop_price = pos.current_price * (1 - TRAILING_CALLBACK)
            else:
                pos.trailing_stop_price = pos.current_price * (1 + TRAILING_CALLBACK)
            
            logger.info(
                f"Trailing Stop ATIVADO para {pos.symbol}: "
                f"lucro {pnl_pct*100:.2f}%, stop em {pos.trailing_stop_price:.4f}"
            )
        
        # Atualizar trailing se ativo
        if pos.trailing_active:
            if pnl_pct > pos.highest_pnl_pct:
                pos.highest_pnl_pct = pnl_pct
                
                # Ajustar stop price
                if pos.side == "LONG":
                    new_stop = pos.current_price * (1 - TRAILING_CALLBACK)
                    if new_stop > pos.trailing_stop_price:
                        pos.trailing_stop_price = new_stop
                else:
                    new_stop = pos.current_price * (1 + TRAILING_CALLBACK)
                    if new_stop < pos.trailing_stop_price:
                        pos.trailing_stop_price = new_stop
            
            # Verificar se trailing stop foi atingido
            if pos.side == "LONG" and pos.current_price <= pos.trailing_stop_price:
                pos.close_reason = "TRAILING_STOP"
                logger.info(
                    f"Trailing Stop atingido para {pos.symbol}: "
                    f"preco {pos.current_price:.4f} <= stop {pos.trailing_stop_price:.4f} | "
                    f"max lucro: {pos.highest_pnl_pct*100:.2f}%"
                )
                return True
            elif pos.side == "SHORT" and pos.current_price >= pos.trailing_stop_price:
                pos.close_reason = "TRAILING_STOP"
                logger.info(
                    f"Trailing Stop atingido para {pos.symbol}: "
                    f"preco {pos.current_price:.4f} >= stop {pos.trailing_stop_price:.4f} | "
                    f"max lucro: {pos.highest_pnl_pct*100:.2f}%"
                )
                return True
        
        return False
    
    def check_time_stop(self, pos: Position) -> bool:
        """Verifica se o time stop foi atingido."""
        elapsed = time.time() - pos.open_time
        if elapsed >= pos.time_stop_seconds:
            pos.close_reason = "TIME_STOP"
            logger.info(
                f"Time Stop atingido para {pos.symbol}: "
                f"{elapsed/60:.1f}min >= {pos.time_stop_seconds/60:.1f}min | "
                f"P&L: {pos.pnl_percent:+.2f}%"
            )
            return True
        return False
    
    def check_manual_sl_tp(self, pos: Position) -> bool:
        """
        Verifica SL/TP por software (modo primario).
        SOMENTE verifica se temos um preco valido e atualizado.
        """
        # PROTECAO CRITICA: nao verificar sem preco valido
        if not pos.price_updated or pos.current_price <= 0:
            return False
        
        if pos.side == "LONG":
            if pos.current_price <= pos.stop_loss:
                pos.close_reason = "STOP_LOSS"
                logger.info(
                    f"Stop Loss atingido para {pos.symbol} (LONG): "
                    f"preco {pos.current_price:.4f} <= SL {pos.stop_loss:.4f}"
                )
                return True
            if pos.current_price >= pos.take_profit:
                pos.close_reason = "TAKE_PROFIT"
                logger.info(
                    f"Take Profit atingido para {pos.symbol} (LONG): "
                    f"preco {pos.current_price:.4f} >= TP {pos.take_profit:.4f}"
                )
                return True
        else:  # SHORT
            if pos.current_price >= pos.stop_loss:
                pos.close_reason = "STOP_LOSS"
                logger.info(
                    f"Stop Loss atingido para {pos.symbol} (SHORT): "
                    f"preco {pos.current_price:.4f} >= SL {pos.stop_loss:.4f}"
                )
                return True
            if pos.current_price <= pos.take_profit:
                pos.close_reason = "TAKE_PROFIT"
                logger.info(
                    f"Take Profit atingido para {pos.symbol} (SHORT): "
                    f"preco {pos.current_price:.4f} <= TP {pos.take_profit:.4f}"
                )
                return True
        return False
    
    def check_update_failure(self, pos: Position) -> bool:
        """Verifica se a posicao deve ser fechada por falha de update."""
        if pos.close_reason == "UPDATE_FAILURE":
            return True
        return False
    
    # --- Ciclo de Monitoramento ---
    
    def monitor_cycle(self) -> List[Dict]:
        """
        Executa um ciclo de monitoramento de todas as posicoes.
        Retorna lista de posicoes fechadas neste ciclo.
        """
        closed_this_cycle = []
        
        # Atualizar precos
        self.update_positions()
        
        for symbol, pos in list(self.positions.items()):
            should_close = False
            
            # 0. Verificar falha de update (prioridade maxima)
            if self.check_update_failure(pos):
                should_close = True
            
            # 1. Verificar SL/TP por software (se nao tem na exchange)
            elif not pos.has_exchange_sl and self.check_manual_sl_tp(pos):
                should_close = True
            
            # 2. Verificar trailing stop
            elif self.check_trailing_stop(pos):
                should_close = True
            
            # 3. Verificar time stop
            elif self.check_time_stop(pos):
                should_close = True
            
            # 4. Verificar SL/TP manual como backup (mesmo com exchange)
            elif pos.has_exchange_sl and self.check_manual_sl_tp(pos):
                should_close = True
            
            # Fechar posicao se necessario
            if should_close:
                closed_info = self._close_position(pos)
                if closed_info:
                    closed_this_cycle.append(closed_info)
        
        return closed_this_cycle
    
    def _close_position(self, pos: Position) -> Optional[Dict]:
        """Fecha uma posicao e registra o resultado."""
        # Usar ultimo preco valido se current_price e 0
        exit_price = pos.current_price if pos.current_price > 0 else pos.last_valid_price
        
        # Recalcular P&L com preco valido
        if exit_price > 0 and exit_price != pos.current_price:
            if pos.side == "LONG":
                pos.unrealized_pnl = (exit_price - pos.entry_price) * pos.quantity
                pos.pnl_percent = ((exit_price - pos.entry_price) / pos.entry_price) * 100 * LEVERAGE
            else:
                pos.unrealized_pnl = (pos.entry_price - exit_price) * pos.quantity
                pos.pnl_percent = ((pos.entry_price - exit_price) / pos.entry_price) * 100 * LEVERAGE
        
        logger.info(
            f"Fechando {pos.symbol} ({pos.close_reason}): "
            f"P&L = {format_pnl(pos.unrealized_pnl)} | "
            f"Duracao: {(time.time() - pos.open_time)/60:.1f}min"
        )
        
        # Cancelar ordens pendentes e fechar
        success = self.client.close_position_robust(
            pos.symbol, pos.side, pos.quantity
        )
        
        if success:
            # Registrar trade
            trade_info = {
                "symbol": pos.symbol,
                "side": pos.side,
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "pnl": pos.unrealized_pnl,
                "pnl_percent": pos.pnl_percent,
                "reason": pos.close_reason,
                "duration_min": (time.time() - pos.open_time) / 60,
                "trailing_was_active": pos.trailing_active,
            }
            
            self.risk_manager.record_trade(trade_info)
            self.closed_positions.append(trade_info)
            self.remove_position(pos.symbol)
            
            return trade_info
        else:
            logger.error(f"FALHA ao fechar posicao {pos.symbol}!")
            # Tentar novamente no proximo ciclo, mas resetar
            pos.update_failures = 0
            pos.close_reason = ""
            return None
    
    def close_all(self) -> int:
        """Fecha todas as posicoes gerenciadas."""
        count = 0
        for symbol, pos in list(self.positions.items()):
            pos.close_reason = "MANUAL_CLOSE"
            result = self._close_position(pos)
            if result:
                count += 1
        return count
    
    def close_by_symbol(self, symbol: str) -> bool:
        """Fecha posicao de um simbolo especifico."""
        pos = self.positions.get(symbol)
        if not pos:
            logger.warning(f"Nenhuma posicao encontrada para {symbol}")
            return False
        
        pos.close_reason = "MANUAL_CLOSE"
        result = self._close_position(pos)
        return result is not None
    
    # --- Informacoes ---
    
    def get_positions_summary(self) -> List[Dict]:
        """Retorna resumo de todas as posicoes abertas.
        IMPORTANTE: Chaves SEM acentos para compatibilidade com dashboard."""
        summary = []
        for symbol, pos in self.positions.items():
            elapsed_min = (time.time() - pos.open_time) / 60
            time_remaining = max(0, (pos.time_stop_seconds - (time.time() - pos.open_time)) / 60)
            
            sl_mode = "EXC" if pos.has_exchange_sl else "SW"
            
            summary.append({
                "Simbolo": symbol,
                "Direcao": pos.side,
                "Qtd": f"{pos.quantity:.4f}",
                "Entrada": f"{pos.entry_price:.4f}",
                "Atual": f"{pos.current_price:.4f}" if pos.current_price > 0 else "N/A",
                "P&L": f"{pos.unrealized_pnl:+.2f}",
                "P&L%": f"{pos.pnl_percent:+.2f}%",
                "SL": f"{pos.stop_loss:.4f}",
                "TP": f"{pos.take_profit:.4f}",
                "Trailing": "SIM" if pos.trailing_active else "NAO",
                "Tempo": f"{elapsed_min:.0f}min",
                "Restante": f"{time_remaining:.0f}min",
                "SL/TP": sl_mode,
                "Updates": "OK" if pos.update_failures == 0 else f"FALHA({pos.update_failures})",
            })
        return summary
    
    def get_total_unrealized_pnl(self) -> float:
        """Retorna P&L total nao realizado."""
        return sum(pos.unrealized_pnl for pos in self.positions.values())
