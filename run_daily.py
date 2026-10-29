# -*- coding: utf-8 -*-
"""ETF 共識雷達 V7.1：原有排行 + 臺灣上市／上櫃最新官方收盤行情。

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
    return status


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


def main():
    import ETF_Buy_Radar_V6 as radar
    ranked, active, upcoming = radar.run_all()
    build_site(radar, ranked, active, upcoming)


if __name__ == '__main__':
    main()
