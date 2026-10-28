"""在业务事务内按金币流水生成可追溯的余额核对报告。"""
from .db import business_state


def reconcile_ledger(conn, event):
    opening = conn.execute('SELECT opening_gold FROM accounting_baseline WHERE id=1').fetchone()['opening_gold']
    totals = conn.execute("SELECT COALESCE(SUM(CASE WHEN action='open_vault' AND amount>0 THEN amount ELSE 0 END),0) AS credits, "
                          "COALESCE(SUM(CASE WHEN action='open_vault' AND amount<0 THEN -amount ELSE 0 END),0) AS debits, "
                          'COUNT(*) AS count, COALESCE(MAX(id),0) AS last_id FROM business_ledger').fetchone()
    expected, actual = opening + totals['credits'] - totals['debits'], business_state(conn)['gold']
    report = dict(opening_gold=opening, total_credits=totals['credits'], total_debits=totals['debits'],
                  expected_gold=expected, actual_gold=actual, difference=actual-expected,
                  consistent=actual == expected, ledger_count=totals['count'],
                  through_ledger_id=totals['last_id'], created_at=event.created_at)
    columns = ','.join(report)
    placeholders = ','.join('?' for _ in report)
    cursor = conn.execute(f'INSERT INTO audit_report(event_id,actor_id,{columns}) VALUES (?,?,{placeholders})',
                          (event.id, event.actor_id, *report.values()))
    report['id'] = cursor.lastrowid
    event.log('LEDGER_RECONCILED', f'覆盖流水至 {totals["last_id"]}；预期余额 {expected}；实际 {actual}；差异 {actual-expected}')
    return report
