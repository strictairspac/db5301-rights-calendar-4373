"""Supplement batches for gap tickets.

Gap tickets are filled in pieces: the rights team may send some grants now and
the rest later.  A supplement batch collects those pieces.  Its pieces are
staged and re-validated as a whole, and only then does one atomic switch move
them into the effective register.  When validation fails the batch is returned
and nothing switches; a retry registers the pieces once per ticket and version,
so a failed attempt followed by a retry never registers the same piece twice.

Ticket state machine::

    缺口单 打开 --补件登记--> 补件待确认 --整批确认--> 补件生效（缺口补齐）
                                  |
                                  +--任一条无效/冲突--> 补件退回（不切换，可重试）
"""

from . import chains

BATCH_STAGED = "补件待确认"
BATCH_EFFECTIVE = "补件生效"
BATCH_RETURNED = "补件退回"


def grant_key(row):
    """A grant is identified by its right id and contract version."""
    return (row["right_id"], row["contract_version"])


def _same_grant(left, right):
    fields = ("film_id", "territory", "medium", "grantor", "grantee",
              "upstream_right_id", "starts_at", "ends_at")
    return all(left.get(field) == right.get(field) for field in fields)


def _stage(items, register):
    """Fold one batch's pieces into a candidate register.

    Pieces already registered are skipped, so a retry after a failure or a
    re-sent batch does not register the same piece twice.  A piece whose id and
    version are already taken with different content is a conflict: corrections
    must raise the contract version instead of overwriting it.
    """
    candidates = dict(register)
    fresh = []
    skipped = 0
    conflicts = []
    seen = set()
    for item in items:
        key = grant_key(item)
        if key in seen:
            conflicts.append(f"{item['right_id']} v{item['contract_version']} 在本批重复")
            continue
        seen.add(key)
        existing = register.get(key)
        if existing is not None:
            if not _same_grant(existing, item):
                conflicts.append(
                    f"{item['right_id']} v{item['contract_version']} 与已登记内容不一致，须提升合同版本")
            else:
                skipped += 1
            continue
        candidates[key] = item
        fresh.append(item)
    return candidates, fresh, skipped, conflicts


def apply(batches, register):
    """Run the supplement state machine over ``batches``.

    ``register`` maps :func:`grant_key` to grant rows that are already in force.
    Returns the updated register, a per-batch report, the staged pieces waiting
    for confirmation, and the resolved status of every touched piece.
    """
    register = dict(register)
    report = []
    pending = []
    statuses = {}
    for batch in batches:
        candidates, fresh, skipped, conflicts = _stage(batch["items"], register)
        outcome = {grant_key(row): row for row in chains.resolve(list(candidates.values()))}
        invalid = []
        for item in fresh:
            resolved = outcome[grant_key(item)]
            statuses[grant_key(item)] = resolved["status"]
            if resolved["status"] not in (chains.STATUS_VALID, chains.STATUS_SUPERSEDED):
                invalid.append(f"{item['right_id']} v{item['contract_version']}：{resolved['note']}")
        problems = conflicts + invalid
        entry = {
            "批次编号": batch["batch_id"],
            "明细数": len(batch["items"]),
            "重复跳过": skipped,
            "登记件数": 0,
        }
        if not batch["confirmed"]:
            entry["状态"] = BATCH_STAGED
            entry["说明"] = "；".join(problems) if problems else "等待整批确认后切换"
            pending.extend(item for item in fresh
                           if outcome[grant_key(item)]["status"] == chains.STATUS_VALID)
        elif problems:
            entry["状态"] = BATCH_RETURNED
            entry["说明"] = "；".join(problems)
        else:
            register = candidates
            entry["状态"] = BATCH_EFFECTIVE
            entry["登记件数"] = len(fresh)
            entry["说明"] = "整批切换进授权台账" if fresh else "明细均已登记，本次未重复登记"
        report.append(entry)
    return register, report, pending, statuses
