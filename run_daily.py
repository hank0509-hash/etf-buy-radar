# -*- coding: utf-8 -*-
"""ETF 共識雷達 V8：原有行情排名＋每日持股快照＋四類歷史排行榜。

只需取代 GitHub 儲存庫根目錄的 run_daily.py；不需要修改 GitHub Actions。
股價屬最新可取得收盤資料（非盤中即時價）；實收資本額不等於總市值。
來源失敗時保留既有 ETF 排名並顯示缺值，不以估計價填補。
"""
from __future__ import annotations
import json
import math
import re
import shutil
import zipfile
from datetime import datetime, date
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
SITE = BASE / 'site'
OUT = BASE / 'ETF_Buy_Radar_V6_output'
TZ = ZoneInfo('Asia/Taipei')

TWSE_QUOTES = 'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL'
TWSE_COMPANIES = 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L'
TPEX_QUOTES = 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes'
TPEX_COMPANIES = 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O'

EXTRA = ['市場', '最新收盤價_元', '收盤資料日', '實收資本額_億元',
         '估算總市值_億元', '當日成交量_張', '當日成交金額_億元', '行情來源']


def n(x):
    try:
        if x is None or isinstance(x, bool): return None
        value = float(str(x).replace(',', '').replace('，', '').strip())
        return value if math.isfinite(value) else None
    except (ValueError, TypeError): return None


def first(obj, *keys):
    for key in keys:
        if key in obj and obj[key] is not None and str(obj[key]).strip():
            return obj[key]
    return None


def parse_day(raw):
    if not raw: return None
    s = str(raw).strip().split(' ')[0]
    try:
        if re.fullmatch(r'\d{7}', s):
            return date(int(s[:3]) + 1911, int(s[3:5]), int(s[5:7]))
        if re.fullmatch(r'\d{8}', s):
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        p = re.findall(r'\d+', s)
        if len(p) >= 3:
            y = int(p[0]); y += 1911 if y < 1900 else 0
            return date(y, int(p[1]), int(p[2]))
    except ValueError: pass
    return None


def load_api(url, market_log):
    try:
        resp = requests.get(url, timeout=25, headers={
            'User-Agent': 'ETF-Buy-Radar/7.1 (public research)',
            'Accept': 'application/json'})
        if resp.status_code == 429:
            raise RuntimeError('HTTP 429；網站限制請求')
        resp.raise_for_status()
        value = resp.json()
        if not isinstance(value, list) or not value or not isinstance(value[0], dict):
            raise ValueError('回應不是有效的非空 JSON 陣列')
        market_log.append({'來源': url, '狀態': '成功', '筆數': len(value), '原因': ''})
        return value
    except (requests.RequestException, ValueError, RuntimeError) as e:
        market_log.append({'來源': url, '狀態': '失敗', '筆數': 0, '原因': str(e)[:250]})
        return []


def build_market_index(twse_quotes, twse_companies, tpex_quotes, tpex_companies, today):
    """Only merge a verified same-code company and official close; no implied share-price valuation."""
    items = {}
    specs = [
        ('上市', twse_quotes, twse_companies, 'TWSE'),
        ('上櫃', tpex_quotes, tpex_companies, 'TPEx'),
    ]
    for market, quotes, companies, source in specs:
        companies_by_code = {}
        for c in companies:
            code = str(first(c, '公司代號', 'Code', 'SecuritiesCompanyCode') or '').strip()
            if re.fullmatch(r'\d{4}', code):
                companies_by_code[code] = c
        for q in quotes:
            code = str(first(q, 'Code', 'SecuritiesCompanyCode', '代號', '股票代號') or '').strip()
            if not re.fullmatch(r'\d{4}', code): continue
            price = n(first(q, 'ClosingPrice', 'Close', '收盤', '收盤價'))
            day = parse_day(first(q, 'Date', '資料日期', '日期'))
            # Same market and recent close only; no quote forward-fill.
            if price is None or price <= 0 or day is None or (today - day).days not in range(0, 8):
                continue
            c = companies_by_code.get(code, {})
            capital = n(first(c, '實收資本額', 'PaidInCapital', 'Capital'))
            shares = n(first(c, '已發行普通股數或TDR原股發行股數', '已發行普通股數',
                             '已發行股份總數', 'IssuedShares'))
            cap_day = parse_day(first(c, '出表日期', '日期', 'Date'))
            # Company data may trail prices, but never use unknown or obviously stale capitalization.
            if cap_day is None or (today - cap_day).days not in range(0, 46):
                capital = None; shares = None
            # TWSE and TPEx corporate registries report paid-in capital in NTD.
            # If amount differs sharply from issued shares * NT$10 for an ordinary par-10 share,
            # avoid silently scaling unknown units. Capital can still be valid for non-NT$10 par,
            # so require plausible magnitude; no arbitrary conversion.
            if capital is not None and not (1e5 <= capital <= 1e14): capital = None
            if shares is not None and not (1e5 <= shares <= 1e12): shares = None
            traded = n(first(q, 'TradeVolume', 'TradingShares', '成交股數'))
            turnover = n(first(q, 'TradeValue', 'TransactionAmount', '成交金額'))
            value = {
                '市場': market,
                '最新收盤價_元': round(price, 3),
                '收盤資料日': day.isoformat(),
                '實收資本額_億元': round(capital / 1e8, 3) if capital is not None else None,
                '估算總市值_億元': round(price * shares / 1e8, 3) if shares is not None else None,
                '當日成交量_張': round(traded / 1000, 1) if traded is not None and traded >= 0 else None,
                '當日成交金額_億元': round(turnover / 1e8, 3) if turnover is not None and turnover >= 0 else None,
                '行情來源': source,
            }
            items[code] = value
    return items


def enrich_ranking(ranked, now, market_log):
    quotes_twse = load_api(TWSE_QUOTES, market_log)
    companies_twse = load_api(TWSE_COMPANIES, market_log)
    quotes_tpex = load_api(TPEX_QUOTES, market_log)
    companies_tpex = load_api(TPEX_COMPANIES, market_log)
    idx = build_market_index(quotes_twse, companies_twse, quotes_tpex, companies_tpex, now.date())
    enriched = ranked.copy()
    if '股票代號' not in enriched.columns:
        raise ValueError('排名表缺少股票代號，無法加入行情')
    # Add the market columns immediately after the stock name, preserving ranking order.
    place = list(enriched.columns).index('股票名稱') + 1
    for column in EXTRA:
        values = [idx.get(str(code).strip(), {}).get(column) for code in enriched['股票代號']]
        enriched.insert(place, column, values)
        place += 1
    return enriched, idx


def fmt(x, col):
    if x is None or pd.isna(x) or x == '': return '—'
    if col in ('最新收盤價_元',): return f'{float(x):,.2f}'
    if col.endswith('_億元'): return f'{float(x):,.2f}'
    if col == '當日成交量_張': return f'{float(x):,.0f}'
    return str(x)


def add_columns_to_html(html, enriched, market_log):
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table', {'id': 't'})
    if table is None or not table.find('thead') or not table.find('tbody'):
        raise RuntimeError('無法辨識原本的 ETF 排名表，不覆蓋已發布網站')
    th = table.find('thead').find_all('th')
    headers = [x.get_text(strip=True) for x in th]
    if '股票名稱' not in headers or len(table.find('tbody').find_all('tr')) != len(enriched):
        raise RuntimeError('原表格欄位或資料列數與排名不符，不發布錯位資料')
    i = headers.index('股票名稱') + 1
    ref = th[i - 1]
    for column in EXTRA:
        new = soup.new_tag('th'); new.string = column.replace('_', ' ')
        ref.insert_after(new)
        ref = new
    for r, (_, record) in zip(table.find('tbody').find_all('tr'), enriched.iterrows()):
        cells = r.find_all('td', recursive=False)
        if len(cells) != len(headers):
            raise RuntimeError('原表格列的儲存格數不符，不覆蓋網站')
        ref = cells[i - 1]
        for column in EXTRA:
            td = soup.new_tag('td'); td.string = fmt(record[column], column)
            ref.insert_after(td); ref = td
    title = soup.find('h1')
    if title:
        message = soup.new_tag('p')
        message['style'] = 'font-size:13px;color:#475569;max-width:1100px'
        valid = int(enriched['最新收盤價_元'].notna().sum())
        capital = int(enriched['實收資本額_億元'].notna().sum())
        message.string = (f'新增官方行情：{valid}/{len(enriched)} 檔有近期收盤價，'
                          f'{capital}/{len(enriched)} 檔有實收資本額。'
                          '股價是最近一個交易日的收盤價，並非盤中即時價；'
                          '總市值為收盤價 × 公開發行股數估算，與股本不同。'
                          '來源失敗、未上市櫃或資料過期顯示 —。')
        title.insert_after(message)
    return str(soup)


def build_site(radar, ranked: pd.DataFrame, active: pd.DataFrame, upcoming: pd.DataFrame):
    if ranked is None or ranked.empty:
        raise RuntimeError('ETF 排名為空；保留上次有效網站')
    if active.empty and upcoming.empty:
        raise RuntimeError('沒有可用的 ETF 持股／公告事件；保留上次有效網站')
    source = OUT / 'ETF_一頁式排名.html'
    if not source.exists(): raise RuntimeError('缺少 V6 的原始排名網頁')
    now = datetime.now(TZ)
    daystr = now.date().isoformat()
    event_day = ''
    if not active.empty and '後持股日' in active.columns:
        dates = active['後持股日'].dropna().astype(str)
        if len(dates):
            event_day = max(dates)
            if date.fromisoformat(event_day) > now.date():
                raise RuntimeError('ETF 持股事件日期晚於現在，拒絕發布')
    market_log = []
    full, idx = enrich_ranking(ranked, now, market_log)
    original_html = source.read_text(encoding='utf-8')
    html = add_columns_to_html(original_html, full, market_log)
    if '<div class="tip">' not in html and 'class="tip"' not in html:
        raise RuntimeError('找不到 ETF 原網站的風險註記，不覆蓋網站')
    summary = soup_summary(now, event_day, active, upcoming)
    soup = BeautifulSoup(html, 'html.parser')
    anchor = soup.select_one('.tip')
    if anchor:
        tag = BeautifulSoup(summary, 'html.parser')
        anchor.insert_before(tag)
    links = BeautifulSoup('''<div style="margin:14px 0;display:flex;gap:12px;flex-wrap:wrap">
      <a href="latest.csv" download>下載最新排名（含股價與資本額）</a>
      <a href="active_detail.csv" download>主動 ETF 明細</a>
      <a href="passive_upcoming.csv" download>被動待生效公告</a>
      <a href="results.zip" download>完整結果</a>
      <a href="status.json">資料更新狀態</a></div>''', 'html.parser')
    if anchor: anchor.insert_before(links)
    html = str(soup)

    # All validations above finished before publishing replacement site files.
    SITE.mkdir(exist_ok=True)
    (SITE / 'history').mkdir(exist_ok=True)
    copies = {
        'active_detail.csv': '02_主動ETF逐檔股數變化.csv',
        'passive_upcoming.csv': '03_被動ETF公告尚未生效.csv',
        'unverified_passive.csv': '05_官方公告但股票名單未核對.csv',
        'source_audit.csv': '06_資料來源紀錄.csv',
        'source_errors.csv': '07_抓取錯誤.csv',
    }
    for target, original in copies.items():
        path = OUT / original
        if not path.exists(): raise RuntimeError(f'缺少原始輸出：{original}')
    for target, original in copies.items():
        shutil.copy2(OUT / original, SITE / target)
    full.to_csv(SITE / 'latest.csv', index=False, encoding='utf-8-sig')
    full.to_csv(SITE / 'history' / f'{daystr}.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame(market_log).to_csv(SITE / 'market_source_audit.csv', index=False, encoding='utf-8-sig')
    old_zip = BASE / 'ETF_Buy_Radar_V6_結果.zip'
    if not old_zip.exists(): raise RuntimeError('缺少完整 V6 ZIP')
    # Preserve V6 original exports; also add enhanced CSV and market-data audit.
    with zipfile.ZipFile(SITE / 'results.zip', 'w', compression=zipfile.ZIP_DEFLATED) as dest:
        with zipfile.ZipFile(old_zip, 'r') as existing:
            for entry in existing.infolist():
                dest.writestr(entry, existing.read(entry.filename))
        dest.write(SITE / 'latest.csv', '08_買進排名_含股價股本市值.csv')
        dest.write(SITE / 'market_source_audit.csv', '09_行情來源稽核.csv')
    passive_missing = OUT / '05_官方公告但股票名單未核對.csv'
    missing = len(pd.read_csv(passive_missing)) if passive_missing.exists() else None
    status = {
        'run_time_taipei': now.isoformat(timespec='seconds'),
        'last_active_event_date': event_day,
        'ranked_stocks': len(full),
        'stocks_with_official_recent_close': int(full['最新收盤價_元'].notna().sum()),
        'stocks_with_paid_in_capital': int(full['實收資本額_億元'].notna().sum()),
        'active_etfs_with_valid_change_events': int(active['ETF代號'].nunique()) if len(active) else 0,
        'passive_etfs_with_unexpired_announced_additions': int(upcoming['ETF代號'].nunique()) if len(upcoming) else 0,
        'unverified_index_announcements': missing,
        'market_sources': market_log,
        'scope': 'partial ETF market coverage; latest public TWSE / TPEx close, not live prices',
        'risk_note': 'ETF future-buy amount is a hypothetical continuation scenario, not an order.',
    }
    (SITE / 'status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    (SITE / 'index.html').write_text(html, encoding='utf-8')
    print(f'網站更新完成：ETF 排名 {len(full)} 檔，'
          f'官方近期股價 {status["stocks_with_official_recent_close"]} 檔，'
          f'實收資本額 {status["stocks_with_paid_in_capital"]} 檔')
    for item in market_log:
        print(f'[{item["狀態"]}] {item["來源"]} ({item["筆數"]}筆) {item["原因"]}')
    return status, idx


def soup_summary(now, event_day, active, upcoming):
    count = int(active['ETF代號'].nunique()) if len(active) else 0
    passive_count = int(upcoming['ETF代號'].nunique()) if len(upcoming) else 0
    return ('<div style="background:white;border:1px solid #cbd5e1;border-radius:10px;'
            'padding:13px 16px;margin:12px 0;line-height:1.7">'
            f'<strong>每日自動更新</strong>：{escape(now.strftime("%Y/%m/%d %H:%M"))}（臺灣）'
            f'｜<strong>ETF 持股事件日</strong>：{escape(event_day or "缺資料")}'
            f'｜<strong>主動 ETF 可比檔數</strong>：{count}'
            f'｜<strong>被動未生效公告 ETF 數</strong>：{passive_count}'
            '<div style="font-size:12px;color:#475569">'
            '新增股價／資本額按官方最近可得資料更新；缺值顯示 —。'
            'ETF 來源仍非全市場；未來續買金額只是情境假設。</div></div>')


# ────────────────────────────────────────────────────────────────────────────
# V8: Active ETF point-in-time holdings archive + separate research leaderboards
# These are ESTIMATES from fund holdings, not executed orders or future orders.
# ────────────────────────────────────────────────────────────────────────────
from collections import defaultdict
from statistics import median
from datetime import timedelta

ARCHIVE = SITE / 'holdings_history'
V8_CSV = {
    'v8_current.csv':'現持股共識',
    'v8_new.csv':'新出現持股',
    'v8_add_5.csv':'近5期淨增持',
    'v8_add_20.csv':'近20期淨增持',
    'v8_add_60.csv':'近60期淨增持',
    'v8_exit_5.csv':'近5期共同減碼',
    'v8_exit_20.csv':'近20期共同減碼',
    'v8_exit_60.csv':'近60期共同減碼',
    'v8_long.csv':'長期持有',
}


def v8_date(raw):
    try:
        d = date.fromisoformat(str(raw)[:10])
        return d
    except (TypeError, ValueError):
        return None


def v8_number(raw):
    try:
        x = float(raw)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def v8_normalize_fund(fund, today):
    """Require actual holdings and a dated complete fund snapshot, not event-only data."""
    if not isinstance(fund, dict) or fund.get('status') != 'ok':
        return None
    asof = v8_date(fund.get('data_date'))
    if asof is None or asof > today or (today - asof).days > 7:
        return None
    raw = fund.get('holdings')
    if not isinstance(raw, list) or len(raw) < 5:
        return None
    holdings = {}
    for h in raw:
        if not isinstance(h, dict):
            continue
        code = str(h.get('code', '')).strip()
        shares = v8_number(h.get('shares'))
        if not re.fullmatch(r'\d{4}', code) or shares is None or shares < 0:
            continue
        if code in holdings:
            return None  # duplicate indicates a malformed full snapshot
        if shares > 0:
            holdings[code] = {'shares': shares,
                              'weight': v8_number(h.get('weight')),
                              'name': str(h.get('name') or '')[:80]}
    if len(holdings) < 5 or len(holdings) < len(raw) * 0.65:
        return None
    return asof.isoformat(), {'name': str(fund.get('name') or ''),
                              'issuer': str(fund.get('issuer') or ''),
                              'holdings': holdings}


def v8_archive_current(source, today):
    """Archive by ACTUAL fund holdings date; GitHub commits these immutable snapshots."""
    accepted = defaultdict(dict)
    failures = []
    etfs = source.get('etfs') if isinstance(source, dict) else None
    if not isinstance(etfs, dict):
        return {'received': 0, 'valid': 0, 'created': 0, 'problems': ['主動來源不可用，保留歷史']}
    for ticker, fund in etfs.items():
        ticker = str(ticker).strip()
        if not re.fullmatch(r'\d{4,6}[A-Z]?', ticker):
            continue
        result = v8_normalize_fund(fund, today)
        if not result:
            failures.append(ticker)
            continue
        asof, content = result
        accepted[asof][ticker] = content
    if not accepted:
        return {'received': len(etfs), 'valid': 0, 'created': 0,
                'problems': ['沒有合格的完整持股快照；未修改歷史']}
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    created = 0
    for asof, funds in sorted(accepted.items()):
        file = ARCHIVE / f'{asof}.json'
        prior = {}
        if file.exists():
            try:
                value = json.loads(file.read_text(encoding='utf-8'))
                if isinstance(value.get('etfs'), dict):
                    prior = value['etfs']
            except (ValueError, OSError, AttributeError):
                raise RuntimeError(f'持股歷史檔案損毀：{file}；拒絕覆寫')
        original = len(prior)
        # Never change a committed snapshot for the same ETF/as-of date.
        for ticker, data in funds.items():
            if ticker not in prior:
                prior[ticker] = data
                created += 1
        if len(prior) != original:
            payload = {'schema': 'v8-snapshot-1', 'asof': asof,
                       'source': 'nctuwanglin/active-etf/active.json',
                       'etfs': dict(sorted(prior.items()))}
            file.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                            encoding='utf-8')
    return {'received': len(etfs), 'valid': sum(map(len, accepted.values())),
            'created': created, 'problems': [f'無合格完整持股：{len(failures)} 檔ETF'] if failures else []}


def v8_load_history():
    history = {}
    for path in sorted(ARCHIVE.glob('????-??-??.json'))[-400:] if ARCHIVE.exists() else []:
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            day = v8_date(path.stem)
            if day is None or str(day) != value.get('asof') or not isinstance(value.get('etfs'), dict):
                continue
            history[day] = value['etfs']
        except (OSError, ValueError, AttributeError):
            continue
    return history


def v8_periods(history):
    return sorted(history)


def v8_good_pair(day0, day1, history, etf):
    # These are *observations*, not a verified trading calendar. Reject unexplained gaps.
    return (day0 < day1 and (day1 - day0).days <= 4
            and etf in history.get(day0, {}) and etf in history.get(day1, {}))


def v8_adjusted_changes(older, newer):
    """Scale-corrected portfolio changes; exclude implausible portfolio-wide jumps."""
    before, after = older['holdings'], newer['holdings']
    common = [after[c]['shares'] / before[c]['shares'] for c in before.keys() & after.keys()
              if before[c]['shares'] > 0 and after[c]['shares'] > 0]
    if len(common) < 5:
        return []
    scale = median(common)
    if not 0.5 <= scale <= 2.0:
        return []
    results = []
    for code in before.keys() | after.keys():
        before_shares = before.get(code, {}).get('shares', 0)
        after_shares = after.get(code, {}).get('shares', 0)
        if before_shares <= 0 and after_shares <= 0:
            continue
        # Threshold filters micro noise (fund subscriptions) and material tracking changes.
        delta = after_shares - before_shares * scale
        if abs(delta) < max(100.0, 0.002 * before_shares):
            continue
        if before_shares > 0 and after_shares > 0:
            ratio = after_shares / before_shares
            if ratio > 4 or ratio < 0.25:
                continue  # possible split / rights issue: no corporate-action ledger
        op = '新增建倉' if before_shares == 0 else ('清倉' if after_shares == 0 else
                                            ('加碼' if delta > 0 else '減碼'))
        h = after.get(code, before.get(code, {}))
        results.append({'code': code, 'name': h.get('name', ''),
                        'delta': delta, 'operation': op})
    return results


def v8_daily_events(history):
    dates = v8_periods(history)
    events = []
    for prev, curr in zip(dates[:-1], dates[1:]):
        if (curr - prev).days > 4:  # unknown missing history, not a one-day order
            continue
        for etf in history[prev].keys() & history[curr].keys():
            older, newer = history[prev][etf], history[curr][etf]
            for item in v8_adjusted_changes(older, newer):
                events.append({'日期': curr, 'ETF代號': etf,
                               '投信': newer.get('issuer', ''),
                               '股票代號': item['code'],
                               '股票名稱': item['name'],
                               '動作': item['operation'], '校正股數變化': item['delta']})
    return events


def v8_current_holdings(history):
    dates = v8_periods(history)
    if not dates:
        return pd.DataFrame(columns=['股票代號','股票名稱','持有ETF數','投信數','ETF清單',
                                     '權重加總_%_非加權','目前持股日'])
    last = dates[-1]
    stocks = defaultdict(lambda: {'name': '', 'etfs': set(), 'issuers': set(), 'weights': []})
    for etf, fund in history[last].items():
        for code, h in fund.get('holdings', {}).items():
            s = stocks[code]
            s['name'] = h.get('name') or s['name']
            s['etfs'].add(etf)
            s['issuers'].add(fund.get('issuer') or etf)
            if h.get('weight') is not None:
                s['weights'].append(h['weight'])
    rows = [{'股票代號': code, '股票名稱': s['name'],
             '持有ETF數': len(s['etfs']), '投信數': len(s['issuers']),
             'ETF清單': '、'.join(sorted(s['etfs'])),
             '權重加總_%_非加權': round(sum(s['weights']), 3) if s['weights'] else None,
             '目前持股日': str(last)} for code, s in stocks.items()]
    return pd.DataFrame(rows).sort_values(['持有ETF數','投信數','股票代號'],
                                         ascending=[False, False, True]).reset_index(drop=True)


def v8_new_entries(events, history, market=None):
    dates = v8_periods(history)
    latest = dates[-1] if dates else None
    recent = set(dates[-20:])
    b = defaultdict(lambda: {'name': '', 'etfs': set(), 'issuers': set(), 'dates': set(), 'shares': 0.0})
    for e in events:
        if e['動作'] != '新增建倉' or e['日期'] not in recent:
            continue
        key = e['股票代號']; s = b[key]
        s['name'] = e['股票名稱'] or s['name']
        s['etfs'].add(e['ETF代號']); s['issuers'].add(e['投信']); s['dates'].add(e['日期'])
        s['shares'] += e['校正股數變化']
    rows = []
    for code, s in b.items():
        p = v8_number((market or {}).get(code, {}).get('最新收盤價_元'))
        amt = round(s['shares'] * p / 1e8, 4) if p is not None else None
        rows.append({'股票代號': code, '股票名稱': s['name'], '近20期新出現ETF數': len(s['etfs']),
                     '獨立投信數': len(s['issuers']), '新增ETF清單': '、'.join(sorted(s['etfs'])),
                     '新出現股數_張': round(s['shares']/1000,1), '最新價估值_億元': amt,
                     '最後首次觀察日': str(max(s['dates'])), '歷史覆蓋觀測日數': len(dates),
                     '說明': '前一完整快照為0、後一完整快照>0；不等於真實交易時點'})
    cols = ['股票代號','股票名稱','近20期新出現ETF數','獨立投信數',
            '新增ETF清單','新出現股數_張','最新價估值_億元','最後首次觀察日','歷史覆蓋觀測日數','說明']
    return (pd.DataFrame(rows, columns=cols).sort_values(
        ['近20期新出現ETF數','獨立投信數','最後首次觀察日'],
        ascending=[False,False,False]).reset_index(drop=True) if rows else pd.DataFrame(columns=cols))


def v8_period_ranking(events, history, periods, direction, market=None):
    dates = v8_periods(history)
    cols = ['股票代號','股票名稱','區間','方向','同向ETF數','獨立投信數','累計校正股數_張',
            '期末收盤價_元','期末價估算金額_億元','ETF清單','資料可比ETF數','說明']
    if len(dates) < periods + 1:
        return pd.DataFrame(columns=cols)
    selected = dates[-(periods+1):]
    # Fixed ETF cohort: every dated snapshot is present for the entire window.
    cohort = set.intersection(*(set(history[day]) for day in selected))
    if not cohort:
        return pd.DataFrame(columns=cols)
    # A series with a calendar gap >4 days is unknown, not a known trading window.
    if any((b-a).days > 4 for a,b in zip(selected[:-1], selected[1:])):
        return pd.DataFrame(columns=cols)
    # Sum all changes per ETF/stock *before* counting a positive/negative signal.
    # This prevents a brief buy followed by a larger sell from being called "加碼".
    pair_net = defaultdict(float)
    pair_names = {}
    for e in events:
        if e['ETF代號'] not in cohort or e['日期'] not in selected[1:]:
            continue
        key=(e['ETF代號'], e['股票代號'])
        pair_net[key] += e['校正股數變化']
        pair_names[key] = e['股票名稱']
    b = defaultdict(lambda: {'name':'', 'etfs': set(), 'issuers': set(), 'deltas': 0.0})
    for (etf,code),delta in pair_net.items():
        if abs(delta) < 100:
            continue
        if direction == '增加' and delta <= 0:
            continue
        if direction == '減少' and delta >= 0:
            continue
        item = b[code]
        item['name'] = pair_names[(etf,code)] or item['name']
        item['etfs'].add(etf)
        item['issuers'].add(history[selected[-1]][etf].get('issuer') or etf)
        item['deltas'] += delta
    rows = []
    for code, s in b.items():
        shares = abs(s['deltas'])
        info = (market or {}).get(code, {})
        price = v8_number(info.get('最新收盤價_元'))
        value = round(shares * price / 1e8, 4) if price is not None else None
        rows.append({'股票代號':code,'股票名稱':s['name'],'區間':f'{periods}個連續觀測間隔',
                     '方向':direction,'同向ETF數':len(s['etfs']),
                     '獨立投信數':len(s['issuers']),
                     '累計校正股數_張':round(shares / 1000, 1),
                     '期末收盤價_元':price, '期末價估算金額_億元':value,
                     'ETF清單':'、'.join(sorted(s['etfs'])),
                     '資料可比ETF數':len(cohort),
                     '說明':'固定ETF樣本且整段區間淨變化；按最新行情估值，非成交額'})
    return (pd.DataFrame(rows, columns=cols).sort_values(
        ['同向ETF數','獨立投信數','期末價估算金額_億元'],
        ascending=[False,False,False],na_position='last').reset_index(drop=True)
            if rows else pd.DataFrame(columns=cols))


def v8_holding_streak(history, dates, etf, code):
    """Conservative observed positive holding streak; not actual purchase date."""
    if not dates or etf not in history[dates[-1]]:
        return None
    latest = history[dates[-1]][etf]
    if code not in latest.get('holdings', {}):
        return None
    start = dates[-1]
    for prev, curr in zip(reversed(dates[:-1]), reversed(dates[1:])):
        if not v8_good_pair(prev, curr, history, etf):
            break
        if code not in history[prev][etf].get('holdings', {}):
            break
        start = prev
    return (dates[-1] - start).days + 1, start


def v8_long_holding(history):
    dates = v8_periods(history)
    cols = ['股票代號','股票名稱','目前持有ETF數','持有滿90日_可驗證ETF數',
            '持有滿180日_可驗證ETF數','持有滿365日_可驗證ETF數',
            '最長連續觀測持有_日','ETF清單','日期備註']
    if not dates:
        return pd.DataFrame(columns=cols)
    last = history[dates[-1]]
    stockmap = defaultdict(lambda: {'name':'','days':[], 'etfs':set()})
    for etf, fund in last.items():
        for code,h in fund['holdings'].items():
            outcome = v8_holding_streak(history, dates, etf, code)
            if outcome is None:
                continue
            days, start = outcome
            bucket = stockmap[code]
            bucket['name'] = h.get('name') or bucket['name']
            bucket['days'].append(days)
            bucket['etfs'].add(etf)
    rows=[]
    period_span = (dates[-1] - dates[0]).days + 1
    for code,s in stockmap.items():
        days=s['days']
        rows.append({'股票代號':code,'股票名稱':s['name'],'目前持有ETF數':len(s['etfs']),
                     '持有滿90日_可驗證ETF數':sum(x>=90 for x in days) if period_span>=90 else None,
                     '持有滿180日_可驗證ETF數':sum(x>=180 for x in days) if period_span>=180 else None,
                     '持有滿365日_可驗證ETF數':sum(x>=365 for x in days) if period_span>=365 else None,
                     '最長連續觀測持有_日':max(days),
                     'ETF清單':'、'.join(sorted(s['etfs'])),
                     '日期備註':'至少已觀察到上述天數；真正初次買入日可能更早'})
    return pd.DataFrame(rows,columns=cols).sort_values(
        ['持有滿365日_可驗證ETF數','持有滿180日_可驗證ETF數','持有滿90日_可驗證ETF數',
         '最長連續觀測持有_日','目前持有ETF數'],
        ascending=[False]*5, na_position='last').reset_index(drop=True)


def v8_enrich_tables(tables, market):
    """Populate market context on all leaderboards; never invent missing quotes."""
    fields=['最新收盤價_元','實收資本額_億元','估算總市值_億元','收盤資料日']
    for name,df in tables.items():
        for field in fields:
            if field not in df.columns:
                if '股票代號' in df.columns and len(df):
                    df[field]=[market.get(str(code),{}).get(field) for code in df['股票代號']]
                else:
                    df[field]=pd.Series(dtype='object')
    return tables


def v8_table(df, cols, max_rows=500):
    show = df.head(max_rows).copy()
    head = ''.join('<th>'+escape(c.replace('_',' '))+'</th>' for c in cols)
    body = []
    for _,r in show.iterrows():
        cells=[]
        for c in cols:
            x=r.get(c)
            value = '—' if x is None or pd.isna(x) or x=='' else (f'{x:,.2f}' if isinstance(x,float) else str(x))
            cells.append('<td>'+escape(value)+'</td>')
        body.append('<tr>'+''.join(cells)+'</tr>')
    return '<div class="v8-scroll"><table class="v8-table"><thead><tr>'+head+'</tr></thead><tbody>'+''.join(body)+'</tbody></table></div>'


def v8_render_site(ranked, tables, history, archive_meta, market):
    """Enhance V7.1 HTML without removing its historical current-day ETF leaderboard."""
    path = SITE / 'index.html'
    if not path.exists():
        raise RuntimeError('V7.1 網站不存在；停止 V8 網頁更新')
    soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
    old = soup.select_one('.tablewrap')
    if old is None:
        raise RuntimeError('找不到舊排行榜；不覆蓋網頁')
    for tag in soup.select('.v8-suite'):
        tag.decompose()
    dates = v8_periods(history)
    count = len(dates)
    last = str(dates[-1]) if dates else '無'
    current_etfs = len(history[dates[-1]]) if dates else 0
    tabs = [
        ('present','目前持股','v8_current.csv',['股票代號','股票名稱','持有ETF數','投信數','ETF清單','目前持股日','最新收盤價_元','實收資本額_億元','估算總市值_億元']),
        ('new','首次建倉','v8_new.csv',['股票代號','股票名稱','近20期新出現ETF數','獨立投信數','新增ETF清單','新出現股數_張','最新價估值_億元','最後首次觀察日','最新收盤價_元','實收資本額_億元']),
        ('add5','加碼 5 期','v8_add_5.csv',['股票代號','股票名稱','同向ETF數','獨立投信數','累計校正股數_張','期末價估算金額_億元','ETF清單','最新收盤價_元','實收資本額_億元']),
        ('add20','加碼 20 期','v8_add_20.csv',['股票代號','股票名稱','同向ETF數','獨立投信數','累計校正股數_張','期末價估算金額_億元','ETF清單','最新收盤價_元','實收資本額_億元']),
        ('add60','加碼 60 期','v8_add_60.csv',['股票代號','股票名稱','同向ETF數','獨立投信數','累計校正股數_張','期末價估算金額_億元','ETF清單','最新收盤價_元','實收資本額_億元']),
        ('long','長期持有','v8_long.csv',['股票代號','股票名稱','目前持有ETF數','最長連續觀測持有_日','持有滿90日_可驗證ETF數','持有滿180日_可驗證ETF數','持有滿365日_可驗證ETF數','ETF清單','最新收盤價_元','實收資本額_億元']),
        ('exit5','撤退 5 期','v8_exit_5.csv',['股票代號','股票名稱','同向ETF數','獨立投信數','累計校正股數_張','期末價估算金額_億元','ETF清單','最新收盤價_元','實收資本額_億元']),
        ('exit20','撤退 20 期','v8_exit_20.csv',['股票代號','股票名稱','同向ETF數','獨立投信數','累計校正股數_張','期末價估算金額_億元','ETF清單','最新收盤價_元','實收資本額_億元']),
        ('exit60','撤退 60 期','v8_exit_60.csv',['股票代號','股票名稱','同向ETF數','獨立投信數','累計校正股數_張','期末價估算金額_億元','ETF清單','最新收盤價_元','實收資本額_億元']),
    ]
    buttons = ''.join(f'<button type="button" class="v8-tab{(" selected" if i==0 else "")}" data-tab="{key}">{escape(title)}</button>'
                      for i,(key,title,_,_) in enumerate(tabs))
    panels=[]
    for i,(key,title,csv,cols) in enumerate(tabs):
        data=tables[csv]
        if data.empty:
            message = ('尚未累積足夠的連續有效歷史，這不是零買入／零賣出。'
                       if key!='present' else '目前沒有通過品質檢查的完整ETF持股快照。')
            content='<p class="v8-hint">'+message+'</p>'
        else:
            content=v8_table(data,cols)
        panels.append(f'<section class="v8-panel" data-panel="{key}" style="display:{"block" if i==0 else "none"}">'
                      f'<div class="v8-panelhead"><strong>{escape(title)}（{len(data)} 檔）</strong>'
                      f'<a href="{csv}" download>下載 CSV</a></div>{content}</section>')
    warning = (f'有效持股快照日期：{last}；已累積 {count} 個觀測日期，最新日期有 {current_etfs} 檔主動ETF。'
               f'新儲存快照 {archive_meta.get("created",0)} 個ETF/日期。'
               '完整 5／20／60 期排行榜分別需要至少 6／21／61 個有效觀測日期，'
               '而且要有每期連續完整的相同ETF資料。')
    section = f'''<section class="v8-suite"><style>
.v8-suite{{margin:20px 0 26px;padding:18px;background:#fff;border:1px solid #cbd5e1;border-radius:14px}}
.v8-suite h2{{margin:0 0 8px}} .v8-hint{{color:#64748b;line-height:1.7}}
.v8-tabs{{display:flex;gap:7px;flex-wrap:wrap;margin:14px 0}}
.v8-tab{{background:#eef2f7;border:1px solid #cbd5e1;border-radius:8px;padding:9px 12px;cursor:pointer;color:#25364c}}
.v8-tab.selected{{background:#173456;color:white}} .v8-scroll{{overflow:auto;max-height:630px}}
.v8-table{{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap}}
.v8-table th{{background:#294668;color:white;position:sticky;top:0}}
.v8-table th,.v8-table td{{padding:9px 10px;border-bottom:1px solid #e5e7eb;text-align:left}}
.v8-panelhead{{display:flex;justify-content:space-between;align-items:center;margin:14px 0;gap:10px}}
.v8-search{{padding:10px;border:1px solid #cbd5e1;border-radius:8px;width:min(300px,90%)}}
</style><h2>ETF 機構持股雷達 V8</h2>
<p class="v8-hint">{escape(warning)}</p><p class="v8-hint">持股天數是<strong>至少觀測連續持有天數</strong>，不是實際買入日。
缺少整份ETF快照不等於清倉；股數變化可能源於基金申贖或公司行動，不能當作逐筆下單。
資料來源目前僅涵蓋可取得的主動式ETF；被動指數公告沿用下方V7榜。</p>
<input class="v8-search" type="search" id="v8Search" placeholder="搜尋股票名稱或代號…" aria-label="搜尋股票">
<div class="v8-tabs">{buttons}</div>{''.join(panels)}
<script>(function(){{
const root=document.querySelector('.v8-suite');if(!root)return;
const buttons=[...root.querySelectorAll('.v8-tab')],panels=[...root.querySelectorAll('.v8-panel')];
let active='present';function apply(){{let q=(root.querySelector('#v8Search').value||'').trim().toLowerCase();
root.querySelectorAll('.v8-panel').forEach(p=>p.querySelectorAll('tbody tr').forEach(row=>{{row.style.display=row.textContent.toLowerCase().includes(q)?'':'none'}}));}}
buttons.forEach(b=>b.addEventListener('click',()=>{{active=b.dataset.tab;buttons.forEach(x=>x.classList.toggle('selected',x===b));
panels.forEach(p=>p.style.display=p.dataset.panel===active?'block':'none');apply();}}));
root.querySelector('#v8Search').addEventListener('input',apply);
}})();</script></section>'''
    old.insert_before(BeautifulSoup(section, 'html.parser'))
    old.insert_before(BeautifulSoup('<h2 style="margin:24px 0 12px">原 V7：最新 ETF 買進共識（含官方行情）</h2>', 'html.parser'))
    path.write_text(str(soup),encoding='utf-8')


def v8_update_site(source, ranked, market):
    now=datetime.now(TZ)
    info=v8_archive_current(source,now.date())
    history=v8_load_history()
    dates=v8_periods(history)
    events=v8_daily_events(history)
    tables={
      'v8_current.csv':v8_current_holdings(history),
      'v8_new.csv':v8_new_entries(events,history,market),
      'v8_long.csv':v8_long_holding(history),
    }
    for n in (5,20,60):
        tables[f'v8_add_{n}.csv']=v8_period_ranking(events,history,n,'增加',market)
        tables[f'v8_exit_{n}.csv']=v8_period_ranking(events,history,n,'減少',market)
    v8_enrich_tables(tables,market or {})
    for fn,df in tables.items():
        df.to_csv(SITE/fn,index=False,encoding='utf-8-sig')
    pd.DataFrame(events,columns=['日期','ETF代號','投信','股票代號','股票名稱','動作','校正股數變化']).to_csv(
        SITE/'v8_event_detail.csv',index=False,encoding='utf-8-sig')
    v8_render_site(ranked,tables,history,info,market)
    status_file=SITE/'status.json'
    status=json.loads(status_file.read_text(encoding='utf-8')) if status_file.exists() else {}
    status['v8']={'latest_holdings_date':str(dates[-1]) if dates else None,
                  'observed_dates':len(dates),'active_etf_snapshot_count':info['valid'],
                  'new_snapshots':info['created'],
                  'new_entry_codes':len(tables['v8_new.csv']),
                  'current_stock_codes':len(tables['v8_current.csv']),
                  '5_20_60_periods_ready':[len(dates)>=n+1 for n in (5,20,60)],
                  'partial_coverage':True,'source_errors':info['problems']}
    status_file.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    result_zip=SITE/'results.zip'
    if result_zip.exists():
        old_zip=SITE/'results_old_v7.zip'
        result_zip.rename(old_zip)
        try:
            with zipfile.ZipFile(result_zip,'w',compression=zipfile.ZIP_DEFLATED) as z:
                with zipfile.ZipFile(old_zip) as old:
                    for filename in old.namelist():
                        z.writestr(filename,old.read(filename))
                for filename in list(V8_CSV)+['v8_event_detail.csv']:
                    z.write(SITE/filename,arcname='V8_'+filename)
        finally:
            old_zip.unlink(missing_ok=True)
    print(f'[V8] 持股快照 {len(dates)} 個觀測日，最新 {dates[-1] if dates else "無"}；'
          f'本次新增 {info["created"]} 個ETF快照；現持股 {len(tables["v8_current.csv"])} 檔股票。')
    print('[V8] 5/20/60 期資料未滿時會明示不足，不會把不存在的歷史當0。')
    return tables


def main():
    import ETF_Buy_Radar_V6 as radar
    captured = {'payload': None}
    original = radar.src_active
    def retain_active_json():
        data = original()
        captured['payload'] = data
        return data
    radar.src_active = retain_active_json
    try:
        ranked, active, upcoming = radar.run_all()
    finally:
        radar.src_active = original
    _, market = build_site(radar, ranked, active, upcoming)
    # Keep original V7.1 ranking. Archive and enhance without re-downloading quotes.
    v8_update_site(captured['payload'], ranked, market)


if __name__ == '__main__':
    main()
