def case_summary(case: dict, alerts: list[dict]) -> str:
    lines = [
        f"Case {case['case_id']}: {len(alerts)} alert(s) on host(s) {', '.join(case['hosts'])}, user(s) {', '.join(case['users']) or 'unknown'}."
    ]
    for a in alerts:
        p = a["process"] or {}
        lineage = " <- ".join(f"{x.get('cmd')} ({x.get('pid')})" for x in p.get("lineage", []))
        lines.append(
            f"- {a['alert_time']:%Y-%m-%d %H:%M:%S}Z {a['category']} severity {a['severity']} "
            f"on {a['host']}: pid {p.get('pid')} {p.get('cmd')!r} as {a['user_name']}"
            + (f"; lineage: {lineage}" if lineage else "")
        )
    return "\n".join(lines)
