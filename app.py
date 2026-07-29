import streamlit as st
import json
import argparse
import pandas as pd
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. タイムゾーン設定 (超重要: UTCのズレを防ぐ)
# ==========================================
JST = timezone(timedelta(hours=+9), 'JST')

# ==========================================
# 2. スクレイピング処理 (--scrape 実行時)
# ==========================================
def run_scraping():
    # JSTで「確実な今日」の日付を取得
    today_jst = datetime.now(JST)
    date_str = today_jst.strftime('%Y%m%d')
    
    print(f"=== スクレイピング開始: {date_str} (JST) ===")
    
    # ---------------------------------------------------------
    # 【ここに既存のPlaywrightのスクレイピング処理を記述してください】
    # target_url = f"https://.../{date_str}/..."
    # データを取得し、辞書やリストの形式にまとめる処理
    # ---------------------------------------------------------
    
    # 仮のスクレイピングデータ（ご自身の処理を実装後は削除または上書きしてください）
    scraped_data = [
        {
            "race_name": "第1レース",
            "start_time": "10:30",
            "players": [
                {"name": "選手A", "points_rank": 2, "condition_rank": 1, "recent_grades": [("S", 1.0), ("A", 0.8), ("A", 0.5)]},
                {"name": "選手B", "points_rank": 5, "condition_rank": 2, "recent_grades": [("B", 1.0), ("A", 0.8)]}
            ]
        }
    ]
    
    # today_data.json として保存
    with open('today_data.json', 'w', encoding='utf-8') as f:
        json.dump(scraped_data, f, ensure_ascii=False, indent=4)
        
    print("=== スクレイピング完了 & today_data.json 保存成功 ===")

# ==========================================
# 3. 予想ロジック (加重平均 & モメンタムギャップ)
# ==========================================
def calculate_true_score(recent_grades):
    """
    データインフレを防ぐ真の加重平均スコア計算
    recent_grades: [(成績スコア, 直近ウェイト), ...] のリスト
    """
    if not recent_grades:
        return 0.0
    
    total_score = 0.0
    total_weight = 0.0
    
    for grade, weight in recent_grades:
        grade_val = 3.0 if grade == "S" else (2.0 if grade == "A" else 1.0)
        total_score += grade_val * weight
        total_weight += weight
        
    return total_score / total_weight if total_weight > 0 else 0.0

def analyze_gap(points_rank, condition_rank):
    """
    モメンタムギャップ（競争得点順位 - 調子順位）の計算
    """
    return points_rank - condition_rank

# ==========================================
# 4. Streamlit UI 表示処理
# ==========================================
def run_ui():
    st.set_page_config(page_title="ガールズケイリン 予想システム", layout="wide")
    st.title("🚴‍♀️ ガールズケイリン AI予想ダッシュボード")
    
    now_jst = datetime.now(JST)
    st.write(f"現在時刻 (JST): {now_jst.strftime('%Y-%m-%d %H:%M:%S')}")

    # データの読み込み
    try:
        with open('today_data.json', 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
    except FileNotFoundError:
        st.warning("本日のデータがまだ取得されていません。")
        return

    # データ構造のブレを吸収する処理
    races = []
    if isinstance(raw_data, list):
        races = raw_data
    elif isinstance(raw_data, dict):
        if "races" in raw_data:
            races = raw_data["races"]
        else:
            races = list(raw_data.values())
    
    # リストの中身が辞書であることを保証
    races = [r for r in races if isinstance(r, dict)]

    if not races:
        st.error("表示できるレースデータが見つかりませんでした。スクレイピング処理のデータ保存形式を確認してください。")
        return

    # レースを開始時間順にソート (安全なgetを使用)
    races.sort(key=lambda x: str(x.get('start_time', '23:59')))

    # 各レースの表示
    for race in races:
        # 開始時間のパース
        start_time_str = str(race.get('start_time', '00:00'))
        
        try:
            hour, minute = map(int, start_time_str.split(':'))
        except ValueError:
            hour, minute = 0, 0
            
        race_datetime = now_jst.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # 10分経過チェック
        time_diff = now_jst - race_datetime
        is_finished = time_diff > timedelta(minutes=10)
        
        status_tag = " 🏁【レース終了】" if is_finished else ""
        
        st.subheader(f"■ {race.get('race_name', 'レース名不明')} (発走 {start_time_str}){status_tag}")
        
        if is_finished:
            st.info("このレースは終了しました。")
            continue
            
        # 選手データの評価と表示
        results = []
        players = race.get('players', [])
        
        if not isinstance(players, list):
            st.warning("選手データが正しく読み込めませんでした。")
            continue
            
        for player in players:
            if not isinstance(player, dict):
                continue
                
            # スコア計算
            recent_grades = player.get('recent_grades', [])
            adjusted_score = calculate_true_score(recent_grades) if isinstance(recent_grades, list) else 0.0
            
            # ギャップ計算
            points_rank = player.get('points_rank', 9)
            condition_rank = player.get('condition_rank', 9)
            gap = analyze_gap(points_rank, condition_rank)
            
            results.append({
                "選手名": player.get('name', '不明'),
                "得点順位": points_rank,
                "調子順位": condition_rank,
                "ギャップ": f"{gap:+d}",
                "補正スコア": round(adjusted_score, 2)
            })
            
        if results:
            df = pd.DataFrame(results)
            df = df.sort_values(by=["補正スコア", "ギャップ"], ascending=[False, False])
            st.dataframe(df, use_container_width=True)
        else:
            st.write("選手データがありません。")
            
        st.divider()

# ==========================================
# 5. エントリーポイント (CLI引数で処理を分岐)
# ==========================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Girls Keirin Prediction System")
    parser.add_argument('--scrape', action='store_true', help="Run scraper to update today_data.json")
    args = parser.parse_args()

    if args.scrape:
        run_scraping()
    else:
        run_ui()
