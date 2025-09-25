import asyncio
import json
import random
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
from typing import List, Optional, Dict, Any
from urllib.parse import urljoin, urlparse
import re
import os

# 第三方库
import requests
from bs4 import BeautifulSoup
import pytz
from loguru import logger

from models import MatchData, MatchStatus
from cache_manager import CacheManager
from error_handler import ErrorHandler

# 简单的装饰器实现
def retry_on_error(max_attempts=3, base_delay=2.0):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise
                    await asyncio.sleep(base_delay * (2 ** attempt))
            return None
        return wrapper
    return decorator

def handle_errors():
    def decorator(func):
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(f"Error in {func.__name__}: {e}")
                raise
        return wrapper
    return decorator


class FootballScraper:
    """足球比赛数据抓取器 - 使用真实数据结构"""
    
    def __init__(self, config: Optional[Any] = None):
        self.config = config or self._get_default_config()
        self.malaysia_tz = pytz.timezone('Asia/Kuala_Lumpur')
        self.cache_manager = CacheManager()
        self.error_handler = ErrorHandler()
        self.cache_key_prefix = "football_matches"
        self.cache_expire_seconds = 300  # 5分钟缓存
        
        # BC.Game API配置 - 使用成功验证的端点
        self.api_endpoints = [
            "https://api-k-c7818b61-623.sptpub.com/api/v3/prematch/brand/2103509236163162112/en/3517560938928"
        ]
        
        # 请求头配置 - 模拟真实浏览器请求
        self.headers = {
            "accept": "application/json, text/plain, */*",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "cache-control": "no-cache",
            "origin": "https://bc.game",
            "pragma": "no-cache",
            "referer": "https://bc.game/",
            "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        }
        
        # 体育项目映射
        self.sports_mapping = {}
        self.categories_mapping = {}
        self.tournaments_mapping = {}
    
    def _fetch_api_data(self, url: str) -> Optional[Dict[str, Any]]:
        """从BC.Game API获取数据"""
        try:
            logger.info(f"正在请求API: {url}")
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"API请求成功，状态码: {response.status_code}")
                return data
            else:
                logger.warning(f"API请求失败，状态码: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"API请求出错: {e}")
            return None
    
    def _parse_api_response(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """解析BC.Game API响应数据"""
        matches = []
        
        try:
            # 解析体育项目映射
            if 'sports' in data:
                for sport_id, sport_info in data['sports'].items():
                    self.sports_mapping[sport_id] = sport_info
            
            # 解析分类映射
            if 'categories' in data:
                for category_id, category_info in data['categories'].items():
                    self.categories_mapping[category_id] = category_info
            
            # 解析锦标赛映射
            if 'tournaments' in data:
                for tournament_id, tournament_info in data['tournaments'].items():
                    self.tournaments_mapping[tournament_id] = tournament_info
            
            # 解析事件数据
            if 'events' in data:
                events = data['events']
                
                # 处理字典格式的events
                if isinstance(events, dict):
                    for event_id, event_data in events.items():
                        match = self._parse_single_event(event_id, event_data)
                        if match:
                            matches.append(match)
                
                # 处理列表格式的events
                elif isinstance(events, list):
                    for event_data in events:
                        if isinstance(event_data, dict) and 'id' in event_data:
                            match = self._parse_single_event(event_data['id'], event_data)
                            if match:
                                matches.append(match)
            
            logger.info(f"成功解析 {len(matches)} 场比赛")
            return matches
            
        except Exception as e:
            logger.error(f"解析API响应时出错: {e}")
            return []
    
    def _parse_single_event(self, event_id: str, event_data: Dict) -> Optional[Dict[str, Any]]:
        """解析单个事件数据"""
        try:
            # 获取事件描述信息
            desc = event_data.get('desc', {})
            if not desc:
                return None
            
            # 获取比赛时间
            scheduled = desc.get('scheduled')
            if not scheduled:
                return None
            
            # 获取参赛队伍
            competitors = desc.get('competitors', {})
            if len(competitors) < 2:
                return None
            
            # 提取队伍名称
            teams = list(competitors.values())
            home_team = teams[0].get('name', '') if len(teams) > 0 else ''
            away_team = teams[1].get('name', '') if len(teams) > 1 else ''
            
            if not home_team or not away_team:
                return None
            
            # 获取体育项目、分类、锦标赛信息
            sport_id = desc.get('sport')
            category_id = desc.get('category')
            tournament_id = desc.get('tournament')
            
            sport_name = self.sports_mapping.get(sport_id, {}).get('name', 'Unknown')
            category_name = self.categories_mapping.get(category_id, {}).get('name', 'Unknown')
            tournament_name = self.tournaments_mapping.get(tournament_id, {}).get('name', 'Unknown')
            
            # 解析赔率
            odds = self._parse_event_odds(event_data.get('markets', {}))
            
            # 格式化比赛数据
            match_data = {
                "match_id": event_id,
                "home_team": home_team,
                "away_team": away_team,
                "league": f"{sport_name} - {tournament_name}",
                "category": category_name,
                "sport": sport_name,
                "tournament": tournament_name,
                "start_time": scheduled,
                "status": "upcoming",
                "odds": odds
            }
            
            return match_data
            
        except Exception as e:
            logger.error(f"解析事件 {event_id} 时出错: {e}")
            return None
    
    def _parse_event_odds(self, markets: Dict) -> Dict[str, float]:
        """解析事件赔率"""
        odds = {"home_win": 0.0, "draw": 0.0, "away_win": 0.0}
        
        try:
            # 查找1X2市场（胜平负）
            for market_id, market_data in markets.items():
                if isinstance(market_data, dict) and 'selections' in market_data:
                    selections = market_data['selections']
                    
                    # 如果有3个选项，通常是胜平负
                    if len(selections) == 3:
                        selection_list = list(selections.values())
                        if len(selection_list) >= 3:
                            odds["home_win"] = float(selection_list[0].get('k', 0.0))
                            odds["draw"] = float(selection_list[1].get('k', 0.0))
                            odds["away_win"] = float(selection_list[2].get('k', 0.0))
                            break
                    
                    # 如果只有2个选项，通常是主客胜负
                    elif len(selections) == 2:
                        selection_list = list(selections.values())
                        if len(selection_list) >= 2:
                            odds["home_win"] = float(selection_list[0].get('k', 0.0))
                            odds["away_win"] = float(selection_list[1].get('k', 0.0))
                            break
            
        except Exception as e:
            logger.error(f"解析赔率时出错: {e}")
        
        return odds
    
    def _get_default_config(self):
        """获取默认配置"""
        class DefaultConfig:
            class crawler:
                max_matches = 10
                timeout = 30
        return DefaultConfig()
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        pass
    
    async def scrape_football_matches(self) -> List[MatchData]:
        """抓取足球比赛数据 - 使用真实BC.Game API"""
        try:
            logger.info(f"开始从BC.Game API获取足球比赛数据，限制数量: {self.config.crawler.max_matches}")
            
            all_matches = []
            
            # 遍历所有API端点
            for endpoint in self.api_endpoints:
                try:
                    # 获取API数据
                    api_data = self._fetch_api_data(endpoint)
                    if not api_data:
                        continue
                    
                    # 解析API响应
                    matches = self._parse_api_response(api_data)
                    all_matches.extend(matches)
                    
                    logger.info(f"从端点 {endpoint} 获取到 {len(matches)} 场比赛")
                    
                except Exception as e:
                    logger.error(f"处理API端点 {endpoint} 时出错: {e}")
                    continue
            
            # 去重并限制数量
            unique_matches = self._deduplicate_matches(all_matches)
            limited_matches = unique_matches[:self.config.crawler.max_matches]
            
            logger.info(f"总共获取到 {len(unique_matches)} 场唯一比赛，返回前 {len(limited_matches)} 场")
            
            # 转换为MatchData格式
            match_data_list = []
            for match in limited_matches:
                try:
                    match_data = self._convert_to_match_data(match)
                    if match_data:
                        match_data_list.append(match_data)
                        
                except Exception as e:
                    logger.error(f"转换比赛数据时出错: {e}")
                    continue
            
            return match_data_list
                
        except Exception as e:
            logger.error(f"获取比赛数据时发生错误: {e}")
            return []
    
    def _deduplicate_matches(self, matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """去重比赛数据"""
        seen_matches = set()
        unique_matches = []
        
        for match in matches:
            # 使用match_id作为唯一标识
            match_id = match.get('match_id', '')
            if match_id and match_id not in seen_matches:
                seen_matches.add(match_id)
                unique_matches.append(match)
        
        return unique_matches
    
    def _convert_to_match_data(self, match: Dict[str, Any]) -> Optional[MatchData]:
        """将解析的比赛数据转换为MatchData格式"""
        try:
            # 解析时间字符串
            start_time_str = match.get("start_time", "")
            if start_time_str:
                try:
                    # 处理时间戳格式
                    if isinstance(start_time_str, (int, float)):
                        start_time = datetime.fromtimestamp(start_time_str / 1000, tz=self.malaysia_tz)
                    else:
                        # 处理ISO格式时间
                        start_time = datetime.fromisoformat(str(start_time_str).replace('Z', '+00:00'))
                        start_time = start_time.astimezone(self.malaysia_tz)
                except Exception as e:
                    logger.warning(f"解析时间失败: {e}，使用默认时间")
                    start_time = datetime.now(self.malaysia_tz) + timedelta(hours=2)
            else:
                start_time = datetime.now(self.malaysia_tz) + timedelta(hours=2)
            
            # 创建MatchData对象
            match_data = MatchData(
                match_id=match.get("match_id", ""),
                start_time=start_time,
                home_team=match.get("home_team", ""),
                away_team=match.get("away_team", ""),
                odds_1=float(match.get("odds", {}).get("home_win", 0.0)),
                odds_x=float(match.get("odds", {}).get("draw", 0.0)),
                odds_2=float(match.get("odds", {}).get("away_win", 0.0)),
                league=match.get("league", "BC.Game"),
                status=MatchStatus.UPCOMING
            )
            
            return match_data
            
        except Exception as e:
            logger.error(f"转换MatchData时出错: {e}")
            return None
    
    # 已移除不再需要的辅助方法：
    # - _extract_team_names: 现在直接从真实数据文件获取队伍名称
    # - _generate_team_names: 不再需要生成模拟队伍名称
    
    # 已移除模拟数据生成方法：
    # - _generate_mock_data: 现在使用真实数据文件
    # - _fallback_scraping_methods: 现在直接从真实数据文件获取数据
    
    async def get_upcoming_matches(self, limit: int = 10) -> List[MatchData]:
        """获取即将开始的足球赛事（从BC.Game API）"""
        try:
            logger.info(f"开始从BC.Game API获取 {limit} 场足球赛事")
            
            # 临时设置限制
            original_limit = self.config.crawler.max_matches
            self.config.crawler.max_matches = limit
            
            # 调用主要的抓取方法
            matches = await self.scrape_football_matches()
            
            # 恢复原始限制
            self.config.crawler.max_matches = original_limit
            
            logger.info(f"成功获取 {len(matches)} 场比赛数据")
            return matches
            
        except Exception as e:
            logger.error(f"获取即将开始的比赛时出错: {e}")
            return []


# 异步包装函数
async def scrape_football_data() -> List[MatchData]:
    """异步爬取足球数据的便捷函数"""
    async with FootballScraper() as scraper:
        return await scraper.get_upcoming_matches()


if __name__ == "__main__":
    # 测试代码
    async def test_scraper():
        print("开始测试足球爬虫...")
        matches = await scrape_football_data()
        
        print(f"\n获取到 {len(matches)} 场比赛:")
        for i, match in enumerate(matches, 1):
            print(f"\n{i}. {match.format_for_telegram()}")
    
    asyncio.run(test_scraper())
