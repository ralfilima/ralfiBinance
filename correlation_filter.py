"""
correlation_filter.py - Filtro de correlação entre ativos para diversificação.
Utiliza correlação de Pearson para remover ativos excessivamente correlacionados.
"""

import numpy as np
import pandas as pd
from typing import List, Dict
from utils.logger import logger


def calculate_correlation_matrix(price_data: Dict[str, pd.Series]) -> pd.DataFrame:
    """
    Calcula a matriz de correlação de Pearson entre múltiplos ativos.
    
    Args:
        price_data: Dicionário {símbolo: série de preços de fechamento}
    
    Returns:
        DataFrame com a matriz de correlação
    """
    df = pd.DataFrame(price_data)
    
    # Usar retornos percentuais para correlação (mais estável)
    returns = df.pct_change().dropna()
    
    if len(returns) < 10:
        logger.warning("Dados insuficientes para cálculo de correlação")
        return pd.DataFrame()
    
    return returns.corr(method="pearson")


def filter_correlated_assets(symbols: List[str], price_data: Dict[str, pd.Series],
                              threshold: float = 0.85, max_assets: int = 5) -> List[str]:
    """
    Filtra ativos com correlação excessiva, mantendo diversificação.
    
    Algoritmo:
    1. Calcula matriz de correlação
    2. Itera pelos símbolos em ordem de prioridade
    3. Remove símbolos com correlação > threshold com qualquer já selecionado
    
    Args:
        symbols: Lista de símbolos candidatos (em ordem de prioridade)
        price_data: Dicionário {símbolo: série de preços}
        threshold: Limite de correlação (padrão: 0.85)
        max_assets: Máximo de ativos a selecionar
    
    Returns:
        Lista de símbolos filtrados
    """
    if len(symbols) <= 1:
        return symbols[:max_assets]
    
    # Filtrar apenas símbolos com dados disponíveis
    available = [s for s in symbols if s in price_data and len(price_data[s]) > 10]
    
    if len(available) <= 1:
        return available[:max_assets]
    
    # Calcular correlação
    corr_matrix = calculate_correlation_matrix(
        {s: price_data[s] for s in available}
    )
    
    if corr_matrix.empty:
        return available[:max_assets]
    
    selected = []
    
    for symbol in available:
        if len(selected) >= max_assets:
            break
        
        if symbol not in corr_matrix.columns:
            continue
        
        # Verificar correlação com já selecionados
        is_correlated = False
        for sel in selected:
            if sel in corr_matrix.columns:
                corr = abs(corr_matrix.loc[symbol, sel])
                if corr > threshold:
                    logger.info(
                        f"  {symbol} removido: correlação {corr:.2f} com {sel} "
                        f"(limite: {threshold})"
                    )
                    is_correlated = True
                    break
        
        if not is_correlated:
            selected.append(symbol)
    
    logger.info(f"Filtro de correlação: {len(symbols)} → {len(selected)} ativos")
    return selected
