# -*- coding: utf-8 -*-
"""Run V6 once and publish verifiable, timestamped results as a static website.

Designed for GitHub Actions. No user credentials or paid market-data key needed.
This program DOES NOT claim fund managers' future orders are publicly knowable.
"""
from __future__ import annotations
import json
import shutil
import sys
from datetime import datetime, date
from html import escape
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo
import pandas as pd

BASE = Path(__file__).resolve().parent
SITE = BASE / 'site'
OUT = BASE / 'ETF_Buy_Radar_V6_output'
TAIPEI = ZoneInfo('Asia/Taipei')


def build_site(radar, ranked: pd.DataFrame, active: pd.DataFrame, upcoming: pd.DataFrame):
    """Run only on sufficient real source data; don't silently replace with empty results."""
    if ranked is None or ranked.empty:
        raise RuntimeError('無可驗證的 ETF 買進共識排名；保留上次有效網站，不發布空白榜單')
    if active.empty and upcoming.empty:
        raise RuntimeError('主動資料與被動未生效公告均無可用事件；保留原網站')
    src = OUT / 'ETF_一頁式排名.html'
    if not src.exists():
        raise RuntimeError(f'V6 未產出 HTML：{src}')
    if '排名' not in ranked.columns or '股票代號' not in ranked.columns:
        raise RuntimeError('排名欄位不完整，拒絕發布')

    run_time = datetime.now(TAIPEI)
    today_str = run_time.date().isoformat()
    active_event_day = ''
    if not active.empty and '後持股日' in active.columns:
        d = active['後持股日'].dropna().astype(str)
        if len(d):
            active_event_day = max(d)
            if date.fromisoformat(active_event_day) > run_time.date():
                raise RuntimeError('持股事件日期晚於臺灣今日，拒絕發布')
    active_event_etfs = int(active['ETF代號'].nunique()) if not active.empty else 0
    passive_notice_etfs = int(upcoming['ETF代號'].nunique()) if not upcoming.empty else 0
    passive_missing_file = OUT / '05_官方公告但股票名單未核對.csv'
    unresolved = len(pd.read_csv(passive_missing_file)) if passive_missing_file.exists() else None

    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / 'history').mkdir(exist_ok=True)
    # Never publish fake synthetic fixtures. These files come solely from the live run.
    copies = {
        'latest.csv': '01_ETF買進共識排名.csv',
        'active_detail.csv': '02_主動ETF逐檔股數變化.csv',
        'passive_upcoming.csv': '03_被動ETF公告尚未生效.csv',
        'unverified_passive.csv': '05_官方公告但股票名單未核對.csv',
        'source_audit.csv': '06_資料來源紀錄.csv',
        'source_errors.csv': '07_抓取錯誤.csv',
    }
    for webname, filename in copies.items():
        srcfile = OUT / filename
        if not srcfile.exists():
            raise RuntimeError(f'輸出檔缺少：{srcfile}')
        shutil.copy2(srcfile, SITE / webname)

    # The complete original V6 CSV package, including explanatory limitations.
    zipfile = BASE / 'ETF_Buy_Radar_V6_結果.zip'
    if not zipfile.exists():
        raise RuntimeError('缺少完整 V6 ZIP 結果檔')
    shutil.copy2(zipfile, SITE / 'results.zip')
    shutil.copy2(SITE / 'latest.csv', SITE / 'history' / f'{today_str}.csv')

    status = {
        'run_time_taipei': run_time.isoformat(timespec='seconds'),
        'last_active_event_date': active_event_day,
        'ranked_stocks': len(ranked),
        'active_etfs_with_valid_change_events': active_event_etfs,
        'passive_etfs_with_unexpired_announced_additions': passive_notice_etfs,
        'passive_event_rows_unexpired': len(upcoming),
        'unverified_index_announcements': unresolved,
        'scope': 'partial market coverage; public information only',
        'risk_note': 'Active future two-day value is a hypothetical continuation scenario, not a manager order.',
    }
    (SITE / 'status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')

    html = src.read_text(encoding='utf-8')
    summary = (
        '<div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;'
        'padding:13px 16px;margin:12px 0;line-height:1.7">'
        '<strong>每日自動檢查</strong>：'
        f'{escape(run_time.strftime("%Y/%m/%d %H:%M"))}（台灣）'
        f'｜<strong>最新主動持股異動日</strong>：{escape(active_event_day or "無有效事件")}'
        f'｜<strong>有效異動 ETF 數</strong>：{active_event_etfs}'
        f'｜<strong>未生效被動公告新增 ETF 數</strong>：{passive_notice_etfs}'
        f'｜<strong>待補公告</strong>：{escape(str(unresolved) if unresolved is not None else "未知")}'
        '<div style="font-size:12px;color:#475569">來源覆蓋不完整；「沒有抓到」不能解讀成沒有其他 ETF 買盤。'
        '預估續買金額是假設情境，不代表確定買入。</div></div>'
    )
    links = (
        '<div style="margin:14px 0;display:flex;gap:12px;flex-wrap:wrap">'
        '<a href="latest.csv" download>下載最新排名 CSV</a>'
        '<a href="active_detail.csv" download>主動 ETF 明細</a>'
        '<a href="passive_upcoming.csv" download>被動待生效公告</a>'
        '<a href="results.zip" download>下載完整結果 ZIP</a>'
        '<a href="status.json">檢視資料更新狀態</a>'
        '</div>'
    )
    anchor = '<div class="tip">'
    if anchor not in html:
        raise RuntimeError('V6 HTML 格式已變，找不到提示區域，拒絕生成有歧義的網站')
    html = html.replace(anchor, summary + links + anchor, 1)
    html = html.replace('主動可比ETF數：', '主動近期資料ETF數：', 1)
    (SITE / 'index.html').write_text(html, encoding='utf-8')
    print('網站已生成：', SITE / 'index.html')
    print('完整排名：', len(ranked), '檔股票｜主動事件日：', active_event_day,
          '｜有效異動ETF：', active_event_etfs, '｜未確認指數公告：', unresolved)
    return status


def main():
    import ETF_Buy_Radar_V6 as radar
    ranked, active, upcoming = radar.run_all()
    build_site(radar, ranked, active, upcoming)


if __name__ == '__main__':
    main()
