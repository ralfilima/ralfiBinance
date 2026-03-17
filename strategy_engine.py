"""
strategy_engine.py - Nucleo da estrategia de trading.
Selecao de ativos, analise individual e decisoes de entrada/saida.

v3.0 - Correcoes:
  - Filtro de ATR minimo (rejeita ativos com ATR < 0.1% do preco)
  - Log detalhado de cada analise e rejeicao
  - Analise de BTC cacheada (nao repete a cada oportunidade)
  - Fallback mais seguro na selecao de ativos
"""

import time
import pandas as pd
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

from binance_client import BinanceClientWrapper
from indicators import (
    klines_to_dataframe, calculate_all_indicators,
    get_signal, get_btc_trend
)
from correlation_filter import filter_correlated_assets
from config import (
    TOP_VOLUME_COUNT, TOP_GAINERS_COUNT, PERSISTENCE_CHECKS,
    PERSISTENCE_INTERVAL, CORRELATION_THRESHOLD, MAX_PORTFOLIO_SIZE,
    EMA_FAST, EMA_SLOW, RSI_PERIOD, BB_PERIOD, BB_STD, ATR_PERIOD,
    RSI_LONG_MIN, RSI_LONG_MAX, RSI_SHORT_MIN, RSI_SHORT_MAX,
    EMA_TREND, TIMEFRAME
)
from utils.logger import logger


# ATR minimo como percentual do preco para aceitar um ativo
MIN_ATR_PCT = 0.001  # 0.1% do preco


class StrategyEngine:
    """Motor de estrategia para selecao e analise de ativos."""
    
    def __init__(self, client: BinanceClientWrapper):
        self.client = client
        self.persistence_tracker = defaultdict(int)  # {symbol: contagem}
        self.last_btc_trend = "LATERAL"
        self.selected_assets = []
        self._btc_trend_time = 0
        self._btc_trend_cache_seconds = 120  # Cache de 2 min
    
    def get_top_volume_symbols(self) -> List[str]:
        """Identifica as top moedas por volume de negociacao nos futuros."""
        try:
            tickers = self.client.get_ticker_24h()
            
            # Filtrar apenas pares USDT e remover stablecoins
            usdt_pairs = []
            excluded = {"USDCUSDT", "BUSDUSDT", "TUSDUSDT", "DAIUSDT", "USDPUSDT"}
            
            for t in tickers:
                symbol = t.get("symbol", "")
                if (symbol.endswith("USDT") and
                        symbol not in excluded and
                        "_" not in symbol):
                    volume = float(t.get("quoteVolume", 0))
                    price_change = float(t.get("priceChangePercent", 0))
                    usdt_pairs.append({
                        "symbol": symbol,
                        "volume": volume,
                        "price_change_24h": price_change
                    })
            
            # Ordenar por volume
            usdt_pairs.sort(key=lambda x: x["volume"], reverse=True)
            
            top = usdt_pairs[:TOP_VOLUME_COUNT]
            logger.info(f"Top {len(top)} moedas por volume identificadas")
            
            return top
        
        except Exception as e:
            logger.error(f"Erro ao buscar top volume: {e}")
            return []
    
    def check_persistence(self, top_volume: List[Dict]) -> List[str]:
        """
        Verifica persistencia: moeda deve estar entre as top gainers
        por PERSISTENCE_CHECKS verificacoes consecutivas.
        """
        # Ordenar por variacao 24h (top gainers)
        sorted_by_change = sorted(
            top_volume, key=lambda x: x["price_change_24h"], reverse=True
        )
        top_gainers = [s["symbol"] for s in sorted_by_change[:TOP_GAINERS_COUNT]]
        
        # Atualizar tracker
        current_symbols = set(top_gainers)
        
        # Incrementar para os que estao no top
        for symbol in current_symbols:
            self.persistence_tracker[symbol] += 1
        
        # Resetar os que sairam
        for symbol in list(self.persistence_tracker.keys()):
            if symbol not in current_symbols:
                self.persistence_tracker[symbol] = 0
        
        # Retornar os que passaram no filtro de persistencia
        persistent = [
            s for s in top_gainers
            if self.persistence_tracker.get(s, 0) >= PERSISTENCE_CHECKS
        ]
        
        return persistent
    
    def select_assets(self) -> List[str]:
        """
        Pipeline completo de selecao de ativos:
        1. Top volume
        2. Filtro de persistencia (multiplas verificacoes)
        3. Filtro de correlacao
        """
        logger.info("Iniciando selecao de ativos...")
        
        persistent_symbols = []
        
        for check in range(PERSISTENCE_CHECKS):
            top_volume = self.get_top_volume_symbols()
            if not top_volume:
                logger.warning("Nao foi possivel obter dados de volume")
                return self.selected_assets  # Manter selecao anterior
            
            persistent_symbols = self.check_persistence(top_volume)
            
            if check < PERSISTENCE_CHECKS - 1:
                logger.info(
                    f"  Verificacao {check + 1}/{PERSISTENCE_CHECKS}: "
                    f"{len(persistent_symbols)} persistentes. "
                    f"Aguardando {PERSISTENCE_INTERVAL}s..."
                )
                time.sleep(PERSISTENCE_INTERVAL)
        
        if not persistent_symbols:
            # Fallback: usar top gainers sem persistencia
            top_volume = self.get_top_volume_symbols()
            sorted_by_change = sorted(
                top_volume, key=lambda x: x["price_change_24h"], reverse=True
            )
            persistent_symbols = [s["symbol"] for s in sorted_by_change[:TOP_GAINERS_COUNT]]
            logger.info(f"Fallback: usando top {len(persistent_symbols)} gainers sem persistencia")
        
        logger.info(f"Ativos persistentes: {persistent_symbols}")
        
        # Filtro de correlacao
        if len(persistent_symbols) > 1:
            price_data = {}
            for symbol in persistent_symbols:
                try:
                    klines = self.client.get_klines(symbol, TIMEFRAME, limit=100)
                    df = klines_to_dataframe(klines)
                    price_data[symbol] = df["close"]
                except Exception as e:
                    logger.warning(f"Erro ao obter dados de {symbol}: {e}")
            
            self.selected_assets = filter_correlated_assets(
                persistent_symbols, price_data,
                threshold=CORRELATION_THRESHOLD,
                max_assets=MAX_PORTFOLIO_SIZE
            )
        else:
            self.selected_assets = persistent_symbols[:MAX_PORTFOLIO_SIZE]
        
        logger.info(f"Ativos selecionados: {self.selected_assets}")
        return self.selected_assets
    
    def analyze_btc_trend(self) -> str:
        """Analisa tendencia do BTC usando EMA 200. Cacheado por 2 minutos."""
        now = time.time()
        if now - self._btc_trend_time < self._btc_trend_cache_seconds:
            return self.last_btc_trend
        
        try:
            klines = self.client.get_klines("BTCUSDT", TIMEFRAME, limit=250)
            df = klines_to_dataframe(klines)
            self.last_btc_trend = get_btc_trend(df, EMA_TREND)
            self._btc_trend_time = now
            logger.info(f"Tendencia BTC: {self.last_btc_trend}")
            return self.last_btc_trend
        except Exception as e:
            logger.error(f"Erro ao analisar tendencia BTC: {e}")
            return self.last_btc_trend
    
    def analyze_asset(self, symbol: str) -> Dict:
        """
        Analise completa de um ativo individual.
        Retorna dicionario com indicadores e sinal.
        """
        try:
            klines = self.client.get_klines(symbol, TIMEFRAME, limit=100)
            df = klines_to_dataframe(klines)
            
            # Calcular todos os indicadores
            df = calculate_all_indicators(
                df, ema_fast=EMA_FAST, ema_slow=EMA_SLOW,
                rsi_period=RSI_PERIOD, bb_period=BB_PERIOD,
                bb_std=BB_STD, atr_period=ATR_PERIOD
            )
            
            # Obter sinal
            signal = get_signal(
                df,
                rsi_long_min=RSI_LONG_MIN, rsi_long_max=RSI_LONG_MAX,
                rsi_short_min=RSI_SHORT_MIN, rsi_short_max=RSI_SHORT_MAX
            )
            
            last = df.iloc[-1]
            
            return {
                "symbol": symbol,
                "signal": signal,
                "price": last["close"],
                "ema_fast": last["ema_fast"],
                "ema_slow": last["ema_slow"],
                "rsi": last["rsi"],
                "bb_upper": last["bb_upper"],
                "bb_lower": last["bb_lower"],
                "bb_middle": last["bb_middle"],
                "atr": last["atr"],
                "df": df,
            }
        
        except Exception as e:
            logger.error(f"Erro ao analisar {symbol}: {e}")
            return {
                "symbol": symbol,
                "signal": "NONE",
                "error": str(e)
            }
    
    def find_opportunities(self, exclude_symbols: List[str] = None) -> List[Dict]:
        """
        Busca oportunidades de entrada nos ativos selecionados.
        
        Filtros aplicados:
          1. Sinal LONG ou SHORT dos indicadores
          2. ATR minimo (0.1% do preco) - evita ativos sem volatilidade
        
        Args:
            exclude_symbols: Simbolos a excluir (ja com posicao aberta ou em cooldown)
        
        Returns:
            Lista de oportunidades com sinal LONG ou SHORT
        """
        if exclude_symbols is None:
            exclude_symbols = []
        
        opportunities = []
        
        # Analisar tendencia do BTC (cacheado)
        btc_trend = self.analyze_btc_trend()
        
        for symbol in self.selected_assets:
            if symbol in exclude_symbols:
                continue
            
            analysis = self.analyze_asset(symbol)
            
            if analysis.get("signal") not in ("LONG", "SHORT"):
                logger.debug(f"  {symbol}: sem sinal ({analysis.get('signal', 'NONE')})")
                continue
            
            # === FILTRO DE ATR MINIMO ===
            atr = analysis.get("atr", 0)
            price = analysis.get("price", 0)
            
            if price > 0 and atr > 0:
                atr_pct = atr / price
                if atr_pct < MIN_ATR_PCT:
                    logger.warning(
                        f"  {symbol}: REJEITADO - ATR muito baixo "
                        f"({atr:.6f} = {atr_pct*100:.4f}% do preco, "
                        f"minimo: {MIN_ATR_PCT*100:.2f}%)"
                    )
                    continue
            elif atr <= 0:
                logger.warning(f"  {symbol}: REJEITADO - ATR zero ou negativo ({atr})")
                continue
            
            # Oportunidade valida
            analysis["btc_trend"] = btc_trend
            opportunities.append(analysis)
            logger.info(
                f"  Oportunidade: {symbol} -> {analysis['signal']} "
                f"(RSI: {analysis.get('rsi', 0):.1f}, "
                f"ATR: {atr:.6f} = {(atr/price*100) if price > 0 else 0:.3f}%)"
            )
        
        if not opportunities:
            logger.info(f"Nenhuma oportunidade encontrada em {len(self.selected_assets)} ativos")
        
        return opportunities
    
    def get_analysis_summary(self) -> List[Dict]:
        """Retorna resumo da analise de todos os ativos selecionados."""
        summaries = []
        for symbol in self.selected_assets:
            analysis = self.analyze_asset(symbol)
            if "error" not in analysis:
                summaries.append({
                    "Simbolo": symbol,
                    "Preco": f"{analysis['price']:.4f}",
                    "EMA9": f"{analysis['ema_fast']:.4f}",
                    "EMA21": f"{analysis['ema_slow']:.4f}",
                    "RSI": f"{analysis['rsi']:.1f}",
                    "BB Sup": f"{analysis['bb_upper']:.4f}",
                    "BB Inf": f"{analysis['bb_lower']:.4f}",
                    "ATR": f"{analysis['atr']:.6f}",
                    "Sinal": analysis["signal"],
                })
        return summaries
