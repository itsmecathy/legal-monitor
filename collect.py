
import json
import re
import hashlib
import argparse
import time

from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import (
    urljoin, urlsplit, urlunsplit,
    parse_qsl, urlencode
)
from urllib.robotparser import RobotFileParser
from email.utils import parsedate_to_datetime

import requests
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from procurement import classify


ROOT = Path(__file__).resolve().parent

AGENT = "LegalMonitor/2.0"

HISTORY_DAYS = 60
MAX_PAGES = 100
MAX_ITEMS = 50000
MAX_RSS_ITEMS = 1000
MAX_RESPONSE = 15_000_000

CONNECT_TIMEOUT = 30
READ_TIMEOUT = 60
RETRIES = 3

HEADERS = {
    "User-Agent": AGENT,
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept": "*/*"
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

ROBOTS_CACHE = {}


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(s):
    return " ".join(
        BeautifulSoup(
            s or "", "html.parser"
        ).get_text(" ", strip=True).split()
    )


def canonical(url):
    p = urlsplit(url)

    if p.scheme not in ("https", "http") or not p.netloc:
        return ""

    query = urlencode([
        (k, v)
        for k, v in parse_qsl(
            p.query, keep_blank_values=True
        )
        if not k.lower().startswith("utm_")
        and k not in ("fbclid", "gclid")
    ])

    return urlunsplit((
        p.scheme,
        p.netloc.lower(),
        p.path,
        query,
        ""
    ))


def parse_date(value):
    if not value:
        return None

    value = str(value).strip()

    try:
        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            dt = None

    if dt is None:
        for fmt in (
            "%d.%m.%Y",
            "%d.%m.%Y %H:%M",
            "%d.%m.%Y %H:%M:%S"
        ):
            try:
                dt = datetime.strptime(value, fmt)
                dt = dt.replace(
                    tzinfo=timezone(timedelta(hours=3))
                )
                break
            except ValueError:
                pass

    if dt is None:
        return None

    return dt.replace(
        tzinfo=dt.tzinfo or timezone.utc
    ).astimezone(timezone.utc).isoformat()


def category(text):
    text = text.lower()

    groups = [
        ("Госзакупки", (
            "закуп", "44-фз", "223-фз"
        )),
        ("Законопроекты", (
            "законопроект", "первом чтении"
        )),
        ("Налоги", (
            "налог", "ндс", "ндфл"
        )),
        ("Трудовое право", (
            "трудов", "работник", "работодател"
        )),
        ("Судебная практика", (
            "судеб", "верховный суд", "арбитраж"
        )),
        ("Финансы", (
            "банк", "финанс", "кредит"
        )),
        ("Гражданское право", (
            "гражданск", "договор", "наследств"
        )),
        ("Уголовное право", (
            "уголов", "ук рф"
        ))
    ]

    for label, stems in groups:
        if any(x in text for x in stems):
            return label

    return "Прочее"


def download(url, allow_404=False):
    for attempt in range(1, RETRIES + 1):
        try:
            with SESSION.get(
                url,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                stream=True
            ) as r:

                if allow_404 and r.status_code == 404:
                    return None

                r.raise_for_status()

                chunks = []
                size = 0

                for chunk in r.iter_content(65536):
                    size += len(chunk)

                    if size > MAX_RESPONSE:
                        raise ValueError(
                            "Ответ слишком большой"
                        )

                    chunks.append(chunk)

                return b"".join(chunks)

        except requests.exceptions.SSLError:
            raise

        except requests.exceptions.HTTPError as e:
            code = e.response.status_code

            if code not in (408, 429, 500, 502, 503, 504):
                raise

            if attempt == RETRIES:
                raise

            delay = 5 * attempt

            retry_after = e.response.headers.get(
                "Retry-After", ""
            )

            if retry_after.isdigit():
                delay = max(delay, int(retry_after))

            if delay > 120:
                raise

            print(
                f"HTTP {code}: повтор через {delay} сек.",
                flush=True
            )

            time.sleep(delay)

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError
        ) as e:

            print(
                f"Попытка {attempt}/{RETRIES}: "
                f"{url} — {type(e).__name__}",
                flush=True
            )

            if attempt == RETRIES:
                raise

            time.sleep(5 * attempt)

    raise RuntimeError("Не удалось загрузить страницу")


def check_permission(url):
    p = urlsplit(url)
    host = f"{p.scheme}://{p.netloc}"

    if host not in ROBOTS_CACHE:
        raw = download(
            host + "/robots.txt",
            allow_404=True
        )

        if raw is None:
            ROBOTS_CACHE[host] = None
        else:
            rp = RobotFileParser()
            rp.parse(
                raw.decode(
                    "utf-8", errors="replace"
                ).splitlines()
            )
            ROBOTS_CACHE[host] = rp

    rp = ROBOTS_CACHE[host]

    if rp is not None and not rp.can_fetch(AGENT, url):
        raise RuntimeError(
            "robots.txt запрещает сбор"
        )


def fetch(url, check_robots=False):
    if check_robots:
        check_permission(url)

    return download(url)


def html_date(a, source):
    if a.get("datetime"):
        date = parse_date(a["datetime"])
        if date:
            return date

    node = a

    for _ in range(4):
        if node is None:
            break

        if node.name in ("html", "body", "main"):
            break

        selector = source.get(
            "date_selector", "time"
        )

        dates = node.select(selector)

        if len(dates) == 1:
            d = dates[0]

            value = (
                d.get("datetime")
                or d.get("content")
                or d.get_text(" ", strip=True)
            )

            date = parse_date(value)

            if date:
                return date

        if len(dates) > 1:
            break

        node = node.parent

    return None


def parse(source, raw):
    typ = source["type"]
    base = source["url"]

    results = []

    if typ == "rss":
        root = ET.fromstring(raw)

        def local(e):
            return e.tag.split("}")[-1]

        if local(root) not in ("rss", "feed", "RDF"):
            raise ValueError("Ответ не является RSS/Atom")

        entries = [
            x for x in root.iter()
            if local(x) in ("item", "entry")
        ]

        for e in entries[:MAX_RSS_ITEMS]:

            def field(*names):
                return next((
                    "".join(x.itertext())
                    for x in e
                    if local(x) in names
                ), "")

            link = next((
                x.get("href") or "".join(x.itertext())
                for x in e
                if local(x) == "link"
                and x.get("rel", "alternate") == "alternate"
            ), "")

            date = parse_date(
                field("pubDate", "published", "date", "updated")
            )

            summary = clean(
                field("description", "summary", "content")
            )

            results.append((
                clean(field("title")),
                urljoin(base, link) if link else "",
                date,
                summary
            ))

    elif typ == "html":
        soup = BeautifulSoup(raw, "html.parser")

        for a in soup.select(
            source["selector"]
        )[:MAX_RSS_ITEMS]:

            title = clean(a.get_text(" ", strip=True))
            href = a.get("href", "")

            if not title or not href:
                continue

            results.append((
                title,
                urljoin(base, href),
                html_date(a, source),
                ""
            ))

    elif typ == "telegram":
        soup = BeautifulSoup(raw, "html.parser")

        for post in soup.select(".tgme_widget_message"):
            t = post.select_one(
                ".tgme_widget_message_text"
            )

            a = post.select_one(
                "a.tgme_widget_message_date"
            )

            d = post.select_one("time")

            if t and a:
                text = clean(t.get_text(" "))

                results.append((
                    text[:160],
                    urljoin(base, a.get("href", "")),
                    parse_date(
                        d.get("datetime") if d else None
                    ),
                    text
                ))

    else:
        raise ValueError(
            f"Неизвестный тип источника: {typ}"
        )

    if not results:
        raise ValueError(
            "Нет записей: проверьте источник или селектор"
        )

    print(
        f"{source['name']}: получено "
        f"{len(results)} исходных записей",
        flush=True
    )

    out = []

    for title, url, date, summary in results:
        url = canonical(url)
        text = title + " " + summary

        if not title or not url:
            continue

        if source.get("keywords"):
            if not any(
                k.lower() in text.lower()
                for k in source["keywords"]
            ):
                continue

        tags = classify(text)

        if source.get("procurement_only") and tags is None:
            continue

        laws, topics, news_type = tags or (
            [], [], "Прочее"
        )

        out.append(dict(
            id=hashlib.sha256(
                url.encode()
            ).hexdigest()[:24],

            title=title[:300],
            url=url,
            published=date,
            first_seen=now(),
            source=source["name"],
            kind=source["kind"],
            laws=laws,
            topics=topics,
            news_type=news_type,
            category=source.get("category") or category(text),
            status="Не проверен"
        ))

    return out


def collect_history(source):
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=HISTORY_DAYS
    )

    records = {}
    visited = set()
    warning = None

    url = source["url"]

    for page in range(MAX_PAGES):
        if url in visited:
            break

        visited.add(url)

        try:
            raw = fetch(
                url,
                source["type"] != "rss"
            )

            parsed = parse(source, raw)

        except Exception as e:
            if page == 0:
                raise

            warning = (
                f"Архив загружен частично: "
                f"{type(e).__name__}: {e}"
            )

            print(warning, flush=True)
            break

        for item in parsed:
            published = parse_date(
                item.get("published")
            )

            if published:
                dt = datetime.fromisoformat(published)

                if dt < cutoff:
                    continue

            records[item["id"]] = item

        if source["type"] != "telegram":
            break

        soup = BeautifulSoup(raw, "html.parser")

        ids = []
        dates = []

        for post in soup.select(
            ".tgme_widget_message[data-post]"
        ):
            post_id = post.get(
                "data-post", ""
            ).split("/")[-1]

            if post_id.isdigit():
                ids.append(int(post_id))

            t = post.select_one("time")

            if t:
                date = parse_date(t.get("datetime"))

                if date:
                    dates.append(
                        datetime.fromisoformat(date)
                    )

        if not ids:
            break

        if dates and min(dates) <= cutoff:
            break

        p = urlsplit(source["url"])

        params = dict(parse_qsl(p.query))
        params["before"] = str(min(ids))

        url = urlunsplit((
            p.scheme,
            p.netloc,
            p.path,
            urlencode(params),
            ""
        ))

        if url in visited:
            break

        if page == MAX_PAGES - 1:
            warning = "Достигнут предел страниц архива"
            break

        time.sleep(2)

    return list(records.values()), warning


def run(config, output):
    ROBOTS_CACHE.clear()

    sources = json.loads(
        config.read_text(encoding="utf-8")
    )

    if output.exists():
        old = json.loads(
            output.read_text(encoding="utf-8")
        )
    else:
        old = {"items": []}

    items = {
        i["id"]: i
        for i in old.get("items", [])
    }

    report = []
    success = 0

    for s in sources:
        if not s.get("enabled"):
            continue

        print("\n" + "=" * 50, flush=True)
        print(s["name"], flush=True)

        try:
            records, warning = collect_history(s)

            success += 1

            for i in records:
                previous = items.get(i["id"])

                if previous:
                    i["first_seen"] = previous.get(
                        "first_seen", now()
                    )

                    if not i.get("published"):
                        i["published"] = previous.get(
                            "published"
                        )

                    i["status"] = previous.get(
                        "status", "Не проверен"
                    )

                items[i["id"]] = i

            entry = {
                "source": s["name"],
                "ok": True,
                "count": len(records)
            }

            if warning:
                entry["warning"] = warning[:500]

            report.append(entry)

            print(
                f"Подходящих новостей: {len(records)}",
                flush=True
            )

        except Exception as e:
            report.append({
                "source": s["name"],
                "ok": False,
                "count": 0,
                "error": type(e).__name__,
                "details": str(e)[:500]
            })

            print(
                s["name"],
                type(e).__name__,
                str(e)[:500],
                flush=True
            )

    data = {
        "updated": now(),
        "sources": report,
        "items": sorted(
            items.values(),
            key=lambda i: (
                parse_date(i.get("published"))
                or parse_date(i.get("first_seen"))
                or ""
            ),
            reverse=True
        )[:MAX_ITEMS]
    }

    output.parent.mkdir(
        parents=True, exist_ok=True
    )

    tmp = output.with_suffix(".tmp")

    tmp.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    tmp.replace(output)

    print(
        f'{success}/{len(report)} источников; '
        f'{len(data["items"])} записей',
        flush=True
    )

    return 0 if success else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()

    p.add_argument(
        "--config",
        type=Path,
        default=ROOT / "sources.json"
    )

    p.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/news.json"
    )

    a = p.parse_args()

    raise SystemExit(
        run(a.config, a.output)
    )
