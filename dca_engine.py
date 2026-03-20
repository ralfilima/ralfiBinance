"""
dca_engine.py - Motor DCA (Dollar Cost Averaging) Inteligente.

Cada moeda tem:
  - Entrada inicial pequena (25% do capital alocado)
  - Ate 5 niveis de DCA quando o preco cai
  - Preco medio vai baixando a cada DCA
  - Moeda so fecha quando esta no LUCRO (ou por stop global)

O DCA transforma perdas em oportunidades de compra.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime

from binance_client import BinanceClientWrapper
from config import (
    DCA_LEVELS, MAX_DCA_ORDERS, INITIAL_ENTRY_PCT,
    CAPITAL_PER_COIN_PCT, LEVERAGE,
)
from utils.logger import logger
from utils.helpers import format_price, format_pnl, format_pct


@dataclass
class DCAPosition:
    """Representa uma posicao com DCA."""
    symbol: str
    direction: str                    # LONG ou SHORT
    capital_allocated: float          # Capital total alocado para esta moeda
    capital_used: float = 0.0         # Capital ja usado em ordens
    total_quantity: float = 0.0       # Quantidade total acumulada
    avg_entry_price: float = 0.0      # Preco medio de entrada
    current_price: float = 0.0        # Preco atual
    unrealized_pnl: float = 0.0       # P&L nao realizado
    unrealized_pnl_pct: float = 0.0   # P&L % nao realizado
    dca_count: int = 0                # Quantos DCAs ja foram feitos
    orders: List[Dict] = field(default_factory=list)  # Historico de ordens
    total_commission: float = 0.0     # Comissoes totais
    created_at: float = 0.0           # Timestamp de criacao
    last_dca_price: float = 0.0       # Preco do ultimo DCA
    status: str = "ACTIVE"            # ACTIVE, CLOSING, CLOSED

    def add_order(self, price: float, quantity: float, commission: float = 0.0):
        """Adiciona uma ordem (entrada ou DCA) e recalcula preco medio."""
        old_cost = self.avg_entry_price * self.total_quantity
        new_cost = price * quantity
        self.total_quantity += quantity
        self.avg_entry_price = (old_cost + new_cost) / self.total_quantity if self.total_quantity > 0 else price
        self.capital_used += (price * quantity) / LEVERAGE
        self.total_commission += commission
        self.last_dca_price = price
        self.orders.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "type": "ENTRY" if self.dca_count == 0 and len(self.orders) == 0 else f"DCA-{self.dca_count}",
            "price": price,
            "quantity": quantity,
            "commission": commission,
            "avg_after": self.avg_entry_price,
        })

    def update_pnl(self, current_price: float):
        """Atualiza P&L com preco atual."""
        if current_price <= 0 or self.total_quantity <= 0:
            return
        self.current_price = current_price
        if self.direction == "LONG":
            self.unrealized_pnl = (current_price - self.avg_entry_price) * self.total_quantity
        else:
            self.unrealized_pnl = (self.avg_entry_price - current_price) * self.total_quantity
        # Descontar comissoes
        self.unrealized_pnl -= self.total_commission
        # Calcular %
        cost_basis = self.avg_entry_price * self.total_quantity
        self.unrealized_pnl_pct = (self.unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0

    def capital_remaining(self) -> float:
        """Capital ainda disponivel para DCA."""
        return max(0, self.capital_allocated - self.capital_used)

    def drop_from_entry(self) -> float:
        """Queda percentual do preco atual em relacao ao preco medio."""
        if self.avg_entry_price <= 0 or self.current_price <= 0:
            return 0.0
        if self.direction == "LONG":
            return (self.avg_entry_price - self.current_price) / self.avg_entry_price
        else:
            return (self.current_price - self.avg_entry_price) / self.avg_entry_price

    def drop_from_last_dca(self) -> float:
        """Queda percentual desde o ultimo DCA."""
        ref_price = self.last_dca_price if self.last_dca_price > 0 else self.avg_entry_price
        if ref_price <= 0 or self.current_price <= 0:
            return 0.0
        if self.direction == "LONG":
            return (ref_price - self.current_price) / ref_price
        else:
            return (self.current_price - ref_price) / ref_price

    def is_profitable(self) -> bool:
        """Retorna True se a posicao esta no lucro (descontando comissoes)."""
        return self.unrealized_pnl > 0

    def summary(self) -> Dict:
        """Retorna resumo da posicao."""
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "qty": self.total_quantity,
            "avg_price": self.avg_entry_price,
            "current_price": self.current_price,
            "pnl": self.unrealized_pnl,
            "pnl_pct": self.unrealized_pnl_pct,
            "dca_count": self.dca_count,
            "max_dca": MAX_DCA_ORDERS,
            "capital_used": self.capital_used,
            "capital_remaining": self.capital_remaining(),
            "commission": self.total_commission,
            "status": self.status,
        }


class DCAEngine:
    """Motor DCA que gerencia entradas e averaging down."""

    def __init__(self, client: BinanceClientWrapper):
        self.client = client
        self.positions: Dict[str, DCAPosition] = {}  # symbol -> DCAPosition

    def open_position(self, symbol: str, direction: str, capital: float,
                      initial_pct: float = None) -> Optional[DCAPosition]:
        """
        Abre uma nova posicao com entrada inicial.
        capital: capital total alocado para esta moeda
        initial_pct: % do capital para a 1a entrada
        """
        if symbol in self.positions:
            logger.warning(f"{symbol} ja tem posicao aberta")
            return None

        if initial_pct is None:
            initial_pct = INITIAL_ENTRY_PCT

        try:
            # Configurar leverage e margem
            self.client.set_leverage(symbol)
            self.client.set_margin_type(symbol)

            # Obter preco atual
            price = self.client.get_mark_price(symbol)
            if price <= 0:
                logger.error(f"Preco invalido para {symbol}: {price}")
                return None

            # Calcular quantidade da entrada inicial
            initial_capital = capital * initial_pct
            notional = initial_capital * LEVERAGE
            quantity = notional / price

            # Verificar nocional minimo
            min_notional = self.client.get_min_notional(symbol)
            if notional < min_notional:
                logger.warning(
                    f"{symbol}: nocional {notional:.2f} < minimo {min_notional}. "
                    f"Ajustando para minimo."
                )
                notional = min_notional * 1.1
                quantity = notional / price
                initial_capital = notional / LEVERAGE

            quantity = self.client.adjust_quantity(symbol, quantity)

            # Executar ordem
            side = "BUY" if direction == "LONG" else "SELL"
            order = self.client.place_market_order(symbol, side, quantity)

            if not order:
                logger.error(f"Ordem falhou para {symbol}")
                return None

            # Buscar preco real de fill
            real_price = price
            commission = 0.0
            time.sleep(0.3)
            fill_info = self.client.get_real_fill_price(symbol, order.get("orderId"))
            if fill_info and fill_info.get("avg_price", 0) > 0:
                real_price = fill_info["avg_price"]
                commission = fill_info.get("total_commission", 0)

            exec_qty = float(order.get("executedQty", quantity))
            if exec_qty > 0:
                quantity = exec_qty

            # Criar posicao DCA
            pos = DCAPosition(
                symbol=symbol,
                direction=direction,
                capital_allocated=capital,
                created_at=time.time(),
            )
            pos.add_order(real_price, quantity, commission)
            pos.update_pnl(real_price)

            self.positions[symbol] = pos

            logger.info(
                f"ENTRADA {direction} {symbol}: {quantity:.4f} @ {format_price(real_price)} | "
                f"Capital: {initial_capital:.2f}/{capital:.2f} USDT | "
                f"Comissao: {commission:.4f}"
            )

            return pos

        except Exception as e:
            logger.error(f"Erro ao abrir posicao {symbol}: {e}")
            return None

    def check_and_execute_dca(self, symbol: str) -> bool:
        """
        Verifica se e hora de fazer DCA em uma posicao.
        Retorna True se um DCA foi executado.
        """
        pos = self.positions.get(symbol)
        if not pos or pos.status != "ACTIVE":
            return False

        if pos.dca_count >= MAX_DCA_ORDERS:
            return False

        if pos.capital_remaining() <= 0:
            return False

        # Calcular queda desde o ultimo DCA (ou entrada)
        drop = pos.drop_from_last_dca()

        if drop <= 0:
            return False  # Preco nao caiu

        # Verificar qual nivel de DCA atingiu
        current_level = pos.dca_count
        if current_level >= len(DCA_LEVELS):
            return False

        trigger_pct, capital_pct = DCA_LEVELS[current_level]

        # Tolerancia de 0.01% para evitar problemas de float
        if drop < (trigger_pct - 0.0001):
            return False  # Ainda nao caiu o suficiente

        # EXECUTAR DCA!
        try:
            remaining_capital = pos.capital_remaining()
            dca_capital = remaining_capital * capital_pct
            notional = dca_capital * LEVERAGE
            price = pos.current_price

            if price <= 0:
                return False

            # Verificar nocional minimo
            min_notional = self.client.get_min_notional(symbol)
            if notional < min_notional:
                if remaining_capital * LEVERAGE >= min_notional:
                    notional = min_notional * 1.1
                    dca_capital = notional / LEVERAGE
                else:
                    logger.info(f"{symbol}: capital restante insuficiente para DCA")
                    return False

            quantity = notional / price
            quantity = self.client.adjust_quantity(symbol, quantity)

            side = "BUY" if pos.direction == "LONG" else "SELL"
            order = self.client.place_market_order(symbol, side, quantity)

            if not order:
                return False

            # Buscar fill real
            real_price = price
            commission = 0.0
            time.sleep(0.3)
            fill_info = self.client.get_real_fill_price(symbol, order.get("orderId"))
            if fill_info and fill_info.get("avg_price", 0) > 0:
                real_price = fill_info["avg_price"]
                commission = fill_info.get("total_commission", 0)

            exec_qty = float(order.get("executedQty", quantity))
            if exec_qty > 0:
                quantity = exec_qty

            old_avg = pos.avg_entry_price
            pos.dca_count += 1
            pos.add_order(real_price, quantity, commission)
            pos.update_pnl(real_price)

            logger.info(
                f"DCA #{pos.dca_count} {symbol}: +{quantity:.4f} @ {format_price(real_price)} | "
                f"Preco medio: {format_price(old_avg)} -> {format_price(pos.avg_entry_price)} | "
                f"Queda: {drop*100:.2f}% | Capital restante: {pos.capital_remaining():.2f}"
            )

            return True

        except Exception as e:
            logger.error(f"Erro ao executar DCA em {symbol}: {e}")
            return False

    def close_position(self, symbol: str, reason: str = "manual") -> Optional[float]:
        """
        Fecha uma posicao. Retorna o P&L realizado.
        """
        pos = self.positions.get(symbol)
        if not pos or pos.status == "CLOSED":
            return None

        pos.status = "CLOSING"

        try:
            result = self.client.close_position(
                symbol, pos.direction, pos.total_quantity
            )

            if result.get("success"):
                # Buscar P&L real
                time.sleep(0.3)
                fill_info = self.client.get_real_fill_price(symbol, result.get("order_id"))
                exit_commission = fill_info.get("total_commission", 0) if fill_info else 0

                # Calcular P&L com precos reais
                exit_price = fill_info.get("avg_price", pos.current_price) if fill_info else pos.current_price
                if pos.direction == "LONG":
                    pnl = (exit_price - pos.avg_entry_price) * pos.total_quantity
                else:
                    pnl = (pos.avg_entry_price - exit_price) * pos.total_quantity
                pnl -= (pos.total_commission + exit_commission)

                pos.status = "CLOSED"

                logger.info(
                    f"FECHOU {symbol} ({reason}): P&L = {format_pnl(pnl)} USDT | "
                    f"DCAs: {pos.dca_count} | Comissoes: {pos.total_commission + exit_commission:.4f}"
                )

                # Remover da lista de posicoes ativas
                del self.positions[symbol]
                return pnl
            else:
                pos.status = "ACTIVE"
                logger.error(f"Falha ao fechar {symbol}")
                return None

        except Exception as e:
            pos.status = "ACTIVE"
            logger.error(f"Erro ao fechar {symbol}: {e}")
            return None

    def close_all(self, reason: str = "global_stop") -> float:
        """Fecha todas as posicoes. Retorna P&L total."""
        total_pnl = 0.0
        symbols = list(self.positions.keys())
        for symbol in symbols:
            pnl = self.close_position(symbol, reason)
            if pnl is not None:
                total_pnl += pnl
        return total_pnl

    def update_all_prices(self):
        """Atualiza precos de todas as posicoes."""
        for symbol, pos in self.positions.items():
            if pos.status != "ACTIVE":
                continue
            try:
                price = self.client.get_mark_price_safe(symbol)
                if price > 0:
                    pos.update_pnl(price)
            except Exception:
                pass

    def get_global_pnl(self) -> float:
        """Retorna P&L global (soma de todas as posicoes)."""
        return sum(pos.unrealized_pnl for pos in self.positions.values())

    def get_global_pnl_pct(self, total_capital: float) -> float:
        """Retorna P&L global como % do capital total."""
        if total_capital <= 0:
            return 0.0
        return (self.get_global_pnl() / total_capital) * 100

    def get_all_summaries(self) -> List[Dict]:
        """Retorna resumo de todas as posicoes."""
        return [pos.summary() for pos in self.positions.values()]

    def count_active(self) -> int:
        """Retorna numero de posicoes ativas."""
        return sum(1 for p in self.positions.values() if p.status == "ACTIVE")

    def count_profitable(self) -> int:
        """Retorna numero de posicoes no lucro."""
        return sum(1 for p in self.positions.values() if p.is_profitable())

    def count_negative(self) -> int:
        """Retorna numero de posicoes no prejuizo."""
        return sum(1 for p in self.positions.values() if not p.is_profitable())

    def get_worst_position(self) -> Optional[DCAPosition]:
        """Retorna a posicao com maior prejuizo."""
        worst = None
        for pos in self.positions.values():
            if worst is None or pos.unrealized_pnl < worst.unrealized_pnl:
                worst = pos
        return worst

    def get_best_position(self) -> Optional[DCAPosition]:
        """Retorna a posicao com maior lucro."""
        best = None
        for pos in self.positions.values():
            if best is None or pos.unrealized_pnl > best.unrealized_pnl:
                best = pos
        return best
