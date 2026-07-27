import os
os.system("playwright install chromium")
import time
import re
import pandas as pd
import streamlit as st
from playwright.sync_api import sync_playwright

TODAY_SCHEDULE_URL = "https://keirin.netkeiba.com/race/?rf=navii"

GRADE_WEIGHTS = {
    "決勝": 1.5, "決": 1.5, "特選": 1.4, "特": 1.4,
    "準決": 1.3, "準": 1.3, "選抜": 1.2, "選": 1.2,
    "予選": 1.0, "予": 1.0, "一般": 0.8, "般": 0.8, "一": 0.8
}

def calculate_condition_score(past_races):
    total_score = 0
    valid_race_count = 0
    
    for r in past_races:
        race_grade = r['grade']
        rank = r['rank']
        time_weight = r['time_weight'] 
        
        if rank == 1: base_pt = 100
        elif rank == 2: base_pt = 85
        elif rank == 3: base_pt = 70
        elif rank == 4: base_pt = 60
        elif rank == 5: base_pt = 40
        elif rank == 6: base_pt = 10
        elif 7 <= rank <= 9: base_pt = 0
        else: continue 
            
        grade_weight = 1.0
        for key, w in GRADE_WEIGHTS.items():
            if key in race_grade:
                grade_weight = w
                break
                
        total_score += (base_pt * grade_weight * time_weight)
        valid_race_count += 1
        
    if valid_race_count == 0: return 0.0
    return round(total_score / valid_race_count, 2)

def get_girls_venue_urls(page, schedule_url):
    page.goto(schedule_url, timeout=60000, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    return page.evaluate(r'''() => {
        const icons = document.querySelectorAll('.Icon_RaceMark.Girls');
        const links = new Set();
        icons.forEach(icon => {
            const cell = icon.closest('td') || icon.closest('li') || icon.closest('div');
            if (cell) {
                const cellLinks = cell.querySelectorAll('a');
                cellLinks.forEach(a => {
                    const href = a.getAttribute('href');
                    if (href && href.includes('race_id') && !href.includes('javascript')) links.add(a.href);
                });
            }
        });
        return Array.from(links);
    }''')

def extract_entry_data(page, entry_url):
    page.goto(entry_url, timeout=30000, wait_until="domcontentloaded")
    
    info = page.evaluate(r'''() => {
        const title = document.title || '';
        let isGirls = false;
        if (title.includes('L級') || title.includes('ガールズ')) isGirls = true;
        const header = document.querySelector('.Race_Title, .Race_Header, .RaceList_Data');
        if (header) {
            if (header.innerText.includes('L級') || header.innerText.includes('ガールズ')) isGirls = true;
            if (header.querySelector('.Icon_RaceMark.Girls')) isGirls = true;
        }
        
        let venue = "";
        let match = title.match(/([^\s【]+)競輪/);
        if (match) {
            venue = match[1];
        } else {
            const placeEl = document.querySelector('.Race_Place');
            if (placeEl) venue = placeEl.innerText.trim();
        }
        return { is_girls: isGirls, venue: venue };
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
    
    return { "venue_name": info['venue'], "players": players }

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
            tds.forEach((td, idx) => {
                if (td.innerText.includes(matchedName)) nameTdIndex = idx;
            });
            
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
                        if (rankMatch && g) {
                            cellRaces.push({grade: g, rank: rankMatch[1]});
                            g = null; 
                        }
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
                if (currentSeries.length === 0) {
                    isNewSeries = true;
                } else {
                    const prevGrade = currentSeries[currentSeries.length - 1].grade;
                    const currGrade = race.grade;
                    
                    if (currGrade.includes("予１") || currGrade.includes("予選１")) {
                        isNewSeries = true;
                    } 
                    else if ((prevGrade.includes("決") || prevGrade.includes("般") || prevGrade.includes("一") || prevGrade.includes("選抜")) 
                              && (currGrade.includes("予") || currGrade.includes("特"))) {
                        isNewSeries = true;
                    } 
                    else if (currGrade.includes("予２") && prevGrade.includes("予２")) {
                        isNewSeries = true;
                    }
                }
                
                if (isNewSeries && currentSeries.length > 0) {
                    seriesList.push(currentSeries);
                    currentSeries = [];
                }
                currentSeries.push(race);
            });
            
            if (currentSeries.length > 0) {
                seriesList.push(currentSeries);
            }
            
            const timeWeights = [2.0, 1.0, 0.8, 0.6, 0.4, 0.2];
            const seriesNames = ["今", "直1", "直2", "直3", "直4", "直5"];
            
            const maxSeries = Math.min(seriesList.length, 6);
            for (let i = 0; i < maxSeries; i++) {
                const weight = timeWeights[i];
                const sName = seriesNames[i];
                seriesList[i].forEach(race => {
                    resultsMap[matchedName].push({
                        grade: race.grade,
                        rank: race.rank,
                        time_weight: weight,
                        series_name: sName
                    });
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
            if (txt.trim() === '人気' || txt.includes('人気順')) {
                el.click();
                break;
            }
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
            if (result[ticket] !== undefined && odds > 0) {
                result[ticket] = odds;
            }
        });
        return result;
    }''', tickets)

def style_players(row):
    styles = [''] * len(row)
    if row['想定順位'] == '1位': styles[0] = 'background-color: #FFF2CC; color: #B8860B; font-weight: bold;'
    elif row['想定順位'] == '2位': styles[0] = 'background-color: #F2F2F2; color: #708090; font-weight: bold;'
    elif row['想定順位'] == '3位': styles[0] = 'background-color: #FCE5CD; color: #A0522D; font-weight: bold;'
    
    try:
        gap = float(row['勢いギャップ'])
        if gap > 0:
            styles[5] = 'color: #D32F2F; font-weight: bold;'
        elif gap < 0:
            styles[5] = 'color: #1976D2;'
    except:
        pass
    return styles

def style_bets(row):
    styles = [''] * len(row)
    if row['購入額'] == '見送り':
        return ['color: #B0BEC5;'] * len(row)
    else:
        styles[1] = 'font-weight: bold;'
        styles[4] = 'color: #D32F2F; font-weight: bold;'
        styles[5] = 'color: #388E3C; font-weight: bold;'
    return styles

# --- StreamlitのUI設定 ---
st.set_page_config(page_title="ガールズケイリン予想システム", page_icon="🚴‍♀️", layout="wide")
st.title("🚴‍♀️ ガールズケイリン予想＆資金配分システム")

if st.button("🚀 本日のレースデータを取得開始"):
    with st.spinner("スクレイピングを実行しています。数分かかる場合があります..."):
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage"
                ]
            )
            context = browser.new_context(viewport={"width": 1280, "height": 720}, ignore_https_errors=True)
            page = context.new_page()
            page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            try:
                target_urls = get_girls_venue_urls(page, TODAY_SCHEDULE_URL)
                base_ids = set()
                for url in target_urls:
                    match = re.search(r'race_id=(\d{10})\d{2}', url)
                    if match: base_ids.add(match.group(1))
                
                if not base_ids:
                    st.warning("本日のL級（ガールズ）レースは見つかりませんでした。")
                
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
                                
                            with st.expander(f"🏆 【{current_venue_name}】 {i}R (L級)", expanded=False):
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
                                
                                # --- ギャップ理論の計算 ---
                                race_evaluations.sort(key=lambda x: x['競走得点'], reverse=True)
                                for rank, p in enumerate(race_evaluations, 1):
                                    p['得点順位'] = rank
                                    
                                race_evaluations.sort(key=lambda x: x['調子スコア'], reverse=True)
                                for rank, p in enumerate(race_evaluations, 1):
                                    p['勢いギャップ'] = p['得点順位'] - rank 
                                    p['総合期待度'] = round(p['調子スコア'] + (p['勢いギャップ'] * 3.0), 2)

                                race_evaluations.sort(key=lambda x: x['総合期待度'], reverse=True)
                                
                                df_players = pd.DataFrame(race_evaluations)
                                df_players.insert(0, '想定順位', [f"{r}位" for r in range(1, len(df_players) + 1)])
                                
                                st.markdown("##### 🚴‍♀️ 出走選手データ (総合期待度順)")
                                styled_players = df_players[['想定順位', '車番', '選手名', '競走得点', '調子スコア', '勢いギャップ', '総合期待度']].style.apply(style_players, axis=1)
                                st.dataframe(styled_players, hide_index=True, use_container_width=True)
                                
                                tickets = []
                                if len(race_evaluations) >= 5:
                                    top_players = race_evaluations[:5] 
                                    top5_waku = [str(p['raw_waku']) for p in top_players]
                                    first_place = top5_waku[:2]
                                    second_place = top5_waku[:3]
                                    third_place = top5_waku[:5]
                                    
                                    w1_bonus_waku = None
                                    if (top_players[0]['総合期待度'] - top_players[1]['総合期待度']) >= 15:
                                        w1_bonus_waku = str(top_players[0]['raw_waku'])
                                    
                                    w2_bonus_waku = None
                                    if len(top_players) >= 3 and (top_players[1]['総合期待度'] - top_players[2]['総合期待度']) >= 15:
                                        w2_bonus_waku = str(top_players[1]['raw_waku'])
                                        
                                    w3_bonus_waku = None
                                    if len(top_players) >= 4 and (top_players[2]['総合期待度'] - top_players[3]['総合期待度']) >= 15:
                                        w3_bonus_waku = str(top_players[2]['raw_waku'])
                                    
                                    for first in first_place:
                                        for second in second_place:
                                            if first == second: continue
                                            for third in third_place:
                                                if first == third or second == third: continue
                                                tickets.append(f"{first}-{second}-{third}")
                                    
                                    odds_data = extract_ticket_odds(page, race_id, tickets)
                                    
                                    ticket_evaluations = []
                                    for t in tickets:
                                        w1, w2, w3 = t.split('-')
                                        s1 = next((p['総合期待度'] for p in race_evaluations if str(p['raw_waku']) == w1), 0)
                                        s2 = next((p['総合期待度'] for p in race_evaluations if str(p['raw_waku']) == w2), 0)
                                        s3 = next((p['総合期待度'] for p in race_evaluations if str(p['raw_waku']) == w3), 0)
                                        
                                        base_score = s1 + s2 + s3
                                        
                                        bonus = 0
                                        if w1_bonus_waku and w1 == w1_bonus_waku: bonus += 100000
                                        if w2_bonus_waku and w2 == w2_bonus_waku: bonus += 50000
                                        if w3_bonus_waku and w3 == w3_bonus_waku: bonus += 25000
                                            
                                        expected_score = base_score + bonus
                                        has_bonus = bonus > 0
                                        
                                        ticket_evaluations.append({
                                            'ticket': t,
                                            'expected_score': expected_score,
                                            'display_score': base_score,
                                            'has_bonus': has_bonus,
                                            'odds': odds_data.get(t, 0.0),
                                            's1': s1, 's2': s2, 's3': s3
                                        })
                                    
                                    ticket_evaluations.sort(key=lambda x: (x['expected_score'], x['s1'], x['s2'], x['s3']), reverse=True)
                                    
                                    total_budget = 1200
                                    target_payout = total_budget * 1.5
                                    
                                    valid_tickets = [ev['ticket'] for ev in ticket_evaluations if ev['odds'] > 0]
                                    ev_dict = {ev['ticket']: ev for ev in ticket_evaluations}
                                    
                                    max_k = min(len(valid_tickets), total_budget // 100)
                                    best_bets = {}
                                    active_tickets = []
                                    
                                    if max_k > 0:
                                        # 買い目の点数を減らしながら、「全員が目標額を超え、かつ均等になる配分」を探す
                                        for k in range(max_k, 0, -1):
                                            current_active = valid_tickets[:k]
                                            bets = {t: 100 for t in current_active}
                                            remaining_budget = total_budget - (100 * k)
                                            
                                            # 残りの予算を、払戻金が一番低い買い目に足して均等化していく
                                            while remaining_budget >= 100:
                                                lowest_t = min(current_active, key=lambda t: bets[t] * ev_dict[t]['odds'])
                                                bets[lowest_t] += 100
                                                remaining_budget -= 100
                                                
                                            # 分配後の、全買い目の中での「最低払戻金額」をチェック
                                            min_payout = min(bets[t] * ev_dict[t]['odds'] for t in current_active)
                                            
                                            # もし全買い目が目標額(150%)を達成しているか、買い目が1つしかなければ決定
                                            if min_payout >= target_payout or k == 1:
                                                best_bets = bets
                                                active_tickets = current_active
                                                break

                                    result_rows = []
                                    for rank, ev in enumerate(ticket_evaluations, 1):
                                        t = ev['ticket']
                                        odds_val = ev['odds']
                                        odds_str = f"{odds_val}倍" if odds_val > 0 else "取得失敗"
                                        score_str = f"{ev['display_score']:.2f}"
                                        if ev['has_bonus']:
                                            score_str += " ★"
                                        
                                        if t in best_bets:
                                            bet = best_bets[t]
                                            payout = bet * odds_val if odds_val > 0 else 0
                                            payout_str = f"¥{payout:,.0f}" if odds_val > 0 else "-"
                                            if odds_val > 0 and payout < target_payout:
                                                payout_str += " (未達)"
                                                
                                            result_rows.append({
                                                "優先順位": f"{rank}位",
                                                "買い目": t,
                                                "調子期待値": score_str,
                                                "現在オッズ": odds_str,
                                                "購入額": f"¥{bet}",
                                                "払戻見込": payout_str
                                            })
                                        else:
                                            result_rows.append({
                                                "優先順位": f"{rank}位",
                                                "買い目": t,
                                                "調子期待値": score_str,
                                                "現在オッズ": odds_str,
                                                "購入額": "見送り",
                                                "払戻見込": "-"
                                            })
                                    
                                    df_results = pd.DataFrame(result_rows)
                                    st.markdown(f"##### 💰 資金配分 (目標回収率150% / {total_budget}円)")
                                    styled_results = df_results.style.apply(style_bets, axis=1)
                                    st.dataframe(styled_results, hide_index=True, use_container_width=True)
                                    
                        time.sleep(1)
                        
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")
            finally:
                browser.close()
                st.success("すべての処理が完了しました！")
