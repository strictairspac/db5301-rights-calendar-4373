"""Rights review and release decisions for 潮幕影展."""

from datetime import datetime, timezone
from json import dumps
import re

from sqlalchemy import text

from src.utils.time import DESK_TIMEZONE
from . import chains, supplements
from .rights_sql import ANALYZE_SCREENINGS


SCOPE_FIELDS = ("film_id", "territory", "medium")
TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::00)?"
    r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)

SPEC = {
    "name": "潮幕影展·放映授权排期",
    "summary": "沿合同转授链逐场核对放映权益，给出保留排期、建议顺延或暂停发布的决策及补权时段。",
    "customer": "潮幕影展排片组与版权组",
    "entity_labels": ["影片台账", "排片校核", "缺口单"],
    "field_labels": {
        "max_delay_minutes": "最多顺延分钟",
        "screenings": "待校核排片",
        "rights": "已生效授权窗口",
        "supplement_batches": "补件批次",
        "screening_id": "场次编号",
        "right_id": "授权编号",
        "batch_id": "批次编号",
        "film_id": "影片编号",
        "title": "片名",
        "territory": "地域",
        "medium": "媒介",
        "exhibitor": "放映方",
        "grantor": "授权方",
        "grantee": "被授权方",
        "upstream_right_id": "上游授权编号",
        "contract_version": "合同版本",
        "starts_at": "开始时间",
        "ends_at": "结束时间",
        "items": "补件明细",
        "confirmed": "整批确认",
    },
    "guidance": (
        "导入排片、已生效授权及补件批次。时间使用带时区的 ISO 8601 整分钟，结果按 UTC+08:00 显示。"
        "授权逐级转授时填写授权方、被授权方和上游授权编号，校核从放映方沿链追到最初版权方；"
        "转授只在上游拿到的影片、地域、媒介和时段内生效，循环转授整条链作废。"
        "补件按批登记，分次交齐后整批确认、一次性切换进授权台账；退回后重试不会重复登记。"
        "同一授权编号的新合同版本取代旧版本，已签缺口单仍保留原版本快照。"
        "授权的地域或媒介可填 * 表示全部。顺延时长按开场时间计算，候选时段须复核场地和平台档期。"
    ),
    "sample": {
        "max_delay_minutes": 45,
        "screenings": [
            dict(screening_id="P-101", film_id="F-01", title="潮线以北", territory="上海",
                 medium="影院", exhibitor="星河影城",
                 starts_at="2026-12-04T19:00+08:00", ends_at="2026-12-04T20:30+08:00"),
            dict(screening_id="P-102", film_id="F-02", title="雾港来信", territory="上海",
                 medium="影院", exhibitor="星河影城",
                 starts_at="2026-12-04T20:00+08:00", ends_at="2026-12-04T21:00+08:00"),
            dict(screening_id="P-103", film_id="F-03", title="白鹭返航", territory="上海",
                 medium="线上", exhibitor="潮幕线上",
                 starts_at="2026-12-04T20:00+08:00", ends_at="2026-12-04T21:00+08:00"),
            dict(screening_id="P-104", film_id="F-03", title="白鹭返航", territory="杭州",
                 medium="线上", exhibitor="潮幕线上",
                 starts_at="2026-12-04T20:00+08:00", ends_at="2026-12-04T21:00+08:00"),
        ],
        "rights": [
            # F-01：版权方 → 华东发行 → 上海院线 → 星河影城；C-01 v1 的截止时间已由 v2 更正
            dict(right_id="C-01", contract_version=1, film_id="F-01", territory="*", medium="*",
                 grantor="潮幕版权", grantee="华东发行",
                 starts_at="2026-12-04T00:00+08:00", ends_at="2026-12-04T21:00+08:00"),
            dict(right_id="C-01", contract_version=2, film_id="F-01", territory="*", medium="*",
                 grantor="潮幕版权", grantee="华东发行",
                 starts_at="2026-12-04T00:00+08:00", ends_at="2026-12-05T00:00+08:00"),
            dict(right_id="D-01", contract_version=1, film_id="F-01", territory="上海", medium="影院",
                 grantor="华东发行", grantee="上海院线", upstream_right_id="C-01",
                 starts_at="2026-12-04T17:00+08:00", ends_at="2026-12-04T23:00+08:00"),
            dict(right_id="R-01", contract_version=1, film_id="F-01", territory="上海", medium="影院",
                 grantor="上海院线", grantee="星河影城", upstream_right_id="D-01",
                 starts_at="2026-12-04T18:55+08:00", ends_at="2026-12-04T19:35+08:00"),
            dict(right_id="R-02", contract_version=1, film_id="F-01", territory="上海", medium="影院",
                 grantor="上海院线", grantee="星河影城", upstream_right_id="D-01",
                 starts_at="2026-12-04T19:10+08:00", ends_at="2026-12-04T19:15+08:00"),
            dict(right_id="R-03", contract_version=1, film_id="F-01", territory="*", medium="影院",
                 grantor="上海院线", grantee="星河影城", upstream_right_id="D-01",
                 starts_at="2026-12-04T19:30+08:00", ends_at="2026-12-04T20:00+08:00"),
            dict(right_id="R-04", contract_version=1, film_id="F-01", territory="上海", medium="*",
                 grantor="上海院线", grantee="星河影城", upstream_right_id="D-01",
                 starts_at="2026-12-04T20:00+08:00", ends_at="2026-12-04T20:15+08:00"),
            # 版权方直接授予影城，与逐级转授的链路在场次内拼接
            dict(right_id="R-10", contract_version=1, film_id="F-01", territory="上海", medium="影院",
                 grantor="潮幕版权", grantee="星河影城",
                 starts_at="2026-12-04T20:15+08:00", ends_at="2026-12-04T20:30+08:00"),
            # F-02：版权方 → 华东发行 → 上海院线 → 星河影城
            dict(right_id="C-02", contract_version=1, film_id="F-02", territory="*", medium="影院",
                 grantor="潮幕版权", grantee="华东发行",
                 starts_at="2026-12-04T18:00+08:00", ends_at="2026-12-04T23:00+08:00"),
            dict(right_id="D-02", contract_version=1, film_id="F-02", territory="上海", medium="影院",
                 grantor="华东发行", grantee="上海院线", upstream_right_id="C-02",
                 starts_at="2026-12-04T19:00+08:00", ends_at="2026-12-04T22:00+08:00"),
            dict(right_id="R-05", contract_version=1, film_id="F-02", territory="上海", medium="影院",
                 grantor="上海院线", grantee="星河影城", upstream_right_id="D-02",
                 starts_at="2026-12-04T19:50+08:00", ends_at="2026-12-04T20:20+08:00"),
            dict(right_id="R-06", contract_version=1, film_id="F-02", territory="上海", medium="影院",
                 grantor="上海院线", grantee="星河影城", upstream_right_id="D-02",
                 starts_at="2026-12-04T20:30+08:00", ends_at="2026-12-04T21:45+08:00"),
            # F-03：上海线上由版权方转授平台，杭州线上另起一条链
            dict(right_id="C-03", contract_version=1, film_id="F-03", territory="上海", medium="线上",
                 grantor="潮幕版权", grantee="上海云幕",
                 starts_at="2026-12-04T19:00+08:00", ends_at="2026-12-04T22:00+08:00"),
            dict(right_id="R-07", contract_version=1, film_id="F-03", territory="上海", medium="线上",
                 grantor="上海云幕", grantee="潮幕线上", upstream_right_id="C-03",
                 starts_at="2026-12-04T20:10+08:00", ends_at="2026-12-04T20:40+08:00"),
            dict(right_id="C-04", contract_version=1, film_id="F-03", territory="杭州", medium="线上",
                 grantor="潮幕版权", grantee="杭州云幕",
                 starts_at="2026-12-04T20:00+08:00", ends_at="2026-12-04T21:00+08:00"),
            dict(right_id="R-08", contract_version=1, film_id="F-03", territory="杭州", medium="线上",
                 grantor="杭州云幕", grantee="潮幕线上", upstream_right_id="C-04",
                 starts_at="2026-12-04T20:00+08:00", ends_at="2026-12-04T21:00+08:00"),
            # F-03 的影院授权不覆盖线上场次
            dict(right_id="C-05", contract_version=1, film_id="F-03", territory="上海", medium="影院",
                 grantor="潮幕版权", grantee="上海院线",
                 starts_at="2026-12-04T19:00+08:00", ends_at="2026-12-04T22:00+08:00"),
            dict(right_id="R-09", contract_version=1, film_id="F-03", territory="上海", medium="影院",
                 grantor="上海院线", grantee="星河影城", upstream_right_id="C-05",
                 starts_at="2026-12-04T19:45+08:00", ends_at="2026-12-04T21:15+08:00"),
            # 循环转授：两张合同互相引用，整条链作废，不产生任何权益
            dict(right_id="X-01", contract_version=1, film_id="F-01", territory="上海", medium="影院",
                 grantor="星河影城", grantee="某代理", upstream_right_id="X-02",
                 starts_at="2026-12-04T19:00+08:00", ends_at="2026-12-04T20:30+08:00"),
            dict(right_id="X-02", contract_version=1, film_id="F-01", territory="上海", medium="影院",
                 grantor="某代理", grantee="星河影城", upstream_right_id="X-01",
                 starts_at="2026-12-04T19:00+08:00", ends_at="2026-12-04T20:30+08:00"),
        ],
        "supplement_batches": [
            # B-01 分两次交齐：第一次未确认，第二次确认后整批一次性切换
            dict(batch_id="B-01", confirmed=False, items=[
                dict(right_id="S-01", contract_version=1, film_id="F-03", territory="上海", medium="线上",
                     grantor="潮幕版权", grantee="潮幕线上",
                     starts_at="2026-12-04T20:00+08:00", ends_at="2026-12-04T20:10+08:00"),
            ]),
            dict(batch_id="B-01", confirmed=True, items=[
                dict(right_id="S-02", contract_version=1, film_id="F-03", territory="上海", medium="线上",
                     grantor="潮幕版权", grantee="潮幕线上",
                     starts_at="2026-12-04T20:40+08:00", ends_at="2026-12-04T20:50+08:00"),
            ]),
            # B-02 只补了后半段且尚未确认，缺口单停留在待确认
            dict(batch_id="B-02", confirmed=False, items=[
                dict(right_id="S-04", contract_version=1, film_id="F-03", territory="上海", medium="线上",
                     grantor="潮幕版权", grantee="潮幕线上",
                     starts_at="2026-12-04T20:50+08:00", ends_at="2026-12-04T21:00+08:00"),
            ]),
            # 合同更正：R-05 的 v2 延到 20:30，原缺口单仍记录 v1
            dict(batch_id="B-03", confirmed=True, items=[
                dict(right_id="R-05", contract_version=2, film_id="F-02", territory="上海", medium="影院",
                     grantor="上海院线", grantee="星河影城", upstream_right_id="D-02",
                     starts_at="2026-12-04T19:50+08:00", ends_at="2026-12-04T20:30+08:00"),
            ]),
            # 首次补件缺少上游，整批退回，不登记任何明细
            dict(batch_id="B-04", confirmed=True, items=[
                dict(right_id="S-03", contract_version=1, film_id="F-02", territory="苏州", medium="影院",
                     grantor="苏州院线", grantee="星河影城", upstream_right_id="C-06",
                     starts_at="2026-12-05T10:00+08:00", ends_at="2026-12-05T12:00+08:00"),
            ]),
            # 补交上游后重试，同一明细只登记一次
            dict(batch_id="B-05", confirmed=True, items=[
                dict(right_id="C-06", contract_version=1, film_id="F-02", territory="苏州", medium="影院",
                     grantor="潮幕版权", grantee="苏州院线",
                     starts_at="2026-12-05T09:00+08:00", ends_at="2026-12-05T13:00+08:00"),
                dict(right_id="S-03", contract_version=1, film_id="F-02", territory="苏州", medium="影院",
                     grantor="苏州院线", grantee="星河影城", upstream_right_id="C-06",
                     starts_at="2026-12-05T10:00+08:00", ends_at="2026-12-05T12:00+08:00"),
            ]),
            # 再次递交同一明细，识别为已登记并跳过
            dict(batch_id="B-06", confirmed=True, items=[
                dict(right_id="S-03", contract_version=1, film_id="F-02", territory="苏州", medium="影院",
                     grantor="苏州院线", grantee="星河影城", upstream_right_id="C-06",
                     starts_at="2026-12-05T10:00+08:00", ends_at="2026-12-05T12:00+08:00"),
            ]),
        ],
    },
    "expect": {
        "排片场次": 4,
        "保留排期场次": 2,
        "建议顺延场次": 1,
        "暂停发布场次": 1,
        "未授权分钟": 40,
        "缺口段数": 3,
        "有效授权": 21,
        "无效链路": 2,
        "补件批次": 6,
        "已生效补件": 4,
        "退回补件": 1,
        "已补齐缺口单": 2,
        "待确认缺口单": 1,
        "未补齐缺口单": 0,
    },
}


def _parse_time(value, label):
    if not isinstance(value, str) or not TIMESTAMP.fullmatch(value):
        raise ValueError(f"{label}须使用带时区的 ISO 8601 整分钟")
    try:
        parsed = datetime.fromisoformat(value).astimezone(timezone.utc)
        parsed.astimezone(DESK_TIMEZONE)
    except (ValueError, OverflowError) as error:
        raise ValueError(f"{label}的日期、时间或时区超出有效范围") from error
    return parsed


def _text(record, field, label, optional=False):
    value = record.get(field)
    if (value is None or value == "") and optional:
        return ""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{label}的{SPEC['field_labels'].get(field, field)}须为有效非空文本")
    return value.strip()


def _version(record, label):
    value = record.get("contract_version")
    if value is None:
        return 1
    if type(value) is not int or value < 1:
        raise ValueError(f"{label}的{SPEC['field_labels']['contract_version']}须为正整数")
    return value


def _read_screenings(payload):
    labels = SPEC["field_labels"]
    records = payload.get("screenings")
    if not isinstance(records, list):
        raise ValueError(f"{labels['screenings']}须为列表")
    normalized = []
    identifiers = set()
    for number, record in enumerate(records, start=1):
        label = f"{labels['screenings']}第 {number} 行"
        if not isinstance(record, dict):
            raise ValueError(f"{label}须为对象")
        row = {field: _text(record, field, label)
               for field in ("screening_id", "film_id", "title", *SCOPE_FIELDS)}
        row["exhibitor"] = _text(record, "exhibitor", label, optional=True)
        if row["screening_id"] in identifiers:
            raise ValueError(f"{label}的{labels['screening_id']}重复（{row['screening_id']}）")
        identifiers.add(row["screening_id"])
        if "*" in (row["territory"], row["medium"]):
            raise ValueError(f"{label}须填写具体地域和媒介")
        start = _parse_time(record.get("starts_at"), f"{label}开始时间")
        end = _parse_time(record.get("ends_at"), f"{label}结束时间")
        if end <= start:
            raise ValueError(f"{label}的结束时间须晚于开始时间")
        row.update(starts_at=start.isoformat(timespec="minutes"),
                   ends_at=end.isoformat(timespec="minutes"))
        normalized.append(row)
    return normalized


def _read_grants(records, label_prefix, identifiers):
    """Normalize a list of grant rows; ``identifiers`` rejects duplicate versions."""
    labels = SPEC["field_labels"]
    normalized = []
    for number, record in enumerate(records, start=1):
        label = f"{label_prefix}第 {number} 行"
        if not isinstance(record, dict):
            raise ValueError(f"{label}须为对象")
        row = {field: _text(record, field, label)
               for field in ("right_id", "film_id", *SCOPE_FIELDS)}
        row["grantor"] = _text(record, "grantor", label, optional=True)
        row["grantee"] = _text(record, "grantee", label, optional=True)
        upstream = record.get("upstream_right_id")
        row["upstream_right_id"] = (None if upstream in (None, "")
                                    else _text(record, "upstream_right_id", label))
        row["contract_version"] = _version(record, label)
        key = supplements.grant_key(row)
        if key in identifiers:
            raise ValueError(
                f"{label}的{labels['right_id']}重复（{row['right_id']} v{row['contract_version']}）")
        identifiers.add(key)
        start = _parse_time(record.get("starts_at"), f"{label}开始时间")
        end = _parse_time(record.get("ends_at"), f"{label}结束时间")
        if end <= start:
            raise ValueError(f"{label}的结束时间须晚于开始时间")
        row.update(starts_at=start, ends_at=end)
        normalized.append(row)
    return normalized


def _read_batches(payload):
    """Read supplement batches.

    The same ``batch_id`` may appear several times: those entries are pieces of
    one batch delivered in installments (分次交齐).  Their items are merged in
    order and the batch is confirmed when any installment says so; the switch
    happens once, for the whole merged batch.
    """
    labels = SPEC["field_labels"]
    records = payload.get("supplement_batches", [])
    if records is None:
        return []
    if not isinstance(records, list):
        raise ValueError(f"{labels['supplement_batches']}须为列表")
    merged = {}
    order = []
    for number, record in enumerate(records, start=1):
        label = f"{labels['supplement_batches']}第 {number} 批"
        if not isinstance(record, dict):
            raise ValueError(f"{label}须为对象")
        batch_id = _text(record, "batch_id", label)
        confirmed = record.get("confirmed", False)
        if not isinstance(confirmed, bool):
            raise ValueError(f"{label}的{labels['confirmed']}须为布尔值")
        items = record.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError(f"{label}须包含非空的{labels['items']}")
        if batch_id not in merged:
            merged[batch_id] = {"batch_id": batch_id, "confirmed": False, "items": []}
            order.append(batch_id)
        batch = merged[batch_id]
        batch["confirmed"] = batch["confirmed"] or confirmed
        batch["items"].extend(_read_grants(items, f"{label}明细", set()))
    return [merged[batch_id] for batch_id in order]


def _read_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("校核输入须为对象")
    max_delay = payload.get("max_delay_minutes")
    if type(max_delay) is not int or max_delay < 0:
        raise ValueError("最多顺延分钟须为非负整数")
    screenings = _read_screenings(payload)
    if not isinstance(payload.get("rights"), list):
        raise ValueError(f"{SPEC['field_labels']['rights']}须为列表")
    rights = _read_grants(payload["rights"], SPEC["field_labels"]["rights"], set())
    return screenings, rights, _read_batches(payload), max_delay


def validate(payload):
    """Raise ValueError for invalid input before the database query."""
    _read_payload(payload)


def _desk_time(value):
    return _moment(value).astimezone(DESK_TIMEZONE).isoformat(timespec="minutes")


def _clock(value):
    if isinstance(value, datetime):
        return value.astimezone(DESK_TIMEZONE)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(DESK_TIMEZONE)


def _moment(value):
    """Parse a timestamp that may arrive as a datetime or a JSONB string."""
    if isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _span(starts_at, ends_at, day=None):
    start, end = _clock(starts_at), _clock(ends_at)
    if day is None or (start.date() == day and end.date() == day):
        return f"{start:%H:%M}–{end:%H:%M}"
    return f"{start:%m-%d %H:%M}–{end:%m-%d %H:%M}"


def _screening_result(row, identity):
    missing = row["unauthorized_minutes"]
    proposed_start = None
    proposed_end = None
    if missing == 0:
        decision = "保留排期"
        action = "按原排期提交发布审核"
    elif row["proposed_start"] is not None:
        decision = "建议顺延"
        action = "确认新时段的场地或平台档期后改签"
        proposed_start = _desk_time(row["proposed_start"])
        proposed_end = _desk_time(row["proposed_end"])
    else:
        decision = "暂停发布"
        action = "按缺口单申请补权或另排"
    return {
        **identity,
        "原开始": _desk_time(row["starts_at"]),
        "原结束": _desk_time(row["ends_at"]),
        "放映分钟": row["screening_minutes"],
        "已授权分钟": row["screening_minutes"] - missing,
        "未授权分钟": missing,
        "编辑决策": decision,
        "建议开始": proposed_start,
        "建议结束": proposed_end,
        "编辑动作": action,
    }


def _analyze(engine, screenings, right_rows, max_delay):
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        return list(conn.execute(ANALYZE_SCREENINGS, {
            "screenings": dumps(screenings, ensure_ascii=False),
            "rights": dumps(right_rows, ensure_ascii=False),
            "max_delay_minutes": max_delay,
        }).mappings())


def _identity(row):
    return {
        "场次编号": row["screening_id"],
        "影片编号": row["film_id"],
        "片名": row["title"],
        "地域": row["territory"],
        "媒介": row["medium"],
    }


def _basis_grants(resolved, row):
    """Effective grants that actually contribute to a screening row."""
    by_id = {grant["right_id"]: grant for grant in resolved if chains.is_effective(grant)}
    identifiers = []
    for interval in row["coverage"]:
        for right_id in interval["right_ids"]:
            if right_id not in identifiers:
                identifiers.append(right_id)
    return [by_id[right_id] for right_id in identifiers if right_id in by_id]


def _version_text(grants):
    return "、".join(f"{grant['right_id']} v{grant['contract_version']}" for grant in grants)


def _current_text(grants, final_resolved):
    """Current version of each original basis grant, flagging corrections."""
    latest = {}
    for grant in final_resolved:
        if grant["active"]:
            latest[grant["right_id"]] = grant
    parts = []
    for grant in grants:
        now = latest.get(grant["right_id"])
        if now is None:
            parts.append(f"{grant['right_id']} v{grant['contract_version']}（已撤下）")
        elif now["contract_version"] == grant["contract_version"]:
            parts.append(f"{grant['right_id']} v{grant['contract_version']}")
        else:
            parts.append(
                f"{grant['right_id']} v{grant['contract_version']} → v{now['contract_version']}（已更正）")
    return "、".join(parts)


def _chain_text(grants):
    """Full chains, printing a shared upstream prefix only once.

    Several grants on a screening often share the same upper chain, so the
    first grant carries the complete root-to-terminal path and later grants
    only append the part below the last prefix already shown.
    """
    shown = []
    parts = []
    for grant in grants:
        chain = [row["right_id"] for row in (grant.get("chain") or [])]
        shared = 0
        for known in shown:
            length = 0
            while (length < len(known) and length < len(chain)
                   and known[length] == chain[length]):
                length += 1
            shared = max(shared, length)
        rest = chains.chain_label(grant["chain"][shared:])
        parts.append(("… → " if shared else "") + rest)
        shown.append(chain)
    return "；".join(parts)


def _span_items(row):
    day = _clock(row["starts_at"]).date()
    return [{
        "时段": _span(interval["starts_at"], interval["ends_at"], day),
        "授权": "、".join(interval["right_ids"]),
    } for interval in row["coverage"]]


def _gap_items(row):
    day = _clock(row["starts_at"]).date()
    return [{
        "时段": _span(gap["starts_at"], gap["ends_at"], day),
        "分钟": int(gap["minutes"]),
    } for gap in row["gaps"]]


def _gap_overlap(ticket, gap):
    return ticket["_start"] < gap["_end"] and gap["_start"] < ticket["_end"]


def _touches(ticket, item):
    if ticket["_film"] != item["film_id"]:
        return False
    if item["territory"] not in (ticket["_territory"], "*"):
        return False
    if item["medium"] not in (ticket["_medium"], "*"):
        return False
    return ticket["_start"] < item["ends_at"] and item["starts_at"] < ticket["_end"]


def solve(payload, engine):
    """Return per-screening decisions, coverage evidence and gap tickets."""
    screenings, rights, batches, max_delay = _read_payload(payload)

    base_resolved = chains.resolve(rights)
    base_effective = chains.effective_rows(base_resolved)
    original = _analyze(engine, screenings, chains.query_rights(base_effective), max_delay)

    # The register keeps the raw grants so a resent supplement matches by
    # content.  Clipping to the chain happens again when the final register is
    # resolved, because a supplement can extend what a chain reaches.
    register = {supplements.grant_key(row): row for row in rights}
    register, batch_report, pending_items, _ = supplements.apply(batches, register)
    final_resolved = chains.resolve(list(register.values()))
    final_rows = chains.query_rights(chains.effective_rows(final_resolved))
    after = {row["screening_id"]: row
             for row in _analyze(engine, screenings, final_rows, max_delay)}

    metrics = dict.fromkeys(SPEC["expect"], 0)
    decisions = []
    coverage_rows = []
    tickets = []
    for row in original:
        identity = _identity(row)
        result = _screening_result(row, identity)
        decisions.append(result)
        metrics[f"{result['编辑决策']}场次"] += 1
        metrics["未授权分钟"] += result["未授权分钟"]

        basis = _basis_grants(base_resolved, row)
        day = _clock(row["starts_at"]).date()
        coverage_rows.append({
            **identity,
            "场次时段": _span(row["starts_at"], row["ends_at"], day),
            "覆盖分段": _span_items(row),
            "断点": _gap_items(row),
            "未授权分钟": result["未授权分钟"],
            "结论": "完整覆盖" if result["未授权分钟"] == 0 else "存在断点",
            "合同版本": _version_text(basis) or "—",
            "覆盖依据": _chain_text(basis) or "无有效授权链",
        })

        gaps = sorted(row["gaps"], key=lambda gap: _moment(gap["starts_at"]))
        for index, gap in enumerate(gaps, start=1):
            tickets.append({
                "缺口单编号": f"{row['screening_id']}-G{index}",
                **identity,
                "缺口开始": _desk_time(gap["starts_at"]),
                "缺口结束": _desk_time(gap["ends_at"]),
                "未授权分钟": int(gap["minutes"]),
                "原合同版本": _version_text(basis) or "—",
                "现行版本": _current_text(basis, final_resolved) or "—",
                "依据链路": _chain_text(basis) or "—",
                "_start": _moment(gap["starts_at"]),
                "_end": _moment(gap["ends_at"]),
                "_film": row["film_id"],
                "_territory": row["territory"],
                "_medium": row["medium"],
            })

    for ticket in tickets:
        after_row = after.get(ticket["场次编号"])
        reopened = any(
            _gap_overlap(ticket, {"_start": _moment(gap["starts_at"]),
                                  "_end": _moment(gap["ends_at"])})
            for gap in (after_row["gaps"] if after_row else [])
        )
        if not reopened:
            ticket["补件状态"] = "已补齐"
        elif any(_touches(ticket, item) for item in pending_items):
            ticket["补件状态"] = "补件待确认"
        else:
            ticket["补件状态"] = "未补齐"

    metrics["排片场次"] = len(decisions)
    metrics["缺口段数"] = len(tickets)
    metrics["有效授权"] = sum(1 for grant in final_resolved if chains.is_effective(grant))
    metrics["无效链路"] = sum(1 for grant in base_resolved
                              if grant["status"] in chains.INVALID_STATUSES)
    metrics["补件批次"] = len(batch_report)
    metrics["已生效补件"] = sum(1 for batch in batch_report if batch["状态"] == supplements.BATCH_EFFECTIVE)
    metrics["退回补件"] = sum(1 for batch in batch_report if batch["状态"] == supplements.BATCH_RETURNED)
    metrics["已补齐缺口单"] = sum(1 for ticket in tickets if ticket["补件状态"] == "已补齐")
    metrics["待确认缺口单"] = sum(1 for ticket in tickets if ticket["补件状态"] == "补件待确认")
    metrics["未补齐缺口单"] = sum(1 for ticket in tickets if ticket["补件状态"] == "未补齐")

    chain_rows = [{
        "授权编号": grant["right_id"],
        "合同版本": f"v{grant['contract_version']}",
        "影片编号": grant["film_id"],
        "授权方": grant["grantor"] or "版权方",
        "被授权方": grant["grantee"] or "—",
        "上游授权": grant["upstream_right_id"] or "—",
        "地域": grant["effective_territory"] or grant["territory"],
        "媒介": grant["effective_medium"] or grant["medium"],
        "状态": grant["status"],
        "说明": grant["note"] or "",
        "链路": chains.chain_label(grant.get("chain")) or "—",
    } for grant in base_resolved]

    review_rows = []
    for decision in decisions:
        after_row = after[decision["场次编号"]]
        after_result = _screening_result(after_row, _identity(after_row))
        review_rows.append({
            "场次编号": decision["场次编号"],
            "片名": decision["片名"],
            "校核时未授权分钟": decision["未授权分钟"],
            "补件后未授权分钟": after_result["未授权分钟"],
            "校核时决策": decision["编辑决策"],
            "补件后决策": after_result["编辑决策"],
            "结论": "决策改变" if after_result["编辑决策"] != decision["编辑决策"] else "决策不变",
        })

    public_tickets = [{key: value for key, value in ticket.items() if not key.startswith("_")}
                      for ticket in tickets]
    sections = [
        {"title": "排片校核与编辑决策", "rows": decisions},
        {"title": "逐场覆盖依据与断点", "kind": "coverage", "rows": coverage_rows},
        {"title": "原排片权益缺口单", "rows": public_tickets},
        {"title": "合同链校核", "rows": chain_rows},
    ]
    if batch_report:
        sections.append({"title": "补件批次", "rows": batch_report})
        sections.append({"title": "补件后复核", "rows": review_rows})

    summary = (
        f"校核 {metrics['排片场次']} 场，保留 {metrics['保留排期场次']} 场，"
        f"建议顺延 {metrics['建议顺延场次']} 场，暂停发布 {metrics['暂停发布场次']} 场。"
        f"原排期有 {metrics['缺口段数']} 处授权缺口，共 {metrics['未授权分钟']} 分钟。"
    )
    if batch_report:
        summary += (f"补件 {metrics['补件批次']} 批，生效 {metrics['已生效补件']} 批、"
                    f"退回 {metrics['退回补件']} 批；缺口单已补齐 {metrics['已补齐缺口单']} 张、"
                    f"待确认 {metrics['待确认缺口单']} 张、未补齐 {metrics['未补齐缺口单']} 张。")
    return {"summary": summary, "metrics": metrics, "sections": sections}
