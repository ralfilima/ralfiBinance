"""
coin_selector.py - Selecao inteligente de moedas para DCA.

Estrategia de selecao:
  1. Filtra por volume minimo e preco minimo
  2. Busca moedas com MOMENTUM POSITIVO (subindo) para LONG
  3. Confirma com indicadores tecnicos (RSI, EMA, BB)
  4. Rankeia por score composto (volume + momentum + proximidade de suporte)
  5. Seleciona as 5 melhores com diversificacao
"""

import time
import pandas as pd
import numpy as np
from typing import List, Dict, Tuple

from binance_client import BinanceClientWrapper
from config import (
    NUM_COINS, TOP_CANDIDATES, MIN_VOLUME_24H, MIN_PRICE,
    RSI_PERIOD, EMA_FAST, EMA_SLOW, BB_PERIOD, BB_STD, ATR_PERIOD,
    MIN_ATR_PCT, MAX_ATR_PCT, TIMEFRAME, TIMEFRAME_TREND,
)
from utils.logger import logger


def _scalar(val):
    """Converte qualquer valor pandas/numpy para float nativo Python."""
    try:
        if hasattr(val, "item"):
            return float(val.item())
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def klines_to_df(klines: List) -> pd.DataFrame:
    """Converte klines da Binance para DataFrame."""
    df = pd.DataFrame(klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=period).mean()
    rs = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close).abs(),
        (low - close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def calc_bb(df: pd.DataFrame, period: int = 20, std: float = 2.0):
    middle = df["close"].rolling(period).mean()
    std_dev = df["close"].rolling(period).std()
    upper = middle + std * std_dev
    lower = middle - std * std_dev
    width = (upper - lower) / middle
    return middle, upper, lower, width


def analyze_coin(client: BinanceClientWrapper, symbol: str) -> Dict:
    """
    Analisa uma moeda e retorna score + direcao.
    Score alto = melhor candidata para DCA.
    """
    try:
        # Dados 5m (curto prazo)
        klines = client.get_klines(symbol, TIMEFRAME, limit=100)
        df = klines_to_df(klines)

        if len(df) < 30:
            return {"symbol": symbol, "score": 0, "reason": "dados insuficientes"}

        price = _scalar(df["close"].iloc[-1])

        if price <= 0:
            return {"symbol": symbol, "score": 0, "reason": "preco zero"}

        # Indicadores
        df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
        df["rsi"] = calc_rsi(df["close"], RSI_PERIOD)
        df["atr"] = calc_atr(df, ATR_PERIOD)
        bb_mid, bb_up, bb_low, bb_width = calc_bb(df, BB_PERIOD, BB_STD)
        df["bb_mid"] = bb_mid
        df["bb_upper"] = bb_up
        df["bb_lower"] = bb_low
        df["bb_width"] = bb_width

        # Extrair valores escalares (NUNCA usar Series em comparacoes if)
        rsi = _scalar(df["rsi"].iloc[-1])
        atr = _scalar(df["atr"].iloc[-1])
        ema_f = _scalar(df["ema_fast"].iloc[-1])
        ema_s = _scalar(df["ema_slow"].iloc[-1])
        bb_upper = _scalar(df["bb_upper"].iloc[-1])
        bb_lower = _scalar(df["bb_lower"].iloc[-1])

        # Checar NaN
        if any(np.isnan(v) for v in [rsi, atr, ema_f, ema_s, bb_upper, bb_lower]):
            return {"symbol": symbol, "score": 0, "reason": "indicadores NaN"}

        # Filtro ATR
        atr_pct = atr / price if price > 0 else 0.0
        if atr_pct < MIN_ATR_PCT:
            return {"symbol": symbol, "score": 0, "reason": f"ATR baixo ({atr_pct*100:.3f}%)"}
        if atr_pct > MAX_ATR_PCT:
            return {"symbol": symbol, "score": 0, "reason": f"ATR alto ({atr_pct*100:.1f}%)"}

        # Determinar direcao
        direction = "NONE"
        direction_score = 0.0
        bb_range = bb_upper - bb_lower

        if ema_f > ema_s and price > ema_f:
            # Tendencia de alta
            if 40 <= rsi <= 70:
                direction = "LONG"
                bb_position = (price - bb_lower) / bb_range if bb_range > 0 else 0.5
                direction_score = max(0.0, 1.0 - bb_position)
            elif rsi < 40:
                direction = "LONG"
                direction_score = 0.9
        elif ema_f < ema_s and price < ema_f:
            # Tendencia de baixa
            if 30 <= rsi <= 60:
                direction = "SHORT"
                bb_position = (price - bb_lower) / bb_range if bb_range > 0 else 0.5
                direction_score = bb_position
            elif rsi > 60:
                direction = "SHORT"
                direction_score = 0.9

        if direction == "NONE":
            return {"symbol": symbol, "score": 0, "reason": "sem tendencia clara"}

        # Momentum: variacao das ultimas 12 candles (1h em 5m)
        momentum = 0.0
        if len(df) >= 12:
            price_12 = _scalar(df["close"].iloc[-12])
            if price_12 > 0:
                momentum = (price - price_12) / price_12

        # Para LONG, queremos momentum positivo mas nao exagerado
        momentum_score = 0.0
        if direction == "LONG":
            if 0 < momentum < 0.03:
                momentum_score = 0.8
            elif -0.02 < momentum <= 0:
                momentum_score = 0.6
            elif momentum >= 0.03:
                momentum_score = 0.3
        else:  # SHORT
            if -0.03 < momentum < 0:
                momentum_score = 0.8
            elif 0 <= momentum < 0.02:
                momentum_score = 0.6
            elif momentum <= -0.03:
                momentum_score = 0.3

        # Volume score: volume recente vs media
        vol_recent = _scalar(df["volume"].iloc[-6:].mean())
        vol_avg = _scalar(df["volume"].iloc[-50:].mean())
        vol_ratio = vol_recent / vol_avg if vol_avg > 0 else 1.0
        vol_score = min(1.0, vol_ratio / 2)

        # ATR score: volatilidade moderada e ideal para DCA
        if 0.003 <= atr_pct <= 0.02:
            atr_score = 1.0
        elif 0.002 <= atr_pct < 0.003:
            atr_score = 0.6
        elif 0.02 < atr_pct <= 0.04:
            atr_score = 0.5
        else:
            atr_score = 0.2

        # Score final composto
        score = (
            direction_score * 0.30 +
            momentum_score * 0.25 +
            vol_score * 0.25 +
            atr_score * 0.20
        )

        bb_pos_final = (price - bb_lower) / bb_range if bb_range > 0 else 0.5

        return {
            "symbol": symbol,
            "score": round(score, 4),
            "direction": direction,
            "price": price,
            "rsi": round(rsi, 1),
            "atr": atr,
            "atr_pct": round(atr_pct * 100, 3),
            "momentum": round(momentum * 100, 2),
            "vol_ratio": round(vol_ratio, 2),
            "bb_position": round(bb_pos_final, 2),
            "ema_trend": "BULL" if ema_f > ema_s else "BEAR",
        }

    except Exception as e:
        logger.warning(f"Erro ao analisar {symbol}: {e}")
        return {"symbol": symbol, "score": 0, "reason": str(e)}


def select_best_coins(client: BinanceClientWrapper, num: int = None) -> List[Dict]:
    """
    Seleciona as melhores moedas para DCA.
    Retorna lista de dicts com symbol, direction, score, etc.
    """
    if num is None:
        num = NUM_COINS

    logger.info(f"Iniciando selecao de {num} moedas...")

    # 1. Buscar tickers 24h
    try:
        tickers = client.get_ticker_24h()
    except Exception as e:
        logger.error(f"Erro ao buscar tickers: {e}")
        return []

    # 2. Filtrar por volume e preco
    excluded = {"USDCUSDT", "BUSDUSDT", "TUSDUSDT", "DAIUSDT", "USDPUSDT"}
    candidates = []

    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT") or symbol in excluded or "_" in symbol:
            continue
        volume = float(t.get("quoteVolume", 0))
        last_price = float(t.get("lastPrice", 0))
        if volume < MIN_VOLUME_24H:
            continue
        if last_price < MIN_PRICE:
            continue
        candidates.append({
            "symbol": symbol,
            "volume": volume,
            "price": last_price,
            "change_24h": float(t.get("priceChangePercent", 0)),
        })

    # 3. Ordenar por volume e pegar top N
    candidates.sort(key=lambda x: x["volume"], reverse=True)
    candidates = candidates[:TOP_CANDIDATES]

    logger.info(f"Analisando {len(candidates)} candidatas...")

    # 4. Analisar cada uma
    analyzed = []
    for c in candidates:
        result = analyze_coin(client, c["symbol"])
        if result.get("score", 0) > 0.2:  # Score minimo
            result["volume_24h"] = c["volume"]
            result["change_24h"] = c["change_24h"]
            analyzed.append(result)
        time.sleep(0.1)  # Rate limit

    # 5. Ordenar por score
    analyzed.sort(key=lambda x: x["score"], reverse=True)

    # 6. Selecionar top N com diversificacao
    selected = []
    long_count = 0
    short_count = 0
    max_per_direction = num

    for coin in analyzed:
        if len(selected) >= num:
            break
        direction = coin.get("direction", "NONE")
        if direction == "LONG" and long_count >= max_per_direction:
            continue
        if direction == "SHORT" and short_count >= max_per_direction:
            continue

        selected.append(coin)
        if direction == "LONG":
            long_count += 1
        else:
            short_count += 1

    logger.info(
        f"Selecionadas {len(selected)} moedas: "
        f"{[c['symbol'] for c in selected]} "
        f"(LONG: {long_count}, SHORT: {short_count})"
    )

    return selected