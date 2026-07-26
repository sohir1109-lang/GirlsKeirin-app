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
                            
                            # --- 独自の「ギャップ理論」を組み込む ---
                            # 1. 競走得点の順位を出す
                            race_evaluations.sort(key=lambda x: x['競走得点'], reverse=True)
                            for rank, p in enumerate(race_evaluations, 1):
                                p['得点順位'] = rank
                                
                            # 2. 調子スコアの順位を出す
                            race_evaluations.sort(key=lambda x: x['調子スコア'], reverse=True)
                            for rank, p in enumerate(race_evaluations, 1):
                                # 得点順位(人気)より調子順位(実力)が上ならプラスになる（例: 得点6位 - 調子2位 = +4）
                                p['勢いギャップ'] = p['得点順位'] - rank 
                                
                                # 3. 買い目選定のための「総合期待度」を算出（ギャップをボーナス加点）
                                # ※係数の5.0は、ギャップ1につき調子スコア5pt分の価値を持たせるという調整値です
                                p['総合期待度'] = round(p['調子スコア'] + (p['勢いギャップ'] * 5.0), 2)

                            # 総合期待度が高い順に並び替え
                            race_evaluations.sort(key=lambda x: x['総合期待度'], reverse=True)
                            
                            # Streamlit用のデータフレーム作成（選手データ）
                            df_players = pd.DataFrame(race_evaluations)
                            df_players.insert(0, '想定順位', [f"{r}位" for r in range(1, len(df_players) + 1)])
                            st.write("▼ 出走選手データ (総合期待度順：実力と勢いのギャップを加味)")
                            
                            # 画面にギャップと総合期待度を表示
                            st.dataframe(df_players[['想定順位', '車番', '選手名', '競走得点', '調子スコア', '勢いギャップ', '総合期待度']], hide_index=True)
                            
                            tickets = []
                            if len(race_evaluations) >= 5:
                                top_players = race_evaluations[:5] # 「総合期待度」の上位5名を選ぶ
                                top5_waku = [str(p['raw_waku']) for p in top_players]
                                first_place = top5_waku[:2]
                                second_place = top5_waku[:3]
                                third_place = top5_waku[:5]
                                
                                w1_bonus_waku = None
                                if (top_players[0]['総合期待度'] - top_players[1]['総合期待度']) >= 15:
                                    w1_bonus_waku = str(top_players[0]['raw_waku'])
                                
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
                                    # 調子スコアではなく、新しい「総合期待度」をベースに買い目を評価する
                                    s1 = next((p['総合期待度'] for p in race_evaluations if str(p['raw_waku']) == w1), 0)
                                    s2 = next((p['総合期待度'] for p in race_evaluations if str(p['raw_waku']) == w2), 0)
                                    s3 = next((p['総合期待度'] for p in race_evaluations if str(p['raw_waku']) == w3), 0)
                                    
                                    base_score = s1 + s2 + s3
                                    bonus = 100000 if (w1_bonus_waku and w1 == w1_bonus_waku) else 0
                                    expected_score = base_score + bonus
                                    
                                    ticket_evaluations.append({
                                        'ticket': t,
                                        'expected_score': expected_score,
                                        'display_score': base_score,
                                        'has_bonus': bonus > 0,
                                        'odds': odds_data.get(t, 0.0),
                                        's1': s1, 's2': s2, 's3': s3
                                    })
