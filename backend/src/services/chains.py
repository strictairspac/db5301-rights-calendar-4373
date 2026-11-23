"""Delegation-chain resolution for screening rights.

Screening rights are delegated down a chain: the rights holder grants a window
to an agent, that agent grants part of it to another party, and so on until the
exhibitor that actually screens the film.  A grant is only effective while it
stays inside everything the grantor received upstream:

* the film must be the same along the whole chain;
* territory and medium are intersected (``*`` means "everything");
* the window is intersected with every upstream window.

A chain that loops back on itself is discarded as a whole: re-delegating a
right cannot create a window that was never granted.  Grants are linked with
``upstream_right_id``; a grant without that link is an original grant from a
rights holder and starts a chain.

Corrections do not overwrite history.  The same ``right_id`` may occur with
several ``contract_version`` values; the highest version is the one in force,
older versions stay visible as superseded so signed gap tickets can still refer
to the contract version they were checked against.
"""

WILDCARD = "*"

STATUS_VALID = "有效"
STATUS_SUPERSEDED = "已更正"
STATUS_MISSING_UPSTREAM = "上游不存在"
STATUS_CYCLE = "循环转授"
STATUS_GRANTOR_MISMATCH = "授权方与上游不符"
STATUS_FILM_OUTSIDE = "影片超出上游"
STATUS_TERRITORY_OUTSIDE = "地域超出上游"
STATUS_MEDIUM_OUTSIDE = "媒介超出上游"
STATUS_WINDOW_OUTSIDE = "时段超出上游"

INVALID_STATUSES = (
    STATUS_MISSING_UPSTREAM,
    STATUS_CYCLE,
    STATUS_GRANTOR_MISMATCH,
    STATUS_FILM_OUTSIDE,
    STATUS_TERRITORY_OUTSIDE,
    STATUS_MEDIUM_OUTSIDE,
    STATUS_WINDOW_OUTSIDE,
)


def _intersect_scope(upstream, downstream):
    """Return the narrower code, or None when the two codes cannot meet."""
    if upstream == WILDCARD:
        return downstream
    if downstream == WILDCARD:
        return upstream
    if upstream == downstream:
        return upstream
    return None


def _cycle_tainted(latest):
    """Right ids whose upstream path never reaches a root grant.

    Everything on or below a loop is discarded as a whole: a delegation that
    comes back around cannot create a window that was never granted, and a
    grant built on top of such a loop inherits the same defect.  Detecting it
    from the whole register keeps the verdict independent of the order in which
    individual grants are walked.
    """
    state = {}  # right_id -> "visiting" | "cycle" | "clean"
    tainted = set()

    def visit(start):
        path = []
        current = start
        while True:
            if current in state:
                # "visiting" means the path re-entered itself: a loop.
                verdict = "cycle" if state[current] == "visiting" else state[current]
                break
            grant = latest.get(current)
            if grant is None or not grant.get("upstream_right_id"):
                verdict = "clean"
                break
            state[current] = "visiting"
            path.append(current)
            current = grant["upstream_right_id"]
        for node in path:
            state[node] = verdict
            if verdict == "cycle":
                tainted.add(node)
        return verdict

    for right_id in latest:
        visit(right_id)
    # A grant downstream of a loop is unusable even though its own path ends.
    changed = True
    while changed:
        changed = False
        for right_id, grant in latest.items():
            if right_id in tainted:
                continue
            if grant.get("upstream_right_id") in tainted:
                tainted.add(right_id)
                changed = True
    return tainted


def _walk(grant, latest, tainted):
    """Resolve one grant against its upstream links.

    ``latest`` maps a right_id to its highest contract version and ``tainted``
    holds every right id sitting on or below a loop.  The returned dict carries
    the chain status, a human readable note, the chain from root to the grant,
    and the effective scope/window.
    """
    if grant["right_id"] in tainted:
        return {
            "status": STATUS_CYCLE,
            "note": f"{grant['right_id']} 位于循环转授链路，不产生权益",
        }
    chain = [grant]  # terminal grant first, root last
    territory = grant["territory"]
    medium = grant["medium"]
    starts_at = grant["starts_at"]
    ends_at = grant["ends_at"]
    clipped = []
    current = grant

    while current.get("upstream_right_id"):
        upstream_id = current["upstream_right_id"]
        upstream = latest.get(upstream_id)
        if upstream is None:
            return {
                "status": STATUS_MISSING_UPSTREAM,
                "note": f"上游授权 {upstream_id} 不在本批台账中",
            }
        if upstream["right_id"] in tainted:
            return {
                "status": STATUS_CYCLE,
                "note": f"转授回到 {upstream['right_id']}，循环链路不产生权益",
            }
        if upstream["film_id"] != current["film_id"]:
            return {
                "status": STATUS_FILM_OUTSIDE,
                "note": f"上游 {upstream['right_id']} 的影片为 {upstream['film_id']}",
            }
        if current.get("grantor") and upstream.get("grantee") \
                and current["grantor"] != upstream["grantee"]:
            return {
                "status": STATUS_GRANTOR_MISMATCH,
                "note": (
                    f"本行授权方 {current['grantor']} 不是上游 {upstream['right_id']} "
                    f"的被授权方 {upstream['grantee']}"
                ),
            }
        narrowed = _intersect_scope(upstream["territory"], territory)
        if narrowed is None:
            return {
                "status": STATUS_TERRITORY_OUTSIDE,
                "note": f"地域 {territory} 超出上游 {upstream['right_id']} 的 {upstream['territory']}",
            }
        if narrowed != territory:
            clipped.append(f"地域按上游 {upstream['right_id']} 收窄为 {narrowed}")
            territory = narrowed
        narrowed = _intersect_scope(upstream["medium"], medium)
        if narrowed is None:
            return {
                "status": STATUS_MEDIUM_OUTSIDE,
                "note": f"媒介 {medium} 超出上游 {upstream['right_id']} 的 {upstream['medium']}",
            }
        if narrowed != medium:
            clipped.append(f"媒介按上游 {upstream['right_id']} 收窄为 {narrowed}")
            medium = narrowed
        clipped_start = max(starts_at, upstream["starts_at"])
        clipped_end = min(ends_at, upstream["ends_at"])
        if clipped_start >= clipped_end:
            return {
                "status": STATUS_WINDOW_OUTSIDE,
                "note": f"时段与上游 {upstream['right_id']} 的窗口没有交集",
            }
        if clipped_start != starts_at or clipped_end != ends_at:
            clipped.append(f"时段按上游 {upstream['right_id']} 裁剪")
        starts_at, ends_at = clipped_start, clipped_end
        chain.append(upstream)
        current = upstream

    root = chain[-1]
    root_label = root.get("grantor") or "版权方"
    note = "；".join(dict.fromkeys(clipped)) or f"自 {root_label} 起完整链路"
    return {
        "status": STATUS_VALID,
        "note": note,
        "chain": list(reversed(chain)),
        "territory": territory,
        "medium": medium,
        "starts_at": starts_at,
        "ends_at": ends_at,
    }


def resolve(rights):
    """Resolve every grant row, preserving input order.

    Returns one dict per input row with the raw values plus ``status``,
    ``note``, ``active`` (latest version in force), ``chain`` (root to
    terminal) and the effective fields when the chain is valid.
    """
    grouped = {}
    for row in rights:
        grouped.setdefault(row["right_id"], []).append(row)
    latest = {}
    for right_id, versions in grouped.items():
        versions.sort(key=lambda row: row["contract_version"])
        latest[right_id] = versions[-1]

    tainted = _cycle_tainted(latest)
    results = []
    for row in rights:
        resolved = {
            **row,
            "active": row is latest[row["right_id"]],
            "chain": None,
            "effective_territory": None,
            "effective_medium": None,
            "effective_start": None,
            "effective_end": None,
        }
        if not resolved["active"]:
            newest = latest[row["right_id"]]["contract_version"]
            resolved.update(status=STATUS_SUPERSEDED,
                            note=f"已由合同版本 v{newest} 取代")
            results.append(resolved)
            continue
        outcome = _walk(row, latest, tainted)
        resolved["status"] = outcome["status"]
        resolved["note"] = outcome.get("note")
        if outcome["status"] == STATUS_VALID:
            resolved.update(
                chain=outcome["chain"],
                effective_territory=outcome["territory"],
                effective_medium=outcome["medium"],
                effective_start=outcome["starts_at"],
                effective_end=outcome["ends_at"],
            )
        results.append(resolved)
    return results


def chain_label(chain):
    """Root-to-terminal chain with contract versions."""
    if not chain:
        return None
    parts = []
    for grant in chain:
        label = f"{grant['right_id']} v{grant['contract_version']}"
        if grant.get("grantor") or grant.get("grantee"):
            label += f"（{grant.get('grantor') or '版权方'}→{grant.get('grantee') or '放映方'}）"
        parts.append(label)
    return " → ".join(parts)


def is_effective(grant):
    """Whether a resolved grant can contribute coverage to a screening."""
    return grant["status"] == STATUS_VALID and grant["active"]


def effective_rows(resolved):
    """Effective grants as register rows: flat scope clipped to the chain.

    Kept as ``datetime`` values so later passes can reuse them; format for the
    database with :func:`query_rights`.
    """
    rows = []
    for grant in resolved:
        if not is_effective(grant):
            continue
        rows.append({
            "right_id": grant["right_id"],
            "film_id": grant["film_id"],
            "territory": grant["effective_territory"],
            "medium": grant["effective_medium"],
            "grantor": grant.get("grantor") or "",
            "grantee": grant.get("grantee") or "",
            "upstream_right_id": grant.get("upstream_right_id"),
            "contract_version": grant["contract_version"],
            "starts_at": grant["effective_start"],
            "ends_at": grant["effective_end"],
        })
    return rows


def query_rights(rows):
    """Rows handed to the window SQL, with the scope clipped to the chain."""
    return [{**row,
             "starts_at": row["starts_at"].isoformat(timespec="minutes"),
             "ends_at": row["ends_at"].isoformat(timespec="minutes")}
            for row in rows]


def snapshot(grant):
    """Version snapshot kept on gap tickets and evidence."""
    if grant is None:
        return None
    return {
        "right_id": grant["right_id"],
        "contract_version": grant["contract_version"],
        "grantor": grant.get("grantor"),
        "grantee": grant.get("grantee"),
        "status": grant["status"],
        "chain": chain_label(grant.get("chain")) or f"{grant['right_id']} v{grant['contract_version']}",
    }
