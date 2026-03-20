# DCA Bot Inteligente - Binance Futures

Bot de trading automatizado para Binance Futures que opera 5 moedas simultaneamente usando a estrategia de **Dollar Cost Averaging (DCA)** com **Stop Profit Global**.

## Filosofia

Em vez de tentar acertar o timing perfeito de entrada e saida, o bot:

1. **Entra em 5 moedas** simultaneamente com posicoes pequenas (25% do capital alocado)
2. **Quando uma moeda cai**, faz DCA automatico (compra mais barato, baixando o preco medio)
3. **Cada moeda so fecha quando esta no LUCRO** (gracas ao DCA, o preco medio e sempre mais favoravel)
4. **Stop Profit GLOBAL**: quando a soma das 5 moedas atinge o target (+0.5% do capital), fecha TUDO
5. **Ciclos infinitos**: fecha, seleciona novas 5 moedas, recomeca

## Estrutura

```
binance_dca_bot/
├── main.py              # Menu interativo + dashboard visual
├── config.py            # Todas as configuracoes
├── binance_client.py    # Wrapper Binance com circuit breaker
├── coin_selector.py     # Selecao inteligente de moedas
├── dca_engine.py        # Motor DCA (averaging down)
├── portfolio_manager.py # Gestao de portfolio + stop profit global
├── utils/
│   ├── logger.py        # Logger centralizado
│   └── helpers.py       # Funcoes utilitarias
├── requirements.txt
├── .env.example
└── README.md
```

## Instalacao

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Configurar credenciais
cp .env.example .env
# Edite o .env com suas chaves API

# 3. Executar
python main.py
```

## Configuracao (.env)

```env
# Testnet (recomendado para testes)
BINANCE_TESTNET_API_KEY=sua_key
BINANCE_TESTNET_API_SECRET=sua_secret

# Conta Real
BINANCE_API_KEY=sua_key_real
BINANCE_API_SECRET=sua_secret_real

# Telegram (opcional)
TELEGRAM_BOT_TOKEN=seu_token
TELEGRAM_CHAT_ID=seu_chat_id
```

## Parametros Principais (config.py)

| Parametro | Valor | Descricao |
|-----------|-------|-----------|
| NUM_COINS | 5 | Moedas simultaneas |
| LEVERAGE | 10 | Alavancagem |
| CAPITAL_PER_COIN_PCT | 15% | Capital por moeda (5x15%=75%, 25% reserva) |
| INITIAL_ENTRY_PCT | 25% | Entrada inicial (% do capital da moeda) |
| GLOBAL_TAKE_PROFIT_PCT | 0.5% | Target de lucro global |
| GLOBAL_STOP_LOSS_PCT | 3% | Stop loss de emergencia |
| MAX_DCA_ORDERS | 5 | Maximo de DCAs por moeda |

## Niveis de DCA

| Nivel | Queda | Capital Adicionado |
|-------|-------|--------------------|
| 1 | -0.5% | 20% do restante |
| 2 | -1.0% | 25% do restante |
| 3 | -2.0% | 30% do restante |
| 4 | -3.5% | 50% do restante |
| 5 | -5.0% | 100% do restante (all-in) |

## Como Funciona

### Selecao de Moedas
- Filtra por volume minimo (10M USDT/24h) e preco minimo (0.01 USDT)
- Analisa indicadores: EMA 9/21, RSI, Bollinger Bands, ATR
- Rankeia por score composto (momentum + volume + volatilidade + posicao BB)
- Seleciona as 5 melhores com diversificacao LONG/SHORT

### Motor DCA
- Entrada inicial pequena (25% do capital da moeda)
- Quando o preco cai, faz DCA automatico nos niveis configurados
- Cada DCA baixa o preco medio, facilitando a recuperacao
- Queda e calculada desde o ultimo DCA (nao desde a entrada original)

### Stop Profit Global
- Monitora a soma do P&L de TODAS as 5 moedas
- Quando a soma atinge o target (+0.5% do capital), fecha TUDO
- Novo ciclo: seleciona novas 5 moedas e recomeca
- Stop loss de emergencia: -3% fecha tudo

## Aviso de Risco

Trading de criptomoedas envolve risco significativo de perda. Este bot e uma ferramenta automatizada, NAO uma garantia de lucro. Use apenas capital que voce pode perder. Teste sempre na Testnet antes de usar com dinheiro real.
