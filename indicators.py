"""
indicators.py - Funções para cálculo de indicadores técnicos.
EMA, RSI, Bandas de Bollinger, ATR.
"""

import numpy as np
import pandas as pd
from typing import Tuple


def klines_to_dataframe(klines: list) -> pd.DataFrame:
    """Converte klines da Binance para DataFrame."""
    df = pd.DataFrame(klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])
    
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = df[col].astype(float)
    
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    
    return df


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """Calcula Média Móvel Exponencial."""
    return series.ewm(span=period, adjust=False).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calcula Índice de Força Relativa (RSI)."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    
    return rsi


def calculate_bollinger_bands(series: pd.Series, period: int = 20,
                               std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Calcula Bandas de Bollinger.
    Retorna: (banda_superior, banda_média, banda_inferior)
    """
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    
    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)
    
    return upper, sma, lower


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calcula Average True Range (ATR)."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.ewm(span=period, adjust=False).mean()
    
    return atr


def calculate_all_indicators(df: pd.DataFrame, ema_fast: int = 9, ema_slow: int = 21,
                              rsi_period: int = 14, bb_period: int = 20,
                              bb_std: float = 2.0, atr_period: int = 14) -> pd.DataFrame:
    """
    Calcula todos os indicadores para um DataFrame de candles.
    Adiciona colunas ao DataFrame original.
    """
    close = df["close"]
    
    # EMAs
    df["ema_fast"] = calculate_ema(close, ema_fast)
    df["ema_slow"] = calculate_ema(close, ema_slow)
    
    # RSI
    df["rsi"] = calculate_rsi(close, rsi_period)
    
    # Bandas de Bollinger
    df["bb_upper"], df["bb_middle"], df["bb_lower"] = calculate_bollinger_bands(
        close, bb_period, bb_std
    )
    
    # ATR
    df["atr"] = calculate_atr(df, atr_period)
    
    return df


def get_signal(df: pd.DataFrame, rsi_long_min: float = 50, rsi_long_max: float = 70,
               rsi_short_min: float = 30, rsi_short_max: float = 50) -> str:
    """
    Analisa o último candle e retorna sinal de trading.
    Retorna: 'LONG', 'SHORT' ou 'NONE'
    """
    if len(df) < 2:
        return "NONE"
    
    last = df.iloc[-1]
    
    price = last["close"]
    ema_fast = last["ema_fast"]
    ema_slow = last["ema_slow"]
    rsi = last["rsi"]
    bb_upper = last["bb_upper"]
    bb_lower = last["bb_lower"]
    
    # Verificar NaN
    if any(pd.isna([price, ema_fast, ema_slow, rsi, bb_upper, bb_lower])):
        return "NONE"
    
    # LONG: Preço > EMA9 > EMA21, RSI entre 50-70, preço abaixo da banda superior
    if (price > ema_fast > ema_slow and
            rsi_long_min <= rsi <= rsi_long_max and
            price < bb_upper):
        return "LONG"
    
    # SHORT: Preço < EMA9 < EMA21, RSI entre 30-50, preço acima da banda inferior
    if (price < ema_fast < ema_slow and
            rsi_short_min <= rsi <= rsi_short_max and
            price > bb_lower):
        return "SHORT"
    
    return "NONE"


def get_btc_trend(btc_df: pd.DataFrame, ema_period: int = 200) -> str:
    """
    Determina a tendência geral do BTC usando EMA 200.
    Retorna: 'ALTA', 'BAIXA' ou 'LATERAL'
    """
    if len(btc_df) < ema_period:
        return "LATERAL"
    
    ema200 = calculate_ema(btc_df["close"], ema_period)
    last_price = btc_df["close"].iloc[-1]
    last_ema = ema200.iloc[-1]
    
    if pd.isna(last_ema):
        return "LATERAL"
    
    diff_percent = ((last_price - last_ema) / last_ema) * 100
    
    if diff_percent > 1.0:
        return "ALTA"
    elif diff_percent < -1.0:
        return "BAIXA"
    else:
        return "LATERAL"
