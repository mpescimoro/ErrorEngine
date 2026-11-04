"""
Notification Service - Sends notifications to Webhook, Telegram, Teams.
"""
import json
import logging
import requests
from html import escape
from utils import format_local_now, get_utc_now, redact_secrets

logger = logging.getLogger(__name__)


class NotificationService:
    
    def __init__(self, app=None):
        self.app = app
        self.timeout = 30
    
    def init_app(self, app):
        self.app = app
        self.timeout = app.config.get('HTTP_TIMEOUT_SECONDS', 30)
    
    def send_to_channel(self, channel, query, errors: list) -> dict:
        """Sends a notification to a specific channel."""
        if not channel.is_active:
            return {'success': False, 'message': 'Channel inactive'}
        
        try:
            config = channel.get_config()
            
            if channel.channel_type == 'webhook':
                result = self._send_webhook(config, query, errors)
            elif channel.channel_type == 'telegram':
                result = self._send_telegram(config, query, errors)
            elif channel.channel_type == 'teams':
                result = self._send_teams(config, query, errors)
            else:
                return {'success': False, 'message': f'Unsupported type: {channel.channel_type}'}
            
            # Update the statistics
            if result['success']:
                channel.total_sent += 1
                channel.last_sent_at = get_utc_now()
                channel.last_error = None
            else:
                channel.last_error = result.get('message', 'Error')
            
            from models import db
            db.session.commit()
            
            return result
            
        except Exception as e:
            logger.error(f"Error sending to {channel.name}: {e}")
            return {'success': False, 'message': redact_secrets(str(e))}
    
    def send_to_all_channels(self, query, errors: list, channel_ids=None) -> dict:
        """
        Sends to all the active channels of the query
        (only those in channel_ids, if given: used to retry the channels that failed).
        """
        results = []
        success_count = 0

        for channel in query.notification_channels:
            if channel_ids is not None and channel.id not in channel_ids:
                continue
            if channel.is_active:
                result = self.send_to_channel(channel, query, errors)
                result['channel_name'] = channel.name
                result['channel_id'] = channel.id
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
        """Sends to a generic webhook."""
        url = config.get('url')
        if not url:
            return {'success': False, 'message': 'URL not configured'}
        
        method = config.get('method', 'POST').upper()
        headers = config.get('headers', {})
        headers.setdefault('Content-Type', 'application/json')
        
        payload = {
            'event': 'errors_detected',
            'timestamp': get_utc_now().isoformat(),
            'query': {
                'id': query.id,
                'name': query.name,
                'description': query.description
            },
            'errors_count': len(errors),
            'errors': errors[:50]
        }
        return self._post_json(url, payload, headers, method)

    def _post_json(self, url: str, payload: dict, headers: dict, method: str = 'POST') -> dict:
        """POST/PUT of a JSON; non-standard values (Decimal, dates) become text."""
        try:
            body = json.dumps(payload, default=str, ensure_ascii=False).encode('utf-8')
            response = requests.request(method, url, data=body, headers=headers, timeout=self.timeout)
            if 200 <= response.status_code < 300:
                return {'success': True, 'message': f'OK ({response.status_code})'}
            return self._http_failure(response.status_code, response.reason)
        except requests.RequestException as e:
            return {'success': False, 'message': redact_secrets(str(e))}

    @staticmethod
    def _http_failure(status: int, reason: str = '') -> dict:
        """
        A failed HTTP answer. 4xx (except 408 and 429) is a definitive refusal
        (wrong URL, credentials...): retrying does not help.
        """
        permanent = 400 <= status < 500 and status not in (408, 429)
        reason = reason if isinstance(reason, str) else ''
        return {'success': False, 'permanent': permanent,
                'message': f"HTTP {status}{' ' + reason if reason else ''}"}
    
    def _send_telegram(self, config: dict, query, errors: list) -> dict:
        """Sends a Telegram message."""
        bot_token = config.get('bot_token')
        chat_id = config.get('chat_id')
        
        if not bot_token or not chat_id:
            return {'success': False, 'message': 'Bot token or chat_id missing'}
        
        # Format the message (parse_mode HTML: data must be converted to entities,
        # otherwise Telegram rejects messages containing <, > or &)
        lines = [
            "<b>ErrorEngine</b>",
            "",
            f"<b>Query:</b> {escape(str(query.name))}",
            f"<b>Errors:</b> {len(errors)}",
            f"<b>Data:</b> {format_local_now('%d/%m/%Y %H:%M')}",
        ]

        if errors:
            lines.append("")
            lines.append("<b>Details:</b>")
            for error in errors[:5]:
                preview = " | ".join([f"{k}: {v}" for k, v in list(error.items())[:2]])
                if len(preview) > 80:
                    preview = preview[:77] + "..."
                lines.append(f"• {escape(preview)}")
            if len(errors) > 5:
                lines.append(f"<i>...and {len(errors) - 5} more</i>")

        return self._post_telegram(bot_token, chat_id, "\n".join(lines))

    def _post_telegram(self, bot_token: str, chat_id: str, message: str) -> dict:
        """Sends a message already formatted in HTML (with the data already converted to entities)."""
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            'chat_id': chat_id,
            'text': message,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True
        }

        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
            try:
                data = response.json()
            except ValueError:
                data = {}
            if data.get('ok'):
                return {'success': True, 'message': 'Sent'}
            result = self._http_failure(response.status_code)
            result['message'] = data.get('description') or result['message']
            return result
        except requests.RequestException as e:
            return {'success': False, 'message': redact_secrets(str(e))}
    
    def _send_teams(self, config: dict, query, errors: list) -> dict:
        """Sends to Microsoft Teams through a webhook."""
        webhook_url = config.get('webhook_url')
        if not webhook_url:
            return {'success': False, 'message': 'Webhook URL missing'}
        
        # MessageCard for Teams
        facts = [
            {"name": "Errors", "value": str(len(errors))},
            {"name": "Data", "value": format_local_now('%d/%m/%Y %H:%M')},
        ]
        
        # Add the first errors as facts
        for i, error in enumerate(errors[:3]):
            preview = " | ".join([f"{k}: {v}" for k, v in list(error.items())[:2]])
            if len(preview) > 60:
                preview = preview[:57] + "..."
            facts.append({"name": f"Error {i+1}", "value": preview})
        
        card = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": "d63384",
            "summary": f"ErrorEngine: {len(errors)} errors",
            "sections": [{
                "activityTitle": f"{query.name}",
                "activitySubtitle": "New errors detected",
                "facts": facts,
                "markdown": True
            }]
        }
        return self._post_teams(webhook_url, card)

    def _post_teams(self, webhook_url: str, card: dict) -> dict:
        """Sends a card to the Teams webhook."""
        try:
            body = json.dumps(card, default=str, ensure_ascii=False).encode('utf-8')
            response = requests.post(webhook_url, data=body, timeout=self.timeout,
                                     headers={'Content-Type': 'application/json'})
            # Workflows webhooks answer 202 Accepted: any 2xx means delivered
            if 200 <= response.status_code < 300:
                return {'success': True, 'message': 'Sent'}
            return self._http_failure(response.status_code, response.reason)
        except requests.RequestException as e:
            return {'success': False, 'message': redact_secrets(str(e))}

    def send_system_message(self, query, event: str, title: str, lines: list) -> dict:
        """
        Service message to the active channels of the query (e.g. query failing or
        recovered). Returns {'total': channels contacted, 'success': channels reached}.
        """
        total = success = 0
        for channel in query.notification_channels:
            if not channel.is_active:
                continue
            total += 1
            try:
                config = channel.get_config()
                if channel.channel_type == 'webhook':
                    if not config.get('url'):
                        result = {'success': False, 'message': 'URL not configured'}
                    else:
                        headers = dict(config.get('headers', {}))
                        headers.setdefault('Content-Type', 'application/json')
                        payload = {
                            'event': event,
                            'timestamp': get_utc_now().isoformat(),
                            'query': {'id': query.id, 'name': query.name, 'description': query.description},
                            'title': title,
                            'message': '\n'.join(lines),
                        }
                        result = self._post_json(config['url'], payload, headers,
                                                 config.get('method', 'POST').upper())
                elif channel.channel_type == 'telegram':
                    if not config.get('bot_token') or not config.get('chat_id'):
                        result = {'success': False, 'message': 'Bot token or chat_id missing'}
                    else:
                        text = "\n".join([f"<b>{escape(title)}</b>", ""] + [escape(line) for line in lines])
                        result = self._post_telegram(config['bot_token'], config['chat_id'], text)
                elif channel.channel_type == 'teams':
                    if not config.get('webhook_url'):
                        result = {'success': False, 'message': 'Webhook URL missing'}
                    else:
                        card = {
                            "@type": "MessageCard",
                            "@context": "http://schema.org/extensions",
                            "themeColor": "d63384",
                            "summary": title,
                            "sections": [{"activityTitle": title, "text": "\n\n".join(lines)}]
                        }
                        result = self._post_teams(config['webhook_url'], card)
                else:
                    result = {'success': False, 'message': f'Unsupported type: {channel.channel_type}'}
            except Exception as e:
                result = {'success': False, 'message': redact_secrets(str(e))}

            if result['success']:
                success += 1
            else:
                logger.error(f"Service message not sent to {channel.name}: {result['message']}")
        return {'total': total, 'success': success}
    
    def test_channel(self, channel) -> dict:
        """Test with dummy data."""
        class MockQuery:
            id = 0
            name = "Test query"
            description = "Test message"
        
        test_errors = [
            {'field1': 'value1', 'field2': 'value2'},
            {'field1': 'value3', 'field2': 'value4'}
        ]
        
        return self.send_to_channel(channel, MockQuery(), test_errors)


notification_service = NotificationService()