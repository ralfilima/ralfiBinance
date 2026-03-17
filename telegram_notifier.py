"""
telegram_notifier.py - Gerenciamento de notificações via Telegram.
Envio de alertas, resumos e comandos remotos.
"""

import time
import threading
import requests
from typing import Optional, Callable, Dict, List
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from utils.logger import logger


class TelegramNotifier:
    """Gerenciador de notificações e comandos via Telegram."""
    
    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token or TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or TELEGRAM_CHAT_ID
        self.enabled = bool(self.bot_token and self.chat_id)
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.last_update_id = 0
        self._command_handlers: Dict[str, Callable] = {}
        self._polling_thread: Optional[threading.Thread] = None
        self._polling_active = False
        
        if not self.enabled:
            logger.warning("Telegram não configurado. Notificações desabilitadas.")
    
    def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Envia mensagem para o chat configurado."""
        if not self.enabled:
            return False
        
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True
            }
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                return True
            else:
                logger.warning(f"Telegram API retornou {response.status_code}: {response.text}")
                return False
        except Exception as e:
            logger.warning(f"Erro ao enviar mensagem Telegram: {e}")
            return False
    
    # --- Mensagens Pré-formatadas ---
    
    def notify_bot_start(self, mode: str, balance: float):
        """Notifica início do bot."""
        msg = (
            f"🤖 <b>Bot Iniciado</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Modo: <b>{mode}</b>\n"
            f"💰 Saldo: <b>{balance:.2f} USDT</b>\n"
            f"⏰ {time.strftime('%d/%m/%Y %H:%M:%S')}"
        )
        self.send_message(msg)
    
    def notify_bot_stop(self, reason: str = "Manual"):
        """Notifica parada do bot."""
        msg = (
            f"🛑 <b>Bot Parado</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 Motivo: {reason}\n"
            f"⏰ {time.strftime('%d/%m/%Y %H:%M:%S')}"
        )
        self.send_message(msg)
    
    def notify_position_open(self, symbol: str, side: str, quantity: float,
                              entry_price: float, sl: float, tp: float):
        """Notifica abertura de posição."""
        emoji = "🟢" if side == "LONG" else "🔴"
        msg = (
            f"{emoji} <b>Posição Aberta</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {symbol} | {side}\n"
            f"📦 Quantidade: {quantity:.4f}\n"
            f"💵 Entrada: {entry_price:.4f}\n"
            f"🛡 Stop Loss: {sl:.4f}\n"
            f"🎯 Take Profit: {tp:.4f}\n"
            f"⏰ {time.strftime('%H:%M:%S')}"
        )
        self.send_message(msg)
    
    def notify_position_close(self, symbol: str, side: str, reason: str,
                               pnl: float, pnl_percent: float, duration_min: float):
        """Notifica fechamento de posição."""
        emoji = "✅" if pnl >= 0 else "❌"
        reason_map = {
            "TAKE_PROFIT": "🎯 Take Profit",
            "STOP_LOSS": "🛡 Stop Loss",
            "TIME_STOP": "⏰ Time Stop",
            "TRAILING_STOP": "📈 Trailing Stop",
            "MANUAL_CLOSE": "👤 Fechamento Manual",
            "DRAWDOWN": "⚠️ Drawdown",
        }
        reason_text = reason_map.get(reason, reason)
        
        msg = (
            f"{emoji} <b>Posição Fechada</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {symbol} | {side}\n"
            f"📝 Motivo: {reason_text}\n"
            f"💰 P&L: <b>{pnl:+.2f} USDT ({pnl_percent:+.2f}%)</b>\n"
            f"⏱ Duração: {duration_min:.0f} min\n"
            f"⏰ {time.strftime('%H:%M:%S')}"
        )
        self.send_message(msg)
    
    def notify_risk_alert(self, alert_type: str, details: str):
        """Notifica alerta de risco."""
        msg = (
            f"⚠️ <b>ALERTA DE RISCO</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔔 Tipo: {alert_type}\n"
            f"📝 {details}\n"
            f"⏰ {time.strftime('%H:%M:%S')}"
        )
        self.send_message(msg)
    
    def notify_daily_summary(self, total_trades: int, win_rate: float,
                              daily_pnl: float, balance: float, drawdown: float):
        """Envia resumo diário de performance."""
        emoji = "📈" if daily_pnl >= 0 else "📉"
        msg = (
            f"{emoji} <b>Resumo Diário</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Trades: {total_trades}\n"
            f"🎯 Win Rate: {win_rate:.1f}%\n"
            f"💰 P&L Diário: <b>{daily_pnl:+.2f} USDT</b>\n"
            f"💼 Saldo: {balance:.2f} USDT\n"
            f"📉 Drawdown: {drawdown:.2f}%\n"
            f"📅 {time.strftime('%d/%m/%Y')}"
        )
        self.send_message(msg)
    
    def notify_asset_selection(self, assets: List[str], btc_trend: str):
        """Notifica seleção de ativos."""
        assets_text = "\n".join([f"  • {a}" for a in assets])
        msg = (
            f"🔍 <b>Ativos Selecionados</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Tendência BTC: {btc_trend}\n"
            f"📋 Portfólio:\n{assets_text}\n"
            f"⏰ {time.strftime('%H:%M:%S')}"
        )
        self.send_message(msg)
    
    # --- Comandos Remotos ---
    
    def register_command(self, command: str, handler: Callable):
        """Registra handler para um comando do Telegram."""
        self._command_handlers[command] = handler
    
    def start_polling(self):
        """Inicia polling de comandos em thread separada."""
        if not self.enabled:
            return
        
        self._polling_active = True
        self._polling_thread = threading.Thread(
            target=self._poll_updates, daemon=True
        )
        self._polling_thread.start()
        logger.info("Telegram: polling de comandos iniciado")
    
    def stop_polling(self):
        """Para o polling de comandos."""
        self._polling_active = False
        if self._polling_thread:
            self._polling_thread.join(timeout=5)
    
    def _poll_updates(self):
        """Loop de polling para receber comandos."""
        while self._polling_active:
            try:
                url = f"{self.base_url}/getUpdates"
                params = {
                    "offset": self.last_update_id + 1,
                    "timeout": 10,
                    "allowed_updates": ["message"]
                }
                response = requests.get(url, params=params, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    for update in data.get("result", []):
                        self.last_update_id = update["update_id"]
                        self._process_update(update)
            except Exception as e:
                logger.debug(f"Telegram polling erro: {e}")
                time.sleep(5)
    
    def _process_update(self, update: dict):
        """Processa uma atualização recebida."""
        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))
        
        # Verificar se é do chat autorizado
        if chat_id != self.chat_id:
            return
        
        if text.startswith("/"):
            command = text.split()[0].lower().replace("/", "")
            handler = self._command_handlers.get(command)
            
            if handler:
                try:
                    response = handler(text)
                    if response:
                        self.send_message(response)
                except Exception as e:
                    self.send_message(f"❌ Erro ao executar comando: {e}")
            else:
                available = ", ".join([f"/{c}" for c in self._command_handlers.keys()])
                self.send_message(
                    f"❓ Comando desconhecido.\n"
                    f"Comandos disponíveis: {available}"
                )
    
    def test_connection(self) -> bool:
        """Testa conexão com o Telegram."""
        if not self.enabled:
            return False
        
        try:
            url = f"{self.base_url}/getMe"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                bot_info = response.json().get("result", {})
                logger.info(f"Telegram conectado: @{bot_info.get('username', 'N/A')}")
                return True
            return False
        except Exception as e:
            logger.warning(f"Teste de conexão Telegram falhou: {e}")
            return False
