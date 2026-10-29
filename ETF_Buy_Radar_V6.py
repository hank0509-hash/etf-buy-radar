# -*- coding: utf-8 -*-
"""ETF 買進共識雷達 V6.0  (Google Colab 單檔一鍵執行)

主動：公開持股股數異動 => 估算近期持股增加金額 => 未來兩個交易日續買情境。
被動：官方指數公告＋可核對新聞 => 已公告尚未生效的新增股票。
不把公告新增當成 ETF 已下單，不把公開持股變化當成交明細。
來源網站不完整時一律標示缺口；沒有公開目標權重時不填被動預估金額。
"""
# -*- coding: utf-8 -*-
"""被動式公告擷取：官方 HTML + 媒體全文。

資料分級：
  A：官方 HTML 明載股票納入/刪除（目前包括富時臺灣系列新聞）
  B：官方 HTML 可確認 ETF 追蹤指數/生效日期，股票明細來自新聞全文轉載
不把 B 說成原始 PDF 已查核，也不把指數新增說成 ETF 已經下單。
使用：Google Colab 一格執行；pip install requests beautifulsoup4 pandas
"""
import re, io, os, json, time, zipfile, unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, quote
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import requests
import pandas as pd
from bs4 import BeautifulSoup

BASE = 'https://taiwanindex.com.tw'
NOW = datetime.now(ZoneInfo('Asia/Taipei'))
TODAY = NOW.date()
ROOT = Path('/content') if Path('/content').exists() else Path.cwd()
OUTPUT = ROOT / 'ETF_共識雷達_V3_HTML優先'
OUTPUT.mkdir(parents=True, exist_ok=True)
TIMEOUT=9
LOOKBACK_DAYS=45
DISCOVER_RSS=True

# 這些網址是已確認存在的公開文章，而非手動預填股票結果。
# 有新公告時可把新聞連結加到 EXTRA_ARTICLE_URLS，不須碰到 PDF。
SEED_ARTICLES = [
 'https://m.moneydj.com/f1a.aspx?a=2291399C-4833-4ECA-99D3-FBCAFC5EABD5', # 00881
 'https://m.moneydj.com/f1a.aspx?a=F3C5CD7A-26AA-40E4-915C-12B7F4BE1B58', # 00935
 'https://m.moneydj.com/f1a.aspx?a=21A3E0D5-EC5C-455D-8CCE-DCB2D1D71087', # 00905
 'https://www.moneydj.com/KMDJ/News/NewsViewer.aspx?a=1009060a-f579-4205-877a-108008c4ab99', # 00733
 'https://www.moneydj.com/kmdj/news/newsviewer.aspx?a=fab2ee37-c18c-4841-8fc7-5b10a0f9c423', # 00912 無異動
]
EXTRA_ARTICLE_URLS=[]  # 可填入新的文章 URL；只解析實際下載成功的內容
OFFICIAL_NEWS=[456,454,452,450,449,448,447,446,445,444,443,442]

# 舊官方公告可核對的對應表；若官方新聞網站可用，當次以官方 HTML 動態對照為主。
# 此表非「所有 ETF」完整清單，僅做首頁無法連線時的部分備援。
STATIC_INDEX_MAP={
 '特選臺灣上市上櫃FactSet科技龍頭通訊指數':[('00881','國泰台灣科技龍頭')],
 '特選臺灣上市上櫃FactSet創新科技50指數':[('00935','野村臺灣新科技50')],
 '特選Smart多因子指數':[('00905','FT臺灣Smart')],
 '特選臺灣上市上櫃智慧50指數':[('00912','中信臺灣智慧50')],
 '中小型A級動能50指數':[('00733','富邦臺灣中小50')],
 '特選臺灣ESG低碳50指數':[('00923','群益台ESG低碳50')],
 '特選臺灣上市上櫃ESG低碳高息40指數':[('00930','永豐ESG低碳高息')],
 '特選臺灣上市上櫃永續高息中小型股指數':[('00936','台新永續高息中小')],
 '特選臺灣上市上櫃高息動能指數':[('00939','統一台灣高息動能')],
 '臺灣全市場半導體精選30指數':[('00904','台新臺灣半導體30')],
 '特選臺灣上市上櫃精選高息指數':[('00919','群益台灣精選高息')],
 '特選臺灣上市上櫃科技優息指數':[('00929','復華台灣科技優息')],
 '特選臺灣上市上櫃優選成長高股息指數':[('00934','中信成長高股息')],
 '台灣上市上櫃旗艦動能50指數':[('009802','富邦旗艦50')],
}
FTSE_MAP={
 '臺灣50':[('0050','元大台灣50'),('006208','富邦台50')],
 '臺灣中型100':[('0051','元大中型100')],
 '臺灣資訊科技':[('0052','富邦科技')],
 '臺灣高股息':[('0056','元大高股息')],
}
HEADERS={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36','Accept-Language':'zh-TW,zh;q=0.9,en;q=0.7'}
SESSION=requests.Session();SESSION.headers.update(HEADERS)
AUDIT=[];ERR=[]
COLS=['股票代號','股票名稱','方向','ETF代號','ETF名稱','官方公告日','官方生效日','資料公開日','公開時序','證據等級','指數名稱','股票資訊來源','官方新聞頁']


def normalize(s):
    t=unicodedata.normalize('NFKC',str(s or '')).replace('臺','台')
    return re.sub(r'[\s「」『』()（）\-－、，:,：]+','',t).replace('台灣指數公司','')


def http_get(url,content_type='HTML'):
    try:
        resp=SESSION.get(url,timeout=TIMEOUT)
        if resp.status_code!=200:
            raise RuntimeError('HTTP '+str(resp.status_code))
        text=resp.text
        if '<html' not in text[:1500].lower() and content_type=='HTML':
            if not text.strip():raise RuntimeError('回傳空白')
        AUDIT.append({'來源':url,'種類':content_type,'狀態':'下載成功','內容字數':len(text),'原因':''})
        return text
    except Exception as exc:
        err=type(exc).__name__+': '+str(exc)[:150]
        ERR.append({'來源':url,'種類':content_type,'錯誤':err})
        AUDIT.append({'來源':url,'種類':content_type,'狀態':'失敗','內容字數':0,'原因':err})
        return ''


def dates_in(s):
    s=unicodedata.normalize('NFKC',s).replace(' ','')
    for pattern in [r'(20\d\d)年(\d{1,2})月(\d{1,2})日',r'(20\d\d)/(\d{1,2})/(\d{1,2})']:
        for m in re.finditer(pattern,s):
            try:yield date(*map(int,m.groups())).isoformat()
            except ValueError:pass


def effective_date(s):
    s=unicodedata.normalize('NFKC',s)
    s=re.sub(r'\s+','',s)
    pats=[r'亦即自(20\d{2})年(\d{1,2})月(\d{1,2})日.{0,25}?起生效',
          r'即自(20\d{2})年(\d{1,2})月(\d{1,2})日.{0,25}?起生效',
          r'自(20\d{2})年(\d{1,2})月(\d{1,2})日.{0,55}?起生效',
          r'生效日[：:](20\d{2})/(\d{1,2})/(\d{1,2})']
    for pat in pats:
        m=re.search(pat,s)
        if m:
            try:return date(*map(int,m.groups())).isoformat()
            except ValueError:pass
    return ''


def article_pub(s):
    # 日期需要帶年份。10/20起是標題日期，不能誤認為新聞發布時間。
    m=re.search(r'(20\d{2})[/-](\d{1,2})[/-](\d{1,2})\s+(\d{2}):(\d{2})',s[:3500])
    if m:
        try:return date(*map(int,m.groups()[:3])).isoformat()
        except ValueError:pass
    return ''


def extract_explicit_change_list(s):
    # 僅接受標示「成分股納入/刪除」且具有 4 碼股票代號的名單。
    t=unicodedata.normalize('NFKC',s)
    labels=list(re.finditer(r'成分股\s*(納入|刪除)\s*(?:\(\s*\d+\s*\))?\s*[：:]\s*',t))
    rows=[]; declared=[]
    for i,m in enumerate(labels):
        tail=t[m.end():labels[i+1].start() if i+1<len(labels) else len(t)]
        tail=re.split(r'\*\s*註|候補名單|如欲|資料來源|免責|推薦新聞|個股K線圖',tail,maxsplit=1)[0]
        if len(tail)>10000:tail=tail[:10000]
        parsed=[]
        for z in re.finditer(r'(?<!\d)(\d{4})(?!\d)\s*([\u3400-\u9fffA-Za-z*＊\-－·]{1,24})',tail):
            code,name=z.groups();name=name.strip('、，。,;:：. ')
            if name:parsed.append((code,name))
        for z in re.finditer(r'([\u3400-\u9fffA-Za-z*＊\-－·]{1,24})\s*\((\d{4})\)',tail):
            parsed.append((z.group(2),z.group(1)))
        parsed=list(dict.fromkeys(parsed))
        rows.extend([('新增' if m.group(1)=='納入' else '刪除',c,n) for c,n in parsed])
        declared.append((m.group(1),len(parsed)))
    return list(dict.fromkeys(rows)),declared


def discover_official():
    # 從官方網站 HTML 的表格確認指數與 ETF 的關係，不下載 PDF。
    mapping={normalize(k):[(code,name,'歷史官方對照') for code,name in vals]
             for k,vals in STATIC_INDEX_MAP.items()}
    pages={}
    # 自動讀取官方新聞列表，不只停留在 2026/10/19 的舊編號。
    news_ids=set(OFFICIAL_NEWS)
    for listing in (BASE+'/media_and_events', BASE+'/news'):
        html=http_get(listing,'TIP官方新聞索引')
        if not html:continue
        for a in BeautifulSoup(html,'html.parser').find_all('a',href=True):
            href=urljoin(listing,a.get('href',''))
            hit=re.search(r'/news/(\d+)(?:$|[?#/])',href)
            if hit:news_ids.add(int(hit.group(1)))
    # 限制每次取得的新聞量，新的官方公告優先；仍保留 FTSE 450 歷史驗收。
    order=sorted(news_ids,reverse=True)
    if 450 not in order[:45]:order=order[:44]+[450]
    else:order=order[:45]
    # 逐個官方新聞頁抓取；任何一頁失敗不影響其他頁
    for num in order:
        url=f'{BASE}/news/{num}'
        html=http_get(url,'TIP官方HTML')
        if not html:continue
        soup=BeautifulSoup(html,'html.parser');txt=soup.get_text('\n',strip=True)
        pages[url]=(txt,soup)
        for tr in soup.find_all('tr'):
            vals=[td.get_text(' ',strip=True) for td in tr.find_all(['td','th'])]
            if len(vals)<3:continue
            codes=re.findall(r'(?<!\d)(0\d{3,5}[A-Z]?)(?!\w)',vals[2])
            etfs=[c for c in codes if not c.startswith('02')]
            if not etfs:continue
            ix=normalize(vals[0]);name=vals[1]
            if '指數' not in vals[0]:continue
            current=mapping.get(ix,[])
            known={c for c,_,_ in current}
            for code in etfs:
                if code not in known:
                    current.append((code,name,'即時官方HTML'))
                    known.add(code)
                else:
                    current=[(c,n,'即時官方HTML' if c==code else q) for c,n,q in current]
            mapping[ix]=current
    return mapping,pages


def detect_index(s,mapping):
    head=s[:2200]
    # 優先抓新聞第一個「....指數」引號標題。
    candidates=re.findall(r'「([^」]{4,110}?指數)」',head)
    hits=[]
    for candidate in candidates:
        c=normalize(candidate)
        for key,etfs in mapping.items():
            if c==key or (len(c)>=12 and len(key)>=12 and (c in key or key in c)):
                hits.append((key,etfs))
        if hits:break
    # fallback 僅能在完整名稱唯一命中時使用，不用單憑幾個詞臆測ETF。
    if not hits:
        n=normalize(head)
        hits=[(key,etfs) for key,etfs in mapping.items() if len(key)>=18 and key in n]
    hits=list({k:(k,es) for k,es in hits}.values())
    return hits[0] if len(hits)==1 else ('',[])


def rss_discovery():
    if not DISCOVER_RSS:return []
    queries=['site:m.moneydj.com/f1a.aspx "成分股納入" "指數"',
             'site:moneydj.com/KMDJ/News/NewsViewer.aspx "成分股納入"',
             '"臺灣指數公司" "成分股審核結果" "ETF"']
    urls=[]
    for q in queries:
        feed='https://www.bing.com/search?format=rss&q='+quote(q)
        xml=http_get(feed,'RSS探索')
        if not xml:continue
        try:
            root=ET.fromstring(xml)
            for item in root.findall('.//item')[:35]:
                link=item.findtext('link') or ''
                domain=link.lower()
                if any(x in domain for x in ('moneydj.com','taiwanindex.com.tw','cna.com.tw')):
                    urls.append(link)
        except Exception as exc:
            ERR.append({'來源':feed,'種類':'RSS解析','錯誤':str(exc)[:120]})
    return list(dict.fromkeys(urls))[:65]


def published_timing(published,effective):
    if not published or not effective:return '日期不完整'
    if published>effective:return '資訊晚於生效日'
    if published==effective:return '生效當日（買盤未必尚存）'
    return f'距生效日 {(date.fromisoformat(effective)-date.fromisoformat(published)).days} 天'


def collect_media(urls,mapping,pages):
    out=[]
    for i,url in enumerate(urls,1):
        html=http_get(url,'媒體報導HTML')
        if not html:continue
        soup=BeautifulSoup(html,'html.parser')
        # 全文中會包含導覽文字，擷取到新聞標題後的主段可減少雜訊。
        content=soup.get_text('\n',strip=True)
        idx,matched=detect_index(content,mapping)
        if not idx or not matched:
            AUDIT.append({'來源':url,'種類':'ETF對照','狀態':'未能唯一配對指數','內容字數':len(content),'原因':''})
            continue
        rows,declared=extract_explicit_change_list(content)
        if not rows:
            AUDIT.append({'來源':url,'種類':'股票名單','狀態':'沒有可驗證的4碼納入名單','內容字數':len(content),'原因':''})
            continue
        eff=effective_date(content); pub=article_pub(content)
        if not (eff and pub):
            AUDIT.append({'來源':url,'種類':'日期稽核','狀態':'日期不足未計入','內容字數':len(content),'原因':f'新聞日期:{pub},生效:{eff}'})
            continue
        # 官方公告日取已抓到的 TIP 新聞網頁，不用新聞回顧的發布日假裝是官方預告日。
        official_date='';off_url='';official_eff=''
        for pageurl,(page_text,_) in pages.items():
            if idx in normalize(page_text):
                pd_=article_pub(page_text);ed_=effective_date(page_text)
                if pd_ and ed_==eff:
                    official_date=pd_;official_eff=ed_;off_url=pageurl;break
        if not official_date:
            # 無法獨立查證同一次官方公告日期時，用新聞日期保守表示「至少於當日可見」。
            official_date=''
        timing=published_timing(pub,eff)
        for action,code,name in rows:
            for etf,etfname,provenance in matched:
                out.append(dict(zip(COLS,[code,name,action,etf,etfname,official_date,eff,pub,timing,
                     'B：媒體全文＋ETF官方對照('+provenance+')',idx,url,off_url])))
        print(f'新聞 {i}/{len(urls)}：{len(rows)} 檔異動，ETF={"、".join(x[0] for x in matched)}，{url[:65]}')
    return out


def parse_official_ftse(pages):
    url=f'{BASE}/news/450'
    if url not in pages:return []
    content,_=pages[url]
    pub=article_pub(content);eff=effective_date(content)
    if not pub or not eff:return []
    # 官網以「臺灣50指數／臺灣中型100指數」等分段，股票以 公司(四碼) 表示。
    t=unicodedata.normalize('NFKC',content)
    headings=list(re.finditer(r'(臺灣50|臺灣中型100|臺灣資訊科技|臺灣發達|臺灣高股息)指數',t))
    seen=set();out=[]
    for j,h in enumerate(headings):
        idx=h.group(1)
        if idx in seen or idx not in FTSE_MAP:continue
        seg=t[h.end():headings[j+1].start() if j+1<len(headings) else len(t)]
        if '成分股納入' not in seg:continue
        seen.add(idx)
        for action in ('納入','刪除'):
            m=re.search(r'成分股\s*'+action+r'\s*[：:]\s*(.*?)(?=成分股\s*(?:納入|刪除)\s*[：:]|候補名單|$)',seg,re.S)
            if not m:continue
            for name,code in re.findall(r'([\u3400-\u9fffA-Za-z*＊\-－·]{1,24})\s*\((\d{4})\)',m.group(1)):
                for etf,ename in FTSE_MAP[idx]:
                    out.append(dict(zip(COLS,[code,name,'新增' if action=='納入' else '刪除',etf,ename,pub,eff,pub,
                        published_timing(pub,eff),'A：官方HTML明列股票','富時'+idx,url,url])))
    return out


def aggregate_by_stock(df,upcoming):
    cols=['股票代號','股票名稱','新增ETF數','刪除ETF數','新增ETF','刪除ETF','公告日','生效日','證據等級','股票名單來源']
    if df.empty:return pd.DataFrame(columns=cols)
    x=df[df['官方生效日']>TODAY.isoformat()] if upcoming else df[(df['官方生效日']<=TODAY.isoformat())&(df['資料公開日']>=(TODAY-timedelta(days=LOOKBACK_DAYS)).isoformat())]
    rows=[]
    for code,g in x.groupby('股票代號'):
        b=g[g.方向=='新增'].drop_duplicates('ETF代號');s=g[g.方向=='刪除'].drop_duplicates('ETF代號')
        rows.append({'股票代號':code,'股票名稱':g.iloc[0]['股票名稱'],'新增ETF數':len(b),'刪除ETF數':len(s),
        '新增ETF':'、'.join(b.ETF代號+' '+b.ETF名稱),'刪除ETF':'、'.join(s.ETF代號+' '+s.ETF名稱),
        '公告日':'、'.join(sorted(set(g['官方公告日'].dropna())-{''})),
        '生效日':'、'.join(sorted(set(g['官方生效日']))),'證據等級':'、'.join(sorted(set(g['證據等級']))),
        '股票名單來源':' | '.join(sorted(set(g['股票資訊來源'])))})
    if not rows:return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows,columns=cols).sort_values(['新增ETF數','刪除ETF數','股票代號'],ascending=[False,True,True]).reset_index(drop=True)


def same_pub_day(df):
    """同一官方公告批次分組；沒有官方公告日才保守用新聞公開日。

    不把媒體公開日當成官方公告日。含來源最晚公開日，讓使用者知道訊號是否已過時。
    """
    fields=['公告分組日','最晚來源公開日','股票代號','股票名稱','新增ETF數','刪除ETF數','新增ETF','生效日','公開時序','證據等級']
    if df.empty:return pd.DataFrame(columns=fields)
    tmp=df.copy()
    tmp['公告分組日']=tmp['官方公告日'].where(tmp['官方公告日']!='',tmp['資料公開日'])
    result=[]
    for (day,code),g in tmp.groupby(['公告分組日','股票代號']):
        buys=g[g.方向=='新增'].drop_duplicates('ETF代號')
        sells=g[g.方向=='刪除'].drop_duplicates('ETF代號')
        result.append({'公告分組日':day,'最晚來源公開日':g['資料公開日'].max(),
          '股票代號':code,'股票名稱':g.iloc[0]['股票名稱'],
          '新增ETF數':len(buys),'刪除ETF數':len(sells),
          '新增ETF':'、'.join(buys.ETF代號+' '+buys.ETF名稱),
          '生效日':'、'.join(sorted(set(g['官方生效日']))),
          '公開時序':'、'.join(sorted(set(g['公開時序']))),
          '證據等級':'、'.join(sorted(set(g['證據等級'])))})
    return pd.DataFrame(result,columns=fields).sort_values(['新增ETF數','公告分組日'],ascending=[False,False]).reset_index(drop=True)


def tests():
    s='''10/20起「特選臺灣上市上櫃FactSet科技龍頭通訊指數」納聯亞等 2026/10/20 08:11\n成分股審核結果\n成分股納入和刪除之變動將自2026年10月19日交易結束後生效(亦即自2026年10月20日起生效)。\n成分股納入（4）:3081聯亞、3189景碩、6770力積電、8046南電\n成分股刪除（4）:3036文曄、3105穩懋、4938和碩、6515穎崴\n*註：資料'''
    assert effective_date(s)=='2026-10-20'
    assert article_pub(s)=='2026-10-20'
    changes,declared=extract_explicit_change_list(s)
    assert len(changes)==8,(changes,declared)
    assert ('新增','3189','景碩') in changes
    assert ('刪除','6515','穎崴') in changes
    idx,etfs=detect_index(s,{normalize(k):[(c,n,'歷史') for c,n in v] for k,v in STATIC_INDEX_MAP.items()})
    assert etfs[0][0]=='00881',(idx,etfs)
    assert published_timing('2026-10-20','2026-10-20').startswith('生效當日')
    print('自我檢查通過：股票、方向、ETF匹配、生效日、時間先後。')


# ====================== V6 主動式資料與單一排名 ======================
from collections import defaultdict
from html import escape

ACTIVE_SOURCE = 'https://raw.githubusercontent.com/nctuwanglin/active-etf/main/active.json'
RADAR_OUT = ROOT / 'ETF_Buy_Radar_V6_output'
RADAR_OUT.mkdir(parents=True, exist_ok=True)
MIN_ACTIVE_ETF = 1  # 多少檔主動ETF加碼都列，正式排序將多檔共識置前
MAX_ACTIVE_AGE_DAYS = 5
PASSIVE_DISCOVERY = True

RANK_FIELDS = ['排名','股票代號','股票名稱','訊號類型','未生效公告新增ETF數','公告獨立指數數',
               '主動加碼ETF數','主動加碼投信數','主動減碼ETF數','最近有效主動淨增持_億元',
               '未來2交易日續買情境_億元','續買情境下限_億元','續買情境上限_億元',
               '被動預估新增金額_億元','被動金額說明','受關注ETF清單','指數生效日','觀測最晚日期','來源狀態']

def num(x):
    try:return float(x)
    except (ValueError, TypeError):return None

def iso_day(s):
    try:return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):return None

def src_active():
    try:
        r=SESSION.get(ACTIVE_SOURCE,timeout=18)
        if r.status_code!=200:raise RuntimeError(f'HTTP {r.status_code}')
        j=r.json()
        if not isinstance(j.get('etfs'),dict) or not isinstance(j.get('stocks'),dict):
            raise ValueError('主動資料格式改變，找不到 etfs / stocks')
        AUDIT.append({'來源':ACTIVE_SOURCE,'種類':'主動ETF','狀態':'下載成功',
                      '內容字數':len(r.content),'原因':f"ETF紀錄 {len(j['etfs'])} 檔；股票行情 {len(j['stocks'])} 檔"})
        return j
    except Exception as e:
        ERR.append({'來源':ACTIVE_SOURCE,'種類':'主動ETF','錯誤':str(e)[:200]})
        print('[主動ETF失敗]',str(e)[:180],'；只計算可取得的被動公告')
        return None

def parse_active(j, today):
    fields=['股票代號','股票名稱','ETF代號','ETF名稱','投信','動作','前持股日','後持股日','股數校正變化',
            '同日股價','當期估算增減部位_億元','品質','可比較','價格日期','來源']
    if not j:return pd.DataFrame(columns=fields), {'ETF總數':0,'有效ETF數':0,'明細筆數':0,'不同觀測日':[]}
    rows=[]; status_dates=[]; valid_etf_count=0
    prices=j.get('stocks',{})
    for ticker,fund in j['etfs'].items():
        day=iso_day(fund.get('data_date'))
        if not day or (today-day).days<0 or (today-day).days>MAX_ACTIVE_AGE_DAYS:continue
        if fund.get('status') not in ('ok','partial',None):continue
        status_dates.append(str(day));valid_etf_count+=1
        for event in fund.get('events',[]):
            if not event.get('daily_comparable',False): continue
            if event.get('quality') not in ('ok','clean','good'):continue
            eday=iso_day(event.get('to_date'))
            if eday!=day:continue
            code=str(event.get('code','')).strip()
            if not re.fullmatch(r'\d{4}',code):continue
            delta=num(event.get('adjusted_shares_delta'))
            if delta is None or abs(delta)<1:continue
            kind='增加' if delta>0 else '減少'
            st=prices.get(code,{})
            price=num(st.get('close'))
            price_day=iso_day(st.get('quote_date'))
            # The quote must be from the same date as the holding change.
            use_price = price if price and price>0 and price_day==eday else None
            worth=round(delta*use_price/1e8,5) if use_price else None
            rows.append({'股票代號':code,'股票名稱':event.get('name') or st.get('name') or '',
                'ETF代號':ticker,'ETF名稱':fund.get('name',''),'投信':fund.get('issuer',''),
                '動作':kind,'前持股日':event.get('from_date',''),'後持股日':str(eday),
                '股數校正變化':delta,'同日股價':use_price,'當期估算增減部位_億元':worth,
                '品質':event.get('quality',''),'可比較':True,'價格日期':str(price_day) if price_day else '',
                '來源':ACTIVE_SOURCE})
    detail=pd.DataFrame(rows,columns=fields)
    # Do NOT combine stale funds and fresh funds into one pseudo-daily buying amount.
    # Preserve coverage information but rank on a single common latest event date.
    common_day=str(detail['後持股日'].max()) if len(detail) else ''
    if len(detail):
        detail=detail[detail['後持股日']==common_day].reset_index(drop=True)
    comparable_etfs=int(detail['ETF代號'].nunique()) if len(detail) else 0
    meta={'ETF總數':len(j['etfs']),'有效ETF數':valid_etf_count,'同日可比ETF數':comparable_etfs,
          '明細筆數':len(detail),'共同事件日':common_day,
          '不同觀測日':sorted(set(status_dates))}
    return detail,meta

def collect_passive():
    global DISCOVER_RSS
    DISCOVER_RSS = PASSIVE_DISCOVERY
    mapping,pages=discover_official()
    # Official readable FTSE HTML. Generalize for new quarterly announcements, not fixed 2026-09-04.
    official=parse_ftse_dynamic(pages)
    urls=list(dict.fromkeys(SEED_ARTICLES+EXTRA_ARTICLE_URLS+rss_discovery()))
    media=collect_media(urls,mapping,pages)
    df=pd.DataFrame(official+media,columns=COLS)
    if len(df):
        df['_is_official']=df['證據等級'].str.startswith('A：').astype(int)
        df=df.sort_values('_is_official',ascending=False).drop_duplicates(
              ['股票代號','ETF代號','方向','官方生效日']).drop(columns=['_is_official'])
    return df, mapping, pages

def parse_ftse_dynamic(pages):
    out=[]
    for url,(content,_) in pages.items():
        pub=article_pub(content);eff=effective_date(content)
        if not pub or not eff:continue
        t=unicodedata.normalize('NFKC',content)
        if '成分股納入' not in t:continue
        heads=list(re.finditer(r'(臺灣50|臺灣中型100|臺灣資訊科技|臺灣發達|臺灣高股息)指數',t))
        seen=set()
        for k,h in enumerate(heads):
            idx=h.group(1)
            if idx in seen or idx not in FTSE_MAP:continue
            seg=t[h.end():heads[k+1].start() if k+1<len(heads) else len(t)]
            if '成分股納入' not in seg:continue
            seen.add(idx)
            for action in ('納入','刪除'):
                match=re.search(r'成分股\s*'+action+r'\s*[：:]\s*(.*?)(?=成分股\s*(?:納入|刪除)\s*[：:]|候補名單|$)',seg,re.S)
                if not match:continue
                for name,code in re.findall(r'([\u3400-\u9fffA-Za-z*＊\-－·]{1,24})\s*\((\d{4})\)',match.group(1)):
                    for etf,ename in FTSE_MAP[idx]:
                        out.append(dict(zip(COLS,[code,name,'新增' if action=='納入' else '刪除',
                            etf,ename,pub,eff,pub,published_timing(pub,eff),
                            'A：官方HTML明列股票','富時'+idx,url,url])))
    return out

def summarize(active_detail, passive, today):
    active=defaultdict(lambda: {'inc_etfs':set(),'inc_issuers':set(),'dec_etfs':set(),
                                 'inc_amt':0.,'dec_amt':0.,'unknown_amount':False,
                                 'names':[],'dates':[]})
    for _,r in active_detail.iterrows():
        b=active[r['股票代號']]
        b['names'].append(str(r['股票名稱']))
        b['dates'].append(str(r['後持股日']))
        amount=r['當期估算增減部位_億元']
        if r['動作']=='增加':
            b['inc_etfs'].add(r['ETF代號']);b['inc_issuers'].add(str(r['投信']) or r['ETF代號'])
            if pd.notna(amount):b['inc_amt']+=float(amount)
            else:b['unknown_amount']=True
        else:
            b['dec_etfs'].add(r['ETF代號'])
            if pd.notna(amount):b['dec_amt']+=abs(float(amount))
            else:b['unknown_amount']=True
    upcoming=passive[(passive['方向']=='新增')&(passive['官方生效日']>today.isoformat())&
                     (passive['資料公開日']<=today.isoformat())].copy() if len(passive) else pd.DataFrame(columns=COLS)
    historical=passive[(passive['官方生效日']<=today.isoformat())].copy() if len(passive) else pd.DataFrame(columns=COLS)
    passive_map={code:g for code,g in upcoming.groupby('股票代號')} if len(upcoming) else {}
    rows=[]
    for code in sorted(set(active)|set(passive_map)):
        a=active.get(code,{})
        g=passive_map.get(code)
        etfs=set(g['ETF代號']) if g is not None else set()
        indices=set(g['指數名稱']) if g is not None else set()
        aetfs=a.get('inc_etfs',set());aissuers=a.get('inc_issuers',set())
        dec=a.get('dec_etfs',set())
        names=[x for x in a.get('names',[]) if x]
        if g is not None:names+=list(g['股票名稱'])
        name=max(names,key=len,default='')
        net=a.get('inc_amt',0.)-a.get('dec_amt',0.)
        # Scenario, NOT a statistical forecast: two days, 50% of today's net delta per day.
        # Zero assumes no continuation; upper assumes same delta repeated for both days.
        model=round(max(0,net),4) if aetfs and not a.get('unknown_amount') else None
        upper=round(2*max(0,net),4) if model is not None else None
        if etfs and aetfs:signal='被動公告＋主動加碼重疊';bucket=0
        elif etfs:signal='被動公告待生效';bucket=1
        elif len(aissuers)>=2:signal='多投信主動加碼';bucket=2
        elif len(aetfs)>=2:signal='多檔主動加碼';bucket=3
        else:signal='單檔主動加碼／減碼';bucket=4
        if not etfs and not aetfs:continue
        notes=[]
        if etfs:notes.append('被動已公告；目標權重與基金資產需求未取得，金額不估')
        if a.get('unknown_amount'):notes.append('主動行情日不一致，金額不估')
        if aetfs:notes.append('主動未來兩日金額為未驗證續買情境，非預告')
        dates=sorted(set(a.get('dates',[])+list(g['資料公開日'] if g is not None else [])))
        all_etfs=sorted(etfs|aetfs)
        rows.append({'_group':bucket,'股票代號':code,'股票名稱':name,'訊號類型':signal,
          '未生效公告新增ETF數':len(etfs),'公告獨立指數數':len(indices),
          '主動加碼ETF數':len(aetfs),'主動加碼投信數':len(aissuers),'主動減碼ETF數':len(dec),
          '最近有效主動淨增持_億元':round(net,4) if aetfs and not a.get('unknown_amount') else None,
          '未來2交易日續買情境_億元':model,
          '續買情境下限_億元':0. if model is not None else None,
          '續買情境上限_億元':upper,
          '被動預估新增金額_億元':None,
          '被動金額說明':'缺指數調整目標權重與可核實基金規模，暫不估算' if etfs else '沒有尚未生效的被動公告',
          '受關注ETF清單':'、'.join(all_etfs),
          '指數生效日':'、'.join(sorted(set(g['官方生效日']))) if g is not None else '',
          '觀測最晚日期':max(dates) if dates else '',
          '來源狀態':'；'.join(notes)})
    ranked=pd.DataFrame(rows)
    if len(ranked):
        ranked['_amount']=ranked['未來2交易日續買情境_億元'].fillna(-1)
        ranked=ranked.sort_values(['_group','公告獨立指數數','主動加碼投信數',
                '主動加碼ETF數','_amount'],ascending=[True,False,False,False,False])
        ranked=ranked.drop(columns=['_group','_amount']).reset_index(drop=True)
        ranked.insert(0,'排名',range(1,len(ranked)+1))
    else:ranked=pd.DataFrame(columns=RANK_FIELDS)
    return ranked,upcoming,historical

def write_html(df,active_meta,passive_n,missing_n):
    cols=['排名','股票代號','股票名稱','訊號類型','未生效公告新增ETF數','主動加碼ETF數',
          '主動加碼投信數','最近有效主動淨增持_億元','未來2交易日續買情境_億元',
          '被動預估新增金額_億元','受關注ETF清單','指數生效日']
    headers=''.join('<th>'+escape(c)+'</th>' for c in cols)
    trs=[]
    for _,r in df.iterrows():
        trs.append('<tr>'+''.join('<td>'+escape('—' if pd.isna(r.get(c)) or r.get(c)=='' else str(r.get(c)))+'</td>' for c in cols)+'</tr>')
    html="""<!DOCTYPE html><html lang="zh-Hant"><meta charset="utf-8"><title>ETF 買進共識排名</title>
<style>body{font-family:Arial,'Noto Sans TC',sans-serif;background:#f7f9fb;color:#172033;padding:26px}h1{margin-bottom:8px}
small{color:#536579}.tablewrap{overflow:auto;border:1px solid #ccd4dd;border-radius:10px;background:#fff}
table{border-collapse:collapse;width:100%;white-space:nowrap;font-size:13px}th{background:#173456;color:white;position:sticky;top:0;cursor:pointer}
th,td{padding:11px 9px;border-bottom:1px solid #e6edf4;text-align:left}tr:nth-child(even){background:#f3f6fa}
p{max-width:1000px;line-height:1.6}.tip{border-left:3px solid #4e748b;padding:10px 14px;margin:16px 0;background:#eaf0f6}</style>
<h1>ETF 買進共識排名</h1><small>資料日期："""+escape(str(TODAY))+"""｜主動可比ETF數："""+str(active_meta['有效ETF數'])+"""｜被動可用公告事件："""+str(passive_n)+"""｜待核官方資料："""+str(missing_n)+"""</small>
<div class="tip">主動式金額是「最近已揭露持股增加」與「未來兩日續買情境」，不是經理人未來下單；被動式未揭露目標權重時金額留空。ETF納入不等於實際下單。</div>
<div class="tablewrap"><table id="t"><thead><tr>"""+headers+"""</tr></thead><tbody>"""+''.join(trs)+"""</tbody></table></div>
<p>排序順序：①被動＋主動重疊 ②被動公告 ③多投信主動加碼 ④多檔主動加碼 ⑤單檔；同級再按估算近期加碼和續買情境排序。</p>
<script>document.querySelectorAll('th').forEach((h,i)=>h.addEventListener('click',()=>{
const t=document.querySelector('#t tbody'), r=[...t.rows]; let asc=h.dataset.order!=='asc';
r.sort((a,b)=>{let x=a.cells[i].textContent.trim(),y=b.cells[i].textContent.trim();
let nx=Number(x.replace(/,/g,'')),ny=Number(y.replace(/,/g,''));let q=Number.isFinite(nx)&&Number.isFinite(ny)&&x!=='—'&&y!=='—'?(nx-ny):x.localeCompare(y,'zh-Hant');return asc?q:-q;});
r.forEach(x=>t.appendChild(x));h.dataset.order=asc?'asc':'desc';}));</script></html>"""
    (RADAR_OUT/'ETF_一頁式排名.html').write_text(html,encoding='utf-8')

def self_test():
    today=TODAY
    j={'etfs':{'00981A':{'data_date':today.isoformat(),'status':'ok','name':'測試主動ETF','issuer':'測試投信',
            'events':[{'code':'3189','name':'測試甲','type':'INCREASE','adjusted_shares_delta':100000,'quality':'ok',
                       'daily_comparable':True,'from_date':(today-timedelta(days=1)).isoformat(),'to_date':today.isoformat()}]}},
            'stocks':{'3189':{'close':100,'quote_date':today.isoformat(),'name':'測試甲'}}}
    a,meta=parse_active(j,today)
    assert len(a)==1 and abs(float(a.iloc[0]['當期估算增減部位_億元'])-0.1)<1e-8
    gg=pd.DataFrame([{'股票代號':'3189','股票名稱':'測試甲','方向':'新增','ETF代號':'00881',
              'ETF名稱':'測試指數ETF','指數名稱':'測試指數','官方公告日':today.isoformat(),
              '官方生效日':(today+timedelta(days=5)).isoformat(),'資料公開日':today.isoformat(),
              '公開時序':'事前公告','證據等級':'A','股票資訊來源':'測試','官方新聞頁':'測試'}])
    ranked,up,history=summarize(a,gg,today)
    assert len(ranked)==1 and ranked.iloc[0]['訊號類型']=='被動公告＋主動加碼重疊'
    assert abs(float(ranked.iloc[0]['未來2交易日續買情境_億元'])-.1)<1e-8
    assert pd.isna(ranked.iloc[0]['被動預估新增金額_億元'])
    # Expired notice never stays in upcoming.
    gg['官方生效日']=(today-timedelta(days=1)).isoformat()
    _,up,_=summarize(a,gg,today)
    assert len(up)==0
    # Stale or missing quote => money unknown instead of false 0.
    j['stocks']['3189']['quote_date']=(today-timedelta(days=1)).isoformat()
    dd,_=parse_active(j,today)
    assert pd.isna(dd.iloc[0]['當期估算增減部位_億元'])
    print('驗收通過：同股合併、金額計算、未生效過濾、缺行情不造金額、主動續買情境。')

def run_all():
    print('='*70)
    print('ETF 買進共識雷達 V6｜一次產生股票買進共識排名｜臺灣日期',TODAY)
    print('先做主動持股異動，再抓被動官方公告／公開轉載；來源不足不視為零買盤。')
    print('='*70)
    self_test()
    j=src_active();active,meta=parse_active(j,TODAY)
    print(f"主動來源：取得 {meta['ETF總數']} 檔ETF，近期有快照 {meta['有效ETF數']} 檔，"
          f"同一事件日 {meta['共同事件日']} 可比 {meta['同日可比ETF數']} 檔、有效異動 {meta['明細筆數']} 筆")
    try:
        passive,mapping,pages=collect_passive()
    except Exception as e:
        ERR.append({'來源':'TIP+MoneyDJ','種類':'被動公告','錯誤':str(e)[:250]})
        print('[被動公告抓取失敗]',str(e)[:160])
        passive=pd.DataFrame(columns=COLS);pages={}
    ranked,upcoming,historical=summarize(active,passive,TODAY)
    # Not all official PDF notices can be read; report coverage explicitly.
    unresolved=[]
    for url,(textbody,soup) in pages.items():
        for tr in soup.find_all('tr'):
            vals=[td.get_text(' ',strip=True) for td in tr.find_all(['th','td'])]
            if len(vals)>2 and '指數' in vals[0]:
                codes=re.findall(r'(?<!\d)(0\d{3,5}[A-Z]?)(?!\w)',vals[2]);codes=[x for x in codes if not x.startswith('02')]
                if codes and not any(x in set(passive['ETF代號']) for x in codes):
                    unresolved.append({'官方頁':url,'指數':vals[0],'ETF代號':'、'.join(codes),'資料狀態':'官方網頁有指數調整，但股票名單未能驗證'})
    missing=pd.DataFrame(unresolved).drop_duplicates() if unresolved else pd.DataFrame(columns=['官方頁','指數','ETF代號','資料狀態'])
    sheets={
     '01_ETF買進共識排名.csv':ranked,
     '02_主動ETF逐檔股數變化.csv':active,
     '03_被動ETF公告尚未生效.csv':upcoming,
     '04_公告事件_含歷史及刪除.csv':passive,
     '05_官方公告但股票名單未核對.csv':missing,
     '06_資料來源紀錄.csv':pd.DataFrame(AUDIT,columns=['來源','種類','狀態','內容字數','原因']),
     '07_抓取錯誤.csv':pd.DataFrame(ERR,columns=['來源','種類','錯誤']),
    }
    for filename,df in sheets.items():df.to_csv(RADAR_OUT/filename,index=False,encoding='utf-8-sig')
    (RADAR_OUT/'README_口徑限制.txt').write_text(
     f'ETF 共識雷達V6｜臺灣日期 {TODAY}\n'
     '1. 被動：只納入官方公告或可靠轉載可核實，且生效日晚於今天的股票；公告不表示ETF尚未買入。\n'
     '2. 主動：每日公開持股股數變化的調整估計，並非逐筆成交。\n'
     '3. 假設未來2交易日各續買最近一日的50%：中性情境＝最近可比淨增加市值×1；下限0、上限×2。\n'
     '   上述純情境假設，未經歷史校準，不是機率預測或實際訂單。\n'
     '4. 如無同期股價或持股品質不足，金額留白。\n'
     '5. 被動公告若缺ETF最新資產規模及指數新目標權重，預計新增金額留空，不可用ETF數量推估金額。\n'
     '6. 資料來源不保證覆蓋全台ETF；PDF來源可能HTTP429，請看缺漏清單。\n'
     '主動來源 https://github.com/nctuwanglin/active-etf\n'
     '指數公告 https://taiwanindex.com.tw/media_and_events\n'
     '7. 重複執行才會更新，不會在Colab關閉後自動持續監測。\n',encoding='utf-8')
    write_html(ranked,meta,len(passive),len(missing))
    z=ROOT/'ETF_Buy_Radar_V6_結果.zip'
    with zipfile.ZipFile(z,'w',compression=zipfile.ZIP_DEFLATED) as file:
        for f in RADAR_OUT.iterdir():
            if f.is_file():file.write(f,arcname=f.name)
    print(f'被動可用事件：{len(passive)}；其中尚未生效新增 {len(upcoming)}；官方未補齊 {len(missing)} 項')
    print('\n【綜合買進共識排名（前20）】')
    show=['排名','股票代號','股票名稱','訊號類型','未生效公告新增ETF數',
          '主動加碼ETF數','主動加碼投信數','未來2交易日續買情境_億元','指數生效日']
    print(ranked[show].head(20).to_string(index=False) if len(ranked) else '資料不足；不產生虛假排名')
    print('下載結果：',z)
    try:
        from google.colab import files
        files.download(str(z))
    except ImportError:pass
    return ranked,active,upcoming

if __name__=='__main__':
    run_all()
