"""
binance_client.py - Wrapper Binance para DCA Bot.
Baseado na v1 com circuit breaker, retry e metodos especificos para DCA.
"""

import time
import threading
from enum import Enum
from functools import wraps
from typing import Optional, List, Dict, Any

from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException

from config import (
    CB_FAILURE_THRESHOLD, CB_RECOVERY_TIMEOUT, CB_HALF_OPEN_MAX_CALLS,
    RETRY_MAX_ATTEMPTS, RETRY_BASE_DELAY, RETRY_MAX_DELAY,
    LEVERAGE, TIMEFRAME
)
from utils.logger import logger


# ============================================
# CIRCUIT BREAKER
# ============================================
class CircuitState(Enum):
    CLOSED = "FECHADO"
    OPEN = "ABERTO"
    HALF_OPEN = "SEMI-ABERTO"


class CircuitBreaker:
    def __init__(self):
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0
        self.half_open_calls = 0
        self._lock = threading.Lock()

    def can_execute(self):
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            elif self.state == CircuitState.OPEN:
                if time.time() - self.last_failure_time >= CB_RECOVERY_TIMEOUT:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_calls = 0
                    self.success_count = 0
                    return True
                return False
            else:
                return self.half_open_calls < CB_HALF_OPEN_MAX_CALLS

    def record_success(self):
        with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                self.half_open_calls += 1
                if self.success_count >= CB_HALF_OPEN_MAX_CALLS:
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
            else:
                self.failure_count = max(0, self.failure_count - 1)

    def record_failure(self):
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
            elif self.failure_count >= CB_FAILURE_THRESHOLD:
                self.state = CircuitState.OPEN

    def force_reset(self):
        with self._lock:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self.half_open_calls = 0


def retry_with_backoff(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                return func(*args, **kwargs)
            except (BinanceAPIException, BinanceRequestException, Exception) as e:
                last_exc = e
                if isinstance(e, BinanceAPIException) and e.code in (-4120, -1116):
                    raise
                if attempt < RETRY_MAX_ATTEMPTS:
                    delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
                    time.sleep(delay)
                else:
                    logger.error(f"Todas as {RETRY_MAX_ATTEMPTS} tentativas falharam: {e}")
        raise last_exc
    return wrapper


# ============================================
# CLIENTE BINANCE
# ============================================
class BinanceClientWrapper:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.testnet = testnet
        self.circuit_breaker = CircuitBreaker()

        if testnet:
            self.client = Client(api_key, api_secret, testnet=True)
            self.client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
            logger.info("Conectado a Binance TESTNET")
        else:
            self.client = Client(api_key, api_secret)
            logger.info("Conectado a Binance MAINNET (CONTA REAL)")

        self._exchange_info_cache = None
        self._exchange_info_time = 0
        self._symbol_filters = {}

    def _check_circuit(self):
        if not self.circuit_breaker.can_execute():
            raise Exception("Circuit Breaker ABERTO - aguarde recuperacao")

    def _api_call(self, func, *args, bypass_circuit=False, **kwargs):
        if not bypass_circuit:
            self._check_circuit()
        try:
            result = func(*args, **kwargs)
            self.circuit_breaker.record_success()
            return result
        except Exception as e:
            if not bypass_circuit:
                self.circuit_breaker.record_failure()
            raise

    # --- Conta ---

    @retry_with_backoff
    def get_balance(self) -> float:
        balances = self._api_call(self.client.futures_account_balance)
        for b in balances:
            if b["asset"] == "USDT":
                return float(b["balance"])
        return 0.0

    def get_balance_safe(self) -> float:
        try:
            balances = self._api_call(
                self.client.futures_account_balance, bypass_circuit=True
            )
            for b in balances:
                if b["asset"] == "USDT":
                    return float(b["balance"])
        except Exception as e:
            logger.warning(f"Erro ao buscar saldo: {e}")
        return 0.0

    @retry_with_backoff
    def get_account(self) -> Dict:
        return self._api_call(self.client.futures_account)

    @retry_with_backoff
    def get_open_positions(self) -> List[Dict]:
        account = self._api_call(self.client.futures_account)
        positions = []
        for pos in account.get("positions", []):
            qty = float(pos.get("positionAmt", 0))
            if qty != 0:
                positions.append({
                    "symbol": pos["symbol"],
                    "side": "LONG" if qty > 0 else "SHORT",
                    "quantity": abs(qty),
                    "entry_price": float(pos.get("entryPrice", 0)),
                    "unrealized_pnl": float(pos.get("unrealizedProfit", 0)),
                    "leverage": int(pos.get("leverage", LEVERAGE)),
                    "notional": float(pos.get("notional", 0)),
                })
        return positions

    # --- Mercado ---

    @retry_with_backoff
    def get_klines(self, symbol: str, interval: str = None, limit: int = 100) -> List:
        if interval is None:
            interval = TIMEFRAME
        klines = self._api_call(
            self.client.futures_klines,
            symbol=symbol, interval=interval, limit=limit
        )
        if not klines:
            raise ValueError(f"Klines vazios para {symbol}")
        return klines

    @retry_with_backoff
    def get_ticker_24h(self, symbol: str = None) -> Any:
        if symbol:
            return self._api_call(self.client.futures_ticker, symbol=symbol)
        return self._api_call(self.client.futures_ticker)

    @retry_with_backoff
    def get_mark_price(self, symbol: str) -> float:
        data = self._api_call(self.client.futures_mark_price, symbol=symbol)
        price = float(data.get("markPrice", 0))
        if price <= 0:
            raise ValueError(f"Mark price invalido para {symbol}: {price}")
        return price

    def get_mark_price_safe(self, symbol: str) -> float:
        try:
            data = self._api_call(
                self.client.futures_mark_price, symbol=symbol, bypass_circuit=True
            )
            return float(data.get("markPrice", 0))
        except Exception:
            return 0.0

    @retry_with_backoff
    def get_orderbook_ticker(self, symbol: str) -> Dict:
        """Retorna best bid/ask para calcular spread."""
        data = self._api_call(
            self.client.futures_orderbook_ticker, symbol=symbol
        )
        return {
            "bid": float(data.get("bidPrice", 0)),
            "ask": float(data.get("askPrice", 0)),
        }

    # --- Exchange Info ---

    @retry_with_backoff
    def get_exchange_info(self) -> Dict:
        now = time.time()
        if self._exchange_info_cache and (now - self._exchange_info_time) < 300:
            return self._exchange_info_cache
        info = self._api_call(self.client.futures_exchange_info)
        self._exchange_info_cache = info
        self._exchange_info_time = now
        return info

    def get_symbol_filters(self, symbol: str) -> Dict:
        if symbol in self._symbol_filters:
            return self._symbol_filters[symbol]
        info = self.get_exchange_info()
        for s in info.get("symbols", []):
            if s["symbol"] == symbol:
                filters = {}
                for f in s.get("filters", []):
                    filters[f["filterType"]] = f
                filters["quantityPrecision"] = s.get("quantityPrecision", 3)
                filters["pricePrecision"] = s.get("pricePrecision", 2)
                self._symbol_filters[symbol] = filters
                return filters
        return {}

    def adjust_quantity(self, symbol: str, quantity: float) -> float:
        filters = self.get_symbol_filters(symbol)
        lot_filter = filters.get("LOT_SIZE", {})
        market_lot = filters.get("MARKET_LOT_SIZE", {})
        step_size = float(lot_filter.get("stepSize", 0.001))
        min_qty = float(lot_filter.get("minQty", 0.001))
        max_qty = float(market_lot.get("maxQty", 0))
        if max_qty <= 0:
            max_qty = float(lot_filter.get("maxQty", 999999999))
        if max_qty > 0 and quantity > max_qty:
            quantity = max_qty * 0.95
        precision = len(str(step_size).rstrip('0').split('.')[-1]) if '.' in str(step_size) else 0
        adjusted = max(min_qty, round(quantity - (quantity % step_size), precision))
        return adjusted

    def adjust_price(self, symbol: str, price: float) -> float:
        filters = self.get_symbol_filters(symbol)
        price_filter = filters.get("PRICE_FILTER", {})
        tick_size = float(price_filter.get("tickSize", 0.01))
        precision = len(str(tick_size).rstrip('0').split('.')[-1]) if '.' in str(tick_size) else 0
        adjusted = round(price - (price % tick_size), precision)
        return adjusted

    def get_min_notional(self, symbol: str) -> float:
        """Retorna o valor nocional minimo para um simbolo."""
        filters = self.get_symbol_filters(symbol)
        min_notional = filters.get("MIN_NOTIONAL", {})
        return float(min_notional.get("notional", 5.0))

    # --- Ordens ---

    @retry_with_backoff
    def set_leverage(self, symbol: str, leverage: int = None):
        if leverage is None:
            leverage = LEVERAGE
        try:
            self._api_call(
                self.client.futures_change_leverage,
                symbol=symbol, leverage=leverage
            )
        except BinanceAPIException as e:
            if e.code != -4028:
                raise

    @retry_with_backoff
    def set_margin_type(self, symbol: str, margin_type: str = "CROSSED"):
        try:
            self._api_call(
                self.client.futures_change_margin_type,
                symbol=symbol, marginType=margin_type
            )
        except BinanceAPIException as e:
            if e.code != -4046:
                raise

    @retry_with_backoff
    def place_market_order(self, symbol: str, side: str, quantity: float) -> Optional[Dict]:
        quantity = self.adjust_quantity(symbol, quantity)
        logger.info(f"Ordem MARKET: {side} {quantity} {symbol}")
        order = self._api_call(
            self.client.futures_create_order,
            symbol=symbol, side=side, type="MARKET", quantity=quantity
        )
        return order

    def close_position(self, symbol: str, side: str, quantity: float) -> Dict:
        """Fecha posicao de forma robusta. Retorna dict com resultado."""
        close_side = "SELL" if side == "LONG" else "BUY"
        result = {"success": False, "order_id": None}

        if self.circuit_breaker.state != CircuitState.CLOSED:
            self.circuit_breaker.force_reset()

        # Cancelar ordens pendentes
        try:
            self._api_call(
                self.client.futures_cancel_all_open_orders,
                symbol=symbol, bypass_circuit=True
            )
        except Exception:
            pass

        # Metodo 1: MARKET
        try:
            qty = self.adjust_quantity(symbol, quantity)
            order = self._api_call(
                self.client.futures_create_order,
                symbol=symbol, side=close_side, type="MARKET",
                quantity=qty, bypass_circuit=True
            )
            result["success"] = True
            result["order_id"] = order.get("orderId") if order else None
            logger.info(f"Posicao {symbol} fechada via MARKET")
            return result
        except Exception as e:
            logger.warning(f"Fechamento MARKET falhou {symbol}: {e}")

        # Metodo 2: reduceOnly
        try:
            qty = self.adjust_quantity(symbol, quantity)
            order = self._api_call(
                self.client.futures_create_order,
                symbol=symbol, side=close_side, type="MARKET",
                quantity=qty, reduceOnly="true", bypass_circuit=True
            )
            result["success"] = True
            result["order_id"] = order.get("orderId") if order else None
            logger.info(f"Posicao {symbol} fechada via reduceOnly")
            return result
        except Exception as e:
            logger.warning(f"Fechamento reduceOnly falhou {symbol}: {e}")

        return result

    def close_all_positions(self) -> int:
        if self.circuit_breaker.state != CircuitState.CLOSED:
            self.circuit_breaker.force_reset()
        try:
            positions = self.get_open_positions()
        except Exception:
            return 0
        closed = 0
        for pos in positions:
            r = self.close_position(pos["symbol"], pos["side"], pos["quantity"])
            if r.get("success"):
                closed += 1
        return closed

    def get_real_fill_price(self, symbol: str, order_id: int = None) -> Dict:
        """Consulta preco real de fill."""
        try:
            params = {"symbol": symbol, "limit": 10}
            if order_id:
                params["orderId"] = order_id
            trades = self._api_call(
                self.client.futures_account_trades,
                bypass_circuit=True, **params
            )
            if not trades:
                return {}
            total_qty = 0.0
            total_cost = 0.0
            total_commission = 0.0
            for t in trades[-5:]:
                qty = float(t.get("qty", 0))
                price = float(t.get("price", 0))
                commission = float(t.get("commission", 0))
                total_qty += qty
                total_cost += qty * price
                total_commission += abs(commission)
            avg_price = total_cost / total_qty if total_qty > 0 else 0
            return {
                "avg_price": avg_price,
                "total_qty": total_qty,
                "total_commission": total_commission,
            }
        except Exception as e:
            logger.warning(f"Erro ao buscar fill real de {symbol}: {e}")
            return {}

    def get_circuit_status(self) -> Dict:
        return {
            "estado": self.circuit_breaker.state.value,
            "falhas": self.circuit_breaker.failure_count,
        }
