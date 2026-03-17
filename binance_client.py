"""
binance_client.py - Wrapper para a API da Binance com circuit breaker,
retry com exponential backoff e sanity checks.

v2.0 - Correções:
  - Circuit Breaker inteligente: não conta falhas de SL/TP
  - Bypass do circuit breaker para operações críticas (fechar posição)
  - SL/TP por software quando exchange não suporta (Testnet)
  - URL da Testnet atualizada para demo-fapi.binance.com
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
    """Implementação de Circuit Breaker para proteção contra falhas da API."""
    
    def __init__(self):
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0
        self.half_open_calls = 0
        self._lock = threading.Lock()
    
    def can_execute(self) -> bool:
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            elif self.state == CircuitState.OPEN:
                if time.time() - self.last_failure_time >= CB_RECOVERY_TIMEOUT:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_calls = 0
                    self.success_count = 0
                    logger.info("Circuit Breaker: SEMI-ABERTO - testando recuperação")
                    return True
                return False
            else:  # HALF_OPEN
                return self.half_open_calls < CB_HALF_OPEN_MAX_CALLS
    
    def record_success(self):
        with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                self.half_open_calls += 1
                if self.success_count >= CB_HALF_OPEN_MAX_CALLS:
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.success_count = 0
                    logger.info("Circuit Breaker: FECHADO - API recuperada")
            else:
                # Decrementar falhas gradualmente em operação normal
                self.failure_count = max(0, self.failure_count - 1)
    
    def record_failure(self):
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                logger.warning("Circuit Breaker: ABERTO - falha durante recuperação")
            elif self.failure_count >= CB_FAILURE_THRESHOLD:
                self.state = CircuitState.OPEN
                logger.warning(
                    f"Circuit Breaker: ABERTO - {self.failure_count} falhas consecutivas"
                )
    
    def force_reset(self):
        """Força reset do circuit breaker (para operações críticas)."""
        with self._lock:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self.half_open_calls = 0
            logger.info("Circuit Breaker: RESET FORÇADO para operação crítica")
    
    def get_status(self) -> Dict[str, Any]:
        return {
            "estado": self.state.value,
            "falhas": self.failure_count,
            "ultima_falha": time.strftime(
                "%H:%M:%S", time.localtime(self.last_failure_time)
            ) if self.last_failure_time else "N/A"
        }


# ============================================
# RETRY COM EXPONENTIAL BACKOFF
# ============================================
def retry_with_backoff(func):
    """Decorator para retry com exponential backoff."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        last_exception = None
        for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
            try:
                return func(*args, **kwargs)
            except (BinanceAPIException, BinanceRequestException, Exception) as e:
                last_exception = e
                # Não fazer retry para erros de tipo de ordem não suportado
                if isinstance(e, BinanceAPIException) and e.code in (-4120, -1116):
                    raise
                if attempt < RETRY_MAX_ATTEMPTS:
                    delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), RETRY_MAX_DELAY)
                    logger.warning(
                        f"Tentativa {attempt}/{RETRY_MAX_ATTEMPTS} falhou: {e}. "
                        f"Retentando em {delay:.1f}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"Todas as {RETRY_MAX_ATTEMPTS} tentativas falharam: {e}")
        raise last_exception
    return wrapper


# ============================================
# CLIENTE BINANCE
# ============================================
class BinanceClientWrapper:
    """Wrapper para a API da Binance com proteções integradas."""
    
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.testnet = testnet
        self.circuit_breaker = CircuitBreaker()
        
        # Flag: se SL/TP na exchange é suportado
        self.exchange_sl_tp_supported = True
        
        if testnet:
            self.client = Client(api_key, api_secret, testnet=True)
            # URL atualizada da Testnet
            self.client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
            logger.info("Conectado à Binance TESTNET")
        else:
            self.client = Client(api_key, api_secret)
            logger.info("Conectado à Binance MAINNET (CONTA REAL)")
        
        self._exchange_info_cache = None
        self._exchange_info_time = 0
        self._symbol_filters = {}
    
    def _check_circuit(self):
        """Verifica se o circuit breaker permite a chamada."""
        if not self.circuit_breaker.can_execute():
            raise Exception(
                "Circuit Breaker ABERTO - API temporariamente indisponível. "
                f"Aguarde {CB_RECOVERY_TIMEOUT}s para recuperação."
            )
    
    def _api_call(self, func, *args, bypass_circuit=False, **kwargs):
        """Executa chamada de API com circuit breaker."""
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
    
    def _api_call_no_cb_count(self, func, *args, **kwargs):
        """Executa chamada de API SEM contar falhas no circuit breaker.
        Usado para SL/TP que podem falhar legitimamente na Testnet."""
        self._check_circuit()
        try:
            result = func(*args, **kwargs)
            self.circuit_breaker.record_success()
            return result
        except Exception:
            # NÃO conta falha no circuit breaker
            raise
    
    # --- Informações da Conta ---
    
    @retry_with_backoff
    def get_futures_balance(self) -> float:
        """Retorna saldo disponível em USDT nos futuros."""
        balances = self._api_call(self.client.futures_account_balance)
        for b in balances:
            if b["asset"] == "USDT":
                return float(b["balance"])
        return 0.0
    
    @retry_with_backoff
    def get_futures_account(self) -> Dict:
        """Retorna informações da conta de futuros."""
        return self._api_call(self.client.futures_account)
    
    @retry_with_backoff
    def get_open_positions(self) -> List[Dict]:
        """Retorna posições abertas (com quantidade != 0)."""
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
                    "margin_type": pos.get("marginType", "cross"),
                })
        return positions
    
    # --- Dados de Mercado ---
    
    @retry_with_backoff
    def get_klines(self, symbol: str, interval: str = None, limit: int = 100) -> List:
        """Retorna candles (klines) com sanity checks."""
        if interval is None:
            interval = TIMEFRAME
        
        klines = self._api_call(
            self.client.futures_klines,
            symbol=symbol, interval=interval, limit=limit
        )
        
        # Sanity checks
        if not klines or len(klines) == 0:
            raise ValueError(f"Klines vazios para {symbol}")
        
        for k in klines:
            if float(k[2]) <= 0 or float(k[3]) <= 0:  # high, low
                raise ValueError(f"Preço inválido detectado em klines de {symbol}")
        
        return klines
    
    @retry_with_backoff
    def get_historical_klines(self, symbol: str, interval: str, start_str: str, end_str: str = None) -> List:
        """Retorna klines históricos para backtesting."""
        klines = self._api_call(
            self.client.futures_historical_klines,
            symbol=symbol, interval=interval,
            start_str=start_str, end_str=end_str
        )
        return klines
    
    @retry_with_backoff
    def get_ticker_24h(self, symbol: str = None) -> Any:
        """Retorna ticker 24h."""
        if symbol:
            return self._api_call(self.client.futures_ticker, symbol=symbol)
        return self._api_call(self.client.futures_ticker)
    
    @retry_with_backoff
    def get_all_tickers(self) -> List[Dict]:
        """Retorna todos os tickers de futuros."""
        return self._api_call(self.client.futures_symbol_ticker)
    
    @retry_with_backoff
    def get_mark_price(self, symbol: str) -> float:
        """Retorna o mark price de um símbolo."""
        data = self._api_call(self.client.futures_mark_price, symbol=symbol)
        price = float(data.get("markPrice", 0))
        if price <= 0:
            raise ValueError(f"Mark price inválido para {symbol}: {price}")
        return price
    
    def get_mark_price_critical(self, symbol: str) -> float:
        """Retorna mark price com bypass do circuit breaker (para fechamento)."""
        try:
            data = self._api_call(
                self.client.futures_mark_price,
                symbol=symbol,
                bypass_circuit=True
            )
            price = float(data.get("markPrice", 0))
            if price <= 0:
                raise ValueError(f"Mark price inválido para {symbol}: {price}")
            return price
        except Exception as e:
            logger.warning(f"Erro ao obter mark price crítico de {symbol}: {e}")
            return 0.0
    
    # --- Exchange Info e Filtros ---
    
    @retry_with_backoff
    def get_exchange_info(self) -> Dict:
        """Retorna exchange info com cache de 5 minutos."""
        now = time.time()
        if self._exchange_info_cache and (now - self._exchange_info_time) < 300:
            return self._exchange_info_cache
        
        info = self._api_call(self.client.futures_exchange_info)
        self._exchange_info_cache = info
        self._exchange_info_time = now
        return info
    
    def get_symbol_filters(self, symbol: str) -> Dict:
        """Retorna filtros de um símbolo (step size, tick size, etc.)."""
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
        """Ajusta quantidade conforme step size e maxQty do simbolo."""
        filters = self.get_symbol_filters(symbol)
        lot_filter = filters.get("LOT_SIZE", {})
        market_lot = filters.get("MARKET_LOT_SIZE", {})
        step_size = float(lot_filter.get("stepSize", 0.001))
        min_qty = float(lot_filter.get("minQty", 0.001))
        
        # Max qty: usar MARKET_LOT_SIZE se disponivel, senao LOT_SIZE
        max_qty = float(market_lot.get("maxQty", 0))
        if max_qty <= 0:
            max_qty = float(lot_filter.get("maxQty", 999999999))
        
        # Limitar ao maxQty
        if max_qty > 0 and quantity > max_qty:
            logger.warning(
                f"{symbol}: qty {quantity:.6f} > maxQty {max_qty}. Reduzindo."
            )
            quantity = max_qty * 0.95  # 95% do max por seguranca
        
        # Arredondar para step size
        precision = len(str(step_size).rstrip('0').split('.')[-1]) if '.' in str(step_size) else 0
        adjusted = max(min_qty, round(quantity - (quantity % step_size), precision))
        return adjusted
    
    def adjust_price(self, symbol: str, price: float) -> float:
        """Ajusta preço conforme tick size do símbolo."""
        filters = self.get_symbol_filters(symbol)
        price_filter = filters.get("PRICE_FILTER", {})
        tick_size = float(price_filter.get("tickSize", 0.01))
        
        precision = len(str(tick_size).rstrip('0').split('.')[-1]) if '.' in str(tick_size) else 0
        adjusted = round(price - (price % tick_size), precision)
        return adjusted
    
    # --- Ordens ---
    
    @retry_with_backoff
    def set_leverage(self, symbol: str, leverage: int = None):
        """Define alavancagem para um símbolo."""
        if leverage is None:
            leverage = LEVERAGE
        try:
            self._api_call(
                self.client.futures_change_leverage,
                symbol=symbol, leverage=leverage
            )
        except BinanceAPIException as e:
            if e.code == -4028:  # Leverage não mudou
                pass
            else:
                raise
    
    @retry_with_backoff
    def set_margin_type(self, symbol: str, margin_type: str = "CROSSED"):
        """Define tipo de margem."""
        try:
            self._api_call(
                self.client.futures_change_margin_type,
                symbol=symbol, marginType=margin_type
            )
        except BinanceAPIException as e:
            if e.code == -4046:  # Já está no tipo correto
                pass
            else:
                raise
    
    @retry_with_backoff
    def place_market_order(self, symbol: str, side: str, quantity: float) -> Dict:
        """Coloca ordem MARKET."""
        quantity = self.adjust_quantity(symbol, quantity)
        logger.info(f"Ordem MARKET: {side} {quantity} {symbol}")
        
        order = self._api_call(
            self.client.futures_create_order,
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=quantity
        )
        return order
    
    def place_stop_loss(self, symbol: str, side: str, stop_price: float,
                        close_position: bool = True, quantity: float = None) -> Optional[Dict]:
        """
        Coloca ordem Stop Loss na exchange.
        Retorna None se não suportado (Testnet) - SL será gerenciado por software.
        NÃO conta falhas no circuit breaker.
        """
        # Se já sabemos que não é suportado, pular
        if not self.exchange_sl_tp_supported:
            logger.info(f"SL por software para {symbol} (exchange não suporta)")
            return None
        
        stop_price = self.adjust_price(symbol, stop_price)
        
        # Método 1: STOP_MARKET com closePosition
        try:
            params = {
                "symbol": symbol,
                "side": side,
                "type": "STOP_MARKET",
                "stopPrice": stop_price,
                "closePosition": "true" if close_position else "false",
                "workingType": "MARK_PRICE"
            }
            if not close_position and quantity:
                params["quantity"] = self.adjust_quantity(symbol, quantity)
            
            order = self._api_call_no_cb_count(self.client.futures_create_order, **params)
            return order
        except BinanceAPIException as e:
            if e.code == -4120:
                # Tipo de ordem não suportado neste endpoint
                self.exchange_sl_tp_supported = False
                logger.warning(
                    f"SL/TP na exchange NÃO SUPORTADO (erro -4120). "
                    f"Usando monitoramento por software."
                )
                return None
            logger.warning(f"SL método 1 falhou: {e}. Tentando fallback...")
        except Exception as e:
            logger.warning(f"SL método 1 falhou: {e}. Tentando fallback...")
        
        # Método 2: STOP_MARKET com quantidade e reduceOnly
        if quantity:
            try:
                order = self._api_call_no_cb_count(
                    self.client.futures_create_order,
                    symbol=symbol,
                    side=side,
                    type="STOP_MARKET",
                    quantity=self.adjust_quantity(symbol, quantity),
                    stopPrice=stop_price,
                    reduceOnly="true",
                    workingType="MARK_PRICE"
                )
                return order
            except BinanceAPIException as e:
                if e.code == -4120:
                    self.exchange_sl_tp_supported = False
                    logger.warning("SL/TP na exchange NÃO SUPORTADO. Usando software.")
                    return None
                logger.warning(f"SL método 2 falhou: {e}")
            except Exception as e:
                logger.warning(f"SL método 2 falhou: {e}")
        
        # Método 3: STOP com preço limite
        if quantity:
            try:
                limit_price = stop_price * (0.995 if side == "SELL" else 1.005)
                limit_price = self.adjust_price(symbol, limit_price)
                order = self._api_call_no_cb_count(
                    self.client.futures_create_order,
                    symbol=symbol,
                    side=side,
                    type="STOP",
                    quantity=self.adjust_quantity(symbol, quantity),
                    price=limit_price,
                    stopPrice=stop_price,
                    reduceOnly="true",
                    workingType="MARK_PRICE"
                )
                return order
            except BinanceAPIException as e:
                if e.code == -4120:
                    self.exchange_sl_tp_supported = False
                    logger.warning("SL/TP na exchange NÃO SUPORTADO. Usando software.")
                    return None
                logger.error(f"SL método 3 falhou: {e}")
            except Exception as e:
                logger.error(f"SL método 3 falhou: {e}")
        
        logger.warning(f"Todos os métodos de SL falharam para {symbol}. SL será por software.")
        return None
    
    def place_take_profit(self, symbol: str, side: str, stop_price: float,
                          close_position: bool = True, quantity: float = None) -> Optional[Dict]:
        """
        Coloca ordem Take Profit na exchange.
        Retorna None se não suportado - TP será gerenciado por software.
        NÃO conta falhas no circuit breaker.
        """
        if not self.exchange_sl_tp_supported:
            logger.info(f"TP por software para {symbol} (exchange não suporta)")
            return None
        
        stop_price = self.adjust_price(symbol, stop_price)
        
        # Método 1: TAKE_PROFIT_MARKET com closePosition
        try:
            params = {
                "symbol": symbol,
                "side": side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": stop_price,
                "closePosition": "true" if close_position else "false",
                "workingType": "MARK_PRICE"
            }
            if not close_position and quantity:
                params["quantity"] = self.adjust_quantity(symbol, quantity)
            
            order = self._api_call_no_cb_count(self.client.futures_create_order, **params)
            return order
        except BinanceAPIException as e:
            if e.code == -4120:
                self.exchange_sl_tp_supported = False
                logger.warning("TP na exchange NÃO SUPORTADO. Usando software.")
                return None
            logger.warning(f"TP método 1 falhou: {e}. Tentando fallback...")
        except Exception as e:
            logger.warning(f"TP método 1 falhou: {e}. Tentando fallback...")
        
        # Método 2: TAKE_PROFIT_MARKET com quantidade
        if quantity:
            try:
                order = self._api_call_no_cb_count(
                    self.client.futures_create_order,
                    symbol=symbol,
                    side=side,
                    type="TAKE_PROFIT_MARKET",
                    quantity=self.adjust_quantity(symbol, quantity),
                    stopPrice=stop_price,
                    reduceOnly="true",
                    workingType="MARK_PRICE"
                )
                return order
            except BinanceAPIException as e:
                if e.code == -4120:
                    self.exchange_sl_tp_supported = False
                    return None
                logger.warning(f"TP método 2 falhou: {e}")
            except Exception as e:
                logger.warning(f"TP método 2 falhou: {e}")
        
        # Método 3: TAKE_PROFIT com preço limite
        if quantity:
            try:
                limit_price = stop_price * (1.005 if side == "SELL" else 0.995)
                limit_price = self.adjust_price(symbol, limit_price)
                order = self._api_call_no_cb_count(
                    self.client.futures_create_order,
                    symbol=symbol,
                    side=side,
                    type="TAKE_PROFIT",
                    quantity=self.adjust_quantity(symbol, quantity),
                    price=limit_price,
                    stopPrice=stop_price,
                    reduceOnly="true",
                    workingType="MARK_PRICE"
                )
                return order
            except BinanceAPIException as e:
                if e.code == -4120:
                    self.exchange_sl_tp_supported = False
                    return None
                logger.error(f"TP método 3 falhou: {e}")
            except Exception as e:
                logger.error(f"TP método 3 falhou: {e}")
        
        logger.warning(f"Todos os métodos de TP falharam para {symbol}. TP será por software.")
        return None
    
    @retry_with_backoff
    def cancel_all_orders(self, symbol: str):
        """Cancela todas as ordens abertas de um símbolo."""
        try:
            self._api_call(
                self.client.futures_cancel_all_open_orders,
                symbol=symbol
            )
            logger.info(f"Todas as ordens de {symbol} canceladas")
        except Exception as e:
            logger.warning(f"Erro ao cancelar ordens de {symbol}: {e}")
    
    @retry_with_backoff
    def get_open_orders(self, symbol: str = None) -> List[Dict]:
        """Retorna ordens abertas."""
        if symbol:
            return self._api_call(
                self.client.futures_get_open_orders, symbol=symbol
            )
        return self._api_call(self.client.futures_get_open_orders)
    
    def close_position_robust(self, symbol: str, side: str, quantity: float) -> bool:
        """
        Fecha posição de forma robusta com múltiplos métodos.
        BYPASS do circuit breaker para garantir fechamento.
        """
        close_side = "SELL" if side == "LONG" else "BUY"
        
        # FORÇAR reset do circuit breaker para operações de fechamento
        if self.circuit_breaker.state != CircuitState.CLOSED:
            self.circuit_breaker.force_reset()
        
        # Cancelar ordens pendentes primeiro
        try:
            self._api_call(
                self.client.futures_cancel_all_open_orders,
                symbol=symbol,
                bypass_circuit=True
            )
            logger.info(f"Ordens de {symbol} canceladas para fechamento")
        except Exception as e:
            logger.warning(f"Erro ao cancelar ordens de {symbol}: {e}")
        
        # Método 1: Ordem MARKET direta
        try:
            qty = self.adjust_quantity(symbol, quantity)
            logger.info(f"Fechando {symbol}: MARKET {close_side} {qty}")
            self._api_call(
                self.client.futures_create_order,
                symbol=symbol,
                side=close_side,
                type="MARKET",
                quantity=qty,
                bypass_circuit=True
            )
            logger.info(f"Posição {symbol} fechada via MARKET")
            return True
        except Exception as e:
            logger.warning(f"Fechamento MARKET falhou para {symbol}: {e}")
        
        # Método 2: Ordem MARKET com reduceOnly
        try:
            qty = self.adjust_quantity(symbol, quantity)
            self._api_call(
                self.client.futures_create_order,
                symbol=symbol,
                side=close_side,
                type="MARKET",
                quantity=qty,
                reduceOnly="true",
                bypass_circuit=True
            )
            logger.info(f"Posição {symbol} fechada via MARKET reduceOnly")
            return True
        except Exception as e:
            logger.warning(f"Fechamento reduceOnly falhou para {symbol}: {e}")
        
        # Método 3: Verificar se já foi fechada
        try:
            positions = self._api_call(
                self.client.futures_account,
                bypass_circuit=True
            )
            for pos in positions.get("positions", []):
                if pos["symbol"] == symbol:
                    qty = float(pos.get("positionAmt", 0))
                    if abs(qty) > 0:
                        logger.error(f"Posição {symbol} ainda aberta ({qty}) após tentativas!")
                        return False
            logger.info(f"Posição {symbol} confirmada como fechada")
            return True
        except Exception as e:
            logger.error(f"Erro ao verificar posição {symbol}: {e}")
            return False
    
    def close_all_positions(self) -> int:
        """Fecha todas as posições abertas. Retorna quantidade fechada."""
        # Forçar reset do circuit breaker
        if self.circuit_breaker.state != CircuitState.CLOSED:
            self.circuit_breaker.force_reset()
        
        try:
            positions = self.get_open_positions()
        except Exception:
            # Tentar com bypass
            try:
                account = self._api_call(
                    self.client.futures_account,
                    bypass_circuit=True
                )
                positions = []
                for pos in account.get("positions", []):
                    qty = float(pos.get("positionAmt", 0))
                    if qty != 0:
                        positions.append({
                            "symbol": pos["symbol"],
                            "side": "LONG" if qty > 0 else "SHORT",
                            "quantity": abs(qty),
                        })
            except Exception as e:
                logger.error(f"Não foi possível listar posições: {e}")
                return 0
        
        closed = 0
        for pos in positions:
            if self.close_position_robust(pos["symbol"], pos["side"], pos["quantity"]):
                closed += 1
        return closed
    
    def get_circuit_status(self) -> Dict:
        """Retorna status do circuit breaker."""
        return self.circuit_breaker.get_status()
