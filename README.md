# Binance Futures Scalping Bot

Bot de trading automatizado para operar contratos futuros na Binance, utilizando uma estratégia de scalping que opera múltiplas criptomoedas simultaneamente.

---

## DISCLAIMER

> **AVISO IMPORTANTE:** Este bot é uma ferramenta de auxílio à decisão de trading. **NÃO há garantia de lucros.** Operar futuros com alavancagem envolve **ALTO RISCO** e você pode perder **TODO** o seu capital investido. O usuário final é o **ÚNICO** responsável por qualquer perda financeira. **Nunca invista mais do que pode perder.**

---

## Funcionalidades

### Menu Interativo
O bot possui um menu interativo completo no terminal com as seguintes opções:

| Opção | Descrição |
|-------|-----------|
| **Modo Testnet** | Operação segura em ambiente simulado da Binance |
| **Modo Conta Real** | Operação com dinheiro real (com confirmação de segurança) |
| **Bot Automático** | Inicia o loop de trading automatizado |
| **Saldo e Posições** | Consulta saldo e posições abertas |
| **Análise de Mercado** | Executa análise técnica dos ativos selecionados |
| **Backtesting** | Simula a estratégia com dados históricos |
| **Configurações** | Exibe todas as configurações atuais |
| **Telegram** | Testa conexão com notificações Telegram |
| **Trocar Modo** | Alterna entre Testnet e Conta Real |
| **Fechar Posições** | Fecha todas as posições abertas |

### Estratégia de Trading

A estratégia utiliza múltiplos indicadores técnicos para decisões de entrada e saída:

**Seleção de Ativos:**
1. Identifica as 15 criptomoedas com maior volume nos futuros
2. Aplica filtro de persistência (3 verificações consecutivas)
3. Filtra correlação (Pearson < 0.85) para diversificação
4. Seleciona até 5 ativos para o portfólio

**Indicadores:**
- EMA 9 e EMA 21 (tendência de curto prazo)
- RSI 14 (momentum)
- Bandas de Bollinger (20, 2) (suporte/resistência dinâmicos)
- ATR 14 (volatilidade para position sizing)

**Regras de Entrada:**
- **LONG:** Preço > EMA9 > EMA21, RSI entre 50-70, preço abaixo da banda superior
- **SHORT:** Preço < EMA9 < EMA21, RSI entre 30-50, preço acima da banda inferior

### Gestão de Risco

| Parâmetro | Valor Padrão | Descrição |
|-----------|-------------|-----------|
| Risco por Trade | 1% | Percentual do capital arriscado por operação |
| Max Posições | 5 | Número máximo de posições simultâneas |
| Perda Diária Máx | 3% | Bot para ao atingir este limite |
| Drawdown Máximo | 10% | Todas as posições são fechadas |
| Perdas Consecutivas | 3 | Pausa após N perdas seguidas |
| Trailing Stop | +0.5% / 0.3% | Ativação e callback do trailing |
| Time Stop | 30-90 min | Fechamento por tempo (aleatório) |

### Segurança e Robustez

- **Circuit Breaker:** Interrompe chamadas à API após falhas consecutivas
- **Retry com Backoff:** Tentativas com intervalo exponencial
- **Sanity Checks:** Validação de todos os dados da API
- **Fechamento Robusto:** Múltiplos métodos de fallback para fechar posições
- **Credenciais Seguras:** Variáveis de ambiente via `.env`

### Notificações Telegram

O bot envia notificações para:
- Início e parada do bot
- Abertura e fechamento de posições
- Alertas de risco (drawdown, perda diária)
- Seleção de ativos

**Comandos remotos via Telegram:**
- `/status` - Status do bot
- `/posicoes` - Posições abertas
- `/saldo` - Saldo atual
- `/pausar` - Pausar o bot
- `/retomar` - Retomar operações

---

## Instalação

### Pré-requisitos
- Python 3.10 ou superior
- Conta na Binance com API habilitada para Futuros
- (Opcional) Bot do Telegram para notificações

### Passo a Passo

```bash
# 1. Clonar/copiar o projeto
cd binance_bot

# 2. Instalar dependências
pip install -r requirements.txt

# 3. Configurar credenciais
cp .env.example .env
nano .env  # Editar com suas credenciais

# 4. Executar o bot
python main.py
```

### Configuração do `.env`

```env
# Binance API (Conta Real)
BINANCE_API_KEY=sua_api_key_real
BINANCE_API_SECRET=sua_api_secret_real

# Binance API (Testnet)
BINANCE_TESTNET_API_KEY=sua_api_key_testnet
BINANCE_TESTNET_API_SECRET=sua_api_secret_testnet

# Telegram (opcional)
TELEGRAM_BOT_TOKEN=seu_token
TELEGRAM_CHAT_ID=seu_chat_id

# Modo inicial
USE_TESTNET=true
```

### Obtendo Chaves da Testnet

1. Acesse [Binance Futures Testnet](https://testnet.binancefuture.com/)
2. Faça login com sua conta GitHub
3. Gere as chaves de API em "API Key"
4. Copie API Key e Secret para o `.env`

### Configurando o Telegram

1. Fale com [@BotFather](https://t.me/BotFather) no Telegram
2. Crie um bot com `/newbot`
3. Copie o token gerado
4. Fale com [@userinfobot](https://t.me/userinfobot) para obter seu Chat ID
5. Configure no `.env`

---

## Arquitetura

```
binance_bot/
├── main.py                 # Menu interativo e ponto de entrada
├── config.py               # Configurações e variáveis de ambiente
├── binance_client.py       # Wrapper da API com circuit breaker
├── strategy_engine.py      # Núcleo da estratégia de trading
├── risk_manager.py         # Gestão de risco e position sizing
├── position_manager.py     # Gerenciamento de posições e trailing stop
├── indicators.py           # Indicadores técnicos (EMA, RSI, BB, ATR)
├── correlation_filter.py   # Filtro de correlação entre ativos
├── telegram_notifier.py    # Notificações e comandos Telegram
├── backtest_engine.py      # Motor de backtesting
├── requirements.txt        # Dependências
├── .env.example            # Template de configuração
├── .gitignore              # Arquivos ignorados pelo Git
├── utils/
│   ├── logger.py           # Sistema de logging
│   └── helpers.py          # Funções auxiliares
├── tests/
│   ├── test_indicators.py  # Testes de indicadores
│   └── test_risk_manager.py # Testes de gestão de risco
├── logs/                   # Logs de execução
└── backtest_data/          # Cache de dados históricos
```

---

## Fluxo de Operação

1. **Inicialização:** Carrega configurações, conecta à Binance, fecha posições prévias
2. **Seleção de Ativos:** Identifica top moedas por volume, aplica filtros
3. **Análise:** Calcula indicadores para cada ativo selecionado
4. **Entrada:** Abre posições quando sinais são detectados (respeitando limites)
5. **Monitoramento:** Atualiza preços, verifica SL/TP/trailing/time stop
6. **Dashboard:** Exibe informações em tempo real no terminal
7. **Notificações:** Envia alertas via Telegram
8. **Parada:** Ctrl+C fecha posições ordenadamente

---

## Testes

```bash
# Executar todos os testes
cd binance_bot
python -m pytest tests/ -v

# Executar teste específico
python -m pytest tests/test_indicators.py -v
python -m pytest tests/test_risk_manager.py -v
```

---

## Recomendações de Segurança

1. **Nunca** compartilhe suas chaves de API
2. Configure permissões mínimas na API (apenas Futuros, sem Saques)
3. **Sempre** teste na Testnet antes de usar dinheiro real
4. Monitore o bot regularmente
5. Defina limites de risco conservadores
6. Mantenha o arquivo `.env` fora do controle de versão

---

## Licença

Este projeto é fornecido "como está", sem garantias de qualquer tipo. Use por sua conta e risco.
