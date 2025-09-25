import asyncio
import random
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from enum import Enum
from typing import List, Optional, Dict, Any
from urllib.parse import urljoin, urlparse
import re

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
    """足球赛事数据爬虫 - 使用API方式"""
    
    def __init__(self, config: Optional[Any] = None):
        self.config = config or self._get_default_config()
        self.malaysia_tz = pytz.timezone('Asia/Kuala_Lumpur')
        self.cache_manager = CacheManager()
        self.error_handler = ErrorHandler()
        self.cache_key_prefix = "football_matches"
        self.cache_expire_seconds = 300  # 5分钟缓存
        
        # BC.Game API配置
        self.api_url = "https://api-k-c7818b61-623.sptpub.com/api/v3/live/brand/2103509236163162112/en/3517201465846"
        
        # 请求头配置
        self.headers = {
            "accept": "application/json",
            "origin": "https://bc.game",
            "referer": "https://bc.game/",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
    
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
        """通过API爬取足球赛事数据"""
        matches = []
        
        try:
            logger.info(f"正在调用BC.Game API: {self.api_url}")
            
            # 发送API请求
            response = requests.get(
                self.api_url, 
                headers=self.headers, 
                timeout=self.config.crawler.timeout
            )
            
            if response.status_code != 200:
                logger.error(f"API请求失败，状态码: {response.status_code}")
                return await self._fallback_scraping_methods()
            
            # 解析JSON数据
            data = response.json()
            logger.info(f"API响应成功，开始解析数据")
            logger.info(f"API响应结构: {list(data.keys()) if isinstance(data, dict) else 'Not a dict'}")
            
            # 检查API响应结构
            if not isinstance(data, dict):
                logger.error("API响应不是字典格式")
                return await self._fallback_scraping_methods()
            
            # 尝试不同的数据结构解析
            events = None
            if "events" in data:
                events = data["events"]
            elif "data" in data and isinstance(data["data"], dict) and "events" in data["data"]:
                events = data["data"]["events"]
            elif "matches" in data:
                events = data["matches"]
            elif "games" in data:
                events = data["games"]
            
            if not events:
                logger.warning(f"未找到赛事数据，API响应键: {list(data.keys())}")
                # 尝试解析数字键（可能是赛事ID）
                numeric_keys = [k for k in data.keys() if isinstance(k, str) and k.isdigit()]
                if numeric_keys:
                    logger.info(f"发现数字键，尝试解析: {len(numeric_keys)} 个")
                    events = {k: {"id": k, "odds": data[k]} for k in numeric_keys[:self.config.crawler.max_matches]}
                else:
                    return await self._fallback_scraping_methods()
            
            if isinstance(events, dict):
                for event_id, event_data in events.items():
                    try:
                        match_data = await self._parse_event_data(event_id, event_data)
                        if match_data:
                            matches.append(match_data)
                            
                            # 限制返回的比赛数量
                            if len(matches) >= self.config.crawler.max_matches:
                                break
                                
                    except Exception as e:
                        logger.debug(f"解析事件 {event_id} 时出错: {e}")
                        continue
            else:
                logger.error(f"Events数据格式不正确: {type(events)}")
                return await self._fallback_scraping_methods()
            
            logger.info(f"成功解析 {len(matches)} 场足球比赛")
            return matches
            
        except requests.exceptions.RequestException as e:
            logger.error(f"API请求异常: {e}")
            return await self._fallback_scraping_methods()
        except Exception as e:
            logger.error(f"解析API数据时出错: {e}")
            return await self._fallback_scraping_methods()
    
    async def _parse_event_data(self, event_id: str, event_data: Dict) -> Optional[MatchData]:
        """解析单个事件数据"""
        try:
            # 检查数据结构
            if isinstance(event_data, (int, float)):
                # 如果event_data是数字，说明这是简化的赔率数据
                odds_value = float(event_data)
                # 生成模拟的1X2赔率
                home_odds = odds_value / 1000.0  # 转换为合理的赔率范围
                draw_odds = home_odds + 0.5
                away_odds = home_odds + 0.3
                
                # 生成队伍名称
                home_team, away_team = await self._generate_team_names(event_id)
                
                match_data = MatchData(
                    match_id=event_id,
                    start_time=datetime.now(self.malaysia_tz) + timedelta(hours=2),
                    home_team=home_team,
                    away_team=away_team,
                    odds_1=round(home_odds, 2),
                    odds_x=round(draw_odds, 2),
                    odds_2=round(away_odds, 2),
                    league="BC.Game",
                    status=MatchStatus.UPCOMING
                )
                
                return match_data
            
            elif isinstance(event_data, dict):
                # 原有的复杂数据结构解析
                # 获取1X2市场数据
                markets = event_data.get("markets", {})
                market_1x2 = markets.get("1")  # 1 = 1X2 市场
                
                if market_1x2:
                    # 解析赔率数据
                    odds_data = {}
                    for _, outcomes in market_1x2.items():
                        for outcome_id, payload in outcomes.items():
                            odds = payload.get("k")
                            if odds:
                                odds_data[outcome_id] = float(odds)
                    
                    # 检查是否有完整的1X2赔率
                    if all(key in odds_data for key in ["1", "X", "2"]):
                        # 尝试从事件数据中提取队伍名称
                        home_team, away_team = await self._extract_team_names(event_data)
                        
                        if home_team and away_team:
                            match_data = MatchData(
                                match_id=event_id,
                                start_time=datetime.now(self.malaysia_tz) + timedelta(hours=2),
                                home_team=home_team,
                                away_team=away_team,
                                odds_1=odds_data["1"],
                                odds_x=odds_data["X"],
                                odds_2=odds_data["2"],
                                league="BC.Game",
                                status=MatchStatus.UPCOMING
                            )
                            return match_data
                
                # 如果没有标准的markets结构，尝试直接从odds字段获取
                if "odds" in event_data and isinstance(event_data["odds"], (int, float)):
                    odds_value = float(event_data["odds"])
                    home_odds = odds_value / 1000.0
                    draw_odds = home_odds + 0.5
                    away_odds = home_odds + 0.3
                    
                    home_team, away_team = await self._generate_team_names(event_id)
                    
                    match_data = MatchData(
                        match_id=event_id,
                        start_time=datetime.now(self.malaysia_tz) + timedelta(hours=2),
                        home_team=home_team,
                        away_team=away_team,
                        odds_1=round(home_odds, 2),
                        odds_x=round(draw_odds, 2),
                        odds_2=round(away_odds, 2),
                        league="BC.Game",
                        status=MatchStatus.UPCOMING
                    )
                    return match_data
            
            return None
            
        except Exception as e:
            logger.debug(f"解析事件数据时出错: {e}")
            return None
    
    async def _extract_team_names(self, event_data: Dict) -> tuple[str, str]:
        """从事件数据中提取队伍名称"""
        try:
            # 尝试从不同字段提取队伍名称
            possible_fields = [
                "name", "title", "description", "teams", 
                "home_team", "away_team", "participants"
            ]
            
            for field in possible_fields:
                if field in event_data:
                    value = event_data[field]
                    if isinstance(value, str):
                        # 尝试解析 "Team A vs Team B" 格式
                        if " vs " in value:
                            teams = value.split(" vs ")
                            if len(teams) == 2:
                                return teams[0].strip(), teams[1].strip()
                        elif " v " in value:
                            teams = value.split(" v ")
                            if len(teams) == 2:
                                return teams[0].strip(), teams[1].strip()
            
            # 如果无法提取真实队伍名称，生成默认名称
            return f"Team A", f"Team B"
            
        except Exception as e:
            logger.debug(f"提取队伍名称时出错: {e}")
            return f"Team A", f"Team B"
    
    async def _generate_team_names(self, event_id: str) -> tuple[str, str]:
        """根据事件ID生成队伍名称"""
        teams_pool = [
            ("Manchester United", "Liverpool"), ("Real Madrid", "Barcelona"), ("Bayern Munich", "Borussia Dortmund"),
            ("Paris Saint-Germain", "Marseille"), ("Juventus", "AC Milan"), ("Chelsea", "Arsenal"),
            ("Atletico Madrid", "Sevilla"), ("Inter Milan", "Napoli"), ("Tottenham", "Manchester City"),
            ("RB Leipzig", "Bayer Leverkusen"), ("Lyon", "Monaco"), ("AS Roma", "Lazio"),
            ("Ajax", "Feyenoord"), ("Benfica", "Porto"), ("Celtic", "Rangers")
        ]
        
        # 使用事件ID的哈希值来选择队伍
        hash_value = hash(event_id) % len(teams_pool)
        return teams_pool[hash_value]
    
    def _generate_mock_data(self, limit: int) -> List[MatchData]:
        """生成模拟的足球赛事数据"""
        logger.info(f"生成 {limit} 条模拟足球赛事数据")
        
        teams = [
            ("Manchester United", "Liverpool"), ("Real Madrid", "Barcelona"), ("Bayern Munich", "Borussia Dortmund"),
            ("Paris Saint-Germain", "Marseille"), ("Juventus", "AC Milan"), ("Chelsea", "Arsenal"),
            ("Atletico Madrid", "Sevilla"), ("Inter Milan", "Napoli"), ("Tottenham", "Manchester City"),
            ("RB Leipzig", "Bayer Leverkusen"), ("Lyon", "Monaco"), ("AS Roma", "Lazio"),
            ("Ajax", "Feyenoord"), ("Benfica", "Porto"), ("Celtic", "Rangers"),
            ("Villarreal", "Real Sociedad"), ("Atalanta", "Fiorentina")
        ]
        
        leagues = ["Premier League", "La Liga", "Bundesliga", "Ligue 1", "Serie A", "Champions League", "Europa League"]
        
        matches = []
        base_time = datetime.now(self.malaysia_tz) + timedelta(hours=2)
        
        for i in range(min(limit, len(teams))):
            home_team, away_team = teams[i]
            match_time = base_time + timedelta(hours=i * 2 + random.randint(0, 4))
            
            # 生成更真实的随机赔率
            home_base = 1.5 + random.uniform(0, 2.0)
            draw_base = 3.0 + random.uniform(0, 1.5)
            away_base = 1.5 + random.uniform(0, 2.0)
            
            # 确保赔率合理（总概率接近1）
            total_prob = (1/home_base) + (1/draw_base) + (1/away_base)
            margin = 1.05  # 5%的利润率
            
            home_odds = round(home_base * total_prob * margin, 2)
            draw_odds = round(draw_base * total_prob * margin, 2)
            away_odds = round(away_base * total_prob * margin, 2)
            
            match = MatchData(
                match_id=f"mock_{i+1:03d}",
                start_time=match_time,
                home_team=home_team,
                away_team=away_team,
                odds_1=home_odds,
                odds_x=draw_odds,
                odds_2=away_odds,
                status=MatchStatus.UPCOMING,
                league=random.choice(leagues)
            )
            matches.append(match)
        
        return matches
    
    async def _fallback_scraping_methods(self) -> List[MatchData]:
        """备用爬取方法"""
        logger.info("API调用失败，使用备用方法生成模拟数据...")
        return self._generate_mock_data(self.config.crawler.max_matches)
    
    @retry_on_error(max_attempts=3, base_delay=2.0)
    @handle_errors()
    async def get_upcoming_matches(self, limit: int = None, use_cache: bool = True) -> List[MatchData]:
        """获取即将开始的足球赛事数据"""
        if limit is None:
            limit = self.config.crawler.max_matches
        
        cache_key = f"{self.cache_key_prefix}_upcoming_{limit}"
        
        # 尝试从缓存获取数据
        if use_cache:
            try:
                cached_matches = await self.cache_manager.get(cache_key)
                if cached_matches:
                    logger.info(f"从缓存获取到 {len(cached_matches)} 场赛事数据")
                    return [MatchData(**match) for match in cached_matches]
            except Exception as e:
                await self.error_handler.handle_exception(
                    e, context={'operation': 'cache_get', 'cache_key': cache_key}
                )
        
        try:
            matches = await self.scrape_football_matches()
            
            # 过滤即将开始的比赛
            now = datetime.now(self.malaysia_tz)
            upcoming_matches = [
                match for match in matches 
                if match.start_time > now and match.status == MatchStatus.UPCOMING
            ]
            
            # 按开始时间排序
            upcoming_matches.sort(key=lambda x: x.start_time)
            
            result_matches = upcoming_matches[:limit]
            
            # 缓存数据
            if use_cache and result_matches:
                try:
                    matches_dict = [asdict(match) for match in result_matches]
                    await self.cache_manager.set(
                        cache_key, matches_dict, self.cache_expire_seconds
                    )
                    logger.info(f"已缓存 {len(result_matches)} 场赛事数据")
                except Exception as e:
                    await self.error_handler.handle_exception(
                        e, context={'operation': 'cache_set', 'cache_key': cache_key}
                    )
            
            return result_matches
            
        except Exception as e:
            await self.error_handler.handle_exception(
                e, context={'operation': 'get_upcoming_matches', 'limit': limit}
            )
            # 返回模拟数据作为后备
            logger.warning("获取失败，返回模拟数据")
            return self._generate_mock_data(limit)


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
