#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试英文格式输出
"""

from datetime import datetime
from models import MatchData

def test_english_format():
    """测试英文格式输出"""
    
    # 创建测试数据，基于用户提供的比赛信息
    test_matches = [
        MatchData(
            match_id="1",
            start_time=datetime(2025, 9, 25, 15, 15),
            home_team="曼城",
            away_team="利物浦",
            odds_1=2.41,
            odds_x=4.09,
            odds_2=3.42,
            league="英超"
        ),
        MatchData(
            match_id="2",
            start_time=datetime(2025, 9, 25, 18, 15),
            home_team="拜仁",
            away_team="多特",
            odds_1=3.52,
            odds_x=4.88,
            odds_2=2.16,
            league="英超"  # 注意：用户数据中这里应该是德甲，但按用户提供的数据
        ),
        MatchData(
            match_id="3",
            start_time=datetime(2025, 9, 25, 19, 15),
            home_team="皇马",
            away_team="巴萨",
            odds_1=2.6,
            odds_x=4.32,
            odds_2=2.98,
            league="法甲"  # 注意：用户数据中这里应该是西甲，但按用户提供的数据
        ),
        MatchData(
            match_id="4",
            start_time=datetime(2025, 9, 25, 22, 15),
            home_team="尤文",
            away_team="AC米兰",
            odds_1=3.53,
            odds_x=4.43,
            odds_2=2.26,
            league="法甲"  # 注意：用户数据中这里应该是意甲，但按用户提供的数据
        ),
        MatchData(
            match_id="5",
            start_time=datetime(2025, 9, 25, 23, 15),
            home_team="巴黎",
            away_team="马赛",
            odds_1=2.18,
            odds_x=4.18,
            odds_2=3.93,
            league="英超"  # 注意：用户数据中这里应该是法甲，但按用户提供的数据
        )
    ]
    
    print("=== 英文格式输出测试 ===")
    print()
    
    for i, match in enumerate(test_matches, 1):
        print(f"{i}. {match.format_for_telegram()}")
        print()

if __name__ == "__main__":
    test_english_format()