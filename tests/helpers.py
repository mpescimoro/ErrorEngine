"""
Saved objects for the tests: queries with their channels and routing rules, channels.
"""
from models import db, MonitoredQuery, NotificationChannel, RoutingRule, RoutingCondition


def make_query(name='Orders', channels=(), rules=(), **kwargs):
    """
    A saved query, SELECT 1 keyed on ID unless given. rules: (recipients, [(field, value)]), with a dict of
    RoutingRule fields as a third item if needed; conditions compare with equals, a rule joins them with OR
    unless it says otherwise, and the rules take priority in the order given.
    """
    kwargs.setdefault('sql_query', 'SELECT 1')
    kwargs.setdefault('key_fields', 'ID')
    query = MonitoredQuery(name=name, **kwargs)
    query.notification_channels.extend(channels)
    db.session.add(query)
    db.session.flush()
    for i, (recipients, conditions, *options) in enumerate(rules):
        fields = {'priority': i, 'is_active': True, 'condition_logic': 'OR', **(options[0] if options else {})}
        rule = RoutingRule(query_id=query.id, name=f'Rule {i}', recipients=recipients, **fields)
        db.session.add(rule)
        db.session.flush()
        db.session.add_all(RoutingCondition(rule_id=rule.id, field_name=field, operator='equals', value=value)
                           for field, value in conditions)
    db.session.commit()
    return query


def make_channel(name, channel_type='webhook'):
    channel = NotificationChannel(name=name, channel_type=channel_type, config='{}', is_active=True)
    db.session.add(channel)
    db.session.commit()
    return channel
