"""Shared proactive outreach cooldown; reply permissions are independent."""
import json

MARKETING_COOLDOWN_SECONDS = 72 * 60 * 60


def last_contact_by_creator(db, plan_id=None, *, creator_id=None, current_delivery_id=None):
    """Read local delivery and observed institution outbounds without creating state.

    The current card/text pair is one outreach. Only that delivery's confirmed
    platform message IDs in its original conversation are excluded before its
    next component; any additional institution message still starts cooldown.
    """
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    latest = {}
    if 'cycle_delivery' in tables and 'cycle_delivery_part' in tables:
        where = ["d.state IN ('confirmed','partial_delivery')"]
        args = []
        for column, value in [('plan_id', plan_id), ('creator_id', creator_id)]:
            if value is not None:
                where.append(f'd.{column}=?')
                args.append(value)
        if current_delivery_id is not None:
            where.append('d.id<>?')
            args.append(current_delivery_id)
        rows = db.execute("SELECT d.creator_id,max(p.started) FROM cycle_delivery d "
                          "JOIN cycle_delivery_part p ON p.delivery_id=d.id WHERE " +
                          ' AND '.join(where) + ' GROUP BY d.creator_id', args)
        latest.update((row[0], row[1]) for row in rows if row[1] is not None)
    if 'inbox_event' not in tables:
        return latest
    where = ["e.kind='ourMessages'", 'e.occurred_ms IS NOT NULL']
    args = []
    for column, value in [('plan_id', plan_id), ('creator_id', creator_id)]:
        if value is not None:
            where.append(f'r.{column}=?')
            args.append(value)
    if current_delivery_id is not None:
        delivery = db.execute('SELECT plan_id,creator_id,snapshot FROM cycle_delivery WHERE id=?',
                              (current_delivery_id,)).fetchone()
        if delivery and delivery[0] == plan_id and delivery[1] == creator_id:
            cid = json.loads(delivery[2]).get('conversationId')
            if not cid and 'cycle_conversation_intent' in tables:
                intent = db.execute('SELECT cid FROM cycle_conversation_intent WHERE delivery_id=?',
                                    (current_delivery_id,)).fetchone()
                cid = intent[0] if intent else None
            if cid:
                for row in db.execute("SELECT confirmation FROM cycle_delivery_part "
                                      "WHERE delivery_id=? AND state='confirmed'", (current_delivery_id,)):
                    mid = json.loads(row[0] or '{}').get('messageId')
                    if mid:
                        where.append('NOT (e.cid=? AND e.message_id=?)')
                        args.extend([str(cid), str(mid)])
    rows = db.execute("SELECT r.creator_id,max(e.occurred_ms)/1000.0 FROM inbox_event e "
                      "JOIN relationship r ON r.plan_id=e.plan_id AND r.oec=e.oec WHERE " +
                      ' AND '.join(where) + ' GROUP BY r.creator_id', args)
    for creator, stamp in rows:
        latest[creator] = max(stamp, latest.get(creator, stamp))
    return latest
