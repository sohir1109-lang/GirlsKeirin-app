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
    date_str = today_jst.strftime('%Y%m%d') # 例: 20260730
    
    print(f"=== スクレイピング開始: {date_str} (JST) ===")
    
    # ---------------------------------------------------------
    # 【ここに既存のPlaywrightのスクレイピング処理を貼り付けてください】
    # target_url = f"https://.../{date_str}/..."
    # データを取得し、辞書やリストの形式にまとめる処理
    # ---------------------------------------------------------
    
    # 仮のスクレイピングデータ（実装時は削除してください）
    scraped_data = [
        {
            "race_name": "第1レース",
            "start_time": "10:30",
            "players": [
                {"name": "選手A", "points_rank": 2, "condition_rank": 1, "recent_grades": [("S", 1.0), ("A", 0.8), ("A", 0.5)]},
                {"name": "選手B", "points_rank": 5, "condition_rank": 2, "recent_grades": [("B", 1.0), ("A", 0.8)]} # 新人/データ少
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
        return 0.0 # 完全なデータなし
    
    total_score = 0.0
    total_weight = 0.0
    
    for grade, weight in recent_grades:
        # 文字列の成績を数値化するロジック（適宜調整してください）
        grade_val = 3.0 if grade == "S" else (2.0 if grade == "A" else 1.0)
        
        total_score += grade_val * weight
        total_weight += weight
        
    # ウェイトの合計で割ることで、出走回数が少ない選手のスコアインフレを防ぐ
    return total_score / total_weight if total_weight > 0 else 0.0

def analyze_gap(points_rank, condition_rank):
    """
    モメンタムギャップ（競争得点順位 - 調子順位）の計算
    プラスが大きいほど「実力評価より今の調子が良い＝オッズの歪み（穴）」
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
            races = json.load(f)
    except FileNotFoundError:
        st.warning("本日のデータがまだ取得されていません。")
        return

    # レースを開始時間順にソート
    races.sort(key=lambda x: x.get('start_time', '23:59'))

    # 各レースの表示
    for race in races:
        # 開始時間のパース (JSTとして扱う)
        start_time_str = race.get('start_time', '00:00')
        hour, minute = map(int, start_time_str.split(':'))
        race_datetime = now_jst.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # 10分経過チェック
        time_diff = now_jst - race_datetime
        is_finished = time_diff > timedelta(minutes=10)
        
        status_tag = " 🏁【レース終了】" if is_finished else ""
        
        st.subheader(f"■ {race.get('race_name')} (発走 {start_time_str}){status_tag}")
        
        if is_finished:
            st.info("このレースは終了しました。")
            continue
            
        # 選手データの評価と表示
        results = []
        for player in race.get('players', []):
            # スコア計算
            adjusted_score = calculate_true_score(player.get('recent_grades', []))
            # ギャップ計算
            gap = analyze_gap(player.get('points_rank', 9), player.get('condition_rank', 9))
            
            results.append({
                "選手名": player.get('name'),
                "得点順位": player.get('points_rank'),
                "調子順位": player.get('condition_rank'),
                "ギャップ": f"{gap:+d}", # +を明示
                "補正スコア": round(adjusted_score, 2)
            })
            
        df = pd.DataFrame(results)
        # 補正スコア順 ＞ ギャップ順 でソートしておすすめを表示
        df = df.sort_values(by=["補正スコア", "ギャップ"], ascending=[False, False])
        
        st.dataframe(df, use_container_width=True)
        st.divider()


# ==========================================
# 5. エントリーポイント (CLI引数で処理を分岐)
# ==========================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Girls Keirin Prediction System")
    parser.add_argument('--scrape', action='store_true', help="Run scraper to update today_data.json")
    args = parser.parse_args()

    if args.scrape:
        # GitHub Actions などで `--scrape` を付けて実行された場合はこちら
        run_scraping()
    else:
        # 通常の `streamlit run app.py` で実行された場合はこちら
        run_ui()
