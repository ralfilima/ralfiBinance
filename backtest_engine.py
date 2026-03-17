"""
backtest_engine.py - Motor de backtesting completo.
Simula a estratégia com dados históricos, incluindo comissões, slippage e métricas.
"""

import os
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

from indicators import (
    klines_to_dataframe, calculate_all_indicators, get_signal
)
from config import (
    EMA_FAST, EMA_SLOW, RSI_PERIOD, BB_PERIOD, BB_STD, ATR_PERIOD,
    RSI_LONG_MIN, RSI_LONG_MAX, RSI_SHORT_MIN, RSI_SHORT_MAX,
    ATR_MULTIPLIER, RISK_PER_TRADE, MAX_OPEN_POSITIONS,
    MAX_DAILY_LOSS_PERCENT, MAX_DRAWDOWN_PERCENT,
    BACKTEST_DAYS, BACKTEST_COMMISSION, BACKTEST_SLIPPAGE,
    BACKTEST_INITIAL_CAPITAL, TIMEFRAME_BACKTEST, DATA_DIR,
    LEVERAGE, TRAILING_ACTIVATION, TRAILING_CALLBACK,
    TIME_STOP_MIN, TIME_STOP_MAX
)
from utils.logger import logger
from utils.helpers import print_header, print_separator, print_success, print_warning


class BacktestPosition:
    """Posição simulada no backtest."""
    def __init__(self, symbol: str, side: str, quantity: float,
                 entry_price: float, sl: float, tp: float, entry_bar: int):
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.entry_price = entry_price
        self.stop_loss = sl
        self.take_profit = tp
        self.entry_bar = entry_bar
        self.time_stop_bars = int(np.random.uniform(
            TIME_STOP_MIN, TIME_STOP_MAX
        ))  # Em barras (simplificado)
        self.trailing_active = False
        self.highest_pnl_pct = 0.0
        self.trailing_stop_price = 0.0


class BacktestEngine:
    """Motor de backtesting para validação da estratégia."""
    
    def __init__(self, client=None):
        self.client = client
        self.initial_capital = BACKTEST_INITIAL_CAPITAL
        self.commission = BACKTEST_COMMISSION
        self.slippage = BACKTEST_SLIPPAGE
        
        # Resultados
        self.trades: List[Dict] = []
        self.equity_curve: List[float] = []
        self.drawdown_curve: List[float] = []
    
    def download_data(self, symbol: str, interval: str = None,
                       days: int = None) -> Optional[pd.DataFrame]:
        """Baixa dados históricos da Binance."""
        if interval is None:
            interval = TIMEFRAME_BACKTEST
        if days is None:
            days = BACKTEST_DAYS
        
        cache_file = os.path.join(DATA_DIR, f"{symbol}_{interval}_{days}d.csv")
        
        # Verificar cache
        if os.path.exists(cache_file):
            mod_time = os.path.getmtime(cache_file)
            if time.time() - mod_time < 86400:  # Cache de 24h
                logger.info(f"Usando cache para {symbol}")
                df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                return df
        
        if not self.client:
            logger.error("Cliente Binance não disponível para download")
            return None
        
        try:
            start_str = (datetime.now() - timedelta(days=days)).strftime("%d %b %Y")
            logger.info(f"Baixando {days} dias de dados para {symbol} ({interval})...")
            
            klines = self.client.get_historical_klines(
                symbol, interval, start_str
            )
            
            if not klines:
                logger.error(f"Nenhum dado retornado para {symbol}")
                return None
            
            df = klines_to_dataframe(klines)
            
            # Salvar cache
            df.to_csv(cache_file)
            logger.info(f"Dados salvos: {len(df)} candles para {symbol}")
            
            return df
        
        except Exception as e:
            logger.error(f"Erro ao baixar dados de {symbol}: {e}")
            return None
    
    def apply_slippage(self, price: float, side: str) -> float:
        """Aplica slippage ao preço."""
        if side == "BUY":
            return price * (1 + self.slippage)
        else:
            return price * (1 - self.slippage)
    
    def calculate_commission(self, quantity: float, price: float) -> float:
        """Calcula comissão da operação."""
        return quantity * price * self.commission
    
    def run_backtest(self, symbol: str, df: pd.DataFrame = None,
                      initial_capital: float = None) -> Dict:
        """
        Executa backtest completo para um símbolo.
        
        Returns:
            Dicionário com resultados e métricas
        """
        if df is None:
            df = self.download_data(symbol)
            if df is None:
                return {"error": "Sem dados disponíveis"}
        
        if initial_capital is None:
            initial_capital = self.initial_capital
        
        # Calcular indicadores
        df = calculate_all_indicators(
            df, ema_fast=EMA_FAST, ema_slow=EMA_SLOW,
            rsi_period=RSI_PERIOD, bb_period=BB_PERIOD,
            bb_std=BB_STD, atr_period=ATR_PERIOD
        )
        
        # Variáveis de estado
        capital = initial_capital
        peak_capital = initial_capital
        positions: List[BacktestPosition] = []
        trades = []
        equity = [initial_capital]
        daily_pnl = 0.0
        consecutive_losses = 0
        
        logger.info(f"Iniciando backtest de {symbol}: {len(df)} candles")
        
        # Loop principal
        for i in range(max(EMA_SLOW, BB_PERIOD, ATR_PERIOD) + 5, len(df)):
            current = df.iloc[i]
            price = current["close"]
            high = current["high"]
            low = current["low"]
            atr = current["atr"]
            
            if pd.isna(atr) or atr <= 0:
                equity.append(capital)
                continue
            
            # --- Verificar posições existentes ---
            closed_this_bar = []
            for pos in positions[:]:
                # Calcular P&L atual
                if pos.side == "LONG":
                    pnl_pct = ((price - pos.entry_price) / pos.entry_price) * LEVERAGE
                    # Verificar SL (usando low)
                    if low <= pos.stop_loss:
                        exit_price = self.apply_slippage(pos.stop_loss, "SELL")
                        pnl = (exit_price - pos.entry_price) * pos.quantity
                        commission = self.calculate_commission(pos.quantity, exit_price)
                        net_pnl = pnl - commission
                        trades.append(self._make_trade(pos, exit_price, net_pnl, "SL", i))
                        capital += net_pnl
                        positions.remove(pos)
                        closed_this_bar.append(pos)
                        continue
                    # Verificar TP (usando high)
                    if high >= pos.take_profit:
                        exit_price = self.apply_slippage(pos.take_profit, "SELL")
                        pnl = (exit_price - pos.entry_price) * pos.quantity
                        commission = self.calculate_commission(pos.quantity, exit_price)
                        net_pnl = pnl - commission
                        trades.append(self._make_trade(pos, exit_price, net_pnl, "TP", i))
                        capital += net_pnl
                        positions.remove(pos)
                        closed_this_bar.append(pos)
                        continue
                else:  # SHORT
                    pnl_pct = ((pos.entry_price - price) / pos.entry_price) * LEVERAGE
                    # Verificar SL (usando high)
                    if high >= pos.stop_loss:
                        exit_price = self.apply_slippage(pos.stop_loss, "BUY")
                        pnl = (pos.entry_price - exit_price) * pos.quantity
                        commission = self.calculate_commission(pos.quantity, exit_price)
                        net_pnl = pnl - commission
                        trades.append(self._make_trade(pos, exit_price, net_pnl, "SL", i))
                        capital += net_pnl
                        positions.remove(pos)
                        closed_this_bar.append(pos)
                        continue
                    # Verificar TP (usando low)
                    if low <= pos.take_profit:
                        exit_price = self.apply_slippage(pos.take_profit, "BUY")
                        pnl = (pos.entry_price - exit_price) * pos.quantity
                        commission = self.calculate_commission(pos.quantity, exit_price)
                        net_pnl = pnl - commission
                        trades.append(self._make_trade(pos, exit_price, net_pnl, "TP", i))
                        capital += net_pnl
                        positions.remove(pos)
                        closed_this_bar.append(pos)
                        continue
                
                # Trailing Stop
                if not pos.trailing_active and pnl_pct >= TRAILING_ACTIVATION:
                    pos.trailing_active = True
                    pos.highest_pnl_pct = pnl_pct
                    if pos.side == "LONG":
                        pos.trailing_stop_price = price * (1 - TRAILING_CALLBACK)
                    else:
                        pos.trailing_stop_price = price * (1 + TRAILING_CALLBACK)
                
                if pos.trailing_active:
                    if pnl_pct > pos.highest_pnl_pct:
                        pos.highest_pnl_pct = pnl_pct
                        if pos.side == "LONG":
                            new_stop = price * (1 - TRAILING_CALLBACK)
                            pos.trailing_stop_price = max(pos.trailing_stop_price, new_stop)
                        else:
                            new_stop = price * (1 + TRAILING_CALLBACK)
                            pos.trailing_stop_price = min(pos.trailing_stop_price, new_stop)
                    
                    if pos.side == "LONG" and low <= pos.trailing_stop_price:
                        exit_price = self.apply_slippage(pos.trailing_stop_price, "SELL")
                        pnl = (exit_price - pos.entry_price) * pos.quantity
                        commission = self.calculate_commission(pos.quantity, exit_price)
                        net_pnl = pnl - commission
                        trades.append(self._make_trade(pos, exit_price, net_pnl, "TRAILING", i))
                        capital += net_pnl
                        positions.remove(pos)
                        closed_this_bar.append(pos)
                        continue
                    elif pos.side == "SHORT" and high >= pos.trailing_stop_price:
                        exit_price = self.apply_slippage(pos.trailing_stop_price, "BUY")
                        pnl = (pos.entry_price - exit_price) * pos.quantity
                        commission = self.calculate_commission(pos.quantity, exit_price)
                        net_pnl = pnl - commission
                        trades.append(self._make_trade(pos, exit_price, net_pnl, "TRAILING", i))
                        capital += net_pnl
                        positions.remove(pos)
                        closed_this_bar.append(pos)
                        continue
                
                # Time Stop
                if (i - pos.entry_bar) >= pos.time_stop_bars:
                    if pos.side == "LONG":
                        exit_price = self.apply_slippage(price, "SELL")
                        pnl = (exit_price - pos.entry_price) * pos.quantity
                    else:
                        exit_price = self.apply_slippage(price, "BUY")
                        pnl = (pos.entry_price - exit_price) * pos.quantity
                    commission = self.calculate_commission(pos.quantity, exit_price)
                    net_pnl = pnl - commission
                    trades.append(self._make_trade(pos, exit_price, net_pnl, "TIME", i))
                    capital += net_pnl
                    positions.remove(pos)
                    closed_this_bar.append(pos)
                    continue
            
            # --- Verificar limites de risco ---
            drawdown = (peak_capital - capital) / peak_capital if peak_capital > 0 else 0
            if drawdown >= MAX_DRAWDOWN_PERCENT:
                # Fechar todas as posições
                for pos in positions[:]:
                    if pos.side == "LONG":
                        exit_price = self.apply_slippage(price, "SELL")
                        pnl = (exit_price - pos.entry_price) * pos.quantity
                    else:
                        exit_price = self.apply_slippage(price, "BUY")
                        pnl = (pos.entry_price - exit_price) * pos.quantity
                    commission = self.calculate_commission(pos.quantity, exit_price)
                    net_pnl = pnl - commission
                    trades.append(self._make_trade(pos, exit_price, net_pnl, "DRAWDOWN", i))
                    capital += net_pnl
                positions.clear()
                equity.append(capital)
                continue
            
            # --- Buscar novas entradas ---
            if len(positions) < MAX_OPEN_POSITIONS and consecutive_losses < 3:
                # Obter sinal
                window = df.iloc[max(0, i - 100):i + 1].copy()
                signal = get_signal(
                    window,
                    rsi_long_min=RSI_LONG_MIN, rsi_long_max=RSI_LONG_MAX,
                    rsi_short_min=RSI_SHORT_MIN, rsi_short_max=RSI_SHORT_MAX
                )
                
                if signal != "NONE":
                    # Verificar se já tem posição no mesmo símbolo
                    has_position = any(p.symbol == symbol for p in positions)
                    
                    if not has_position:
                        # Position sizing
                        capital_arriscado = capital * RISK_PER_TRADE
                        stop_distance = atr * ATR_MULTIPLIER
                        quantity = capital_arriscado / stop_distance
                        
                        # Aplicar slippage na entrada
                        if signal == "LONG":
                            entry_price = self.apply_slippage(price, "BUY")
                            sl = entry_price - stop_distance
                            tp = entry_price + (stop_distance * 2)
                        else:
                            entry_price = self.apply_slippage(price, "SELL")
                            sl = entry_price + stop_distance
                            tp = entry_price - (stop_distance * 2)
                        
                        # Comissão de entrada
                        entry_commission = self.calculate_commission(quantity, entry_price)
                        capital -= entry_commission
                        
                        pos = BacktestPosition(
                            symbol, signal, quantity,
                            entry_price, sl, tp, i
                        )
                        positions.append(pos)
            
            # Atualizar equity
            unrealized = 0
            for pos in positions:
                if pos.side == "LONG":
                    unrealized += (price - pos.entry_price) * pos.quantity
                else:
                    unrealized += (pos.entry_price - price) * pos.quantity
            
            current_equity = capital + unrealized
            equity.append(current_equity)
            
            if current_equity > peak_capital:
                peak_capital = current_equity
            
            # Atualizar consecutive losses
            for t in closed_this_bar:
                last_trades = [tr for tr in trades if tr["exit_bar"] == i]
                for lt in last_trades:
                    if lt["net_pnl"] < 0:
                        consecutive_losses += 1
                    else:
                        consecutive_losses = 0
        
        # Fechar posições restantes
        last_price = df.iloc[-1]["close"]
        for pos in positions:
            if pos.side == "LONG":
                exit_price = self.apply_slippage(last_price, "SELL")
                pnl = (exit_price - pos.entry_price) * pos.quantity
            else:
                exit_price = self.apply_slippage(last_price, "BUY")
                pnl = (pos.entry_price - exit_price) * pos.quantity
            commission = self.calculate_commission(pos.quantity, exit_price)
            net_pnl = pnl - commission
            trades.append(self._make_trade(pos, exit_price, net_pnl, "END", len(df) - 1))
            capital += net_pnl
        
        self.trades = trades
        self.equity_curve = equity
        
        # Calcular métricas
        metrics = self._calculate_metrics(trades, equity, initial_capital, capital)
        
        return metrics
    
    def _make_trade(self, pos: BacktestPosition, exit_price: float,
                     net_pnl: float, reason: str, exit_bar: int) -> Dict:
        """Cria registro de trade."""
        return {
            "symbol": pos.symbol,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "quantity": pos.quantity,
            "net_pnl": net_pnl,
            "reason": reason,
            "entry_bar": pos.entry_bar,
            "exit_bar": exit_bar,
            "bars_held": exit_bar - pos.entry_bar,
        }
    
    def _calculate_metrics(self, trades: List[Dict], equity: List[float],
                            initial_capital: float, final_capital: float) -> Dict:
        """Calcula métricas de performance do backtest."""
        if not trades:
            return {
                "total_trades": 0,
                "message": "Nenhum trade executado"
            }
        
        pnls = [t["net_pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        
        # Equity curve para drawdown
        equity_arr = np.array(equity)
        peak = np.maximum.accumulate(equity_arr)
        drawdown = (peak - equity_arr) / peak * 100
        max_drawdown = np.max(drawdown)
        
        # Profit Factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Sharpe Ratio (aproximado)
        if len(pnls) > 1:
            returns = np.array(pnls) / initial_capital
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0
        else:
            sharpe = 0
        
        # Razões de fechamento
        reasons = {}
        for t in trades:
            r = t["reason"]
            reasons[r] = reasons.get(r, 0) + 1
        
        total_return = ((final_capital - initial_capital) / initial_capital) * 100
        
        metrics = {
            "total_trades": len(trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": (len(wins) / len(trades)) * 100 if trades else 0,
            "total_pnl": sum(pnls),
            "total_return_pct": total_return,
            "avg_win": np.mean(wins) if wins else 0,
            "avg_loss": np.mean(losses) if losses else 0,
            "largest_win": max(wins) if wins else 0,
            "largest_loss": min(losses) if losses else 0,
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": max_drawdown,
            "initial_capital": initial_capital,
            "final_capital": final_capital,
            "avg_bars_held": np.mean([t["bars_held"] for t in trades]),
            "close_reasons": reasons,
        }
        
        return metrics
    
    def print_report(self, metrics: Dict, symbol: str = ""):
        """Imprime relatório formatado do backtest."""
        from colorama import Fore, Style
        
        print_header(f"RELATÓRIO DE BACKTEST{' - ' + symbol if symbol else ''}")
        
        if metrics.get("total_trades", 0) == 0:
            print_warning("Nenhum trade executado no período")
            return
        
        print(f"\n{Fore.WHITE}{'='*55}")
        print(f"  {'MÉTRICAS GERAIS':^51}")
        print(f"{'='*55}{Style.RESET_ALL}")
        
        data = [
            ("Total de Trades", f"{metrics['total_trades']}"),
            ("Trades Vencedores", f"{metrics['winning_trades']}"),
            ("Trades Perdedores", f"{metrics['losing_trades']}"),
            ("Win Rate", f"{metrics['win_rate']:.1f}%"),
            ("", ""),
            ("Capital Inicial", f"{metrics['initial_capital']:,.2f} USDT"),
            ("Capital Final", f"{metrics['final_capital']:,.2f} USDT"),
            ("Retorno Total", f"{metrics['total_return_pct']:+.2f}%"),
            ("P&L Total", f"{metrics['total_pnl']:+.2f} USDT"),
            ("", ""),
            ("Maior Ganho", f"{metrics['largest_win']:+.2f} USDT"),
            ("Maior Perda", f"{metrics['largest_loss']:+.2f} USDT"),
            ("Ganho Médio", f"{metrics['avg_win']:+.2f} USDT"),
            ("Perda Média", f"{metrics['avg_loss']:+.2f} USDT"),
            ("", ""),
            ("Profit Factor", f"{metrics['profit_factor']:.2f}"),
            ("Sharpe Ratio", f"{metrics['sharpe_ratio']:.2f}"),
            ("Max Drawdown", f"{metrics['max_drawdown_pct']:.2f}%"),
            ("Barras Médias", f"{metrics['avg_bars_held']:.1f}"),
        ]
        
        for label, value in data:
            if not label:
                print_separator("-", 55)
            else:
                color = ""
                if "Retorno" in label or "P&L Total" in label:
                    val = metrics.get('total_return_pct', 0)
                    color = Fore.GREEN if val >= 0 else Fore.RED
                print(f"  {Fore.CYAN}{label:<25}{color}{value:>28}{Style.RESET_ALL}")
        
        # Razões de fechamento
        print(f"\n{Fore.WHITE}  Razões de Fechamento:{Style.RESET_ALL}")
        for reason, count in metrics.get("close_reasons", {}).items():
            pct = (count / metrics["total_trades"]) * 100
            print(f"    {reason:<15} {count:>4} ({pct:.1f}%)")
        
        print_separator("=", 55)
    
    def run_multi_symbol_backtest(self, symbols: List[str],
                                   initial_capital: float = None) -> Dict:
        """Executa backtest para múltiplos símbolos."""
        if initial_capital is None:
            initial_capital = self.initial_capital
        
        all_results = {}
        total_pnl = 0
        total_trades = 0
        total_wins = 0
        
        capital_per_symbol = initial_capital / len(symbols)
        
        for symbol in symbols:
            print(f"\n  Processando {symbol}...")
            result = self.run_backtest(symbol, initial_capital=capital_per_symbol)
            all_results[symbol] = result
            
            if "total_trades" in result:
                total_pnl += result.get("total_pnl", 0)
                total_trades += result.get("total_trades", 0)
                total_wins += result.get("winning_trades", 0)
        
        # Resumo consolidado
        summary = {
            "symbols": symbols,
            "individual_results": all_results,
            "total_trades": total_trades,
            "total_pnl": total_pnl,
            "total_wins": total_wins,
            "overall_win_rate": (total_wins / total_trades * 100) if total_trades > 0 else 0,
            "initial_capital": initial_capital,
            "final_capital": initial_capital + total_pnl,
            "total_return_pct": (total_pnl / initial_capital * 100) if initial_capital > 0 else 0,
        }
        
        return summary
