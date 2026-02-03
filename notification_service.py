"""
Notification Service - Invio notifiche a Webhook, Telegram, Teams.
"""
import logging
import requests
from datetime import datetime
from utils import format_local_now

logger = logging.getLogger(__name__)


class NotificationService:
    
    def __init__(self, app=None):
        self.app = app
        self.timeout = 30
    
    def init_app(self, app):
        self.app = app
        self.timeout = app.config.get('HTTP_TIMEOUT_SECONDS', 30)
    
    def send_to_channel(self, channel, query, errors: list) -> dict:
        """Invia notifica a un canale specifico."""
        if not channel.is_active:
            return {'success': False, 'message': 'Canale disattivo'}
        
        try:
            config = channel.get_config()
            
            if channel.channel_type == 'webhook':
                result = self._send_webhook(config, query, errors)
            elif channel.channel_type == 'telegram':
                result = self._send_telegram(config, query, errors)
            elif channel.channel_type == 'teams':
                result = self._send_teams(config, query, errors)
            else:
                return {'success': False, 'message': f'Tipo non supportato: {channel.channel_type}'}
            
            # Aggiorna statistiche
            if result['success']:
                channel.total_sent += 1
                channel.last_sent_at = datetime.utcnow()
                channel.last_error = None
            else:
                channel.last_error = result.get('message', 'Errore')
            
            from models import db
            db.session.commit()
            
            return result
            
        except Exception as e:
            logger.error(f"Errore invio a {channel.name}: {e}")
            return {'success': False, 'message': str(e)}
    
    def send_to_all_channels(self, query, errors: list) -> dict:
        """Invia a tutti i canali associati alla query."""
        results = []
        success_count = 0
        
        for channel in query.notification_channels:
            if channel.is_active:
                result = self.send_to_channel(channel, query, errors)
                result['channel_name'] = channel.name
                results.append(result)
                if result['success']:
                    success_count += 1
        
        return {
            'total': len(results),
            'success': success_count,
            'failed': len(results) - success_count,
            'results': results
        }
    
    def _send_webhook(self, config: dict, query, errors: list) -> dict:
        """Invia a webhook generico."""
        url = config.get('url')
        if not url:
            return {'success': False, 'message': 'URL non configurato'}
        
        method = config.get('method', 'POST').upper()
        headers = config.get('headers', {})
        headers.setdefault('Content-Type', 'application/json')
        
        payload = {
            'event': 'errors_detected',
            'timestamp': datetime.utcnow().isoformat(),
            'query': {
                'id': query.id,
                'name': query.name,
                'description': query.description
            },
            'errors_count': len(errors),
            'errors': errors[:50]
        }
        
        try:
            response = requests.request(method, url, json=payload, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            return {'success': True, 'message': f'OK ({response.status_code})'}
        except requests.RequestException as e:
            return {'success': False, 'message': str(e)}
    
    def _send_telegram(self, config: dict, query, errors: list) -> dict:
        """Invia messaggio Telegram."""
        bot_token = config.get('bot_token')
        chat_id = config.get('chat_id')
        
        if not bot_token or not chat_id:
            return {'success': False, 'message': 'Bot token o chat_id mancante'}
        
        # Formatta messaggio
        lines = [
            f"<b>ErrorEngine</b>",
            f"",
            f"<b>Consultazione:</b> {query.name}",
            f"<b>Errori:</b> {len(errors)}",
            f"<b>Data:</b> {format_local_now('%d/%m/%Y %H:%M')}",
        ]
        
        if errors:
            lines.append("")
            lines.append("<b>Dettagli:</b>")
            for error in errors[:5]:
                preview = " | ".join([f"{k}: {v}" for k, v in list(error.items())[:2]])
                if len(preview) > 80:
                    preview = preview[:77] + "..."
                lines.append(f"• {preview}")
            if len(errors) > 5:
                lines.append(f"<i>...e altri {len(errors) - 5}</i>")
        
        message = "\n".join(lines)
        
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True
        }
        
        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
            data = response.json()
            if data.get('ok'):
                return {'success': True, 'message': 'Inviato'}
            else:
                return {'success': False, 'message': data.get('description', 'Errore Telegram')}
        except requests.RequestException as e:
            return {'success': False, 'message': str(e)}
    
    def _send_teams(self, config: dict, query, errors: list) -> dict:
        """Invia a Microsoft Teams via Incoming Webhook."""
        webhook_url = config.get('webhook_url')
        if not webhook_url:
            return {'success': False, 'message': 'Webhook URL mancante'}
        
        # MessageCard per Teams
        facts = [
            {"name": "Errori", "value": str(len(errors))},
            {"name": "Data", "value": format_local_now('%d/%m/%Y %H:%M')},
        ]
        
        # Aggiungi primi errori come facts
        for i, error in enumerate(errors[:3]):
            preview = " | ".join([f"{k}: {v}" for k, v in list(error.items())[:2]])
            if len(preview) > 60:
                preview = preview[:57] + "..."
            facts.append({"name": f"Errore {i+1}", "value": preview})
        
        card = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": "d63384",
            "summary": f"ErrorEngine: {len(errors)} errori",
            "sections": [{
                "activityTitle": f"{query.name}",
                "activitySubtitle": "Nuovi errori rilevati",
                "facts": facts,
                "markdown": True
            }]
        }
        
        try:
            response = requests.post(webhook_url, json=card, timeout=self.timeout)
            if response.status_code == 200:
                return {'success': True, 'message': 'Inviato'}
            else:
                return {'success': False, 'message': f'Errore: {response.status_code}'}
        except requests.RequestException as e:
            return {'success': False, 'message': str(e)}
    
    def test_channel(self, channel) -> dict:
        """Test con dati fittizi."""
        class MockQuery:
            id = 0
            name = "Test Consultazione"
            description = "Messaggio di test"
        
        test_errors = [
            {'campo1': 'valore1', 'campo2': 'valore2'},
            {'campo1': 'valore3', 'campo2': 'valore4'}
        ]
        
        return self.send_to_channel(channel, MockQuery(), test_errors)


notification_service = NotificationService()