import os
import sys
import time
import re
import json
import pandas as pd
from datetime import datetime, timedelta, timezone
import streamlit as st
from playwright.sync_api import sync_playwright

os.system("playwright install chromium")

TODAY_SCHEDULE_URL = "https://keirin.netkeiba.com/race/?rf=navii"
DATA_FILE = "today_data.json"
TOTAL_BUDGET = 1200  # 予算を共通変数として定義
JST = timezone(timedelta(hours=+9), 'JST')

GRADE_WEIGHTS = {
    "決勝": 1.5, "決": 1.5, "特選": 1.4, "特": 1.4,
    "準決": 1.3, "準": 1.3, "選抜": 1.2, "選": 1.2,
    "予選": 1.0, "予": 1.0, "一般": 0.8, "般": 0.8, "一": 0.8
}

def get_today_str():
    return datetime.now(JST).strftime("%Y-%m-%d")

# 発走時刻ソート用の補助関数
def get_sort_time(time_str):
    if not time_str:
        return "23:59"  
    if ':' in time_str:
        try:
            h, m = time_str.split(':', 1)
            return f"{int(h):02d}:{m}"
        except:
            return time_str
    return time_str

def calculate_condition_score(past_races):
    total_score = 0
    total_weight = 0.0  
    
    for r in past_races:
        race_grade = r['grade']
        rank = r['rank']
        time_weight = r['time_weight'] 
        
        is_standard_race = False
        grade_weight = 1.0
        
        for key, w in GRADE_WEIGHTS.items():
            if key in race_grade:
                is_standard_race = True
                grade_weight = w
                break
                
        if not is_standard_race:
            continue
        
        if rank == 1: base_pt = 100
        elif rank == 2: base_pt = 85
        elif rank == 3: base_pt = 70
        elif rank == 4: base_pt = 60
        elif rank == 5: base_pt = 40
        elif rank == 6: base_pt = 10
        elif 7 <= rank <= 9: base_pt = 0
        else: continue 
            
        total_score += (base_pt * grade_weight * time_weight)
        total_weight += time_weight
        
    if total_weight == 0: 
        return 0.0
    
    return round(total_score / total_weight, 2)

def generate_ticket_evaluations(evals):
    tickets = []
    if len(evals) < 5: return []
    
    top_players = evals[:5] 
    top5_waku = [str(p['raw_waku']) for p in top_players]
    
    is_ironclad_1st = (top_players[0]['総合期待度'] - top_players[1]['総合期待度']) >= 15
    
    w1_bonus_waku = str(top_players[0]['raw_waku']) if is_ironclad_1st else None
    w2_bonus_waku = str(top_players[1]['raw_waku']) if len(top_players) >= 3 and (top_players[1]['総合期待度'] - top_players[2]['総合期待度']) >= 15 else None
    w3_bonus_waku = str(top_players[2]['raw_waku']) if len(top_players) >= 4 and (top_players[2]['総合期待度'] - top_players[3]['総合期待度']) >= 15 else None
    
    if is_ironclad_1st:
        first_candidates = [top5_waku[0]]
        second_candidates = top5_waku[1:3]
        third_candidates = top5_waku[1:5]
    else:
        first_candidates = top5_waku[:2]
        second_candidates = top5_waku[:3]
        third_candidates = top5_waku[:5]
    
    for first in first_candidates:
        for second in second_candidates:
            if first == second: continue
            for third in third_candidates:
                if first == third or second == third: continue
                tickets.append(f"{first}-{second}-{third}")
    
    ticket_evaluations = []
    for t in tickets:
        w1, w2, w3 = t.split('-')
        s1 = next((p['総合期待度'] for p in evals if str(p['raw_waku']) == w1), 0)
        s2 = next((p['総合期待度'] for p in evals if str(p['raw_waku']) == w2), 0)
        s3 = next((p['総合期待度'] for p in evals if str(p['raw_waku']) == w3), 0)
        base_score = s1 + s2 + s3
        
        bonus = 0
        if w1_bonus_waku and w1 == w1_bonus_waku: bonus += 100000
        if w2_bonus_waku and w2 == w2_bonus_waku: bonus += 50000
        if w3_bonus_waku and w3 == w3_bonus_waku: bonus += 25000
        
        ticket_evaluations.append({
            'ticket': t, 'expected_score': base_score + bonus, 'display_score': base_score,
            'has_bonus': bonus > 0, 's1': s1, 's2': s2, 's3': s3
        })
    
    ticket_evaluations.sort(key=lambda x: (x['expected_score'], x['s1'], x['s2'], x['s3']), reverse=True)
    return ticket_evaluations

# ==========================================
# 修正箇所: 本日の「全会場」のURLを確実に取得する
# ==========================================
def get_all_venue_urls(page, schedule_url):
    page.goto(schedule_url, timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    return page.evaluate(r'''() => {
        const links = new Set();
        const aTags = document.querySelectorAll('a');
        aTags.forEach(a => {
            const href = a.getAttribute('href');
            // race_idが含まれるリンクを全て取得（漏れを無くす）
            if (href && href.includes('race_id=') && !href.includes('javascript')) {
                links.add(a.href);
            }
        });
        return Array.from(links);
    }''')

# ==========================================
# 修正箇所: ガールズケイリンの判定を「クラス（Ｌ１）」などで完全判定
# ==========================================
def extract_entry_data(page, entry_url):
    page.goto(entry_url, timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(1000) # DOM描画待ち
    info = page.evaluate(r'''() => {
        let isGirls = false;
        
        const header = document.querySelector('.Race_Title') || document.querySelector('.Race_Header');
        const dataArea = document.querySelector('.RaceList_Data');
        
        let targetText = '';
        if (header) targetText += header.innerText + ' ';
        if (dataArea) targetText += dataArea.innerText + ' ';
        
        // 1. レース名や出走表に「L級」「Ｌ級」「L1」「Ｌ１」「ガールズ」が含まれているか（全角半角対応）
        if (targetText.includes('L級') || targetText.includes('Ｌ級') || targetText.includes('L1') || targetText.includes('Ｌ１') || targetText.includes('ガールズ')) {
            isGirls = true;
        }
        
        // 2. アイコンがあるか
        if (document.querySelector('.Icon_RaceMark.Girls')) {
            isGirls = true;
        }
        
        // 3. 誤爆防止: S級・A級のみのレースは強制除外（男子確定のため）
        if ((targetText.includes('Ｓ級') || targetText.includes('S級') || targetText.includes('Ａ級') || targetText.includes('A級')) 
            && !(targetText.includes('Ｌ級') || targetText.includes('L級') || targetText.includes('ガールズ'))) {
            isGirls = false;
        }
        
        const title = document.title || '';
        let venue = "";
        let match = title.match(/([^\s【]+)競輪/);
        if (match) venue = match[1];
        else {
            const placeEl = document.querySelector('.Race_Place');
            if (placeEl) venue = placeEl.innerText.trim();
        }
        
        let startTime = "";
        let closeTime = "";
        const pageText = document.body.innerText || "";
        const startMatch = pageText.match(/発走\s*(\d{1,2}:\d{2})/);
        if (startMatch) startTime = startMatch[1];
        const closeMatch = pageText.match(/締切\s*(\d{1,2}:\d{2})/);
        if (closeMatch) closeTime = closeMatch[1];

        return { is_girls: isGirls, venue: venue, start_time: startTime, close_time: closeTime };
    }''')
    
    if not info['is_girls']: return None
    page.wait_for_timeout(2000)
    
    players = page.evaluate(r'''() => {
        const results = [];
        const seen = new Set();
        const playerLinks = document.querySelectorAll('a[href*="profile/?id="]');
        let orderCount = 1;
        playerLinks.forEach(a => {
            const text = a.innerText.trim();
            if(!text || text.length < 2 || !isNaN(text)) return;
            const name = text.split('\n')[0].trim();
            if(name === 'データベース' || name.includes('プロフィール')) return;
            if (seen.has(name)) return;
            seen.add(name);
            
            let playerBlock = a.parentElement;
            while (playerBlock && playerBlock.tagName !== 'BODY') {
                let parent = playerBlock.parentElement;
                if (!parent) break;
                let profilesInParent = parent.querySelectorAll('a[href*="profile/?id="]');
                let uniqueProfiles = new Set();
                profilesInParent.forEach(link => {
                    let linkText = link.innerText.trim();
                    if(linkText && linkText.length >= 2 && isNaN(linkText) && !linkText.includes('データベース')) uniqueProfiles.add(linkText.split('\n')[0].trim());
                });
                if (uniqueProfiles.size > 1) break; 
                playerBlock = parent;
            }
            
            let blockText = playerBlock.innerText || playerBlock.textContent || "";
            let nextNode = playerBlock.nextElementSibling;
            let limit = 0;
            while (nextNode && limit < 5) {
                let profiles = Array.from(nextNode.querySelectorAll('a[href*="profile/?id="]')).map(el => el.innerText.trim().split('\n')[0]);
                let hasOtherPlayer = profiles.some(p => p && p.length >= 2 && isNaN(p) && p !== 'データベース' && !p.includes('プロフィール'));
                if (hasOtherPlayer) break;
                blockText += " " + (nextNode.innerText || nextNode.textContent || "");
                nextNode = nextNode.nextElementSibling;
                limit++;
            }
            
            let cleanText = blockText.replace(/\s+/g, ' ').replace(/[０-９．]/g, s => s === '．' ? '.' : String.fromCharCode(s.charCodeAt(0) - 0xFEE0));
            let score = "---.--";
            const numberRegex = /(\d{2,3}(?:\.\d{1,2})?)(?!\s*(%|％|kg|ｋｇ|歳|才|期|勝|回|車))/gi;
            const matches = [...cleanText.matchAll(numberRegex)];
            let foundScore = null;
            let fallbackScore = null;
            for (let m of matches) {
                let val = parseFloat(m[1]);
                if (val >= 40.0 && val <= 90.0) {
                    if (m[1].includes('.')) { foundScore = val.toFixed(2); break; } 
                    else { if (!fallbackScore) fallbackScore = val.toFixed(2); }
                }
            }
            score = foundScore || fallbackScore || "---.--";
            results.push({ waku: orderCount, name: name, score: score });
            orderCount++;
        });
        return results;
    }''')
    return { "venue_name": info['venue'], "start_time": info['start_time'], "close_time": info['close_time'], "players": players }

def extract_past_results(page, results_url, player_names):
    page.goto(results_url, timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    return page.evaluate(r'''(names) => {
        const resultsMap = {};
        names.forEach(name => resultsMap[name] = []);
        const trs = document.querySelectorAll('tr');
        trs.forEach(tr => {
            let matchedName = null;
            for (let name of names) {
                if (tr.innerText.includes(name)) { matchedName = name; break; }
            }
            if (!matchedName) return;
            const tds = Array.from(tr.querySelectorAll('td'));
            let nameTdIndex = -1;
            tds.forEach((td, idx) => { if (td.innerText.includes(matchedName)) nameTdIndex = idx; });
            if (nameTdIndex === -1) return;
            const races = [];
            for (let idx = nameTdIndex + 1; idx < tds.length; idx++) {
                if (idx > nameTdIndex + 25) break; 
                const td = tds[idx];
                let cleanText = td.innerText.replace(/\d{1,2}\/\d{1,2}/g, ' ').replace(/\d{3}m?/g, ' ');
                let lines = cleanText.split(/[\s\n]+/);
                let cellRaces = [];
                let g = null;
                lines.forEach(line => {
                    if (!line) return;
                    const attachedRank = line.match(/(.*?)([①-⑨❶-❾落失欠])$/);
                    if (attachedRank && /(ガ|決勝|特選|選抜|予選|一般|準決|決|特|選|予|般|準)/.test(attachedRank[1])) {
                        cellRaces.push({grade: attachedRank[1], rank: attachedRank[2]});
                    } else if (/(ガ|決勝|特選|選抜|予選|一般|準決|決|特|選|予|般|準)/.test(line)) {
                        g = line;
                    } else {
                        const rankMatch = line.match(/^([1-9①-⑨❶-❾落失欠])$/);
                        if (rankMatch && g) { cellRaces.push({grade: g, rank: rankMatch[1]}); g = null; }
                    }
                });
                cellRaces.forEach(r => {
                    const circleMap = {'①':'1','②':'2','③':'3','④':'4','⑤':'5','⑥':'6','⑦':'7','⑧':'8','⑨':'9','❶':'1','❷':'2','❸':'3','❹':'4','❺':'5','❻':'6','❼':'7','❽':'8','❾':'9'};
                    let rankText = circleMap[r.rank] || r.rank;
                    let rankNum = parseInt(rankText);
                    if (isNaN(rankNum)) rankNum = 99;
                    races.push({ grade: r.grade, rank: rankNum });
                });
            }
            const seriesList = [];
            let currentSeries = [];
            races.forEach(race => {
                let isNewSeries = false;
                if (currentSeries.length === 0) isNewSeries = true;
                else {
                    const prevGrade = currentSeries[currentSeries.length - 1].grade;
                    const currGrade = race.grade;
                    if (currGrade.includes("予１") || currGrade.includes("予選１")) isNewSeries = true;
                    else if ((prevGrade.includes("決") || prevGrade.includes("般") || prevGrade.includes("一") || prevGrade.includes("選抜")) && (currGrade.includes("予") || currGrade.includes("特"))) isNewSeries = true;
                    else if (currGrade.includes("予２") && prevGrade.includes("予２")) isNewSeries = true;
                }
                if (isNewSeries && currentSeries.length > 0) { seriesList.push(currentSeries); currentSeries = []; }
                currentSeries.push(race);
            });
            if (currentSeries.length > 0) seriesList.push(currentSeries);
            
            const timeWeights = [2.0, 1.0, 0.8, 0.6, 0.4, 0.2];
            const seriesNames = ["今", "直1", "直2", "直3", "直4", "直5"];
            const maxSeries = Math.min(seriesList.length, 6);
            for (let i = 0; i < maxSeries; i++) {
                const weight = timeWeights[i];
                const sName = seriesNames[i];
                seriesList[i].forEach(race => {
                    resultsMap[matchedName].push({ grade: race.grade, rank: race.rank, time_weight: weight, series_name: sName });
                });
            }
        });
        return resultsMap;
    }''', player_names)

def extract_ticket_odds(page, race_id, tickets):
    odds_url = f"https://keirin.netkeiba.com/race/odds/?race_id={race_id}"
    page.goto(odds_url, timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    page.evaluate(r'''() => {
        const tabs = document.querySelectorAll('a, li, span, div, label, button');
        for (let el of tabs) {
            let txt = el.innerText || "";
            if (txt.trim() === '人気' || txt.includes('人気順')) { el.click(); break; }
        }
    }''')
    page.wait_for_timeout(3000)
    return page.evaluate(r'''(tickets) => {
        const result = {};
        tickets.forEach(t => result[t] = 0.0);
        const elements = document.querySelectorAll('li, tr, .OddsItem, .OddsList_Item');
        elements.forEach(el => {
            let txt = el.innerText;
            if (!txt) return;
            const matches = txt.match(/\d+(\.\d+)?/g);
            if (!matches || matches.length < 4) return;
            const floats = matches.map(Number);
            const odds = floats[floats.length - 1];
            const w3 = floats[floats.length - 2];
            const w2 = floats[floats.length - 3];
            const w1 = floats[floats.length - 4];
            const ticket = `${w1}-${w2}-${w3}`;
            if (result[ticket] !== undefined && odds > 0) result[ticket] = odds;
        });
        return result;
    }''', tickets)

def run_heavy_scraping():
    now = datetime.now(JST)
    today_str = now.strftime("%Y-%m-%d")
    timestamp_str = now.isoformat()
    scraped_data = {"date": today_str, "timestamp": timestamp_str, "races": []}
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        context = browser.new_context(viewport={"width": 1280, "height": 720}, ignore_https_errors=True)
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        try:
            target_urls = get_all_venue_urls(page, TODAY_SCHEDULE_URL)
            base_ids = set()
            for url in target_urls:
                match = re.search(r'race_id=(\d{10})\d{2}', url)
                if match: base_ids.add(match.group(1))
            
            for base_id in base_ids:
                venue_code = base_id[-2:]
                current_venue_name = f"場コード({venue_code})"
                
                for i in range(1, 13):
                    race_num = str(i).zfill(2)
                    race_id = f"{base_id}{race_num}"
                    entry_url = f"https://keirin.netkeiba.com/race/entry/?race_id={race_id}"
                    results_url = f"https://keirin.netkeiba.com/race/entry/results.html?race_id={race_id}"
                    
                    entry_data = extract_entry_data(page, entry_url)
                    if entry_data:
                        if entry_data['venue_name']:
                            current_venue_name = entry_data['venue_name']
                        
                        players = entry_data['players']
                        player_names = [p['name'] for p in players]
                        past_results_data = extract_past_results(page, results_url, player_names)
                        
                        race_evaluations = []
                        for p_data in players:
                            name = p_data['name']
                            waku = p_data['waku']
                            score_val = float(p_data['score']) if p_data['score'] != "---.--" else 40.0
                            target_races = past_results_data.get(name, [])
                            condition_score = calculate_condition_score(target_races)
                            
                            race_evaluations.append({
                                '車番': waku,
                                '選手名': name,
                                '競走得点': score_val,
                                '調子スコア': condition_score,
                                'raw_waku': waku,
                                'raw_cond': condition_score
                            })
                        
                        race_evaluations.sort(key=lambda x: x['競走得点'], reverse=True)
                        for rank, p_ev in enumerate(race_evaluations, 1): p_ev['得点順位'] = rank
                            
                        race_evaluations.sort(key=lambda x: x['調子スコア'], reverse=True)
                        for rank, p_ev in enumerate(race_evaluations, 1):
                            p_ev['勢いギャップ'] = p_ev['得点順位'] - rank 
                            p_ev['総合期待度'] = round(p_ev['調子スコア'] + (p_ev['勢いギャップ'] * 3.0), 2)

                        race_evaluations.sort(key=lambda x: x['総合期待度'], reverse=True)
                        
                        scraped_data["races"].append({
                            "race_id": race_id,
                            "venue_name": current_venue_name,
                            "race_num": i,
                            "start_time": entry_data.get('start_time', ''),
                            "close_time": entry_data.get('close_time', ''),
                            "evaluations": race_evaluations
                        })
                        time.sleep(1)
        finally:
            browser.close()
            
    scraped_data["races"].sort(key=lambda x: get_sort_time(x.get('start_time', '')))
            
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(scraped_data, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--scrape":
        run_heavy_scraping()
        sys.exit(0)

# ==========================================
# スタイル設定
# ==========================================
def style_players(row):
    styles = [''] * len(row)
    if row['想定順位'] == '1位': styles[0] = 'background-color: #FFF2CC; color: #B8860B; font-weight: bold;'
    elif row['想定順位'] == '2位': styles[0] = 'background-color: #F2F2F2; color: #708090; font-weight: bold;'
    elif row['想定順位'] == '3位': styles[0] = 'background-color: #FCE5CD; color: #A0522D; font-weight: bold;'
    try:
        gap = float(row['勢いギャップ'])
        if gap > 0: styles[5] = 'color: #D32F2F; font-weight: bold;'
        elif gap < 0: styles[5] = 'color: #1976D2;'
    except: pass
    return styles

def style_bets(row):
    styles = [''] * len(row)
    if row['購入額'] == '見送り': return ['color: #B0BEC5;'] * len(row)
    else:
        styles[1] = 'font-weight: bold;'
        styles[4] = 'color: #D32F2F; font-weight: bold;'
        styles[5] = 'color: #388E3C; font-weight: bold;'
    return styles

def style_pre(row):
    return [''] + ['font-weight: bold;'] + ['']

# ==========================================
# Streamlit UI
# ==========================================
st.set_page_config(page_title="ガールズケイリン予想システム", page_icon="🚴‍♀️", layout="wide")
st.title("🚴‍♀️ ガールズケイリン予想＆資金配分システム")

now = datetime.now(JST)
today_str = now.strftime("%Y-%m-%d")
data = None

if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            
        if isinstance(raw_data, dict) and "races" in raw_data:
            data = raw_data
        else:
            data = None 

        if data is not None:
            if data.get("date") != today_str:
                data = None 
            else:
                seven_am = now.replace(hour=7, minute=0, second=0, microsecond=0)
                if now >= seven_am:
                    ts_str = data.get("timestamp")
                    if ts_str:
                        data_ts = datetime.fromisoformat(ts_str)
                        if data_ts < seven_am:
                            data = None
                else:
                    data = None
    except Exception as e:
        st.error(f"データ読み込みエラー: {e}")
        data = None

if data is None:
    st.warning(f"本日 ({today_str}) の出走表データがまだ準備されていません。")
    st.info("💡 朝7:00以前に確認したい場合やデータ未作成時は、下のボタンから手動で出走表を取得・生成できます。")
    if st.button("🚀 手動で本日の出走表を取得・予想作成"):
        with st.spinner("出走表と過去成績を取得・計算しています。男子レースを弾きながら探すため、数分お待ちください..."):
            try:
                run_heavy_scraping()
                st.success("取得が完了しました！画面を更新します。")
                time.sleep(1)
                st.rerun()
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")
else:
    fetched_time_str = ""
    if "timestamp" in data:
        dt = datetime.fromisoformat(data["timestamp"])
        fetched_time_str = dt.strftime("%H:%M")
        
    st.success(f"✅ {data['date']} の出走表データ読み込み完了（取得時刻: {fetched_time_str} / 全{len(data.get('races', []))}レース）")
    
    safe_races = [r for r in data.get('races', []) if isinstance(r, dict)]
    safe_races.sort(key=lambda x: get_sort_time(str(x.get('start_time', ''))))
    data['races'] = safe_races
    
    for race in data['races']:
        venue = race.get('venue_name', '不明')
        r_num = race.get('race_num', 0)
        r_id = race.get('race_id', '0000')
        evals = race.get('evaluations', [])
        
        s_time = race.get('start_time', '')
        c_time = race.get('close_time', '')
        time_info_str = ""
        is_finished = False
        
        if c_time:
            try:
                ch, cm = map(int, c_time.split(':'))
                close_dt = now.replace(hour=ch, minute=cm, second=0, microsecond=0)
                if now >= close_dt + timedelta(minutes=10):
                    is_finished = True
            except:
                pass
        
        if s_time or c_time:
            time_parts = []
            if s_time: time_parts.append(f"発走 {s_time}")
            if c_time: time_parts.append(f"締切 {c_time}")
            time_info_str = f" ｜ ⏰ {' / '.join(time_parts)}"
            
        if is_finished:
            expander_title = f"🏁【レース終了】 🏆 【{venue}】 {r_num}R (L級){time_info_str}"
        else:
            expander_title = f"🏆 【{venue}】 {r_num}R (L級){time_info_str}"
            
        with st.expander(expander_title, expanded=False):
            df_players = pd.DataFrame(evals)
            if not df_players.empty:
                df_players.insert(0, '想定順位', [f"{r}位" for r in range(1, len(df_players) + 1)])
                
                st.markdown("##### 🚴‍♀️ 出走選手データ (総合期待度順)")
                styled_players = df_players[['想定順位', '車番', '選手名', '競走得点', '調子スコア', '勢いギャップ', '総合期待度']].style.apply(style_players, axis=1)
                st.dataframe(styled_players, hide_index=True)
                
                if len(evals) >= 2:
                    gap_1_2 = evals[0]['総合期待度'] - evals[1]['総合期待度']
                    if gap_1_2 >= 15:
                        st.success(f"🔥 **鉄板レース検知！** 1位と2位の評価ギャップが **{gap_1_2:.2f}** あります。1着を【{evals[0]['車番']}番 {evals[0]['選手名']}】に完全固定して買い目を生成します。")

                col1, col2 = st.columns([1, 1])
                with col1:
                    if st.button(f"🎯 買い目を事前確認", key=f"btn_tic_{r_id}"):
                        st.session_state[f"mode_{r_id}"] = "tickets"
                with col2:
                    if st.button(f"📊 オッズ取得＆資金配分", key=f"btn_odds_{r_id}"):
                        with st.spinner("オッズを取得中... (約5秒)"):
                            try:
                                ticket_evals = generate_ticket_evaluations(evals)
                                tickets = [ev['ticket'] for ev in ticket_evals]
                                
                                with sync_playwright() as p:
                                    browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
                                    context = browser.new_context(ignore_https_errors=True)
                                    page = context.new_page()
                                    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                                    odds_data = extract_ticket_odds(page, r_id, tickets)
                                    browser.close()
                                    
                                for ev in ticket_evals:
                                    ev['odds'] = odds_data.get(ev['ticket'], 0.0)
                                    
                                target_payout = TOTAL_BUDGET * 1.5
                                valid_tickets = [ev['ticket'] for ev in ticket_evals if ev['odds'] > 0]
                                ev_dict = {ev['ticket']: ev for ev in ticket_evals}
                                
                                max_k = min(len(valid_tickets), TOTAL_BUDGET // 100)
                                best_bets = {}
                                
                                if max_k > 0:
                                    for k in range(max_k, 0, -1):
                                        current_active = valid_tickets[:k]
                                        bets = {t: 100 for t in current_active}
                                        remaining_budget = TOTAL_BUDGET - (100 * k)
                                        
                                        while remaining_budget >= 100:
                                            lowest_t = min(current_active, key=lambda t: bets[t] * ev_dict[t]['odds'])
                                            bets[lowest_t] += 100
                                            remaining_budget -= 100
                                            
                                        min_payout = min(bets[t] * ev_dict[t]['odds'] for t in current_active)
                                        if min_payout >= target_payout or k == 1:
                                            best_bets = bets
                                            break

                                result_rows = []
                                for rank, ev in enumerate(ticket_evals, 1):
                                    t = ev['ticket']
                                    odds_val = ev['odds']
                                    odds_str = f"{odds_val}倍" if odds_val > 0 else "取得失敗"
                                    score_str = f"{ev['display_score']:.2f}" + (" ★" if ev['has_bonus'] else "")
                                    
                                    if t in best_bets:
                                        bet = best_bets[t]
                                        payout = bet * odds_val
                                        payout_str = f"¥{payout:,.0f}" + (" (未達)" if payout < target_payout else "")
                                        result_rows.append({"優先順位": f"{rank}位", "買い目": t, "調子期待値": score_str, "現在オッズ": odds_str, "購入額": f"¥{bet}", "払戻見込": payout_str})
                                    else:
                                        result_rows.append({"優先順位": f"{rank}位", "買い目": t, "調子期待値": score_str, "現在オッズ": odds_str, "購入額": "見送り", "払戻見込": "-"})
                                
                                st.session_state[f"result_{r_id}"] = pd.DataFrame(result_rows)
                                st.session_state[f"mode_{r_id}"] = "odds"
                            except Exception as e:
                                st.error(f"オッズ取得でエラーが発生しました: {e}")

                current_mode = st.session_state.get(f"mode_{r_id}")
                
                if current_mode == "tickets":
                    ticket_evals = generate_ticket_evaluations(evals)
                    result_rows = []
                    for rank, ev in enumerate(ticket_evals, 1):
                        score_str = f"{ev['display_score']:.2f}" + (" ★" if ev['has_bonus'] else "")
                        result_rows.append({"優先順位": f"{rank}位", "買い目": ev['ticket'], "調子期待値": score_str})
                    df_tickets = pd.DataFrame(result_rows)
                    
                    st.markdown("##### 🎯 買い目候補 (オッズ取得前)")
                    st.dataframe(df_tickets.style.apply(style_pre, axis=1), hide_index=True)
                    
                elif current_mode == "odds" and f"result_{r_id}" in st.session_state:
                    st.markdown(f"##### 💰 資金配分 (目標回収率150% / {TOTAL_BUDGET}円)")
                    styled_results = st.session_state[f"result_{r_id}"].style.apply(style_bets, axis=1)
                    st.dataframe(styled_results, hide_index=True)
            else:
                st.warning("選手データがありません。")
