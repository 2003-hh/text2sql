import re
from datetime import date, timedelta
from typing import NamedTuple

TODAY = date(2026, 10, 31)
DAY = re.compile(r"(?:(\d{4})年)?(?:(\d{1,2})月)?(\d{1,2})日")
ISO = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
MONTH = re.compile(r"(?:(\d{4})年)?(\d{1,2})月")
QUARTER = re.compile(r"(?:(\d{4})年)?第([一二三四1-4])季度")
YEAR = re.compile(r"(\d{4})年(?:全年|整年)")
SEPARATORS = {"至", "到", "—", "~", "～", "-"}

# 匹配整句话。首个日期明确年份，后续端点可沿用同一区间的年月。
_EXPLICIT_DATE = (
    r"\d{4}年\s*\d{1,2}月(?:\s*\d{1,2}日"
    r"(?:\s*[至到—~～-]\s*(?:(?:\d{4}年\s*)?\d{1,2}月\s*)?\d{1,2}日)?)?"
    r"|\d{4}-\d{2}-\d{2}(?:\s*[至到/~～]\s*\d{4}-\d{2}-\d{2})?"
)
_DATE_REVISION = re.compile(
    r"(?:只(?:把)?|仅(?:把)?)?(?:查询)?(?:日期|时间范围)"
    r"(?:改为|改成|改到|调整为|设为)\s*"
    rf"(?P<date>{_EXPLICIT_DATE})"
    r"\s*(?:[，,；;]\s*其(?:他|它)(?:条件)?(?:保持原样|保持不变|不变|照旧))?[。.!！]?"
)


class DateRange(NamedTuple):
    start: date
    end: date


def _move_month(value: date, delta: int) -> date:
    year, month = divmod(value.year * 12 + value.month - 1 + delta, 12)
    return date(year, month + 1, 1)


def _endpoints(text: str, matches: list[re.Match], parse, separators: set[str]) -> list[date]:
    if len(matches) > 2:
        raise ValueError("支持一个连续区间")
    if len(matches) == 2 and text[matches[0].end():matches[1].start()] not in separators:
        raise ValueError("无法识别日期连接词")
    dates = []
    previous = TODAY
    for match in matches:
        previous = parse(match, previous)
        dates.append(previous)
    if dates[0] > dates[-1]:
        raise ValueError("日期倒序")
    return dates


def parse_date_range(text: str) -> DateRange | None:
    text = re.sub(r"\s+", "", text)
    if re.search("日和|日、|日及|不要|排除|不含|不包括", text):
        return None
    try:
        if matches := list(DAY.finditer(text)):
            def day(match, previous):
                year, month, number = match.groups()
                return date(int(year or previous.year), int(month or previous.month), int(number))

            dates = _endpoints(text, matches, day, SEPARATORS)
            return DateRange(dates[0], dates[-1] + timedelta(days=1))
        if matches := list(ISO.finditer(text)):
            dates = _endpoints(text, matches, lambda match, _: date.fromisoformat(match[0]),
                               {"至", "到", "/", "~", "～"})
            return DateRange(dates[0], dates[-1] + timedelta(days=1))

        words = {word for word in ("今天", "昨天", "本月", "上月") if word in text}
        if "上个月" in text:
            words.add("上月")
        if "这个月" in text:
            words.add("本月")
        if len(words) > 1:
            return None
        if words:
            word = words.pop()
            if word.endswith("月"):
                start = _move_month(TODAY, -1 if word == "上月" else 0)
                return DateRange(start, _move_month(start, 1))
            start = TODAY - timedelta(days=int(word == "昨天"))
            return DateRange(start, start + timedelta(days=1))

        if quarter := QUARTER.search(text):
            year, part = quarter.groups()
            number = "一二三四".find(part) + 1 or int(part)
            start = date(int(year or TODAY.year), number * 3 - 2, 1)
            return DateRange(start, _move_month(start, 3))
        if matches := list(MONTH.finditer(text)):
            dates = _endpoints(text, matches, lambda m, p: date(int(m[1] or p.year), int(m[2]), 1),
                               SEPARATORS)
            return DateRange(dates[0], _move_month(dates[-1], 1))
        if year := YEAR.search(text):
            start = date(int(year[1]), 1, 1)
            return DateRange(start, _move_month(start, 12))
    except (ValueError, OverflowError):
        return None
    return None


def explicit_date_in_text(text: str) -> DateRange | None:
    """只采纳唯一且带明确年份的日期表达。"""
    matches = list(re.finditer(_EXPLICIT_DATE, text))
    if len(matches) != 1 or re.search(r"不是|并非|不查|不要|排除|不含|不包括|之前|之后|以前|以后", text):
        return None
    match = matches[0]
    other_text = text[:match.start()] + " " + text[match.end():]
    if re.search(
        r"\d\s*[年月日号]|\d{4}-|本月|上月|上个月|今天|昨天|这周|上周|"
        r"今年|去年|明年|本周|本季度|上季度|最近|过去|截至|截止|以来|往后|开始|起算",
        other_text,
    ):
        return None
    return parse_date_range(match[0])


def parse_date_revision(text: str) -> tuple[DateRange, str] | None:
    match = _DATE_REVISION.fullmatch(text.strip())
    if match is None:
        return None
    quote = match["date"]
    span = parse_date_range(quote)
    return (span, quote) if span is not None else None
