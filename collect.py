import json, re, hashlib, calendar, argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser
import requests
from procurement import classify
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from bs4 import BeautifulSoup
ROOT = Path(__file__).resolve().parent
AGENT = 'LegalMonitor/1.0'
HISTORY_DAYS = 60
MAX_PAGES = 100
MAX_ITEMS = 50000

def now(): return datetime.now(timezone.utc).isoformat()
def clean(s): return ' '.join(BeautifulSoup(s or '', 'html.parser').get_text(' ', strip=True).split())
def canonical(url):
    p = urlsplit(url)
    if p.scheme not in ('https','http') or not p.netloc: return ''
    return urlunsplit((p.scheme,p.netloc.lower(),p.path,urlencode([(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not k.lower().startswith('utm_') and k not in ('fbclid','gclid')]),''))
def category(text):
    for label, stems in [('Законопроекты',('законопроект','первом чтении')),('Налоги',('налог','ндс','ндфл')),('Трудовое право',('трудов','работник','работодател')),('Судебная практика',('судеб','верховный суд','арбитраж')),('Финансы',('банк','финанс','кредит')),('Госзакупки',('закуп','44-фз','223-фз')),('Гражданское право',('гражданск','договор','наследств')),('Уголовное право',('уголов','ук рф'))]:
        if any(x in text.lower() for x in stems): return label
    return 'Прочее'
def fetch(url, check_robots=False):
    if check_robots:
        p=urlsplit(url); robots=requests.get(f'{p.scheme}://{p.netloc}/robots.txt',timeout=15,headers={'User-Agent':AGENT})
        if robots.status_code != 404:
            robots.raise_for_status(); rp=RobotFileParser(); rp.parse(robots.text.splitlines())
            if not rp.can_fetch(AGENT,url): raise RuntimeError('robots.txt запрещает сбор')
    with requests.get(url,timeout=(10,25),headers={'User-Agent':AGENT},stream=True) as r:
        r.raise_for_status(); chunks=[]; size=0
        for chunk in r.iter_content(65536):
            size+=len(chunk)
            if size>5_000_000: raise ValueError('Ответ слишком большой')
            chunks.append(chunk)
        return b''.join(chunks)
def parse(source, raw):
    typ=source['type']; base=source['url']; results=[]
    if typ=='rss':
        root=ET.fromstring(raw)
        def local(e): return e.tag.split('}')[-1]
        if local(root) not in ('rss','feed','RDF'): raise ValueError('Не RSS/Atom')
        for e in [x for x in root.iter() if local(x) in ('item','entry')][:100]:
            def field(*names):
                return next((''.join(x.itertext()) for x in e if local(x) in names), '')
            link=next((x.get('href') or ''.join(x.itertext()) for x in e if local(x)=='link' and x.get('rel','alternate')=='alternate'), '')
            d=field('pubDate','published','date'); date=None
            if d:
                try:
                    try: dt=parsedate_to_datetime(d)
                    except ValueError: dt=datetime.fromisoformat(d.replace('Z','+00:00'))
                    date=dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
                except (ValueError,TypeError,OverflowError): pass
            results.append((clean(field('title')),urljoin(base,link) if link else '',date,clean(field('description','summary'))))
    else:
        soup=BeautifulSoup(raw,'html.parser')
        if typ=='html':
            for a in soup.select(source['selector'])[:100]: results.append((clean(a.get_text()),urljoin(base,a.get('href','')) if a.get('href') else '',None,''))
        elif typ=='telegram':
            for post in soup.select('.tgme_widget_message')[:100]:
                t=post.select_one('.tgme_widget_message_text'); a=post.select_one('a.tgme_widget_message_date'); d=post.select_one('time')
                if t and a: results.append((clean(t.get_text(' '))[:160],urljoin(base,a.get('href','')),d.get('datetime') if d else None,clean(t.get_text(' '))))
        else: raise ValueError('Неизвестный тип')
        if not results: raise ValueError('Нет записей: проверьте селектор или доступ')
    out=[]
    for title,url,date,summary in results:
        url=canonical(url); text=title+' '+summary
        if not title or not url or (source.get('keywords') and not any(k.lower() in text.lower() for k in source['keywords'])): continue
        tags=classify(text)
        if source.get('procurement_only') and tags is None: continue
        laws,topics,news_type=tags or ([],[], 'Прочее')
        out.append(dict(laws=laws,topics=topics,news_type=news_type,id=hashlib.sha256(url.encode()).hexdigest()[:24],title=title[:300],url=url,published=date,first_seen=now(),source=source['name'],kind=source['kind'],category=source.get('category') or category(text),status='Не проверен'))
    return out


def collect_history(source):
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=HISTORY_DAYS
    )

    records = {}
    url = source["url"]

    for page in range(MAX_PAGES):
        raw = fetch(url, source["type"] != "rss")

        for item in parse(source, raw):
            published = item.get("published")

            if published:
                try:
                    dt = datetime.fromisoformat(
                        published.replace("Z", "+00:00")
                    )
                    if dt < cutoff:
                        continue
                except ValueError:
                    pass

            records[item["id"]] = item

        if source["type"] != "telegram":
            break

        soup = BeautifulSoup(raw, "html.parser")

        posts = soup.select("[data-post]")
        ids = []
        dates = []

        for post in posts:
            post_id = post.get("data-post", "").split("/")[-1]

            if post_id.isdigit():
                ids.append(int(post_id))

            t = post.select_one("time")
            if t and t.get("datetime"):
                try:
                    dates.append(datetime.fromisoformat(
                        t["datetime"].replace("Z", "+00:00")
                    ))
                except ValueError:
                    pass

        if not ids:
            break

        if dates and min(dates) <= cutoff:
            break

        next_url = source["url"] + "?before=" + str(min(ids))

        if next_url == url:
            break

        url = next_url

        import time
        time.sleep(2)

    return list(records.values())


def run(config,output):
    sources=json.loads(config.read_text(encoding='utf-8')); old=json.loads(output.read_text(encoding='utf-8')) if output.exists() else {'items':[]}
    items={i['id']:i for i in old['items'] if i.get('laws') or not any(s.get('procurement_only') for s in sources)}; report=[]; success=0
    for s in sources:
        if not s.get('enabled'): continue
        try:
            records = collect_history(s)
success += 1
            for i in records:
                if i['id'] in items: i['first_seen']=items[i['id']]['first_seen']
                items[i['id']]=i
            report.append({'source':s['name'],'ok':True,'count':len(records)})
        except Exception as e:
            report.append({'source':s['name'],'ok':False,'error':type(e).__name__}); print(s['name'],type(e).__name__,str(e)[:200])
    data={'updated':now(),'sources':report,'items':sorted(items.values(),key=lambda i:i['published'] or i['first_seen'],reverse=True)[:MAX_ITEMS]}
    output.parent.mkdir(parents=True,exist_ok=True); tmp=output.with_suffix('.tmp'); tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(output)
    print(f'{success}/{len(report)} источников; {len(data["items"])} записей')
    return 0 if success else 1
if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--config',type=Path,default=ROOT/'sources.json'); p.add_argument('--output',type=Path,default=ROOT/'docs/news.json'); a=p.parse_args(); raise SystemExit(run(a.config,a.output))
